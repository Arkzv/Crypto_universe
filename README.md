# Crypto Universe

Daily overview of tradable spot crypto pairs and their 24h volume distribution across major exchanges.

- [Volume Distribution Explorer](https://arkzv.github.io/Crypto_universe/universe.html) — GUI Interactive table with sorting and filtering
- [Funding Rate Explorer](https://arkzv.github.io/Crypto_universe/funding_universe.html) — GUI overview of futures funding rates by exchange
- [Report (Markdown)](output/README.md) — Plain text volume distribution report
- [Combined Data (JSON)](output/spot_universe_combined.json) — Machine-readable structured data
- [Futures inventories](crypto_universe/FUTURES.md) — Active contract availability across the exchanges in scope




Exchanges: Binance, Bitfinex, Bitget, Bybit, Coinbase, CoinW, Crypto.com, Gate, HTX, KuCoin, MEXC, OKX, Upbit

Bitfinex spot and perpetual inventories use its public V2 API, including its
currency mappings (`UST` → `USDT`, `UDC` → `USDC`, `DSH` → `DASH`). Paper-trading
pairs are excluded before mapping currencies. Pairs must be in the current
exchange/derivatives configuration and the platform must be operative to count
as active. Native symbols are retained for trading links. Bitfinex reports 24h
base volume; quote volume is estimated as base volume × last price and marked
with `quote_volume_method` in the spot snapshot. Zero-volume markets remain in
the universe. See [Bitfinex configurations](https://docs.bitfinex.com/reference/rest-public-conf),
[platform status](https://docs.bitfinex.com/reference/rest-public-platform-status),
and [tickers](https://docs.bitfinex.com/reference/rest-public-tickers).

Run `python -m crypto_universe --exchanges bitfinex --no-push` to collect Bitfinex
spot and futures snapshots locally, or `python -m crypto_universe.spot_universe_bitfinex`
for spot only (`crypto-universe-bitfinex` after installation). The default full
run includes Bitfinex and publishes its snapshots through the existing output
commit/push flow.

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
    - Spot venues in the volume distribution and a futures exchange list for each pair, including zero-volume markets
    - Spot exchange filters: include pairs listed on any or all selected exchanges, and exclude pairs listed on any excluded exchange
    - Futures exchange filter: exclude spot pairs with active futures on any selected exchange
- Crypto withdrawal fees
- Historical exchange and trading pair specific traded volume

In `universe.html`, select exchanges under **Include spot** and choose **any** or
**all**. Pairs may also be listed on other exchanges. Use **Exclude spot** to hide
pairs listed on any of those exchanges; for example, include Bitget and exclude
Binance. An empty Include spot selection allows all pairs, so exclusion-only
searches also work. Selecting an exchange in one spot group clears it from the other.

Use **Exclude futures** to hide spot pairs with active futures on any selected
exchange; selecting Binance and KuCoin excludes pairs with futures on either.
This selection is independent of the spot groups, so you can include an exchange's
spot pairs while excluding its futures. Futures match the same base and quote
currency, including perpetual and dated contracts. Only known active listings
are excluded; unknown availability remains visible with the existing incomplete
coverage indicator.

**Reset exchange filters** clears all three groups and restores **any**, while keeping
the pair search, quote, and primary exchange filters.

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
