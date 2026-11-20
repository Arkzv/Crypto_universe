import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.error import HTTPError

from crypto_universe import pancakeswap_pools as pcs


def addr(i):
    return f"0x{i:040x}"


def evm(volume=10, *, pid=100, t0=1, t1=2, **fields):
    return {
        "id": addr(pid), "chainId": 56, "volumeUSD24h": volume, "protocol": "v3",
        "token0": {"id": addr(t0), "symbol": "A"}, "token1": {"id": addr(t1), "symbol": "B"},
        "feeTier": 500, **fields,
    }


def universe(bases, exchanges):
    return {"generated_at": "test", "exchanges": exchanges,
            "pairs": [{"base_asset": base, "venues": [{"exchange": e} for e in exchanges]} for base in bases]}


def fees(exchange, rows):
    return {"exchange": exchange, "currencies": [
        {"currency": base, "chains": [{"chain_name": chain, "contract_address": address} for chain, address in networks]}
        for base, networks in rows.items()
    ]}


def page(rows, cursor=None):
    return {"rows": rows, "endCursor": cursor, "hasNextPage": cursor is not None}


class CollectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_paginate_match_both_sides_filter_zero_and_deduplicate(self):
        spot = universe(["A", "B", "NOPOOL", "OTHER", "NOTLISTED"], ["mexc"])
        spot["pairs"][-1]["venues"] = [{"exchange": "gate"}]
        source = fees("mexc", {"A": [("BSC", addr(1))], "B": [("BSC", addr(2))],
                               "NOPOOL": [("BSC", addr(6))], "NOTLISTED": [("BSC", addr(1))]})
        get = AsyncMock(side_effect=[
            page([evm(10), evm("0.5", pid=101, isDynamicFee=True, feeTier=0)], "next"),
            page([evm(0.5, pid=101), evm(0.25, pid=102, t1=7),
                  evm(0, pid=103), evm("0.00", pid=104)], "unused"),
        ])
        with patch.object(pcs.Explorer, "get", get):
            data = await pcs.collect_pools(spot, {"mexc": source})
        self.assertEqual(get.await_count, 2)
        self.assertIn("after=next", get.await_args_list[1].args[0])
        self.assertEqual(data["collection_status"], "ok")
        a = data["assets"]["A"]["pool_ids"]
        b = data["assets"]["B"]["pool_ids"]
        self.assertEqual(len(a), 3)
        self.assertEqual(b, a[:2])
        self.assertEqual(len(data["pools"]), 3)
        self.assertEqual(sum(data["pools"][k]["volume_24h_usdt"] for k in a), 10.75)
        self.assertEqual(data["pools"][a[0]]["fee_percent"], 0.05)
        self.assertTrue(data["pools"][a[1]]["dynamic_fee"])
        self.assertIsNone(data["pools"][a[1]]["fee_percent"])
        self.assertEqual(data["assets"]["NOPOOL"]["status"], "ok")
        self.assertEqual(data["assets"]["NOPOOL"]["pool_ids"], [])
        self.assertEqual(data["assets"]["NOTLISTED"]["status"], "unresolved")

    async def test_scope_and_address_conflicts(self):
        bases = ["A", "B", "NOCONTRACT", "MISSING", "FILLED", "OTHERCHAIN"]
        spot = universe(bases, ["mexc", "bitget"])
        mexc = fees("mexc", {"A": [("BSC", addr(1))], "B": [("BSC", addr(2)), ("BASE", addr(4))],
                             "NOCONTRACT": [("BSC", None)], "MISSING": [("BSC", None), ("BASE", addr(5))],
                             "FILLED": [("BSC", None)], "OTHERCHAIN": [("TRX", "T123")]})
        bitget = {"code": "00000", "data": [
            {"coin": base, "chains": [{"chain": "BEP20", "contractAddress": address}]}
            for base, address in [("A", addr(22)), ("B", addr(33)), ("FILLED", addr(5))]
        ]}
        with (patch.object(pcs, "fetch_json", AsyncMock(return_value=bitget)),
              patch.object(pcs.Explorer, "get", AsyncMock(return_value=page([evm()])))):
            data = await pcs.collect_pools(spot, {"mexc": mexc})
        self.assertEqual({a: v["status"] for a, v in data["assets"].items()},
                         {"A": "ambiguous", "B": "partial", "NOCONTRACT": "unresolved",
                          "MISSING": "partial", "FILLED": "ok", "OTHERCHAIN": "unsupported"})
        self.assertFalse(data["pools"])

    async def test_native_wrapped_solana_and_cross_chain_identities(self):
        spot = universe(["BNB", "SOL", "A"], ["mexc"])
        mint = "2kYnNbfR1R7gaLWjb7GSXmQGSrpJzjUoMpJgASnS3ped"
        mexc = fees("mexc", {"A": [("SOL", mint)]})
        bnb = evm()
        bnb["token0"]["id"] = pcs.WRAPPED_NATIVE[56][1].upper().replace("0X", "0x")
        bnb["token1"]["id"] = pcs.ZERO
        unrelated = {**bnb, "chainId": 8453, "token1": {"id": addr(9), "symbol": "BNB"}}
        solpool = {"id": "Gh51XUdnBHrUTaKa7HK4qjoKXxmJtQNeGYA1eZnnNtA1",
                   "mintA": {"address": pcs.WRAPPED_NATIVE[pcs.SOL][1], "symbol": "SOL"},
                   "mintB": {"address": mint, "symbol": "A"}, "day": {"volume": 9}, "feeRate": 0.003}
        with patch.object(pcs.Explorer, "get", AsyncMock(side_effect=[
            page([bnb, unrelated]),
            {"success": True, "pagination": {"totalPages": 2, "page": 1},
             "data": [{**solpool, "day": {"volume": 0}}]},
            {"success": True, "pagination": {"totalPages": 2, "page": 2}, "data": [solpool]},
        ])):
            data = await pcs.collect_pools(spot, {"mexc": mexc})
        self.assertEqual(len(data["assets"]["BNB"]["pool_ids"]), 1)
        self.assertEqual(data["assets"]["SOL"]["pool_ids"], data["assets"]["A"]["pool_ids"])
        k, = data["assets"]["A"]["pool_ids"]
        self.assertEqual(data["pools"][k]["fee_percent"], 0.3)
        self.assertTrue(data["pools"][k]["url"].endswith("/sol/" + solpool["id"]))

    async def test_incomplete_and_failed_lookups(self):
        spot = universe(["A", "B", "SOL"], ["mexc", "bitget"])
        mexc = fees("mexc", {"A": [("BSC", addr(1))], "B": [("BSC", addr(11))]})
        with (patch.object(pcs, "fetch_json", AsyncMock(return_value={"code": "00000", "data": [None]})),
              patch.object(pcs.Explorer, "get", AsyncMock(side_effect=[
                  page([evm()], "first"), page([evm()], "first"), ValueError("Solana API unavailable"),
              ]))):
            d = await pcs.collect_pools(spot, {"mexc": mexc})
        self.assertEqual(d["assets"]["A"]["status"], "partial")
        self.assertEqual(d["assets"]["B"]["status"], "error")
        self.assertEqual(d["assets"]["SOL"]["status"], "error")
        self.assertEqual(d["collection_status"], "partial")
        self.assertTrue(any("cursor" in message for message in d["errors"]))
        self.assertTrue(any("Bitget" in message for message in d["errors"]))

    async def test_malformed_targeted_volume_never_reported_as_zero(self):
        spot = universe(["A"], ["mexc"])
        with patch.object(pcs.Explorer, "get", AsyncMock(return_value=page([evm("nan"), evm(None), evm(-2)]))):
            data = await pcs.collect_pools(spot, {"mexc": fees("mexc", {"A": [("BSC", addr(1))]})})
        self.assertFalse(data["pools"])
        self.assertEqual(data["collection_status"], "partial")
        self.assertEqual(data["assets"]["A"]["status"], "partial")

    async def test_solana_positive_quote_turnover_and_trusted_conversion(self):
        a = "2kYnNbfR1R7gaLWjb7GSXmQGSrpJzjUoMpJgASnS3ped"
        b = "zBTCug3er3tLyffELcvDNrKkCymbPWysGcWihESYfLg"
        unknown = "cbbtcf3aa214zXHbiAZQwf4122FBYbraNdFqgw4iMij"
        spot = universe(["A", "B"], ["mexc"])
        spot["usdt_rates"] = {"SOL": 100, "B": 5, "FAKE": 9999}
        mexc = fees("mexc", {"A": [("SOL", a)], "B": [("SOL", b)]})
        rows = []
        volumes = [(2, 600, b), (None, 3, b), (0, 0.5, pcs.WRAPPED_NATIVE[pcs.SOL][1]),
                   (0, 3, unknown), (0, 0, unknown), (None, 0, b)]
        for i, (usd, qty, mint) in enumerate(volumes):
            rows.append({"id": "1" * 31 + str(i + 1), "mintA": {"address": a, "symbol": "A"},
                         "mintB": {"address": mint, "symbol": "FAKE"},
                         "feeRate": 0.001, "day": {"volume": usd, "volumeQuote": qty}})
        with patch.object(pcs.Explorer, "get", AsyncMock(return_value={
                "success": True, "pagination": {"totalPages": 1, "page": 1}, "data": rows})):
            data = await pcs.collect_pools(spot, {"mexc": mexc})
        matched = [data["pools"][k] for k in data["assets"]["A"]["pool_ids"]]
        self.assertEqual([p["volume_24h_usdt"] for p in matched], [50, 15, 2, None])
        self.assertNotIn("volume_24h_quote", matched[2])
        self.assertEqual(matched[3]["volume_24h_quote"], 3)
        self.assertEqual(matched[3]["volume_quote_symbol"], "FAKE")
        self.assertEqual(data["assets"]["A"]["status"], "ok")

    def test_atomic_snapshot_retains_previous_file_on_serialization_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            folder = Path(tmpdir)
            data = {"pools": {}, "errors": [], "collection_status": "ok"}
            pcs.save_pools(data, folder, 2)
            with self.assertRaises(ValueError):
                pcs.save_pools({**data, "bad": float("inf")}, folder, 2)
            self.assertEqual(list(folder.iterdir()), [folder / "pancakeswap_pools.json"])
            self.assertEqual(json.loads((folder / "pancakeswap_pools.json").read_text()), data)


class RateLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_shared_rate_limit_retry_and_pacing_including_success_headers(self):
        clock = 0
        starts = []
        calls = 0

        async def sleep(delay):
            nonlocal clock
            clock += delay

        async def fetch(url, timeout, *, response_headers):
            nonlocal calls
            calls += 1
            starts.append(clock)
            if calls == 1:
                raise HTTPError(url, 429, "", {"Retry-After": "15"}, None)
            if calls == 2:
                response_headers.update({"ratelimit-remaining": "0", "ratelimit-reset": "10"})
            return {}

        with (patch.object(pcs.time, "monotonic", side_effect=lambda: clock),
              patch.object(pcs.asyncio, "sleep", side_effect=sleep), patch.object(pcs, "fetch_json", side_effect=fetch)):
            api = pcs.Explorer(10, 20)
            await api.get(pcs.EVM_URL)
            await api.get(pcs.SOL_URL)
            await api.get(pcs.SOL_URL)
        self.assertEqual(starts, [0, 15, 25, 28])
        later = datetime.now(UTC) + timedelta(seconds=30)
        self.assertGreater(pcs.retry_after(format_datetime(later, usegmt=True)), 28)

    async def test_long_retry_after_stops_all_further_requests(self):
        fetch = AsyncMock(side_effect=HTTPError("test", 429, "", {"Retry-After": "360"}, None))
        with patch.object(pcs, "fetch_json", fetch):
            api = pcs.Explorer(10)
            for url in [pcs.EVM_URL, pcs.SOL_URL]:
                with self.assertRaisesRegex(RuntimeError, "deferred"):
                    await api.get(url)
        fetch.assert_awaited_once()

    async def test_429_reset_without_retry_after(self):
        for header, value in [("RateLimit-Reset", "22"), ("X-RateLimit-Reset", "1900000022")]:
            with self.subTest(header=header):
                fetch = AsyncMock(side_effect=[HTTPError("test", 429, "", {header: value}, None), {}])
                with (patch.object(pcs, "fetch_json", fetch),
                      patch.object(pcs.time, "time", return_value=1900000000),
                      patch.object(pcs.time, "monotonic", return_value=0),
                      patch.object(pcs.asyncio, "sleep", new_callable=AsyncMock) as sleep):
                    await pcs.Explorer(10).get(pcs.EVM_URL)
                self.assertEqual(sleep.await_args.args[0], 22)
