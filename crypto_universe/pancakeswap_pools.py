"""Daily positive-volume PancakeSwap pools matched by chain and token address."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import tempfile
import time
from collections import defaultdict
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

from .common import fetch_json, generated_at_utc, today_output_dir

EVM_URL = "https://explorer.pancakeswap.com/api/cached/pools/list"
SOL_URL = "https://sol-explorer.pancakeswap.com/api/cached/v1/pools/info/list"
BITGET_URL = "https://api.bitget.com/api/v3/market/coins"
FEE_EXCHANGES = ("bybit", "kucoin", "mexc")
PROTOCOLS = {
    "v2": "V2", "v3": "V3", "stable": "StableSwap", "infinityCl": "Infinity CL",
    "infinityBin": "Infinity Bin", "infinityStable": "Infinity StableSwap",
}
# Explorer network parameter, display name, exchange withdrawal-network aliases.
CHAINS = {
    56: ("bsc", "BNB Chain", ("BSC", "BEP20")),
    1: ("ethereum", "Ethereum", ("ETH", "ERC20", "ETHEREUM")),
    8453: ("base", "Base", ("BASE",)),
    42161: ("arbitrum", "Arbitrum One", ("ARB", "ARBI", "ARBITRUM", "ARBITRUMONE")),
    324: ("zksync", "ZKsync Era", ("ZKSYNCERA", "ZKS20", "ZKV2")),
    204: ("opbnb", "opBNB", ("OPBNB",)),
    59144: ("linea", "Linea", ("LINEA",)),
    143: ("monad", "Monad", ("MONAD",)),
    4663: ("robinhood", "Robinhood", ("ROBINHOOD",)),
    5042: ("arc", "Arc", ("ARC",)),
    8000001001: ("sol", "Solana", ("SOL", "SOLANA")),
}
SOL = 8000001001
NETWORK_IDS = {name: cid for cid, (_, _, names) in CHAINS.items() for name in names}
ZERO = "0x" + "0" * 40
# PancakeSwap SDK's canonical native and wrapped token representations.
WRAPPED_NATIVE = {
    56: ("BNB", "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"),
    204: ("BNB", "0x4200000000000000000000000000000000000006"),
    1: ("ETH", "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"),
    8453: ("ETH", "0x4200000000000000000000000000000000000006"),
    42161: ("ETH", "0x82af49447d8a07e3bd95bd0d56f35241523fbab1"),
    324: ("ETH", "0x5aea5775959fbc2557cc8789bc1bf90a239d9a91"),
    59144: ("ETH", "0xe5d7c2a44ffddf6b295a15c148167daaaf5cf34f"),
    4663: ("ETH", "0x0bd7d308f8e1639fab988df18a8011f41eacad73"),
    143: ("MON", "0x3bd359c1119da7da1d913d1c4d2b7c461115433a"),
    SOL: ("SOL", "So11111111111111111111111111111111111111112"),
}
EVM_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")
EVM_POOL_ID = re.compile(r"0x(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})")
SOL_ADDRESS = re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,44}")
MAX_RPM = 50
MAX_PAGES = 2500


def retry_after(value):
    if not value:
        return 0
    try:
        return max(0, int(value))
    except ValueError:
        try:
            return max(0, (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return 0


def cooldown(headers, *, throttled=False):
    headers = {name.lower(): value for name, value in (headers or {}).items()}
    delay = retry_after(headers.get("retry-after"))
    remaining = headers.get("ratelimit-remaining", headers.get("x-ratelimit-remaining"))
    if throttled or remaining == "0":
        reset = number(headers.get("ratelimit-reset", headers.get("x-ratelimit-reset")))
        if reset is not None:
            if reset > 1_000_000_000:
                reset = max(0, reset - time.time())
            delay = max(delay, reset)
        elif not delay and remaining == "0":
            delay = 60
    return delay


class Explorer:
    """A single sequential request stream shared by both explorers and all retries."""

    def __init__(self, timeout_seconds, requests_per_minute=MAX_RPM):
        if not 0 < requests_per_minute <= MAX_RPM:
            raise ValueError(f"PancakeSwap requests/minute must be > 0 and <= {MAX_RPM}")
        self.timeout = timeout_seconds
        self.interval = 60 / requests_per_minute
        self.next_request = 0.0
        self.stopped = None

    async def get(self, url):
        if self.stopped:
            raise RuntimeError(self.stopped)
        for attempt in range(3):
            await asyncio.sleep(max(0, self.next_request - time.monotonic()))
            self.next_request = time.monotonic() + self.interval
            try:
                headers = {}
                page = await fetch_json(url, self.timeout, response_headers=headers)
                delay = cooldown(headers)
                if delay > 120:
                    self.stopped = "PancakeSwap requested a cooldown longer than two minutes"
                self.next_request = max(self.next_request, time.monotonic() + delay)
                return page
            except HTTPError as exc:
                if exc.code not in {408, 429, 500, 502, 503, 504}:
                    raise
                delay = max(2 ** (attempt + 1), cooldown(exc.headers, throttled=exc.code == 429))
                if delay > 120 or (exc.code == 429 and attempt == 2):
                    self.stopped = f"HTTP {exc.code}, collection deferred after provider throttling"
                    raise RuntimeError(self.stopped) from exc
                if attempt == 2:
                    self.next_request = max(self.next_request, time.monotonic() + delay)
                    raise
            except (URLError, TimeoutError, ConnectionError):
                if attempt == 2:
                    raise
                delay = 2 ** (attempt + 1)
            self.next_request = max(self.next_request, time.monotonic() + delay)


def token_address(chain, value):
    if not isinstance(value, str):
        return None
    value = value.strip()
    if chain == SOL:
        return value if SOL_ADDRESS.fullmatch(value) else None
    return value.lower() if EVM_ADDRESS.fullmatch(value) else None


def address_index(listings, sources):
    """An exchange can identify only a base it lists. Conflicts stay unresolved."""
    candidates = defaultdict(set)
    unsupported = set()
    supported = defaultdict(set)
    for exchange, rows in sources.items():
        for row in rows:
            base = str(row.get("coin" if exchange == "bitget" else "currency") or "").strip().upper()
            if exchange not in listings.get(base, ()):
                continue
            for chain_row in row.get("chains") or []:
                chain_name = chain_row.get("chain") if exchange == "bitget" else chain_row.get("chain_name")
                if not chain_name:
                    continue
                chain = NETWORK_IDS.get(chain_name.upper())
                if chain is None:
                    unsupported.add(base)
                    continue
                supported[base].add(chain)
                raw = chain_row.get("contractAddress") if exchange == "bitget" else chain_row.get("contract_address")
                address = token_address(chain, raw)
                if address and address != ZERO:
                    candidates[base, chain].add(address)

    for chain, (base, address) in WRAPPED_NATIVE.items():
        if base in listings:
            candidates[base, chain].add(address)
    by_address = defaultdict(set)
    networks = defaultdict(set)
    conflicts = set()
    for (base, chain), addresses in candidates.items():
        if len(addresses) > 1:
            conflicts.add(base)
            continue
        address, = addresses
        by_address[chain, address].add(base)
        networks[base].add(chain)
        if chain != SOL and WRAPPED_NATIVE.get(chain) == (base, address):
            by_address[chain, ZERO].add(base)
    missing = {base for base, chains in supported.items() if any((base, c) not in candidates for c in chains)}
    return by_address, networks, conflicts, missing, unsupported - supported.keys()


def currency_rows(data):
    if not isinstance(data, list):
        raise ValueError("expected a currency list")
    for row in data:
        if not isinstance(row, dict) or not isinstance(row.get("chains"), list):
            raise ValueError("invalid currency record")
        for chain in row["chains"]:
            if not isinstance(chain, dict) or not isinstance(chain.get("chain_name", chain.get("chain")), str):
                raise ValueError("invalid currency network")
    return data


def number(value):
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (ValueError, OverflowError, TypeError):
        return None
    return parsed if math.isfinite(parsed) and parsed >= 0 else None


async def pool_pages(explorer, solana):
    if solana:
        args = [("poolType", "concentrated"), ("poolSortField", "default"),
                ("order", "desc"), ("pageSize", 100)]
    else:
        args = [("protocols", name) for name in PROTOCOLS]
        args += [("chains", config[0]) for chain, config in CHAINS.items() if chain != SOL]
        args.append(("orderBy", "volumeUSD24h"))
    cursor = None
    seen = set()
    for page in range(1, MAX_PAGES + 1):
        if solana:
            payload = await explorer.get(SOL_URL + "?" + urlencode([*args, ("page", page)]))
            page_info = payload["pagination"]
            if (payload.get("success") is not True or not isinstance(payload["data"], list)
                    or page_info["page"] != page or type(page_info["totalPages"]) is not int):
                raise ValueError("invalid Solana pool page")
            rows = payload["data"]
            yield rows
            if page >= page_info["totalPages"]:
                return
            if not rows:
                raise ValueError("empty intermediate Solana pool page")
        else:
            query = args if cursor is None else [*args, ("after", cursor)]
            payload = await explorer.get(EVM_URL + "?" + urlencode(query))
            rows = payload["rows"]
            if not isinstance(rows, list) or type(payload["hasNextPage"]) is not bool:
                raise ValueError("invalid EVM pool page")
            yield rows
            # The listing is sorted by descending 24h USD volume.
            if not payload["hasNextPage"] or (rows and number(rows[-1]["volumeUSD24h"]) == 0):
                return
            cursor = payload.get("endCursor")
            if not rows or not isinstance(cursor, str) or not cursor or cursor in seen:
                raise ValueError("invalid or repeated EVM pool cursor")
            seen.add(cursor)
    raise ValueError("PancakeSwap pagination limit reached")


def solana_quote_rate(mint, by_address, usdt_rates):
    if mint == WRAPPED_NATIVE[SOL][1]:
        symbol = "SOL"
    else:
        symbols = by_address.get((SOL, mint), set())
        if len(symbols) != 1:
            return None
        symbol, = symbols
    rate = number(1 if symbol == "USDT" else usdt_rates.get(symbol))
    return rate if rate else None


def read_pool(row, solana, by_address, usdt_rates):
    """Return matched bases and one pool, or raise if we can't classify a row."""
    chain = SOL if solana else row["chainId"]
    if chain not in CHAINS or (chain == SOL) != solana:
        raise ValueError(f"unexpected pool chain {chain!r}")
    volume = number(row["day"].get("volume") if solana else row.get("volumeUSD24h"))
    quote_volume = number(row["day"].get("volumeQuote")) if solana else None
    if (volume == 0 and not quote_volume) or (volume is None and quote_volume == 0):
        return set(), None
    first, second = (row["mintA"], row["mintB"]) if solana else (row["token0"], row["token1"])
    key = "address" if solana else "id"
    addresses = [token_address(chain, t[key]) for t in (first, second)]
    if None in addresses:
        raise ValueError("invalid pool token address")
    bases = by_address.get((chain, addresses[0]), set()) | by_address.get((chain, addresses[1]), set())
    if not bases:
        return set(), None
    if volume is None and not quote_volume:
        return bases, None
    pool_id = row["id"]
    pattern = SOL_ADDRESS if solana else EVM_POOL_ID
    if not isinstance(pool_id, str) or not pattern.fullmatch(pool_id):
        raise ValueError("invalid pool ID")
    slug, display, _ = CHAINS[chain]
    if solana:
        pool_type = "V3"
        fee = number(row.get("feeRate"))
        fee_percent = None if fee is None else fee * 100
        dynamic = False
    else:
        pool_id = pool_id.lower()
        pool_type = PROTOCOLS[row["protocol"]]
        dynamic = row.get("isDynamicFee") is True
        fee = number(row.get("feeTier"))
        # StableSwap's list response uses zero even for fee-bearing pools.
        if row["protocol"] == "stable" and fee == 0:
            fee = None
        fee_percent = None if dynamic or fee is None else fee / 10_000
    native_turnover = {}
    if not volume and quote_volume:
        rate = solana_quote_rate(addresses[1], by_address, usdt_rates)
        volume = (number(quote_volume * rate) or None) if rate else None
        native_turnover = {"volume_24h_quote": quote_volume,
                           "volume_quote_symbol": second.get("symbol") or addresses[1]}
    return bases, {
        "chain": display, "pool_id": pool_id,
        "token0": first.get("symbol") or addresses[0], "token1": second.get("symbol") or addresses[1],
        "pool_type": pool_type, "fee_percent": fee_percent, "dynamic_fee": dynamic,
        "volume_24h_usdt": volume, "tvl_usd": number(row.get("tvl") if solana else row.get("tvlUSD")),
        **native_turnover,
        "url": f"https://pancakeswap.finance/liquidity/pool/{slug}/{pool_id}",
    }


async def collect_pools(spot, fee_payloads, timeout_seconds=20, requests_per_minute=MAX_RPM):
    listings = defaultdict(set)
    for pair in spot["pairs"]:
        listings[pair["base_asset"]].update(v["exchange"] for v in pair["venues"])
    exchanges = set(spot["exchanges"])
    errors = []
    failed_sources = set()
    sources = {}
    for exchange in FEE_EXCHANGES:
        if exchange not in exchanges:
            continue
        data = fee_payloads.get(exchange)
        try:
            if not isinstance(data, dict) or data.get("exchange") != exchange:
                raise ValueError("currency metadata unavailable")
            sources[exchange] = currency_rows(data.get("currencies"))
        except ValueError as exc:
            failed_sources.add(exchange)
            errors.append(f"{exchange} currency metadata: {exc}")
    if "bitget" in exchanges:
        try:
            result = await fetch_json(BITGET_URL, timeout_seconds)
            if result.get("code") != "00000":
                raise ValueError("invalid Bitget coins response")
            sources["bitget"] = currency_rows(result.get("data"))
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            failed_sources.add("bitget")
            errors.append(f"Bitget currency metadata: {exc}")

    by_address, networks, conflicts, missing, unsupported = address_index(listings, sources)
    explorer = Explorer(timeout_seconds, requests_per_minute)
    pools = {}
    usdt_rates = spot.get("usdt_rates", {})
    pool_ids = defaultdict(set)
    incomplete = set()
    failed_chains = set()
    finished_chains = set()
    chains_needed = {chain for chain, _ in by_address}
    for solana in (False, True):
        covered = {chain for chain in chains_needed if (chain == SOL) == solana}
        if not covered:
            continue
        label = "Solana" if solana else "EVM"
        invalid = 0
        try:
            async for rows in pool_pages(explorer, solana):
                for row in rows:
                    try:
                        bases, pool = read_pool(row, solana, by_address, usdt_rates)
                    except (AttributeError, KeyError, ValueError, TypeError):
                        invalid += 1
                        continue
                    if not pool:
                        incomplete.update(bases)
                        continue
                    chain = SOL if solana else row["chainId"]
                    pool_key = f"{chain}:{pool['pool_id']}"
                    pools.setdefault(pool_key, pool)
                    for base in bases:
                        pool_ids[base].add(pool_key)
            if invalid:
                raise ValueError(f"{invalid} unclassifiable pool rows")
            finished_chains.update(covered)
        except (OSError, RuntimeError, ValueError, AttributeError, KeyError, TypeError) as exc:
            errors.append(f"{label} pool listing: {exc}")
            failed_chains.update(covered)

    assets = {}
    for base, venues in sorted(listings.items()):
        ids = sorted(pool_ids[base], key=lambda key: (pools[key]["volume_24h_usdt"] is None,
                                                       -(pools[key]["volume_24h_usdt"] or 0), key))
        checked = networks[base] & finished_chains
        issues = venues & failed_sources or networks[base] & failed_chains or base in incomplete
        if issues:
            status = "partial" if ids or checked else "error"
        elif base in conflicts:
            status = "partial" if checked else "ambiguous"
        elif base in missing:
            status = "partial" if checked else "unresolved"
        elif checked:
            status = "ok"
        else:
            status = "unsupported" if base in unsupported else "unresolved"
        assets[base] = {"status": status, "pool_ids": ids}
        if checked:
            assets[base]["checked_networks"] = sorted(CHAINS[c][1] for c in checked)
    if incomplete:
        errors.append(f"24h turnover missing or invalid for pools matching {len(incomplete)} assets")
    status = "ok"
    if errors:
        status = "partial" if finished_chains or pools else "error"
    return {
        "schema_version": 1, "generated_at": generated_at_utc(), "spot_generated_at": spot["generated_at"],
        "collection_status": status, "errors": errors,
        "networks": [config[1] for config in CHAINS.values()],
        "assets": assets, "pools": dict(sorted(pools.items())),
    }


def save_pools(payload, directory, indent):
    path = directory / "pancakeswap_pools.json"
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, delete=False) as out:
        tmp = Path(out.name)
        try:
            json.dump(payload, out, indent=indent, allow_nan=False)
            out.write("\n")
            out.close()
            tmp.chmod(0o644)
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)
    print(f"PancakeSwap: {len(payload['pools'])} active pools ({payload['collection_status']}) in {path}")
    for error in payload["errors"]:
        print(f"PancakeSwap: {error}")


def add_rate_argument(parser):
    parser.add_argument("--pancakeswap-requests-per-minute", type=int, default=MAX_RPM,
                        choices=range(1, MAX_RPM + 1), metavar=f"1..{MAX_RPM}",
                        help="Maximum serial Explorer requests per minute, including retries")


async def async_main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=today_output_dir(),
                        help="Directory containing spot and withdrawal snapshots")
    parser.add_argument("--timeout-seconds", type=float, default=20)
    parser.add_argument("--indent", type=int, default=2)
    add_rate_argument(parser)
    args = parser.parse_args()
    if args.timeout_seconds <= 0 or args.indent < 0:
        parser.error("timeout must be positive and indentation must be nonnegative")
    directory = args.output_dir
    spot = json.loads((directory / "spot_universe_combined.json").read_text())
    fees = {}
    for exchange in FEE_EXCHANGES:
        path = directory / f"crypto_withdrawal_fee_{exchange}.json"
        try:
            fees[exchange] = json.loads(path.read_text())
        except (OSError, ValueError):
            pass  # collect_pools reports missing or malformed metadata for in-scope venues.
    payload = await collect_pools(spot, fees, args.timeout_seconds, args.pancakeswap_requests_per_minute)
    save_pools(payload, directory, args.indent)
    return int(payload["collection_status"] != "ok")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(async_main()))
