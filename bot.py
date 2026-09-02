import os
import time
import json
import logging
import asyncio
from datetime import datetime, timezone

import requests
import pandas as pd
import numpy as np
import pytz

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle


# ============================================================
# CONFIG
# ============================================================

TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY", "").strip()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

TWELVE_DATA_URL = "https://api.twelvedata.com/time_series"

SYMBOL = "EUR/USD"
SYMBOL_NAME = "EUR/USD"

INTERVAL = "1min"

OUTPUTSIZE = 180
MIN_CANDLES = 80

# التوصية: صفقة افتراضية لمدة دقيقتين
TRADE_DURATION_SECONDS = 120

# لا نرسل أقل من 80/100
MIN_SCORE = 80.0

# الفرق الأدنى بين الاتجاهين
MIN_SCORE_EDGE = 10

# كل كم ثانية نطلب البيانات
POLL_SECONDS = 10

# بعد انتهاء الصفقة ننتظر قليلاً
RESULT_GRACE_SECONDS = 5

REQUEST_TIMEOUT = 20

TURKEY_TZ = pytz.timezone("Europe/Istanbul")

STATS_FILE = "trading_stats.json"


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("EURUSD_REAL_FOREX_BOT")


# ============================================================
# GLOBALS
# ============================================================

total_wins = 0
total_losses = 0
total_ties = 0
total_signals = 0

last_processed_candle = None


# ============================================================
# STATISTICS
# ============================================================

def load_stats():
    global total_wins
    global total_losses
    global total_ties
    global total_signals

    try:
        if not os.path.exists(STATS_FILE):
            return

        with open(STATS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        total_wins = int(data.get("wins", 0))
        total_losses = int(data.get("losses", 0))
        total_ties = int(data.get("ties", 0))
        total_signals = int(data.get("signals", 0))

        logger.info(
            "Stats loaded | WIN=%s LOSS=%s TIE=%s SIGNALS=%s",
            total_wins,
            total_losses,
            total_ties,
            total_signals
        )

    except Exception as e:
        logger.warning("Could not load stats: %s", e)


def save_stats():
    data = {
        "wins": total_wins,
        "losses": total_losses,
        "ties": total_ties,
        "signals": total_signals,
        "updated_at": datetime.now(TURKEY_TZ).isoformat()
    }

    try:
        with open(STATS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    except Exception as e:
        logger.warning("Could not save stats: %s", e)


def win_rate():
    finished = total_wins + total_losses

    if finished == 0:
        return 0.0

    return total_wins / finished * 100.0


# ============================================================
# TELEGRAM
# ============================================================

def telegram_message(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("Telegram credentials are missing.")
        return False

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        data = response.json()

        if not data.get("ok"):
            logger.error("Telegram error: %s", data)
            return False

        return True

    except Exception as e:
        logger.error("Telegram message error: %s", e)
        return False


def telegram_photo(path, caption):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    if not path or not os.path.exists(path):
        return False

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    )

    try:
        with open(path, "rb") as photo:
            response = requests.post(
                url,
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "caption": caption,
                    "parse_mode": "HTML"
                },
                files={
                    "photo": photo
                },
                timeout=30
            )

        response.raise_for_status()

        data = response.json()

        if not data.get("ok"):
            logger.error("Telegram photo error: %s", data)
            return False

        return True

    except Exception as e:
        logger.error("Telegram photo error: %s", e)
        return False


# ============================================================
# TIME
# ============================================================

def now_utc():
    return int(datetime.now(timezone.utc).timestamp())


def fmt_time(ts):
    return datetime.fromtimestamp(
        float(ts),
        tz=TURKEY_TZ
    ).strftime("%H:%M:%S")


def fmt_datetime(ts):
    return datetime.fromtimestamp(
        float(ts),
        tz=TURKEY_TZ
    ).strftime("%Y-%m-%d %H:%M:%S")


# ============================================================
# TWELVE DATA
# ============================================================

def fetch_candles():
    if not TWELVE_DATA_API_KEY:
        logger.error("TWELVE_DATA_API_KEY is missing.")
        return None

    params = {
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "outputsize": OUTPUTSIZE,
        "apikey": TWELVE_DATA_API_KEY,
        "format": "JSON",
        "timezone": "UTC"
    }

    try:
        response = requests.get(
            TWELVE_DATA_URL,
            params=params,
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        data = response.json()

        if data.get("status") == "error":
            logger.error(
                "Twelve Data error: %s",
                data.get("message", "unknown")
            )
            return None

        values = data.get("values")

        if not values:
            logger.warning("No candle values returned.")
            return None

        df = pd.DataFrame(values)

        required = [
            "datetime",
            "open",
            "high",
            "low",
            "close"
        ]

        for column in required:
            if column not in df.columns:
                logger.error("Missing column: %s", column)
                return None

        # ----------------------------------------------------
        # TIME
        # ----------------------------------------------------

        df["timestamp"] = pd.to_datetime(
            df["datetime"],
            utc=True,
            errors="coerce"
        )

        df.dropna(
            subset=["timestamp"],
            inplace=True
        )

        df["timestamp"] = (
            df["timestamp"].astype("int64") // 10**9
        )

        # ----------------------------------------------------
        # PRICE
        # ----------------------------------------------------

        for column in [
            "open",
            "high",
            "low",
            "close"
        ]:
            df[column] = pd.to_numeric(
                df[column],
                errors="coerce"
            )

        df.dropna(
            subset=[
                "open",
                "high",
                "low",
                "close"
            ],
            inplace=True
        )

        # ----------------------------------------------------
        # OHLC SANITY
        # ----------------------------------------------------

        df = df[
            (df["high"] >= df["open"]) &
            (df["high"] >= df["close"]) &
            (df["low"] <= df["open"]) &
            (df["low"] <= df["close"])
        ]

        # ----------------------------------------------------
        # SORT
        # ----------------------------------------------------

        df.sort_values(
            "timestamp",
            inplace=True
        )

        df.drop_duplicates(
            subset=["timestamp"],
            keep="last",
            inplace=True
        )

        df.reset_index(
            drop=True,
            inplace=True
        )

        if len(df) < MIN_CANDLES:
            logger.warning(
                "Only %s candles. Required=%s",
                len(df),
                MIN_CANDLES
            )
            return None

        return df

    except requests.RequestException as e:
        logger.error("HTTP error: %s", e)
        return None

    except Exception as e:
        logger.exception("Candle error: %s", e)
        return None


# ============================================================
# CLOSED CANDLES
# ============================================================

def closed_candles(df):
    if df is None or df.empty:
        return None

    df = df.copy()

    current_minute = (
        now_utc() // 60
    ) * 60

    df = df[
        df["timestamp"] < current_minute
    ].copy()

    if len(df) < MIN_CANDLES:
        return None

    return df.tail(
        OUTPUTSIZE
    ).reset_index(
        drop=True
    )


# ============================================================
# RSI
# ============================================================

def rsi(series, period=14):
    delta = series.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / period,
        adjust=False
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / period,
        adjust=False
    ).mean()

    rs = (
        avg_gain /
        avg_loss.replace(0, np.nan)
    )

    value = (
        100 -
        100 / (1 + rs)
    )

    return value.fillna(50)


# ============================================================
# ATR
# ============================================================

def atr(df, period=14):
    previous_close = df["close"].shift(1)

    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - previous_close).abs(),
            (df["low"] - previous_close).abs()
        ],
        axis=1
    ).max(axis=1)

    return tr.rolling(period).mean()


# ============================================================
# INDICATORS
# ============================================================

def indicators(df):
    data = df.copy()

    close = data["close"]

    # EMA
    data["ema9"] = close.ewm(
        span=9,
        adjust=False
    ).mean()

    data["ema21"] = close.ewm(
        span=21,
        adjust=False
    ).mean()

    data["ema50"] = close.ewm(
        span=50,
        adjust=False
    ).mean()

    # RSI
    data["rsi"] = rsi(
        close,
        14
    )

    # MACD
    ema12 = close.ewm(
        span=12,
        adjust=False
    ).mean()

    ema26 = close.ewm(
        span=26,
        adjust=False
    ).mean()

    data["macd"] = ema12 - ema26

    data["macd_signal"] = data["macd"].ewm(
        span=9,
        adjust=False
    ).mean()

    data["macd_hist"] = (
        data["macd"] -
        data["macd_signal"]
    )

    # Bollinger
    data["bb_mid"] = close.rolling(20).mean()

    std = close.rolling(20).std()

    data["bb_upper"] = (
        data["bb_mid"] + 2 * std
    )

    data["bb_lower"] = (
        data["bb_mid"] - 2 * std
    )

    # Stochastic
    low14 = data["low"].rolling(14).min()
    high14 = data["high"].rolling(14).max()

    denominator = (
        high14 - low14
    ).replace(0, np.nan)

    data["stoch_k"] = (
        100 *
        (close - low14) /
        denominator
    )

    data["stoch_d"] = (
        data["stoch_k"]
        .rolling(3)
        .mean()
    )

    # ATR
    data["atr"] = atr(
        data,
        14
    )

    # Momentum
    data["momentum"] = (
        close -
        close.shift(4)
    )

    # ROC
    data["roc"] = (
        close.pct_change(5) * 100
    )

    # Candle
    data["body"] = (
        data["close"] -
        data["open"]
    )

    data["range"] = (
        data["high"] -
        data["low"]
    )

    data["body_ratio"] = (
        data["body"].abs() /
        data["range"].replace(0, np.nan)
    )

    # EMA slope
    data["ema21_slope"] = (
        data["ema21"] -
        data["ema21"].shift(3)
    )

    data.replace(
        [np.inf, -np.inf],
        np.nan,
        inplace=True
    )

    data.dropna(inplace=True)

    data.reset_index(
        drop=True,
        inplace=True
    )

    return data


# ============================================================
# MARKET ANALYSIS
# ============================================================

def analyze(df):
    if df is None or len(df) < MIN_CANDLES:
        return None

    data = indicators(df)

    if len(data) < 60:
        return None

    last = data.iloc[-1]
    prev = data.iloc[-2]

    call = 0
    put = 0

    confirmations = []

    # --------------------------------------------------------
    # 1 EMA TREND - 15
    # --------------------------------------------------------

    if (
        last["ema9"] >
        last["ema21"] >
        last["ema50"]
    ):
        call += 15
        confirmations.append("EMA Trend: CALL")

    elif (
        last["ema9"] <
        last["ema21"] <
        last["ema50"]
    ):
        put += 15
        confirmations.append("EMA Trend: PUT")

    else:
        confirmations.append("EMA Trend: NEUTRAL")

    # --------------------------------------------------------
    # 2 EMA MOMENTUM - 10
    # --------------------------------------------------------

    if (
        last["ema9"] > prev["ema9"] and
        last["ema21"] > prev["ema21"]
    ):
        call += 10
        confirmations.append("EMA Momentum: CALL")

    elif (
        last["ema9"] < prev["ema9"] and
        last["ema21"] < prev["ema21"]
    ):
        put += 10
        confirmations.append("EMA Momentum: PUT")

    else:
        confirmations.append("EMA Momentum: NEUTRAL")

    # --------------------------------------------------------
    # 3 RSI - 10
    # --------------------------------------------------------

    r = float(last["rsi"])

    if 52 <= r <= 68 and r > float(prev["rsi"]):
        call += 10
        confirmations.append(
            f"RSI: CALL ({r:.1f})"
        )

    elif 32 <= r <= 48 and r < float(prev["rsi"]):
        put += 10
        confirmations.append(
            f"RSI: PUT ({r:.1f})"
        )

    else:
        confirmations.append(
            f"RSI: NEUTRAL ({r:.1f})"
        )

    # --------------------------------------------------------
    # 4 MACD - 15
    # --------------------------------------------------------

    if (
        last["macd"] >
        last["macd_signal"] and
        last["macd_hist"] > 0 and
        last["macd_hist"] >
        prev["macd_hist"]
    ):
        call += 15
        confirmations.append("MACD: CALL")

    elif (
        last["macd"] <
        last["macd_signal"] and
        last["macd_hist"] < 0 and
        last["macd_hist"] <
        prev["macd_hist"]
    ):
        put += 15
        confirmations.append("MACD: PUT")

    else:
        confirmations.append("MACD: NEUTRAL")

    # --------------------------------------------------------
    # 5 BOLLINGER - 8
    # --------------------------------------------------------

    if (
        last["close"] >
        last["bb_mid"] and
        last["close"] <
        last["bb_upper"]
    ):
        call += 8
        confirmations.append("Bollinger: CALL")

    elif (
        last["close"] <
        last["bb_mid"] and
        last["close"] >
        last["bb_lower"]
    ):
        put += 8
        confirmations.append("Bollinger: PUT")

    else:
        confirmations.append("Bollinger: NEUTRAL")

    # --------------------------------------------------------
    # 6 STOCHASTIC - 10
    # --------------------------------------------------------

    k = float(last["stoch_k"])
    d = float(last["stoch_d"])

    pk = float(prev["stoch_k"])
    pd_ = float(prev["stoch_d"])

    if (
        k > d and
        pk <= pd_ and
        k < 80
    ):
        call += 10
        confirmations.append("Stochastic: CALL")

    elif (
        k < d and
        pk >= pd_ and
        k > 20
    ):
        put += 10
        confirmations.append("Stochastic: PUT")

    else:
        confirmations.append("Stochastic: NEUTRAL")

    # --------------------------------------------------------
    # 7 MOMENTUM - 10
    # --------------------------------------------------------

    if (
        last["momentum"] > 0 and
        last["roc"] > 0
    ):
        call += 10
        confirmations.append("Momentum: CALL")

    elif (
        last["momentum"] < 0 and
        last["roc"] < 0
    ):
        put += 10
        confirmations.append("Momentum: PUT")

    else:
        confirmations.append("Momentum: NEUTRAL")

    # --------------------------------------------------------
    # 8 CANDLE - 10
    # --------------------------------------------------------

    body_ratio = float(
        last["body_ratio"]
    )

    if (
        last["close"] >
        last["open"] and
        body_ratio >= 0.60
    ):
        call += 10
        confirmations.append("Candle: CALL")

    elif (
        last["close"] <
        last["open"] and
        body_ratio >= 0.60
    ):
        put += 10
        confirmations.append("Candle: PUT")

    else:
        confirmations.append("Candle: NEUTRAL")

    # --------------------------------------------------------
    # 9 TREND STRENGTH - 12
    # --------------------------------------------------------

    current_atr = float(last["atr"])

    distance = abs(
        last["ema21"] -
        last["ema50"]
    )

    if (
        current_atr > 0 and
        distance / current_atr >= 0.50 and
        last["ema21"] >
        last["ema50"] and
        last["ema21_slope"] > 0
    ):
        call += 12
        confirmations.append("Trend Strength: CALL")

    elif (
        current_atr > 0 and
        distance / current_atr >= 0.50 and
        last["ema21"] <
        last["ema50"] and
        last["ema21_slope"] < 0
    ):
        put += 12
        confirmations.append("Trend Strength: PUT")

    else:
        confirmations.append("Trend Strength: NEUTRAL")

    # --------------------------------------------------------
    # TOTAL
    # --------------------------------------------------------

    maximum_score = 110

    if call > put:
        direction = "CALL"
        raw_score = call

    elif put > call:
        direction = "PUT"
        raw_score = put

    else:
        return None

    score = round(
        raw_score /
        maximum_score *
        100,
        2
    )

    edge = abs(call - put)

    logger.info(
        "Analysis | CALL=%s PUT=%s SCORE=%.2f EDGE=%s",
        call,
        put,
        score,
        edge
    )

    if score < MIN_SCORE:
        return None

    if edge < MIN_SCORE_EDGE:
        return None

    # اتجاه EMA يجب أن يؤكد الصفقة
    if direction == "CALL":
        if not last["ema9"] > last["ema21"]:
            return None
    else:
        if not last["ema9"] < last["ema21"]:
            return None

    return {
        "direction": direction,
        "score": score,
        "call_score": call,
        "put_score": put,
        "edge": edge,
        "strategies": confirmations,
        "signal_candle_timestamp": int(
            last["timestamp"]
        ),
        "reference_price": float(
            last["close"]
        )
    }


# ============================================================
# CHART
# ============================================================

def generate_chart(df, title):
    try:
        chart = df.tail(70).copy()

        if chart.empty:
            return None

        fig, ax = plt.subplots(
            figsize=(13, 7),
            dpi=140
        )

        times = [
            datetime.fromtimestamp(
                float(ts),
                tz=timezone.utc
            )
            for ts in chart["timestamp"]
        ]

        x = mdates.date2num(times)

        if len(x) > 1:
            width = np.median(
                np.diff(x)
            ) * 0.70
        else:
            width = 0.0004

        # Candles
        for i, (_, row) in enumerate(
            chart.iterrows()
        ):
            xx = x[i]

            o = float(row["open"])
            h = float(row["high"])
            l = float(row["low"])
            c = float(row["close"])

            if c >= o:
                candle_color = "#16a34a"
            else:
                candle_color = "#dc2626"

            ax.plot(
                [xx, xx],
                [l, h],
                color=candle_color,
                linewidth=1
            )

            rectangle = Rectangle(
                (
                    xx - width / 2,
                    min(o, c)
                ),
                width,
                max(abs(c - o), 0.000001),
                facecolor=candle_color,
                edgecolor=candle_color
            )

            ax.add_patch(rectangle)

        # EMA
        ind = indicators(chart)

        if ind is not None and not ind.empty:
            itimes = [
                datetime.fromtimestamp(
                    float(ts),
                    tz=timezone.utc
                )
                for ts in ind["timestamp"]
            ]

            ix = mdates.date2num(itimes)

            ax.plot(
                ix,
                ind["ema9"],
                linewidth=1.1,
                label="EMA 9"
            )

            ax.plot(
                ix,
                ind["ema21"],
                linewidth=1.1,
                label="EMA 21"
            )

            ax.plot(
                ix,
                ind["ema50"],
                linewidth=1.1,
                label="EMA 50"
            )

        ax.set_title(
            f"{SYMBOL_NAME} | {title}"
        )

        ax.set_ylabel("Price")

        ax.grid(
            True,
            linestyle="--",
            alpha=0.30
        )

        ax.xaxis.set_major_formatter(
            mdates.DateFormatter(
                "%H:%M",
                tz=timezone.utc
            )
        )

        ax.legend(
            loc="upper left"
        )

        fig.autofmt_xdate()

        plt.tight_layout()

        filename = (
            f"eurusd_{int(time.time())}.png"
        )

        plt.savefig(
            filename,
            dpi=140,
            bbox_inches="tight"
        )

        plt.close(fig)

        return filename

    except Exception as e:
        logger.exception(
            "Chart error: %s",
            e
        )
        return None


# ============================================================
# SIGNAL MESSAGE
# ============================================================

def build_signal(signal):
    signal_candle = int(
        signal["signal_candle_timestamp"]
    )

    # الشمعة التالية هي شمعة الدخول
    entry = signal_candle + 60

    # مدة الصفقة دقيقتان
    expiry = entry + 120

    if signal["direction"] == "CALL":
        direction_text = "🟢 CALL / UP"
    else:
        direction_text = "🔴 PUT / DOWN"

    lines = [
        "🎯 <b>EUR/USD — REAL FOREX</b>",
        "",
        "📡 <b>السوق:</b> Forex الحقيقي",
        "💱 <b>الزوج:</b> EUR/USD",
        "🕯️ <b>الفريم:</b> 1 دقيقة",
        "🚫 <b>OTC:</b> لا",
        "🚫 <b>شموع وهمية:</b> لا",
        "",
        f"🚀 <b>الإشارة:</b> {direction_text}",
        f"⭐ <b>Score:</b> {signal['score']:.1f}/100",
        f"🟢 <b>CALL Score:</b> {signal['call_score']}",
        f"🔴 <b>PUT Score:</b> {signal['put_score']}",
        "",
        f"🕯️ <b>الشمعة المحللة:</b> "
        f"{fmt_time(signal_candle)}",
        f"🔔 <b>دخول الصفقة:</b> "
        f"{fmt_time(entry)}",
        f"🏁 <b>انتهاء الصفقة:</b> "
        f"{fmt_time(expiry)}",
        "⏱️ <b>المدة:</b> دقيقتان",
        "",
        f"💵 <b>السعر المرجعي:</b> "
        f"{signal['reference_price']:.5f}",
        "",
        "🧠 <b>التأكيدات:</b>"
    ]

    for item in signal["strategies"]:
        if "CALL" in item:
            icon = "🟢"
        elif "PUT" in item:
            icon = "🔴"
        else:
            icon = "⚪"

        lines.append(
            f"{icon} {item}"
        )

    lines.extend([
        "",
        "🛡️ <b>بدون مضاعفات</b>",
        "🤖 <b>تنفيذ الصفقات:</b> متوقف",
        "",
        "⚠️ التقييم الفني ليس ضمانًا للربح."
    ])

    return "\n".join(lines), entry, expiry


# ============================================================
# WAIT
# ============================================================

async def wait_until(timestamp):
    while True:
        remaining = timestamp - time.time()

        if remaining <= 0:
            return

        await asyncio.sleep(
            min(
                0.5,
                max(0.05, remaining)
            )
        )


# ============================================================
# FIND CANDLE
# ============================================================

def find_candle(df, timestamp):
    if df is None or df.empty:
        return None

    result = df[
        df["timestamp"] == int(timestamp)
    ]

    if result.empty:
        return None

    return result.iloc[0]


# ============================================================
# RESULT
# ============================================================

def calculate_result(
    direction,
    entry_price,
    exit_price
):
    difference = exit_price - entry_price

    if abs(difference) <= 1e-8:
        return "TIE"

    if direction == "CALL":
        return "WIN" if difference > 0 else "LOSS"

    if direction == "PUT":
        return "WIN" if difference < 0 else "LOSS"

    return "TIE"


# ============================================================
# RESULT CHECK
# ============================================================

async def get_trade_result(
    entry_timestamp,
    expiry_timestamp
):
    await wait_until(
        expiry_timestamp +
        RESULT_GRACE_SECONDS
    )

    for attempt in range(1, 9):

        raw = await asyncio.to_thread(
            fetch_candles
        )

        df = closed_candles(raw)

        if df is not None:

            entry_candle = find_candle(
                df,
                entry_timestamp
            )

            # الصفقة دقيقتان:
            # الدقيقة الأولى تبدأ عند entry
            # الدقيقة الثانية تبدأ عند entry + 60
            expiry_candle = find_candle(
                df,
                entry_timestamp + 60
            )

            if (
                entry_candle is not None and
                expiry_candle is not None
            ):
                entry_price = float(
                    entry_candle["open"]
                )

                exit_price = float(
                    expiry_candle["close"]
                )

                return (
                    entry_price,
                    exit_price,
                    df
                )

        logger.info(
            "Waiting for result candles... attempt=%s",
            attempt
        )

        await asyncio.sleep(3)

    return None, None, None


# ============================================================
# SEND RESULT
# ============================================================

def send_result(
    signal,
    entry_price,
    exit_price,
    expiry,
    df,
    result
):
    global total_wins
    global total_losses
    global total_ties

    if result == "WIN":
        total_wins += 1
        result_text = "WIN 🟢"

    elif result == "LOSS":
        total_losses += 1
        result_text = "LOSS 🔴"

    else:
        total_ties += 1
        result_text = "TIE ⚪"

    save_stats()

    completed = (
        total_wins +
        total_losses +
        total_ties
    )

    message = (
        "📊 <b>نتيجة EUR/USD REAL FOREX</b>\n\n"
        f"🚀 <b>الإشارة:</b> "
        f"{signal['direction']}\n"
        f"⭐ <b>Score:</b> "
        f"{signal['score']:.1f}/100\n\n"
        f"🏁 <b>النتيجة:</b> "
        f"<b>{result_text}</b>\n\n"
        f"💵 <b>الدخول:</b> "
        f"{entry_price:.5f}\n"
        f"🏁 <b>الانتهاء:</b> "
        f"{exit_price:.5f}\n\n"
        f"⏰ <b>وقت الانتهاء:</b> "
        f"{fmt_time(expiry)}\n\n"
        "📈 <b>الإحصائيات</b>\n"
        f"🟢 WIN: {total_wins}\n"
        f"🔴 LOSS: {total_losses}\n"
        f"⚪ TIE: {total_ties}\n"
        f"🔢 الإشارات: {total_signals}\n"
        f"📊 المكتملة: {completed}\n"
        f"🎯 نسبة الفوز: {win_rate():.2f}%\n\n"
        "🛡️ <b>بدون مضاعفات</b>"
    )

    chart = generate_chart(
        df,
        f"RESULT {result}"
    )

    if chart:
        telegram_photo(
            chart,
            message
        )

        try:
            os.remove(chart)
        except OSError:
            pass

    else:
        telegram_message(message)


# ============================================================
# PROCESS SIGNAL
# ============================================================

async def process_signal(signal, df):
    global total_signals

    message, entry, expiry = build_signal(
        signal
    )

    remaining = entry - time.time()

    logger.info(
        "Signal=%s | entry=%s | remaining=%.1fs",
        signal["direction"],
        fmt_time(entry),
        remaining
    )

    # لا نرسل إذا تأخر النظام بشكل واضح
    if remaining < 40:
        logger.warning(
            "Signal too late. Skipped."
        )
        return

    # ننتظر حتى يصبح الدخول قريبًا من دقيقة
    if remaining > 65:
        await wait_until(
            entry - 60
        )

    remaining = entry - time.time()

    if remaining < 40 or remaining > 65:
        logger.warning(
            "Invalid signal timing. Skipped."
        )
        return

    total_signals += 1
    save_stats()

    chart = generate_chart(
        df,
        (
            f"{signal['direction']} "
            f"| SCORE {signal['score']:.1f}/100"
        )
    )

    if chart:
        telegram_photo(
            chart,
            message
        )

        try:
            os.remove(chart)
        except OSError:
            pass

    else:
        telegram_message(message)

    # انتظار انتهاء الصفقة
    (
        entry_price,
        exit_price,
        result_df
    ) = await get_trade_result(
        entry,
        expiry
    )

    if (
        entry_price is None or
        exit_price is None or
        result_df is None
    ):
        telegram_message(
            "⚠️ <b>تعذر تأكيد نتيجة EUR/USD</b>\n\n"
            f"🚀 الإشارة: {signal['direction']}\n"
            f"⭐ Score: {signal['score']:.1f}/100\n\n"
            "لم يتم احتساب WIN أو LOSS "
            "لأن بيانات انتهاء الصفقة لم تصل "
            "بشكل مؤكد."
        )
        return

    result = calculate_result(
        signal["direction"],
        entry_price,
        exit_price
    )

    send_result(
        signal,
        entry_price,
        exit_price,
        expiry,
        result_df,
        result
    )


# ============================================================
# START MESSAGE
# ============================================================

def send_start():
    message = (
        "🤖 <b>EUR/USD REAL FOREX BOT</b>\n\n"
        "✅ السوق: Forex الحقيقي\n"
        "💱 الزوج: EUR/USD\n"
        "🕯️ الفريم: 1 دقيقة\n"
        "⏱️ مدة التوصية: دقيقتان\n"
        "🔔 التنبيه: قبل الدخول بحوالي دقيقة\n"
        "⭐ أقل Score: 80/100\n"
        "🚫 OTC: متوقف\n"
        "🚫 شموع وهمية: متوقفة\n"
        "🛡️ المضاعفات: متوقفة\n"
        "🤖 تنفيذ التداول: متوقف\n\n"
        "⏳ <b>بدأت مراقبة EUR/USD...</b>"
    )

    telegram_message(message)


# ============================================================
# VALIDATE
# ============================================================

def validate():
    missing = []

    if not TWELVE_DATA_API_KEY:
        missing.append("TWELVE_DATA_API_KEY")

    if not TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")

    if not TELEGRAM_CHAT_ID:
        missing.append("TELEGRAM_CHAT_ID")

    if missing:
        raise RuntimeError(
            "Missing environment variables: "
            + ", ".join(missing)
        )


# ============================================================
# INITIAL TEST
# ============================================================

def initial_test():
    logger.info(
        "Testing Twelve Data EUR/USD..."
    )

    raw = fetch_candles()

    df = closed_candles(raw)

    if df is None:
        raise RuntimeError(
            "Could not obtain enough closed EUR/USD candles."
        )

    last = df.iloc[-1]

    logger.info(
        "DATA OK | candles=%s | "
        "time=%s | close=%.5f",
        len(df),
        fmt_datetime(last["timestamp"]),
        float(last["close"])
    )

    return df


# ============================================================
# MAIN
# ============================================================

async def main():
    global last_processed_candle

    validate()

    load_stats()

    logger.info("=" * 60)
    logger.info("EUR/USD REAL FOREX SIGNAL BOT")
    logger.info("No OTC")
    logger.info("No Pocket Option")
    logger.info("No PO_SSID")
    logger.info("No fake candles")
    logger.info("No trade execution")
    logger.info("Trade duration = 2 minutes")
    logger.info("Minimum score = 80/100")
    logger.info("=" * 60)

    initial_test()

    send_start()

    while True:

        try:
            raw = await asyncio.to_thread(
                fetch_candles
            )

            df = closed_candles(raw)

            if df is None:
                logger.warning(
                    "No sufficient closed candles."
                )

                await asyncio.sleep(
                    POLL_SECONDS
                )

                continue

            latest = df.iloc[-1]

            timestamp = int(
                latest["timestamp"]
            )

            logger.info(
                "Closed candle | %s | "
                "O=%.5f H=%.5f L=%.5f C=%.5f",
                fmt_datetime(timestamp),
                float(latest["open"]),
                float(latest["high"]),
                float(latest["low"]),
                float(latest["close"])
            )

            # نفس الشمعة
            if last_processed_candle == timestamp:
                await asyncio.sleep(
                    POLL_SECONDS
                )
                continue

            last_processed_candle = timestamp

            # تحليل
            signal = analyze(df)

            if signal is None:
                logger.info(
                    "No >=80 score opportunity."
                )

                await asyncio.sleep(
                    POLL_SECONDS
                )

                continue

            logger.info(
                "STRONG SIGNAL: %s | %.1f/100",
                signal["direction"],
                signal["score"]
            )

            await process_signal(
                signal,
                df
            )

            await asyncio.sleep(2)

        except KeyboardInterrupt:
            logger.info("Stopped.")
            break

        except Exception as e:
            logger.exception(
                "Main loop error: %s",
                e
            )

            try:
                telegram_message(
                    "⚠️ <b>خطأ في بوت EUR/USD</b>\n\n"
                    f"<code>{str(e)[:800]}</code>\n\n"
                    "🔄 سيحاول البوت المتابعة."
                )
            except Exception:
                pass

            await asyncio.sleep(15)


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":
    try:
        asyncio.run(main())

    except KeyboardInterrupt:
        logger.info("Bot terminated.")

    except Exception as e:
        logger.exception(
            "Fatal error: %s",
            e
        )
        raise
