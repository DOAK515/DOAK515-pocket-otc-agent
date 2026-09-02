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

from pocketoptionapi import PocketOption


# ============================================================
# CONFIG
# ============================================================

ASSET = "EURUSD_otc"
ASSET_NAME = "EUR/USD (OTC)"

CANDLE_PERIOD = 60
HISTORY_CANDLES = 150
MIN_CANDLES = 60
MIN_CONFIRMATIONS = 7

RESULT_GRACE_SECONDS = 5
POLL_SECONDS = 3

TURKEY_TZ = pytz.timezone("Europe/Istanbul")
STATS_FILE = "trading_stats.json"
STATE_FILE = "bot_state.json"


# ============================================================
# ENVIRONMENT
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


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("EURUSD_OTC_BOT")


# ============================================================
# GLOBALS
# ============================================================

client = None

total_wins = 0
total_losses = 0
total_ties = 0
total_signals = 0

last_processed_candle = None


# ============================================================
# STATE
# ============================================================

def load_state():

    global last_processed_candle

    try:

        if not os.path.exists(STATE_FILE):
            return

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

        value = data.get(
            "last_processed_candle"
        )

        if value is not None:
            last_processed_candle = int(value)

        logger.info(
            "State loaded. Last candle: %s",
            last_processed_candle
        )

    except Exception as e:

        logger.warning(
            "Could not load state: %s",
            e
        )


def save_state():

    try:

        with open(
            STATE_FILE,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                {
                    "last_processed_candle":
                        last_processed_candle
                },
                f,
                indent=2
            )

    except Exception as e:

        logger.warning(
            "Could not save state: %s",
            e
        )


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

    try:

        with open(
            STATS_FILE,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                {
                    "wins": total_wins,
                    "losses": total_losses,
                    "ties": total_ties,
                    "signals": total_signals,
                    "updated_at":
                        datetime.now(
                            TURKEY_TZ
                        ).isoformat()
                },
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
# TELEGRAM
# ============================================================

def telegram_message(text):

    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN missing.")
        return False

    if not TELEGRAM_CHAT_ID:
        logger.error("TELEGRAM_CHAT_ID missing.")
        return False

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    try:

        response = requests.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML"
            },
            timeout=20
        )

        response.raise_for_status()

        return True

    except Exception as e:

        logger.error(
            "Telegram error: %s",
            e
        )

        return False


def telegram_photo(path, caption):

    if not os.path.exists(path):
        return False

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    )

    try:

        with open(path, "rb") as f:

            response = requests.post(
                url,
                data={
                    "chat_id":
                        TELEGRAM_CHAT_ID,
                    "caption":
                        caption,
                    "parse_mode":
                        "HTML"
                },
                files={
                    "photo": f
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
# CONNECTION
# ============================================================

def validate_ssid():

    if not PO_SSID:

        raise RuntimeError(
            "PO_SSID is missing."
        )

    # لا نقبل UID أو رقم فقط.
    if PO_SSID.isdigit():

        raise RuntimeError(
            "PO_SSID is invalid: "
            "you supplied only a number/UID. "
            "A valid Pocket Option session string "
            "is required."
        )

    if (
        "auth" not in PO_SSID
        and
        "session" not in PO_SSID
    ):

        logger.warning(
            "PO_SSID does not look like a complete "
            "Pocket Option auth/session string."
        )


def connect():

    global client

    validate_ssid()

    logger.info(
        "Connecting to Pocket Option..."
    )

    client = PocketOption(
        PO_SSID
    )

    ok, error = client.connect()

    if not ok:

        raise RuntimeError(
            f"Pocket Option connection failed: {error}"
        )

    logger.info(
        "Pocket Option connected."
    )

    deadline = time.time() + 30

    while time.time() < deadline:

        try:

            if (
                client.check_connect()
                and
                client.is_time_synced()
            ):

                logger.info(
                    "Pocket Option time synchronized."
                )

                break

        except Exception:
            pass

        time.sleep(0.25)

    else:

        raise RuntimeError(
            "Connected, but server time was not synchronized."
        )

    client.subscribe(
        ASSET,
        period=CANDLE_PERIOD
    )

    logger.info(
        "Subscribed: %s | %ss",
        ASSET,
        CANDLE_PERIOD
    )

    time.sleep(2)


def connected():

    if client is None:
        return False

    try:

        return (
            client.check_connect()
            and
            client.is_time_synced()
        )

    except Exception:

        return False


def reconnect():

    global client

    logger.warning(
        "Reconnecting..."
    )

    try:

        if client is not None:

            client.disconnect_websocket()

    except Exception:
        pass

    client = None

    time.sleep(3)

    connect()


# ============================================================
# CANDLE NORMALIZATION
# ============================================================

def normalize_candles(data):

    if data is None:
        return None

    if isinstance(data, pd.DataFrame):

        df = data.copy()

    else:

        try:

            df = pd.DataFrame(data)

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
            "created_at"
        ):

            rename[c] = "timestamp"

        elif name in (
            "open",
            "o"
        ):

            rename[c] = "open"

        elif name in (
            "high",
            "h",
            "max"
        ):

            rename[c] = "high"

        elif name in (
            "low",
            "l",
            "min"
        ):

            rename[c] = "low"

        elif name in (
            "close",
            "c"
        ):

            rename[c] = "close"

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

    if any(
        c not in df.columns
        for c in required
    ):

        return None

    for c in [
        "open",
        "high",
        "low",
        "close"
    ]:

        df[c] = pd.to_numeric(
            df[c],
            errors="coerce"
        )

    df["timestamp"] = pd.to_numeric(
        df["timestamp"],
        errors="coerce"
    )

    if (
        df["timestamp"]
        .dropna()
        .empty
    ):

        return None

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
# REAL POCKET OPTION CANDLES
# ============================================================

def fetch_candles():

    if not connected():
        return None

    try:

        raw = client.get_historical_candles(
            ASSET,
            period=CANDLE_PERIOD,
            offset=45000,
            count_request=1
        )

        return normalize_candles(raw)

    except Exception as e:

        logger.error(
            "Candle fetch error: %s",
            e
        )

        return None


def closed_candles(df):

    df = normalize_candles(df)

    if df is None:
        return None

    try:

        server_time = float(
            client.get_server_timestamp()
        )

    except Exception:

        server_time = time.time()

    current_bucket = (
        int(server_time)
        // CANDLE_PERIOD
    ) * CANDLE_PERIOD

    df = df[
        df["timestamp"]
        < current_bucket
    ].copy()

    if len(df) < MIN_CANDLES:

        logger.info(
            "Waiting for candles: %s/%s",
            len(df),
            MIN_CANDLES
        )

        return None

    return df.tail(
        HISTORY_CANDLES
    ).reset_index(
        drop=True
    )


# ============================================================
# INDICATORS
# ============================================================

def rsi(series, period=14):

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

    value = (
        100 -
        (
            100 /
            (1 + rs)
        )
    )

    return value.fillna(50)


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

    middle = (
        close
        .rolling(20)
        .mean()
    )

    std = (
        close
        .rolling(20)
        .std()
    )

    d["bb_middle"] = middle
    d["bb_upper"] = (
        middle + 2 * std
    )
    d["bb_lower"] = (
        middle - 2 * std
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
    ).replace(
        0,
        np.nan
    )

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
        tr.rolling(14).mean()
    )

    d["momentum"] = (
        close -
        close.shift(4)
    )

    return (
        d.replace(
            [np.inf, -np.inf],
            np.nan
        )
        .dropna()
        .reset_index(drop=True)
    )


# ============================================================
# 9 CONFIRMATIONS
# ============================================================

def get_votes(df):

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
        last.bb_middle
        and
        last.close <
        last.bb_upper
    ):

        votes["Bollinger"] = "CALL"

    elif (
        last.close <
        last.bb_middle
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
    body = abs(
        last.close -
        last.open
    )

    candle_range = (
        last.high -
        last.low
    )

    ratio = (
        body / candle_range
        if candle_range > 0
        else 0
    )

    if (
        last.close > last.open
        and
        ratio >= 0.55
    ):

        votes[
            "Candle Confirmation"
        ] = "CALL"

    elif (
        last.close < last.open
        and
        ratio >= 0.55
    ):

        votes[
            "Candle Confirmation"
        ] = "PUT"

    else:

        votes[
            "Candle Confirmation"
        ] = "NEUTRAL"

    # 8 Support / Resistance
    high20 = (
        d.high.tail(20).max()
    )

    low20 = (
        d.low.tail(20).min()
    )

    distance_low = (
        last.close - low20
    )

    distance_high = (
        high20 - last.close
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

    # 9 Trend strength
    if last.atr > 0:

        strength = (
            abs(
                last.ema21 -
                last.ema50
            )
            /
            last.atr
        )

    else:

        strength = 0

    if (
        strength > 0.8
        and
        last.ema21 >
        last.ema50
    ):

        votes[
            "Trend Strength"
        ] = "CALL"

    elif (
        strength > 0.8
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
        "Confirmations: CALL=%s PUT=%s",
        call,
        put
    )

    if (
        call >= MIN_CONFIRMATIONS
        and
        call > put
    ):

        return {
            "direction": "CALL",
            "votes": call,
            "strategies": votes
        }

    if (
        put >= MIN_CONFIRMATIONS
        and
        put > call
    ):

        return {
            "direction": "PUT",
            "votes": put,
            "strategies": votes
        }

    return None


# ============================================================
# CHART
# ============================================================

def make_chart(
    df,
    title,
    direction=None,
    marker_timestamp=None
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
            for x in d.timestamp
        ]

        xs = mdates.date2num(
            times
        )

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

            candle_color = (
                "#00c878"
                if c >= o
                else "#ff3344"
            )

            ax.plot(
                [x, x],
                [l, h],
                color=candle_color,
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
                facecolor=candle_color,
                edgecolor=candle_color
            )

            ax.add_patch(rect)

        if marker_timestamp is not None:

            found = d[
                d.timestamp ==
                marker_timestamp
            ]

            if not found.empty:

                row = found.iloc[0]

                x = mdates.date2num(
                    datetime.fromtimestamp(
                        float(marker_timestamp),
                        tz=TURKEY_TZ
                    )
                )

                if direction == "CALL":

                    ax.annotate(
                        "CALL",
                        xy=(x, row.high),
                        xytext=(
                            x,
                            row.high +
                            (
                                row.high -
                                row.low
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
                        xy=(x, row.low),
                        xytext=(
                            x,
                            row.low -
                            (
                                row.high -
                                row.low
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

        path = (
            f"eurusd_otc_"
            f"{int(time.time())}.png"
        )

        plt.savefig(
            path,
            dpi=140,
            bbox_inches="tight"
        )

        plt.close(fig)

        return path

    except Exception as e:

        logger.error(
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

    candle_ts = int(
        df.iloc[-1].timestamp
    )

    entry_ts = (
        candle_ts +
        CANDLE_PERIOD
    )

    expiry_ts = (
        entry_ts +
        CANDLE_PERIOD
    )

    entry = datetime.fromtimestamp(
        entry_ts,
        tz=TURKEY_TZ
    )

    expiry = datetime.fromtimestamp(
        expiry_ts,
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
        "📡 المصدر: Pocket Option",
        "🕯️ شموع حقيقية مغلقة فقط",
        "🚫 لا توجد شموع وهمية",
        "",
        f"🚀 الاتجاه: <b>{direction}</b>",
        f"⭐ التأكيدات: <b>{signal['votes']}/9</b>",
        "",
        f"⏰ الدخول: <b>{entry:%H:%M:%S}</b>",
        f"🏁 الانتهاء: <b>{expiry:%H:%M:%S}</b>",
        "⏱️ المدة: 1 دقيقة",
        "",
        f"💵 السعر المرجعي: "
        f"{float(df.iloc[-1].close):.5f}",
        "",
        "📊 <b>التأكيدات</b>"
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
            "🛡️ بدون مضاعفات",
            "🤖 التنفيذ الآلي: متوقف",
            "",
            "⚠️ إشارة تحليلية وليست ضمانًا للربح."
        ]
    )

    return (
        "\n".join(lines),
        entry_ts,
        expiry_ts,
        float(df.iloc[-1].close)
    )


# ============================================================
# RESULT
# ============================================================

def result_for(
    direction,
    entry,
    exit_price
):

    if abs(
        exit_price - entry
    ) < 1e-10:

        return "TIE"

    if direction == "CALL":

        return (
            "WIN"
            if exit_price > entry
            else
            "LOSS"
        )

    return (
        "WIN"
        if exit_price < entry
        else
        "LOSS"
    )


# ============================================================
# RESULT FETCH
# ============================================================

def get_exit_price(
    df,
    entry_timestamp
):

    if df is None:
        return None

    d = normalize_candles(df)

    if d is None:
        return None

    rows = d[
        d.timestamp ==
        float(entry_timestamp)
    ]

    if rows.empty:
        return None

    return float(
        rows.iloc[0].close
    )


async def wait_result(
    entry_timestamp
):

    expiry = (
        entry_timestamp +
        CANDLE_PERIOD
    )

    while (
        time.time()
        <
        expiry +
        RESULT_GRACE_SECONDS
    ):

        await asyncio.sleep(
            0.5
        )

    for attempt in range(8):

        raw = await asyncio.to_thread(
            fetch_candles
        )

        df = closed_candles(
            raw
        )

        price = get_exit_price(
            df,
            entry_timestamp
        )

        if price is not None:

            return df, price

        logger.info(
            "Waiting for result candle... "
            "attempt %s/8",
            attempt + 1
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
    expiry_timestamp,
    result_df,
    result
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

    expiry = datetime.fromtimestamp(
        expiry_timestamp,
        tz=TURKEY_TZ
    )

    message = (

        "📊 <b>نتيجة EUR/USD OTC</b>\n\n"

        f"🚀 الإشارة: "
        f"{signal['direction']}\n"

        f"🏁 النتيجة: <b>{label}</b>\n\n"

        f"💵 الدخول: "
        f"{entry_price:.5f}\n"

        f"🏁 الإغلاق: "
        f"{exit_price:.5f}\n"

        f"⏰ الانتهاء: "
        f"{expiry:%H:%M:%S}\n\n"

        "📈 <b>الإحصائيات</b>\n"

        f"🟢 WIN: {total_wins}\n"
        f"🔴 LOSS: {total_losses}\n"
        f"⚪ TIE: {total_ties}\n"
        f"🔢 الإشارات: {total_signals}\n"
        f"🎯 الفوز: {win_rate():.2f}%"
    )

    path = make_chart(
        result_df,
        f"RESULT {result}"
    )

    if path:

        telegram_photo(
            path,
            message
        )

        try:
            os.remove(path)
        except OSError:
            pass

    else:

        telegram_message(
            message
        )


# ============================================================
# START MESSAGE
# ============================================================

def start_message():

    telegram_message(

        "🤖 <b>بوت EUR/USD OTC بدأ</b>\n\n"

        "📡 المصدر: Pocket Option\n"
        "🕯️ شموع مغلقة حقيقية فقط\n"
        "🚫 الشموع الوهمية: متوقفة\n"
        "📊 الفريم: 1 دقيقة\n"
        "🎯 الحد الأدنى: 7/9\n"
        "⏱️ مدة الصفقة: 1 دقيقة\n"
        "🛡️ بدون مضاعفات\n"
        "🤖 التنفيذ الآلي: متوقف\n\n"

        "⏳ <b>البوت يراقب الآن...</b>"
    )


# ============================================================
# MAIN
# ============================================================

async def main():

    global total_signals
    global last_processed_candle

    load_stats()
    load_state()

    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is missing."
        )

    if not TELEGRAM_CHAT_ID:
        raise RuntimeError(
            "TELEGRAM_CHAT_ID is missing."
        )

    connect()
    start_message()

    while True:

        try:

            if not connected():

                reconnect()

            raw = await asyncio.to_thread(
                fetch_candles
            )

            df = closed_candles(
                raw
            )

            if df is None:

                logger.info(
                    "⏳ Waiting for real OTC candles..."
                )

                await asyncio.sleep(
                    POLL_SECONDS
                )

                continue

            latest_ts = int(
                df.iloc[-1].timestamp
            )

            latest_dt = datetime.fromtimestamp(
                latest_ts,
                tz=TURKEY_TZ
            )

            logger.info(
                "Latest closed candle: %s",
                latest_dt.strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            )

            if (
                last_processed_candle
                ==
                latest_ts
            ):

                await asyncio.sleep(
                    POLL_SECONDS
                )

                continue

            signal = strong_signal(
                df
            )

            last_processed_candle = (
                latest_ts
            )

            save_state()

            if signal is None:

                logger.info(
                    "No 7/9 signal."
                )

                await asyncio.sleep(
                    POLL_SECONDS
                )

                continue

            (
                message,
                entry_ts,
                expiry_ts,
                entry_price
            ) = signal_message(
                signal,
                df
            )

            # لا نرسل إشارة إذا انتهى وقت الدخول.
            try:

                server_time = float(
                    client.get_server_timestamp()
                )

            except Exception:

                server_time = time.time()

            if (
                entry_ts -
                server_time
                <= 2
            ):

                logger.warning(
                    "Signal too late. Skipping."
                )

                continue

            total_signals += 1
            save_stats()

            logger.info(
                "SIGNAL %s | %s/9",
                signal["direction"],
                signal["votes"]
            )

            path = make_chart(
                df,
                (
                    f"{signal['direction']} "
                    f"{signal['votes']}/9"
                ),
                direction=signal[
                    "direction"
                ],
                marker_timestamp=latest_ts
            )

            if path:

                telegram_photo(
                    path,
                    message
                )

                try:
                    os.remove(path)
                except OSError:
                    pass

            else:

                telegram_message(
                    message
                )

            # انتظار نتيجة الشمعة التالية
            result_df, exit_price = (
                await wait_result(
                    entry_ts
                )
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

                    "❌ لم تصل شمعة الإغلاق "
                    "من مصدر Pocket Option.\n\n"

                    "🛡️ لم يتم احتساب WIN/LOSS."
                )

                await asyncio.sleep(2)
                continue

            result = result_for(
                signal["direction"],
                entry_price,
                exit_price
            )

            send_result(
                signal,
                entry_price,
                exit_price,
                expiry_ts,
                result_df,
                result
            )

            await asyncio.sleep(2)

        except KeyboardInterrupt:

            break

        except Exception as e:

            logger.exception(
                "Main loop error"
            )

            try:

                telegram_message(
                    "⚠️ <b>خطأ مؤقت في البوت</b>\n\n"
                    f"<code>{str(e)[:700]}</code>\n\n"
                    "🔄 ستتم محاولة إعادة الاتصال."
                )

            except Exception:
                pass

            await asyncio.sleep(10)

            try:

                if not connected():
                    reconnect()

            except Exception as reconnect_error:

                logger.error(
                    "Reconnect error: %s",
                    reconnect_error
                )

                await asyncio.sleep(10)

    try:

        if client is not None:
            client.disconnect_websocket()

    except Exception:
        pass


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    asyncio.run(
        main()
    )
