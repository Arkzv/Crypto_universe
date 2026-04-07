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

EXCHANGE = "coinbase"
PRODUCTS_URL = "https://api.exchange.coinbase.com/products"
MAX_CONCURRENT_TICKERS = 20


async def fetch_exchange_universe(timeout_seconds: float = 20.0) -> dict[str, Any]:
    products_raw = await fetch_json(PRODUCTS_URL, timeout_seconds)

    rows = extract_product_list(products_raw)
    volume_by_id = await fetch_all_tickers(rows, timeout_seconds)

    pairs: list[dict[str, Any]] = []
    seen_pairs: set[str] = set()
    skipped_rows = 0
    duplicate_pair_rows = 0

    for row in rows:
        normalized = normalize_coinbase_pair(row)
        if normalized is None:
            skipped_rows += 1
            continue
        if normalized["pair"] in seen_pairs:
            duplicate_pair_rows += 1
            continue
        seen_pairs.add(normalized["pair"])
        normalized["volume_24h"] = volume_by_id.get(normalized["symbol"])
        pairs.append(normalized)

    pairs.sort(key=lambda item: item["pair"])
    return {
        "schema_version": 1,
        "universe_type": "spot",
        "exchange": EXCHANGE,
        "generated_at": generated_at_utc(),
        "source": {
            "exchange_info_url": PRODUCTS_URL,
            "ticker_24hr_url": f"{PRODUCTS_URL}/{{product_id}}/stats",
        },
        "summary": {
            "exchange_info_symbol_rows": len(rows),
            "ticker_24hr_rows": len(volume_by_id),
            "tradable_spot_pair_count": len(pairs),
            "pairs_with_24h_volume_count": sum(1 for pair in pairs if pair.get("volume_24h") is not None),
            "skipped_symbol_rows": skipped_rows,
            "duplicate_pair_rows": duplicate_pair_rows,
        },
        "pairs": pairs,
    }


def normalize_coinbase_pair(row: dict[str, Any]) -> dict[str, Any] | None:
    status = normalize_text(row.get("status"))
    if status != "ONLINE":
        return None

    if row.get("trading_disabled") is True:
        return None
    if row.get("cancel_only") is True:
        return None

    product_id = str(row.get("id", "")).strip().upper()
    base_asset = normalize_symbol(row.get("base_currency"))
    quote_asset = normalize_symbol(row.get("quote_currency"))
    if not product_id or not base_asset or not quote_asset:
        return None

    return {
        "exchange": EXCHANGE,
        "pair": pair_key(base_asset, quote_asset),
        "base_asset": base_asset,
        "quote_asset": quote_asset,
        "symbol": product_id,
        "flags": {
            "status": status,
            "post_only": bool(row.get("post_only")),
            "limit_only": bool(row.get("limit_only")),
            "auction_mode": bool(row.get("auction_mode")),
        },
    }


def extract_product_list(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise TypeError(f"unexpected Coinbase products payload type: {type(payload)!r}")
    return [row for row in payload if isinstance(row, dict)]


async def fetch_all_tickers(
    products: list[dict[str, Any]],
    timeout_seconds: float,
) -> dict[str, dict[str, Any]]:
    sem = asyncio.Semaphore(MAX_CONCURRENT_TICKERS)
    volume_by_id: dict[str, dict[str, Any]] = {}

    async def fetch_one(product_id: str) -> None:
        async with sem:
            try:
                data = await fetch_json(
                    f"{PRODUCTS_URL}/{product_id}/stats",
                    timeout_seconds,
                )
            except Exception:
                return
        if not isinstance(data, dict):
            return
        upper_id = product_id.upper()
        last_price = string_or_none(data.get("last"))
        base_volume = string_or_none(data.get("volume"))
        quote_volume = None
        if last_price and base_volume:
            try:
                quote_volume = str(float(base_volume) * float(last_price))
            except (ValueError, TypeError):
                pass
        volume_by_id[upper_id] = {
            "symbol": upper_id,
            "last_price": last_price,
            "base_volume": base_volume,
            "quote_volume": quote_volume,
            "open_time_ms": None,
            "close_time_ms": None,
            "trade_count": None,
        }

    ids = [str(row.get("id", "")) for row in products if row.get("id")]
    await asyncio.gather(*(fetch_one(pid) for pid in ids))
    return volume_by_id


def build_parser():
    return build_output_parser(
        "Fetch Coinbase tradable spot universe as normalized JSON.",
        "spot_universe_coinbase",
    )


def print_summary(payload: dict[str, Any], output_target: str) -> None:
    summary = payload["summary"]
    print("Coinbase spot universe")
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
