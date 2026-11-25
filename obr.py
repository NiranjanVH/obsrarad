import tel
from obsr import scan_symbol

# ============================================================
# CONFIGURATION
# ============================================================

SYMBOLS = {
    "ZECUSDT": True,
    "XAUUSDT": False,
    "ETHUSDT": True,
    "BTCUSDT": True,
}

# True  = SPOT + FUTURES
# False = FUTURES only


# ============================================================
# TARGET CALCULATION
# ============================================================

def calculate_targets(direction, entry):
    if direction == "LONG":
        return {
            "TP1": entry * 1.005,
            "TP2": entry * 1.010,
            "TP3": entry * 1.015,
        }
    return {
        "TP1": entry * 0.995,
        "TP2": entry * 0.990,
        "TP3": entry * 0.985,
    }


# ============================================================
# FIND NEAREST ORDER BLOCK
# ============================================================

def find_nearest_orderblock(order_blocks, futures_price):
    valid_obs = []

    for ob in order_blocks:
        top = float(ob["TOP"])
        bottom = float(ob["BOTTOM"])

        # Ignore invalid zones
        if top <= bottom:
            continue

        # LONG: Price is above the order block
        if futures_price > top:
            position = "ABOVE ZONE"
            distance = futures_price - top
            direction = "LONG"
            entry = top
            sl = bottom

        # SHORT: Price is below the order block
        elif futures_price < bottom:
            position = "BELOW ZONE"
            distance = bottom - futures_price
            direction = "SHORT"
            entry = bottom
            sl = top

        else:
            # Ignore price inside order block
            continue

        distance_pct = (distance / futures_price * 100) if futures_price else 0.0

        valid_obs.append({
            **ob,
            "POSITION": position,
            "DIRECTION": direction,
            "ENTRY": entry,
            "SL": sl,
            "DISTANCE_FROM_FUTURES": distance,
            "DISTANCE_PERCENT": distance_pct,
        })

    if not valid_obs:
        return None

    valid_obs.sort(key=lambda x: x["DISTANCE_FROM_FUTURES"])
    return valid_obs[0]


# ============================================================
# TELEGRAM MESSAGE FORMATTER
# ============================================================

def send_signal_to_telegram(s):
    direction = s["SIGNAL"]
    emoji = "🟢" if direction == "LONG" else "🔴"

    message = (
        f"*{s['SYMBOL']} — {direction} {emoji}*\n"
        f"OB: *{s['SOURCE']} · {s['TIMEFRAME']}*\n"
        f"━━━━━━━━━━━━━━\n"
        f"Entry: `{s['ENTRY']:.4f}`\n"
        f"SL: `{s['SL']:.4f}`\n"
        f"━━━━━━━━━━━━━━\n"
        f"TP1: `{s['TP1']:.4f}`\n"
        f"TP2: `{s['TP2']:.4f}`\n"
        f"TP3: `{s['TP3']:.4f}`"
    )

    try:
        sent = tel.send_telegram_message(message, parse_mode="Markdown")
        if sent:
            print(f"{s['SYMBOL']}: TELEGRAM SENT")
        else:
            print(f"{s['SYMBOL']}: TELEGRAM RETURNED FALSE")
    except Exception as e:
        print(f"{s['SYMBOL']}: TELEGRAM ERROR: {e}")


# ============================================================
# GENERATE SIGNAL
# ============================================================

def generate_signal(symbol, include_spot):
    print(f"\nScanning {symbol}...")

    try:
        data = scan_symbol(symbol, include_spot=include_spot)
        prices = data["prices"]
        order_blocks = data["order_blocks"]

        # Futures price is mandatory
        futures_price = prices.get("FUTURES")
        if futures_price is None:
            print(f"{symbol}: FUTURES PRICE UNAVAILABLE")
            return None

        if not order_blocks:
            print(f"{symbol}: NO OPEN ORDER BLOCKS")
            return None

        nearest = find_nearest_orderblock(order_blocks, futures_price)
        if nearest is None:
            print(f"{symbol}: NO VALID ORDER BLOCK OUTSIDE ZONE")
            return None

        direction = nearest["DIRECTION"]
        entry = nearest["ENTRY"]
        sl = nearest["SL"]
        targets = calculate_targets(direction, entry)

        return {
            "SYMBOL": symbol,
            "SIGNAL": direction,
            "FUTURES PRICE": futures_price,
            "SOURCE": nearest["MARKET"],
            "TIMEFRAME": nearest["TF"],
            "OB TYPE": nearest["TYPE"],
            "OB TOP": nearest["TOP"],
            "OB BOTTOM": nearest["BOTTOM"],
            "POSITION": nearest["POSITION"],
            "DISTANCE": nearest["DISTANCE_FROM_FUTURES"],
            "DISTANCE (%)": nearest["DISTANCE_PERCENT"],
            "ENTRY": entry,
            "SL": sl,
            "TP1": targets["TP1"],
            "TP2": targets["TP2"],
            "TP3": targets["TP3"],
        }

    except Exception as e:
        print(f"{symbol} ERROR: {e}")
        return None


# ============================================================
# TERMINAL OUTPUT
# ============================================================

def print_signal(s):
    print("\n")
    print("=" * 65)
    print(f"  {s['SYMBOL']}  |  {s['SIGNAL']}")
    print("=" * 65)
    print(f"  Futures Price : {s['FUTURES PRICE']:.4f}")
    print(f"  Source        : {s['SOURCE']}")
    print(f"  Timeframe     : {s['TIMEFRAME']}")
    print(f"  OB Type       : {s['OB TYPE']}")
    print(f"  Position      : {s['POSITION']}")
    print("-" * 65)
    print(f"  ENTRY         : {s['ENTRY']:.4f}")
    print(f"  STOP LOSS     : {s['SL']:.4f}")
    print("-" * 65)
    print("  TAKE PROFIT TARGETS")
    print(f"  TP1 (+/-0.5%) : {s['TP1']:.4f}")
    print(f"  TP2 (+/-1.0%) : {s['TP2']:.4f}")
    print(f"  TP3 (+/-1.5%) : {s['TP3']:.4f}")
    print("-" * 65)
    print(f"  OB TOP        : {s['OB TOP']:.4f}")
    print(f"  OB BOTTOM     : {s['OB BOTTOM']:.4f}")
    print(f"  DISTANCE      : {s['DISTANCE']:.4f} ({s['DISTANCE (%)']:.4f}%)")
    print("=" * 65)


# ============================================================
# MAIN
# ============================================================

def main():
    signals = []

    print("\n")
    print("=" * 65)
    print("          MULTI-COIN ORDER BLOCK SIGNAL GENERATOR")
    print("=" * 65)

    for symbol, include_spot in SYMBOLS.items():
        signal = generate_signal(symbol, include_spot)
        if signal:
            signals.append(signal)
            print_signal(signal)
            send_signal_to_telegram(signal)

    print("\n")
    print("=" * 65)
    print("SCAN COMPLETED")
    print(f"TOTAL SIGNALS GENERATED: {len(signals)}")
    print("=" * 65)


if __name__ == "__main__":
    main()
