from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from crypto_universe import bitfinex, fut_universe, spot_universe, spot_universe_bitfinex as spot


ALIASES = [
    ["UST", "USDt"], ["UDC", "USDC"], ["DSH", "DASH"], ["IOT", "IOTA"],
    ["USTF0", "USDt"], ["EUTF0", "EURt"], ["TESTBTC", "BTC"], ["TESTUSD", "USD"],
    ["WBT", "WBTC"], ["WHBT", "WBT"],
]
UNDERLYINGS = [["BTCF0", "BTC"], ["USTF0", "UST"], ["DSHF0", "DSH"], ["EUTF0", "EUT"]]


def ticker(symbol, price=10, volume=2):
    return [symbol, 9, 1, 11, 1, 0, 0, price, volume, 12, 8]


def responses(*, operative=1):
    return {
        bitfinex.SPOT_CONFIG_URL: [[
            "BTCUSD", "BTCUST", "DSHUST", "IOTUST", "LONG:UDC", "BTCUST",
            "TESTBTC:TESTUSD", "BTCF0:USTF0", "WHBT:UST",
        ], ALIASES],
        bitfinex.TICKERS_URL: [
            ticker("tBTCUSD", 100, 2), ticker("tBTCUST", 100, 0), ticker("tDSHUST", 25, 4),
            ticker("tIOTUST", 0.5, 6), ticker("tWHBT:UST", 1, 2),
            ticker("tDELISTED:USD"), ticker("tBTCF0:USTF0"), ["fUSD", 0.01],
        ],
        bitfinex.PLATFORM_STATUS_URL: [operative],
        bitfinex.FUTURES_CONFIG_URL: [[
            "BTCF0:USTF0", "BTCF0:USTF0", "DSHF0:USTF0", "NEWF0:USTF0", "BTCF0:USDF0",
            "EUTF0:USTF0", "TESTBTCF0:TESTUSDTF0", "BTCUSD",
        ], ALIASES, UNDERLYINGS],
    }


def fetch_mock(data):
    async def fetch(url, timeout_seconds):
        result = data[url]
        if isinstance(result, Exception):
            raise result
        return result
    return AsyncMock(side_effect=fetch)


class BitfinexSpotTests(unittest.IsolatedAsyncioTestCase):
    async def test_active_inventory_aliases_and_volumes_reach_combined_output(self):
        fetch = fetch_mock(responses())
        with patch.object(spot, "fetch_json", fetch):
            payload = await spot.fetch_exchange_universe(3)
        self.assertEqual(fetch.await_count, 3)
        self.assertTrue(all(call.args[1] == 3 for call in fetch.await_args_list))
        pairs = {p["pair"]: p for p in payload["pairs"]}
        self.assertEqual(list(pairs), ["BTC/USD", "BTC/USDT", "DASH/USDT", "IOTA/USDT", "LONG/USDC", "WBT/USDT"])
        self.assertEqual(payload["summary"]["duplicate_pair_rows"], 1)
        self.assertEqual(payload["summary"]["skipped_symbol_rows"], 2)
        self.assertEqual(pairs["DASH/USDT"]["symbol"], "tDSHUST")
        self.assertEqual(pairs["BTC/USDT"]["volume_24h"]["quote_volume"], "0")
        self.assertIsNone(pairs["LONG/USDC"]["volume_24h"])
        self.assertEqual(pairs["WBT/USDT"]["symbol"], "tWHBT:UST")

        combined = spot_universe.build_combined_payload([payload])
        spot_universe.enrich_with_usdt_volume(combined, spot_universe.build_usdt_rates(combined))
        output = spot_universe.build_combined_json_payload(combined)
        displayed = {p["pair"]: p for p in output["pairs"]}
        self.assertEqual(output["exchanges"], ["bitfinex"])
        self.assertEqual(displayed["BTC/USD"]["total_usdt_volume"], 200)
        self.assertEqual(displayed["BTC/USDT"]["total_usdt_volume"], 0)
        self.assertEqual(displayed["DASH/USDT"]["total_usdt_volume"], 100)
        self.assertEqual(displayed["DASH/USDT"]["venues"][0]["symbol"], "tDSHUST")
        self.assertEqual(displayed["LONG/USDC"]["venue_count"], 1)

    async def test_maintenance_excludes_spot_markets_even_with_ticker_volume(self):
        with patch.object(spot, "fetch_json", fetch_mock(responses(operative=0))):
            payload = await spot.fetch_exchange_universe()
        self.assertEqual(payload["pairs"], [])
        self.assertEqual(payload["source"]["platform_status"], 0)

    async def test_invalid_api_responses_fail_instead_of_looking_empty(self):
        for url, invalid in [
            (bitfinex.SPOT_CONFIG_URL, ["error", 10020, "invalid configuration"]),
            (bitfinex.SPOT_CONFIG_URL, [[]]),
            (bitfinex.SPOT_CONFIG_URL, [[], [["UST", None]]]),
            (bitfinex.TICKERS_URL, ["error", 10020, "rate limit"]),
            (bitfinex.PLATFORM_STATUS_URL, []),
            (bitfinex.PLATFORM_STATUS_URL, [2]),
        ]:
            with self.subTest(url=url, response=invalid):
                data = {**responses(), url: invalid}
                with patch.object(spot, "fetch_json", fetch_mock(data)):
                    with self.assertRaises(ValueError):
                        await spot.fetch_exchange_universe()

    def test_estimated_volume_preserves_decimal_precision_zero_and_missing_values(self):
        examples = [
            ("0.12345678", "0.12345678", "0.0152415765279684"),
            (None, 0, "0"), (None, 2, None), (0, 2, None),
            (1, None, None), (1, -2, None), ("NaN", 2, None), (1, "Infinity", None),
        ]
        for price, volume, expected in examples:
            with self.subTest(price=price, volume=volume):
                result = spot.build_bitfinex_volume_by_symbol([ticker("tBTCUSD", price, volume)])["tBTCUSD"]
                self.assertEqual(result["quote_volume"], expected)
                self.assertEqual(result["quote_volume_method"], "base_volume_times_last_price")
                self.assertIsNone(result["close_time_ms"])

    def test_ticker_schema_allows_appended_fields_but_rejects_truncated_rows(self):
        result = spot.build_bitfinex_volume_by_symbol([ticker("tBTCUSD") + [1234567890000, None]])
        self.assertEqual(result["tBTCUSD"]["quote_volume"], "20")
        with self.assertRaises(ValueError):
            spot.build_bitfinex_volume_by_symbol([["tBTCUSD", 1]])


class BitfinexFuturesTests(unittest.IsolatedAsyncioTestCase):
    async def test_perpetuals_normalize_underlyings_aliases_and_native_symbols(self):
        with patch.object(fut_universe, "fetch_json", fetch_mock(responses())):
            payload = await fut_universe.fetch_exchange_universe("bitfinex", 3)
        self.assertEqual(payload["collection_status"], "ok")
        self.assertEqual(payload["summary"]["duplicate_symbol_rows"], 1)
        self.assertEqual(payload["summary"]["skipped_instrument_rows"], 2)
        pairs = {p["pair"]: p for p in payload["pairs"]}
        self.assertEqual(set(pairs), {"BTC/USDT", "BTC/USD", "DASH/USDT", "NEW/USDT", "EURT/USDT"})
        self.assertEqual(pairs["BTC/USDT"]["symbol"], "tBTCF0:USTF0")
        self.assertEqual(pairs["BTC/USDT"]["settle_asset"], "USDT")
        self.assertEqual(pairs["BTC/USDT"]["contract_type"], "PERPETUAL")
        self.assertTrue(all(p["flags"]["is_active"] and p["flags"]["is_tradable"] for p in pairs.values()))

    async def test_maintenance_keeps_inventory_but_marks_contracts_inactive(self):
        with patch.object(fut_universe, "fetch_json", fetch_mock(responses(operative=0))):
            payload = await fut_universe.fetch_exchange_universe("bitfinex")
        self.assertEqual(payload["collection_status"], "ok")
        self.assertGreater(len(payload["pairs"]), 0)
        self.assertEqual(payload["summary"]["active_futures_pair_count"], 0)
        self.assertEqual(payload["summary"]["tradable_futures_pair_count"], 0)
        self.assertTrue(all(p["flags"]["status"] == "MAINTENANCE" for p in payload["pairs"]))

    async def test_failed_configuration_and_unknown_platform_status_report_error(self):
        for url, failure in [
            (bitfinex.FUTURES_CONFIG_URL, TimeoutError("offline")),
            (bitfinex.FUTURES_CONFIG_URL, ["error", 10020, "unavailable"]),
            (bitfinex.PLATFORM_STATUS_URL, [None]),
            (bitfinex.PLATFORM_STATUS_URL, TimeoutError("status unavailable")),
        ]:
            with self.subTest(url=url, failure=failure):
                with patch.object(fut_universe, "fetch_json", fetch_mock({**responses(), url: failure})):
                    payload = await fut_universe.fetch_exchange_universe("bitfinex")
                self.assertEqual(payload["collection_status"], "error")
                self.assertTrue(payload["errors"])
                self.assertEqual(payload["pairs"], [])

    async def test_malformed_contract_keeps_valid_contracts_with_partial_status(self):
        data = responses()
        data[bitfinex.FUTURES_CONFIG_URL][0].extend(["BAD", None, "F0:USTF0"])
        with patch.object(fut_universe, "fetch_json", fetch_mock(data)):
            payload = await fut_universe.fetch_exchange_universe("bitfinex")
        self.assertEqual(payload["collection_status"], "partial")
        self.assertEqual(payload["summary"]["active_futures_pair_count"], 5)
        self.assertEqual(len(payload["errors"]), 3)

    async def test_empty_inventory_is_successful(self):
        data = {**responses(), bitfinex.FUTURES_CONFIG_URL: [[], ALIASES, UNDERLYINGS]}
        with patch.object(fut_universe, "fetch_json", fetch_mock(data)):
            payload = await fut_universe.fetch_exchange_universe("bitfinex")
        self.assertEqual(payload["collection_status"], "ok")
        self.assertEqual(payload["pairs"], [])


class BitfinexCollectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_registered_scope_collects_and_writes_before_publication(self):
        self.assertEqual(set(spot_universe.EXCHANGE_FETCHERS), set(fut_universe.EXCHANGES))
        with patch("sys.argv", ["crypto-universe"]):
            self.assertIn("bitfinex", spot_universe.parse_args().exchanges)
        for no_push in (True, False):
            with self.subTest(no_push=no_push), tempfile.TemporaryDirectory() as temp:
                out = Path(temp)
                args = argparse.Namespace(exchanges=[" Bitfinex ", "bitfinex"], timeout_seconds=1, indent=2,
                                          output=str(out / "spot_universe_combined.json"), no_push=no_push)
                def publish(_generated_at):
                    self.assertTrue((out / "spot_universe_bitfinex.json").is_file())
                    future = json.loads((out / "fut_universe_bitfinex.json").read_text())
                    self.assertEqual(future["collection_status"], "ok")
                    combined = json.loads((out / "spot_universe_combined.json").read_text())
                    self.assertEqual(combined["exchanges"], ["bitfinex"])
                    self.assertTrue(all(p["venue_count"] == 1 for p in combined["pairs"]))
                with (
                    patch.object(spot, "fetch_json", fetch_mock(responses())),
                    patch.object(fut_universe, "fetch_json", fetch_mock(responses())),
                    patch.object(spot_universe, "parse_args", return_value=args),
                    patch.object(spot_universe, "clean_output_dir"),
                    patch.object(spot_universe, "today_output_dir", return_value=out),
                    patch.object(spot_universe, "auto_commit", side_effect=publish) as commit,
                ):
                    self.assertEqual(await spot_universe.async_main(), 0)
                self.assertEqual(commit.call_count, int(not no_push))
                publish(None)
                self.assertTrue((out / "README.md").is_file())
                self.assertFalse((out / "fut_universe_mexc.json").exists())


if __name__ == "__main__":
    unittest.main()
