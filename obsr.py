import time
import pandas as pd
import requests

# ============================================================
# CONFIGURATION
# ============================================================

COINDCX_PUBLIC_URL = "https://public.coindcx.com"
COINDCX_API_URL = "https://api.coindcx.com"

DEFAULT_SYMBOL = "ZECUSDT"
TIMEFRAMES = ["5m", "15m"]

CANDLES = 1000
BARS_BACK = 500

FRACTAL_FILTER = "3"
FIND_OB_TYPE = "Close"

FILTER_FVGS = True
FVG_DISTANCE = 3

LINE_HEIGHT = "Body"
MAX_OBS = 4

session = requests.Session()
session.headers.update({
    "User-Agent": "CoinDCX-OB-Scanner/1.0"
})

RESOLUTION_MAP = {
    "1m": "1",
    "5m": "5",
    "15m": "15",
    "30m": "30",
    "1h": "60",
    "1H": "60",
    "4h": "240",
    "4H": "240",
    "1d": "1D",
    "1D": "1D",
}

INTERVAL_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "1H": 3600,
    "4h": 14400,
    "4H": 14400,
    "1d": 86400,
    "1D": 86400,
}


# ============================================================
# PAIR & TIMEFRAME HELPERS
# ============================================================

def to_coindcx_pair(symbol: str, market: str) -> str:
    clean = symbol.upper().replace("-", "").replace("_", "")
    if clean.endswith("USDT"):
        base = clean[:-4]
        quote = "USDT"
    elif clean.endswith("INR"):
        base = clean[:-3]
        quote = "INR"
    else:
        base = clean
        quote = "USDT"

    if market == "FUTURES":
        return f"B-{base}_{quote}"
    if market == "SPOT":
        return f"KC-{base}_{quote}"
    raise ValueError(f"Unsupported market: {market}")


def to_resolution(timeframe: str) -> str:
    if timeframe in RESOLUTION_MAP:
        return RESOLUTION_MAP[timeframe]
    if timeframe.endswith("m") and timeframe[:-1].isdigit():
        return timeframe[:-1]
    if timeframe.endswith("h") and timeframe[:-1].isdigit():
        return str(int(timeframe[:-1]) * 60)
    if timeframe.endswith("d") and timeframe[:-1].isdigit():
        return f"{timeframe[:-1]}D"
    raise ValueError(f"Unsupported timeframe: {timeframe}")


def timeframe_seconds(timeframe: str) -> int:
    if timeframe in INTERVAL_SECONDS:
        return INTERVAL_SECONDS[timeframe]
    if timeframe.endswith("m") and timeframe[:-1].isdigit():
        return int(timeframe[:-1]) * 60
    if timeframe.endswith("h") and timeframe[:-1].isdigit():
        return int(timeframe[:-1]) * 3600
    if timeframe.endswith("d") and timeframe[:-1].isdigit():
        return int(timeframe[:-1]) * 86400
    return 300


def log_orderblocks(symbol: str, market: str, timeframe: str, price: float, obs: list[dict]) -> None:
    if not obs:
        print(f"  [{symbol} | {market} | {timeframe}] No open OBs found (price={price:.4f})")
        return

    print(f"  [{symbol} | {market} | {timeframe}] Found {len(obs)} open OB(s) (price={price:.4f}):")
    for idx, ob in enumerate(obs, start=1):
        dist = get_distance(price, ob)
        print(
            f"    #{idx} {ob['type']} OB | "
            f"Range: [{ob['bottom']:.4f} -> {ob['top']:.4f}] | "
            f"Idx: {ob['index']} | "
            f"Pos: {dist['position']} | "
            f"Dist: {dist['distance_points']:.4f} ({dist['distance_pct']:.3f}%)"
        )


# ============================================================
# COINDCX API
# ============================================================

def get_json(url: str, params: dict | None = None):
    response = session.get(url, params=params, timeout=20)
    response.raise_for_status()
    return response.json()


def _parse_candles_payload(raw) -> pd.DataFrame:
    if isinstance(raw, dict):
        rows = raw.get("data") or raw.get("candles") or []
    else:
        rows = raw or []

    if not rows:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])

    records = []
    for item in rows:
        if isinstance(item, dict):
            ts = item.get("time") or item.get("timestamp") or item.get("t")
            o = item.get("open") or item.get("o")
            h = item.get("high") or item.get("h")
            l = item.get("low") or item.get("l")
            c = item.get("close") or item.get("c")
            v = item.get("volume") or item.get("v") or 0
        elif isinstance(item, (list, tuple)) and len(item) >= 5:
            ts, o, h, l, c = item[:5]
            v = item[5] if len(item) > 5 else 0
        else:
            continue

        ts_num = float(ts)
        # Normalize seconds vs milliseconds
        if ts_num > 1e11:
            ts_sec = ts_num / 1000.0
        else:
            ts_sec = ts_num

        records.append({
            "time_sec": ts_sec,
            "open": float(o),
            "high": float(h),
            "low": float(l),
            "close": float(c),
            "volume": float(v),
        })

    if not records:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])

    df = pd.DataFrame(records)
    df = df.sort_values("time_sec").drop_duplicates(subset=["time_sec"]).reset_index(drop=True)
    df["time"] = pd.to_datetime(df["time_sec"], unit="s", utc=True)
    return df


def get_klines(market: str, timeframe: str, symbol: str = DEFAULT_SYMBOL) -> pd.DataFrame:
    pair = to_coindcx_pair(symbol, market)
    step_sec = timeframe_seconds(timeframe)
    now_sec = int(time.time())

    if market == "SPOT":
        raw = get_json(
            f"{COINDCX_PUBLIC_URL}/market_data/candles",
            params={
                "pair": pair,
                "interval": timeframe,
                "limit": min(CANDLES + 10, 1000),
            },
        )
    elif market == "FUTURES":
        resolution = to_resolution(timeframe)
        from_sec = now_sec - (CANDLES + 20) * step_sec
        raw = get_json(
            f"{COINDCX_PUBLIC_URL}/market_data/candlesticks",
            params={
                "pair": pair,
                "from": from_sec,
                "to": now_sec,
                "resolution": resolution,
                "pcode": "f",
            },
        )
    else:
        raise ValueError(f"Unsupported market: {market}")

    df = _parse_candles_payload(raw)

    if df.empty:
        return df

    # Remove unfinished candle (open time still within current interval)
    current_bar_start = (now_sec // step_sec) * step_sec
    df = df[df["time_sec"] < current_bar_start].copy()

    if len(df) > CANDLES:
        df = df.tail(CANDLES)

    df = df.drop(columns=["time_sec"]).reset_index(drop=True)
    return df


def get_price(market: str, symbol: str = DEFAULT_SYMBOL) -> float:
    if market == "SPOT":
        target_pair = to_coindcx_pair(symbol, "SPOT")
        tickers = get_json(f"{COINDCX_API_URL}/exchange/ticker")
        if isinstance(tickers, list):
            for t in tickers:
                m = str(t.get("market", "")).upper()
                if m in (target_pair, symbol.upper()):
                    return float(t["last_price"])
        raise ValueError(f"Spot ticker not found for {symbol}")

    if market == "FUTURES":
        # Fetch recent 1m candle to get latest closed/mark price without needing auth
        df = get_klines("FUTURES", "1m", symbol)
        if not df.empty:
            return float(df["close"].iloc[-1])
        raise ValueError(f"Futures price unavailable for {symbol}")

    raise ValueError(f"Unsupported market: {market}")


# ============================================================
# FRACTAL DETECTION
# ============================================================

def fractal_high(df, i):
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


def fractal_low(df, i):
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

def find_orderblocks(df):
    fractal_highs = []
    fractal_lows = []
    active_obs = []

    shift = 1 if FRACTAL_FILTER == "3" else 2

    for i in range(len(df)):
        if fractal_high(df, i):
            fractal_highs.append({
                "index": i - shift,
                "price": float(df.high.iloc[i - shift])
            })

        if fractal_low(df, i):
            fractal_lows.append({
                "index": i - shift,
                "price": float(df.low.iloc[i - shift])
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

            valid_fvg = (
                gap_index > 0
                and 0 <= idx - gap_index <= FVG_DISTANCE
            )
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
                    "index": ob_index
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

            valid_fvg = (
                gap_index > 0
                and 0 <= idx - gap_index <= FVG_DISTANCE
            )
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
                    "index": ob_index
                })

            fractal_highs.pop(p)

        # ----------------------------------------------------
        # REMOVE FILLED ORDER BLOCKS
        # ----------------------------------------------------
        remaining = []
        for ob in active_obs:
            if ob["type"] == "BEARISH":
                filled = df.high.iloc[i] >= ob["top"]
            else:
                filled = df.low.iloc[i] <= ob["bottom"]

            if not filled:
                remaining.append(ob)

        active_obs = remaining

    active_obs.sort(key=lambda x: x["index"], reverse=True)

    unique_obs = []
    seen = set()
    for ob in active_obs:
        key = (
            ob["type"],
            round(ob["top"], 4),
            round(ob["bottom"], 4)
        )
        if key not in seen:
            seen.add(key)
            unique_obs.append(ob)

    return unique_obs[:MAX_OBS]


# ============================================================
# DISTANCE CALCULATION
# ============================================================

def get_distance(price, ob):
    top = float(ob["top"])
    bottom = float(ob["bottom"])

    zone_height = top - bottom
    zone_size_pct = (zone_height / top * 100) if top else 0

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

    distance_pct = (points / reference * 100) if reference else 0

    return {
        "distance_points": points,
        "distance_pct": distance_pct,
        "zone_height": zone_height,
        "zone_size_pct": zone_size_pct,
        "position": position
    }


# ============================================================
# REUSABLE SCANNER METHOD FOR obr.py
# ============================================================

def scan_symbol(symbol, include_spot=True):
    results = []
    prices = {}

    markets = ["SPOT", "FUTURES"] if include_spot else ["FUTURES"]

    for market in markets:
        try:
            price = get_price(market, symbol)
            prices[market] = price
            print(f"[{symbol}] {market} PRICE: {price:.4f}")
        except Exception as e:
            print(f"[{symbol}] {market} PRICE ERROR: {e}")
            continue

        for timeframe in TIMEFRAMES:
            try:
                df = get_klines(market, timeframe, symbol)
                obs = find_orderblocks(df)
                log_orderblocks(symbol, market, timeframe, price, obs)

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
                        "POSITION": distance["position"]
                    })

            except Exception as e:
                print(f"[{symbol}] {market} {timeframe} ERROR: {e}")

    return {
        "symbol": symbol,
        "prices": prices,
        "order_blocks": results
    }


# ============================================================
# MAIN SCANNER
# ============================================================

def main():
    results = []
    print("\nFetching CoinDCX market data...")

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
                log_orderblocks(DEFAULT_SYMBOL, market, timeframe, price, obs)

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
                        "POSITION": "NO OPEN OB"
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
                        "POSITION": distance["position"]
                    })

            except Exception as e:
                print(f"ERROR: {market} {timeframe}: {e}")

    print("\n")
    print("=" * 150)
    print(f"                    {DEFAULT_SYMBOL} OPEN ORDER BLOCK SCANNER (COINDCX)")
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
