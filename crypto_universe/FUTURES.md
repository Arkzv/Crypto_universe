# Futures availability

`universe.html` reads the active spot venues from
`output/spot_universe_combined.json` and loads
`output/fut_universe_<exchange>.json` for each exchange in that file's scope.
The two exchange columns do not depend on trading volume. Multiple active
contracts for one pair appear as a single exchange name.

Pairs match by normalized base **and** quote currency. USD, USDT, and USDC remain
distinct; multiplier contracts such as `1000PEPE/USDT` are not treated as
`PEPE/USDT`. KuCoin's native `XBT` asset is normalized to `BTC`, while its
contract symbols remain unchanged. Futures availability is independent of
whether the same exchange lists that pair on spot.

The inventories contain perpetual and dated futures where exposed by the
public APIs below; spot instruments and options are excluded. Coinbase coverage
is the International Exchange perpetual inventory. Exchange status describes
the public market, not account-specific or regional eligibility.

| Exchange | Markets queried | Active status | API reference |
| --- | --- | --- | --- |
| Binance | USD-M and coin-M | `status` or `contractStatus` = `TRADING` | [Exchange information](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Exchange-Information) |
| Bitget | USDT, USDC, coin futures | `symbolStatus` = `normal` or `restrictedAPI` | [Contract config](https://www.bitget.com/docs/catalog/classic-contract-market/classic-contract-market) |
| BitMart | Contract inventory | `status` = `Trading` | [Contract details](https://developer-pro.bitmart.com/en/futuresv2/#get-contract-details) |
| Bybit | Linear and inverse, all cursor pages | `status` = `Trading`, not pre-listing | [Instruments info](https://bybit-exchange.github.io/docs/v5/market/instrument) |
| Coinbase | International perpetuals | `type` = `PERP`, `trading_state` = `TRADING` | [Instruments](https://docs.cdp.coinbase.com/api-reference/international-exchange-api/rest-api/instruments/list-instruments) |
| CoinW | Perpetual inventory | `status` = `online` | [Instrument info](https://www.coinw.com/api-doc/en/futures-trading/market/get-instrument-information) |
| Crypto.com | Perpetual swaps and futures | `tradable` = true | [Instruments](https://exchange-docs.crypto.com/exchange/v1/rest-ws/index.html#public-get-instruments) |
| Gate | USDT, BTC, USD1 perpetuals; USDT delivery, all offset pages | `status` = `trading`, no delisting or pre-market; delivery contracts must be unexpired and not delisting | [Contracts](https://www.gate.com/docs/developers/apiv4/en/futures/) |
| HTX | Linear swaps/futures, inverse swaps, coin delivery | `contract_status` = 1 | [Contract info](https://huobiapi.github.io/docs/usdt_swap/v1/en/#get-contract-info) |
| KuCoin | Active futures inventory | `status` = `Open`, normal market stage | [Symbols](https://www.kucoin.com/docs-new/rest/futures-trading/market-data/get-all-symbols) |
| MEXC | Contract inventory | `state` = 0, `type` = 1, not pre-market | [Contract detail](https://www.mexc.com/api-docs/futures/market-endpoints/get-contract-info) |
| OKX | Swaps and dated futures | `state` = `live` | [Instruments](https://www.okx.com/docs-v5/en/#public-data-rest-api-get-instruments) |
| Upbit | No public futures inventory configured | `unsupported` | [Public API scope](https://global-docs.upbit.com/reference/api-overview) |

Each normalized contract has `flags.is_active` for the exchange list and
`flags.is_tradable` for API trading. MEXC's existing API permission requirement
and funding-rate output are retained. Bitget's `restrictedAPI` status can be
active on the exchange while disallowing API trading. Missing or unrecognized
statuses do not qualify as active.

Each snapshot includes its timestamp and a `collection_status`:

- `ok`: every configured endpoint completed successfully.
- `partial`: some contracts were collected but an endpoint or row failed.
- `error`: collection failed without usable contracts.
- `unsupported`: no public futures inventory is configured for that exchange.

Failures overwrite the current snapshot with an explicit failure record rather
than silently presenting an older successful snapshot as current. Successful
contracts in a partial snapshot remain visible. Missing, malformed, and failed
files show incomplete coverage in the explorer; `Unknown` or `+ ?` indicates
that absence cannot be confirmed. A dash means no active exact-pair match among
the covered futures markets. Snapshot dates are shown separately from the spot
timestamp.

The main collector uses the same `--exchanges` scope for spot and futures,
writes all futures files before publishing, and stages/commits only `output/`.
If spot collection fails, the collected futures are still saved and published;
existing spot snapshots retain their original timestamps and the command returns
a nonzero status.
The futures-only command writes locally and does not run Git. Both commands
return a nonzero status if any requested futures inventory is incomplete.
