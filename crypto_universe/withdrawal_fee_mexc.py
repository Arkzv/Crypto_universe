from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any
from urllib.error import HTTPError

from .common import (
    fetch_json_sync,
    generated_at_utc,
    string_or_none,
)

EXCHANGE = "mexc"
COIN_CONFIG_URL = "https://api.mexc.com/api/v3/capital/config/getall"
ENV_VAR = "CRYPTO_UNIVERSE_MEXC_RO"
INVALID_ACCESS_KEY_CODE = 10072
TIMESTAMP_ERROR = 700003
logger = logging.getLogger(__name__)


def _get_api_key() -> tuple[str, str] | None:
    """Return (api_key, api_secret) from env var or None if not set.

    The env var format is ``key:secret``.
    """
    raw = os.environ.get(ENV_VAR, "").strip()
    if not raw:
        return None
    parts = raw.split(":", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        print(f"WARNING: {ENV_VAR} must be in 'key:secret' format — skipping MEXC withdrawal fees")
        return None
    return parts[0], parts[1]


def _mexc_signed_url(api_key: str, api_secret: str) -> tuple[str, dict[str, str]]:
    """Build signed URL and headers for MEXC v3 API (Binance-style signing)."""
    timestamp = str(int(time.time() * 1000))
    query = f"timestamp={timestamp}"
    signature = hmac.new(
        api_secret.encode("utf-8"),
        query.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    url = f"{COIN_CONFIG_URL}?{query}&signature={signature}"
    headers = {"X-MEXC-APIKEY": api_key}
    return url, headers


def _request_coin_config(api_key: str, api_secret: str, timeout_seconds: float) -> Any:
    url, headers = _mexc_signed_url(api_key, api_secret)
    try:
        return fetch_json_sync(url, timeout_seconds, extra_headers=headers)
    except HTTPError as exc:
        # MEXC can return its API error code inside an HTTP error response.
        with exc:
            try:
                payload = json.load(exc)
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise exc from None
        code = payload.get("code") if isinstance(payload, dict) else None
        if code == INVALID_ACCESS_KEY_CODE:
            raise RuntimeError(
                "MEXC API key is invalid or expired. Create a new key with "
                f"SPOT_WITHDRAW_READ permission and update {ENV_VAR}."
            ) from exc
        if code == TIMESTAMP_ERROR:
            return payload
        raise


def _fetch_coin_config(api_key: str, api_secret: str, timeout_seconds: float, queued_at: float) -> Any:
    worker_wait_ms = (time.monotonic() - queued_at) * 1000
    for attempt in (1, 2):
        started = time.monotonic()
        payload = _request_coin_config(api_key, api_secret, timeout_seconds)
        if not isinstance(payload, dict) or payload.get("code") != TIMESTAMP_ERROR:
            break
        logger.warning(
            "MEXC coin config code=700003 (attempt %d/2, initial worker wait %.0f ms, HTTP duration %.0f ms)",
            attempt, worker_wait_ms, (time.monotonic() - started) * 1000,
        )
    return payload


async def fetch_withdrawal_fees(timeout_seconds: float = 20.0) -> dict[str, Any] | None:
    creds = _get_api_key()
    if creds is None:
        print(
            f"WARNING: {ENV_VAR} environment variable is not set — "
            "skipping MEXC withdrawal fees. "
            "See crypto_universe/MEXC_WITHDRAWAL_FEES.md for details."
        )
        return None

    raw = await asyncio.to_thread(_fetch_coin_config, *creds, timeout_seconds, time.monotonic())

    rows = extract_coin_rows(raw)

    currencies: list[dict[str, Any]] = []
    skipped = 0

    for row in rows:
        normalized = normalize_coin(row)
        if normalized is None:
            skipped += 1
            continue
        currencies.append(normalized)

    currencies.sort(key=lambda c: c["currency"])

    return {
        "schema_version": 1,
        "data_type": "withdrawal_fees",
        "exchange": EXCHANGE,
        "generated_at": generated_at_utc(),
        "source": {
            "coin_config_url": COIN_CONFIG_URL,
        },
        "summary": {
            "total_coin_rows": len(rows),
            "currencies_with_chains": len(currencies),
            "total_chains": sum(len(c["chains"]) for c in currencies),
            "skipped_rows": skipped,
        },
        "currencies": currencies,
    }


def extract_coin_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        code = payload.get("code")
        msg = payload.get("msg", "")
        raise RuntimeError(f"MEXC API error code={code} msg={msg}")
    raise TypeError(f"unexpected MEXC coin-config payload type: {type(payload)!r}")


def normalize_coin(row: dict[str, Any]) -> dict[str, Any] | None:
    currency = str(row.get("coin", "")).strip().upper()
    if not currency:
        return None

    raw_networks = row.get("networkList")
    if not isinstance(raw_networks, list) or not raw_networks:
        return None

    chains: list[dict[str, Any]] = []
    for net in raw_networks:
        if not isinstance(net, dict):
            continue
        chains.append({
            "chain_name": str(net.get("netWork", "")).strip(),
            "network": string_or_none(net.get("network")),
            "withdrawal_min_fee": string_or_none(net.get("withdrawFee")),
            "withdraw_fee_rate": None,
            "withdrawal_min_size": string_or_none(net.get("withdrawMin")),
            "is_withdraw_enabled": bool(net.get("withdrawEnable", False)),
            "is_deposit_enabled": bool(net.get("depositEnable", False)),
            "contract_address": string_or_none(net.get("contract")),
        })

    if not chains:
        return None

    return {
        "currency": currency,
        "name": string_or_none(row.get("name")),
        "full_name": string_or_none(row.get("name")),
        "chains": chains,
    }


def print_summary(payload: dict[str, Any], output_target: str) -> None:
    summary = payload["summary"]
    print("MEXC withdrawal fees")
    print(f"  Generated at: {payload['generated_at']}")
    print(f"  Output: {output_target}")
    print(f"  Currencies with chains: {summary['currencies_with_chains']}")
    print(f"  Total chains: {summary['total_chains']}")
    print(f"  Skipped rows: {summary['skipped_rows']}")
