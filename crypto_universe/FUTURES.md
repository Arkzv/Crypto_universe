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

OKX's USD-margined families use `USD_UM` and `USD_UM_XPERP` product labels;
their quote currency is normalized to `USD`. Native symbols and settlement
currencies are retained, and `USDC` families remain distinct. See the
[OKX instrument-family definitions](https://www.okx.com/docs-v5/log_en/).

Bitfinex's perpetual assets are mapped to their underlying currencies and API
aliases (e.g. `tBTCF0:USTF0` → `BTC/USDT`), preserving native contract symbols.
Paper-trading assets (`TEST*`) are excluded before applying the alias mappings.
The `F0` suffix is removed when a new contract is not yet in the
underlying map. Fiat USD and Tether USDT remain distinct.

The inventories contain perpetual and dated futures where exposed by the
public APIs below; spot instruments and options are excluded. Coinbase coverage
is the Global Derivatives perpetual and dated futures inventory. Exchange status describes
the public market, not account-specific or regional eligibility.

Coinbase moved international derivatives to its Deribit-powered gateway on
October 1, 2026. The collector uses `drb.coinbase.com` with `currency=any`,
`kind=future`, and `expired=false`, and reads the JSON-RPC `result` list,
replacing the old INTX instruments endpoint. Native instrument names
such as `BTC_USDC-PERPETUAL` are preserved, and base, quote, and settlement
currencies come from the instrument fields. See Coinbase's
[migration guide](https://docs.cdp.coinbase.com/coinbase-app/advanced-trade-apis/guides/derivatives/technical).

| Exchange | Markets queried | Active status | API reference |
| --- | --- | --- | --- |
| Binance | USD-M and coin-M | `status` or `contractStatus` = `TRADING` | [Exchange information](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Exchange-Information) |
| Bitfinex | Public perpetual inventory | Listed in `pub:list:pair:futures`, excluding paper trading; platform status = 1 | [Configurations](https://docs.bitfinex.com/reference/rest-public-conf), [Platform status](https://docs.bitfinex.com/reference/rest-public-platform-status) |
| Bitget | USDT, USDC, coin futures | `symbolStatus` = `normal` or `restrictedAPI` | [Contract config](https://www.bitget.com/docs/catalog/classic-contract-market/classic-contract-market) |
| Bybit | Linear and inverse, all cursor pages | `status` = `Trading`, not pre-listing | [Instruments info](https://bybit-exchange.github.io/docs/v5/market/instrument) |
| Coinbase | Global perpetuals and dated futures, all currencies | `kind` = `future`, `is_active` = true, `state` = `open` when present, unexpired | [Instruments](https://docs.cdp.coinbase.com/api-reference/market-data/public-get_instruments) |
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

An incomplete exchange affects every pair whose absence cannot be confirmed:
`Unknown` means no active match was found in the successful snapshots, while
`+ ?` follows known exchange names when another inventory is incomplete. Upbit's
`unsupported` status is expected and does not cause these unknown indicators.

The main collector uses the same `--exchanges` scope for spot and futures,
writes all futures files before publishing, and stages/commits only `output/`.
If spot collection fails, the collected futures are still saved and published;
existing spot snapshots retain their original timestamps and the command returns
a nonzero status.
The futures-only command writes locally and does not run Git. Both commands
return a nonzero status if any requested futures inventory is incomplete.
