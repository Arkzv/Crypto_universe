from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from crypto_universe import withdrawal_fee_bybit as bybit
from crypto_universe import withdrawal_fee_mexc as mexc


class WithdrawalFeeTimingTests(unittest.IsolatedAsyncioTestCase):
    async def test_both_collectors_sign_after_waiting_for_a_worker(self):
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
        release_worker = threading.Event()
        occupied = loop.run_in_executor(None, release_worker.wait)
        expected_ms = "1700000006250"
        sent = []

        def urlopen(request, timeout):
            url = urlsplit(request.full_url)
            sent.append(url.hostname)
            if url.hostname == "api.bybit.com":
                self.assertEqual(request.get_header("X-bapi-timestamp"), expected_ms)
                self.assertEqual(request.get_header("X-bapi-recv-window"), "5000")
                expected = hmac.digest(b"secret", (expected_ms + "key5000").encode(), hashlib.sha256)
                self.assertEqual(request.get_header("X-bapi-sign"), expected.hex())
                return BytesIO(b'{"retCode":0,"result":{"rows":[]}}')
            self.assertEqual(url.hostname, "api.mexc.com")
            query = parse_qs(url.query)
            self.assertEqual(query["timestamp"], [expected_ms])
            expected = hmac.digest(b"secret", ("timestamp=" + expected_ms).encode(), hashlib.sha256)
            self.assertEqual(query["signature"], [expected.hex()])
            return BytesIO(b'[]')

        with (
            patch.dict(os.environ, {bybit.ENV_VAR: "key:secret", mexc.ENV_VAR: "key:secret"}),
            patch("time.time", return_value=1_700_000_000) as clock,
            patch("urllib.request.urlopen", side_effect=urlopen),
        ):
            fees = asyncio.gather(bybit.fetch_withdrawal_fees(), mexc.fetch_withdrawal_fees())
            try:
                # Let both collectors reach to_thread while the sole worker is occupied.
                await asyncio.sleep(0)
                clock.return_value += 6.25
            finally:
                release_worker.set()
            await occupied
            await fees
        self.assertCountEqual(sent, ["api.bybit.com", "api.mexc.com"])

    async def test_bybit_retries_timestamp_error_with_new_timestamp_and_signature(self):
        timestamps = []
        signatures = []
        with (
            patch.dict(os.environ, {bybit.ENV_VAR: "key:secret"}),
            patch("time.time", return_value=1_700_000_000) as clock,
            self.assertLogs(bybit.logger, level="WARNING") as log,
        ):
            def urlopen(request, timeout):
                timestamps.append(request.get_header("X-bapi-timestamp"))
                signatures.append(request.get_header("X-bapi-sign"))
                if len(timestamps) == 1:
                    clock.return_value += 6.25
                    return BytesIO(b'{"retCode":10002,"retMsg":"Request expired"}')
                return BytesIO(b'{"retCode":0,"result":{"rows":[]}}')

            with patch("urllib.request.urlopen", side_effect=urlopen):
                result = await bybit.fetch_withdrawal_fees()
        self.assertEqual(result["currencies"], [])
        self.assertEqual(timestamps, ["1700000000000", "1700000006250"])
        self.assertNotEqual(signatures[0], signatures[1])
        self.assertRegex(log.output[0], r"attempt 1/2, initial worker wait \d+ ms, HTTP duration \d+ ms")

    async def test_bybit_reports_second_timestamp_error_and_other_errors_immediately(self):
        for code, expected_calls in ((10002, 2), (10003, 1)):
            with (
                self.subTest(code=code),
                patch.dict(os.environ, {bybit.ENV_VAR: "key:secret"}),
                patch.object(bybit, "fetch_json_sync", return_value={"retCode": code, "retMsg": "test"}) as get,
                patch.object(bybit.logger, "warning"),
                self.assertRaisesRegex(RuntimeError, f"retCode={code} retMsg=test"),
            ):
                await bybit.fetch_withdrawal_fees()
            self.assertEqual(get.call_count, expected_calls)
