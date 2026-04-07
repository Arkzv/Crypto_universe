from __future__ import annotations

import asyncio
from typing import Any

from .common import (
    build_output_parser,
    clean_output_dir,
    fetch_json,
    generated_at_utc,
    pair_key,
    string_or_none,
    validate_common_args,
    write_json,
)

EXCHANGE = "upbit"
MARKETS_URL = "https://api.upbit.com/v1/market/all?is_details=true"
TICKERS_URL = "https://api.upbit.com/v1/ticker/all?quote_currencies=KRW,BTC,USDT"


async def fetch_exchange_universe(timeout_seconds: float = 20.0) -> dict[str, Any]:
    markets_raw, tickers_raw = await asyncio.gather(
        fetch_json(MARKETS_URL, timeout_seconds),
        fetch_json(TICKERS_URL, timeout_seconds),
    )

    rows = extract_list(markets_raw, "markets")
    volume_by_market = build_upbit_volume_by_market(tickers_raw)

    pairs: list[dict[str, Any]] = []
    seen_pairs: set[str] = set()
    skipped_rows = 0
    duplicate_pair_rows = 0

    for row in rows:
        normalized = normalize_upbit_pair(row)
        if normalized is None:
            skipped_rows += 1
            continue
        if normalized["pair"] in seen_pairs:
            duplicate_pair_rows += 1
            continue
        seen_pairs.add(normalized["pair"])
        normalized["volume_24h"] = volume_by_market.get(normalized["symbol"])
        pairs.append(normalized)

    pairs.sort(key=lambda item: item["pair"])
    return {
        "schema_version": 1,
        "universe_type": "spot",
        "exchange": EXCHANGE,
        "generated_at": generated_at_utc(),
        "source": {
            "exchange_info_url": MARKETS_URL,
            "ticker_24hr_url": TICKERS_URL,
        },
        "summary": {
            "exchange_info_symbol_rows": len(rows),
            "ticker_24hr_rows": len(extract_list(tickers_raw, "tickers")),
            "tradable_spot_pair_count": len(pairs),
            "pairs_with_24h_volume_count": sum(1 for pair in pairs if pair.get("volume_24h") is not None),
            "skipped_symbol_rows": skipped_rows,
            "duplicate_pair_rows": duplicate_pair_rows,
        },
        "pairs": pairs,
    }


def normalize_upbit_pair(row: dict[str, Any]) -> dict[str, Any] | None:
    market = str(row.get("market", "")).strip().upper()
    if not market or "-" not in market:
        return None

    # Upbit format is QUOTE-BASE (e.g. KRW-BTC means BTC/KRW)
    parts = market.split("-", 1)
    if len(parts) != 2:
        return None
    quote_asset = parts[0].strip()
    base_asset = parts[1].strip()
    if not base_asset or not quote_asset:
        return None

    warning = False
    event = row.get("market_event")
    if isinstance(event, dict):
        warning = bool(event.get("warning"))

    return {
        "exchange": EXCHANGE,
        "pair": pair_key(base_asset, quote_asset),
        "base_asset": base_asset,
        "quote_asset": quote_asset,
        "symbol": market,
        "flags": {
            "warning": warning,
        },
    }


def extract_list(payload: Any, label: str) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise TypeError(f"unexpected Upbit {label} payload type: {type(payload)!r}")
    return [row for row in payload if isinstance(row, dict)]


def build_upbit_volume_by_market(payload: Any) -> dict[str, dict[str, Any]]:
    rows = extract_list(payload, "tickers")
    volume_by_market: dict[str, dict[str, Any]] = {}
    for row in rows:
        market = str(row.get("market", "")).strip().upper()
        if not market:
            continue
        volume_by_market[market] = {
            "symbol": market,
            "last_price": string_or_none(row.get("trade_price")),
            "base_volume": string_or_none(row.get("acc_trade_volume_24h")),
            "quote_volume": string_or_none(row.get("acc_trade_price_24h")),
            "open_time_ms": None,
            "close_time_ms": None,
            "trade_count": None,
        }
    return volume_by_market


def build_parser():
    return build_output_parser(
        "Fetch Upbit tradable spot universe as normalized JSON.",
        "spot_universe_upbit",
    )


def print_summary(payload: dict[str, Any], output_target: str) -> None:
    summary = payload["summary"]
    print("Upbit spot universe")
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
