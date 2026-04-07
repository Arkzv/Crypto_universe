from __future__ import annotations

import asyncio
from typing import Any

from .common import (
    build_output_parser,
    clean_output_dir,
    fetch_json,
    generated_at_utc,
    normalize_symbol,
    normalize_text,
    pair_key,
    string_or_none,
    validate_common_args,
    write_json,
)

EXCHANGE = "bitmart"
SYMBOLS_URL = "https://api-cloud.bitmart.com/spot/v1/symbols/details"
TICKERS_URL = "https://api-cloud.bitmart.com/spot/quotation/v3/tickers"


async def fetch_exchange_universe(timeout_seconds: float = 20.0) -> dict[str, Any]:
    symbols_raw, tickers_raw = await asyncio.gather(
        fetch_json(SYMBOLS_URL, timeout_seconds),
        fetch_json(TICKERS_URL, timeout_seconds),
    )

    rows = extract_symbol_list(symbols_raw)
    volume_by_symbol = build_bitmart_volume_by_symbol(tickers_raw)

    pairs: list[dict[str, Any]] = []
    seen_pairs: set[str] = set()
    skipped_rows = 0
    duplicate_pair_rows = 0

    for row in rows:
        normalized = normalize_bitmart_pair(row)
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
            "exchange_info_url": SYMBOLS_URL,
            "ticker_24hr_url": TICKERS_URL,
        },
        "summary": {
            "exchange_info_symbol_rows": len(rows),
            "ticker_24hr_rows": len(volume_by_symbol),
            "tradable_spot_pair_count": len(pairs),
            "pairs_with_24h_volume_count": sum(1 for pair in pairs if pair.get("volume_24h") is not None),
            "skipped_symbol_rows": skipped_rows,
            "duplicate_pair_rows": duplicate_pair_rows,
        },
        "pairs": pairs,
    }


def normalize_bitmart_pair(row: dict[str, Any]) -> dict[str, Any] | None:
    trade_status = normalize_text(row.get("trade_status"))
    if trade_status != "TRADING":
        return None

    symbol = str(row.get("symbol", "")).strip().upper()
    base_asset = normalize_symbol(row.get("base_currency"))
    quote_asset = normalize_symbol(row.get("quote_currency"))
    if not symbol or not base_asset or not quote_asset:
        return None

    return {
        "exchange": EXCHANGE,
        "pair": pair_key(base_asset, quote_asset),
        "base_asset": base_asset,
        "quote_asset": quote_asset,
        "symbol": symbol,
        "flags": {
            "trade_status": trade_status,
        },
    }


def extract_symbol_list(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise TypeError(f"unexpected BitMart payload type: {type(payload)!r}")
    code = payload.get("code")
    if code != 1000:
        msg = payload.get("message", "")
        raise RuntimeError(f"BitMart API error code={code} message={msg}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise TypeError("BitMart response does not contain a data object")
    symbols = data.get("symbols")
    if not isinstance(symbols, list):
        raise TypeError("BitMart data does not contain a symbols list")
    return [row for row in symbols if isinstance(row, dict)]


def build_bitmart_volume_by_symbol(payload: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, dict):
        raise TypeError(f"unexpected BitMart tickers payload type: {type(payload)!r}")
    code = payload.get("code")
    if code != 1000:
        msg = payload.get("message", "")
        raise RuntimeError(f"BitMart API error code={code} message={msg}")
    data = payload.get("data")
    if not isinstance(data, list):
        raise TypeError("BitMart tickers data is not a list")

    volume_by_symbol: dict[str, dict[str, Any]] = {}
    for row in data:
        if not isinstance(row, list) or len(row) < 4:
            continue
        symbol = str(row[0]).strip().upper()
        if not symbol:
            continue
        volume_by_symbol[symbol] = {
            "symbol": symbol,
            "last_price": string_or_none(row[1]),
            "base_volume": string_or_none(row[2]),
            "quote_volume": string_or_none(row[3]),
            "open_time_ms": None,
            "close_time_ms": None,
            "trade_count": None,
        }
    return volume_by_symbol


def build_parser():
    return build_output_parser(
        "Fetch BitMart tradable spot universe as normalized JSON.",
        "spot_universe_bitmart",
    )


def print_summary(payload: dict[str, Any], output_target: str) -> None:
    summary = payload["summary"]
    print("BitMart spot universe")
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
