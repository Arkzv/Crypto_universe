from __future__ import annotations

import asyncio
from collections import Counter
from typing import Any

from .common import (
    build_output_parser,
    clean_output_dir,
    coerce_bool,
    fetch_json,
    generated_at_utc,
    int_or_none,
    normalize_symbol,
    pair_key,
    string_or_none,
    today_output_dir,
    validate_common_args,
    write_json,
)

EXCHANGE = "mexc"
CONTRACT_DETAIL_URL = "https://api.mexc.com/api/v1/contract/detail"
TICKER_URL = "https://api.mexc.com/api/v1/contract/ticker"
FUNDING_OUTPUT_FILENAME = "fut_funding_rates_mexc.json"

STATE_LABELS = {
    0: "enabled",
    1: "delivery",
    2: "delivered",
    3: "offline",
    4: "paused",
}
PAIR_TYPE_LABELS = {
    1: "normal",
    2: "suspended",
}


async def fetch_exchange_universe(timeout_seconds: float = 20.0) -> dict[str, Any]:
    contract_detail, ticker = await asyncio.gather(
        fetch_json(CONTRACT_DETAIL_URL, timeout_seconds),
        fetch_json(TICKER_URL, timeout_seconds),
    )

    contract_rows = extract_data_rows(contract_detail, "contract/detail")
    ticker_rows = extract_data_rows(ticker, "contract/ticker")
    ticker_by_symbol = build_ticker_by_symbol(ticker_rows)

    pairs: list[dict[str, Any]] = []
    seen_symbols: set[str] = set()
    skipped_contract_rows = 0
    duplicate_symbol_rows = 0

    for row in contract_rows:
        symbol = normalize_symbol(row.get("symbol"))
        normalized = normalize_mexc_futures_pair(row, ticker_by_symbol.get(symbol))
        if normalized is None:
            skipped_contract_rows += 1
            continue
        if normalized["symbol"] in seen_symbols:
            duplicate_symbol_rows += 1
            continue
        seen_symbols.add(normalized["symbol"])
        pairs.append(normalized)

    pairs.sort(key=lambda item: item["symbol"])
    ticker_symbols = set(ticker_by_symbol)
    state_counts = count_int_field(contract_rows, "state")
    type_counts = count_int_field(contract_rows, "type")

    return {
        "schema_version": 1,
        "universe_type": "futures",
        "exchange": EXCHANGE,
        "generated_at": generated_at_utc(),
        "source": {
            "contract_detail_url": CONTRACT_DETAIL_URL,
            "ticker_url": TICKER_URL,
        },
        "summary": {
            "contract_detail_rows": len(contract_rows),
            "ticker_rows": len(ticker_rows),
            "futures_pair_count": len(pairs),
            "tradable_futures_pair_count": sum(1 for pair in pairs if pair["flags"]["is_tradable"]),
            "api_allowed_enabled_pair_count": sum(
                1 for pair in pairs if pair["flags"]["state"] == 0 and pair["flags"]["apiAllowed"]
            ),
            "pairs_with_24h_volume_count": sum(1 for pair in pairs if pair.get("ticker_24h") is not None),
            "skipped_contract_rows": skipped_contract_rows,
            "duplicate_symbol_rows": duplicate_symbol_rows,
            "ticker_only_symbol_count": len(ticker_symbols - seen_symbols),
            "state_counts": dict(sorted(state_counts.items())),
            "type_counts": dict(sorted(type_counts.items())),
        },
        "ticker_only_symbols": sorted(ticker_symbols - seen_symbols),
        "pairs": pairs,
    }


def extract_data_rows(payload: Any, endpoint_name: str) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise TypeError(f"unexpected MEXC {endpoint_name} payload type: {type(payload)!r}")
    data = payload.get("data")
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        return [data]
    raise TypeError(f"MEXC {endpoint_name} response does not contain a data object or list")


def count_int_field(rows: list[dict[str, Any]], field_name: str) -> Counter[str]:
    counts: Counter[str] = Counter()
    for row in rows:
        value = int_or_none(row.get(field_name))
        if value is not None:
            counts[str(value)] += 1
    return counts


def normalize_mexc_futures_pair(
    row: dict[str, Any],
    ticker_row: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    symbol = normalize_symbol(row.get("symbol"))
    base_asset = normalize_symbol(row.get("baseCoin"))
    quote_asset = normalize_symbol(row.get("quoteCoin"))
    settle_asset = normalize_symbol(row.get("settleCoin"))
    if not symbol or not base_asset or not quote_asset:
        return None

    state = int_or_none(row.get("state"))
    pair_type = int_or_none(row.get("type"))
    api_allowed = coerce_bool(row.get("apiAllowed"), default=False)
    is_tradable = state == 0 and api_allowed and pair_type == 1

    return {
        "exchange": EXCHANGE,
        "pair": pair_key(base_asset, quote_asset),
        "base_asset": base_asset,
        "quote_asset": quote_asset,
        "settle_asset": settle_asset or None,
        "symbol": symbol,
        "display_name": string_or_none(row.get("displayName")),
        "display_name_en": string_or_none(row.get("displayNameEn")),
        "contract_size": row.get("contractSize"),
        "min_leverage": int_or_none(row.get("minLeverage")),
        "max_leverage": int_or_none(row.get("maxLeverage")),
        "price_scale": int_or_none(row.get("priceScale")),
        "volume_scale": int_or_none(row.get("volScale")),
        "amount_scale": int_or_none(row.get("amountScale")),
        "price_unit": row.get("priceUnit"),
        "volume_unit": row.get("volUnit"),
        "min_volume": row.get("minVol"),
        "max_volume": row.get("maxVol"),
        "maker_fee_rate": row.get("makerFeeRate"),
        "taker_fee_rate": row.get("takerFeeRate"),
        "index_origin": row.get("indexOrigin") if isinstance(row.get("indexOrigin"), list) else [],
        "flags": {
            "state": state,
            "state_label": STATE_LABELS.get(state, "unknown"),
            "apiAllowed": api_allowed,
            "type": pair_type,
            "type_label": PAIR_TYPE_LABELS.get(pair_type, "unknown"),
            "is_tradable": is_tradable,
            "futureType": int_or_none(row.get("futureType")),
            "positionOpenType": int_or_none(row.get("positionOpenType")),
            "isNew": coerce_bool(row.get("isNew"), default=False),
            "isHot": coerce_bool(row.get("isHot"), default=False),
            "isHidden": coerce_bool(row.get("isHidden"), default=False),
            "preMarket": coerce_bool(row.get("preMarket"), default=False),
        },
        "ticker_24h": normalize_mexc_futures_ticker(ticker_row) if ticker_row else None,
        "contract_detail": row,
    }


def build_ticker_by_symbol(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    ticker_by_symbol: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = normalize_symbol(row.get("symbol"))
        if not symbol:
            continue
        ticker_by_symbol[symbol] = row
    return ticker_by_symbol


def normalize_mexc_futures_ticker(row: dict[str, Any]) -> dict[str, Any]:
    symbol = normalize_symbol(row.get("symbol"))
    return {
        "symbol": symbol,
        "last_price": string_or_none(row.get("lastPrice")),
        "bid_price": string_or_none(row.get("bid1")),
        "ask_price": string_or_none(row.get("ask1")),
        "contract_volume": string_or_none(row.get("volume24")),
        "quote_volume": string_or_none(row.get("amount24")),
        "open_interest_contracts": string_or_none(row.get("holdVol")),
        "low_24h": string_or_none(row.get("lower24Price")),
        "high_24h": string_or_none(row.get("high24Price")),
        "rise_fall_rate": string_or_none(row.get("riseFallRate")),
        "rise_fall_value": string_or_none(row.get("riseFallValue")),
        "index_price": string_or_none(row.get("indexPrice")),
        "fair_price": string_or_none(row.get("fairPrice")),
        "funding_rate": string_or_none(row.get("fundingRate")),
        "timestamp_ms": int_or_none(row.get("timestamp")),
        "raw": row,
    }


def build_funding_rates_payload(futures_payload: dict[str, Any]) -> dict[str, Any]:
    tradable_pairs: list[dict[str, Any]] = []
    positive_count = 0
    negative_count = 0
    zero_count = 0
    missing_count = 0

    for pair in futures_payload.get("pairs", []):
        if not pair.get("flags", {}).get("is_tradable"):
            continue

        ticker = pair.get("ticker_24h")
        funding_rate = None if not isinstance(ticker, dict) else ticker.get("funding_rate")
        funding_timestamp_ms = None if not isinstance(ticker, dict) else ticker.get("timestamp_ms")
        if funding_rate is None:
            missing_count += 1
        else:
            try:
                funding_rate_value = float(funding_rate)
            except (TypeError, ValueError):
                missing_count += 1
            else:
                if funding_rate_value > 0:
                    positive_count += 1
                elif funding_rate_value < 0:
                    negative_count += 1
                else:
                    zero_count += 1

        tradable_pairs.append(
            {
                "symbol": pair["symbol"],
                "pair": pair["pair"],
                "base_asset": pair["base_asset"],
                "quote_asset": pair["quote_asset"],
                "settle_asset": pair.get("settle_asset"),
                "funding_rate": funding_rate,
                "funding_timestamp_ms": funding_timestamp_ms,
            }
        )

    tradable_pairs.sort(key=lambda item: item["symbol"])
    summary = futures_payload.get("summary", {})
    return {
        "schema_version": 1,
        "data_type": "futures_funding_rates",
        "exchange": EXCHANGE,
        "generated_at": futures_payload.get("generated_at", generated_at_utc()),
        "source": {
            "contract_detail_url": CONTRACT_DETAIL_URL,
            "ticker_url": TICKER_URL,
            "funding_rate_source": "ticker.fundingRate",
            "tradable_filter": "state == 0 and apiAllowed == true and type == 1",
        },
        "summary": {
            "contract_detail_rows": summary.get("contract_detail_rows"),
            "ticker_rows": summary.get("ticker_rows"),
            "futures_pair_count": summary.get("futures_pair_count"),
            "tradable_futures_pair_count": len(tradable_pairs),
            "pairs_with_funding_rate_count": len(tradable_pairs) - missing_count,
            "pairs_missing_funding_rate_count": missing_count,
            "positive_funding_rate_count": positive_count,
            "negative_funding_rate_count": negative_count,
            "zero_funding_rate_count": zero_count,
        },
        "pairs": tradable_pairs,
    }


def build_parser():
    parser = build_output_parser(
        "Fetch MEXC futures universe as normalized JSON.",
        "fut_universe_mexc",
    )
    parser.add_argument(
        "--funding-output",
        default=str(today_output_dir() / FUNDING_OUTPUT_FILENAME),
        help=(
            "Funding-rate JSON output path, or '-' for stdout "
            f"(default: output/YYYY.MM.DD/{FUNDING_OUTPUT_FILENAME})"
        ),
    )
    return parser


def print_summary(payload: dict[str, Any], output_target: str) -> None:
    summary = payload["summary"]
    print("MEXC futures universe")
    print(f"Generated at: {payload['generated_at']}")
    print(f"Output: {output_target}")
    print(f"Futures pairs: {summary['futures_pair_count']}")
    print(f"Tradable futures pairs: {summary['tradable_futures_pair_count']}")
    print(f"Pairs with 24h ticker: {summary['pairs_with_24h_volume_count']}")
    print(f"Ticker-only symbols: {summary['ticker_only_symbol_count']}")


def print_funding_rates_summary(payload: dict[str, Any], output_target: str) -> None:
    summary = payload["summary"]
    print("MEXC futures funding rates")
    print(f"Generated at: {payload['generated_at']}")
    print(f"Output: {output_target}")
    print(f"Tradable futures pairs: {summary['tradable_futures_pair_count']}")
    print(f"Pairs with funding rate: {summary['pairs_with_funding_rate_count']}")
    print(f"Positive funding rates: {summary['positive_funding_rate_count']}")
    print(f"Negative funding rates: {summary['negative_funding_rate_count']}")
    print(f"Zero funding rates: {summary['zero_funding_rate_count']}")


async def async_main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    validate_common_args(args)
    clean_output_dir()
    payload = await fetch_exchange_universe(args.timeout_seconds)
    funding_payload = build_funding_rates_payload(payload)
    output_target = write_json(payload, args.output, args.indent)
    funding_output_target = write_json(funding_payload, args.funding_output, args.indent)
    print_summary(payload, output_target)
    print_funding_rates_summary(funding_payload, funding_output_target)
    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
