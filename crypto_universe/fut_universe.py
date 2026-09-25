"""Public futures instrument inventories for the exchanges in the spot universe.

Keep native contract symbols, but match spot using normalized base/quote assets.
Failed endpoints are recorded explicitly: an unavailable inventory is not an
empty inventory. No ticker/volume threshold is used to decide availability.
"""
from __future__ import annotations

import argparse
import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from .common import (
    coerce_bool,
    fetch_json,
    generated_at_utc,
    int_or_none,
    normalize_symbol,
    normalize_text,
    pair_key,
    today_output_dir,
    validate_common_args,
    write_json,
)
from . import fut_universe_mexc


@dataclass(frozen=True)
class Endpoint:
    market: str
    url: str
    rows_path: tuple[str, ...] = ()
    code_field: str | None = None
    success_code: str = "0"
    pagination: str | None = None


ENDPOINTS: dict[str, tuple[Endpoint, ...]] = {
    "binance": (
        Endpoint("linear", "https://fapi.binance.com/fapi/v1/exchangeInfo", ("symbols",)),
        Endpoint("inverse", "https://dapi.binance.com/dapi/v1/exchangeInfo", ("symbols",)),
    ),
    "bitget": tuple(
        Endpoint(product, f"https://api.bitget.com/api/v2/mix/market/contracts?productType={product}",
                 ("data",), "code", "00000")
        for product in ("USDT-FUTURES", "USDC-FUTURES", "COIN-FUTURES")
    ),
    "bybit": tuple(
        Endpoint(category, f"https://api.bybit.com/v5/market/instruments-info?category={category}&limit=1000",
                 ("result", "list"), "retCode", pagination="cursor")
        for category in ("linear", "inverse")
    ),
    "coinbase": (
        Endpoint("international", "https://api.international.coinbase.com/api/v1/instruments"),
    ),
    "coinw": (
        Endpoint("perpetual", "https://api.coinw.com/v1/perpum/instruments", ("data",), "code"),
    ),
    "cryptocom": (
        Endpoint("derivatives", "https://api.crypto.com/exchange/v1/public/get-instruments",
                 ("result", "data"), "code"),
    ),
    "gate": tuple(
        Endpoint(f"{kind}/{settle}", f"https://api.gateio.ws/api/v4/{kind}/{settle}/contracts",
                 pagination="offset" if kind == "futures" else None)
        for kind, settle in (("futures", "usdt"), ("futures", "btc"),
                             ("futures", "usd1"), ("delivery", "usdt"))
    ),
    "htx": (
        Endpoint("linear", "https://api.hbdm.com/linear-swap-api/v1/swap_contract_info",
                 ("data",), "status", "ok"),
        Endpoint("inverse", "https://api.hbdm.com/swap-api/v1/swap_contract_info",
                 ("data",), "status", "ok"),
        Endpoint("delivery", "https://api.hbdm.com/api/v1/contract_contract_info",
                 ("data",), "status", "ok"),
    ),
    "kucoin": (
        Endpoint("futures", "https://api-futures.kucoin.com/api/v1/contracts/active",
                 ("data",), "code", "200000"),
    ),
    "okx": tuple(
        Endpoint(kind, f"https://www.okx.com/api/v5/public/instruments?instType={kind}",
                 ("data",), "code")
        for kind in ("SWAP", "FUTURES")
    ),
}
UNSUPPORTED_EXCHANGES = {
    "upbit": "Upbit's public market API covers spot; no futures inventory is available.",
}
EXCHANGES = tuple(sorted({*ENDPOINTS, "mexc", *UNSUPPORTED_EXCHANGES}))


def extract_rows(payload: Any, endpoint: Endpoint) -> list[dict[str, Any]]:
    if endpoint.code_field:
        if not isinstance(payload, dict) or str(payload.get(endpoint.code_field)) != endpoint.success_code:
            raise ValueError(f"unsuccessful API response for {endpoint.market}")
    rows = payload
    for key in endpoint.rows_path:
        if not isinstance(rows, dict) or key not in rows:
            raise ValueError(f"missing {'.'.join(endpoint.rows_path)} in {endpoint.market} response")
        rows = rows[key]
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"expected an instrument list for {endpoint.market}")
    return rows


async def fetch_endpoint(endpoint: Endpoint, timeout_seconds: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cursor = ""
    seen_cursors: set[str] = set()
    seen_symbols: set[str] = set()
    offset = 0
    while True:
        params: dict[str, Any] = {}
        if endpoint.pagination == "cursor" and cursor:
            params["cursor"] = cursor
        elif endpoint.pagination == "offset":
            params = {"limit": 100, "offset": offset}
        url = endpoint.url
        if params:
            url += ("&" if "?" in url else "?") + urlencode(params)
        payload = await fetch_json(url, timeout_seconds)
        page = extract_rows(payload, endpoint)
        rows.extend(page)
        if endpoint.pagination == "cursor":
            cursor = str(payload["result"].get("nextPageCursor") or "")
            if not cursor:
                break
            if cursor in seen_cursors:
                raise ValueError("repeated instruments pagination cursor")
            seen_cursors.add(cursor)
        elif endpoint.pagination == "offset":
            if len(page) < 100:
                break
            symbols = {str(row.get("name")) for row in page}
            if not symbols - seen_symbols:
                raise ValueError("instruments pagination made no progress")
            seen_symbols.update(symbols)
            offset += len(page)
        else:
            break
    return rows


def normalize_contract(exchange: str, market: str, row: dict[str, Any]) -> dict[str, Any] | None:
    """Use affirmative trading statuses; unknown/missing statuses stay inactive."""
    symbol = row.get("symbol")
    base = quote = settle = contract_type = status = None
    active = False
    api_tradable = None
    if exchange == "binance":
        base, quote, settle = row.get("baseAsset"), row.get("quoteAsset"), row.get("marginAsset")
        contract_type = row.get("contractType")
        status = row.get("status") if market == "linear" else row.get("contractStatus")
        active = normalize_text(status) == "TRADING"
    elif exchange == "bitget":
        base, quote = row.get("baseCoin"), row.get("quoteCoin")
        margin_coins = row.get("supportMarginCoins") or []
        settle = margin_coins[0] if len(margin_coins) == 1 else None
        contract_type, status = row.get("symbolType"), row.get("symbolStatus")
        active = normalize_text(status) in {"NORMAL", "RESTRICTEDAPI"}
        api_tradable = normalize_text(status) == "NORMAL"
    elif exchange == "bybit":
        base, quote, settle = row.get("baseCoin"), row.get("quoteCoin"), row.get("settleCoin")
        contract_type, status = row.get("contractType"), row.get("status")
        active = normalize_text(status) == "TRADING" and not coerce_bool(row.get("isPreListing"), False)
    elif exchange == "coinbase":
        if normalize_text(row.get("type")) != "PERP":
            return None
        base, quote = row.get("base_asset_name"), row.get("quote_asset_name")
        settle, contract_type, status = quote, row.get("type"), row.get("trading_state")
        active = normalize_text(status) == "TRADING"
    elif exchange == "coinw":
        symbol, base, quote = row.get("name"), row.get("base"), row.get("quote")
        settle, contract_type, status = quote, "PERPETUAL", row.get("status")
        active = normalize_text(status) == "ONLINE"
    elif exchange == "cryptocom":
        contract_type = row.get("inst_type")
        if normalize_text(contract_type) not in {"PERPETUAL_SWAP", "FUTURE"}:
            return None
        base, quote = row.get("base_ccy"), row.get("quote_ccy")
        active = coerce_bool(row.get("tradable"), False)
        status = "TRADING" if active else "NOT_TRADABLE"
    elif exchange == "gate":
        symbol = row.get("name")
        parts = normalize_symbol(symbol).split("_")
        if len(parts) < 2:
            raise ValueError("missing Gate contract base/quote")
        base, quote = parts[:2]
        kind, settle = market.split("/")
        contract_type = "PERPETUAL" if kind == "futures" else "FUTURES"
        status = row.get("status")
        # Delivery contracts expose in_delisting rather than a trading status.
        active = (normalize_text(status) == "TRADING" if status is not None else
                  kind == "delivery" and (int_or_none(row.get("expire_time")) or 0) > time.time())
        active = active and not coerce_bool(row.get("in_delisting"), True)
        active = active and not coerce_bool(row.get("is_pre_market"), False)
    elif exchange == "htx":
        symbol, base = row.get("contract_code"), row.get("symbol")
        if market == "delivery":
            quote, settle = "USD", base
        else:
            parts = normalize_symbol(row.get("pair") or symbol).split("-")
            if len(parts) != 2:
                raise ValueError("missing HTX contract base/quote")
            base, quote = parts
            settle = quote if market == "linear" else base
        contract_type = row.get("contract_type") or "PERPETUAL"
        status = row.get("contract_status")
        active = int_or_none(status) == 1
    elif exchange == "kucoin":
        # XBT is KuCoin futures' native code for the BTC spot asset.
        base, quote, settle = ("BTC" if normalize_symbol(row.get(key)) == "XBT" else row.get(key)
                               for key in ("baseCurrency", "quoteCurrency", "settleCurrency"))
        contract_type = "FUTURES" if row.get("expireDate") else "PERPETUAL"
        status = row.get("status")
        active = normalize_text(status) == "OPEN"
        active = active and normalize_text(row.get("marketStage") or "NORMAL") == "NORMAL"
    elif exchange == "okx":
        symbol = row.get("instId")
        # baseCcy/quoteCcy are empty for derivatives; use the instrument family.
        parts = normalize_symbol(row.get("instFamily") or row.get("uly")).split("-")
        if len(parts) != 2:
            raise ValueError("missing OKX instrument family")
        base, quote = parts
        settle, contract_type, status = row.get("settleCcy"), row.get("instType"), row.get("state")
        active = normalize_text(status) == "LIVE"
    else:
        raise ValueError(f"no futures normalizer for {exchange}")

    symbol, base, quote = normalize_symbol(symbol), normalize_symbol(base), normalize_symbol(quote)
    if not symbol or not base or not quote:
        raise ValueError(f"missing contract identity in {exchange} {market}")
    return {
        "exchange": exchange,
        "symbol": symbol,
        "pair": pair_key(base, quote),
        "base_asset": base,
        "quote_asset": quote,
        "settle_asset": normalize_symbol(settle) or None,
        "contract_type": normalize_text(contract_type) or None,
        "market": market,
        "flags": {
            "status": normalize_text(status) or None,
            "is_active": active,
            "is_tradable": active if api_tradable is None else active and api_tradable,
        },
    }


def build_payload(exchange: str, pairs: list[dict[str, Any]], *, status: str = "ok",
                  sources: list[str] | None = None, errors: list[dict[str, str]] | None = None,
                  skipped: int = 0, duplicates: int = 0) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "universe_type": "futures",
        "exchange": exchange,
        "generated_at": generated_at_utc(),
        "collection_status": status,
        "source": {"instrument_urls": sources or []},
        "errors": errors or [],
        "summary": {
            "futures_pair_count": len(pairs),
            "active_futures_pair_count": sum(p["flags"]["is_active"] for p in pairs),
            "tradable_futures_pair_count": sum(p["flags"]["is_tradable"] for p in pairs),
            "skipped_instrument_rows": skipped,
            "duplicate_symbol_rows": duplicates,
        },
        "pairs": sorted(pairs, key=lambda p: (p["pair"], p["symbol"], p["market"])),
    }


async def fetch_exchange_universe(exchange: str, timeout_seconds: float = 20.0) -> dict[str, Any]:
    if exchange in UNSUPPORTED_EXCHANGES:
        payload = build_payload(exchange, [], status="unsupported")
        payload["reason"] = UNSUPPORTED_EXCHANGES[exchange]
        return payload
    if exchange == "mexc":
        try:
            payload = await fut_universe_mexc.fetch_exchange_universe(timeout_seconds)
            payload["collection_status"] = "ok"
            return payload
        except Exception as exc:
            return build_payload(exchange, [], status="error", errors=[{"message": str(exc)}])

    endpoints = ENDPOINTS[exchange]
    results = await asyncio.gather(*(fetch_endpoint(e, timeout_seconds) for e in endpoints),
                                   return_exceptions=True)
    pairs: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    skipped = duplicates = 0
    for endpoint, result in zip(endpoints, results):
        if isinstance(result, BaseException):
            errors.append({"url": endpoint.url, "message": str(result)})
            continue
        for row in result:
            try:
                pair = normalize_contract(exchange, endpoint.market, row)
            except (ValueError, TypeError) as exc:
                errors.append({"url": endpoint.url, "message": str(exc)})
                continue
            if pair is None:
                skipped += 1
                continue
            identity = (pair["symbol"], endpoint.market)
            if identity in seen:
                duplicates += 1
                continue
            seen.add(identity)
            pairs.append(pair)
    status = ("partial" if pairs else "error") if errors else "ok"
    return build_payload(exchange, pairs, status=status, sources=[e.url for e in endpoints],
                         errors=errors, skipped=skipped, duplicates=duplicates)


async def fetch_requested_exchanges(exchange_names: list[str], timeout_seconds: float) -> list[dict[str, Any]]:
    names = list(dict.fromkeys(name.strip().lower() for name in exchange_names if name.strip()))
    if not names:
        raise ValueError("at least one exchange must be requested")
    unknown = sorted(set(names) - set(EXCHANGES))
    if unknown:
        raise ValueError(f"unknown futures exchanges: {', '.join(unknown)}")
    return await asyncio.gather(*(fetch_exchange_universe(name, timeout_seconds) for name in names))


def write_universes(payloads: list[dict[str, Any]], out_dir: Path, indent: int = 2) -> None:
    for payload in payloads:
        exchange = payload["exchange"]
        path = write_json(payload, str(out_dir / f"fut_universe_{exchange}.json"), indent)
        count = payload["summary"].get("active_futures_pair_count", 0)
        print(f"{exchange} futures: {count} active contracts ({payload['collection_status']}); {path}")
        for error in payload.get("errors", []):
            print(f"  {error.get('url', exchange)}: {error['message']}")
        if exchange == "mexc":
            funding = fut_universe_mexc.build_funding_rates_payload(payload)
            funding["collection_status"] = payload["collection_status"]
            write_json(funding, str(out_dir / fut_universe_mexc.FUNDING_OUTPUT_FILENAME), indent)


async def async_main() -> int:
    parser = argparse.ArgumentParser(description="Collect public futures universes without committing or pushing.")
    parser.add_argument("--exchanges", nargs="+", choices=EXCHANGES, default=list(EXCHANGES))
    parser.add_argument("--output-dir", type=Path, default=today_output_dir())
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    parser.add_argument("--indent", type=int, default=2)
    args = parser.parse_args()
    validate_common_args(args)
    payloads = await fetch_requested_exchanges(args.exchanges, args.timeout_seconds)
    write_universes(payloads, args.output_dir, args.indent)
    return int(any(p["collection_status"] in {"partial", "error"} for p in payloads))


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
