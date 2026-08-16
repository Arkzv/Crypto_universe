from __future__ import annotations

from io import BytesIO
import unittest
from unittest.mock import AsyncMock, patch
from urllib.error import HTTPError

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
                "fetch_json",
                new=AsyncMock(side_effect=error),
            ),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                r"MEXC API key is invalid or expired.*CRYPTO_UNIVERSE_MEXC_RO",
            ):
                await withdrawal_fee_mexc.fetch_withdrawal_fees(timeout_seconds=1.0)


if __name__ == "__main__":
    unittest.main()
