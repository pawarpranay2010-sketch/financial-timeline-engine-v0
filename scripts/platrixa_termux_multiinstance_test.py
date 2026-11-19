#!/usr/bin/env python3
"""M7 — two worker instances on one Android device (no phone required).

Proves the multi-instance contract without touching a device:

  * each instance resolves its OWN env file, key file, log, status file and
    process lock — no path is shared except the device-wide slot directory;
  * each instance is started with its own worker id and its own status file;
  * the device-wide limiter is shared and stays at the same cap;
  * a duplicate of instance A is refused while a live lock exists, and
    instance B is NOT affected by A's lock;
  * the installer targets platrixa-worker-<instance> and refuses to install a
    second instance whose key file is byte-identical to the default one
    (compared by path/content hash only — never printed);
  * no key VALUE ever appears in output.

Run:
    python3 scripts/platrixa_termux_multiinstance_test.py
"""

from __future__ import annotations

import http.server
import os
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUN = ROOT / "device/termux/service/platrixa-worker/run"
INSTALLER = ROOT / "device/termux/install-termux-service.sh"
PYTHON = sys.executable

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))
    return bool(ok)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _Models(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = b'{"object":"list","data":[{"id":"default"}]}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_a):  # silence
        return


def main() -> int:
    home = Path(tempfile.mkdtemp(prefix="platrixa-mi-"))
    port = _free_port()
    srv = http.server.HTTPServer(("127.0.0.1", port), _Models)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    model_url = f"http://127.0.0.1:{port}"

    # Termux-only tools the installer checks for. `sv`/`runsvdir` do not exist
    # on a Linux CI host, so stand in for them — the installer is being tested
    # for WHICH service it targets, not for runit itself (that is on-device).
    fake_bin = home / "bin"
    fake_bin.mkdir()
    for tool in ("sv", "runsvdir"):
        stub = fake_bin / tool
        stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        stub.chmod(0o755)

    (home / ".platrixa/logs").mkdir(parents=True)
    (home / ".platrixa/status").mkdir(parents=True)
    (home / ".platrixa/locks").mkdir(parents=True)

    def make_instance(name: str, key_value: str) -> Path:
        env = home / f".platrixa/worker-{name}.env"
        env.write_text(
            "\n".join(
                [
                    "PLATRIXA_API_BASE_URL=https://api.example.invalid",
                    f"PLATRIXA_WORKER_ID=android-worker-{name}",
                    f"PLATRIXA_WORKER_MODEL_URL={model_url}",
                    "PLATRIXA_WORKER_MODEL_ID=default",
                    "PLATRIXA_WORKER_MODEL_DIALECT=chat",
                    "PLATRIXA_WORKER_MODEL_TIMEOUT_S=120",
                    "PLATRIXA_SLOT_MAX=1",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        env.chmod(0o600)
        key = home / f".platrixa/worker-{name}.key"
        key.write_text(key_value, encoding="utf-8")
        key.chmod(0o600)
        return key

    key_a = make_instance("alice", "plx_test_alice_key_value_do_not_print_aaaaaaaa")
    key_b = make_instance("bob", "plx_test_bob_key_value_do_not_print_bbbbbbbbbb")

    base_env = {
        **os.environ,
        "HOME": str(home),
        "PREFIX": "/data/data/com.termux/files/usr",
        "PLATRIXA_REPO_DIR": str(ROOT),
        "PLATRIXA_PYTHON": PYTHON,
        "PLATRIXA_WORKER_PRINT_COMMAND": "1",
        "PLATRIXA_MODEL_HEALTH_URL": f"{model_url}/v1/models",
        "PLATRIXA_READINESS_ATTEMPTS": "3",
        "PLATRIXA_READINESS_INTERVAL": "0.1",
        "PATH": f"{home / 'bin'}:{os.environ.get('PATH', '')}",
    }

    def run_instance(name: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["sh", str(RUN)],
            env={**base_env, "PLATRIXA_WORKER_INSTANCE": name},
            capture_output=True,
            text=True,
            timeout=60,
        )

    a = run_instance("alice")
    b = run_instance("bob")
    check("instance alice starts from its own configuration", a.returncode == 0, a.stdout + a.stderr)
    check("instance bob starts from its own configuration", b.returncode == 0, b.stdout + b.stderr)

    out_a, out_b = a.stdout, b.stdout
    check("alice's worker id is its own", "android-worker-alice" in out_a and "android-worker-bob" not in out_a)
    check("bob's worker id is its own", "android-worker-bob" in out_b and "android-worker-alice" not in out_b)
    check(
        "each instance has its own status file",
        "worker-alice.json" in out_a and "worker-bob.json" in out_b and "worker-alice.json" not in out_b,
    )
    check(
        "each instance passes its own key FILE path (never the value)",
        "worker-alice.key" in out_a
        and "worker-bob.key" in out_b
        and key_a.read_text() not in out_a
        and key_b.read_text() not in out_b,
    )
    check(
        "both instances share the SAME device-wide slot directory and cap",
        f"--slot-dir {home}/.platrixa/slots" in out_a
        and f"--slot-dir {home}/.platrixa/slots" in out_b
        and "--slot-max 1" in out_a
        and "--slot-max 1" in out_b,
    )

    # duplicate protection is per instance
    lock = home / ".platrixa/locks/worker-alice.lock.d"
    # The run script releases its own lock on every non-exec exit; create the
    # directory explicitly to simulate a LIVE holder regardless.
    lock.mkdir(parents=True, exist_ok=True)
    (lock / "pid").write_text(str(os.getpid()), encoding="utf-8")
    dup_a = run_instance("alice")
    dup_b = run_instance("bob")
    check(
        "a duplicate of alice is refused while alice's lock is live",
        dup_a.returncode == 0 and "not starting a duplicate" in dup_a.stdout + dup_a.stderr,
        dup_a.stdout + dup_a.stderr,
    )
    check(
        "bob is unaffected by alice's live lock",
        dup_b.returncode == 0 and "android-worker-bob" in dup_b.stdout,
        dup_b.stdout + dup_b.stderr,
    )
    (lock / "pid").unlink()
    lock.rmdir()

    # installer targets the named service
    dry = subprocess.run(
        ["sh", str(INSTALLER), "--instance", "alice", "--dry-run"],
        env={**base_env, "PLATRIXA_WORKER_PRINT_COMMAND": "0"},
        capture_output=True,
        text=True,
        timeout=60,
    )
    check("installer runs for a named instance", dry.returncode == 0, dry.stdout + dry.stderr)
    check(
        "installer targets platrixa-worker-alice and worker-alice.env",
        "platrixa-worker-alice" in dry.stdout and "worker-alice.env" in dry.stdout,
        dry.stdout,
    )

    # a second instance must not reuse the default key file
    legacy = home / ".platrixa/worker.key"
    legacy.write_text(key_b.read_text(), encoding="utf-8")
    legacy.chmod(0o600)
    (home / ".platrixa/worker-bob.key").write_text(key_b.read_text(), encoding="utf-8")
    (home / ".platrixa/worker-bob.key").chmod(0o600)
    shared = subprocess.run(
        ["sh", str(INSTALLER), "--instance", "bob", "--dry-run"],
        env={**base_env, "PLATRIXA_WORKER_PRINT_COMMAND": "0"},
        capture_output=True,
        text=True,
        timeout=60,
    )
    check(
        "installer refuses an instance whose key file duplicates the legacy key",
        shared.returncode == 3,
        shared.stdout + shared.stderr,
    )

    srv.shutdown()
    print()
    passed = sum(1 for _n, ok, _d in CHECKS if ok)
    print("=" * 78)
    print(f"RESULT: {passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        for name, ok, detail in CHECKS:
            if not ok:
                print(f"  FAIL: {name} — {detail}")
        print("RESULT: FAILED")
        return 1
    print("RESULT: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
