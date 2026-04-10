from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

OUTPUT_DIR = Path("output")


def build_output_parser(description: str, prefix: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--output",
        default=default_output_path(prefix),
        help=f"JSON output path, or '-' for stdout (default: output/{prefix}.json)",
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
    return parser


def today_output_dir() -> Path:
    return OUTPUT_DIR


def default_output_path(prefix: str) -> str:
    return str(today_output_dir() / f"{prefix}.json")


def clean_output_dir() -> None:
    """Ensure output/ exists and remove legacy dated subdirectories and Latest.* files."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for child in OUTPUT_DIR.iterdir():
        if child.is_dir() and re.fullmatch(r"\d{4}\.\d{2}\.\d{2}", child.name):
            shutil.rmtree(child)
            continue
        if child.is_file() and child.name in {"Latest.md", "Latest.json"}:
            child.unlink()


clean_today_output_dir = clean_output_dir


def generated_at_utc() -> str:
    return datetime.now(tz=UTC).replace(microsecond=0).isoformat()


async def fetch_json(url: str, timeout_seconds: float, *, extra_headers: dict[str, str] | None = None) -> Any:
    return await asyncio.to_thread(fetch_json_sync, url, timeout_seconds, extra_headers=extra_headers)


def fetch_json_sync(url: str, timeout_seconds: float, *, extra_headers: dict[str, str] | None = None) -> Any:
    headers = {
        "Accept": "application/json",
        "User-Agent": "crypto-universe/0.1.0",
    }
    if extra_headers:
        headers.update(extra_headers)
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return json.load(response)


def write_json(payload: dict[str, Any], output_path: str, indent: int) -> str:
    text = json.dumps(payload, indent=indent, sort_keys=False)
    if output_path == "-":
        print(text)
        return "stdout"

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")
    return str(path)


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().upper()


def normalize_symbol(value: Any) -> str:
    return normalize_text(value)


def string_or_none(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def coerce_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = normalize_text(value)
    if text in {"TRUE", "T", "YES", "Y", "1"}:
        return True
    if text in {"FALSE", "F", "NO", "N", "0", ""}:
        return False
    return default


def normalize_permission_list(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {normalize_text(item) for item in value if normalize_text(item)}


def normalize_permission_sets(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    flattened: set[str] = set()
    for item in value:
        if isinstance(item, list):
            flattened.update(normalize_permission_list(item))
    return flattened


def pair_key(base_asset: str, quote_asset: str) -> str:
    return f"{base_asset}/{quote_asset}"


def build_volume_by_symbol(payload: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, list):
        raise TypeError(f"unexpected ticker/24hr payload type: {type(payload)!r}")

    volume_by_symbol: dict[str, dict[str, Any]] = {}
    for row in payload:
        if not isinstance(row, dict):
            continue
        symbol = normalize_symbol(row.get("symbol"))
        if not symbol:
            continue
        volume_by_symbol[symbol] = {
            "symbol": symbol,
            "last_price": string_or_none(row.get("lastPrice")),
            "base_volume": string_or_none(row.get("volume")),
            "quote_volume": string_or_none(row.get("quoteVolume")),
            "open_time_ms": int_or_none(row.get("openTime")),
            "close_time_ms": int_or_none(row.get("closeTime")),
            "trade_count": int_or_none(row.get("count")),
        }
    return volume_by_symbol


def extract_symbol_rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise TypeError(f"unexpected exchangeInfo payload type: {type(payload)!r}")
    rows = payload.get("symbols")
    if not isinstance(rows, list):
        raise TypeError("exchangeInfo response does not contain a symbols list")
    return [row for row in rows if isinstance(row, dict)]


def validate_common_args(args: argparse.Namespace) -> None:
    if args.timeout_seconds <= 0:
        raise SystemExit("timeout_seconds must be > 0")
    if args.indent < 0:
        raise SystemExit("indent must be >= 0")
