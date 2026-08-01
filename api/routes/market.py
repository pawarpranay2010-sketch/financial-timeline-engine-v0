"""Stage 2 — market data endpoints (via Phase 6 DataAgent/ProviderOrchestrator)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

import api.services as svc
from api.schemas import MarketSnapshotResponse

router = APIRouter(tags=["market"])


@router.get("/market/{ticker}", response_model=MarketSnapshotResponse)
def market_snapshot(ticker: str) -> MarketSnapshotResponse:
    """
    Full company intelligence snapshot for a ticker.

    Delegates to the existing Phase 6 DataAgent → ProviderOrchestrator
    chain (PostgreSQL cache → Redis cache → live providers) with the
    existing failover and key rotation intact.
    """
    result = svc.fetch_market_snapshot(ticker)
    if result.get("error") and not result.get("success"):
        # DataAgent failures are data-level, not API-level: surface the
        # structured payload but let the frontend render the error state.
        return MarketSnapshotResponse(**result)
    return MarketSnapshotResponse(**result)


@router.get("/market/{ticker}/price", response_model=MarketSnapshotResponse)
def market_price(ticker: str) -> MarketSnapshotResponse:
    """Latest market price only (cheap, cache-first)."""
    result = svc.fetch_market_snapshot(ticker)
    data = result.get("data", {})
    price = data.get("market_price", {})
    payload = {
        "ticker": ticker.upper(),
        "success": bool(price.get("data")) if isinstance(price, dict) else False,
        "data": price,
        "latency_ms": result.get("latency_ms", 0),
        "error": price.get("error") if isinstance(price, dict) else None,
    }
    return MarketSnapshotResponse(**payload)
