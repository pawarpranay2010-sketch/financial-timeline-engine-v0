#!/usr/bin/env python3
"""
PLATRIXA — SECURITY REGRESSION: WEBHOOK SSRF + SECRET SEAL (fte_sec_03)
=======================================================================

Captures two findings from the 2026-09-29 security audit
(reports/security_audit_2026-09-29.md) BEFORE any fix is applied.

  H-03  Webhook delivery follows redirects. ``_deliver_webhooks`` calls
        ``requests.post(endpoint.url, data=body, headers=headers, timeout=5)``
        with NO ``allow_redirects=False``. ``requests`` defaults
        ``allow_redirects=True``, so an approved public HTTPS destination
        that answers 302 can steer the worker to an internal, private, or
        cloud-metadata address. ``_is_https_url()`` validates only at
        REGISTRATION time and never at DELIVERY time, and it never
        resolves DNS — so DNS rebinding passes it too.

  M-03  Webhook signing-secret "sealing" is UNAUTHENTICATED encryption:
        ``_seal_keystream`` is SHA256(key||nonce||counter) XOR with no MAC
        and no AEAD. Ciphertext is malleable — flipping bit n of the
        ciphertext flips bit n of the plaintext secret — and unsealing
        detects nothing.

SAFETY — NO REAL INTERNAL OR METADATA ADDRESS IS EVER CONTACTED
---------------------------------------------------------------
Redirect targets are modelled entirely inside a LOCAL mock HTTP server
bound to 127.0.0.1. The "internal"/"metadata" destinations are local
paths on that same server, flagged as forbidden. No request is ever sent
to 169.254.169.254, 10.0.0.0/8, 192.168.0.0/16 or any real host.

  * If delivery REFUSES to follow a redirect into the forbidden zone ->
    PASS
  * If delivery FOLLOWS it -> FAIL, and the mock server records that the
    forbidden path was reached.

These tests are EXPECTED TO FAIL on the current tree. Do not weaken them.

Run:  python3 scripts/fte_sec_03_webhook_ssrf_seal_test.py
"""

from __future__ import annotations

import inspect
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, List, Set

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# A fixed, non-secret test key for the seal. Not a credential.
os.environ.setdefault("PLATRIXA_WEBHOOK_SIGNING_KEY", "regression-test-seal-key-0123456789")

from backend.auth import async_jobs  # noqa: E402
from api.routes import async_api  # noqa: E402

# ---------------------------------------------------------------------------
# Shared check machinery (house style)
# ---------------------------------------------------------------------------

_PASS = 0
_FAIL = 0
_MESSAGES: List[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    global _PASS, _FAIL
    if ok:
        _PASS += 1
        print(f"  [PASS] {name}" + (f" — {detail}" if detail else ""))
    else:
        _FAIL += 1
        print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))
        _MESSAGES.append(f"{name} — {detail}")
    return ok


def expected_failure(name: str, detail: str) -> None:
    global _FAIL
    _FAIL += 1
    print(f"  [FAIL] {name} — {detail}")
    _MESSAGES.append(f"{name} — {detail}")


def note(text: str) -> None:
    print(f"  [NOTE] {text}")


# ---------------------------------------------------------------------------
# Local mock webhook server
# ---------------------------------------------------------------------------

# Paths that must NEVER be reached. Reaching one proves a redirect into an
# internal zone was followed. All are on 127.0.0.1 — no real internal or
# metadata address is involved.
FORBIDDEN_PATHS: Set[str] = {
    "/internal-private",
    "/internal-metadata",
    "/internal-localhost",
}
REACHED: List[str] = []


class _WebhookHandler(BaseHTTPRequestHandler):
    """Models an approved public endpoint plus redirect routes into a
    'forbidden' internal zone. Everything stays on 127.0.0.1."""

    def log_message(self, *args: Any) -> None:  # silence server logging
        pass

    def _hit(self) -> str:
        REACHED.append(self.path)
        return self.path

    def do_GET(self) -> None:  # noqa: N802
        self._hit()
        if self.path == "/approved":
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")
        elif self.path == "/redirect-to-internal":
            self.send_response(302)
            self.send_header("Location", "/internal-private")
            self.end_headers()
        elif self.path == "/redirect-to-metadata":
            self.send_response(302)
            self.send_header("Location", "/internal-metadata")
            self.end_headers()
        elif self.path == "/redirect-to-localhost":
            self.send_response(302)
            self.send_header("Location", "/internal-localhost")
            self.end_headers()
        elif self.path == "/internal-private":
            # Stands in for an RFC1918 / metadata destination.
            self.send_response(200)
            self.send_header("Content-Length", "4")
            self.end_headers()
            self.wfile.write(b"LEAK")
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        self.do_GET()


def _start_server() -> tuple:
    server = HTTPServer(("127.0.0.1", 0), _WebhookHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}"


# ---------------------------------------------------------------------------
# A. Registration-time validation (what exists today)
# ---------------------------------------------------------------------------


def test_registration_validation() -> None:
    print("\n--- A. _is_https_url registration-time validation ---")
    v = async_api._is_https_url

    for bad, label in (
        ("http://evil.example.com/hook", "plain http"),
        ("https://localhost/hook", "localhost"),
        ("https://127.0.0.1/hook", "loopback"),
        ("https://10.0.0.5/hook", "private (host:port form)"),
        ("https://169.254.169.254/latest/meta-data/", "raw IP literal"),
        ("https://foo.local/hook", "mDNS .local"),
    ):
        check(f"A1 rejects {label}", v(bad) is False, f"url={bad}")

    check("A2 accepts a public https host",
          v("https://hooks.example.com/x") is True)

    # A host that is a valid public NAME but resolves to an internal
    # address is accepted today. That is the DNS-rebinding gap: the check
    # never resolves DNS, so it cannot detect it. Asserted as a property
    # any delivery-time fix must satisfy.
    rebind = "https://rebind.attacker.example/hook"
    check("A3 registration accepts a public-looking hostname (documented)",
          v(rebind) is True,
          "DNS resolution is NOT performed — this is why delivery-time "
          "validation is required")


# ---------------------------------------------------------------------------
# B. H-03 — delivery must not follow redirects into a forbidden zone
# ---------------------------------------------------------------------------


def test_delivery_does_not_follow_redirects() -> None:
    print("\n--- B. H-03: webhook delivery redirect safety "
          "(EXPECTED TO FAIL today) ---")
    import requests

    server, base = _start_server()
    try:
        import requests  # noqa: F401  (imported for parity with the seam)
        # B0 — the delivery seam must pin redirect behaviour.
        src = inspect.getsource(async_api._post_webhook)
        pins_redirects = "allow_redirects=False" in src
        if pins_redirects:
            check("B0 delivery pins allow_redirects=False", True)
        else:
            expected_failure(
                "B0 delivery pins allow_redirects=False",
                "H-03: the delivery seam calls requests.post() with no "
                "allow_redirects argument; requests defaults to True, so a "
                "302 is followed out of the validated host",
            )

        # B0b — delivery must re-validate the resolved destination, so a host
        # that changes answer after registration cannot be used.
        revalidates = hasattr(async_api, "_assert_safe_delivery_target") and (
            "_assert_safe_delivery_target" in src
        )
        if revalidates:
            check("B0b delivery re-validates the resolved target", True)
        else:
            expected_failure(
                "B0b delivery re-validates the resolved target",
                "H-03: registration-time validation alone is not sufficient; "
                "a re-pointed or rebound host would still be contacted",
            )

        # B1..B3 — behavioural proof against the local mock, exercising the
        # REAL delivery seam. The target check is stubbed for this section so
        # that the REDIRECT property is tested in isolation (127.0.0.1 would
        # otherwise be refused before a redirect is even possible). The
        # target check itself is asserted separately in section B5.
        original_check = async_api._assert_safe_delivery_target
        async_api._assert_safe_delivery_target = lambda url: None
        try:
            for route, label in (
                ("/redirect-to-internal", "approved -> internal/private"),
                ("/redirect-to-metadata", "approved -> metadata-style"),
                ("/redirect-to-localhost", "approved -> localhost"),
            ):
                REACHED.clear()
                url = f"{base}{route}"
                try:
                    resp = async_api._post_webhook(url, "{}", {"Content-Type": "application/json"})
                    followed = resp.status_code == 200
                except Exception as exc:
                    followed = False
                    note(f"{route} raised {type(exc).__name__}: {str(exc)[:60]}")

                leaked = sorted(set(REACHED) & FORBIDDEN_PATHS)
                if leaked:
                    expected_failure(
                        f"B redirect not followed ({label})",
                        f"H-03 SSRF: delivery followed the redirect and reached "
                        f"the forbidden zone {leaked}",
                    )
                else:
                    check(f"B redirect not followed ({label})", True,
                          f"status={'200' if followed else 'not followed'}, "
                          f"paths_hit={sorted(set(REACHED))}")

            # B4 — control: the approved destination is still deliverable.
            REACHED.clear()
            try:
                resp = async_api._post_webhook(
                    f"{base}/approved", "{}", {"Content-Type": "application/json"})
                check("B4 approved destination is still deliverable",
                      resp.status_code == 200, f"HTTP {resp.status_code}")
            except Exception as exc:
                expected_failure("B4 approved destination is still deliverable",
                                 f"mock server unreachable: {exc}")
        finally:
            async_api._assert_safe_delivery_target = original_check
    finally:
        server.shutdown()
        server.server_close()

    # B5 — the delivery-time target check, unit level. This is the
    # DNS-rebinding / private-address property, asserted on REAL addresses.
    # No connection is made; only name resolution / address classification.
    def _rejects(host_url: str) -> str:
        try:
            async_api._assert_safe_delivery_target(host_url)
            return "ACCEPTED"
        except Exception as exc:
            return f"rejected ({str(exc)[:44]})"

    for url, label in (
        ("https://127.0.0.1/hook", "loopback literal"),
        ("https://localhost/hook", "localhost"),
        ("https://10.0.0.5/hook", "RFC1918 private"),
        ("https://192.168.1.1/hook", "RFC1918 private"),
        ("https://169.254.169.254/latest/meta-data/", "link-local / metadata"),
    ):
        outcome = _rejects(url)
        if outcome == "ACCEPTED":
            expected_failure(
                f"B5 delivery-time check rejects {label}",
                f"H-03: _assert_safe_delivery_target accepted {url}",
            )
        else:
            check(f"B5 delivery-time check rejects {label}", True, outcome)


# ---------------------------------------------------------------------------
# C. M-03 — sealed webhook secrets must be tamper-evident
# ---------------------------------------------------------------------------


def test_webhook_secret_seal_is_authenticated() -> None:
    print("\n--- C. M-03: webhook secret seal integrity "
          "(EXPECTED TO FAIL today) ---")

    plain = "whsec_regression_test_secret_value"
    sealed = async_jobs.seal_webhook_secret(plain)
    check("C0 seal/unseal round-trips", async_jobs.unseal_webhook_secret(sealed) == plain)

    # C1 — the stored form must not be plaintext.
    check("C1 sealed value is not plaintext", plain not in sealed,
          f"sealed={sealed[:24]}...")

    # C2 — THE DEFECT. Flip one bit of the ciphertext and attempt unseal.
    # An authenticated scheme (AEAD / HMAC) MUST reject it.
    #
    # The tamper is applied to whatever format is current, so the test does
    # not silently pass by assuming a version:
    #   v2 -> base64url(nonce || ciphertext || tag); flip a bit in the
    #         ciphertext region (after the 12-byte nonce).
    #   v1 -> hex(nonce || ciphertext); flip a bit in the ciphertext.
    import base64 as _b64

    version, _, payload = sealed.partition(":")
    if version == "v2":
        blob = bytearray(_b64.urlsafe_b64decode(payload.encode("ascii")))
        blob[12] ^= 0x01               # single bit flip inside the ciphertext
        tampered = f"{version}:{_b64.urlsafe_b64encode(bytes(blob)).decode('ascii')}"
    else:
        parts = payload.split(":")
        ct = bytearray(bytes.fromhex(parts[1]))
        ct[0] ^= 0x01
        tampered = f"{version}:{parts[0]}:{bytes(ct).hex()}"

    detected = False
    recovered = None
    try:
        recovered = async_jobs.unseal_webhook_secret(tampered)
    except Exception:
        detected = True

    if detected:
        check("C2 tampered ciphertext is rejected", True, f"seal version={version}")
    else:
        expected_failure(
            "C2 tampered ciphertext is rejected",
            f"M-03 MALLEABLE: a one-bit ciphertext change unsealed silently "
            f"to {recovered!r} instead of a secret of {len(plain)} chars — "
            "the seal is unauthenticated, so it provides no integrity",
        )

    # C3 — the practical consequence: an attacker with storage write access
    # can force a chosen signing secret, forging every future event.
    if not detected and recovered is not None:
        try:
            recovered.encode("utf-8")
            utf8_ok = True
        except Exception:
            utf8_ok = False
        if utf8_ok:
            expected_failure(
                "C3 tampering must not yield a usable signing secret",
                "M-03: a tampered seal produced a well-formed secret, so "
                "webhook signatures for this tenant could be forged",
            )
        else:
            check("C3 tampering must not yield a usable signing secret", True,
                  "tampered value failed UTF-8 decode (accidental, not a control)")

    # C4 — assert the CURRENT seal path is authenticated, not the legacy
    # helper (which is retained only for the documented v1 refusal path).
    seal_src = inspect.getsource(async_jobs.seal_webhook_secret)
    unseal_src = inspect.getsource(async_jobs.unseal_webhook_secret)
    has_aead = bool(re.search(r"AESGCM|ChaCha20Poly1305|Fernet", seal_src, re.I))
    verifies = bool(re.search(r"InvalidTag|compare_digest|decrypt", unseal_src))
    if has_aead and verifies:
        check("C4 seal construction is authenticated", True,
              "AES-GCM seal + InvalidTag verification")
    else:
        expected_failure(
            "C4 seal construction is authenticated",
            f"M-03: seal uses AEAD={has_aead}, unseal verifies={verifies}; "
            "an unauthenticated construction must not protect signing secrets",
        )

    # C5 — legacy v1 blobs must be REFUSED, not silently accepted. Accepting
    # them would restore the malleability this change removes.
    legacy = "v1:" + "00" * 16 + ":" + "00" * 16
    try:
        out = async_jobs.unseal_webhook_secret(legacy)
        expected_failure(
            "C5 legacy v1 seal must be refused",
            f"M-03 MIGRATION: a v1 blob was accepted and returned {out!r}; "
            "unauthenticated legacy data must fail closed",
        )
    except Exception:
        check("C5 legacy v1 seal must be refused", True, "fails closed")


# ---------------------------------------------------------------------------


def main() -> int:
    print("=" * 78)
    print("PLATRIXA — SECURITY REGRESSION: WEBHOOK SSRF + SECRET SEAL")
    print("Captures audit findings H-03 and M-03 (pre-fix).")
    print("All redirect targets are LOCAL mock paths; no real internal or")
    print("metadata address is contacted.")
    print("=" * 78)

    test_registration_validation()
    test_delivery_does_not_follow_redirects()
    test_webhook_secret_seal_is_authenticated()

    print()
    print("=" * 78)
    if _FAIL == 0:
        print(f"RESULT: PASS — {_PASS}/{_PASS + _FAIL} checks passed")
    else:
        print(f"RESULT: FAIL — {_PASS} passed, {_FAIL} failed (expected pre-fix)")
        print("\nOutstanding defects (audit findings this suite locks in):")
        for m in _MESSAGES:
            print(f"  - {m}")
    print("=" * 78)
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
