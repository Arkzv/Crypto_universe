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

EXCHANGE = "bybit"
COIN_INFO_URL = "https://api.bybit.com/v5/asset/coin/query-info"
ENV_VAR = "CRYPTO_UNIVERSE_BYBIT_RO"
RECV_WINDOW = "5000"


def _get_api_key() -> tuple[str, str] | None:
    """Return (api_key, api_secret) from env var or None if not set.

    The env var format is ``key:secret``.
    """
    raw = os.environ.get(ENV_VAR, "").strip()
    if not raw:
        return None
    parts = raw.split(":", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        print(f"WARNING: {ENV_VAR} must be in 'key:secret' format — skipping Bybit withdrawal fees")
        return None
    return parts[0], parts[1]


def _bybit_auth_headers(api_key: str, api_secret: str) -> dict[str, str]:
    """Build Bybit v5 signed request headers (GET, no query params)."""
    timestamp = str(int(time.time() * 1000))
    sign_payload = timestamp + api_key + RECV_WINDOW
    signature = hmac.new(
        api_secret.encode("utf-8"),
        sign_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {
        "X-BAPI-API-KEY": api_key,
        "X-BAPI-SIGN": signature,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": RECV_WINDOW,
    }


async def fetch_withdrawal_fees(timeout_seconds: float = 20.0) -> dict[str, Any] | None:
    creds = _get_api_key()
    if creds is None:
        print(
            f"WARNING: {ENV_VAR} environment variable is not set — "
            "skipping Bybit withdrawal fees. "
            "See crypto_universe/BYBIT_WITHDRAWAL_FEES.md for details."
        )
        return None

    api_key, api_secret = creds
    headers = _bybit_auth_headers(api_key, api_secret)
    raw = await fetch_json(COIN_INFO_URL, timeout_seconds, extra_headers=headers)

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
            "coin_info_url": COIN_INFO_URL,
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
    if not isinstance(payload, dict):
        raise TypeError(f"unexpected Bybit coin-info payload type: {type(payload)!r}")
    ret_code = payload.get("retCode")
    if ret_code != 0:
        msg = payload.get("retMsg", "")
        raise RuntimeError(f"Bybit API error retCode={ret_code} retMsg={msg}")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise TypeError("Bybit coin-info response does not contain a result object")
    rows = result.get("rows")
    if not isinstance(rows, list):
        raise TypeError("Bybit coin-info result does not contain a rows list")
    return [row for row in rows if isinstance(row, dict)]


def normalize_coin(row: dict[str, Any]) -> dict[str, Any] | None:
    currency = str(row.get("coin", "")).strip().upper()
    if not currency:
        return None

    raw_chains = row.get("chains")
    if not isinstance(raw_chains, list) or not raw_chains:
        return None

    chains: list[dict[str, Any]] = []
    for chain in raw_chains:
        if not isinstance(chain, dict):
            continue
        chains.append({
            "chain_name": str(chain.get("chain", "")).strip(),
            "withdrawal_min_fee": string_or_none(chain.get("withdrawFee")),
            "withdraw_fee_rate": string_or_none(chain.get("withdrawPercentageFee")),
            "withdrawal_min_size": string_or_none(chain.get("withdrawMin")),
            "is_withdraw_enabled": str(chain.get("chainWithdraw")) == "1",
            "is_deposit_enabled": str(chain.get("chainDeposit")) == "1",
            "contract_address": string_or_none(chain.get("contractAddress")),
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
    print("Bybit withdrawal fees")
    print(f"  Generated at: {payload['generated_at']}")
    print(f"  Output: {output_target}")
    print(f"  Currencies with chains: {summary['currencies_with_chains']}")
    print(f"  Total chains: {summary['total_chains']}")
    print(f"  Skipped rows: {summary['skipped_rows']}")
