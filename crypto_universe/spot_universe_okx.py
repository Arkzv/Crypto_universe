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

EXCHANGE = "okx"
INSTRUMENTS_URL = "https://www.okx.com/api/v5/public/instruments?instType=SPOT"
TICKERS_URL = "https://www.okx.com/api/v5/market/tickers?instType=SPOT"
NON_TRADABLE_STATES = {
    "",
    "PREOPEN",
    "REBASE",
    "SUSPEND",
    "TEST",
}


async def fetch_exchange_universe(timeout_seconds: float = 20.0) -> dict[str, Any]:
    instruments_raw, tickers_raw = await asyncio.gather(
        fetch_json(INSTRUMENTS_URL, timeout_seconds),
        fetch_json(TICKERS_URL, timeout_seconds),
    )

    rows = extract_data_list(instruments_raw)
    volume_by_inst = build_okx_volume_by_inst(tickers_raw)

    pairs: list[dict[str, Any]] = []
    seen_pairs: set[str] = set()
    skipped_rows = 0
    duplicate_pair_rows = 0

    for row in rows:
        normalized = normalize_okx_pair(row)
        if normalized is None:
            skipped_rows += 1
            continue
        if normalized["pair"] in seen_pairs:
            duplicate_pair_rows += 1
            continue
        seen_pairs.add(normalized["pair"])
        normalized["volume_24h"] = volume_by_inst.get(normalized["symbol"])
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
            "ticker_24hr_rows": len(extract_data_list(tickers_raw)),
            "tradable_spot_pair_count": len(pairs),
            "pairs_with_24h_volume_count": sum(1 for pair in pairs if pair.get("volume_24h") is not None),
            "skipped_symbol_rows": skipped_rows,
            "duplicate_pair_rows": duplicate_pair_rows,
        },
        "pairs": pairs,
    }


def normalize_okx_pair(row: dict[str, Any]) -> dict[str, Any] | None:
    state = normalize_text(row.get("state"))
    if state in NON_TRADABLE_STATES or state != "LIVE":
        return None

    base_asset = normalize_symbol(row.get("baseCcy"))
    quote_asset = normalize_symbol(row.get("quoteCcy"))
    inst_id = str(row.get("instId", "")).strip().upper()
    if not inst_id or not base_asset or not quote_asset:
        return None

    rule_type = normalize_text(row.get("ruleType", ""))

    return {
        "exchange": EXCHANGE,
        "pair": pair_key(base_asset, quote_asset),
        "base_asset": base_asset,
        "quote_asset": quote_asset,
        "symbol": inst_id,
        "flags": {
            "state": state,
            "ruleType": rule_type,
        },
    }


def extract_data_list(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise TypeError(f"unexpected OKX payload type: {type(payload)!r}")
    code = payload.get("code")
    if code != "0":
        msg = payload.get("msg", "")
        raise RuntimeError(f"OKX API error code={code} msg={msg}")
    data = payload.get("data")
    if not isinstance(data, list):
        raise TypeError("OKX response does not contain a data list")
    return [row for row in data if isinstance(row, dict)]


def build_okx_volume_by_inst(payload: Any) -> dict[str, dict[str, Any]]:
    rows = extract_data_list(payload)
    volume_by_inst: dict[str, dict[str, Any]] = {}
    for row in rows:
        inst_id = str(row.get("instId", "")).strip().upper()
        if not inst_id:
            continue
        volume_by_inst[inst_id] = {
            "symbol": inst_id,
            "last_price": string_or_none(row.get("last")),
            "base_volume": string_or_none(row.get("vol24h")),
            "quote_volume": string_or_none(row.get("volCcy24h")),
            "open_time_ms": None,
            "close_time_ms": None,
            "trade_count": None,
        }
    return volume_by_inst


def build_parser():
    return build_output_parser(
        "Fetch OKX tradable spot universe as normalized JSON.",
        "spot_universe_okx",
    )


def print_summary(payload: dict[str, Any], output_target: str) -> None:
    summary = payload["summary"]
    print("OKX spot universe")
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
