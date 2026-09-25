from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from crypto_universe import fut_universe as futures, fut_universe_mexc, spot_universe
from crypto_universe.spot_universe_mexc import normalize_mexc_pair


class ContractStatusTests(unittest.TestCase):
    def test_active_inactive_and_unknown_statuses_across_exchanges(self):
        examples = [
            ("binance", "linear", {"symbol": "BTCUSDT", "baseAsset": "BTC", "quoteAsset": "USDT"}, "status", "TRADING", "PENDING_TRADING"),
            ("binance", "inverse", {"symbol": "BTCUSD_PERP", "baseAsset": "BTC", "quoteAsset": "USD"}, "contractStatus", "TRADING", "DELIVERED"),
            ("bitget", "USDT-FUTURES", {"symbol": "BTCUSDT", "baseCoin": "BTC", "quoteCoin": "USDT"}, "symbolStatus", "normal", "limit_open"),
            ("bitmart", "futures", {"symbol": "BTCUSDT", "base_currency": "BTC", "quote_currency": "USDT"}, "status", "Trading", "Delisted"),
            ("bybit", "linear", {"symbol": "BTCUSDT", "baseCoin": "BTC", "quoteCoin": "USDT"}, "status", "Trading", "PreLaunch"),
            ("coinbase", "international", {"symbol": "BTC-PERP", "type": "PERP", "base_asset_name": "BTC", "quote_asset_name": "USDC"}, "trading_state", "TRADING", "HALT"),
            ("coinw", "perpetual", {"name": "BTC", "base": "btc", "quote": "usdt"}, "status", "online", "preOffline"),
            ("cryptocom", "derivatives", {"symbol": "BTCUSD-PERP", "base_ccy": "BTC", "quote_ccy": "USD", "inst_type": "PERPETUAL_SWAP"}, "tradable", True, False),
            ("gate", "futures/usdt", {"name": "BTC_USDT", "in_delisting": False}, "status", "trading", "prelaunch"),
            ("htx", "linear", {"symbol": "BTC", "contract_code": "BTC-USDT"}, "contract_status", 1, 3),
            ("kucoin", "futures", {"symbol": "XBTUSDTM", "baseCurrency": "XBT", "quoteCurrency": "USDT"}, "status", "Open", "Closed"),
            ("okx", "SWAP", {"instId": "BTC-USDT-SWAP", "instFamily": "BTC-USDT", "baseCcy": "", "quoteCcy": ""}, "state", "live", "suspend"),
        ]
        for exchange, market, row, field, live, halted in examples:
            for status, expected in ((live, True), (halted, False), (None, False)):
                with self.subTest(exchange=exchange, market=market, status=status):
                    pair = futures.normalize_contract(exchange, market, {**row, field: status})
                    self.assertEqual(pair["flags"]["is_active"], expected)
                    self.assertEqual(pair["flags"]["is_tradable"], expected)
                    self.assertEqual(pair["base_asset"], "BTC")

    def test_spot_and_options_are_not_futures(self):
        for exchange, row in [
            ("coinbase", {"type": "SPOT"}),
            ("cryptocom", {"inst_type": "CCY_PAIR"}),
            ("cryptocom", {"inst_type": "VANILLA_OPTION"}),
        ]:
            self.assertIsNone(futures.normalize_contract(exchange, "mixed", row))

    def test_contract_aliases_and_quotes_are_preserved(self):
        kucoin = futures.normalize_contract("kucoin", "futures", {
            "symbol": "XBTUSDM", "baseCurrency": "XBT", "quoteCurrency": "USD",
            "settleCurrency": "XBT", "status": "Open",
        })
        self.assertEqual((kucoin["pair"], kucoin["symbol"], kucoin["settle_asset"]),
                         ("BTC/USD", "XBTUSDM", "BTC"))
        scaled = futures.normalize_contract("bybit", "linear", {
            "symbol": "1000PEPEUSDT", "baseCoin": "1000PEPE", "quoteCoin": "USDT", "status": "Trading",
        })
        self.assertEqual(scaled["pair"], "1000PEPE/USDT")
        okx = futures.normalize_contract("okx", "FUTURES", {
            "instId": "BTC-USD-270326", "uly": "BTC-USD", "state": "live", "settleCcy": "BTC",
        })
        self.assertEqual(okx["pair"], "BTC/USD")

    def test_prelisting_delisting_and_expiry_are_not_active(self):
        bybit = futures.normalize_contract("bybit", "linear", {
            "symbol": "BTCUSDT", "baseCoin": "BTC", "quoteCoin": "USDT",
            "status": "Trading", "isPreListing": True,
        })
        self.assertFalse(bybit["flags"]["is_active"])
        for extras in ({"in_delisting": True}, {"is_pre_market": True}):
            gate = futures.normalize_contract("gate", "futures/usdt", {
                "name": "BTC_USDT", "status": "trading", "in_delisting": False, **extras,
            })
            self.assertFalse(gate["flags"]["is_active"])
        with patch.object(futures.time, "time", return_value=100):
            for expiry, active in [(90, False), (110, True), (None, False)]:
                gate = futures.normalize_contract("gate", "delivery/usdt", {
                    "name": "BTC_USDT_270326", "in_delisting": False, "expire_time": expiry,
                })
                self.assertEqual(gate["pair"], "BTC/USDT")
                self.assertEqual(gate["flags"]["is_active"], active)

    def test_exchange_trading_is_separate_from_api_permission(self):
        row = {"symbol": "BTC_USDT", "baseCoin": "BTC", "quoteCoin": "USDT",
               "state": 0, "type": 1, "apiAllowed": False}
        mexc = fut_universe_mexc.normalize_mexc_futures_pair(row)
        self.assertTrue(mexc["flags"]["is_active"])
        self.assertFalse(mexc["flags"]["is_tradable"])
        for overrides in ({"state": 4}, {"type": 2}, {"preMarket": True}, {"state": None}):
            self.assertFalse(fut_universe_mexc.normalize_mexc_futures_pair({**row, **overrides})["flags"]["is_active"])
        bitget = futures.normalize_contract("bitget", "USDT-FUTURES", {
            "symbol": "BTCUSDT", "baseCoin": "BTC", "quoteCoin": "USDT", "symbolStatus": "restrictedAPI",
        })
        self.assertTrue(bitget["flags"]["is_active"])
        self.assertFalse(bitget["flags"]["is_tradable"])

    def test_mexc_unknown_spot_status_is_not_active(self):
        row = {"symbol": "BTCUSDT", "baseAsset": "BTC", "quoteAsset": "USDT", "isSpotTradingAllowed": True}
        for status in (None, "", "unknown", "2"):
            self.assertIsNone(normalize_mexc_pair({**row, "status": status}))
        self.assertIsNotNone(normalize_mexc_pair({**row, "status": "1"}))

    def test_api_error_envelopes_cannot_become_empty_successes(self):
        for endpoint in [futures.ENDPOINTS[ex][0] for ex in ("bybit", "bitget", "kucoin", "okx", "htx", "bitmart")]:
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(ValueError):
                    futures.extract_rows({endpoint.code_field: "FAIL", "data": [], "result": {"list": []}}, endpoint)
        with self.assertRaises(ValueError):
            fut_universe_mexc.extract_data_rows({"success": False, "code": 1, "data": []}, "contract/detail")


class FuturesCollectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_cursor_pagination_includes_later_pages_and_encodes_cursor(self):
        fetch = AsyncMock(side_effect=[
            {"retCode": 0, "result": {"list": [{"symbol": "ONE"}], "nextPageCursor": "next&last=ONE"}},
            {"retCode": 0, "result": {"list": [{"symbol": "TWO"}], "nextPageCursor": ""}},
        ])
        with patch.object(futures, "fetch_json", fetch):
            rows = await futures.fetch_endpoint(futures.ENDPOINTS["bybit"][0], 1)
        self.assertEqual([r["symbol"] for r in rows], ["ONE", "TWO"])
        self.assertIn("cursor=next%26last%3DONE", fetch.call_args_list[1].args[0])

    async def test_repeated_cursor_fails_instead_of_truncating_or_looping(self):
        fetch = AsyncMock(return_value={"retCode": 0, "result": {"list": [], "nextPageCursor": "same"}})
        with patch.object(futures, "fetch_json", fetch):
            with self.assertRaisesRegex(ValueError, "repeated"):
                await futures.fetch_endpoint(futures.ENDPOINTS["bybit"][0], 1)
        self.assertEqual(fetch.await_count, 2)

    async def test_offset_pagination_includes_all_contracts(self):
        fetch = AsyncMock(side_effect=[[{"name": f"COIN{i}_USDT"} for i in range(100)], [{"name": "LAST_USDT"}]])
        with patch.object(futures, "fetch_json", fetch):
            rows = await futures.fetch_endpoint(futures.ENDPOINTS["gate"][0], 1)
        self.assertEqual(len(rows), 101)
        self.assertIn("offset=100", fetch.call_args_list[1].args[0])

    async def test_partial_failure_keeps_successful_contracts_and_reports_gap(self):
        row = {"symbol": "BTCUSDT", "baseAsset": "BTC", "quoteAsset": "USDT", "status": "TRADING"}
        fetch = AsyncMock(side_effect=[[row, row], TimeoutError("inverse unavailable")])
        with patch.object(futures, "fetch_endpoint", fetch):
            payload = await futures.fetch_exchange_universe("binance", 1)
        self.assertEqual(payload["collection_status"], "partial")
        self.assertEqual(payload["summary"]["duplicate_symbol_rows"], 1)
        self.assertEqual(len(payload["pairs"]), 1)
        self.assertEqual(payload["errors"][0]["message"], "inverse unavailable")

    async def test_failure_is_distinct_from_successful_empty_and_unsupported(self):
        with patch.object(futures, "fetch_endpoint", AsyncMock(side_effect=TimeoutError("offline"))):
            failed = await futures.fetch_exchange_universe("bitmart", 1)
        with patch.object(futures, "fetch_endpoint", AsyncMock(return_value=[])):
            empty = await futures.fetch_exchange_universe("bitmart", 1)
        unsupported = await futures.fetch_exchange_universe("upbit", 1)
        self.assertEqual([p["collection_status"] for p in (failed, empty, unsupported)], ["error", "ok", "unsupported"])

    async def test_requested_scope_is_deduplicated_and_does_not_force_mexc(self):
        fetch = AsyncMock(side_effect=lambda name, timeout: {"exchange": name})
        with patch.object(futures, "fetch_exchange_universe", fetch):
            result = await futures.fetch_requested_exchanges([" OKX ", "okx", "upbit"], 1)
        self.assertEqual(result, [{"exchange": "okx"}, {"exchange": "upbit"}])
        self.assertEqual(set(futures.EXCHANGES), set(spot_universe.EXCHANGE_FETCHERS))

    async def test_combined_collection_writes_futures_before_publishing_output_directory(self):
        pair = {"exchange": "binance", "symbol": "BTCUSDT", "pair": "BTC/USDT",
                "base_asset": "BTC", "quote_asset": "USDT", "flags": {}, "volume_24h": None}
        spot = {"exchange": "binance", "pairs": [pair], "summary": {"tradable_spot_pair_count": 1}, "source": {}}
        future = futures.build_payload("binance", [])
        for no_push in (False, True):
            with self.subTest(no_push=no_push), tempfile.TemporaryDirectory() as temp:
                out = Path(temp)
                args = argparse.Namespace(exchanges=["binance"], timeout_seconds=1, indent=2,
                                          output=str(out / "spot_universe_combined.json"), no_push=no_push)
                def publish(generated_at):
                    self.assertTrue((out / "fut_universe_binance.json").exists())
                    self.assertTrue((out / "spot_universe_combined.json").exists())
                with (
                    patch.object(spot_universe, "parse_args", return_value=args),
                    patch.object(spot_universe, "clean_output_dir"),
                    patch.object(spot_universe, "today_output_dir", return_value=out),
                    patch.object(spot_universe, "fetch_requested_exchanges", AsyncMock(return_value=[spot])),
                    patch.object(spot_universe, "fetch_futures_universes", AsyncMock(return_value=[future])) as fetch,
                    patch.object(spot_universe, "auto_commit", side_effect=publish) as commit,
                ):
                    self.assertEqual(await spot_universe.async_main(), 0)
                fetch.assert_awaited_once_with(["binance"], 1)
                self.assertEqual(commit.call_count, int(not no_push))
                self.assertFalse((out / "fut_universe_mexc.json").exists())
                self.assertEqual(json.loads((out / "fut_universe_binance.json").read_text())["collection_status"], "ok")

    async def test_spot_outage_does_not_discard_collected_futures(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            combined = out / "spot_universe_combined.json"
            combined.write_text('{"generated_at":"previous"}')
            args = argparse.Namespace(exchanges=["binance"], timeout_seconds=1, indent=2,
                                      output=str(combined), no_push=False)
            with (
                patch.object(spot_universe, "parse_args", return_value=args),
                patch.object(spot_universe, "clean_output_dir"),
                patch.object(spot_universe, "today_output_dir", return_value=out),
                patch.object(spot_universe, "fetch_requested_exchanges", AsyncMock(side_effect=TimeoutError("spot offline"))),
                patch.object(spot_universe, "fetch_futures_universes", AsyncMock(return_value=[futures.build_payload("binance", [])])),
                patch.object(spot_universe, "auto_commit") as commit,
            ):
                self.assertEqual(await spot_universe.async_main(), 1)
            commit.assert_called_once()
            self.assertTrue((out / "fut_universe_binance.json").exists())
            self.assertEqual(json.loads(combined.read_text())["generated_at"], "previous")


class OutputPublishingTests(unittest.TestCase):
    def test_auto_commit_pushes_new_futures_files_without_unrelated_staged_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            remote = Path(temp) / "remote.git"
            root.mkdir()
            def git(*args, cwd=root):
                return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout
            git("init", "--bare", str(remote))
            git("init", "-b", "main")
            git("config", "user.name", "Universe Test")
            git("config", "user.email", "test@example.invalid")
            git("commit", "--allow-empty", "-m", "initial")
            git("remote", "add", "origin", str(remote))
            git("push", "-u", "origin", "main")
            (root / "unrelated.txt").write_text("keep staged\n")
            git("add", "unrelated.txt")
            out = root / "output"
            futures.write_universes([futures.build_payload("binance", []),
                                     futures.build_payload("okx", [])], out)
            with patch.object(spot_universe, "__file__", str(root / "crypto_universe/spot_universe.py")):
                spot_universe.auto_commit("2026-09-25T12:00:00Z")
            published = git("ls-tree", "-r", "--name-only", "main", cwd=remote).splitlines()
            self.assertEqual(published, ["output/fut_universe_binance.json", "output/fut_universe_okx.json"])
            self.assertEqual(git("diff", "--cached", "--name-only").strip(), "unrelated.txt")


if __name__ == "__main__":
    unittest.main()
