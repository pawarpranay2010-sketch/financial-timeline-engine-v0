"""Security tests for the trusted client-IP path (Phase 2).

The backend cannot read the visitor address: the Cloudflare Pages proxy
forwards an allowlist of headers and Railway rewrites X-Forwarded-For /
X-Real-IP with the edge address. A per-visitor cap therefore has to come from a
claim the edge SIGNS and binds to the request.

These tests pin the trust rules:

  * a correctly signed claim is used;
  * a claim without a signature, with a wrong signature, bound to a different
    request's assertion, or sent outside the website surface is ignored;
  * a malformed address is ignored;
  * a missing claim falls back to a non-forgeable source;
  * two different clients land in two different buckets;
  * the canonical string matches the shared golden vector used by the
    JavaScript edge implementation.

Run:
    python -m pytest tests/test_client_ip_claim.py -v
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
VECTOR_PATH = REPO_ROOT / "tests" / "fixtures" / "client_ip_claim_vector.json"

PEER = "10.0.0.9"


@pytest.fixture()
def vector():
    return json.loads(VECTOR_PATH.read_text(encoding="utf-8"))


@pytest.fixture()
def secret(monkeypatch, vector):
    monkeypatch.setenv("PLATRIXA_VISITOR_HMAC_SECRET", vector["secret"])
    return vector["secret"]


def _assertion(signature: str) -> str:
    """A well-formed assertion carrying ``signature`` as its HMAC field."""
    return (
        "V1|platrixa|11111111-2222-4333-8444-555555555555|GET|/v1/developer/access-status|"
        "|e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855|"
        "1700000000|nonce-1234|" + signature
    )


class _Request:
    def __init__(self, headers=None, peer=PEER):
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.client = SimpleNamespace(host=peer)


def _signed_headers(vector, client_ip, *, assertion_signature=None, surface="website"):
    from backend.auth import client_ip_claim

    bound_to = assertion_signature or vector["assertion_signature"]
    return {
        client_ip_claim.SURFACE_HEADER: surface,
        client_ip_claim.ASSERTION_HEADER: _assertion(bound_to),
        client_ip_claim.CLAIM_HEADER: client_ip,
        client_ip_claim.CLAIM_SIGNATURE_HEADER: client_ip_claim.sign(
            vector["secret"], client_ip, bound_to
        ),
    }


# ---------------------------------------------------------------------------
# The cross-language contract
# ---------------------------------------------------------------------------


def test_golden_vector_matches_the_shared_fixture(vector):
    from backend.auth import client_ip_claim

    for entry in vector["vectors"]:
        assert (
            client_ip_claim.sign(
                vector["secret"], entry["client_ip"], vector["assertion_signature"]
            )
            == entry["signature"]
        )
        assert client_ip_claim.canonical_message(
            entry["client_ip"], vector["assertion_signature"]
        ) == f"{vector['canonical_prefix']}|{entry['client_ip']}|{vector['assertion_signature']}"


# ---------------------------------------------------------------------------
# Accepted claim
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("index", [0, 1])
def test_a_correctly_signed_claim_is_the_bucket_key(monkeypatch, vector, secret, index):
    from api.rate_limit import _client_key

    entry = vector["vectors"][index]
    request = _Request(_signed_headers(vector, entry["client_ip"]))
    assert _client_key(request) == entry["client_ip"]


def test_the_claim_beats_every_header_based_fallback(monkeypatch, vector, secret):
    from api.rate_limit import _client_key

    monkeypatch.setenv("PLATRIXA_CLIENT_IP_HEADER", "cf-connecting-ip")
    monkeypatch.setenv("PLATRIXA_TRUSTED_PROXY_COUNT", "1")
    headers = _signed_headers(vector, "203.0.113.7")
    headers["cf-connecting-ip"] = "192.0.2.99"
    headers["x-forwarded-for"] = "192.0.2.99"
    assert _client_key(_Request(headers)) == "203.0.113.7"


def test_two_clients_get_two_buckets(monkeypatch, vector, secret):
    from api.rate_limit import _client_key

    first = _Request(_signed_headers(vector, "203.0.113.7"))
    second = _Request(_signed_headers(vector, "198.51.100.4"))
    assert _client_key(first) != _client_key(second)
    assert _client_key(first) == "203.0.113.7"
    assert _client_key(second) == "198.51.100.4"


def test_ipv6_claim_is_canonicalised_after_verification(monkeypatch, vector, secret):
    from api.rate_limit import _client_key

    request = _Request(_signed_headers(vector, "2001:db8::1"))
    assert _client_key(request) == "2001:db8::1"


# ---------------------------------------------------------------------------
# Rejected claims — every one falls back to the transport peer
# ---------------------------------------------------------------------------


def test_a_spoofed_claim_without_a_signature_is_ignored(monkeypatch, vector, secret):
    from api.rate_limit import _client_key

    request = _Request(
        {
            "x-platrixa-surface": "website",
            "x-platrixa-visitor-assertion": _assertion(vector["assertion_signature"]),
            "x-platrixa-client-ip": "203.0.113.7",
        }
    )
    assert _client_key(request) == PEER


def test_a_claim_signed_with_the_wrong_secret_is_ignored(monkeypatch, vector):
    from api.rate_limit import _client_key

    monkeypatch.setenv("PLATRIXA_VISITOR_HMAC_SECRET", "a-different-visitor-secret")
    headers = {
        "x-platrixa-surface": "website",
        "x-platrixa-visitor-assertion": _assertion(vector["assertion_signature"]),
        "x-platrixa-client-ip": "203.0.113.7",
        # Signature produced with the REAL secret.
        "x-platrixa-client-ip-signature": vector["vectors"][0]["signature"],
    }
    assert _client_key(_Request(headers)) == PEER


def test_a_claim_bound_to_another_requests_assertion_is_ignored(monkeypatch, vector, secret):
    from api.rate_limit import _client_key

    # Signature signed for assertion A, sent with assertion B.
    headers = _signed_headers(
        vector, "203.0.113.7", assertion_signature=vector["assertion_signature"]
    )
    headers["x-platrixa-visitor-assertion"] = _assertion("ff" * 32)
    assert _client_key(_Request(headers)) == PEER


def test_a_signature_for_a_different_ip_is_ignored(monkeypatch, vector, secret):
    from api.rate_limit import _client_key

    headers = _signed_headers(vector, "198.51.100.4")
    headers["x-platrixa-client-ip"] = "203.0.113.7"
    assert _client_key(_Request(headers)) == PEER


def test_a_claim_outside_the_website_surface_is_ignored(monkeypatch, vector, secret):
    from api.rate_limit import _client_key

    # A data-plane caller can send any header it likes; without the surface
    # marker the claim is not even considered.
    headers = _signed_headers(vector, "203.0.113.7", surface="developer")
    assert _client_key(_Request(headers)) == PEER


def test_a_claim_without_an_assertion_is_ignored(monkeypatch, vector, secret):
    from api.rate_limit import _client_key

    headers = _signed_headers(vector, "203.0.113.7")
    headers.pop("x-platrixa-visitor-assertion")
    assert _client_key(_Request(headers)) == PEER


@pytest.mark.parametrize(
    "bad_ip",
    ["", "not-an-ip", "203.0.113.7, 198.51.100.4", "203.0.113.7:443:1", "203.0.113.7\r\nx: y"],
)
def test_a_malformed_claimed_address_is_ignored(monkeypatch, vector, secret, bad_ip):
    from api.rate_limit import _client_key

    headers = _signed_headers(vector, bad_ip)
    # The signature is valid over the exact bytes; the address itself is not.
    assert _client_key(_Request(headers)) == PEER


def test_a_claim_is_not_trusted_without_a_configured_secret(vector, monkeypatch):
    from api.rate_limit import _client_key

    monkeypatch.delenv("PLATRIXA_VISITOR_HMAC_SECRET", raising=False)
    assert _client_key(_Request(_signed_headers(vector, "203.0.113.7"))) == PEER


def test_verifier_never_raises_on_garbage():
    from backend.auth import client_ip_claim

    assert client_ip_claim.verified_client_ip(
        surface="website", assertion=None, client_ip="1.2.3.4", signature="0" * 64
    ) is None
    assert client_ip_claim.verified_client_ip(
        surface=None, assertion="|", client_ip=None, signature=None
    ) is None
    assert client_ip_claim.assertion_signature("V1|platrixa") is None
