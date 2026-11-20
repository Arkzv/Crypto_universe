from __future__ import annotations

from io import BytesIO
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from crypto_universe import withdrawal_fee_mexc


class MexcWithdrawalFeeTests(unittest.IsolatedAsyncioTestCase):
    async def test_expired_api_key_has_explicit_error_message(self) -> None:
        error = HTTPError(
            withdrawal_fee_mexc.COIN_CONFIG_URL,
            400,
            "Bad Request",
            hdrs=None,
            fp=BytesIO(b'{"code":10072,"msg":"Api key info invalid"}'),
        )

        with (
            patch.object(
                withdrawal_fee_mexc,
                "_get_api_key",
                return_value=("api-key", "api-secret"),
            ),
            patch.object(
                withdrawal_fee_mexc,
                "fetch_json_sync",
                side_effect=error,
            ) as fetch,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                r"MEXC API key is invalid or expired.*CRYPTO_UNIVERSE_MEXC_RO",
            ):
                await withdrawal_fee_mexc.fetch_withdrawal_fees(timeout_seconds=1.0)
            fetch.assert_called_once()

    async def test_http_timestamp_error_retries_with_a_fresh_signature(self) -> None:
        queries = []

        def urlopen(request, timeout):
            queries.append(parse_qs(urlsplit(request.full_url).query))
            if len(queries) == 1:
                clock.return_value += 6.25
                body = BytesIO(b'{"code":700003,"msg":"Timestamp outside recvWindow"}')
                raise HTTPError(withdrawal_fee_mexc.COIN_CONFIG_URL, 400, "Bad Request", {}, body)
            return BytesIO(b'[]')

        with (
            patch.object(withdrawal_fee_mexc, "_get_api_key", return_value=("key", "secret")),
            patch("time.time", return_value=1_700_000_000) as clock,
            patch("urllib.request.urlopen", side_effect=urlopen),
            self.assertLogs(withdrawal_fee_mexc.logger, level="WARNING") as log,
        ):
            result = await withdrawal_fee_mexc.fetch_withdrawal_fees()
        self.assertEqual(result["currencies"], [])
        self.assertEqual([q["timestamp"] for q in queries], [["1700000000000"], ["1700000006250"]])
        self.assertNotEqual(queries[0]["signature"], queries[1]["signature"])
        self.assertRegex(log.output[0], r"attempt 1/2, initial worker wait \d+ ms, HTTP duration \d+ ms")

    async def test_repeated_timestamp_error_in_json_stops_after_second_attempt(self) -> None:
        with (
            patch.object(withdrawal_fee_mexc, "_get_api_key", return_value=("key", "secret")),
            patch.object(withdrawal_fee_mexc, "fetch_json_sync",
                         return_value={"code": 700003, "msg": "outside window"}) as fetch,
            self.assertLogs(withdrawal_fee_mexc.logger, level="WARNING"),
            self.assertRaisesRegex(RuntimeError, "code=700003 msg=outside window"),
        ):
            await withdrawal_fee_mexc.fetch_withdrawal_fees()
        self.assertEqual(fetch.call_count, 2)


if __name__ == "__main__":
    unittest.main()
