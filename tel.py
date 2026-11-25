import os
import requests


def get_config(
    chat_id: str | int | None = None,
    bot_token: str | None = None,
) -> tuple[str, str | int]:
    resolved_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    resolved_chat_id = chat_id if chat_id is not None else os.getenv("TELEGRAM_CHAT_ID", "").strip()

    missing = []
    if not resolved_token:
        missing.append("TELEGRAM_BOT_TOKEN")
    if resolved_chat_id == "":
        missing.append("TELEGRAM_CHAT_ID")

    if missing:
        raise RuntimeError(
            "Missing required environment variable(s): " + ", ".join(missing)
        )

    return resolved_token, resolved_chat_id


def validate_configuration() -> None:
    """Validate that TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set."""
    get_config()


def send_telegram_message(
    text: str,
    chat_id: str | int | None = None,
    bot_token: str | None = None,
    parse_mode: str | None = None,
) -> bool:
    """Send a message to Telegram. Returns True if successful."""
    if not text:
        return False

    token, target_chat_id = get_config(chat_id=chat_id, bot_token=bot_token)
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload: dict[str, str | int] = {
        "chat_id": target_chat_id,
        "text": text,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode

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
    validate_configuration()
    sent = send_telegram_message("Test message from Python")
    print("Sent:", sent)
    on_order_placed("B-BTC_USDT", "BUY", 92500.0)
