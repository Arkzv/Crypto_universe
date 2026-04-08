from __future__ import annotations

from typing import Any

from .common import (
    fetch_json,
    generated_at_utc,
    string_or_none,
)

EXCHANGE = "kucoin"
CURRENCIES_URL = "https://api.kucoin.com/api/v3/currencies"


async def fetch_withdrawal_fees(timeout_seconds: float = 20.0) -> dict[str, Any]:
    raw = await fetch_json(CURRENCIES_URL, timeout_seconds)
    rows = extract_currency_list(raw)

    currencies: list[dict[str, Any]] = []
    skipped = 0

    for row in rows:
        normalized = normalize_currency(row)
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
            "currencies_url": CURRENCIES_URL,
        },
        "summary": {
            "total_currency_rows": len(rows),
            "currencies_with_chains": len(currencies),
            "total_chains": sum(len(c["chains"]) for c in currencies),
            "skipped_rows": skipped,
        },
        "currencies": currencies,
    }


def extract_currency_list(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise TypeError(f"unexpected KuCoin currencies payload type: {type(payload)!r}")
    code = payload.get("code")
    if code != "200000":
        msg = payload.get("msg", "")
        raise RuntimeError(f"KuCoin API error code={code} msg={msg}")
    data = payload.get("data")
    if not isinstance(data, list):
        raise TypeError("KuCoin currencies response does not contain a data list")
    return [row for row in data if isinstance(row, dict)]


def normalize_currency(row: dict[str, Any]) -> dict[str, Any] | None:
    currency = str(row.get("currency", "")).strip().upper()
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
            "chain_name": str(chain.get("chainName", "")).strip(),
            "withdrawal_min_fee": string_or_none(chain.get("withdrawalMinFee")),
            "withdraw_fee_rate": string_or_none(chain.get("withdrawFeeRate")),
            "withdrawal_min_size": string_or_none(chain.get("withdrawalMinSize")),
            "is_withdraw_enabled": bool(chain.get("isWithdrawEnabled", False)),
            "is_deposit_enabled": bool(chain.get("isDepositEnabled", False)),
            "contract_address": string_or_none(chain.get("contractAddress")),
        })

    if not chains:
        return None

    return {
        "currency": currency,
        "name": string_or_none(row.get("name")),
        "full_name": string_or_none(row.get("fullName")),
        "chains": chains,
    }


def print_summary(payload: dict[str, Any], output_target: str) -> None:
    summary = payload["summary"]
    print("KuCoin withdrawal fees")
    print(f"  Generated at: {payload['generated_at']}")
    print(f"  Output: {output_target}")
    print(f"  Currencies with chains: {summary['currencies_with_chains']}")
    print(f"  Total chains: {summary['total_chains']}")
    print(f"  Skipped rows: {summary['skipped_rows']}")
