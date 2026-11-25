import os
import requests

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def validate_configuration() -> None:
    missing = [
        name
        for name, value in (
            ("TELEGRAM_BOT_TOKEN", BOT_TOKEN),
            ("TELEGRAM_CHAT_ID", CHAT_ID),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            "Missing required environment variable(s): " + ", ".join(missing)
        )


def send_telegram_message(
    text: str,
    chat_id: str | int | None = None,
    bot_token: str | None = None,
) -> bool:
    """Send a plain-text message to Telegram. Returns True if successful."""
    if not text:
        return False

    chat_id = chat_id or CHAT_ID
    bot_token = bot_token or BOT_TOKEN
    if not chat_id or not bot_token:
        validate_configuration()

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
    }

    try:
        response = requests.post(url, json=payload, timeout=15)
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            print(f"Telegram API error: {data}")
            return False
        return True
    except requests.RequestException as exc:
        print(f"Failed to send Telegram message: {exc}")
        return False


# --- Example usage from other methods ---

def on_order_placed(symbol: str, side: str, price: float) -> None:
    msg = f"Order Placed: {side.upper()} {symbol} @ {price}"
    send_telegram_message(msg)


def on_error_occurred(error_detail: str) -> None:
    send_telegram_message(f"Error: {error_detail}")


if __name__ == "__main__":
    sent = send_telegram_message("Test message from Python")
    print("Sent:", sent)
    on_order_placed("B-BTC_USDT", "BUY", 92500.0)
