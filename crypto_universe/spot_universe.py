from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from typing import Any, Awaitable, Callable

from .common import clean_output_dir, generated_at_utc, today_output_dir, validate_common_args, write_json
from .spot_universe_binance import fetch_exchange_universe as fetch_binance_universe
from .spot_universe_bybit import fetch_exchange_universe as fetch_bybit_universe
from .spot_universe_mexc import fetch_exchange_universe as fetch_mexc_universe

ExchangeFetcher = Callable[[float], Awaitable[dict[str, Any]]]

EXCHANGE_FETCHERS: dict[str, ExchangeFetcher] = {
    "binance": fetch_binance_universe,
    "bybit": fetch_bybit_universe,
    "mexc": fetch_mexc_universe,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch multiple spot universes asynchronously and combine them by normalized pair.",
    )
    parser.add_argument(
        "--exchanges",
        nargs="+",
        default=["mexc", "binance", "bybit"],
        help="Exchange ids to query asynchronously (default: mexc binance bybit)",
    )
    default_out = str(today_output_dir() / "spot_universe_combined.json")
    parser.add_argument(
        "--output",
        default=default_out,
        help=f"JSON output path, or '-' for stdout (default: {default_out})",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=20.0,
        help="HTTP timeout for each REST request",
    )
    parser.add_argument(
        "--indent",
        type=int,
        default=2,
        help="JSON indentation",
    )
    return parser.parse_args()


async def fetch_requested_exchanges(exchange_names: list[str], timeout_seconds: float) -> list[dict[str, Any]]:
    normalized_names = [name.strip().lower() for name in exchange_names if name and name.strip()]
    if not normalized_names:
        raise SystemExit("at least one exchange must be requested")

    unknown = sorted({name for name in normalized_names if name not in EXCHANGE_FETCHERS})
    if unknown:
        raise SystemExit(f"unknown exchanges: {', '.join(unknown)}")

    ordered_unique_names = list(dict.fromkeys(normalized_names))
    return await asyncio.gather(
        *(EXCHANGE_FETCHERS[name](timeout_seconds) for name in ordered_unique_names),
    )


def build_combined_payload(exchange_payloads: list[dict[str, Any]]) -> dict[str, Any]:
    payloads_by_exchange = {payload["exchange"]: payload for payload in exchange_payloads}
    pair_map: dict[str, dict[str, Any]] = {}

    for exchange_name, payload in payloads_by_exchange.items():
        for pair in payload["pairs"]:
            pair_key = pair["pair"]
            combined = pair_map.setdefault(
                pair_key,
                {
                    "pair": pair_key,
                    "base_asset": pair["base_asset"],
                    "quote_asset": pair["quote_asset"],
                    "venues": [],
                    "venue_count": 0,
                    "by_exchange": {},
                },
            )
            combined["venues"].append(exchange_name)
            combined["by_exchange"][exchange_name] = {
                "symbol": pair["symbol"],
                "flags": pair["flags"],
                "volume_24h": pair.get("volume_24h"),
            }

    pairs = sorted(pair_map.values(), key=lambda item: item["pair"])
    for item in pairs:
        item["venues"].sort()
        item["venue_count"] = len(item["venues"])

    exchanges = list(payloads_by_exchange)
    venue_count_distribution = Counter(item["venue_count"] for item in pairs)
    summary_by_exchange = {
        exchange: payload["summary"]["tradable_spot_pair_count"]
        for exchange, payload in payloads_by_exchange.items()
    }
    present_on_all_requested = sum(1 for item in pairs if item["venue_count"] == len(exchanges))

    return {
        "schema_version": 1,
        "universe_type": "spot",
        "generated_at": generated_at_utc(),
        "requested_exchanges": exchanges,
        "summary": {
            "pair_count_total": len(pairs),
            "pair_count_by_exchange": summary_by_exchange,
            "pair_count_present_on_all_requested_venues": present_on_all_requested,
            "pair_count_by_venue_count": {
                str(count): venue_count_distribution[count]
                for count in sorted(venue_count_distribution)
            },
        },
        "sources": {
            exchange: {
                "source": payload["source"],
                "summary": payload["summary"],
            }
            for exchange, payload in payloads_by_exchange.items()
        },
        "pairs": pairs,
    }


def print_summary(payload: dict[str, Any], output_target: str) -> None:
    print("Spot universe combined")
    print(f"Generated at: {payload['generated_at']}")
    print(f"Output: {output_target}")
    for exchange, count in payload["summary"]["pair_count_by_exchange"].items():
        print(f"{exchange}: {count}")
    print(f"Total normalized pairs: {payload['summary']['pair_count_total']}")
    print(
        "Present on all requested venues: "
        f"{payload['summary']['pair_count_present_on_all_requested_venues']}"
    )
    distribution = payload["summary"]["pair_count_by_venue_count"]
    for venue_count in sorted(distribution, key=int):
        print(f"Pairs on {venue_count} venue(s): {distribution[venue_count]}")


async def async_main() -> int:
    args = parse_args()
    validate_common_args(args)
    clean_output_dir()
    exchange_payloads = await fetch_requested_exchanges(args.exchanges, args.timeout_seconds)

    out_dir = today_output_dir()
    for payload in exchange_payloads:
        exchange_path = str(out_dir / f"spot_universe_{payload['exchange']}.json")
        write_json(payload, exchange_path, args.indent)

    combined = build_combined_payload(exchange_payloads)
    output_target = write_json(combined, args.output, args.indent)
    print_summary(combined, output_target)
    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
