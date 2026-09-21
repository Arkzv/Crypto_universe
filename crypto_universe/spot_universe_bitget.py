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
    validate_common_args,
    write_json,
)

EXCHANGE = "bitget"
SYMBOLS_URL = "https://api.bitget.com/api/v3/market/instruments?category=SPOT"
TICKERS_URL = "https://api.bitget.com/api/v3/market/tickers?category=SPOT"


async def fetch_exchange_universe(timeout_seconds: float = 20.0) -> dict[str, Any]:
    symbols_raw, tickers_raw = await asyncio.gather(
        fetch_json(SYMBOLS_URL, timeout_seconds),
        fetch_json(TICKERS_URL, timeout_seconds),
    )

    rows = extract_data_list(symbols_raw)
    reality_symbols = {
        normalize_symbol(row.get("symbol"))
        for row in rows
        if normalize_text(row.get("isReality")) == "YES"
    }
    volume_by_symbol = build_bitget_volume_by_symbol(tickers_raw, reality_symbols)

    pairs: list[dict[str, Any]] = []
    seen_pairs: set[str] = set()
    skipped_rows = 0
    duplicate_pair_rows = 0

    for row in rows:
        normalized = normalize_bitget_pair(row)
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
            "ticker_24hr_rows": len(extract_data_list(tickers_raw)),
            "tradable_spot_pair_count": len(pairs),
            "pairs_with_24h_volume_count": sum(1 for pair in pairs if pair.get("volume_24h") is not None),
            "skipped_symbol_rows": skipped_rows,
            "duplicate_pair_rows": duplicate_pair_rows,
        },
        "pairs": pairs,
    }


def normalize_bitget_pair(row: dict[str, Any]) -> dict[str, Any] | None:
    status = normalize_text(row.get("status"))
    if status != "ONLINE":
        return None

    symbol = normalize_symbol(row.get("symbol"))
    base_asset = normalize_symbol(row.get("baseCoin"))
    quote_asset = normalize_symbol(row.get("quoteCoin"))
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
            "isReality": normalize_text(row.get("isReality")),
            "symbolType": normalize_text(row.get("symbolType")),
        },
    }


def extract_data_list(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise TypeError(f"unexpected Bitget payload type: {type(payload)!r}")
    code = payload.get("code")
    if code != "00000":
        msg = payload.get("msg", "")
        raise RuntimeError(f"Bitget API error code={code} msg={msg}")
    data = payload.get("data")
    if not isinstance(data, list):
        raise TypeError("Bitget response does not contain a data list")
    return [row for row in data if isinstance(row, dict)]


def build_bitget_volume_by_symbol(
    payload: Any,
    reality_symbols: set[str],
) -> dict[str, dict[str, Any]]:
    rows = extract_data_list(payload)
    volume_by_symbol: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = normalize_symbol(row.get("symbol"))
        if not symbol:
            continue
        # Reality turnover24h/volume24h describe the stock market, not Bitget
        # fills. V3 provides the exchange's quote turnover separately:
        # https://www.bitget.com/docs/catalog/market/market-data
        is_reality = symbol in reality_symbols
        quote_volume_source = "platformTurnover24h" if is_reality else "turnover24h"
        quote_volume = string_or_none(row.get(quote_volume_source))
        if is_reality and quote_volume is None:
            raise RuntimeError(f"Bitget Reality ticker {symbol} is missing platformTurnover24h")
        volume_by_symbol[symbol] = {
            "symbol": symbol,
            "last_price": string_or_none(row.get("lastPrice")),
            # No platform base volume is supplied for Reality tokens.
            "base_volume": None if is_reality else string_or_none(row.get("volume24h")),
            "quote_volume": quote_volume,
            "quote_volume_source": quote_volume_source,
            "open_time_ms": None,
            "close_time_ms": int_or_none(row.get("ts")),
            "trade_count": None,
        }
    return volume_by_symbol


def build_parser():
    return build_output_parser(
        "Fetch Bitget tradable spot universe as normalized JSON.",
        "spot_universe_bitget",
    )


def print_summary(payload: dict[str, Any], output_target: str) -> None:
    summary = payload["summary"]
    print("Bitget spot universe")
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
