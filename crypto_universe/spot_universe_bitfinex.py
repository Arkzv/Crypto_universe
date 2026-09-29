from __future__ import annotations

import asyncio
from decimal import Decimal, InvalidOperation
from typing import Any

from .bitfinex import (
    PLATFORM_STATUS_URL,
    SPOT_CONFIG_URL,
    TICKERS_URL,
    currency_map,
    extract_config,
    native_symbol,
    platform_is_operational,
    split_pair,
)
from .common import (
    build_output_parser,
    clean_output_dir,
    fetch_json,
    generated_at_utc,
    pair_key,
    validate_common_args,
    write_json,
)

EXCHANGE = "bitfinex"


async def fetch_exchange_universe(timeout_seconds: float = 20.0) -> dict[str, Any]:
    config_raw, tickers_raw, status_raw = await asyncio.gather(
        fetch_json(SPOT_CONFIG_URL, timeout_seconds),
        fetch_json(TICKERS_URL, timeout_seconds),
        fetch_json(PLATFORM_STATUS_URL, timeout_seconds),
    )
    rows, aliases_raw = extract_config(config_raw, 2)
    aliases = currency_map(aliases_raw)
    operative = platform_is_operational(status_raw)
    volumes = build_bitfinex_volume_by_symbol(tickers_raw)
    pairs: list[dict[str, Any]] = []
    seen_pairs: set[str] = set()
    skipped = duplicates = 0
    for row in rows:
        normalized = normalize_bitfinex_pair(row, aliases, operative=operative)
        if normalized is None:
            skipped += 1
            continue
        if normalized["pair"] in seen_pairs:
            duplicates += 1
            continue
        seen_pairs.add(normalized["pair"])
        normalized["volume_24h"] = volumes.get(normalized["symbol"])
        pairs.append(normalized)

    return {
        "schema_version": 1,
        "universe_type": "spot",
        "exchange": EXCHANGE,
        "generated_at": generated_at_utc(),
        "source": {
            "exchange_info_url": SPOT_CONFIG_URL,
            "ticker_24hr_url": TICKERS_URL,
            "platform_status_url": PLATFORM_STATUS_URL,
            "platform_status": status_raw[0],
        },
        "summary": {
            "exchange_info_symbol_rows": len(rows),
            "ticker_24hr_rows": len(tickers_raw),
            "tradable_spot_pair_count": len(pairs),
            "pairs_with_24h_volume_count": sum(p["volume_24h"] is not None for p in pairs),
            "skipped_symbol_rows": skipped,
            "duplicate_pair_rows": duplicates,
        },
        "pairs": sorted(pairs, key=lambda p: p["pair"]),
    }


def normalize_bitfinex_pair(symbol: Any, aliases: dict[str, str], *,
                            operative: bool = True) -> dict[str, Any] | None:
    base, quote = split_pair(symbol)
    # Filter before applying aliases: TESTBTC is mapped to BTC by Bitfinex.
    if not operative or any(asset.startswith("TEST") or asset.endswith("F0") for asset in (base, quote)):
        return None
    base_asset, quote_asset = (aliases.get(asset, asset) for asset in (base, quote))
    return {
        "exchange": EXCHANGE,
        "pair": pair_key(base_asset, quote_asset),
        "base_asset": base_asset,
        "quote_asset": quote_asset,
        "symbol": native_symbol(base, quote),
        "flags": {"status": "TRADING", "is_active": True, "is_tradable": True},
    }


def _nonnegative_decimal(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() and number >= 0 else None


def build_bitfinex_volume_by_symbol(payload: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, list):
        raise ValueError("invalid Bitfinex tickers response")
    volumes: dict[str, dict[str, Any]] = {}
    for row in payload:
        if not isinstance(row, list) or not row or not isinstance(row[0], str):
            raise ValueError("invalid Bitfinex ticker row")
        symbol = row[0]
        if symbol.startswith("f"):
            continue  # Margin-funding tickers have a different array layout.
        if not symbol.startswith("t") or len(row) < 11:
            raise ValueError("invalid Bitfinex trading ticker")
        last_price, base_volume = (_nonnegative_decimal(row[i]) for i in (7, 8))
        # Bitfinex reports base volume, not quote turnover. Preserve zero and
        # missing values; multiplying by the last price is only an estimate.
        quote_volume = None
        if base_volume == 0:
            quote_volume = Decimal(0)
        elif base_volume is not None and last_price is not None and last_price > 0:
            quote_volume = base_volume * last_price
        volumes[symbol] = {
            "symbol": symbol,
            "last_price": str(last_price) if last_price is not None else None,
            "base_volume": str(base_volume) if base_volume is not None else None,
            "quote_volume": str(quote_volume) if quote_volume is not None else None,
            "quote_volume_method": "base_volume_times_last_price",
            "open_time_ms": None,
            "close_time_ms": None,
            "trade_count": None,
        }
    return volumes


def build_parser():
    return build_output_parser(
        "Fetch Bitfinex tradable spot universe as normalized JSON.",
        "spot_universe_bitfinex",
    )


def print_summary(payload: dict[str, Any], output_target: str) -> None:
    summary = payload["summary"]
    print("Bitfinex spot universe")
    print(f"Generated at: {payload['generated_at']}")
    print(f"Output: {output_target}")
    print(f"Tradable spot pairs: {summary['tradable_spot_pair_count']}")
    print(f"Pairs with 24h volume: {summary['pairs_with_24h_volume_count']}")
    print(f"Skipped symbol rows: {summary['skipped_symbol_rows']}")


async def async_main() -> int:
    args = build_parser().parse_args()
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
