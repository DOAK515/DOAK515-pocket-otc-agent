import os
import json
import time
import asyncio
import logging
from datetime import datetime

import requests
import pandas as pd
import numpy as np
import pytz

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle

from BinaryOptionsToolsV2.pocketoption import PocketOptionAsync


# ============================================================
# CONFIG
# ============================================================

PO_SSID = (
    os.getenv("PO_SSID", "").strip()
    or os.getenv("POCKET_OPTION_SSID", "").strip()
)

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN", ""
).strip()

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID", ""
).strip()

ASSET = "EURUSD_otc"
ASSET_NAME = "EUR/USD (OTC)"

CANDLE_PERIOD = 60
HISTORY_CANDLES = 150
MIN_CANDLES = 60

# لا توصية إلا عند 7 من 9
MIN_CONFIRMATIONS = 7

# عدد ثواني انتظار نتيجة شمعة الصفقة
RESULT_GRACE_SECONDS = 5

TURKEY_TZ = pytz.timezone("Europe/Istanbul")

STATS_FILE = "trading_stats.json"


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("EURUSD_OTC_BOT")


# ============================================================
# GLOBAL
# ============================================================

api = None

total_wins = 0
total_losses = 0
total_ties = 0
total_signals = 0


# ============================================================
# TELEGRAM
# ============================================================

def telegram_message(text):

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("Telegram secrets are missing.")
        return False

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    try:

        r = requests.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            },
            timeout=20
        )

        r.raise_for_status()

        return True

    except Exception as e:

        logger.error(
            "Telegram message error: %s",
            e
        )

        return False


def telegram_photo(path, caption):

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    if not path or not os.path.exists(path):
        return False

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    )

    try:

        with open(path, "rb") as f:

            r = requests.post(
                url,
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "caption": caption,
                    "parse_mode": "HTML"
                },
                files={
                    "photo": f
                },
                timeout=30
            )

        r.raise_for_status()

        return True

    except Exception as e:

        logger.error(
            "Telegram photo error: %s",
            e
        )

        return False


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

        with open(
            STATS_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

        total_wins = int(data.get("wins", 0))
        total_losses = int(data.get("losses", 0))
        total_ties = int(data.get("ties", 0))
        total_signals = int(data.get("signals", 0))

    except Exception as e:

        logger.warning(
            "Statistics load error: %s",
            e
        )


def save_stats():

    data = {
        "wins": total_wins,
        "losses": total_losses,
        "ties": total_ties,
        "signals": total_signals,
        "updated_at": datetime.now(
            TURKEY_TZ
        ).isoformat()
    }

    try:

        with open(
            STATS_FILE,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2
            )

    except Exception as e:

        logger.warning(
            "Statistics save error: %s",
            e
        )


def win_rate():

    finished = (
        total_wins +
        total_losses
    )

    if finished == 0:
        return 0.0

    return (
        total_wins /
        finished
    ) * 100


# ============================================================
# CANDLE NORMALIZATION
# ============================================================

def normalize_candles(raw):

    if raw is None:
        return None

    try:

        if isinstance(raw, pd.DataFrame):

            df = raw.copy()

        elif isinstance(raw, dict):

            # بعض إصدارات المكتبة قد تعيد
            # قاموسًا يحتوي على candles/data
            for key in (
                "candles",
                "data",
                "history",
                "result"
            ):

                if key in raw:

                    raw = raw[key]
                    break

            df = pd.DataFrame(raw)

        else:

            df = pd.DataFrame(raw)

    except Exception as e:

        logger.error(
            "DataFrame conversion error: %s",
            e
        )

        return None

    if df.empty:
        return None

    df = df.copy()

    # --------------------------------------------------------
    # Normalize names
    # --------------------------------------------------------

    rename = {}

    for col in df.columns:

        c = str(col).lower().strip()

        if c in (
            "time",
            "timestamp",
            "at",
            "date",
            "created_at"
        ):

            rename[col] = "timestamp"

        elif c in ("open", "o"):
            rename[col] = "open"

        elif c in ("high", "h", "max"):
            rename[col] = "high"

        elif c in ("low", "l", "min"):
            rename[col] = "low"

        elif c in ("close", "c"):
            rename[col] = "close"

    df.rename(
        columns=rename,
        inplace=True
    )

    required = [
        "timestamp",
        "open",
        "high",
        "low",
        "close"
    ]

    missing = [
        c for c in required
        if c not in df.columns
    ]

    if missing:

        logger.warning(
            "Missing candle fields: %s",
            missing
        )

        logger.warning(
            "Received columns: %s",
            list(df.columns)
        )

        return None

    # --------------------------------------------------------
    # Numeric
    # --------------------------------------------------------

    for c in (
        "timestamp",
        "open",
        "high",
        "low",
        "close"
    ):

        df[c] = pd.to_numeric(
            df[c],
            errors="coerce"
        )

    df.dropna(
        subset=required,
        inplace=True
    )

    if df.empty:
        return None

    # --------------------------------------------------------
    # Timestamp normalization
    # --------------------------------------------------------

    median_ts = df["timestamp"].median()

    if median_ts > 10_000_000_000:

        df["timestamp"] /= 1000.0

    # --------------------------------------------------------
    # Sort / unique
    # --------------------------------------------------------

    df.sort_values(
        "timestamp",
        inplace=True
    )

    df.drop_duplicates(
        subset=["timestamp"],
        keep="last",
        inplace=True
    )

    return df.reset_index(drop=True)


# ============================================================
# REAL POCKET OPTION CANDLES
# ============================================================

async def get_real_candles():

    global api

    if api is None:
        return None

    try:

        # API الحالية توفر get_candles
        raw = await api.get_candles(
            ASSET,
            CANDLE_PERIOD,
            HISTORY_CANDLES * CANDLE_PERIOD
        )

        df = normalize_candles(raw)

        if df is None:

            logger.warning(
                "Pocket Option returned unusable candle data."
            )

            return None

        logger.info(
            "Pocket Option returned %s candles.",
            len(df)
        )

        return df.tail(
            HISTORY_CANDLES
        ).reset_index(drop=True)

    except Exception as e:

        logger.exception(
            "Pocket Option candle request failed: %s",
            e
        )

        return None


# ============================================================
# CLOSED CANDLES ONLY
# ============================================================

def get_closed_candles(df):

    if df is None or df.empty:
        return None

    df = normalize_candles(df)

    if df is None:
        return None

    now = int(time.time())

    current_minute = (
        now // CANDLE_PERIOD
    ) * CANDLE_PERIOD

    # حذف الشمعة التي ما زالت تتكون
    closed = df[
        df["timestamp"] <
        current_minute
    ].copy()

    if len(closed) < MIN_CANDLES:

        logger.warning(
            "Only %s closed candles. Need %s.",
            len(closed),
            MIN_CANDLES
        )

        return None

    return closed.tail(
        HISTORY_CANDLES
    ).reset_index(drop=True)


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
# INDICATORS
# ============================================================

def indicators(df):

    d = df.copy()

    close = d["close"]

    d["ema5"] = close.ewm(
        span=5,
        adjust=False
    ).mean()

    d["ema9"] = close.ewm(
        span=9,
        adjust=False
    ).mean()

    d["ema21"] = close.ewm(
        span=21,
        adjust=False
    ).mean()

    d["ema50"] = close.ewm(
        span=50,
        adjust=False
    ).mean()

    d["rsi"] = rsi(
        close,
        14
    )

    ema12 = close.ewm(
        span=12,
        adjust=False
    ).mean()

    ema26 = close.ewm(
        span=26,
        adjust=False
    ).mean()

    d["macd"] = (
        ema12 -
        ema26
    )

    d["macd_signal"] = (
        d["macd"]
        .ewm(
            span=9,
            adjust=False
        )
        .mean()
    )

    d["macd_hist"] = (
        d["macd"] -
        d["macd_signal"]
    )

    d["bb_mid"] = (
        close
        .rolling(20)
        .mean()
    )

    std = (
        close
        .rolling(20)
        .std()
    )

    d["bb_upper"] = (
        d["bb_mid"] +
        2 * std
    )

    d["bb_lower"] = (
        d["bb_mid"] -
        2 * std
    )

    lowest = (
        d["low"]
        .rolling(14)
        .min()
    )

    highest = (
        d["high"]
        .rolling(14)
        .max()
    )

    denominator = (
        highest -
        lowest
    ).replace(0, np.nan)

    d["stoch_k"] = (
        100 *
        (close - lowest) /
        denominator
    )

    d["stoch_d"] = (
        d["stoch_k"]
        .rolling(3)
        .mean()
    )

    previous_close = close.shift(1)

    tr = pd.concat(
        [
            d["high"] - d["low"],
            (
                d["high"] -
                previous_close
            ).abs(),
            (
                d["low"] -
                previous_close
            ).abs()
        ],
        axis=1
    ).max(axis=1)

    d["atr"] = (
        tr
        .rolling(14)
        .mean()
    )

    d["momentum"] = (
        close -
        close.shift(4)
    )

    d.replace(
        [np.inf, -np.inf],
        np.nan,
        inplace=True
    )

    d.dropna(
        inplace=True
    )

    return d.reset_index(drop=True)


# ============================================================
# 9 STRATEGIES
# ============================================================

def strategy_votes(df):

    d = indicators(df)

    if d is None or len(d) < 50:
        return None

    last = d.iloc[-1]
    prev = d.iloc[-2]

    votes = {}

    # 1 EMA
    if (
        last.ema5 >
        last.ema9 >
        last.ema21 >
        last.ema50
    ):

        votes["EMA Trend"] = "CALL"

    elif (
        last.ema5 <
        last.ema9 <
        last.ema21 <
        last.ema50
    ):

        votes["EMA Trend"] = "PUT"

    else:

        votes["EMA Trend"] = "NEUTRAL"

    # 2 RSI
    if (
        50 < last.rsi < 68
        and last.rsi > prev.rsi
    ):

        votes["RSI"] = "CALL"

    elif (
        32 < last.rsi < 50
        and last.rsi < prev.rsi
    ):

        votes["RSI"] = "PUT"

    else:

        votes["RSI"] = "NEUTRAL"

    # 3 MACD
    if (
        last.macd >
        last.macd_signal
        and
        last.macd_hist > 0
    ):

        votes["MACD"] = "CALL"

    elif (
        last.macd <
        last.macd_signal
        and
        last.macd_hist < 0
    ):

        votes["MACD"] = "PUT"

    else:

        votes["MACD"] = "NEUTRAL"

    # 4 Bollinger
    if (
        last.close >
        last.bb_mid
        and
        last.close <
        last.bb_upper
    ):

        votes["Bollinger"] = "CALL"

    elif (
        last.close <
        last.bb_mid
        and
        last.close >
        last.bb_lower
    ):

        votes["Bollinger"] = "PUT"

    else:

        votes["Bollinger"] = "NEUTRAL"

    # 5 Stochastic
    if (
        last.stoch_k >
        last.stoch_d
        and
        last.stoch_k < 80
    ):

        votes["Stochastic"] = "CALL"

    elif (
        last.stoch_k <
        last.stoch_d
        and
        last.stoch_k > 20
    ):

        votes["Stochastic"] = "PUT"

    else:

        votes["Stochastic"] = "NEUTRAL"

    # 6 Momentum
    if (
        last.momentum > 0
        and
        last.close > last.open
    ):

        votes["Momentum"] = "CALL"

    elif (
        last.momentum < 0
        and
        last.close < last.open
    ):

        votes["Momentum"] = "PUT"

    else:

        votes["Momentum"] = "NEUTRAL"

    # 7 Candle
    candle_body = abs(
        last.close -
        last.open
    )

    candle_range = (
        last.high -
        last.low
    )

    body_ratio = (
        candle_body /
        candle_range
        if candle_range > 0
        else 0
    )

    if (
        last.close >
        last.open
        and
        body_ratio >= 0.55
    ):

        votes[
            "Candle Confirmation"
        ] = "CALL"

    elif (
        last.close <
        last.open
        and
        body_ratio >= 0.55
    ):

        votes[
            "Candle Confirmation"
        ] = "PUT"

    else:

        votes[
            "Candle Confirmation"
        ] = "NEUTRAL"

    # 8 Support / Resistance
    recent_high = (
        d["high"]
        .tail(20)
        .max()
    )

    recent_low = (
        d["low"]
        .tail(20)
        .min()
    )

    distance_low = (
        last.close -
        recent_low
    )

    distance_high = (
        recent_high -
        last.close
    )

    if (
        distance_low >
        distance_high
        and
        last.close >
        last.ema21
    ):

        votes[
            "Support/Resistance"
        ] = "CALL"

    elif (
        distance_high >
        distance_low
        and
        last.close <
        last.ema21
    ):

        votes[
            "Support/Resistance"
        ] = "PUT"

    else:

        votes[
            "Support/Resistance"
        ] = "NEUTRAL"

    # 9 Trend Strength
    atr = float(last.atr)

    distance = abs(
        last.ema21 -
        last.ema50
    )

    if (
        atr > 0
        and
        distance / atr > 0.8
        and
        last.ema21 >
        last.ema50
    ):

        votes[
            "Trend Strength"
        ] = "CALL"

    elif (
        atr > 0
        and
        distance / atr > 0.8
        and
        last.ema21 <
        last.ema50
    ):

        votes[
            "Trend Strength"
        ] = "PUT"

    else:

        votes[
            "Trend Strength"
        ] = "NEUTRAL"

    return votes


# ============================================================
# STRONG SIGNAL
# ============================================================

def strong_signal(df):

    votes = strategy_votes(df)

    if votes is None:
        return None

    calls = sum(
        1 for x in votes.values()
        if x == "CALL"
    )

    puts = sum(
        1 for x in votes.values()
        if x == "PUT"
    )

    logger.info(
        "CONFIRMATIONS: CALL=%s PUT=%s",
        calls,
        puts
    )

    if (
        calls >= MIN_CONFIRMATIONS
        and
        calls > puts
    ):

        return {
            "direction": "CALL",
            "votes": calls,
            "strategies": votes
        }

    if (
        puts >= MIN_CONFIRMATIONS
        and
        puts > calls
    ):

        return {
            "direction": "PUT",
            "votes": puts,
            "strategies": votes
        }

    return None


# ============================================================
# CHART
# ============================================================

def create_chart(
    df,
    title,
    signal=None
):

    try:

        d = df.tail(60).copy()

        if d.empty:
            return None

        fig, ax = plt.subplots(
            figsize=(12, 6),
            dpi=140
        )

        times = [
            datetime.fromtimestamp(
                float(x),
                tz=TURKEY_TZ
            )
            for x in d["timestamp"]
        ]

        xs = mdates.date2num(times)

        if len(xs) > 1:

            width = (
                np.median(
                    np.diff(xs)
                ) * 0.75
            )

        else:

            width = 0.0005

        for i, (_, row) in enumerate(
            d.iterrows()
        ):

            x = xs[i]

            o = float(row.open)
            h = float(row.high)
            l = float(row.low)
            c = float(row.close)

            if c >= o:
                color = "#00c878"
            else:
                color = "#ff3344"

            ax.plot(
                [x, x],
                [l, h],
                color=color,
                linewidth=1
            )

            rect = Rectangle(
                (
                    x - width / 2,
                    min(o, c)
                ),
                width,
                max(
                    abs(c - o),
                    1e-8
                ),
                facecolor=color,
                edgecolor=color
            )

            ax.add_patch(rect)

        if signal:

            last = d.iloc[-1]

            x = xs[-1]

            if signal["direction"] == "CALL":

                ax.annotate(
                    "CALL",
                    xy=(
                        x,
                        float(last.high)
                    ),
                    xytext=(
                        x,
                        float(last.high)
                        +
                        (
                            float(last.high)
                            -
                            float(last.low)
                        ) * 1.5
                    ),
                    ha="center",
                    fontweight="bold",
                    arrowprops={
                        "arrowstyle": "->"
                    }
                )

            else:

                ax.annotate(
                    "PUT",
                    xy=(
                        x,
                        float(last.low)
                    ),
                    xytext=(
                        x,
                        float(last.low)
                        -
                        (
                            float(last.high)
                            -
                            float(last.low)
                        ) * 1.5
                    ),
                    ha="center",
                    fontweight="bold",
                    arrowprops={
                        "arrowstyle": "->"
                    }
                )

        ax.set_title(
            f"{ASSET_NAME} | {title}",
            fontweight="bold"
        )

        ax.grid(
            True,
            linestyle="--",
            alpha=0.3
        )

        ax.xaxis.set_major_formatter(
            mdates.DateFormatter(
                "%H:%M:%S",
                tz=TURKEY_TZ
            )
        )

        fig.autofmt_xdate()

        plt.tight_layout()

        filename = (
            f"eurusd_otc_"
            f"{int(time.time())}.png"
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

def signal_message(
    signal,
    df
):

    last = df.iloc[-1]

    candle_time = int(
        last.timestamp
    )

    entry = (
        candle_time +
        CANDLE_PERIOD
    )

    expiry = (
        entry +
        CANDLE_PERIOD
    )

    entry_dt = datetime.fromtimestamp(
        entry,
        tz=TURKEY_TZ
    )

    expiry_dt = datetime.fromtimestamp(
        expiry,
        tz=TURKEY_TZ
    )

    direction = (
        "🟢 CALL / UP"
        if signal["direction"] == "CALL"
        else
        "🔴 PUT / DOWN"
    )

    lines = [

        "🎯 <b>EUR/USD OTC</b>",
        "",
        "📡 <b>مصدر البيانات:</b> Pocket Option",
        "🕯️ <b>الشموع:</b> حقيقية ومغلقة فقط",
        "🚫 <b>شموع وهمية:</b> لا",
        "",
        f"🚀 <b>الاتجاه:</b> {direction}",
        f"⭐ <b>التأكيدات:</b> {signal['votes']}/9",
        "",
        f"⏰ <b>الدخول:</b> "
        f"{entry_dt.strftime('%H:%M:%S')}",
        f"🏁 <b>الانتهاء:</b> "
        f"{expiry_dt.strftime('%H:%M:%S')}",
        "⏱️ <b>المدة:</b> 1 دقيقة",
        "",
        f"💵 <b>السعر المرجعي:</b> "
        f"{float(last.close):.5f}",
        "",
        "📊 <b>الاستراتيجيات:</b>"
    ]

    for name, vote in signal[
        "strategies"
    ].items():

        icon = (
            "🟢"
            if vote == "CALL"
            else
            "🔴"
            if vote == "PUT"
            else
            "⚪"
        )

        lines.append(
            f"{icon} {name}: {vote}"
        )

    lines.extend(
        [
            "",
            "🛡️ <b>بدون مضاعفات</b>",
            "🤖 <b>التنفيذ الآلي:</b> متوقف",
            "",
            "⚠️ هذه توصية تحليلية وليست ضمانًا للربح."
        ]
    )

    return (
        "\n".join(lines),
        entry,
        expiry,
        float(last.close)
    )


# ============================================================
# WAIT
# ============================================================

async def wait_until(timestamp):

    while True:

        remaining = (
            timestamp -
            time.time()
        )

        if remaining <= 0:
            return

        await asyncio.sleep(
            min(
                1.0,
                max(0.1, remaining)
            )
        )


# ============================================================
# RESULT
# ============================================================

def calculate_result(
    direction,
    entry_price,
    exit_price
):

    if abs(
        exit_price -
        entry_price
    ) < 1e-10:

        return "TIE"

    if direction == "CALL":

        return (
            "WIN"
            if exit_price > entry_price
            else "LOSS"
        )

    if direction == "PUT":

        return (
            "WIN"
            if exit_price < entry_price
            else "LOSS"
        )

    return "TIE"


# ============================================================
# FIND RESULT CANDLE
# ============================================================

def result_exit_price(
    df,
    entry_timestamp
):

    if df is None:
        return None

    d = normalize_candles(df)

    if d is None:
        return None

    matches = d[
        d["timestamp"].round(0)
        ==
        float(entry_timestamp)
    ]

    if matches.empty:
        return None

    return float(
        matches.iloc[0]["close"]
    )


# ============================================================
# RESULT
# ============================================================

async def wait_result(
    entry_timestamp
):

    global api

    expiry = (
        entry_timestamp +
        CANDLE_PERIOD
    )

    await wait_until(
        expiry +
        RESULT_GRACE_SECONDS
    )

    for attempt in range(1, 10):

        logger.info(
            "Checking result candle: attempt %s/9",
            attempt
        )

        df = await get_real_candles()

        closed = get_closed_candles(df)

        if closed is not None:

            price = result_exit_price(
                closed,
                entry_timestamp
            )

            if price is not None:

                return (
                    closed,
                    price
                )

        await asyncio.sleep(3)

    return None, None


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

    expiry_dt = datetime.fromtimestamp(
        expiry,
        tz=TURKEY_TZ
    )

    message = (

        "📊 <b>نتيجة EUR/USD OTC</b>\n\n"

        f"🚀 <b>الإشارة:</b> "
        f"{signal['direction']}\n"

        f"🏁 <b>النتيجة:</b> "
        f"<b>{result_text}</b>\n\n"

        f"💵 <b>دخول:</b> "
        f"{entry_price:.5f}\n"

        f"💵 <b>إغلاق:</b> "
        f"{exit_price:.5f}\n"

        f"⏰ <b>الانتهاء:</b> "
        f"{expiry_dt.strftime('%H:%M:%S')}\n\n"

        "📈 <b>الإحصائيات</b>\n"

        f"🟢 WIN: {total_wins}\n"
        f"🔴 LOSS: {total_losses}\n"
        f"⚪ TIE: {total_ties}\n"
        f"🔢 الإشارات: {total_signals}\n"
        f"🎯 نسبة الفوز: {win_rate():.2f}%"
    )

    chart = create_chart(
        df,
        f"RESULT: {result}"
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
# START MESSAGE
# ============================================================

def send_start():

    telegram_message(

        "🤖 <b>بوت EUR/USD OTC بدأ</b>\n\n"

        "📡 <b>المصدر:</b> Pocket Option\n"
        "🕯️ <b>البيانات:</b> شموع حقيقية\n"
        "🚫 <b>شموع وهمية:</b> متوقفة\n"
        "📊 <b>الفريم:</b> 1 دقيقة\n"
        "🎯 <b>الإشارة:</b> 7/9 تأكيدات على الأقل\n"
        "🛡️ <b>مضاعفات:</b> لا\n"
        "🤖 <b>التداول الآلي:</b> متوقف\n\n"

        "⏳ <b>جاري مراقبة EUR/USD OTC...</b>"
    )


# ============================================================
# CONNECT
# ============================================================

async def connect():

    global api

    if not PO_SSID:

        raise RuntimeError(
            "PO_SSID غير موجود في GitHub Secrets."
        )

    logger.info(
        "Connecting to Pocket Option..."
    )

    try:

        api = await PocketOptionAsync(
            PO_SSID
        )

        # ننتظر تهيئة الاتصال
        await asyncio.sleep(5)

        logger.info(
            "Pocket Option API initialized."
        )

        # اختبار أولي للشموع
        test = await get_real_candles()

        closed = get_closed_candles(test)

        if closed is None:

            raise RuntimeError(
                "تم الاتصال لكن لم تصل شموع EURUSD_otc."
            )

        logger.info(
            "Real EURUSD_otc candles confirmed: %s",
            len(closed)
        )

        return True

    except Exception as e:

        logger.exception(
            "Pocket Option connection failed."
        )

        api = None

        raise RuntimeError(
            f"Pocket Option connection failed: {e}"
        )


# ============================================================
# MAIN
# ============================================================

async def main():

    global total_signals

    load_stats()

    # --------------------------------------------------------
    # Validate
    # --------------------------------------------------------

    if not PO_SSID:
        raise RuntimeError("PO_SSID missing.")

    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN missing."
        )

    if not TELEGRAM_CHAT_ID:
        raise RuntimeError(
            "TELEGRAM_CHAT_ID missing."
        )

    # --------------------------------------------------------
    # Connect
    # --------------------------------------------------------

    await connect()

    send_start()

    last_candle = None

    logger.info(
        "=========================================="
    )

    logger.info(
        "EUR/USD OTC SIGNAL BOT IS RUNNING"
    )

    logger.info(
        "Asset: %s",
        ASSET
    )

    logger.info(
        "Period: %s seconds",
        CANDLE_PERIOD
    )

    logger.info(
        "Minimum confirmations: %s/9",
        MIN_CONFIRMATIONS
    )

    logger.info(
        "=========================================="
    )

    # --------------------------------------------------------
    # LOOP
    # --------------------------------------------------------

    while True:

        try:

            df_raw = await get_real_candles()

            df = get_closed_candles(
                df_raw
            )

            if df is None:

                logger.info(
                    "⏳ في انتظار شموع EUR/USD OTC الحقيقية..."
                )

                await asyncio.sleep(5)

                continue

            latest = int(
                df.iloc[-1]["timestamp"]
            )

            latest_dt = datetime.fromtimestamp(
                latest,
                tz=TURKEY_TZ
            )

            logger.info(
                "Latest CLOSED candle: %s",
                latest_dt.strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            )

            # نفس الشمعة لا تحلل مرتين
            if latest == last_candle:

                await asyncio.sleep(3)

                continue

            last_candle = latest

            logger.info(
                "Analyzing new CLOSED candle..."
            )

            signal = strong_signal(df)

            if signal is None:

                logger.info(
                    "No strong signal."
                )

                await asyncio.sleep(3)

                continue

            # ------------------------------------------------
            # Build
            # ------------------------------------------------

            (
                message,
                entry,
                expiry,
                entry_price
            ) = signal_message(
                signal,
                df
            )

            # تأكد أن الإشارة للشمعة القادمة
            now = time.time()

            if entry <= now + 2:

                logger.warning(
                    "Signal is too late. Skipping."
                )

                continue

            # ------------------------------------------------
            # Count
            # ------------------------------------------------

            total_signals += 1

            save_stats()

            logger.info(
                "=========================================="
            )

            logger.info(
                "🔥 SIGNAL %s | %s/9",
                signal["direction"],
                signal["votes"]
            )

            logger.info(
                "Entry: %s",
                datetime.fromtimestamp(
                    entry,
                    tz=TURKEY_TZ
                ).strftime("%H:%M:%S")
            )

            logger.info(
                "Expiry: %s",
                datetime.fromtimestamp(
                    expiry,
                    tz=TURKEY_TZ
                ).strftime("%H:%M:%S")
            )

            logger.info(
                "=========================================="
            )

            # ------------------------------------------------
            # Signal chart
            # ------------------------------------------------

            chart = create_chart(
                df,
                (
                    f"{signal['direction']} "
                    f"| {signal['votes']}/9"
                ),
                signal
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

                telegram_message(
                    message
                )

            # ------------------------------------------------
            # Result
            # ------------------------------------------------

            result_df, exit_price = (
                await wait_result(entry)
            )

            if (
                result_df is None
                or
                exit_price is None
            ):

                telegram_message(

                    "⚠️ <b>تعذر تأكيد نتيجة "
                    "EUR/USD OTC</b>\n\n"

                    f"الإشارة: "
                    f"{signal['direction']}\n\n"

                    "لم تصل شمعة انتهاء الصفقة "
                    "من Pocket Option.\n\n"

                    "🛡️ لم يتم احتساب WIN/LOSS."
                )

                continue

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

            await asyncio.sleep(3)

        except KeyboardInterrupt:

            logger.info(
                "Bot stopped."
            )

            break

        except Exception as e:

            logger.exception(
                "MAIN LOOP ERROR: %s",
                e
            )

            telegram_message(

                "⚠️ <b>خطأ في البوت</b>\n\n"
                f"<code>{str(e)[:700]}</code>\n\n"
                "🔄 سيتم إعادة الاتصال."
            )

            await asyncio.sleep(10)

            try:

                await connect()

            except Exception as reconnect_error:

                logger.error(
                    "Reconnect failed: %s",
                    reconnect_error
                )

                await asyncio.sleep(15)


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    try:

        asyncio.run(
            main()
        )

    except Exception as e:

        logger.exception(
            "FATAL ERROR: %s",
            e
        )

        raise
