import asyncio
from urllib.parse import urlencode
import pandas as pd
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

# ============================================================
# CONFIGURATION
# ============================================================

SPOT_URL = "https://api.binance.com"
FUTURES_URL = "https://fapi.binance.com"

SYMBOL = "ZECUSDT"
TIMEFRAMES = ["5m", "15m"]

CANDLES = 1000
BARS_BACK = 500

FRACTAL_FILTER = "3"
FIND_OB_TYPE = "Close"

FILTER_FVGS = True
FVG_DISTANCE = 3

LINE_HEIGHT = "Body"
MAX_OBS = 4


# ============================================================
# PLAYWRIGHT FETCH HELPER
# ============================================================

async def browser_get_json(page, url, params=None):
    if params:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}{urlencode(params)}"

    return await page.evaluate(
        """async (targetUrl) => {
            const resp = await fetch(targetUrl, {
                method: "GET",
                headers: { "Accept": "application/json" }
            });
            if (!resp.ok) {
                throw new Error(`${resp.status} Client Error for url: ${targetUrl}`);
            }
            return await resp.json();
        }""",
        url,
    )


async def get_klines_async(page, symbol, market, timeframe):
    url = (
        SPOT_URL + "/api/v3/klines"
        if market == "SPOT"
        else FUTURES_URL + "/fapi/v1/klines"
    )

    data = await browser_get_json(
        page,
        url,
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
    df = df[df["close_time"] < now_ms].reset_index(drop=True)

    return df


async def get_price_async(page, symbol, market):
    url = (
        SPOT_URL + "/api/v3/ticker/price"
        if market == "SPOT"
        else FUTURES_URL + "/fapi/v1/ticker/price"
    )
    data = await browser_get_json(page, url, {"symbol": symbol})
    return float(data["price"])


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
                "price": float(df.high.iloc[i - shift]),
            })

        if fractal_low(df, i):
            fractal_lows.append({
                "index": i - shift,
                "price": float(df.low.iloc[i - shift]),
            })

        # BEARISH ORDER BLOCK
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
                and 0 <= (idx - gap_index) <= FVG_DISTANCE
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
                    "index": ob_index,
                })

            fractal_lows.pop(p)

        # BULLISH ORDER BLOCK
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
                and 0 <= (idx - gap_index) <= FVG_DISTANCE
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
                    "index": ob_index,
                })

            fractal_highs.pop(p)

        # REMOVE FILLED ORDER BLOCKS
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
        key = (ob["type"], round(ob["top"], 2), round(ob["bottom"], 2))
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
        "position": position,
    }


# ============================================================
# SCANNER METHOD FOR obr.py
# ============================================================

async def _scan_symbol_async(symbol, include_spot=True):
    results = []
    prices = {}
    markets = ["SPOT", "FUTURES"] if include_spot else ["FUTURES"]

    async with Stealth().use_async(async_playwright()) as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        try:
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                    " (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                ),
                locale="en-US",
            )
            page = await context.new_page()

            for market in markets:
                try:
                    price = await get_price_async(page, symbol, market)
                    prices[market] = price
                except Exception as e:
                    print(f"{symbol} {market} PRICE ERROR: {e}")
                    continue

                for timeframe in TIMEFRAMES:
                    try:
                        df = await get_klines_async(page, symbol, market, timeframe)
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
        finally:
            await browser.close()

    return {
        "symbol": symbol,
        "prices": prices,
        "order_blocks": results,
    }


def scan_symbol(symbol, include_spot=True):
    return asyncio.run(_scan_symbol_async(symbol, include_spot=include_spot))
