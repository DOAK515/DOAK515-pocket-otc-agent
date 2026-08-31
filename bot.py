import os
import time
import json
import logging
import asyncio
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

# 1 minute candles
CANDLE_PERIOD = 60

# 2 hours of historical data
HISTORY_SECONDS = 7200

# Minimum candles required for analysis
MIN_CANDLES = 60

# Strong signal threshold
MIN_CONFIRMATIONS = 7

# Result checking delay
RESULT_GRACE_SECONDS = 5

# Turkey time
TURKEY_TZ = pytz.timezone("Europe/Istanbul")

STATS_FILE = "trading_stats.json"


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(
    "PO_OTC_SIGNAL_BOT"
)


# ============================================================
# GLOBAL STATE
# ============================================================

client = None

total_wins = 0
total_losses = 0
total_ties = 0
total_signals = 0


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

        total_wins = int(
            data.get("wins", 0)
        )

        total_losses = int(
            data.get("losses", 0)
        )

        total_ties = int(
            data.get("ties", 0)
        )

        total_signals = int(
            data.get("signals", 0)
        )

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


def get_win_rate():

    completed = (
        total_wins +
        total_losses
    )

    if completed == 0:
        return 0.0

    return (
        total_wins /
        completed *
        100
    )


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram_message(message):

    if not TELEGRAM_BOT_TOKEN:
        logger.error(
            "TELEGRAM_BOT_TOKEN is missing."
        )
        return False

    if not TELEGRAM_CHAT_ID:
        logger.error(
            "TELEGRAM_CHAT_ID is missing."
        )
        return False

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    try:

        response = requests.post(
            url,
            json=payload,
            timeout=20
        )

        response.raise_for_status()

        return True

    except Exception as e:

        logger.error(
            "Telegram message error: %s",
            e
        )

        return False


def send_telegram_photo(
    photo_path,
    caption
):

    if not os.path.exists(photo_path):
        return False

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    )

    try:

        with open(
            photo_path,
            "rb"
        ) as photo:

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

        return True

    except Exception as e:

        logger.error(
            "Telegram photo error: %s",
            e
        )

        return False


# ============================================================
# POCKET OPTION CONNECTION
# ============================================================

async def connect_pocket_option():

    global client

    if not PO_SSID:

        raise RuntimeError(
            "PO_SSID is missing."
        )

    logger.info(
        "Connecting to Pocket Option..."
    )

    client = PocketOptionAsync(
        PO_SSID
    )

    # Give websocket time to initialize
    await asyncio.sleep(5)

    logger.info(
        "Pocket Option client initialized."
    )


# ============================================================
# FETCH REAL CANDLES
# ============================================================

async def fetch_pocket_option_candles():

    if client is None:
        return None

    try:

        candles = await client.get_candles(
            ASSET,
            CANDLE_PERIOD,
            HISTORY_SECONDS
        )

        if not candles:

            logger.warning(
                "No candles returned."
            )

            return None

        rows = []

        for candle in candles:

            if isinstance(
                candle,
                dict
            ):

                rows.append(candle)

            else:

                rows.append({
                    "time": getattr(
                        candle,
                        "time",
                        None
                    ),
                    "open": getattr(
                        candle,
                        "open",
                        None
                    ),
                    "high": getattr(
                        candle,
                        "high",
                        None
                    ),
                    "low": getattr(
                        candle,
                        "low",
                        None
                    ),
                    "close": getattr(
                        candle,
                        "close",
                        None
                    )
                })

        return normalize_candles(
            pd.DataFrame(rows)
        )

    except Exception as e:

        logger.warning(
            "Pocket Option candle error: %s",
            e
        )

        return None


# ============================================================
# NORMALIZE CANDLES
# ============================================================

def normalize_candles(df):

    if df is None or df.empty:
        return None

    df = df.copy()

    rename = {}

    for column in df.columns:

        name = str(
            column
        ).lower().strip()

        if name in (
            "time",
            "timestamp",
            "created_at",
            "at"
        ):

            rename[column] = "timestamp"

        elif name in (
            "open",
            "o"
        ):

            rename[column] = "open"

        elif name in (
            "high",
            "h",
            "max"
        ):

            rename[column] = "high"

        elif name in (
            "low",
            "l",
            "min"
        ):

            rename[column] = "low"

        elif name in (
            "close",
            "c"
        ):

            rename[column] = "close"

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

    for column in required:

        if column not in df.columns:

            logger.warning(
                "Missing candle field: %s",
                column
            )

            return None

    for column in required[1:]:

        df[column] = pd.to_numeric(
            df[column],
            errors="coerce"
        )

    df["timestamp"] = pd.to_numeric(
        df["timestamp"],
        errors="coerce"
    )

    if df["timestamp"].dropna().empty:
        return None

    # milliseconds -> seconds

    if (
        df["timestamp"]
        .dropna()
        .median()
        > 10_000_000_000
    ):

        df["timestamp"] /= 1000

    df.dropna(
        subset=required,
        inplace=True
    )

    df["timestamp"] = (
        df["timestamp"]
        .astype(float)
        .astype(int)
    )

    df.sort_values(
        "timestamp",
        inplace=True
    )

    df.drop_duplicates(
        "timestamp",
        keep="last",
        inplace=True
    )

    return df.reset_index(
        drop=True
    )


# ============================================================
# CLOSED CANDLES ONLY
# ============================================================

def get_closed_candles(df):

    df = normalize_candles(df)

    if df is None:
        return None

    now = int(
        time.time()
    )

    current_bucket = (
        now //
        CANDLE_PERIOD
    ) * CANDLE_PERIOD

    # IMPORTANT:
    # Never analyze the currently-forming candle.

    closed = df[
        df["timestamp"] <
        current_bucket
    ].copy()

    if len(closed) < MIN_CANDLES:

        logger.info(
            "Not enough candles: %s/%s",
            len(closed),
            MIN_CANDLES
        )

        return None

    return closed.tail(
        120
    ).reset_index(
        drop=True
    )


# ============================================================
# RSI
# ============================================================

def calculate_rsi(
    series,
    period=14
):

    delta = series.diff()

    gain = delta.clip(
        lower=0
    )

    loss = -delta.clip(
        upper=0
    )

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
        avg_loss.replace(
            0,
            np.nan
        )
    )

    return (
        100 -
        100 /
        (1 + rs)
    ).fillna(50)


# ============================================================
# INDICATORS
# ============================================================

def calculate_indicators(df):

    result = df.copy()

    close = result["close"]

    result["ema5"] = (
        close.ewm(
            span=5,
            adjust=False
        ).mean()
    )

    result["ema9"] = (
        close.ewm(
            span=9,
            adjust=False
        ).mean()
    )

    result["ema21"] = (
        close.ewm(
            span=21,
            adjust=False
        ).mean()
    )

    result["ema50"] = (
        close.ewm(
            span=50,
            adjust=False
        ).mean()
    )

    result["rsi"] = calculate_rsi(
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

    result["macd"] = (
        ema12 - ema26
    )

    result["macd_signal"] = (
        result["macd"]
        .ewm(
            span=9,
            adjust=False
        )
        .mean()
    )

    result["macd_hist"] = (
        result["macd"] -
        result["macd_signal"]
    )

    result["bb_mid"] = (
        close
        .rolling(20)
        .mean()
    )

    std = (
        close
        .rolling(20)
        .std()
    )

    result["bb_upper"] = (
        result["bb_mid"] +
        2 * std
    )

    result["bb_lower"] = (
        result["bb_mid"] -
        2 * std
    )

    lowest = (
        result["low"]
        .rolling(14)
        .min()
    )

    highest = (
        result["high"]
        .rolling(14)
        .max()
    )

    denominator = (
        highest - lowest
    ).replace(
        0,
        np.nan
    )

    result["stoch_k"] = (
        100 *
        (close - lowest) /
        denominator
    ).fillna(50)

    result["stoch_d"] = (
        result["stoch_k"]
        .rolling(3)
        .mean()
        .fillna(50)
    )

    tr = pd.concat(
        [
            result["high"] -
            result["low"],

            (
                result["high"] -
                close.shift(1)
            ).abs(),

            (
                result["low"] -
                close.shift(1)
            ).abs()
        ],
        axis=1
    ).max(axis=1)

    result["atr"] = (
        tr.rolling(14).mean()
    )

    result["momentum"] = (
        close -
        close.shift(4)
    )

    return (
        result
        .dropna()
        .reset_index(drop=True)
    )


# ============================================================
# 9 CONFIRMATIONS
# ============================================================

def strategy_votes(df):

    df = calculate_indicators(df)

    if df is None or len(df) < 50:
        return None

    last = df.iloc[-1]
    prev = df.iloc[-2]

    votes = {}

    # 1 EMA TREND

    if (
        last["ema5"] >
        last["ema9"] >
        last["ema21"] >
        last["ema50"]
    ):

        votes["EMA"] = "CALL"

    elif (
        last["ema5"] <
        last["ema9"] <
        last["ema21"] <
        last["ema50"]
    ):

        votes["EMA"] = "PUT"

    else:

        votes["EMA"] = "NEUTRAL"

    # 2 RSI

    if (
        50 < last["rsi"] < 68
        and
        last["rsi"] > prev["rsi"]
    ):

        votes["RSI"] = "CALL"

    elif (
        32 < last["rsi"] < 50
        and
        last["rsi"] < prev["rsi"]
    ):

        votes["RSI"] = "PUT"

    else:

        votes["RSI"] = "NEUTRAL"

    # 3 MACD

    if (
        last["macd"] >
        last["macd_signal"]
        and
        last["macd_hist"] > 0
    ):

        votes["MACD"] = "CALL"

    elif (
        last["macd"] <
        last["macd_signal"]
        and
        last["macd_hist"] < 0
    ):

        votes["MACD"] = "PUT"

    else:

        votes["MACD"] = "NEUTRAL"

    # 4 BOLLINGER

    if (
        last["close"] >
        last["bb_mid"]
        and
        last["close"] <
        last["bb_upper"]
    ):

        votes["Bollinger"] = "CALL"

    elif (
        last["close"] <
        last["bb_mid"]
        and
        last["close"] >
        last["bb_lower"]
    ):

        votes["Bollinger"] = "PUT"

    else:

        votes["Bollinger"] = "NEUTRAL"

    # 5 STOCHASTIC

    if (
        last["stoch_k"] >
        last["stoch_d"]
        and
        last["stoch_k"] < 80
    ):

        votes["Stochastic"] = "CALL"

    elif (
        last["stoch_k"] <
        last["stoch_d"]
        and
        last["stoch_k"] > 20
    ):

        votes["Stochastic"] = "PUT"

    else:

        votes["Stochastic"] = "NEUTRAL"

    # 6 MOMENTUM

    if (
        last["momentum"] > 0
        and
        last["close"] >
        last["open"]
    ):

        votes["Momentum"] = "CALL"

    elif (
        last["momentum"] < 0
        and
        last["close"] <
        last["open"]
    ):

        votes["Momentum"] = "PUT"

    else:

        votes["Momentum"] = "NEUTRAL"

    # 7 CANDLE

    candle_body = abs(
        last["close"] -
        last["open"]
    )

    candle_range = (
        last["high"] -
        last["low"]
    )

    body_ratio = (
        candle_body /
        candle_range
        if candle_range > 0
        else 0
    )

    if (
        last["close"] >
        last["open"]
        and
        body_ratio >= 0.55
    ):

        votes["Candle"] = "CALL"

    elif (
        last["close"] <
        last["open"]
        and
        body_ratio >= 0.55
    ):

        votes["Candle"] = "PUT"

    else:

        votes["Candle"] = "NEUTRAL"

    # 8 SUPPORT / RESISTANCE

    recent_high = (
        df["high"]
        .tail(20)
        .max()
    )

    recent_low = (
        df["low"]
        .tail(20)
        .min()
    )

    price = last["close"]

    if (
        price > last["ema21"]
        and
        (
            price -
            recent_low
        ) >
        (
            recent_high -
            price
        )
    ):

        votes["S/R"] = "CALL"

    elif (
        price < last["ema21"]
        and
        (
            recent_high -
            price
        ) >
        (
            price -
            recent_low
        )
    ):

        votes["S/R"] = "PUT"

    else:

        votes["S/R"] = "NEUTRAL"

    # 9 TREND STRENGTH

    distance = abs(
        last["ema21"] -
        last["ema50"]
    )

    atr = last["atr"]

    if (
        atr > 0
        and
        distance / atr > 0.8
        and
        last["ema21"] >
        last["ema50"]
    ):

        votes["Trend Strength"] = "CALL"

    elif (
        atr > 0
        and
        distance / atr > 0.8
        and
        last["ema21"] <
        last["ema50"]
    ):

        votes["Trend Strength"] = "PUT"

    else:

        votes["Trend Strength"] = "NEUTRAL"

    return votes


# ============================================================
# STRONG SIGNAL
# ============================================================

def get_strong_signal(df):

    votes = strategy_votes(df)

    if votes is None:
        return None

    call_votes = sum(
        value == "CALL"
        for value in votes.values()
    )

    put_votes = sum(
        value == "PUT"
        for value in votes.values()
    )

    # Require 7 confirmations
    if (
        call_votes >= MIN_CONFIRMATIONS
        and
        call_votes > put_votes
    ):

        return {
            "direction": "CALL",
            "votes": call_votes,
            "strategies": votes
        }

    if (
        put_votes >= MIN_CONFIRMATIONS
        and
        put_votes > call_votes
    ):

        return {
            "direction": "PUT",
            "votes": put_votes,
            "strategies": votes
        }

    return None


# ============================================================
# CHART
# ============================================================

def generate_chart(
    df,
    title
):

    try:

        data = df.tail(50).copy()

        fig, ax = plt.subplots(
            figsize=(12, 6),
            dpi=150
        )

        times = [
            datetime.fromtimestamp(
                int(ts),
                tz=TURKEY_TZ
            )
            for ts in data["timestamp"]
        ]

        x = mdates.date2num(times)

        if len(x) > 1:

            width = (
                np.median(
                    np.diff(x)
                ) * 0.75
            )

        else:

            width = 0.0005

        for i, (_, row) in enumerate(
            data.iterrows()
        ):

            o = float(row["open"])
            h = float(row["high"])
            l = float(row["low"])
            c = float(row["close"])

            candle_color = (
                "#00d084"
                if c >= o
                else "#ff3b4d"
            )

            ax.plot(
                [x[i], x[i]],
                [l, h],
                color=candle_color,
                linewidth=1
            )

            body = Rectangle(
                (
                    x[i] - width / 2,
                    min(o, c)
                ),
                width,
                max(
                    abs(c - o),
                    1e-8
                ),
                facecolor=candle_color,
                edgecolor=candle_color
            )

            ax.add_patch(body)

        ax.set_title(
            f"{ASSET_NAME} OTC | {title}",
            fontsize=14,
            fontweight="bold"
        )

        ax.grid(
            True,
            linestyle="--",
            alpha=0.35
        )

        ax.xaxis.set_major_formatter(
            mdates.DateFormatter(
                "%H:%M",
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
            dpi=150,
            bbox_inches="tight"
        )

        plt.close(fig)

        return filename

    except Exception as e:

        logger.error(
            "Chart generation error: %s",
            e
        )

        return None


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
                0.5,
                max(
                    0.05,
                    remaining
                )
            )
        )


# ============================================================
# SIGNAL MESSAGE
# ============================================================

def build_signal_message(
    signal,
    df
):

    candle = df.iloc[-1]

    closed_timestamp = int(
        candle["timestamp"]
    )

    # The next candle starts here.
    entry_timestamp = (
        closed_timestamp +
        CANDLE_PERIOD
    )

    # One minute expiry.
    expiry_timestamp = (
        entry_timestamp +
        CANDLE_PERIOD
    )

    entry_time = datetime.fromtimestamp(
        entry_timestamp,
        tz=TURKEY_TZ
    )

    expiry_time = datetime.fromtimestamp(
        expiry_timestamp,
        tz=TURKEY_TZ
    )

    direction = signal["direction"]

    if direction == "CALL":

        direction_text = "🟢 CALL / UP"

    else:

        direction_text = "🔴 PUT / DOWN"

    lines = [

        "🎯 <b>EUR/USD OTC SIGNAL</b>",
        "",
        "📡 <b>المصدر:</b> Pocket Option",
        "🕯️ <b>البيانات:</b> شموع حقيقية فقط",
        "🚫 <b>الشموع الوهمية:</b> لا",
        "",
        f"🚀 <b>الاتجاه:</b> {direction_text}",
        f"⭐ <b>التأكيدات:</b> {signal['votes']}/9",
        "",
        f"⏰ <b>الدخول:</b> "
        f"{entry_time.strftime('%H:%M:%S')}",
        f"🏁 <b>الانتهاء:</b> "
        f"{expiry_time.strftime('%H:%M:%S')}",
        "⏱️ <b>المدة:</b> 1 دقيقة",
        "",
        f"💵 <b>السعر المرجعي:</b> "
        f"{float(candle['close']):.5f}",
        "",
        "📊 <b>التأكيدات:</b>"
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

    lines.extend([
        "",
        "🛡️ بدون مضاعفات",
        "🤖 التنفيذ الآلي: متوقف",
        "",
        "⚠️ هذه إشارة تحليلية وليست "
        "ضمانًا للربح."
    ])

    return (
        "\n".join(lines),
        entry_timestamp,
        expiry_timestamp
    )


# ============================================================
# RESULT
# ============================================================

def calculate_result(
    direction,
    entry_price,
    exit_price
):

    if exit_price == entry_price:
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
# RESULT MESSAGE
# ============================================================

def build_result_message(
    signal,
    result,
    entry_price,
    exit_price,
    expiry_timestamp
):

    global total_wins
    global total_losses
    global total_ties
    global total_signals

    if result == "WIN":

        result_text = "WIN 🟢"

    elif result == "LOSS":

        result_text = "LOSS 🔴"

    else:

        result_text = "TIE ⚪"

    completed = (
        total_wins +
        total_losses +
        total_ties
    )

    expiry_time = datetime.fromtimestamp(
        expiry_timestamp,
        tz=TURKEY_TZ
    )

    return (

        "📊 <b>EUR/USD OTC RESULT</b>\n\n"

        f"🚀 <b>الإشارة:</b> "
        f"{signal['direction']}\n"

        f"⭐ <b>التأكيدات:</b> "
        f"{signal['votes']}/9\n"

        f"🏁 <b>النتيجة:</b> "
        f"<b>{result_text}</b>\n\n"

        f"💵 <b>سعر الدخول:</b> "
        f"{entry_price:.5f}\n"

        f"🏁 <b>سعر الإغلاق:</b> "
        f"{exit_price:.5f}\n"

        f"⏰ <b>وقت الانتهاء:</b> "
        f"{expiry_time.strftime('%H:%M:%S')}\n\n"

        "📈 <b>الإحصائيات:</b>\n"

        f"🟢 WIN: {total_wins}\n"
        f"🔴 LOSS: {total_losses}\n"
        f"⚪ TIE: {total_ties}\n"
        f"🔢 الإشارات: {total_signals}\n"
        f"📊 الصفقات المحسوبة: {completed}\n"
        f"🎯 نسبة الفوز: "
        f"{get_win_rate():.2f}%"
    )


# ============================================================
# MAIN
# ============================================================

async def main():

    global total_signals
    global total_wins
    global total_losses
    global total_ties

    load_stats()

    # --------------------------------------------------------
    # CHECK SETTINGS
    # --------------------------------------------------------

    if not PO_SSID:

        raise RuntimeError(
            "PO_SSID is missing."
        )

    if not TELEGRAM_BOT_TOKEN:

        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is missing."
        )

    if not TELEGRAM_CHAT_ID:

        raise RuntimeError(
            "TELEGRAM_CHAT_ID is missing."
        )

    # --------------------------------------------------------
    # CONNECT
    # --------------------------------------------------------

    await connect_pocket_option()

    send_telegram_message(

        "🤖 <b>بوت EUR/USD OTC بدأ</b>\n\n"

        "📡 مصدر البيانات: Pocket Option\n"
        "🕯️ شموع حقيقية فقط\n"
        "🚫 شموع وهمية: متوقفة\n"
        "🎯 الإشارة: 7/9 تأكيدات على الأقل\n"
        "⏱️ مدة الصفقة: دقيقة واحدة\n"
        "📸 صورة مع الإشارة\n"
        "📸 صورة مع النتيجة\n"
        "📊 إحصائيات WIN / LOSS / TIE\n"
        "🛡️ بدون مضاعفات\n"
        "🤖 التنفيذ الآلي: متوقف"
    )

    last_processed_candle = None

    while True:

        try:

            # ------------------------------------------------
            # GET REAL DATA
            # ------------------------------------------------

            raw_df = (
                await fetch_pocket_option_candles()
            )

            df = get_closed_candles(
                raw_df
            )

            if df is None:

                logger.info(
                    "Waiting for real OTC candles..."
                )

                await asyncio.sleep(3)

                continue

            latest_timestamp = int(
                df.iloc[-1]["timestamp"]
            )

            # ------------------------------------------------
            # DO NOT ANALYZE SAME CANDLE TWICE
            # ------------------------------------------------

            if (
                last_processed_candle
                ==
                latest_timestamp
            ):

                await asyncio.sleep(2)

                continue

            last_processed_candle = (
                latest_timestamp
            )

            candle_time = datetime.fromtimestamp(
                latest_timestamp,
                tz=TURKEY_TZ
            )

            logger.info(
                "Analyzing closed candle %s",
                candle_time.strftime(
                    "%H:%M:%S"
                )
            )

            # ------------------------------------------------
            # SIGNAL
            # ------------------------------------------------

            signal = get_strong_signal(
                df
            )

            if signal is None:

                logger.info(
                    "No signal. "
                    "Less than 7 confirmations."
                )

                await asyncio.sleep(2)

                continue

            (
                message,
                entry_timestamp,
                expiry_timestamp
            ) = build_signal_message(
                signal,
                df
            )

            # ------------------------------------------------
            # MAKE SURE SIGNAL IS BEFORE ENTRY
            # ------------------------------------------------

            seconds_to_entry = (
                entry_timestamp -
                time.time()
            )

            if seconds_to_entry <= 0:

                logger.warning(
                    "Entry time already passed."
                )

                continue

            # ------------------------------------------------
            # COUNT SIGNAL
            # ------------------------------------------------

            total_signals += 1

            save_stats()

            logger.info(
                "SIGNAL: %s | %s/9 | "
                "%.1f sec to entry",
                signal["direction"],
                signal["votes"],
                seconds_to_entry
            )

            # ------------------------------------------------
            # SIGNAL CHART
            # ------------------------------------------------

            chart = generate_chart(
                df,
                (
                    f"SIGNAL "
                    f"{signal['direction']} "
                    f"{signal['votes']}/9"
                )
            )

            if chart:

                send_telegram_photo(
                    chart,
                    message
                )

                try:
                    os.remove(chart)
                except OSError:
                    pass

            else:

                send_telegram_message(
                    message
                )

            # ------------------------------------------------
            # WAIT FOR ONE-MINUTE EXPIRY
            # ------------------------------------------------

            await wait_until(
                expiry_timestamp +
                RESULT_GRACE_SECONDS
            )

            # ------------------------------------------------
            # GET NEW CANDLES
            # ------------------------------------------------

            result_raw = (
                await fetch_pocket_option_candles()
            )

            result_df = get_closed_candles(
                result_raw
            )

            if result_df is None:

                send_telegram_message(

                    "⚠️ <b>لم يتم تأكيد النتيجة</b>\n\n"

                    "لم تصل بيانات الشمعة "
                    "الحقيقية بعد انتهاء الصفقة.\n\n"

                    "🚫 لم يتم احتساب WIN أو LOSS."
                )

                continue

            # ------------------------------------------------
            # FIND THE EXPIRY CANDLE
            # ------------------------------------------------

            trade_candle = result_df[
                result_df["timestamp"]
                ==
                entry_timestamp
            ]

            if trade_candle.empty:

                send_telegram_message(

                    "⚠️ <b>لم يتم تأكيد النتيجة</b>\n\n"

                    "شمعة انتهاء الصفقة "
                    "غير موجودة في البيانات.\n\n"

                    "🚫 لم يتم تغيير الإحصائيات."
                )

                continue

            candle = trade_candle.iloc[0]

            # IMPORTANT:
            # The opening price of the target candle
            # is a better reference for the actual
            # beginning of the one-minute candle.

            actual_entry_price = float(
                candle["open"]
            )

            exit_price = float(
                candle["close"]
            )

            result = calculate_result(
                signal["direction"],
                actual_entry_price,
                exit_price
            )

            # ------------------------------------------------
            # UPDATE STATISTICS
            # ------------------------------------------------

            if result == "WIN":

                total_wins += 1

            elif result == "LOSS":

                total_losses += 1

            else:

                total_ties += 1

            save_stats()

            # ------------------------------------------------
            # RESULT MESSAGE
            # ------------------------------------------------

            result_message = build_result_message(
                signal,
                result,
                actual_entry_price,
                exit_price,
                expiry_timestamp
            )

            # ------------------------------------------------
            # RESULT CHART
            # ------------------------------------------------

            result_chart = generate_chart(
                result_df,
                f"RESULT: {result}"
            )

            if result_chart:

                send_telegram_photo(
                    result_chart,
                    result_message
                )

                try:
                    os.remove(result_chart)
                except OSError:
                    pass

            else:

                send_telegram_message(
                    result_message
                )

            await asyncio.sleep(2)

        except KeyboardInterrupt:

            logger.info(
                "Bot stopped manually."
            )

            break

        except Exception as e:

            logger.exception(
                "Main loop error: %s",
                e
            )

            await asyncio.sleep(10)

    # --------------------------------------------------------
    # SHUTDOWN
    # --------------------------------------------------------

    try:

        if client:

            await client.shutdown()

    except Exception:

        pass


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    asyncio.run(
        main()
    )
