import os
import time
import json
import logging
import asyncio
from datetime import datetime, timedelta

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

PO_SSID = os.getenv("PO_SSID", "").strip() or os.getenv("POCKET_OPTION_SSID", "").strip()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

ASSET = "EURUSD_otc"
ASSET_NAME = "EUR/USD (OTC)"

CANDLE_PERIOD = 60
HISTORY_CANDLES = 120

MIN_CANDLES = 60
MIN_CONFIRMATIONS = 7

# الإشارة تكون للشمعة التالية بعد إغلاق الشمعة الحالية
RESULT_GRACE_SECONDS = 4

TURKEY_TZ = pytz.timezone("Europe/Istanbul")

STATS_FILE = "trading_stats.json"


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("PO_OTC_SIGNAL_BOT")


# ============================================================
# STATISTICS
# ============================================================

total_wins = 0
total_losses = 0
total_ties = 0
total_signals = 0


def load_stats():
    global total_wins
    global total_losses
    global total_ties
    global total_signals

    try:

        if os.path.exists(STATS_FILE):

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
            "Could not load statistics: %s",
            e
        )


def save_stats():

    data = {

        "wins": total_wins,

        "losses": total_losses,

        "ties": total_ties,

        "signals": total_signals,

        "updated_at":
            datetime.now(
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
            "Could not save statistics: %s",
            e
        )


def get_win_rate():

    total = (
        total_wins +
        total_losses
    )

    if total == 0:
        return 0.0

    return (
        total_wins /
        total *
        100
    )


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram_message(message):

    if (
        not TELEGRAM_BOT_TOKEN
        or
        not TELEGRAM_CHAT_ID
    ):

        logger.error(
            "Telegram credentials are missing."
        )

        return False

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {

        "chat_id":
            TELEGRAM_CHAT_ID,

        "text":
            message,

        "parse_mode":
            "HTML"
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

    if (
        not TELEGRAM_BOT_TOKEN
        or
        not TELEGRAM_CHAT_ID
    ):

        return False

    if (
        not photo_path
        or
        not os.path.exists(photo_path)
    ):

        return False

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    )

    try:

        with open(
            photo_path,
            "rb"
        ) as photo_file:

            files = {

                "photo":
                    photo_file
            }

            data = {

                "chat_id":
                    TELEGRAM_CHAT_ID,

                "caption":
                    caption,

                "parse_mode":
                    "HTML"
            }

            response = requests.post(
                url,
                data=data,
                files=files,
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

client = None


async def connect_pocket_option():

    global client

    if not PO_SSID:

        raise RuntimeError(
            "PO_SSID is empty. "
            "Add your Pocket Option SSID "
            "to Environment Variables."
        )

    logger.info(
        "Connecting to Pocket Option..."
    )

    client = PocketOptionAsync(
        PO_SSID
    )

    await asyncio.sleep(5)

    logger.info(
        "Pocket Option client initialized."
    )


# ============================================================
# GET REAL POCKET OPTION CANDLES
# ============================================================

async def fetch_pocket_option_candles():

    if client is None:

        return None

    try:

        candles = await client.get_candles(
            ASSET,
            CANDLE_PERIOD,
            HISTORY_CANDLES
        )

        if not candles:

            logger.warning(
                "Pocket Option returned no candles."
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

                    "time":
                        getattr(
                            candle,
                            "time",
                            None
                        ),

                    "open":
                        getattr(
                            candle,
                            "open",
                            None
                        ),

                    "high":
                        getattr(
                            candle,
                            "high",
                            None
                        ),

                    "low":
                        getattr(
                            candle,
                            "low",
                            None
                        ),

                    "close":
                        getattr(
                            candle,
                            "close",
                            None
                        )
                })

        df = pd.DataFrame(
            rows
        )

        df = normalize_candles(
            df
        )

        return df

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

    if (
        df is None
        or
        df.empty
    ):

        return None

    df = df.copy()

    rename_map = {}

    for column in df.columns:

        c = str(
            column
        ).lower().strip()

        if c in (
            "time",
            "timestamp",
            "created_at",
            "at"
        ):

            rename_map[
                column
            ] = "timestamp"

        elif c in (
            "open",
            "o"
        ):

            rename_map[
                column
            ] = "open"

        elif c in (
            "high",
            "h",
            "max"
        ):

            rename_map[
                column
            ] = "high"

        elif c in (
            "low",
            "l",
            "min"
        ):

            rename_map[
                column
            ] = "low"

        elif c in (
            "close",
            "c"
        ):

            rename_map[
                column
            ] = "close"

    df.rename(
        columns=rename_map,
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
                "Missing candle column: %s",
                column
            )

            return None

    for column in required[1:]:

        df[column] = pd.to_numeric(
            df[column],
            errors="coerce"
        )

    timestamp = pd.to_numeric(
        df["timestamp"],
        errors="coerce"
    )

    if timestamp.dropna().empty:

        return None

    # milliseconds -> seconds

    if (
        timestamp.dropna().median()
        >
        10_000_000_000
    ):

        timestamp = (
            timestamp /
            1000
        )

    df["timestamp"] = timestamp

    df.dropna(
        subset=required,
        inplace=True
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
# ONLY CLOSED CANDLES
# ============================================================

def get_closed_candles(df):

    df = normalize_candles(
        df
    )

    if df is None:

        return None

    now = int(
        time.time()
    )

    current_bucket = (
        now //
        CANDLE_PERIOD
    ) * CANDLE_PERIOD

    # لا نستخدم الشمعة الحالية لأنها ما زالت تتكون

    df = df[
        df["timestamp"]
        <
        current_bucket
    ].copy()

    if len(df) < MIN_CANDLES:

        return None

    return df.tail(
        HISTORY_CANDLES
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
        (
            100 /
            (1 + rs)
        )
    ).fillna(50)


# ============================================================
# INDICATORS
# ============================================================

def calculate_indicators(df):

    result = df.copy()

    close = result[
        "close"
    ]

    result[
        "ema_5"
    ] = close.ewm(
        span=5,
        adjust=False
    ).mean()

    result[
        "ema_9"
    ] = close.ewm(
        span=9,
        adjust=False
    ).mean()

    result[
        "ema_21"
    ] = close.ewm(
        span=21,
        adjust=False
    ).mean()

    result[
        "ema_50"
    ] = close.ewm(
        span=50,
        adjust=False
    ).mean()

    result[
        "rsi"
    ] = calculate_rsi(
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

    result[
        "macd"
    ] = (
        ema12 -
        ema26
    )

    result[
        "macd_signal"
    ] = result[
        "macd"
    ].ewm(
        span=9,
        adjust=False
    ).mean()

    result[
        "macd_hist"
    ] = (
        result["macd"] -
        result["macd_signal"]
    )

    result[
        "bb_middle"
    ] = close.rolling(
        20
    ).mean()

    result[
        "bb_std"
    ] = close.rolling(
        20
    ).std()

    result[
        "bb_upper"
    ] = (
        result["bb_middle"] +
        2 *
        result["bb_std"]
    )

    result[
        "bb_lower"
    ] = (
        result["bb_middle"] -
        2 *
        result["bb_std"]
    )

    lowest = result[
        "low"
    ].rolling(
        14
    ).min()

    highest = result[
        "high"
    ].rolling(
        14
    ).max()

    denominator = (
        highest -
        lowest
    ).replace(
        0,
        np.nan
    )

    result[
        "stoch_k"
    ] = (
        100 *
        (
            close -
            lowest
        ) /
        denominator
    ).fillna(50)

    result[
        "stoch_d"
    ] = result[
        "stoch_k"
    ].rolling(
        3
    ).mean().fillna(50)

    high = result[
        "high"
    ]

    low = result[
        "low"
    ]

    tr = pd.concat(
        [
            high - low,

            (
                high -
                close.shift(1)
            ).abs(),

            (
                low -
                close.shift(1)
            ).abs()
        ],
        axis=1
    ).max(
        axis=1
    )

    result[
        "atr"
    ] = tr.rolling(
        14
    ).mean()

    result[
        "momentum"
    ] = (
        close -
        close.shift(4)
    )

    return result.dropna().reset_index(
        drop=True
    )


# ============================================================
# STRATEGY VOTES
# ============================================================

def strategy_votes(df):

    df = calculate_indicators(
        df
    )

    if (
        df is None
        or
        len(df) < 50
    ):

        return None

    last = df.iloc[-1]

    prev = df.iloc[-2]

    votes = {}

    # 1 EMA

    votes[
        "EMA Trend"
    ] = (

        "CALL"

        if (
            last["ema_5"] >
            last["ema_9"] >
            last["ema_21"] >
            last["ema_50"]
        )

        else

        "PUT"

        if (
            last["ema_5"] <
            last["ema_9"] <
            last["ema_21"] <
            last["ema_50"]
        )

        else

        "NEUTRAL"
    )

    # 2 RSI

    votes[
        "RSI"
    ] = (

        "CALL"

        if (
            50 <
            last["rsi"] <
            68
            and
            last["rsi"] >
            prev["rsi"]
        )

        else

        "PUT"

        if (
            32 <
            last["rsi"] <
            50
            and
            last["rsi"] <
            prev["rsi"]
        )

        else

        "NEUTRAL"
    )

    # 3 MACD

    votes[
        "MACD"
    ] = (

        "CALL"

        if (
            last["macd"] >
            last["macd_signal"]
            and
            last["macd_hist"] >
            0
        )

        else

        "PUT"

        if (
            last["macd"] <
            last["macd_signal"]
            and
            last["macd_hist"] <
            0
        )

        else

        "NEUTRAL"
    )

    # 4 Bollinger

    votes[
        "Bollinger"
    ] = (

        "CALL"

        if (
            last["close"] >
            last["bb_middle"]
            and
            last["close"] <
            last["bb_upper"]
        )

        else

        "PUT"

        if (
            last["close"] <
            last["bb_middle"]
            and
            last["close"] >
            last["bb_lower"]
        )

        else

        "NEUTRAL"
    )

    # 5 Stochastic

    votes[
        "Stochastic"
    ] = (

        "CALL"

        if (
            last["stoch_k"] >
            last["stoch_d"]
            and
            last["stoch_k"] <
            80
        )

        else

        "PUT"

        if (
            last["stoch_k"] <
            last["stoch_d"]
            and
            last["stoch_k"] >
            20
        )

        else

        "NEUTRAL"
    )

    # 6 Momentum

    votes[
        "Momentum"
    ] = (

        "CALL"

        if (
            last["momentum"] >
            0
            and
            last["close"] >
            last["open"]
        )

        else

        "PUT"

        if (
            last["momentum"] <
            0
            and
            last["close"] <
            last["open"]
        )

        else

        "NEUTRAL"
    )

    # 7 Candle

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

    votes[
        "Candle Confirmation"
    ] = (

        "CALL"

        if (
            last["close"] >
            last["open"]
            and
            body_ratio >= 0.55
        )

        else

        "PUT"

        if (
            last["close"] <
            last["open"]
            and
            body_ratio >= 0.55
        )

        else

        "NEUTRAL"
    )

    # 8 Support / Resistance

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

    votes[
        "Support/Resistance"
    ] = (

        "CALL"

        if (
            (
                price -
                recent_low
            )
            >
            (
                recent_high -
                price
            )
            and
            price >
            last["ema_21"]
        )

        else

        "PUT"

        if (
            (
                recent_high -
                price
            )
            >
            (
                price -
                recent_low
            )
            and
            price <
            last["ema_21"]
        )

        else

        "NEUTRAL"
    )

    # 9 Trend Strength

    distance = abs(
        last["ema_21"] -
        last["ema_50"]
    )

    atr = last["atr"]

    votes[
        "Trend Strength"
    ] = (

        "CALL"

        if (
            atr > 0
            and
            distance / atr > 0.8
            and
            last["ema_21"] >
            last["ema_50"]
        )

        else

        "PUT"

        if (
            atr > 0
            and
            distance / atr > 0.8
            and
            last["ema_21"] <
            last["ema_50"]
        )

        else

        "NEUTRAL"
    )

    return votes


# ============================================================
# STRONG SIGNAL
# ============================================================

def get_strong_signal(df):

    votes = strategy_votes(
        df
    )

    if votes is None:

        return None

    call_votes = sum(
        1
        for v in votes.values()
        if v == "CALL"
    )

    put_votes = sum(
        1
        for v in votes.values()
        if v == "PUT"
    )

    if (
        call_votes >= MIN_CONFIRMATIONS
        and
        call_votes > put_votes
    ):

        return {

            "direction":
                "CALL",

            "votes":
                call_votes,

            "strategies":
                votes
        }

    if (
        put_votes >= MIN_CONFIRMATIONS
        and
        put_votes > call_votes
    ):

        return {

            "direction":
                "PUT",

            "votes":
                put_votes,

            "strategies":
                votes
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

        chart_df = (
            df.tail(50)
            .copy()
        )

        fig, ax = plt.subplots(
            figsize=(11, 5.5),
            dpi=150
        )

        times = [

            datetime.fromtimestamp(
                float(ts),
                tz=TURKEY_TZ
            )

            for ts
            in chart_df[
                "timestamp"
            ]
        ]

        x_values = mdates.date2num(
            times
        )

        if len(x_values) > 1:

            candle_width = (
                np.median(
                    np.diff(
                        x_values
                    )
                )
                *
                0.82
            )

        else:

            candle_width = 0.0005

        for i, (_, row) in enumerate(
            chart_df.iterrows()
        ):

            x = x_values[i]

            o = float(
                row["open"]
            )

            h = float(
                row["high"]
            )

            l = float(
                row["low"]
            )

            c = float(
                row["close"]
            )

            if c >= o:

                color = "#00df89"

            else:

                color = "#ff3344"

            ax.plot(
                [x, x],
                [l, h],
                color=color,
                linewidth=1.1
            )

            rect = Rectangle(

                (
                    x -
                    candle_width / 2,

                    min(o, c)
                ),

                candle_width,

                max(
                    abs(c - o),
                    1e-8
                ),

                facecolor=color,

                edgecolor=color
            )

            ax.add_patch(
                rect
            )

        ax.set_title(
            f"EUR/USD OTC | {title}",
            fontsize=13,
            fontweight="bold"
        )

        ax.grid(
            True,
            linestyle="--",
            alpha=0.4
        )

        ax.xaxis.set_major_formatter(
            mdates.DateFormatter(
                "%H:%M",
                tz=TURKEY_TZ
            )
        )

        fig.autofmt_xdate()

        plt.tight_layout()

        file_path = (
            f"eurusd_otc_"
            f"{int(time.time())}.png"
        )

        plt.savefig(
            file_path,
            dpi=150,
            bbox_inches="tight"
        )

        plt.close(
            fig
        )

        return file_path

    except Exception as e:

        logger.error(
            "Chart error: %s",
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

    latest = df.iloc[-1]

    candle_timestamp = int(
        latest["timestamp"]
    )

    entry_timestamp = (
        candle_timestamp +
        CANDLE_PERIOD
    )

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

    direction = (
        signal["direction"]
    )

    if direction == "CALL":

        direction_text = (
            "🟢 CALL / UP"
        )

    else:

        direction_text = (
            "🔴 PUT / DOWN"
        )

    message = [

        "🎯 <b>EUR/USD OTC</b>",

        "",

        "📡 <b>مصدر البيانات:</b> "
        "Pocket Option",

        "🕯️ <b>التحليل:</b> "
        "شمعة مغلقة حقيقية",

        "",

        f"🚀 <b>الاتجاه:</b> "
        f"{direction_text}",

        f"⭐ <b>التأكيدات:</b> "
        f"{signal['votes']}/9",

        f"⏰ <b>الدخول:</b> "
        f"{entry_time.strftime('%H:%M:%S')}",

        f"🏁 <b>الانتهاء:</b> "
        f"{expiry_time.strftime('%H:%M:%S')}",

        "⏱️ <b>المدة:</b> 1 دقيقة",

        f"💵 <b>السعر المرجعي:</b> "
        f"{float(latest['close']):.5f}",

        "",

        "📊 <b>التأكيدات:</b>"
    ]

    for (
        name,
        vote
    ) in signal[
        "strategies"
    ].items():

        if vote == "CALL":

            icon = "🟢"

        elif vote == "PUT":

            icon = "🔴"

        else:

            icon = "⚪"

        message.append(
            f"{icon} {name}: {vote}"
        )

    message.extend([

        "",

        "🛡️ <b>بدون مضاعفات</b>",

        "🤖 <b>التنفيذ الآلي:</b> متوقف",

        "",

        "⚠️ الإشارة تحليلية "
        "وليست ضمانًا للربح."
    ])

    return (
        "\n".join(message),
        entry_timestamp,
        expiry_timestamp,
        float(latest["close"])
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

        if exit_price > entry_price:

            return "WIN"

        return "LOSS"

    if direction == "PUT":

        if exit_price < entry_price:

            return "WIN"

        return "LOSS"

    return "TIE"


# ============================================================
# MAIN
# ============================================================

async def main():

    global total_signals
    global total_wins
    global total_losses
    global total_ties

    load_stats()

    if not TELEGRAM_BOT_TOKEN:

        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is missing."
        )

    if not TELEGRAM_CHAT_ID:

        raise RuntimeError(
            "TELEGRAM_CHAT_ID is missing."
        )

    if not PO_SSID:

        raise RuntimeError(
            "PO_SSID is missing."
        )

    await connect_pocket_option()

    send_telegram_message(

        "🤖 <b>بوت EUR/USD OTC بدأ</b>\n\n"

        "📡 مصدر البيانات: "
        "Pocket Option\n"

        "🕯️ الشموع: حقيقية فقط\n"

        "🚫 الشموع الوهمية: متوقفة\n"

        "🎯 الحد الأدنى للإشارة: "
        "7/9\n"

        "⏱️ مدة الصفقة: "
        "1 دقيقة\n"

        "📸 سيتم إرسال صورة الإشارة "
        "وصورة النتيجة\n"

        "🤖 التنفيذ الآلي: متوقف"
    )

    last_processed_candle = None

    while True:

        try:

            raw_df = (
                await fetch_pocket_option_candles()
            )

            df = get_closed_candles(
                raw_df
            )

            if df is None:

                logger.info(
                    "Waiting for real Pocket Option candles..."
                )

                await asyncio.sleep(
                    3
                )

                continue

            latest_timestamp = int(
                df.iloc[-1][
                    "timestamp"
                ]
            )

            # منع تحليل نفس الشمعة مرتين

            if (
                last_processed_candle
                ==
                latest_timestamp
            ):

                await asyncio.sleep(
                    2
                )

                continue

            last_processed_candle = (
                latest_timestamp
            )

            logger.info(
                "Analyzing closed candle: %s",
                datetime.fromtimestamp(
                    latest_timestamp,
                    tz=TURKEY_TZ
                ).strftime(
                    "%H:%M:%S"
                )
            )

            signal = get_strong_signal(
                df
            )

            if signal is None:

                logger.info(
                    "No valid signal: "
                    "less than 7 confirmations."
                )

                await asyncio.sleep(
                    2
                )

                continue

            (
                message,
                entry_timestamp,
                expiry_timestamp,
                entry_price
            ) = build_signal_message(
                signal,
                df
            )

            # التأكد أن لدينا وقتًا كافيًا قبل الدخول

            if (
                entry_timestamp -
                time.time()
                <
                1
            ):

                logger.warning(
                    "Signal arrived too late. Skipping."
                )

                continue

            total_signals += 1

            save_stats()

            logger.info(
                "SIGNAL %s %s/9",
                signal["direction"],
                signal["votes"]
            )

            # صورة الإشارة

            chart_path = generate_chart(

                df,

                f"{signal['direction']} "
                f"| {signal['votes']}/9"
            )

            if chart_path:

                send_telegram_photo(
                    chart_path,
                    message
                )

                try:

                    os.remove(
                        chart_path
                    )

                except OSError:

                    pass

            else:

                send_telegram_message(
                    message
                )

            # انتظار انتهاء دقيقة الصفقة

            await wait_until(
                expiry_timestamp +
                RESULT_GRACE_SECONDS
            )

            # جلب الشموع من جديد

            result_raw_df = (
                await fetch_pocket_option_candles()
            )

            result_df = get_closed_candles(
                result_raw_df
            )

            if result_df is None:

                send_telegram_message(

                    "⚠️ <b>تعذر تأكيد النتيجة</b>\n\n"

                    "لم تصل شمعة الإغلاق "
                    "الحقيقية من Pocket Option.\n"

                    "لن يتم احتساب الصفقة حتى "
                    "لا نحسب نتيجة خاطئة."
                )

                continue

            # العثور على شمعة انتهاء الصفقة

            trade_candle = result_df[
                result_df["timestamp"]
                ==
                entry_timestamp
            ]

            if trade_candle.empty:

                send_telegram_message(

                    "⚠️ <b>تعذر تأكيد النتيجة</b>\n\n"

                    "لم يتم العثور على شمعة "
                    "انتهاء الصفقة.\n"

                    "لم يتم احتساب WIN أو LOSS."
                )

                continue

            exit_price = float(
                trade_candle.iloc[0][
                    "close"
                ]
            )

            result = calculate_result(

                signal["direction"],

                entry_price,

                exit_price
            )

            if result == "WIN":

                total_wins += 1

                result_text = (
                    "WIN 🟢"
                )

            elif result == "LOSS":

                total_losses += 1

                result_text = (
                    "LOSS 🔴"
                )

            else:

                total_ties += 1

                result_text = (
                    "TIE ⚪"
                )

            save_stats()

            completed = (
                total_wins +
                total_losses +
                total_ties
            )

            expiry_time = datetime.fromtimestamp(
                expiry_timestamp,
                tz=TURKEY_TZ
            )

            result_message = (

                "📊 <b>نتيجة EUR/USD OTC</b>\n\n"

                f"🚀 <b>الإشارة:</b> "
                f"{signal['direction']}\n"

                f"🏁 <b>النتيجة:</b> "
                f"<b>{result_text}</b>\n\n"

                f"💵 <b>سعر الدخول المرجعي:</b> "
                f"{entry_price:.5f}\n"

                f"🏁 <b>سعر الإغلاق:</b> "
                f"{exit_price:.5f}\n"

                f"⏰ <b>وقت الانتهاء:</b> "
                f"{expiry_time.strftime('%H:%M:%S')}\n\n"

                "📈 <b>الإحصائيات</b>\n"

                f"🟢 WIN: {total_wins}\n"

                f"🔴 LOSS: {total_losses}\n"

                f"⚪ TIE: {total_ties}\n"

                f"🔢 إجمالي الإشارات: "
                f"{total_signals}\n"

                f"📊 الصفقات المحسوبة: "
                f"{completed}\n"

                f"🎯 نسبة الفوز: "
                f"{get_win_rate():.2f}%"
            )

            # صورة النتيجة

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

                    os.remove(
                        result_chart
                    )

                except OSError:

                    pass

            else:

                send_telegram_message(
                    result_message
                )

            await asyncio.sleep(
                2
            )

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

            await asyncio.sleep(
                10
            )

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
