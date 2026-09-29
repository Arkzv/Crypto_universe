"""Shared parsing for Bitfinex's public, array-based v2 API."""
from __future__ import annotations

from typing import Any

API_URL = "https://api-pub.bitfinex.com/v2"
PLATFORM_STATUS_URL = f"{API_URL}/platform/status"
SPOT_CONFIG_URL = f"{API_URL}/conf/pub:list:pair:exchange,pub:map:currency:sym"
FUTURES_CONFIG_URL = (
    f"{API_URL}/conf/pub:list:pair:futures,pub:map:currency:sym,pub:map:currency:undl"
)
TICKERS_URL = f"{API_URL}/tickers?symbols=ALL"


def extract_config(payload: Any, count: int) -> list[list[Any]]:
    # API errors also use arrays: ["error", CODE, MESSAGE]. Do not treat them
    # or missing configuration sections as a successful empty inventory.
    if (not isinstance(payload, list) or len(payload) != count
            or any(not isinstance(section, list) for section in payload)):
        raise ValueError("invalid Bitfinex configuration response")
    return payload


def currency_map(rows: list[Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in rows:
        if (not isinstance(row, list) or len(row) < 2
                or any(not isinstance(value, str) or not value.strip() for value in row[:2])):
            raise ValueError("invalid Bitfinex currency mapping")
        result[row[0].strip().upper()] = row[1].strip().upper()
    return result


def platform_is_operational(payload: Any) -> bool:
    if (not isinstance(payload, list) or not payload
            or type(payload[0]) is not int or payload[0] not in (0, 1)):
        raise ValueError("invalid Bitfinex platform status")
    return payload[0] == 1


def split_pair(value: Any) -> tuple[str, str]:
    if not isinstance(value, str):
        raise ValueError("invalid Bitfinex pair symbol")
    symbol = value.strip().removeprefix("t").upper()
    if ":" in symbol:
        parts = symbol.split(":")
    elif len(symbol) == 6:
        parts = [symbol[:3], symbol[3:]]
    else:
        raise ValueError(f"invalid Bitfinex pair symbol: {value!r}")
    if len(parts) != 2 or any(not part.isalnum() for part in parts):
        raise ValueError(f"invalid Bitfinex pair symbol: {value!r}")
    return parts[0], parts[1]


def native_symbol(base: str, quote: str) -> str:
    separator = "" if len(base) == len(quote) == 3 else ":"
    return f"t{base}{separator}{quote}"


def derivative_asset(asset: str, aliases: dict[str, str], underlyings: dict[str, str]) -> str:
    # The underlying map can lag new listings. F0 denotes the perpetual asset;
    # use its underlying code when no explicit mapping exists. Direct aliases
    # also matter (e.g. EUTF0 -> EURt, USTF0 -> USDt).
    underlying = underlyings.get(asset, asset.removesuffix("F0"))
    if not underlying:
        raise ValueError(f"missing Bitfinex derivative underlying: {asset!r}")
    return aliases.get(asset, aliases.get(underlying, underlying))
