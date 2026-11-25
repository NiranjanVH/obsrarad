import os
import pandas as pd
import requests

# ============================================================
# CONFIGURATION
# ============================================================

SPOT_URL = "https://data-api.binance.vision"
FUTURES_URL = "https://fapi.binance.com"

DEFAULT_SYMBOL = "BTCUSDT"
TIMEFRAMES = ["5m", "15m"]

CANDLES = 1000
BARS_BACK = 500

FRACTAL_FILTER = "3"
FIND_OB_TYPE = "Close"

FILTER_FVGS = True
FVG_DISTANCE = 3

LINE_HEIGHT = "Body"
MAX_OBS = 4

# Optional proxy for Binance Futures (e.g. socks5h://user:pass@host:port or http://host:port)
FUTURES_PROXY = os.getenv("FUTURES_PROXY", "").strip() or None

spot_session = requests.Session()
spot_session.headers.update({"User-Agent": "MultiCoin-OB-Scanner/1.0"})

futures_session = requests.Session()
futures_session.headers.update({"User-Agent": "MultiCoin-OB-Scanner/1.0"})
if FUTURES_PROXY:
    futures_session.proxies.update({
        "http": FUTURES_PROXY,
        "https": FUTURES_PROXY,
    })


# ============================================================
# BINANCE API
# ============================================================

def _get_client(market: str):
    if market == "SPOT":
        return spot_session, SPOT_URL, "/api/v3"
    if market == "FUTURES":
        return futures_session, FUTURES_URL, "/fapi/v1"
    raise ValueError(f"Unsupported market: {market}")


def get_json(session: requests.Session, url: str, params: dict | None = None):
    response = session.get(url, params=params, timeout=20)
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        if status == 451:
            raise RuntimeError(f"HTTP 451 (blocked by IP/region) for {url}") from exc
        raise
    return response.json()


def get_klines(market: str, timeframe: str, symbol: str = DEFAULT_SYMBOL) -> pd.DataFrame:
    sess, base_url, prefix = _get_client(market)
    data = get_json(
        sess,
        f"{base_url}{prefix}/klines",
        {
            "symbol": symbol,
            "interval": timeframe,
            "limit": CANDLES,
        },
    )

    columns = [
        "time", "open", "high", "low", "close",
        "volume", "close_time", "quote_volume",
        "trades", "taker_buy_base",
        "taker_buy_quote", "ignore",
    ]

    df = pd.DataFrame(data, columns=columns)

    for col in ["open", "high", "low", "close"]:
        df[col] = pd.to_numeric(df[col])

    df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
    df["close_time"] = pd.to_numeric(df["close_time"])

    now_ms = int(pd.Timestamp.now(tz="UTC").timestamp() * 1000)

    # Remove unfinished candle
    df = df[df["close_time"] < now_ms].reset_index(drop=True)
    return df


def get_price(market: str, symbol: str = DEFAULT_SYMBOL) -> float:
    sess, base_url, prefix = _get_client(market)
    data = get_json(
        sess,
        f"{base_url}{prefix}/ticker/price",
        {"symbol": symbol},
    )
    return float(data["price"])


# ============================================================
# FRACTAL DETECTION
# ============================================================

def fractal_high(df: pd.DataFrame, i: int) -> bool:
    if FRACTAL_FILTER == "3":
        if i < 3:
            return False
        return (
            df.high.iloc[i] < df.high.iloc[i - 1]
            and (
                df.high.iloc[i - 2] < df.high.iloc[i - 1]
                or (
                    df.high.iloc[i - 2] == df.high.iloc[i - 1]
                    and df.high.iloc[i - 3] < df.high.iloc[i - 2]
                )
            )
        )

    if i < 4:
        return False
    return (
        df.high.iloc[i] < df.high.iloc[i - 2]
        and df.high.iloc[i - 1] < df.high.iloc[i - 2]
        and df.high.iloc[i - 3] < df.high.iloc[i - 2]
        and df.high.iloc[i - 4] < df.high.iloc[i - 2]
    )


def fractal_low(df: pd.DataFrame, i: int) -> bool:
    if FRACTAL_FILTER == "3":
        if i < 3:
            return False
        return (
            df.low.iloc[i] > df.low.iloc[i - 1]
            and (
                df.low.iloc[i - 2] > df.low.iloc[i - 1]
                or (
                    df.low.iloc[i - 2] == df.low.iloc[i - 1]
                    and df.low.iloc[i - 3] > df.low.iloc[i - 2]
                )
            )
        )

    if i < 4:
        return False
    return (
        df.low.iloc[i] > df.low.iloc[i - 2]
        and df.low.iloc[i - 1] > df.low.iloc[i - 2]
        and df.low.iloc[i - 3] > df.low.iloc[i - 2]
        and df.low.iloc[i - 4] > df.low.iloc[i - 2]
    )


# ============================================================
# ORDER BLOCK DETECTION
# ============================================================

def find_orderblocks(df: pd.DataFrame):
    fractal_highs = []
    fractal_lows = []
    active_obs = []
    shift = 1 if FRACTAL_FILTER == "3" else 2

    for i in range(len(df)):
        if fractal_high(df, i):
            fractal_highs.append({
                "index": i - shift,
                "price": float(df.high.iloc[i - shift]),
            })

        if fractal_low(df, i):
            fractal_lows.append({
                "index": i - shift,
                "price": float(df.low.iloc[i - shift]),
            })

        # ----------------------------------------------------
        # BEARISH ORDER BLOCK
        # ----------------------------------------------------
        for p in range(len(fractal_lows) - 1, -1, -1):
            fractal = fractal_lows[p]
            broken = (
                df.close.iloc[i] < fractal["price"]
                if FIND_OB_TYPE == "Close"
                else df.low.iloc[i] < fractal["price"]
            )
            if not broken:
                continue

            idx = 0
            highest_price = float(df.low.iloc[i])
            gap_index = 0

            for k in range(BARS_BACK + 1):
                j = i - k
                if j < 0 or j < fractal["index"]:
                    break

                bearish_gap = False
                if j >= 2:
                    bearish_gap = (
                        df.close.iloc[j - 1] < df.low.iloc[j - 2]
                        and df.high.iloc[j] < df.low.iloc[j - 2]
                    )

                if df.close.iloc[j] > df.open.iloc[j] and df.high.iloc[j] > highest_price:
                    idx = k
                    highest_price = float(df.high.iloc[j])

                if bearish_gap and df.high.iloc[j] > highest_price:
                    gap_index = k + 2

            valid_fvg = gap_index > 0 and 0 <= idx - gap_index <= FVG_DISTANCE
            if not FILTER_FVGS:
                valid_fvg = True

            if idx != 0 and valid_fvg:
                ob_index = i - idx
                top = float(df.high.iloc[ob_index])
                bottom = (
                    float(df.open.iloc[ob_index])
                    if LINE_HEIGHT == "Body"
                    else float(df.low.iloc[ob_index])
                )
                active_obs.append({
                    "type": "BEARISH",
                    "top": top,
                    "bottom": bottom,
                    "index": ob_index,
                })

            fractal_lows.pop(p)

        # ----------------------------------------------------
        # BULLISH ORDER BLOCK
        # ----------------------------------------------------
        for p in range(len(fractal_highs) - 1, -1, -1):
            fractal = fractal_highs[p]
            broken = (
                df.close.iloc[i] > fractal["price"]
                if FIND_OB_TYPE == "Close"
                else df.high.iloc[i] > fractal["price"]
            )
            if not broken:
                continue

            idx = 0
            lowest_price = float(df.low.iloc[i])
            gap_index = 0

            for k in range(BARS_BACK + 1):
                j = i - k
                if j < 0 or j < fractal["index"]:
                    break

                bullish_gap = False
                if j >= 2:
                    bullish_gap = (
                        df.close.iloc[j - 1] > df.high.iloc[j - 2]
                        and df.low.iloc[j] > df.high.iloc[j - 2]
                    )

                if df.close.iloc[j] < df.open.iloc[j] and df.low.iloc[j] < lowest_price:
                    idx = k
                    lowest_price = float(df.low.iloc[j])

                if bullish_gap:
                    gap_index = k + 2

            valid_fvg = gap_index > 0 and 0 <= idx - gap_index <= FVG_DISTANCE
            if not FILTER_FVGS:
                valid_fvg = True

            if idx != 0 and valid_fvg:
                ob_index = i - idx
                bottom = float(df.low.iloc[ob_index])
                top = (
                    float(df.open.iloc[ob_index])
                    if LINE_HEIGHT == "Body"
                    else float(df.high.iloc[ob_index])
                )
                active_obs.append({
                    "type": "BULLISH",
                    "top": top,
                    "bottom": bottom,
                    "index": ob_index,
                })

            fractal_highs.pop(p)

        # ----------------------------------------------------
        # REMOVE FILLED ORDER BLOCKS
        # ----------------------------------------------------
        remaining = []
        for ob in active_obs:
            filled = (
                df.high.iloc[i] >= ob["top"]
                if ob["type"] == "BEARISH"
                else df.low.iloc[i] <= ob["bottom"]
            )
            if not filled:
                remaining.append(ob)
        active_obs = remaining

    active_obs.sort(key=lambda x: x["index"], reverse=True)

    unique_obs = []
    seen = set()
    for ob in active_obs:
        key = (ob["type"], round(ob["top"], 4), round(ob["bottom"], 4))
        if key not in seen:
            seen.add(key)
            unique_obs.append(ob)

    return unique_obs[:MAX_OBS]


# ============================================================
# DISTANCE CALCULATION
# ============================================================

def get_distance(price: float, ob: dict) -> dict:
    top = float(ob["top"])
    bottom = float(ob["bottom"])
    zone_height = top - bottom
    zone_size_pct = (zone_height / top * 100) if top else 0.0

    if price > top:
        points = price - top
        reference = top
        position = "ABOVE ZONE"
    elif price < bottom:
        points = bottom - price
        reference = bottom
        position = "BELOW ZONE"
    else:
        points = 0.0
        reference = price
        position = "INSIDE ZONE"

    distance_pct = (points / reference * 100) if reference else 0.0

    return {
        "distance_points": points,
        "distance_pct": distance_pct,
        "zone_height": zone_height,
        "zone_size_pct": zone_size_pct,
        "position": position,
    }


# ============================================================
# REUSABLE SCANNER METHOD FOR obr.py
# ============================================================

def scan_symbol(symbol: str, include_spot: bool = True) -> dict:
    results = []
    prices = {}
    markets = ["SPOT", "FUTURES"] if include_spot else ["FUTURES"]

    for market in markets:
        try:
            price = get_price(market, symbol)
            prices[market] = price
        except Exception as e:
            print(f"{symbol} {market} PRICE ERROR: {e}")
            continue

        for timeframe in TIMEFRAMES:
            try:
                df = get_klines(market, timeframe, symbol)
                obs = find_orderblocks(df)

                for ob in obs:
                    distance = get_distance(price, ob)
                    results.append({
                        "SYMBOL": symbol,
                        "MARKET": market,
                        "TF": timeframe,
                        "TYPE": ob["type"],
                        "TOP": float(ob["top"]),
                        "BOTTOM": float(ob["bottom"]),
                        "PRICE": float(price),
                        "DISTANCE (POINTS)": float(distance["distance_points"]),
                        "DISTANCE (%)": float(distance["distance_pct"]),
                        "ZONE HEIGHT": float(distance["zone_height"]),
                        "ZONE SIZE (%)": float(distance["zone_size_pct"]),
                        "POSITION": distance["position"],
                    })
            except Exception as e:
                print(f"{symbol} {market} {timeframe} ERROR: {e}")

    return {
        "symbol": symbol,
        "prices": prices,
        "order_blocks": results,
    }


# ============================================================
# MAIN SCANNER
# ============================================================

def main():
    results = []
    print("\nFetching Binance market data...")
    print(f"Futures Proxy: {'ENABLED' if FUTURES_PROXY else 'NONE (direct)'}")

    for market in ["SPOT", "FUTURES"]:
        try:
            price = get_price(market, DEFAULT_SYMBOL)
            print(f"{market} PRICE ({DEFAULT_SYMBOL}): {price:,.4f}")
        except Exception as e:
            print(f"{market} ERROR: {e}")
            continue

        for timeframe in TIMEFRAMES:
            try:
                df = get_klines(market, timeframe, DEFAULT_SYMBOL)
                obs = find_orderblocks(df)

                if not obs:
                    results.append({
                        "MARKET": market,
                        "TF": timeframe,
                        "TYPE": "NONE",
                        "TOP": "-",
                        "BOTTOM": "-",
                        "PRICE": round(price, 4),
                        "DISTANCE (POINTS)": "-",
                        "DISTANCE (%)": "-",
                        "ZONE HEIGHT": "-",
                        "ZONE SIZE (%)": "-",
                        "POSITION": "NO OPEN OB",
                    })
                    continue

                for ob in obs:
                    distance = get_distance(price, ob)
                    results.append({
                        "MARKET": market,
                        "TF": timeframe,
                        "TYPE": ob["type"],
                        "TOP": round(ob["top"], 4),
                        "BOTTOM": round(ob["bottom"], 4),
                        "PRICE": round(price, 4),
                        "DISTANCE (POINTS)": round(distance["distance_points"], 4),
                        "DISTANCE (%)": round(distance["distance_pct"], 3),
                        "ZONE HEIGHT": round(distance["zone_height"], 4),
                        "ZONE SIZE (%)": round(distance["zone_size_pct"], 3),
                        "POSITION": distance["position"],
                    })
            except Exception as e:
                print(f"ERROR: {market} {timeframe}: {e}")

    print("\n")
    print("=" * 150)
    print(f"                    {DEFAULT_SYMBOL} OPEN ORDER BLOCK SCANNER")
    print("=" * 150)

    if results:
        table = pd.DataFrame(results)
        print(table.to_string(index=False, justify="center"))
    else:
        print("No market data available.")

    print("=" * 150)
    print("SCAN COMPLETED")


if __name__ == "__main__":
    main()
