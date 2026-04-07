from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any, Awaitable, Callable

import json

from .common import OUTPUT_DIR, clean_output_dir, generated_at_utc, today_output_dir, validate_common_args, write_json

EXTERNAL_OUTPUT = Path(__file__).resolve().parent.parent.parent / "output"
from .spot_universe_binance import fetch_exchange_universe as fetch_binance_universe
from .spot_universe_bitmart import fetch_exchange_universe as fetch_bitmart_universe
from .spot_universe_bybit import fetch_exchange_universe as fetch_bybit_universe
from .spot_universe_coinbase import fetch_exchange_universe as fetch_coinbase_universe
from .spot_universe_coinw import fetch_exchange_universe as fetch_coinw_universe
from .spot_universe_cryptocom import fetch_exchange_universe as fetch_cryptocom_universe
from .spot_universe_gate import fetch_exchange_universe as fetch_gate_universe
from .spot_universe_htx import fetch_exchange_universe as fetch_htx_universe
from .spot_universe_kucoin import fetch_exchange_universe as fetch_kucoin_universe
from .spot_universe_mexc import fetch_exchange_universe as fetch_mexc_universe
from .spot_universe_okx import fetch_exchange_universe as fetch_okx_universe
from .spot_universe_upbit import fetch_exchange_universe as fetch_upbit_universe

ExchangeFetcher = Callable[[float], Awaitable[dict[str, Any]]]

EXCHANGE_FETCHERS: dict[str, ExchangeFetcher] = {
    "binance": fetch_binance_universe,
    "bitmart": fetch_bitmart_universe,
    "bybit": fetch_bybit_universe,
    "coinbase": fetch_coinbase_universe,
    "coinw": fetch_coinw_universe,
    "cryptocom": fetch_cryptocom_universe,
    "gate": fetch_gate_universe,
    "htx": fetch_htx_universe,
    "kucoin": fetch_kucoin_universe,
    "mexc": fetch_mexc_universe,
    "okx": fetch_okx_universe,
    "upbit": fetch_upbit_universe,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch multiple spot universes asynchronously and combine them by normalized pair.",
    )
    parser.add_argument(
        "--exchanges",
        nargs="+",
        default=sorted(EXCHANGE_FETCHERS),
        help="Exchange ids to query asynchronously",
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

    exchanges = sorted(payloads_by_exchange)
    venue_count_distribution = Counter(item["venue_count"] for item in pairs)
    summary_by_exchange = {
        exchange: payloads_by_exchange[exchange]["summary"]["tradable_spot_pair_count"]
        for exchange in exchanges
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
                "source": payloads_by_exchange[exchange]["source"],
                "summary": payloads_by_exchange[exchange]["summary"],
            }
            for exchange in exchanges
        },
        "pairs": pairs,
    }


def _fmt_volume(value: float) -> str:
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.1f}B"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return f"{value:.0f}"


def build_volume_report(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    generated = payload["generated_at"]
    exchanges = payload["requested_exchanges"]

    lines.append(f"# Spot Universe — Volume Distribution")
    lines.append("")
    lines.append(f"Generated: {generated}")
    lines.append("")
    summary = payload["summary"]
    lines.append(f"- **Exchanges**: {', '.join(exchanges)}")
    lines.append(f"- **Total pairs**: {summary['pair_count_total']}")
    lines.append(f"- **Present on all venues**: {summary['pair_count_present_on_all_requested_venues']}")
    lines.append("")
    lines.append("| # | Pair | Venues | Volume Distribution (sorted by share) |")
    lines.append("|--:|------|-------:|----------------------------------------|")

    for idx, pair in enumerate(payload["pairs"], 1):
        pair_name = pair["pair"]
        quote = pair["quote_asset"]
        venue_count = pair["venue_count"]

        exchange_volumes: list[tuple[str, float]] = []
        for ex_name, ex_data in pair["by_exchange"].items():
            vol = ex_data.get("volume_24h")
            qv = 0.0
            if vol and vol.get("quote_volume"):
                try:
                    qv = float(vol["quote_volume"])
                except (ValueError, TypeError):
                    pass
            exchange_volumes.append((ex_name, qv))

        total = sum(v for _, v in exchange_volumes)
        exchange_volumes.sort(key=lambda x: x[1], reverse=True)

        parts: list[str] = []
        for ex_name, vol in exchange_volumes:
            pct = (vol / total * 100) if total > 0 else 0.0
            parts.append(f"{ex_name} {_fmt_volume(vol)} {quote} {pct:.0f}%")

        dist_cell = " · ".join(parts) if parts else "—"
        lines.append(f"| {idx} | {pair_name} | {venue_count} | {dist_cell} |")

    lines.append("")
    return "\n".join(lines)


def build_latest_json(payload: dict[str, Any]) -> dict[str, Any]:
    pairs_out: list[dict[str, Any]] = []
    for pair in payload["pairs"]:
        quote = pair["quote_asset"]
        exchange_volumes: list[tuple[str, float]] = []
        for ex_name, ex_data in pair["by_exchange"].items():
            vol = ex_data.get("volume_24h")
            qv = 0.0
            if vol and vol.get("quote_volume"):
                try:
                    qv = float(vol["quote_volume"])
                except (ValueError, TypeError):
                    pass
            exchange_volumes.append((ex_name, qv))

        total = sum(v for _, v in exchange_volumes)
        exchange_volumes.sort(key=lambda x: x[1], reverse=True)

        venues: list[dict[str, Any]] = []
        for ex_name, vol in exchange_volumes:
            pct = round(vol / total * 100, 2) if total > 0 else 0.0
            venues.append({
                "exchange": ex_name,
                "quote_volume": vol,
                "quote_currency": quote,
                "volume_pct": pct,
            })

        pairs_out.append({
            "pair": pair["pair"],
            "base_asset": pair["base_asset"],
            "quote_asset": pair["quote_asset"],
            "venue_count": pair["venue_count"],
            "total_quote_volume": total,
            "venues": venues,
        })

    return {
        "schema_version": 1,
        "generated_at": payload["generated_at"],
        "exchanges": sorted(payload["requested_exchanges"]),
        "summary": payload["summary"],
        "pairs": pairs_out,
    }


def write_volume_report(payload: dict[str, Any], out_dir: Path) -> str:
    report = build_volume_report(payload)
    path = out_dir / "README.md"
    path.write_text(report + "\n", encoding="utf-8")

    latest_json_data = build_latest_json(payload)
    latest_json_text = json.dumps(latest_json_data, indent=2, sort_keys=False) + "\n"

    # Copy to repo output/
    shutil.copy2(path, OUTPUT_DIR / "Latest.md")
    (OUTPUT_DIR / "Latest.json").write_text(latest_json_text, encoding="utf-8")

    # Copy to external output/
    EXTERNAL_OUTPUT.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, EXTERNAL_OUTPUT / "Latest.md")
    (EXTERNAL_OUTPUT / "Latest.json").write_text(latest_json_text, encoding="utf-8")

    return str(path)


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
    report_path = write_volume_report(combined, out_dir)
    print_summary(combined, output_target)
    print(f"Volume report: {report_path}")
    auto_commit(combined["generated_at"])
    return 0


def auto_commit(generated_at: str) -> None:
    repo_root = Path(__file__).resolve().parent.parent
    try:
        subprocess.run(["git", "add", "output/"], cwd=repo_root, check=True, capture_output=True)
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=repo_root, capture_output=True,
        )
        if result.returncode == 0:
            print("Git: nothing new to commit")
            return
        date_str = generated_at[:10]
        subprocess.run(
            ["git", "commit", "-m", f"spot universe {date_str}"],
            cwd=repo_root, check=True, capture_output=True,
        )
        print(f"Git: committed spot universe {date_str}")
    except FileNotFoundError:
        print("Git: git not found, skipping auto-commit")
    except subprocess.CalledProcessError as exc:
        print(f"Git: auto-commit failed: {exc.stderr.decode().strip()}")


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
