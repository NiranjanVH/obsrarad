# Order block scanner

The scanner reads Binance spot/futures market data and sends generated signals
to Telegram.

## GitHub Actions

The scanner workflow runs only when started manually from the repository's
**Actions** tab.

Before running it, add these repository secrets under **Settings → Secrets and
variables → Actions → New repository secret**:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

The workflow passes these values to the program as environment variables. Do
not put Telegram credentials in source files or commit them to the repository.

To run locally, install the packages in `requirements.txt`, set the same two
environment variables, then run `python obr.py`.
