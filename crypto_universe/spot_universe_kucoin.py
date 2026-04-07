from __future__ import annotations

import asyncio
from typing import Any

from .common import (
    build_output_parser,
    clean_output_dir,
    coerce_bool,
    fetch_json,
    generated_at_utc,
    normalize_symbol,
    normalize_text,
    pair_key,
    string_or_none,
    validate_common_args,
    write_json,
)

EXCHANGE = "kucoin"
SYMBOLS_URL = "https://api.kucoin.com/api/v2/symbols"
TICKERS_URL = "https://api.kucoin.com/api/v1/market/allTickers"


async def fetch_exchange_universe(timeout_seconds: float = 20.0) -> dict[str, Any]:
    symbols_raw, tickers_raw = await asyncio.gather(
        fetch_json(SYMBOLS_URL, timeout_seconds),
        fetch_json(TICKERS_URL, timeout_seconds),
    )

    rows = extract_data_list(symbols_raw)
    volume_by_symbol = build_kucoin_volume_by_symbol(tickers_raw)

    pairs: list[dict[str, Any]] = []
    seen_pairs: set[str] = set()
    skipped_rows = 0
    duplicate_pair_rows = 0

    for row in rows:
        normalized = normalize_kucoin_pair(row)
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
    ticker_rows = extract_ticker_list(tickers_raw)
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
            "ticker_24hr_rows": len(ticker_rows),
            "tradable_spot_pair_count": len(pairs),
            "pairs_with_24h_volume_count": sum(1 for pair in pairs if pair.get("volume_24h") is not None),
            "skipped_symbol_rows": skipped_rows,
            "duplicate_pair_rows": duplicate_pair_rows,
        },
        "pairs": pairs,
    }


def normalize_kucoin_pair(row: dict[str, Any]) -> dict[str, Any] | None:
    if not coerce_bool(row.get("enableTrading"), default=False):
        return None

    symbol = str(row.get("symbol", "")).strip().upper()
    base_asset = normalize_symbol(row.get("baseCurrency"))
    quote_asset = normalize_symbol(row.get("quoteCurrency"))
    if not symbol or not base_asset or not quote_asset:
        return None

    market = normalize_text(row.get("market", ""))
    st = coerce_bool(row.get("st"), default=False)

    return {
        "exchange": EXCHANGE,
        "pair": pair_key(base_asset, quote_asset),
        "base_asset": base_asset,
        "quote_asset": quote_asset,
        "symbol": symbol,
        "flags": {
            "enableTrading": True,
            "market": market,
            "st": st,
        },
    }


def extract_data_list(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise TypeError(f"unexpected KuCoin payload type: {type(payload)!r}")
    code = payload.get("code")
    if code != "200000":
        msg = payload.get("msg", "")
        raise RuntimeError(f"KuCoin API error code={code} msg={msg}")
    data = payload.get("data")
    if not isinstance(data, list):
        raise TypeError("KuCoin response does not contain a data list")
    return [row for row in data if isinstance(row, dict)]


def extract_ticker_list(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise TypeError(f"unexpected KuCoin tickers payload type: {type(payload)!r}")
    code = payload.get("code")
    if code != "200000":
        msg = payload.get("msg", "")
        raise RuntimeError(f"KuCoin API error code={code} msg={msg}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise TypeError("KuCoin tickers response does not contain a data object")
    ticker = data.get("ticker")
    if not isinstance(ticker, list):
        raise TypeError("KuCoin tickers data does not contain a ticker list")
    return [row for row in ticker if isinstance(row, dict)]


def build_kucoin_volume_by_symbol(payload: Any) -> dict[str, dict[str, Any]]:
    rows = extract_ticker_list(payload)
    volume_by_symbol: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = str(row.get("symbol", "")).strip().upper()
        if not symbol:
            continue
        volume_by_symbol[symbol] = {
            "symbol": symbol,
            "last_price": string_or_none(row.get("last")),
            "base_volume": string_or_none(row.get("vol")),
            "quote_volume": string_or_none(row.get("volValue")),
            "open_time_ms": None,
            "close_time_ms": None,
            "trade_count": None,
        }
    return volume_by_symbol


def build_parser():
    return build_output_parser(
        "Fetch KuCoin tradable spot universe as normalized JSON.",
        "spot_universe_kucoin",
    )


def print_summary(payload: dict[str, Any], output_target: str) -> None:
    summary = payload["summary"]
    print("KuCoin spot universe")
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
