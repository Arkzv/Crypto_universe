from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from crypto_universe import spot_universe, spot_universe_bitget


class BitgetConnectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_platform_volume_reaches_cockpit_without_stock_market_turnover(self) -> None:
        # Reproduce the inflated September 20 snapshot with platform turnover
        # observed in the V3 API on September 21. These are separate measures.
        examples = [
            ("rILMN", "239.64", "50283383.1419", "12041073464.2361", "0"),
            ("rCVX", "209.45", "43805772.2145", "9192830791.9669", "999.9944"),
            ("rP", "103.63", "74133429.1894", "7669696880.7827", "0"),
            ("BTC", "81413.15", "1911.21376", "154321683.626604", ""),
        ]
        symbols = []
        tickers = []
        for base, price, volume, turnover, platform_turnover in examples:
            symbol = base.upper() + "USDT"
            symbols.append({
                "symbol": symbol,
                "category": "SPOT",
                "baseCoin": base,
                "quoteCoin": "USDT",
                "status": "online",
                "isReality": "yes" if base.startswith("r") else "no",
                "symbolType": "stock" if base.startswith("r") else "crypto",
            })
            tickers.append({
                "symbol": symbol,
                "category": "SPOT",
                "lastPrice": price,
                "volume24h": volume,
                "turnover24h": turnover,
                "platformTurnover24h": platform_turnover,
                "ts": "1789975225481",
            })
        symbols.append({**symbols[0], "symbol": "OFFLINEUSDT", "status": "offline"})
        responses = {
            "https://api.bitget.com/api/v3/market/instruments?category=SPOT": {
                "code": "00000", "data": symbols,
            },
            "https://api.bitget.com/api/v3/market/tickers?category=SPOT": {
                "code": "00000", "data": tickers,
            },
        }

        async def fake_fetch_json(url: str, timeout_seconds: float):
            self.assertEqual(timeout_seconds, 1.0)
            return responses[url]

        with patch.object(spot_universe_bitget, "fetch_json", new=AsyncMock(side_effect=fake_fetch_json)):
            payload = await spot_universe_bitget.fetch_exchange_universe(1.0)

        self.assertEqual(payload["summary"]["tradable_spot_pair_count"], 4)
        self.assertEqual(payload["summary"]["skipped_symbol_rows"], 1)
        pairs = {pair["pair"]: pair for pair in payload["pairs"]}
        for base, price, base_volume, turnover, platform_turnover in examples:
            pair = pairs[base.upper() + "/USDT"]
            volume = pair["volume_24h"]
            self.assertEqual(volume["last_price"], price)
            self.assertEqual(volume["close_time_ms"], 1789975225481)
            if base.startswith("r"):
                self.assertEqual(pair["flags"]["isReality"], "YES")
                self.assertEqual(volume["quote_volume"], platform_turnover)
                self.assertIsNone(volume["base_volume"])
            else:
                self.assertEqual(volume["quote_volume"], turnover)
                self.assertEqual(volume["base_volume"], base_volume)

        combined = spot_universe.build_combined_payload([payload])
        spot_universe.enrich_with_usdt_volume(combined, spot_universe.build_usdt_rates(combined))
        cockpit = spot_universe.build_combined_json_payload(combined)
        displayed = {pair["pair"]: pair for pair in cockpit["pairs"]}
        for pair, expected in {"RILMN/USDT": 0, "RCVX/USDT": 999.99, "RP/USDT": 0}.items():
            self.assertEqual(displayed[pair]["total_usdt_volume"], expected)
            self.assertEqual(displayed[pair]["venues"][0]["usdt_volume"], expected)
        self.assertEqual(displayed["BTC/USDT"]["total_usdt_volume"], 154321683.63)
        ranked = sorted(cockpit["pairs"], key=lambda pair: pair["total_usdt_volume"], reverse=True)
        self.assertEqual([pair["pair"] for pair in ranked[:2]], ["BTC/USDT", "RCVX/USDT"])
        report = spot_universe.build_volume_report(combined)
        self.assertIn("RILMN/USDT | 1 | 0 | bitget 0 USDT 0%", report)

    def test_regular_crypto_uses_quote_turnover_without_price_multiplication(self) -> None:
        # A crypto symbol starting with R is not necessarily a Reality token.
        ticker = {
            "symbol": "RAYUSDT",
            "lastPrice": "2.5",
            "volume24h": "4000000",
            "turnover24h": "10000000",
        }
        for optional_fields in ({}, {"platformTurnover24h": ""}, {"platformTurnover24h": None}):
            with self.subTest(optional_fields=optional_fields):
                volumes = spot_universe_bitget.build_bitget_volume_by_symbol(
                    {"code": "00000", "data": [{**ticker, **optional_fields}]},
                    reality_symbols=set(),
                )
                self.assertEqual(volumes["RAYUSDT"]["quote_volume"], "10000000")
                self.assertEqual(volumes["RAYUSDT"]["base_volume"], "4000000")
                self.assertEqual(volumes["RAYUSDT"]["last_price"], "2.5")

    def test_zero_platform_turnover_is_not_replaced_by_market_turnover(self) -> None:
        for zero in ("0", "0.0000", 0):
            with self.subTest(zero=zero):
                volumes = spot_universe_bitget.build_bitget_volume_by_symbol(
                    {"code": "00000", "data": [{
                        "symbol": "RILMNUSDT",
                        "turnover24h": "12041073464.2361",
                        "platformTurnover24h": zero,
                    }]},
                    reality_symbols={"RILMNUSDT"},
                )
                self.assertEqual(volumes["RILMNUSDT"]["quote_volume"], str(zero))

    def test_missing_platform_turnover_cannot_silently_use_stock_market_volume(self) -> None:
        for optional_fields in ({}, {"platformTurnover24h": ""}, {"platformTurnover24h": None}):
            with self.subTest(optional_fields=optional_fields):
                with self.assertRaisesRegex(RuntimeError, "RILMNUSDT.*platformTurnover24h"):
                    spot_universe_bitget.build_bitget_volume_by_symbol(
                        {"code": "00000", "data": [{
                            "symbol": "RILMNUSDT",
                            "turnover24h": "12041073464.2361",
                            **optional_fields,
                        }]},
                        reality_symbols={"RILMNUSDT"},
                    )


if __name__ == "__main__":
    unittest.main()
