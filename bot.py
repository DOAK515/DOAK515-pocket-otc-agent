import os
import time
import json
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

PO_SSID = os.getenv("PO_SSID", "").strip()

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN", ""
).strip()

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID", ""
).strip()

ASSET = "EURUSD_otc"
ASSET_NAME = "EUR/USD (OTC)"

TIMEFRAME = 60

HISTORY_CANDLES = 180
MIN_CANDLES = 60

MIN_CONFIRMATIONS = 7

RESULT_GRACE_SECONDS = 5

POLL_SECONDS = 2

TURKEY_TZ = pytz.timezone("Europe/Istanbul")

STATS_FILE = "trading_stats.json"


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger(
    "EURUSD_OTC_V2"
)


# ============================================================
# GLOBAL
# ============================================================

api = None

total_wins = 0
total_losses = 0
total_ties = 0
total_signals = 0


# ============================================================
# HELPERS
# ============================================================

def now_text():

    return datetime.now(
        TURKEY_TZ
    ).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def save_stats():

    data = {
        "wins": total_wins,
        "losses": total_losses,
        "ties": total_ties,
        "signals": total_signals,
        "updated_at": now_text(),
    }

    try:

        with open(
            STATS_FILE,
            "w",
            encoding="utf-8",
        ) as f:

            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2,
            )

    except Exception as e:

        logger.warning(
            "Could not save statistics: %s",
            e,
        )


def load_stats():

    global total_wins
    global total_losses
    global total_ties
    global total_signals

    if not os.path.exists(
        STATS_FILE
    ):

        return

    try:

        with open(
            STATS_FILE,
            "r",
            encoding="utf-8",
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
            "Statistics load failed: %s",
            e,
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
# TELEGRAM
# ============================================================

def telegram_message(
    message
):

    if not TELEGRAM_BOT_TOKEN:
        return False

    if not TELEGRAM_CHAT_ID:
        return False

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
    }

    try:

        r = requests.post(
            url,
            json=payload,
            timeout=20,
        )

        r.raise_for_status()

        return True

    except Exception as e:

        logger.error(
            "Telegram error: %s",
            e,
        )

        return False


def telegram_photo(
    path,
    caption
):

    if not os.path.exists(path):

        return False

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    )

    try:

        with open(
            path,
            "rb",
        ) as f:

            r = requests.post(
                url,
                data={
                    "chat_id":
                        TELEGRAM_CHAT_ID,
                    "caption":
                        caption,
                    "parse_mode":
                        "HTML",
                },
                files={
                    "photo": f
                },
                timeout=30,
            )

        r.raise_for_status()

        return True

    except Exception as e:

        logger.error(
            "Telegram photo error: %s",
            e,
        )

        return False


# ============================================================
# NORMALIZE
# ============================================================

def normalize_candles(
    candles
):

    if candles is None:

        return None

    if isinstance(
        candles,
        pd.DataFrame,
    ):

        df = candles.copy()

    else:

        try:

            df = pd.DataFrame(
                candles
            )

        except Exception:

            return None

    if df.empty:

        return None

    rename = {}

    for c in df.columns:

        name = str(c).lower().strip()

        if name in (
            "time",
            "timestamp",
            "at",
            "date",
        ):

            rename[c] = "timestamp"

        elif name in (
            "open",
            "o",
        ):

            rename[c] = "open"

        elif name in (
            "high",
            "h",
        ):

            rename[c] = "high"

        elif name in (
            "low",
            "l",
        ):

            rename[c] = "low"

        elif name in (
            "close",
            "c",
        ):

            rename[c] = "close"

    df.rename(
        columns=rename,
        inplace=True,
    )

    required = [
        "timestamp",
        "open",
        "high",
        "low",
        "close",
    ]

    for col in required:

        if col not in df.columns:

            logger.warning(
                "Missing column: %s",
                col,
            )

            return None

    for col in (
        "open",
        "high",
        "low",
        "close",
    ):

        df[col] = pd.to_numeric(
            df[col],
            errors="coerce",
        )

    df["timestamp"] = pd.to_numeric(
        df["timestamp"],
        errors="coerce",
    )

    df.dropna(
        subset=required,
        inplace=True,
    )

    if df.empty:

        return None

    # milliseconds -> seconds
    if (
        df["timestamp"].median()
        >
        10_000_000_000
    ):

        df["timestamp"] /= 1000

    df["timestamp"] = (
        df["timestamp"]
        .astype(float)
        .astype(np.int64)
    )

    df.sort_values(
        "timestamp",
        inplace=True,
    )

    df.drop_duplicates(
        subset="timestamp",
        keep="last",
        inplace=True,
    )

    return df.reset_index(
        drop=True
    )


# ============================================================
# CLOSED CANDLES ONLY
# ============================================================

def closed_candles(
    candles
):

    df = normalize_candles(
        candles
    )

    if df is None:

        return None

    current_bucket = (
        int(time.time())
        //
        TIMEFRAME
    ) * TIMEFRAME

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
# POCKET OPTION
# ============================================================

async def create_api():

    if not PO_SSID:

        raise RuntimeError(
            "PO_SSID is missing from GitHub Secrets."
        )

    logger.info(
        "Creating BinaryOptionsToolsV2 client..."
    )

    client = PocketOptionAsync(
        ssid=PO_SSID
    )

    # Official library recommends waiting
    # for initialization.
    await asyncio.sleep(3)

    try:

        demo = client.is_demo()

        logger.info(
            "Pocket Option account mode: %s",
            "DEMO" if demo else "REAL",
        )

    except Exception as e:

        logger.warning(
            "Could not detect account mode: %s",
            e,
        )

    return client


async def connect():

    global api

    api = await create_api()

    logger.info(
        "Pocket Option API initialized."
    )

    # Verify that the client can access
    # server time.
    try:

        server_time = await api.server_time()

        logger.info(
            "Pocket Option server time: %s",
            server_time,
        )

    except Exception as e:

        logger.warning(
            "Server time unavailable: %s",
            e,
        )

    # Check OTC asset using candle request.
    candles = await api.get_candles(
        ASSET,
        TIMEFRAME,
        3600,
    )

    df = normalize_candles(
        candles
    )

    if df is None or df.empty:

        raise RuntimeError(
            "EURUSD_otc returned no candle data."
        )

    logger.info(
        "EURUSD_otc verified. Candles: %s",
        len(df),
    )

    return api


async def reconnect():

    global api

    logger.warning(
        "Reconnecting to Pocket Option..."
    )

    try:

        if api is not None:

            await api.shutdown()

    except Exception:

        pass

    api = None

    await asyncio.sleep(5)

    return await connect()


# ============================================================
# GET HISTORY
# ============================================================

async def get_history():

    if api is None:

        return None

    try:

        candles = await api.get_candles(
            ASSET,
            TIMEFRAME,
            10800,
        )

        df = closed_candles(
            candles
        )

        if df is None:

            return None

        logger.info(
            "Received %s CLOSED OTC candles.",
            len(df),
        )

        return df

    except Exception as e:

        logger.error(
            "Historical candle error: %s",
            e,
        )

        return None


# ============================================================
# INDICATORS
# ============================================================

def rsi(
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
        adjust=False,
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / period,
        adjust=False,
    ).mean()

    rs = (
        avg_gain /
        avg_loss.replace(
            0,
            np.nan,
        )
    )

    value = (
        100 -
        100 /
        (1 + rs)
    )

    return value.fillna(50)


def indicators(df):

    x = df.copy()

    close = x["close"]

    x["ema5"] = (
        close.ewm(
            span=5,
            adjust=False,
        ).mean()
    )

    x["ema9"] = (
        close.ewm(
            span=9,
            adjust=False,
        ).mean()
    )

    x["ema21"] = (
        close.ewm(
            span=21,
            adjust=False,
        ).mean()
    )

    x["ema50"] = (
        close.ewm(
            span=50,
            adjust=False,
        ).mean()
    )

    x["rsi"] = rsi(
        close,
        14,
    )

    ema12 = close.ewm(
        span=12,
        adjust=False,
    ).mean()

    ema26 = close.ewm(
        span=26,
        adjust=False,
    ).mean()

    x["macd"] = (
        ema12 - ema26
    )

    x["macd_signal"] = (
        x["macd"]
        .ewm(
            span=9,
            adjust=False,
        )
        .mean()
    )

    x["macd_hist"] = (
        x["macd"] -
        x["macd_signal"]
    )

    x["bb_mid"] = (
        close
        .rolling(20)
        .mean()
    )

    std = (
        close
        .rolling(20)
        .std()
    )

    x["bb_upper"] = (
        x["bb_mid"] +
        2 * std
    )

    x["bb_lower"] = (
        x["bb_mid"] -
        2 * std
    )

    low14 = (
        x["low"]
        .rolling(14)
        .min()
    )

    high14 = (
        x["high"]
        .rolling(14)
        .max()
    )

    den = (
        high14 - low14
    ).replace(
        0,
        np.nan,
    )

    x["stoch_k"] = (
        100 *
        (close - low14) /
        den
    )

    x["stoch_d"] = (
        x["stoch_k"]
        .rolling(3)
        .mean()
    )

    prev_close = close.shift(1)

    tr = pd.concat(
        [
            x["high"] - x["low"],
            (
                x["high"] -
                prev_close
            ).abs(),
            (
                x["low"] -
                prev_close
            ).abs(),
        ],
        axis=1,
    ).max(axis=1)

    x["atr"] = (
        tr.rolling(14).mean()
    )

    x["momentum"] = (
        close -
        close.shift(4)
    )

    return (
        x
        .replace(
            [np.inf, -np.inf],
            np.nan,
        )
        .dropna()
        .reset_index(
            drop=True
        )
    )


# ============================================================
# 9 CONFIRMATIONS
# ============================================================

def get_votes(df):

    x = indicators(df)

    if len(x) < 50:

        return None

    last = x.iloc[-1]
    prev = x.iloc[-2]

    votes = {}

    # 1 EMA
    if (
        last.ema5 >
        last.ema9 >
        last.ema21 >
        last.ema50
    ):

        votes["EMA"] = "CALL"

    elif (
        last.ema5 <
        last.ema9 <
        last.ema21 <
        last.ema50
    ):

        votes["EMA"] = "PUT"

    else:

        votes["EMA"] = "NEUTRAL"

    # 2 RSI
    if (
        50 < last.rsi < 68
        and
        last.rsi > prev.rsi
    ):

        votes["RSI"] = "CALL"

    elif (
        32 < last.rsi < 50
        and
        last.rsi < prev.rsi
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
        last.close >
        last.open
    ):

        votes["Momentum"] = "CALL"

    elif (
        last.momentum < 0
        and
        last.close <
        last.open
    ):

        votes["Momentum"] = "PUT"

    else:

        votes["Momentum"] = "NEUTRAL"

    # 7 Candle
    candle_range = (
        last.high -
        last.low
    )

    if candle_range > 0:

        body_ratio = (
            abs(
                last.close -
                last.open
            ) /
            candle_range
        )

    else:

        body_ratio = 0

    if (
        last.close >
        last.open
        and
        body_ratio >= 0.55
    ):

        votes["Candle"] = "CALL"

    elif (
        last.close <
        last.open
        and
        body_ratio >= 0.55
    ):

        votes["Candle"] = "PUT"

    else:

        votes["Candle"] = "NEUTRAL"

    # 8 Support/Resistance
    recent_high = (
        x.high.tail(20).max()
    )

    recent_low = (
        x.low.tail(20).min()
    )

    dist_low = (
        last.close -
        recent_low
    )

    dist_high = (
        recent_high -
        last.close
    )

    if (
        dist_low > dist_high
        and
        last.close >
        last.ema21
    ):

        votes["S/R"] = "CALL"

    elif (
        dist_high > dist_low
        and
        last.close <
        last.ema21
    ):

        votes["S/R"] = "PUT"

    else:

        votes["S/R"] = "NEUTRAL"

    # 9 Trend strength
    atr = last.atr

    if atr > 0:

        strength = (
            abs(
                last.ema21 -
                last.ema50
            ) / atr
        )

    else:

        strength = 0

    if (
        strength > 0.8
        and
        last.ema21 >
        last.ema50
    ):

        votes["Trend"] = "CALL"

    elif (
        strength > 0.8
        and
        last.ema21 <
        last.ema50
    ):

        votes["Trend"] = "PUT"

    else:

        votes["Trend"] = "NEUTRAL"

    return votes


def strong_signal(df):

    votes = get_votes(df)

    if votes is None:

        return None

    call = sum(
        v == "CALL"
        for v in votes.values()
    )

    put = sum(
        v == "PUT"
        for v in votes.values()
    )

    logger.info(
        "CONFIRMATIONS: CALL=%s PUT=%s",
        call,
        put,
    )

    if (
        call >= MIN_CONFIRMATIONS
        and
        call > put
    ):

        return {
            "direction": "CALL",
            "votes": call,
            "details": votes,
        }

    if (
        put >= MIN_CONFIRMATIONS
        and
        put > call
    ):

        return {
            "direction": "PUT",
            "votes": put,
            "details": votes,
        }

    return None


# ============================================================
# CHART
# ============================================================

def make_chart(
    df,
    title,
    signal=None,
):

    try:

        x = df.tail(60).copy()

        if x.empty:

            return None

        fig, ax = plt.subplots(
            figsize=(12, 6),
            dpi=150,
        )

        times = [
            datetime.fromtimestamp(
                int(t),
                tz=TURKEY_TZ,
            )
            for t in x.timestamp
        ]

        xs = mdates.date2num(
            times
        )

        if len(xs) > 1:

            width = (
                np.median(
                    np.diff(xs)
                )
                * 0.75
            )

        else:

            width = 0.0005

        for i, (_, row) in enumerate(
            x.iterrows()
        ):

            xx = xs[i]

            o = float(row.open)
            h = float(row.high)
            l = float(row.low)
            c = float(row.close)

            up = c >= o

            color = (
                "#00b86b"
                if up
                else
                "#ef3340"
            )

            ax.plot(
                [xx, xx],
                [l, h],
                color=color,
                linewidth=1,
            )

            rect = Rectangle(
                (
                    xx - width / 2,
                    min(o, c),
                ),
                width,
                max(
                    abs(c - o),
                    1e-8,
                ),
                facecolor=color,
                edgecolor=color,
            )

            ax.add_patch(rect)

        ax.set_title(
            f"{ASSET_NAME} | {title}",
            fontweight="bold",
        )

        ax.grid(
            True,
            linestyle="--",
            alpha=0.3,
        )

        ax.xaxis.set_major_formatter(
            mdates.DateFormatter(
                "%H:%M:%S",
                tz=TURKEY_TZ,
            )
        )

        fig.autofmt_xdate()

        plt.tight_layout()

        path = (
            f"eurusd_otc_"
            f"{int(time.time())}.png"
        )

        plt.savefig(
            path,
            bbox_inches="tight",
        )

        plt.close(fig)

        return path

    except Exception as e:

        logger.error(
            "Chart error: %s",
            e,
        )

        return None


# ============================================================
# SIGNAL MESSAGE
# ============================================================

def signal_message(
    signal,
    df,
):

    candle = df.iloc[-1]

    candle_time = int(
        candle.timestamp
    )

    entry = (
        candle_time +
        TIMEFRAME
    )

    expiry = (
        entry +
        TIMEFRAME
    )

    entry_dt = datetime.fromtimestamp(
        entry,
        tz=TURKEY_TZ,
    )

    expiry_dt = datetime.fromtimestamp(
        expiry,
        tz=TURKEY_TZ,
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

        "📡 المصدر: "
        "<b>Pocket Option OTC</b>",

        "🕯️ الشمعة: "
        "<b>مغلقة فقط</b>",

        "🚫 شموع وهمية: "
        "<b>لا</b>",

        "",

        f"🚀 الاتجاه: <b>{direction}</b>",

        f"⭐ التأكيدات: "
        f"<b>{signal['votes']}/9</b>",

        f"⏰ الدخول: "
        f"<b>{entry_dt.strftime('%H:%M:%S')}</b>",

        f"🏁 الانتهاء: "
        f"<b>{expiry_dt.strftime('%H:%M:%S')}</b>",

        "⏱️ المدة: <b>1 دقيقة</b>",

        f"💵 السعر المرجعي: "
        f"<b>{float(candle.close):.5f}</b>",

        "",

        "📊 <b>التأكيدات:</b>",
    ]

    for name, value in (
        signal["details"].items()
    ):

        icon = (
            "🟢"
            if value == "CALL"
            else
            "🔴"
            if value == "PUT"
            else
            "⚪"
        )

        lines.append(
            f"{icon} {name}: {value}"
        )

    lines.extend(
        [
            "",
            "🛡️ بدون مضاعفات",
            "🤖 التنفيذ الآلي: متوقف",
            "",
            "⚠️ هذه إشارة تحليلية "
            "وليست ضمانًا للربح.",
        ]
    )

    return (
        "\n".join(lines),
        entry,
        expiry,
        float(candle.close),
    )


# ============================================================
# RESULT
# ============================================================

def calculate_result(
    direction,
    entry_price,
    exit_price,
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

    return (
        "WIN"
        if exit_price < entry_price
        else "LOSS"
    )


async def get_result_candle(
    entry_timestamp
):

    expiry = (
        entry_timestamp +
        TIMEFRAME
    )

    # Wait until result candle has closed.
    wait = (
        expiry +
        RESULT_GRACE_SECONDS
        -
        time.time()
    )

    if wait > 0:

        await asyncio.sleep(
            wait
        )

    for attempt in range(8):

        try:

            candles = await api.get_candles(
                ASSET,
                TIMEFRAME,
                600,
            )

            df = closed_candles(
                candles
            )

            if df is not None:

                matches = df[
                    df.timestamp ==
                    entry_timestamp
                ]

                if not matches.empty:

                    row = (
                        matches.iloc[0]
                    )

                    return (
                        df,
                        float(row.close),
                    )

        except Exception as e:

            logger.warning(
                "Result attempt %s: %s",
                attempt + 1,
                e,
            )

        await asyncio.sleep(3)

    return None, None


def send_result(
    signal,
    entry_price,
    exit_price,
    df,
    result,
):

    global total_wins
    global total_losses
    global total_ties

    if result == "WIN":

        total_wins += 1

        label = "WIN 🟢"

    elif result == "LOSS":

        total_losses += 1

        label = "LOSS 🔴"

    else:

        total_ties += 1

        label = "TIE ⚪"

    save_stats()

    message = (

        "📊 <b>نتيجة EUR/USD OTC</b>\n\n"

        f"🚀 الإشارة: "
        f"<b>{signal['direction']}</b>\n"

        f"🏁 النتيجة: <b>{label}</b>\n\n"

        f"💵 الدخول: "
        f"{entry_price:.5f}\n"

        f"🏁 الإغلاق: "
        f"{exit_price:.5f}\n\n"

        "📈 <b>الإحصائيات</b>\n"

        f"🟢 WIN: {total_wins}\n"
        f"🔴 LOSS: {total_losses}\n"
        f"⚪ TIE: {total_ties}\n"

        f"🔢 الإشارات: "
        f"{total_signals}\n"

        f"🎯 نسبة الفوز: "
        f"{win_rate():.2f}%"
    )

    chart = make_chart(
        df,
        f"RESULT {result}",
    )

    if chart:

        telegram_photo(
            chart,
            message,
        )

        try:

            os.remove(chart)

        except OSError:

            pass

    else:

        telegram_message(
            message
        )


# ============================================================
# START
# ============================================================

def start_message():

    return (

        "🤖 <b>بوت EUR/USD OTC V2 بدأ</b>\n\n"

        "📡 المصدر: "
        "<b>Pocket Option OTC</b>\n"

        "💎 الأصل: "
        "<b>EURUSD_otc</b>\n"

        "🕯️ الشموع: "
        "<b>حقيقية ومغلقة فقط</b>\n"

        "🚫 شموع وهمية: "
        "<b>متوقفة</b>\n"

        "📊 الفريم: "
        "<b>1 دقيقة</b>\n"

        "🎯 الحد الأدنى: "
        "<b>7/9</b>\n"

        "📸 الشارت: "
        "<b>مفعل</b>\n"

        "🤖 التنفيذ الآلي: "
        "<b>متوقف</b>\n\n"

        "⏳ <b>بدأت مراقبة EUR/USD OTC...</b>"
    )


# ============================================================
# MAIN
# ============================================================

async def main():

    global total_signals

    load_stats()

    # --------------------------------------------------------
    # Secrets validation
    # --------------------------------------------------------

    missing = []

    if not PO_SSID:
        missing.append("PO_SSID")

    if not TELEGRAM_BOT_TOKEN:
        missing.append(
            "TELEGRAM_BOT_TOKEN"
        )

    if not TELEGRAM_CHAT_ID:
        missing.append(
            "TELEGRAM_CHAT_ID"
        )

    if missing:

        raise RuntimeError(
            "Missing GitHub Secrets: "
            +
            ", ".join(missing)
        )

    # --------------------------------------------------------
    # Connect
    # --------------------------------------------------------

    await connect()

    telegram_message(
        start_message()
    )

    last_candle = None

    logger.info(
        "========================================"
    )

    logger.info(
        "EUR/USD OTC BOT IS RUNNING"
    )

    logger.info(
        "Asset: %s",
        ASSET,
    )

    logger.info(
        "Timeframe: %s seconds",
        TIMEFRAME,
    )

    logger.info(
        "Minimum confirmations: %s/9",
        MIN_CONFIRMATIONS,
    )

    logger.info(
        "========================================"
    )

    # --------------------------------------------------------
    # Loop
    # --------------------------------------------------------

    while True:

        try:

            df = await get_history()

            if df is None:

                logger.info(
                    "Waiting for real OTC candles..."
                )

                await asyncio.sleep(
                    POLL_SECONDS
                )

                continue

            latest = int(
                df.iloc[-1].timestamp
            )

            if latest == last_candle:

                await asyncio.sleep(
                    POLL_SECONDS
                )

                continue

            last_candle = latest

            candle_time = datetime.fromtimestamp(
                latest,
                tz=TURKEY_TZ,
            )

            logger.info(
                "New CLOSED candle: %s",
                candle_time.strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
            )

            # ------------------------------------------------
            # Signal
            # ------------------------------------------------

            signal = strong_signal(
                df
            )

            if signal is None:

                logger.info(
                    "No 7/9 signal."
                )

                continue

            (
                message,
                entry_timestamp,
                expiry_timestamp,
                entry_price,
            ) = signal_message(
                signal,
                df,
            )

            # ------------------------------------------------
            # Make sure entry is future
            # ------------------------------------------------

            if (
                entry_timestamp
                <=
                int(time.time()) + 2
            ):

                logger.warning(
                    "Entry candle is already too close."
                )

                continue

            total_signals += 1

            save_stats()

            logger.info(
                "SIGNAL = %s | %s/9",
                signal["direction"],
                signal["votes"],
            )

            # ------------------------------------------------
            # Signal chart
            # ------------------------------------------------

            chart = make_chart(
                df,
                (
                    f"{signal['direction']} "
                    f"{signal['votes']}/9"
                ),
                signal,
            )

            if chart:

                telegram_photo(
                    chart,
                    message,
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
                await get_result_candle(
                    entry_timestamp
                )
            )

            if (
                result_df is None
                or
                exit_price is None
            ):

                telegram_message(

                    "⚠️ <b>لم أستطع تأكيد نتيجة "
                    "EUR/USD OTC</b>\n\n"

                    f"الإشارة: "
                    f"{signal['direction']}\n"

                    "لم تصل شمعة النتيجة بشكل "
                    "موثوق، لذلك <b>لم يتم احتسابها</b>."
                )

                continue

            result = calculate_result(
                signal["direction"],
                entry_price,
                exit_price,
            )

            send_result(
                signal,
                entry_price,
                exit_price,
                result_df,
                result,
            )

        except asyncio.CancelledError:

            raise

        except KeyboardInterrupt:

            break

        except Exception as e:

            logger.exception(
                "Main loop error"
            )

            telegram_message(

                "⚠️ <b>خطأ في البوت</b>\n\n"

                f"<code>{str(e)[:700]}</code>\n\n"

                "🔄 سيتم إعادة الاتصال."
            )

            try:

                await reconnect()

            except Exception as reconnect_error:

                logger.error(
                    "Reconnect failed: %s",
                    reconnect_error,
                )

                await asyncio.sleep(
                    15
                )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    asyncio.run(
        main()
    )
