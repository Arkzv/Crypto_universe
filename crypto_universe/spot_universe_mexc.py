from __future__ import annotations

import asyncio
from typing import Any

from .common import (
    build_output_parser,
    build_volume_by_symbol,
    clean_output_dir,
    coerce_bool,
    extract_symbol_rows,
    fetch_json,
    generated_at_utc,
    normalize_permission_list,
    normalize_symbol,
    normalize_text,
    pair_key,
    validate_common_args,
    write_json,
)

EXCHANGE = "mexc"
EXCHANGE_INFO_URL = "https://api.mexc.com/api/v3/exchangeInfo"
TICKER_24HR_URL = "https://api.mexc.com/api/v3/ticker/24hr"
NON_TRADABLE_STATUSES = {
    "0",
    "2",
    "3",
    "BREAK",
    "DISABLED",
    "HALT",
    "OFFLINE",
    "PAUSE",
    "PAUSED",
    "STOP",
    "SUSPENDED",
}


async def fetch_exchange_universe(timeout_seconds: float = 20.0) -> dict[str, Any]:
    exchange_info, ticker_24hr = await asyncio.gather(
        fetch_json(EXCHANGE_INFO_URL, timeout_seconds),
        fetch_json(TICKER_24HR_URL, timeout_seconds),
    )

    rows = extract_symbol_rows(exchange_info)
    volume_by_symbol = build_volume_by_symbol(ticker_24hr)

    pairs: list[dict[str, Any]] = []
    seen_pairs: set[str] = set()
    skipped_rows = 0
    duplicate_pair_rows = 0

    for row in rows:
        normalized = normalize_mexc_pair(row)
        if normalized is None:
            skipped_rows += 1
            continue
        if normalized["pair"] in seen_pairs:
            duplicate_pair_rows += 1
            continue
        seen_pairs.add(normalized["pair"])
        normalized["volume_24h"] = volume_by_symbol.get(normalized["symbol"])
        pairs.append(normalized)

    pairs.sort(key=lambda item: item["pair"])
    return {
        "schema_version": 1,
        "universe_type": "spot",
        "exchange": EXCHANGE,
        "generated_at": generated_at_utc(),
        "source": {
            "exchange_info_url": EXCHANGE_INFO_URL,
            "ticker_24hr_url": TICKER_24HR_URL,
        },
        "summary": {
            "exchange_info_symbol_rows": len(rows),
            "ticker_24hr_rows": len(ticker_24hr) if isinstance(ticker_24hr, list) else 0,
            "tradable_spot_pair_count": len(pairs),
            "pairs_with_24h_volume_count": sum(1 for pair in pairs if pair.get("volume_24h") is not None),
            "skipped_symbol_rows": skipped_rows,
            "duplicate_pair_rows": duplicate_pair_rows,
        },
        "pairs": pairs,
    }


def normalize_mexc_pair(row: dict[str, Any]) -> dict[str, Any] | None:
    if not coerce_bool(row.get("isSpotTradingAllowed"), default=False):
        return None
    if coerce_bool(row.get("st"), default=False):
        return None

    permissions = sorted(normalize_permission_list(row.get("permissions")))
    if permissions and "SPOT" not in permissions:
        return None

    status = normalize_text(row.get("status"))
    if status in NON_TRADABLE_STATUSES:
        return None

    symbol = normalize_symbol(row.get("symbol"))
    base_asset = normalize_symbol(row.get("baseAsset"))
    quote_asset = normalize_symbol(row.get("quoteAsset"))
    if not symbol or not base_asset or not quote_asset:
        return None

    return {
        "exchange": EXCHANGE,
        "pair": pair_key(base_asset, quote_asset),
        "base_asset": base_asset,
        "quote_asset": quote_asset,
        "symbol": symbol,
        "flags": {
            "status": status,
            "isSpotTradingAllowed": True,
            "st": False,
            "permissions": permissions,
        },
    }


def build_parser():
    return build_output_parser("Fetch MEXC tradable spot universe as normalized JSON.", "spot_universe_mexc")


def print_summary(payload: dict[str, Any], output_target: str) -> None:
    summary = payload["summary"]
    print("MEXC spot universe")
    print(f"Generated at: {payload['generated_at']}")
    print(f"Output: {output_target}")
    print(f"Tradable spot pairs: {summary['tradable_spot_pair_count']}")
    print(f"Pairs with 24h volume: {summary['pairs_with_24h_volume_count']}")
    print(f"Skipped symbol rows: {summary['skipped_symbol_rows']}")


async def async_main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    validate_common_args(args)
    clean_output_dir()
    payload = await fetch_exchange_universe(args.timeout_seconds)
    output_target = write_json(payload, args.output, args.indent)
    print_summary(payload, output_target)
    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
