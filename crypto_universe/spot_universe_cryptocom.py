from __future__ import annotations

import asyncio
from typing import Any

from .common import (
    build_output_parser,
    clean_output_dir,
    fetch_json,
    generated_at_utc,
    normalize_symbol,
    pair_key,
    string_or_none,
    validate_common_args,
    write_json,
)

EXCHANGE = "cryptocom"
INSTRUMENTS_URL = "https://api.crypto.com/exchange/v1/public/get-instruments"
TICKERS_URL = "https://api.crypto.com/exchange/v1/public/get-tickers"


async def fetch_exchange_universe(timeout_seconds: float = 20.0) -> dict[str, Any]:
    instruments_raw, tickers_raw = await asyncio.gather(
        fetch_json(INSTRUMENTS_URL, timeout_seconds),
        fetch_json(TICKERS_URL, timeout_seconds),
    )

    rows = [r for r in extract_data_list(instruments_raw) if r.get("inst_type") == "CCY_PAIR"]
    spot_symbols = {str(r.get("symbol", "")).strip().upper() for r in rows}
    volume_by_symbol = build_cryptocom_volume_by_symbol(tickers_raw, spot_symbols)

    pairs: list[dict[str, Any]] = []
    seen_pairs: set[str] = set()
    skipped_rows = 0
    duplicate_pair_rows = 0

    for row in rows:
        normalized = normalize_cryptocom_pair(row)
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
            "ticker_24hr_rows": len(volume_by_symbol),
            "tradable_spot_pair_count": len(pairs),
            "pairs_with_24h_volume_count": sum(1 for pair in pairs if pair.get("volume_24h") is not None),
            "skipped_symbol_rows": skipped_rows,
            "duplicate_pair_rows": duplicate_pair_rows,
        },
        "pairs": pairs,
    }


def normalize_cryptocom_pair(row: dict[str, Any]) -> dict[str, Any] | None:
    if not row.get("tradable"):
        return None

    symbol = str(row.get("symbol", "")).strip().upper()
    base_asset = normalize_symbol(row.get("base_ccy"))
    quote_asset = normalize_symbol(row.get("quote_ccy"))
    if not symbol or not base_asset or not quote_asset:
        return None

    return {
        "exchange": EXCHANGE,
        "pair": pair_key(base_asset, quote_asset),
        "base_asset": base_asset,
        "quote_asset": quote_asset,
        "symbol": symbol,
        "flags": {
            "inst_type": "CCY_PAIR",
            "tradable": True,
        },
    }


def extract_data_list(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise TypeError(f"unexpected Crypto.com payload type: {type(payload)!r}")
    code = payload.get("code")
    if code != 0:
        raise RuntimeError(f"Crypto.com API error code={code}")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise TypeError("Crypto.com response does not contain a result object")
    data = result.get("data")
    if not isinstance(data, list):
        raise TypeError("Crypto.com result does not contain a data list")
    return [row for row in data if isinstance(row, dict)]


def build_cryptocom_volume_by_symbol(payload: Any, spot_symbols: set[str]) -> dict[str, dict[str, Any]]:
    rows = extract_data_list(payload)
    volume_by_symbol: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = str(row.get("i", "")).strip().upper()
        if not symbol or symbol not in spot_symbols:
            continue
        volume_by_symbol[symbol] = {
            "symbol": symbol,
            "last_price": string_or_none(row.get("a")),
            "base_volume": string_or_none(row.get("v")),
            "quote_volume": string_or_none(row.get("vv")),
            "open_time_ms": None,
            "close_time_ms": None,
            "trade_count": None,
        }
    return volume_by_symbol


def build_parser():
    return build_output_parser(
        "Fetch Crypto.com tradable spot universe as normalized JSON.",
        "spot_universe_cryptocom",
    )


def print_summary(payload: dict[str, Any], output_target: str) -> None:
    summary = payload["summary"]
    print("Crypto.com spot universe")
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
