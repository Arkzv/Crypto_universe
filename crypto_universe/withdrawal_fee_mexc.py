from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Any

from .common import (
    fetch_json,
    generated_at_utc,
    string_or_none,
)

EXCHANGE = "mexc"
COIN_CONFIG_URL = "https://api.mexc.com/api/v3/capital/config/getall"
ENV_VAR = "CRYPTO_UNIVERSE_MEXC_RO"


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


async def fetch_withdrawal_fees(timeout_seconds: float = 20.0) -> dict[str, Any] | None:
    creds = _get_api_key()
    if creds is None:
        print(
            f"WARNING: {ENV_VAR} environment variable is not set — "
            "skipping MEXC withdrawal fees. "
            "See crypto_universe/MEXC_WITHDRAWAL_FEES.md for details."
        )
        return None

    api_key, api_secret = creds
    url, headers = _mexc_signed_url(api_key, api_secret)
    raw = await fetch_json(url, timeout_seconds, extra_headers=headers)

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
