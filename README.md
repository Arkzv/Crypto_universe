# Crypto Universe

Daily overview of tradable spot crypto pairs and their 24h volume distribution across major exchanges.

- [Volume Distribution Explorer](https://arkzv.github.io/Crypto_universe/universe.html) — GUI Interactive table with sorting and filtering
- [Funding Rate Explorer](https://arkzv.github.io/Crypto_universe/funding_universe.html) — GUI overview of futures funding rates by exchange
- [Report (Markdown)](output/README.md) — Plain text volume distribution report
- [Combined Data (JSON)](output/spot_universe_combined.json) — Machine-readable structured data
- [Futures inventories](crypto_universe/FUTURES.md) — Active contract availability across the exchanges in scope




Exchanges: Binance, Bitget, BitMart, Bybit, Coinbase, CoinW, Crypto.com, Gate, HTX, KuCoin, MEXC, OKX, Upbit

Bitget uses the V3 spot API. For Reality stock tokens (`isReality=yes`, such as
`rILMN`), the displayed volume comes from `platformTurnover24h`, which measures
trading on Bitget. The general stock-market turnover can be billions even when
Bitget's platform volume is zero. Other Bitget spot pairs use `turnover24h`.
The collector preserves zero platform volume and rejects missing platform
turnover instead of substituting stock-market volume. Platform base volume is
unavailable for Reality tokens and is stored as `null`.
See [Bitget's volume field definitions](https://www.bitget.com/docs/catalog/market/market-data).

### Features
- Spot crypto pairs
    - Conversion of traded volumes to USDT
    - Explicit spot and futures exchange lists for each pair, including zero-volume markets
- Crypto withdrawal fees
- Historical exchange and trading pair specific traded volume

Run `python -m crypto_universe` to refresh spot data, futures inventories, and
withdrawal fees. The collector saves `output/fut_universe_<exchange>.json` for
each requested exchange and includes those files in its automatic output commit
and GitHub push. `--exchanges binance bybit okx` limits both spot and futures to
those exchanges. Add `--no-push` to write locally without committing or pushing.

Run `python -m crypto_universe.fut_universe` to refresh only futures inventories
without committing or pushing. This command also accepts `--exchanges` and
`--output-dir`. Collection failures are saved explicitly and return a nonzero
exit status; the explorer marks the affected coverage as incomplete.

Checks: `python -m unittest discover -s tests` and `node --test tests/test_universe.cjs`.
