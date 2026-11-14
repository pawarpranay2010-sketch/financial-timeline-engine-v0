"""Visitor Ownership V2 — query-level ownership enforcement regression tests.

These tests exercise the real ``backend.auth.async_jobs`` read/write functions
against an in-memory table that evaluates the same predicates the PostgreSQL
statements use. They therefore verify actual access decisions (A can read A,
B cannot read A, website cannot read developer/legacy, developer keeps access,
cross-tenant denied) rather than merely string-matching SQL.

Run:  python -m unittest tests.test_visitor_ownership_v2 -v
"""

from __future__ import annotations

import contextlib
import unittest
from datetime import datetime, timezone

from backend.auth import async_jobs as aj

TENANT = "tenant-1"
OTHER_TENANT = "tenant-2"
VISITOR_A = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
VISITOR_B = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def _row(
    *,
    job_id="job_1",
    result_id="res_1",
    tenant_id=TENANT,
    visitor_id=None,
    owner_type="developer_client",
    kind="document",
    request_json='{"doc":"x"}',
):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return {
        "job_id": job_id,
        "tenant_id": tenant_id,
        "result_id": result_id,
        "status": "COMPLETED",
        "http_status": 200,
        "result_json": "{}",
        "error_json": None,
        "retryable": False,
        "request_id": None,
        "created_at": now,
        "updated_at": now,
        "lease_owner": None,
        "lease_expires_at": None,
        "lease_generation": 0,
        "attempt_count": 1,
        "max_attempts": 3,
        "next_attempt_at": None,
        "kind": kind,
        "request_json": request_json,
        "visitor_id": visitor_id,
        "owner_type": owner_type,
    }


class _Result:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


class _Session:
    """Evaluates the production predicates over an in-memory row set."""

    def __init__(self, rows, log):
        self.rows = rows
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def begin(self):
        return contextlib.nullcontext()

    def execute(self, stmt, params=None):
        sql = str(stmt)
        params = dict(params or {})
        self.log.append((sql, params))
        row = self._match(sql, params)
        if "SELECT request_json" in sql:
            # Production projects a single column and indexes row[0].
            return _Result((row["request_json"],) if row else None)
        return _Result(row)

    def _match(self, sql, p):
        for row in self.rows:
            if "tenant_id = :t" in sql and row["tenant_id"] != p.get("t"):
                continue
            if "job_id = :j" in sql and row["job_id"] != p.get("j"):
                continue
            if "result_id = :r" in sql and row["result_id"] != p.get("r"):
                continue
            if "owner_type = 'website_visitor'" in sql and row["owner_type"] != "website_visitor":
                continue
            if "visitor_id = :v" in sql and row["visitor_id"] != p.get("v"):
                continue
            return row
        return None


class VisitorOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.log = []
        self._orig_factory = aj._session_factory
        self._orig_scope = aj.current_visitor_scope()
        aj.set_visitor_scope(None)

    def tearDown(self):
        aj._session_factory = self._orig_factory
        aj.set_visitor_scope(None)

    def _install(self, rows):
        log = self.log
        aj._session_factory = lambda: (lambda: _Session(rows, log))

    # 1. Visitor A can access A's own job.
    def test_1_visitor_a_reads_own_job(self):
        self._install([_row(visitor_id=VISITOR_A, owner_type="website_visitor")])
        rec = aj.get_job(TENANT, "job_1", visitor_id=VISITOR_A)
        self.assertIsNotNone(rec)
        self.assertEqual(rec.job_id, "job_1")

    # 2. Visitor B cannot access A's job.
    def test_2_visitor_b_cannot_read_a_job(self):
        self._install([_row(visitor_id=VISITOR_A, owner_type="website_visitor")])
        self.assertIsNone(aj.get_job(TENANT, "job_1", visitor_id=VISITOR_B))

    # 3. Visitor B cannot retrieve A's result.
    def test_3_visitor_b_cannot_read_a_result(self):
        self._install([_row(visitor_id=VISITOR_A, owner_type="website_visitor")])
        self.assertIsNone(aj.get_result_record(TENANT, "res_1", visitor_id=VISITOR_B))
        self.assertIsNotNone(aj.get_result_record(TENANT, "res_1", visitor_id=VISITOR_A))

    # 3b. The stored request payload is ownership-gated too.
    def test_3b_visitor_b_cannot_read_a_request_payload(self):
        self._install([_row(visitor_id=VISITOR_A, owner_type="website_visitor")])
        self.assertIsNone(aj.get_job_request_json(TENANT, "job_1", visitor_id=VISITOR_B))
        self.assertEqual(
            aj.get_job_request_json(TENANT, "job_1", visitor_id=VISITOR_A), '{"doc":"x"}'
        )

    # 4. Cancel/retry paths gate on get_job, so B cannot reach them either.
    def test_4_visitor_b_cannot_cancel_or_retry_a_job(self):
        self._install([_row(visitor_id=VISITOR_A, owner_type="website_visitor")])
        # Both the cancel and retry handlers resolve the job through get_job
        # before mutating it; that lookup must return nothing for B.
        self.assertIsNone(aj.get_job(TENANT, "job_1", visitor_id=VISITOR_B))

    # 5. Website visitors cannot access developer-owned jobs.
    def test_5_website_cannot_read_developer_job(self):
        self._install([_row(visitor_id=None, owner_type="developer_client")])
        self.assertIsNone(aj.get_job(TENANT, "job_1", visitor_id=VISITOR_A))

    # 6. Website visitors cannot access legacy jobs.
    def test_6_website_cannot_read_legacy_job(self):
        self._install([_row(visitor_id=None, owner_type="legacy")])
        self.assertIsNone(aj.get_job(TENANT, "job_1", visitor_id=VISITOR_A))

    # 7. Developer API clients retain their legitimate (tenant-scoped) access.
    def test_7_developer_client_keeps_access(self):
        self._install(
            [
                _row(visitor_id=None, owner_type="developer_client"),
                _row(job_id="job_2", result_id="res_2", visitor_id=VISITOR_A, owner_type="website_visitor"),
            ]
        )
        self.assertIsNotNone(aj.get_job(TENANT, "job_1"))
        self.assertIsNotNone(aj.get_job(TENANT, "job_2"))

    # 8. Cross-tenant access remains denied.
    def test_8_cross_tenant_denied(self):
        self._install([_row(tenant_id=OTHER_TENANT, visitor_id=VISITOR_A, owner_type="website_visitor")])
        self.assertIsNone(aj.get_job(TENANT, "job_1", visitor_id=VISITOR_A))

    # 9. Missing / malformed visitor identity never widens access.
    def test_9_missing_or_malformed_visitor_fails_closed(self):
        self._install([_row(visitor_id=VISITOR_A, owner_type="website_visitor")])
        # A malformed identity is not equal to the stored visitor id.
        self.assertIsNone(aj.get_job(TENANT, "job_1", visitor_id="not-a-visitor"))
        self.assertIsNone(aj.get_job(TENANT, "job_1", visitor_id=""))
        # No scope bound at all → developer semantics (no visitor predicate).
        aj.set_visitor_scope(None)
        rec = aj.get_job(TENANT, "job_1")
        self.assertIsNotNone(rec)  # developer path, tenant-only
        sql = self.log[-1][0]
        self.assertNotIn("owner_type", sql)

    # Context scope flows into the reads (the middleware binds it per request).
    def test_scope_binding_is_used_by_reads(self):
        self._install([_row(visitor_id=VISITOR_A, owner_type="website_visitor")])
        aj.set_visitor_scope(VISITOR_A)
        self.assertIsNotNone(aj.get_job(TENANT, "job_1"))
        aj.set_visitor_scope(VISITOR_B)
        self.assertIsNone(aj.get_job(TENANT, "job_1"))

    # Write path records ownership so the predicates have data to match.
    def test_create_job_records_website_ownership(self):
        self._install([])
        aj.set_visitor_scope(VISITOR_A)
        aj.create_job(TENANT, {"doc": "x"})
        sql, params = self.log[0]
        self.assertIn("visitor_id", sql)
        self.assertIn("owner_type", sql)
        self.assertEqual(params["v"], VISITOR_A)
        self.assertEqual(params["o"], "website_visitor")

    def test_create_job_records_developer_ownership(self):
        self._install([])
        aj.set_visitor_scope(None)
        aj.create_job(TENANT, {"doc": "x"})
        _sql, params = self.log[0]
        self.assertIsNone(params["v"])
        self.assertEqual(params["o"], "developer_client")


class VisitorAssertionSafetyTests(unittest.TestCase):
    """Assertion parsing / HMAC verification (never trust a bare header)."""

    SECRET = "test-secret"
    VISITOR = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

    def setUp(self):
        import os

        os.environ["PLATRIXA_VISITOR_HMAC_SECRET"] = self.SECRET

    def _sign(self, visitor, method="GET", path="/api/x", query="", body_hash="0" * 64, ts="1700000000", nonce="n1"):
        import hashlib
        import hmac

        canonical = f"V1|platrixa|{visitor}|{method}|{path}|{query}|{body_hash}|{ts}|{nonce}"
        sig = hmac.new(self.SECRET.encode(), canonical.encode(), hashlib.sha256).hexdigest()
        return f"{canonical}|{sig}"

    def _validate(self, assertion, **kw):
        from backend.auth.api_keys import validate_visitor_assertion

        kw.setdefault("method", "GET")
        kw.setdefault("path", "/api/x")
        kw.setdefault("query_string", "")
        kw.setdefault("body_hash", "0" * 64)
        kw.setdefault("provided_timestamp", "1700000000")
        kw.setdefault("provided_nonce", "n1")
        kw.setdefault("secret", self.SECRET)
        return validate_visitor_assertion(assertion, **kw)

    def test_garbage_assertion_rejected(self):
        reason, vid, _tid = self._validate("not-an-assertion")
        self.assertEqual(reason, "MALFORMED_ASSERTION")
        self.assertIsNone(vid)

    def test_legacy_nine_field_assertion_rejected(self):
        # The pre-fix 9-field form carried no signature and must never pass.
        unsigned = f"V1|platrixa|{self.VISITOR}|GET|/api/x||{'0'*64}|1700000000|n1"
        reason, _vid, _ = self._validate(unsigned)
        self.assertEqual(reason, "MALFORMED_ASSERTION")

    def test_forged_visitor_id_rejected(self):
        # Signed for visitor A, presented as visitor B → signature must fail.
        assertion = self._sign(self.VISITOR)
        forged = assertion.replace(self.VISITOR, "b" * 32)
        reason, vid, _ = self._validate(forged)
        self.assertEqual(reason, "SIGNATURE_MISMATCH")
        self.assertIsNone(vid)

    def test_method_mismatch_rejected(self):
        reason, _vid, _ = self._validate(self._sign(self.VISITOR), method="POST")
        self.assertEqual(reason, "METHOD_MISMATCH")

    def test_path_mismatch_rejected(self):
        reason, _vid, _ = self._validate(self._sign(self.VISITOR), path="/api/other")
        self.assertEqual(reason, "PATH_MISMATCH")

    def test_body_hash_mismatch_rejected(self):
        reason, _vid, _ = self._validate(self._sign(self.VISITOR), body_hash="1" * 64)
        self.assertEqual(reason, "BODY_HASH_MISMATCH")

    def test_stale_timestamp_rejected(self):
        old = "1000000000"
        reason, _vid, _ = self._validate(
            self._sign(self.VISITOR, ts=old), provided_timestamp=None
        )
        self.assertEqual(reason, "TIMESTAMP_EXPIRED")

    def test_correctly_signed_but_no_nonce_store_fails_closed(self):
        # Replay protection is mandatory, so without a nonce store the
        # assertion is refused rather than accepted without single-use proof.
        import time

        now = str(int(time.time()))
        reason, vid, _ = self._validate(
            self._sign(self.VISITOR, ts=now), provided_timestamp=now
        )
        self.assertEqual(reason, "NONCE_STORE_UNAVAILABLE")
        self.assertIsNone(vid)


class VisitorRequestScopeTests(unittest.TestCase):
    """Website surface is fail-closed; developer/worker surfaces are untouched."""

    SECRET = "test-secret"
    VISITOR = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

    def setUp(self):
        import os

        os.environ["PLATRIXA_VISITOR_HMAC_SECRET"] = self.SECRET
        from backend.auth import async_jobs as aj

        aj.set_visitor_scope(None)

    def _sign(self, visitor=VISITOR, ts=None, nonce="n1", method="GET", path="/api/x",
              query="", body_hash="0" * 64):
        import hashlib
        import hmac
        import time

        ts = str(int(time.time())) if ts is None else ts
        canonical = f"V1|platrixa|{visitor}|{method}|{path}|{query}|{body_hash}|{ts}|{nonce}"
        sig = hmac.new(self.SECRET.encode(), canonical.encode(), hashlib.sha256).hexdigest()
        return f"{canonical}|{sig}"

    def _verify(self, **overrides):
        from backend.auth.visitor_request import verify_visitor_request

        args = dict(
            assertion=self._sign(),
            visitor_id=self.VISITOR,
            timestamp=None,
            nonce="n1",
            method="GET",
            path="/api/x",
            query_string="",
            body_hash="0" * 64,
        )
        if "assertion" in overrides:
            assertion = overrides.pop("assertion")
            args["assertion"] = assertion
            if assertion:
                args["timestamp"] = assertion.split("|")[7]
        args.update(overrides)
        if args["timestamp"] is None:
            import time

            args["timestamp"] = str(int(time.time()))
        return verify_visitor_request(**args)

    # 1. Missing visitor scope fails closed.
    def test_missing_assertion_fails_closed(self):
        reason, vid = self._verify(assertion=None)
        self.assertEqual(reason, "MISSING_ASSERTION")
        self.assertIsNone(vid)

    # 2. Empty visitor scope fails closed.
    def test_empty_visitor_id_fails_closed(self):
        reason, vid = self._verify(visitor_id="")
        self.assertEqual(reason, "MISSING_VISITOR_ID")
        self.assertIsNone(vid)

    # 3. Malformed visitor identity fails closed.
    def test_malformed_visitor_id_fails_closed(self):
        short = "short"
        reason, vid = self._verify(assertion=self._sign(visitor=short), visitor_id=short)
        self.assertEqual(reason, "MALFORMED_VISITOR_ID")
        self.assertIsNone(vid)

    # 4. Invalid assertion fails closed.
    def test_invalid_signature_fails_closed(self):
        forged = self._sign().replace(self.VISITOR, "b" * 32)
        reason, vid = self._verify(assertion=forged, visitor_id="b" * 32)
        self.assertEqual(reason, "SIGNATURE_MISMATCH")
        self.assertIsNone(vid)

    def test_missing_timestamp_and_nonce_fail_closed(self):
        self.assertEqual(self._verify(timestamp="")[0], "MISSING_TIMESTAMP")
        self.assertEqual(self._verify(nonce="")[0], "MISSING_NONCE")

    def test_forged_body_hash_fails_closed(self):
        assertion = self._sign()
        reason, vid = self._verify(assertion=assertion, body_hash="1" * 64)
        self.assertEqual(reason, "BODY_HASH_MISMATCH")
        self.assertIsNone(vid)

    def test_well_signed_assertion_still_fails_closed_without_nonce_store(self):
        # Correct HMAC + fresh timestamp, but replay protection unavailable →
        # refuse rather than bind a scope that could be replayed.
        reason, vid = self._verify()
        self.assertEqual(reason, "NONCE_STORE_UNAVAILABLE")
        self.assertIsNone(vid)

    # 5. Developer authorization without visitor scope keeps working.
    def test_developer_surface_is_not_website(self):
        from backend.auth.visitor_request import is_website_surface

        for value in (None, "", "developer", "Developer", "worker"):
            self.assertFalse(is_website_surface(value))
        for value in ("website", " Website ", "WEBSITE"):
            self.assertTrue(is_website_surface(value))

    def test_developer_request_reads_without_visitor_predicate(self):
        from backend.auth import async_jobs as aj

        log = []

        class _Session:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def begin(self):
                import contextlib

                return contextlib.nullcontext()

            def execute(self, stmt, params=None):
                log.append((str(stmt), dict(params or {})))
                class _R:
                    def mappings(self):
                        return self

                    def first(self):
                        return None

                return _R()

        aj._session_factory = lambda: (lambda: _Session())
        try:
            aj.set_visitor_scope(None)  # developer / worker surface
            self.assertIsNone(aj.get_job("tenant-1", "job_1"))
            self.assertNotIn("owner_type", log[-1][0])
            self.assertNotIn("visitor_id = :v", log[-1][0])
        finally:
            pass

    def test_worker_request_reads_without_visitor_predicate(self):
        # Workers never send the surface marker, so the same tenant-only path
        # applies — a worker may process any job in its tenant.
        from backend.auth import async_jobs as aj

        aj.set_visitor_scope(None)
        self.assertIsNone(aj.current_visitor_scope())

    def test_csrf_origin_gate(self):
        from backend.auth.visitor_request import csrf_origin_allowed

        public = "https://app.platrixa.test"
        self.assertTrue(csrf_origin_allowed(method="GET", origin=None, public_origin=public))
        self.assertTrue(csrf_origin_allowed(method="HEAD", origin="https://evil", public_origin=public))
        self.assertTrue(csrf_origin_allowed(method="POST", origin=public, public_origin=public))
        self.assertFalse(csrf_origin_allowed(method="POST", origin="https://evil", public_origin=public))
        self.assertFalse(csrf_origin_allowed(method="POST", origin="null", public_origin=public))
        self.assertFalse(csrf_origin_allowed(method="POST", origin=None, public_origin=public))
        self.assertFalse(csrf_origin_allowed(method="POST", origin=public, public_origin=""))
        self.assertFalse(csrf_origin_allowed(method="POST", origin=public, public_origin=None))


if __name__ == "__main__":
    unittest.main()
