# Bybit Withdrawal Fees — API Key Requirement

## Why an API key is needed

Unlike KuCoin (which exposes currency/chain info on a public endpoint), Bybit's
coin-info endpoint (`GET /v5/asset/coin/query-info`) **requires authentication**.
There is no public alternative that returns withdrawal fee data.

The key only needs **read-only** permissions — no trading, no withdrawals.

## Setup

1. Create a **read-only** API key on Bybit (no trading/withdrawal permissions).
2. Set the environment variable before running the tool:

```bash
export CRYPTO_UNIVERSE_BYBIT_RO="your_api_key:your_api_secret"
```

The format is `key:secret`, separated by a colon.

## Behaviour when the key is missing

If `CRYPTO_UNIVERSE_BYBIT_RO` is not set (or empty), the tool prints a warning
to the console and **skips** Bybit withdrawal fees. The spot universe fetch
proceeds normally — only the withdrawal-fee JSON file is omitted.
