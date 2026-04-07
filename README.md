# crypto_universe

Independent daily spot-universe comparison project for crypto exchanges.

Current scope:

- normalized spot pair universe snapshots
- one script/module per exchange
- one central async combiner
- raw exchange-reported 24h volume attached to each exchange payload

Current exchanges:

- MEXC
- Binance
- Bybit

## Install

From this repo root:

```bash
python3 -m pip install -e ./crypto_universe
```

Or run directly from the project directory:

```bash
cd crypto_universe
python3 -m crypto_universe.spot_universe --exchanges mexc binance
```

## Layout

- `crypto_universe.spot_universe`
  Central async combiner. Fetches multiple exchanges concurrently and merges by normalized `BASE/QUOTE`.
- `crypto_universe.spot_universe_mexc`
  MEXC spot-universe fetcher.
- `crypto_universe.spot_universe_binance`
  Binance spot-universe fetcher.
- `crypto_universe.spot_universe_bybit`
  Bybit spot-universe fetcher.

## Run

Fetch one exchange:

```bash
python3 -m crypto_universe.spot_universe_mexc
python3 -m crypto_universe.spot_universe_binance
python3 -m crypto_universe.spot_universe_bybit
```

Fetch and combine multiple exchanges asynchronously:

```bash
python3 -m crypto_universe.spot_universe --exchanges mexc binance bybit
```

If installed, the same commands are available as:

```bash
crypto-universe-mexc
crypto-universe-binance
crypto-universe-bybit
crypto-universe --exchanges mexc binance bybit
```

## Output Contract

Each exchange module emits:

- `exchange`
- `generated_at`
- `summary`
- `pairs`

Each pair entry includes:

- `pair`
- `base_asset`
- `quote_asset`
- `symbol`
- `flags`
- `volume_24h`

The central combiner emits:

- `requested_exchanges`
- `summary`
- `sources`
- `pairs`

Each combined pair entry includes:

- `pair`
- `base_asset`
- `quote_asset`
- `venues`
- `venue_count`
- `by_exchange`

## Output Files

The output directory is cleaned at every run. Files are placed in a date subdirectory:

```
output/
  YYYY.MM.DD/
    spot_universe_mexc.json
    spot_universe_binance.json
    spot_universe_bybit.json
    spot_universe_combined.json
```

Override the combined output path with `--output`.

## Notes

- Pair matching is by normalized `baseAsset/quoteAsset`, not raw symbol string.
- Volume fields are preserved as reported by each exchange.
- Exchange-specific metadata stays under each venue's `flags` object.
