#!/usr/bin/env python3
"""Platrixa S2 — Termux service definition checks (no device required).

Validates the runit/boot assets in ``device/termux/`` by executing the POSIX
shell scripts against a fake HOME, fake repo, fake python and a fake ``curl``.
Nothing here touches Android, Termux, Railway, a model server, or a credential.

Covered:
  * POSIX syntax of every shipped script;
  * the env template has the required keys (and no secret literals);
  * missing env file / missing key file / insecure key permissions refuse;
  * a missing required variable refuses;
  * the model-readiness wait exits 75 (EX_TEMPFAIL) when the model never comes up;
  * it starts after the model becomes reachable, in dry-run mode;
  * the exact worker argv is correct and never contains the key value;
  * a live duplicate is refused, and a STALE lock is reclaimed;
  * `finish` applies bounded backoff and rotates an oversized log;
  * the installer refuses a non-Termux PREFIX.

Run: python scripts/platrixa_termux_service_test.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TERMUX_DIR = REPO_ROOT / "device" / "termux"
RUN_SCRIPT = TERMUX_DIR / "service" / "platrixa-worker" / "run"
FINISH_SCRIPT = TERMUX_DIR / "service" / "platrixa-worker" / "finish"
BOOT_SCRIPT = TERMUX_DIR / "boot" / "platrixa-worker-boot.sh"
INSTALL_SCRIPT = TERMUX_DIR / "install-termux-service.sh"
ENV_EXAMPLE = TERMUX_DIR / "worker.env.example"

KEY_VALUE = "plx_test_key_value_do_not_leak"

PASSED = 0
FAILED: list = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"  [PASS] {name}")
    else:
        FAILED.append((name, detail))
        print(f"  [FAIL] {name} — {detail}")


def write(path: Path, text: str, mode: int = 0o755) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)
    return path


class Sandbox:
    """A fake device filesystem: HOME, repo, python, and curl."""

    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="platrixa_s2_"))
        self.home = self.root / "home"
        self.repo = self.root / "repo"
        self.bin = self.root / "bin"
        self.env_file = self.home / ".platrixa" / "worker.env"
        self.key_file = self.home / ".platrixa" / "worker.key"
        self.log_dir = self.home / ".platrixa" / "logs"
        self.curl_state = self.root / "curl.state"
        self.lock_dir = self.home / ".platrixa" / "worker.lock.d"

        write(
            self.repo / "scripts" / "platrixa_outbound_inference_worker.py",
            "# stub worker for the service test\n",
            0o644,
        )
        write(self.bin / "python", "#!/bin/sh\nexit 0\n")
        # Fake curl: fails FAKE_CURL_FAILS times, then reports a model list.
        write(
            self.bin / "curl",
            "#!/bin/sh\n"
            'state="${FAKE_CURL_STATE:?}"\n'
            'n=$(cat "$state" 2>/dev/null || echo 0)\n'
            "n=$((n + 1))\n"
            'echo "$n" >"$state"\n'
            'if [ "$n" -le "${FAKE_CURL_FAILS:-0}" ]; then exit 7; fi\n'
            'echo \'{"object":"list","data":[{"id":"default"}]}\'\n',
        )
        self.write_env()
        write(self.key_file, KEY_VALUE + "\n", 0o600)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def write_env(self, **overrides: str) -> None:
        values = {
            "PLATRIXA_API_BASE_URL": "https://api.example.test",
            "PLATRIXA_WORKER_ID": "android-test-worker",
            "PLATRIXA_WORKER_MODEL_URL": "http://127.0.0.1:11434",
            "PLATRIXA_WORKER_MODEL_ID": "default",
        }
        values.update(overrides)
        body = "# test env\n" + "".join(f"{k}={v}\n" for k, v in values.items() if v)
        self.env_file.parent.mkdir(parents=True, exist_ok=True)
        write(self.env_file, body, 0o600)

    def env(self, **overrides: str) -> dict:
        env = dict(os.environ)
        # Keep system tools (sh, stat, mkdir, sleep) reachable; ours come first.
        env["PATH"] = f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}"
        env.update(
            {
                "HOME": str(self.home),
                "PLATRIXA_REPO_DIR": str(self.repo),
                "PLATRIXA_PYTHON": str(self.bin / "python"),
                "PLATRIXA_LOG_DIR": str(self.log_dir),
                "PLATRIXA_LOCK_FILE": str(self.home / ".platrixa" / "worker.lock"),
                "PLATRIXA_WORKER_ENV_FILE": str(self.env_file),
                "PLATRIXA_WORKER_KEY_FILE": str(self.key_file),
                "PLATRIXA_MODEL_HEALTH_URL": "http://127.0.0.1:11434/v1/models",
                "PLATRIXA_READINESS_ATTEMPTS": "2",
                "PLATRIXA_READINESS_INTERVAL": "0",
                "FAKE_CURL_STATE": str(self.curl_state),
                "FAKE_CURL_FAILS": "0",
            }
        )
        env.update(overrides)
        return env

    def run(self, **overrides: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["sh", str(RUN_SCRIPT)],
            env=self.env(**overrides),
            capture_output=True,
            text=True,
            timeout=30,
        )

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


def output_of(result: subprocess.CompletedProcess) -> str:
    return (result.stdout or "") + (result.stderr or "")


# ---------------------------------------------------------------------------
# 1. Syntax + template
# ---------------------------------------------------------------------------


def test_shell_syntax() -> None:
    for script in (RUN_SCRIPT, FINISH_SCRIPT, BOOT_SCRIPT, INSTALL_SCRIPT):
        result = subprocess.run(
            ["sh", "-n", str(script)], capture_output=True, text=True, timeout=20
        )
        check(
            f"{script.name} is valid POSIX shell",
            result.returncode == 0,
            (result.stderr or "").strip(),
        )


def test_env_template() -> None:
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    required = (
        "PLATRIXA_API_BASE_URL",
        "PLATRIXA_WORKER_ID",
        "PLATRIXA_WORKER_MODEL_URL",
        "PLATRIXA_WORKER_MODEL_ID",
    )
    check(
        "env template declares every required setting",
        all(name in text for name in required),
        f"missing {[n for n in required if n not in text]}",
    )
    check(
        "env template points at the loopback model endpoint",
        "PLATRIXA_WORKER_MODEL_URL=http://127.0.0.1:11434" in text,
        "loopback default missing",
    )
    check(
        "env template contains no secret-looking literal",
        "plx_" not in text and "sk-" not in text.lower(),
        "a secret literal appears in the template",
    )


# ---------------------------------------------------------------------------
# 2. Refusals (exit 78 = EX_CONFIG)
# ---------------------------------------------------------------------------


def test_missing_env_file_refused() -> None:
    box = Sandbox()
    try:
        result = box.run(PLATRIXA_WORKER_ENV_FILE=str(box.root / "absent.env"))
        check(
            "missing env file exits 78",
            result.returncode == 78,
            f"rc={result.returncode} out={output_of(result)[:200]}",
        )
    finally:
        box.cleanup()


def test_missing_key_file_refused() -> None:
    box = Sandbox()
    try:
        box.key_file.unlink()
        result = box.run()
        check(
            "missing key file exits 78",
            result.returncode == 78,
            f"rc={result.returncode} out={output_of(result)[:200]}",
        )
    finally:
        box.cleanup()


def test_insecure_key_permissions_refused() -> None:
    box = Sandbox()
    try:
        box.key_file.chmod(0o644)
        result = box.run()
        text = output_of(result)
        check(
            "world-readable key file exits 78",
            result.returncode == 78,
            f"rc={result.returncode} out={text[:200]}",
        )
        check(
            "permission refusal names the chmod fix and not the key",
            "chmod 600" in text and KEY_VALUE not in text,
            text[:200],
        )
    finally:
        box.cleanup()


def test_missing_required_variable_refused() -> None:
    box = Sandbox()
    try:
        box.write_env(PLATRIXA_WORKER_ID="")
        result = box.run()
        check(
            "missing PLATRIXA_WORKER_ID exits 78",
            result.returncode == 78 and "PLATRIXA_WORKER_ID" in output_of(result),
            f"rc={result.returncode} out={output_of(result)[:200]}",
        )
    finally:
        box.cleanup()


# ---------------------------------------------------------------------------
# 3. Readiness behaviour
# ---------------------------------------------------------------------------


def test_model_never_ready_exits_tempfail() -> None:
    box = Sandbox()
    try:
        result = box.run(FAKE_CURL_FAILS="99", PLATRIXA_READINESS_ATTEMPTS="2")
        text = output_of(result)
        check(
            "a model that never answers exits 75 (EX_TEMPFAIL)",
            result.returncode == 75,
            f"rc={result.returncode} out={text[:300]}",
        )
        check(
            "the wait is logged with bounded attempts",
            "attempt 1/2" in text and "attempt 2/2" in text,
            text[:300],
        )
        check(
            "no job is started while the model is down",
            "starting worker" not in text,
            text[:300],
        )
    finally:
        box.cleanup()


def test_model_recovery_allows_start_dry_run() -> None:
    box = Sandbox()
    try:
        result = box.run(
            FAKE_CURL_FAILS="1", PLATRIXA_READINESS_ATTEMPTS="4", PLATRIXA_WORKER_DRY_RUN="1"
        )
        text = output_of(result)
        check(
            "a late model is waited for, then validated",
            result.returncode == 0 and "dry run OK" in text,
            f"rc={result.returncode} out={text[:300]}",
        )
    finally:
        box.cleanup()


# ---------------------------------------------------------------------------
# 4. Exact worker argv, no secrets
# ---------------------------------------------------------------------------


def test_worker_command_contract() -> None:
    box = Sandbox()
    try:
        result = box.run(PLATRIXA_WORKER_PRINT_COMMAND="1")
        text = output_of(result)
        expected = (
            "--api-base-url https://api.example.test",
            f"--api-key-file {box.key_file}",
            "--worker-id android-test-worker",
            "--model-url http://127.0.0.1:11434",
            "--model-id default",
            "--model-dialect chat",
            "--verbose",
        )
        check(
            "the worker is started with the configured loopback model and ids",
            result.returncode == 0 and all(piece in text for piece in expected),
            f"rc={result.returncode} out={text[:400]}",
        )
        check(
            "the printed command never contains the key value",
            KEY_VALUE not in text,
            "the key value leaked into the command output",
        )
        check(
            "the key is passed by file path, not value",
            "--api-key " not in text,
            text[:300],
        )
    finally:
        box.cleanup()


# ---------------------------------------------------------------------------
# 5. Duplicate protection
# ---------------------------------------------------------------------------


def test_live_duplicate_refused() -> None:
    box = Sandbox()
    try:
        # A live PID (this test process) owns the lock: a duplicate must refuse.
        box.lock_dir.mkdir(parents=True, exist_ok=True)
        (box.lock_dir / "pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
        result = box.run(PLATRIXA_WORKER_DRY_RUN="1")
        text = output_of(result)
        check(
            "a live duplicate is refused without starting a second worker",
            result.returncode == 0 and "not starting a duplicate" in text,
            f"rc={result.returncode} out={text[:300]}",
        )
    finally:
        box.cleanup()


def test_stale_lock_is_reclaimed() -> None:
    box = Sandbox()
    try:
        box.lock_dir.mkdir(parents=True, exist_ok=True)
        (box.lock_dir / "pid").write_text("999999999\n", encoding="utf-8")
        result = box.run(PLATRIXA_WORKER_DRY_RUN="1")
        text = output_of(result)
        check(
            "a stale lock from a dead worker is reclaimed",
            result.returncode == 0 and "dry run OK" in text,
            f"rc={result.returncode} out={text[:300]}",
        )
    finally:
        box.cleanup()


# ---------------------------------------------------------------------------
# 6. finish: backoff + rotation
# ---------------------------------------------------------------------------


def test_finish_backoff_and_rotation() -> None:
    box = Sandbox()
    try:
        log_file = box.log_dir / "worker.log"
        log_file.write_text("x" * 4096, encoding="utf-8")
        env = box.env(
            PLATRIXA_LOG_MAX_BYTES="1024",
            PLATRIXA_CONFIG_BACKOFF_S="0",
            PLATRIXA_RESTART_BACKOFF_S="0",
        )
        result = subprocess.run(
            ["sh", str(FINISH_SCRIPT), "78"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        check(
            "finish exits 0 (runit keeps supervising)",
            result.returncode == 0,
            f"rc={result.returncode} err={result.stderr[:200]}",
        )
        check(
            "finish rotates an oversized log",
            (box.log_dir / "worker.log.1").exists(),
            "worker.log.1 was not created",
        )
        # A clean exit restarts immediately (the sleep paths are 0 in this env).
        clean = subprocess.run(
            ["sh", str(FINISH_SCRIPT), "0"], env=env, capture_output=True, text=True, timeout=20
        )
        check("finish treats a clean exit as immediate", clean.returncode == 0, clean.stderr[:200])
    finally:
        box.cleanup()


# ---------------------------------------------------------------------------
# 7. installer refuses a non-Termux environment
# ---------------------------------------------------------------------------


def test_installer_requires_termux() -> None:
    result = subprocess.run(
        ["sh", str(INSTALL_SCRIPT)],
        env={**os.environ, "PREFIX": "/usr", "HOME": tempfile.gettempdir()},
        capture_output=True,
        text=True,
        timeout=20,
    )
    check(
        "installer refuses a non-Termux PREFIX",
        result.returncode == 2 and "must run inside Termux" in (result.stderr + result.stdout),
        f"rc={result.returncode} out={(result.stdout + result.stderr)[:200]}",
    )


def main() -> int:
    print("=" * 78)
    print("Platrixa S2 — Termux worker service definition checks")
    print("=" * 78)
    sections = (
        ("A. Scripts and env template", (test_shell_syntax, test_env_template)),
        (
            "B. Configuration refusals",
            (
                test_missing_env_file_refused,
                test_missing_key_file_refused,
                test_insecure_key_permissions_refused,
                test_missing_required_variable_refused,
            ),
        ),
        (
            "C. Model readiness wait",
            (test_model_never_ready_exits_tempfail, test_model_recovery_allows_start_dry_run),
        ),
        ("D. Worker command contract", (test_worker_command_contract,)),
        (
            "E. Duplicate protection",
            (test_live_duplicate_refused, test_stale_lock_is_reclaimed),
        ),
        ("F. Supervisor finish", (test_finish_backoff_and_rotation,)),
        ("G. Installer", (test_installer_requires_termux,)),
    )
    for title, tests in sections:
        print(f"\n{title}")
        for test in tests:
            try:
                test()
            except BaseException as exc:  # noqa: BLE001
                check(test.__name__, False, f"{type(exc).__name__}: {exc}")

    total = PASSED + len(FAILED)
    print("\n" + "=" * 78)
    print(f"RESULT: {PASSED}/{total} Termux service checks passed")
    for name, detail in FAILED:
        print(f"  FAIL {name} — {detail}")
    print("=" * 78)
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
