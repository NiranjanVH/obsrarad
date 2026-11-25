# Order block scanner

The scanner reads Binance spot/futures market data and sends generated signals
to Telegram.

## GitHub Actions

The scanner workflow runs only when started manually from the repository's
**Actions** tab.

The `Binance API connectivity` workflow probes Binance's official public Spot
and USD-M Futures endpoints from a GitHub-hosted runner. It does not send
Telegram messages or require Telegram secrets. The Spot scanner uses Binance's
documented public market-data host; USD-M Futures uses Binance's Futures API.

Before running it, add these repository secrets under **Settings → Secrets and
variables → Actions → New repository secret**:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

The workflow passes these values to the program as environment variables. Do
not put Telegram credentials in source files or commit them to the repository.

To run locally, install the packages in `requirements.txt`, set the same two
environment variables, then run `python obr.py`.
