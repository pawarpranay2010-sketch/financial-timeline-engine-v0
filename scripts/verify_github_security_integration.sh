#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Read-only verification of the GitHub Advanced Security integration.
#
# Usage:  sh ./scripts/verify_github_security_integration.sh
#
# Requires the GitHub CLI (gh) with repository read access. Checks the four
# GHAS surfaces for this repository and exits non-zero if the expected
# configuration is missing. Performs no modifications.
# ---------------------------------------------------------------------------
set -euo pipefail

REPO="${GITHUB_REPOSITORY:-pawarpranay2010-sketch/financial-timeline-engine-v0}"
fail=0

setting() {
    # setting <jq-path> <expected> <label>
    got="$(gh api "repos/$REPO" --jq ".security_and_analysis.$1 // \"absent\"" 2>/dev/null || echo "api-error")"
    if [ "$got" = "$2" ]; then
        echo "PASS  $3 ($got)"
    else
        echo "FAIL  $3 (got: $got, expected: $2)"
        fail=1
    fi
}

echo "Repository: $REPO"
echo "--- Secret scanning ---"
setting "secret_scanning.status"                 "enabled" "secret scanning"
setting "secret_scanning_push_protection.status" "enabled" "secret scanning push protection"

echo "--- Dependabot ---"
setting "dependabot_security_updates.status" "enabled" "Dependabot security updates"

echo "--- Code scanning (CodeQL) ---"
if gh api "repos/$REPO/code-scanning/analyses?per_page=1" --jq '.[0].tool.name // empty' 2>/dev/null | grep -q "CodeQL"; then
    latest="$(gh api "repos/$REPO/code-scanning/analyses?per_page=1" --jq '.[0] | "\(.tool.name) @ \(.created_at) (\(.analysis_key))"' 2>/dev/null || true)"
    echo "PASS  Code scanning has a CodeQL analysis: $latest"
else
    echo "FAIL  No Code scanning analyses found (CodeQL workflow has not run yet)"
    fail=1
fi

echo "--- CodeQL workflow present ---"
if gh api "repos/$REPO/contents/.github/workflows/codeql.yml" --jq '.name' >/dev/null 2>&1; then
    echo "PASS  .github/workflows/codeql.yml exists on the default branch"
else
    echo "FAIL  .github/workflows/codeql.yml not found on the default branch"
    fail=1
fi

if [ "$fail" -eq 0 ]; then
    echo "ALL CHECKS PASSED"
else
    echo "SOME CHECKS FAILED" >&2
fi
exit "$fail"
