from __future__ import annotations

import asyncio
from typing import Any

from .common import (
    build_output_parser,
    clean_output_dir,
    fetch_json,
    generated_at_utc,
    int_or_none,
    normalize_symbol,
    normalize_text,
    pair_key,
    string_or_none,
    today_output_dir,
    validate_common_args,
    write_json,
)
from .withdrawal_fee_bybit import fetch_withdrawal_fees, print_summary as print_fee_summary

EXCHANGE = "bybit"
INSTRUMENTS_URL = "https://api.bybit.com/v5/market/instruments-info?category=spot"
TICKERS_URL = "https://api.bybit.com/v5/market/tickers?category=spot"
NON_TRADABLE_STATUSES = {
    "",
    "CLOSED",
    "DELIVERING",
    "OFFLINE",
    "PRELAUNCH",
    "SETTLING",
}


async def fetch_exchange_universe(timeout_seconds: float = 20.0) -> dict[str, Any]:
    instruments_raw, tickers_raw = await asyncio.gather(
        fetch_json(INSTRUMENTS_URL, timeout_seconds),
        fetch_json(TICKERS_URL, timeout_seconds),
    )

    rows = extract_instrument_rows(instruments_raw)
    volume_by_symbol = build_bybit_volume_by_symbol(tickers_raw)

    pairs: list[dict[str, Any]] = []
    seen_pairs: set[str] = set()
    skipped_rows = 0
    duplicate_pair_rows = 0

    for row in rows:
        normalized = normalize_bybit_pair(row)
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
            "exchange_info_url": INSTRUMENTS_URL,
            "ticker_24hr_url": TICKERS_URL,
        },
        "summary": {
            "exchange_info_symbol_rows": len(rows),
            "ticker_24hr_rows": len(extract_result_list(tickers_raw)),
            "tradable_spot_pair_count": len(pairs),
            "pairs_with_24h_volume_count": sum(1 for pair in pairs if pair.get("volume_24h") is not None),
            "skipped_symbol_rows": skipped_rows,
            "duplicate_pair_rows": duplicate_pair_rows,
        },
        "pairs": pairs,
    }


def normalize_bybit_pair(row: dict[str, Any]) -> dict[str, Any] | None:
    status = normalize_text(row.get("status"))
    if status in NON_TRADABLE_STATUSES or status != "TRADING":
        return None

    symbol = normalize_symbol(row.get("symbol"))
    base_asset = normalize_symbol(row.get("baseCoin"))
    quote_asset = normalize_symbol(row.get("quoteCoin"))
    if not symbol or not base_asset or not quote_asset:
        return None

    margin_trading = normalize_text(row.get("marginTrading", ""))

    return {
        "exchange": EXCHANGE,
        "pair": pair_key(base_asset, quote_asset),
        "base_asset": base_asset,
        "quote_asset": quote_asset,
        "symbol": symbol,
        "flags": {
            "status": status,
            "marginTrading": margin_trading,
        },
    }


def extract_result_list(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise TypeError(f"unexpected Bybit payload type: {type(payload)!r}")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise TypeError("Bybit response does not contain a result object")
    rows = result.get("list")
    if not isinstance(rows, list):
        raise TypeError("Bybit result does not contain a list")
    return [row for row in rows if isinstance(row, dict)]


def extract_instrument_rows(payload: Any) -> list[dict[str, Any]]:
    return extract_result_list(payload)


def build_bybit_volume_by_symbol(payload: Any) -> dict[str, dict[str, Any]]:
    rows = extract_result_list(payload)
    volume_by_symbol: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = normalize_symbol(row.get("symbol"))
        if not symbol:
            continue
        volume_by_symbol[symbol] = {
            "symbol": symbol,
            "last_price": string_or_none(row.get("lastPrice")),
            "base_volume": string_or_none(row.get("volume24h")),
            "quote_volume": string_or_none(row.get("turnover24h")),
            "open_time_ms": None,
            "close_time_ms": None,
            "trade_count": int_or_none(row.get("count24h")),
        }
    return volume_by_symbol


def build_parser():
    return build_output_parser(
        "Fetch Bybit tradable spot universe as normalized JSON.",
        "spot_universe_bybit",
    )


def print_summary(payload: dict[str, Any], output_target: str) -> None:
    summary = payload["summary"]
    print("Bybit spot universe")
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
    payload, fee_payload = await asyncio.gather(
        fetch_exchange_universe(args.timeout_seconds),
        fetch_withdrawal_fees(args.timeout_seconds),
    )
    output_target = write_json(payload, args.output, args.indent)
    print_summary(payload, output_target)
    if fee_payload is not None:
        fee_path = str(today_output_dir() / "crypto_withdrawal_fee_bybit.json")
        fee_output = write_json(fee_payload, fee_path, args.indent)
        print_fee_summary(fee_payload, fee_output)
    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
