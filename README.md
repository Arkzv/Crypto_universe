# Crypto Universe

Daily overview of tradable spot crypto pairs and their 24h volume distribution across major exchanges.

- [Volume Distribution Explorer](https://arkzv.github.io/Crypto_universe/universe.html) — GUI Interactive table with sorting and filtering
- [Funding Rate Explorer](https://arkzv.github.io/Crypto_universe/funding_universe.html) — GUI overview of futures funding rates by exchange
- [Report (Markdown)](output/README.md) — Plain text volume distribution report
- [Combined Data (JSON)](output/spot_universe_combined.json) — Machine-readable structured data




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
- Crypto withdrawal fees
- Historical exchange and trading pair specific traded volume
