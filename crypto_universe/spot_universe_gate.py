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

EXCHANGE = "gate"
CURRENCY_PAIRS_URL = "https://api.gateio.ws/api/v4/spot/currency_pairs"
TICKERS_URL = "https://api.gateio.ws/api/v4/spot/tickers"
TRADABLE_STATUSES = {"TRADABLE", "BUYABLE", "SELLABLE"}


async def fetch_exchange_universe(timeout_seconds: float = 20.0) -> dict[str, Any]:
    pairs_raw, tickers_raw = await asyncio.gather(
        fetch_json(CURRENCY_PAIRS_URL, timeout_seconds),
        fetch_json(TICKERS_URL, timeout_seconds),
    )

    rows = extract_list(pairs_raw, "currency_pairs")
    volume_by_pair = build_gate_volume_by_pair(tickers_raw)

    pairs: list[dict[str, Any]] = []
    seen_pairs: set[str] = set()
    skipped_rows = 0
    duplicate_pair_rows = 0

    for row in rows:
        normalized = normalize_gate_pair(row)
        if normalized is None:
            skipped_rows += 1
            continue
        if normalized["pair"] in seen_pairs:
            duplicate_pair_rows += 1
            continue
        seen_pairs.add(normalized["pair"])
        normalized["volume_24h"] = volume_by_pair.get(normalized["symbol"])
        pairs.append(normalized)

    pairs.sort(key=lambda item: item["pair"])
    return {
        "schema_version": 1,
        "universe_type": "spot",
        "exchange": EXCHANGE,
        "generated_at": generated_at_utc(),
        "source": {
            "exchange_info_url": CURRENCY_PAIRS_URL,
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


def normalize_gate_pair(row: dict[str, Any]) -> dict[str, Any] | None:
    trade_status = normalize_text(row.get("trade_status"))
    if trade_status not in TRADABLE_STATUSES:
        return None

    pair_id = str(row.get("id", "")).strip().upper()
    base_asset = normalize_symbol(row.get("base"))
    quote_asset = normalize_symbol(row.get("quote"))
    if not pair_id or not base_asset or not quote_asset:
        return None

    return {
        "exchange": EXCHANGE,
        "pair": pair_key(base_asset, quote_asset),
        "base_asset": base_asset,
        "quote_asset": quote_asset,
        "symbol": pair_id,
        "flags": {
            "trade_status": trade_status,
        },
    }


def extract_list(payload: Any, label: str) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise TypeError(f"unexpected Gate {label} payload type: {type(payload)!r}")
    return [row for row in payload if isinstance(row, dict)]


def build_gate_volume_by_pair(payload: Any) -> dict[str, dict[str, Any]]:
    rows = extract_list(payload, "tickers")
    volume_by_pair: dict[str, dict[str, Any]] = {}
    for row in rows:
        pair_id = str(row.get("currency_pair", "")).strip().upper()
        if not pair_id:
            continue
        volume_by_pair[pair_id] = {
            "symbol": pair_id,
            "last_price": string_or_none(row.get("last")),
            "base_volume": string_or_none(row.get("base_volume")),
            "quote_volume": string_or_none(row.get("quote_volume")),
            "open_time_ms": None,
            "close_time_ms": None,
            "trade_count": None,
        }
    return volume_by_pair


def build_parser():
    return build_output_parser(
        "Fetch Gate.io tradable spot universe as normalized JSON.",
        "spot_universe_gate",
    )


def print_summary(payload: dict[str, Any], output_target: str) -> None:
    summary = payload["summary"]
    print("Gate spot universe")
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
