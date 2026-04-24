from __future__ import annotations

import asyncio
from urllib.parse import quote
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
TICKER_URL = "https://api-cloud.bitmart.com/spot/quotation/v3/ticker?symbol={symbol}"
SINGLE_TICKER_BATCH_SIZE = 15
SINGLE_TICKER_BATCH_PAUSE_SECONDS = 2.05
SINGLE_TICKER_RETRY_ATTEMPTS = 3
SINGLE_TICKER_RETRY_PAUSE_SECONDS = 1.0


async def fetch_exchange_universe(timeout_seconds: float = 20.0) -> dict[str, Any]:
    symbols_raw, tickers_raw = await asyncio.gather(
        fetch_json(SYMBOLS_URL, timeout_seconds),
        fetch_json(TICKERS_URL, timeout_seconds),
    )

    rows = extract_symbol_list(symbols_raw)
    bulk_volume_by_symbol = build_bitmart_volume_by_symbol(tickers_raw)

    pairs: list[dict[str, Any]] = []
    normalized_pairs: list[dict[str, Any]] = []
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
        normalized_pairs.append(normalized)

    missing_symbols = [
        pair["symbol"]
        for pair in normalized_pairs
        if pair["symbol"] not in bulk_volume_by_symbol
    ]
    backfilled_volume_by_symbol = await fetch_missing_bitmart_volumes(
        missing_symbols,
        timeout_seconds,
    )
    volume_by_symbol = {
        **bulk_volume_by_symbol,
        **backfilled_volume_by_symbol,
    }

    for normalized in normalized_pairs:
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
            "ticker_24hr_symbol_url_template": "https://api-cloud.bitmart.com/spot/quotation/v3/ticker?symbol={urlencoded_symbol}",
        },
        "summary": {
            "exchange_info_symbol_rows": len(rows),
            "ticker_24hr_rows": len(volume_by_symbol),
            "ticker_24hr_bulk_rows": len(bulk_volume_by_symbol),
            "ticker_24hr_backfilled_rows": len(backfilled_volume_by_symbol),
            "tradable_spot_pair_count": len(pairs),
            "pairs_with_24h_volume_count": sum(1 for pair in pairs if pair.get("volume_24h") is not None),
            "pairs_missing_from_bulk_ticker_count": len(missing_symbols),
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
        volume_by_symbol[symbol] = build_bitmart_volume_entry(
            symbol=symbol,
            last_price=row[1],
            base_volume=row[2],
            quote_volume=row[3],
        )
    return volume_by_symbol


def build_bitmart_single_ticker_volume(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        raise TypeError(f"unexpected BitMart single ticker payload type: {type(payload)!r}")
    code = payload.get("code")
    if code != 1000:
        msg = payload.get("message", "")
        raise RuntimeError(f"BitMart API error code={code} message={msg}")
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    symbol = str(data.get("symbol", "")).strip().upper()
    if not symbol:
        return None
    return build_bitmart_volume_entry(
        symbol=symbol,
        last_price=data.get("last"),
        base_volume=data.get("v_24h"),
        quote_volume=data.get("qv_24h"),
    )


def build_bitmart_volume_entry(
    *,
    symbol: str,
    last_price: Any,
    base_volume: Any,
    quote_volume: Any,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "last_price": string_or_none(last_price),
        "base_volume": string_or_none(base_volume),
        "quote_volume": string_or_none(quote_volume),
        "open_time_ms": None,
        "close_time_ms": None,
        "trade_count": None,
    }


async def fetch_missing_bitmart_volumes(
    symbols: list[str],
    timeout_seconds: float,
) -> dict[str, dict[str, Any]]:
    ordered_symbols = list(dict.fromkeys(symbol for symbol in symbols if symbol))
    if not ordered_symbols:
        return {}

    volume_by_symbol: dict[str, dict[str, Any]] = {}
    for start in range(0, len(ordered_symbols), SINGLE_TICKER_BATCH_SIZE):
        batch = ordered_symbols[start:start + SINGLE_TICKER_BATCH_SIZE]
        responses = await asyncio.gather(
            *(
                fetch_single_ticker_payload(symbol, timeout_seconds)
                for symbol in batch
            ),
            return_exceptions=True,
        )
        for symbol, response in zip(batch, responses):
            if isinstance(response, Exception):
                print(f"WARNING: BitMart single ticker backfill failed for {symbol}: {response}")
                continue
            volume = build_bitmart_single_ticker_volume(response)
            if volume is not None:
                volume_by_symbol[volume["symbol"]] = volume
        if start + SINGLE_TICKER_BATCH_SIZE < len(ordered_symbols):
            await asyncio.sleep(SINGLE_TICKER_BATCH_PAUSE_SECONDS)
    return volume_by_symbol


def build_ticker_url(symbol: str) -> str:
    return TICKER_URL.format(symbol=quote(symbol, safe=""))


async def fetch_single_ticker_payload(symbol: str, timeout_seconds: float) -> Any:
    last_error: Exception | None = None
    for attempt in range(1, SINGLE_TICKER_RETRY_ATTEMPTS + 1):
        try:
            return await fetch_json(
                build_ticker_url(symbol),
                timeout_seconds,
            )
        except Exception as exc:
            last_error = exc
            if attempt == SINGLE_TICKER_RETRY_ATTEMPTS:
                break
            await asyncio.sleep(SINGLE_TICKER_RETRY_PAUSE_SECONDS)
    if last_error is None:
        raise RuntimeError(f"BitMart single ticker fetch failed for {symbol}")
    raise last_error


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
    print(f"Pairs missing from bulk ticker: {summary['pairs_missing_from_bulk_ticker_count']}")
    print(f"Backfilled ticker rows: {summary['ticker_24hr_backfilled_rows']}")
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
