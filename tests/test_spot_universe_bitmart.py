from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from crypto_universe import spot_universe_bitmart


class BitmartConnectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_exchange_universe_backfills_missing_bulk_ticker(self) -> None:
        symbols_payload = {
            "code": 1000,
            "message": "OK",
            "data": {
                "symbols": [
                    {
                        "symbol": "WHITEWHALE_USDT",
                        "base_currency": "WHITEWHALE",
                        "quote_currency": "USDT",
                        "trade_status": "trading",
                    },
                ],
            },
        }
        bulk_tickers_payload = {
            "code": 1000,
            "message": "success",
            "data": [],
        }
        single_ticker_payload = {
            "code": 1000,
            "message": "success",
            "data": {
                "symbol": "WHITEWHALE_USDT",
                "last": "0.00709",
                "v_24h": "112404867.2",
                "qv_24h": "804558.03687",
            },
        }

        async def fake_fetch_json(url: str, timeout_seconds: float):
            del timeout_seconds
            if url == spot_universe_bitmart.SYMBOLS_URL:
                return symbols_payload
            if url == spot_universe_bitmart.TICKERS_URL:
                return bulk_tickers_payload
            if url == spot_universe_bitmart.build_ticker_url("WHITEWHALE_USDT"):
                return single_ticker_payload
            raise AssertionError(f"unexpected URL {url}")

        with patch.object(
            spot_universe_bitmart,
            "fetch_json",
            new=AsyncMock(side_effect=fake_fetch_json),
        ):
            payload = await spot_universe_bitmart.fetch_exchange_universe(timeout_seconds=1.0)

        self.assertEqual(payload["summary"]["ticker_24hr_bulk_rows"], 0)
        self.assertEqual(payload["summary"]["ticker_24hr_backfilled_rows"], 1)
        self.assertEqual(payload["summary"]["pairs_missing_from_bulk_ticker_count"], 1)

        pair = payload["pairs"][0]
        self.assertEqual(pair["pair"], "WHITEWHALE/USDT")
        self.assertEqual(pair["volume_24h"]["quote_volume"], "804558.03687")
        self.assertEqual(pair["volume_24h"]["base_volume"], "112404867.2")
        self.assertEqual(
            payload["source"]["ticker_24hr_symbol_url_template"],
            "https://api-cloud.bitmart.com/spot/quotation/v3/ticker?symbol={urlencoded_symbol}",
        )

    def test_build_bitmart_single_ticker_volume(self) -> None:
        payload = {
            "code": 1000,
            "message": "success",
            "data": {
                "symbol": "WHITEWHALE_USDT",
                "last": "0.00709",
                "v_24h": "112404867.2",
                "qv_24h": "804558.03687",
            },
        }

        volume = spot_universe_bitmart.build_bitmart_single_ticker_volume(payload)

        self.assertEqual(
            volume,
            {
                "symbol": "WHITEWHALE_USDT",
                "last_price": "0.00709",
                "base_volume": "112404867.2",
                "quote_volume": "804558.03687",
                "open_time_ms": None,
                "close_time_ms": None,
                "trade_count": None,
            },
        )

    def test_build_ticker_url_percent_encodes_symbol(self) -> None:
        url = spot_universe_bitmart.build_ticker_url("踏马的没房_USDT")
        self.assertIn("%E8%B8%8F", url)
        self.assertTrue(url.endswith("_USDT"))

    async def test_fetch_single_ticker_payload_retries_transient_error(self) -> None:
        fetch_json = AsyncMock(
            side_effect=[
                TimeoutError("temporary failure"),
                {"code": 1000, "message": "success", "data": {"symbol": "WHITEWHALE_USDT"}},
            ],
        )

        with (
            patch.object(spot_universe_bitmart, "fetch_json", new=fetch_json),
            patch("crypto_universe.spot_universe_bitmart.asyncio.sleep", new=AsyncMock()),
        ):
            payload = await spot_universe_bitmart.fetch_single_ticker_payload("WHITEWHALE_USDT", 1.0)

        self.assertEqual(payload["data"]["symbol"], "WHITEWHALE_USDT")
        self.assertEqual(fetch_json.await_count, 2)


if __name__ == "__main__":
    unittest.main()
