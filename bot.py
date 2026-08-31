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


# نفس رمز EUR/USD OTC في Pocket Option
ASSET = "EURUSD_otc"
ASSET_NAME = "EUR/USD (OTC)"

# شموع دقيقة واحدة
CANDLE_PERIOD = 60

# عدد الشموع المستخدمة للتحليل
HISTORY_CANDLES = 150

# أقل عدد شموع مطلوب
MIN_CANDLES = 60

# لا نرسل الإشارة إلا إذا حصلت على 7 من 9
MIN_CONFIRMATIONS = 7

# انتظار بسيط بعد انتهاء الشمعة
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

logger = logging.getLogger(
    "EURUSD_OTC_SIGNAL_BOT"
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

        logger.info(
            "Statistics loaded: "
            "WIN=%s LOSS=%s TIE=%s SIGNALS=%s",
            total_wins,
            total_losses,
            total_ties,
            total_signals
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

    finished = (
        total_wins +
        total_losses
    )

    if finished <= 0:
        return 0.0

    return (
        total_wins /
        finished
    ) * 100.0


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
        "parse_mode": "HTML"
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

    if not TELEGRAM_BOT_TOKEN:
        return False

    if not TELEGRAM_CHAT_ID:
        return False

    if not photo_path:
        return False

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
        ) as photo_file:

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
                    "photo":
                        photo_file
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

def connect_pocket_option():

    global client

    if not PO_SSID:

        raise RuntimeError(
            "PO_SSID is missing."
        )

    logger.info(
        "Connecting to Pocket Option..."
    )

    client = PocketOption(
        PO_SSID
    )

    ok, error = client.connect()

    if not ok:

        raise RuntimeError(
            "Pocket Option connection failed: "
            f"{error}"
        )

    logger.info(
        "Pocket Option WebSocket connected."
    )

    # انتظار مزامنة وقت السيرفر
    for _ in range(300):

        try:

            connected = (
                client.check_connect()
            )

            synced = (
                client.is_time_synced()
            )

            if connected and synced:

                logger.info(
                    "Pocket Option server time synchronized."
                )

                break

        except Exception as e:

            logger.warning(
                "Time sync check error: %s",
                e
            )

        time.sleep(0.2)

    else:

        raise RuntimeError(
            "Pocket Option connected but "
            "server time was not synchronized."
        )

    # الاشتراك في EUR/USD OTC
    try:

        client.subscribe(
            ASSET,
            period=CANDLE_PERIOD
        )

        logger.info(
            "Subscribed to %s | %s seconds",
            ASSET,
            CANDLE_PERIOD
        )

    except Exception as e:

        raise RuntimeError(
            "Could not subscribe to "
            f"{ASSET}: {e}"
        )

    time.sleep(2)

    # فحص الأصل
    try:

        assets = client.get_assets()

        if assets and ASSET in assets:

            asset_info = assets[ASSET]

            logger.info(
                "Asset found: %s",
                ASSET
            )

            logger.info(
                "Asset availability: %s",
                asset_info.get(
                    "is_available",
                    "unknown"
                )
            )

            logger.info(
                "Asset payout: %s",
                asset_info.get(
                    "payout",
                    "unknown"
                )
            )

        else:

            logger.warning(
                "EURUSD_otc was not found "
                "in the current asset catalog."
            )

    except Exception as e:

        logger.warning(
            "Could not read asset catalog: %s",
            e
        )


def is_pocket_option_connected():

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


# ============================================================
# RECONNECT
# ============================================================

def reconnect_pocket_option():

    global client

    logger.warning(
        "Pocket Option connection lost. "
        "Trying to reconnect..."
    )

    try:

        if client is not None:

            try:

                client.disconnect_websocket()

            except Exception:
                pass

    except Exception:
        pass

    client = None

    time.sleep(3)

    connect_pocket_option()


# ============================================================
# NORMALIZE CANDLES
# ============================================================

def normalize_candles(df):

    if df is None:
        return None

    if not isinstance(
        df,
        pd.DataFrame
    ):

        try:

            df = pd.DataFrame(df)

        except Exception:

            return None

    if df.empty:
        return None

    df = df.copy()

    # --------------------------------------------------------
    # Rename columns
    # --------------------------------------------------------

    rename_map = {}

    for column in df.columns:

        name = str(
            column
        ).lower().strip()

        if name in (
            "time",
            "timestamp",
            "at",
            "created_at",
            "date"
        ):

            rename_map[column] = "timestamp"

        elif name in (
            "open",
            "o"
        ):

            rename_map[column] = "open"

        elif name in (
            "high",
            "h",
            "max"
        ):

            rename_map[column] = "high"

        elif name in (
            "low",
            "l",
            "min"
        ):

            rename_map[column] = "low"

        elif name in (
            "close",
            "c"
        ):

            rename_map[column] = "close"

    df.rename(
        columns=rename_map,
        inplace=True
    )

    # --------------------------------------------------------
    # Required columns
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Numeric conversion
    # --------------------------------------------------------

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
            1000.0
        )

    df["timestamp"] = timestamp

    df.dropna(
        subset=required,
        inplace=True
    )

    # --------------------------------------------------------
    # Clean OHLC
    # --------------------------------------------------------

    df = df[
        (df["high"] >= df["open"]) |
        (df["high"] >= df["close"])
    ]

    df = df[
        (df["low"] <= df["open"]) |
        (df["low"] <= df["close"])
    ]

    if df.empty:
        return None

    # --------------------------------------------------------
    # Sort / deduplicate
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

    return df.reset_index(
        drop=True
    )


# ============================================================
# FETCH REAL POCKET OPTION CANDLES
# ============================================================

def fetch_pocket_option_candles():

    if client is None:

        return None

    try:

        if not is_pocket_option_connected():

            logger.warning(
                "Pocket Option is not connected."
            )

            return None

        candles = (
            client.get_historical_candles(
                ASSET,
                period=CANDLE_PERIOD,
                offset=45000,
                count_request=1
            )
        )

        if candles is None:

            logger.warning(
                "Pocket Option returned no candles."
            )

            return None

        df = normalize_candles(
            candles
        )

        if df is None:

            logger.warning(
                "Could not normalize Pocket Option candles."
            )

            return None

        logger.info(
            "Received %s real candles for %s.",
            len(df),
            ASSET
        )

        return df

    except Exception as e:

        logger.exception(
            "Pocket Option candle error: %s",
            e
        )

        return None


# ============================================================
# GET SERVER TIME
# ============================================================

def get_server_time():

    try:

        if client is not None:

            return float(
                client.get_server_timestamp()
            )

    except Exception as e:

        logger.warning(
            "Could not get server timestamp: %s",
            e
        )

    return time.time()


# ============================================================
# ONLY CLOSED CANDLES
# ============================================================

def get_closed_candles(df):

    df = normalize_candles(
        df
    )

    if df is None:

        return None

    try:

        now = get_server_time()

        current_bucket = (
            int(now) //
            CANDLE_PERIOD
        ) * CANDLE_PERIOD

    except Exception:

        current_bucket = (
            int(time.time()) //
            CANDLE_PERIOD
        ) * CANDLE_PERIOD

    # --------------------------------------------------------
    # IMPORTANT:
    # Remove current forming candle.
    # Only completed Pocket Option candles are used.
    # --------------------------------------------------------

    df = df[
        df["timestamp"]
        <
        current_bucket
    ].copy()

    if len(df) < MIN_CANDLES:

        logger.warning(
            "Only %s closed candles available. "
            "Need at least %s.",
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

    rsi = (
        100 -
        (
            100 /
            (1 + rs)
        )
    )

    return rsi.fillna(50)


# ============================================================
# INDICATORS
# ============================================================

def calculate_indicators(df):

    result = df.copy()

    close = result["close"]

    # EMA
    result["ema_5"] = (
        close.ewm(
            span=5,
            adjust=False
        ).mean()
    )

    result["ema_9"] = (
        close.ewm(
            span=9,
            adjust=False
        ).mean()
    )

    result["ema_21"] = (
        close.ewm(
            span=21,
            adjust=False
        ).mean()
    )

    result["ema_50"] = (
        close.ewm(
            span=50,
            adjust=False
        ).mean()
    )

    # RSI
    result["rsi"] = calculate_rsi(
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

    result["macd"] = (
        ema12 -
        ema26
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

    # Bollinger Bands
    result["bb_middle"] = (
        close
        .rolling(20)
        .mean()
    )

    result["bb_std"] = (
        close
        .rolling(20)
        .std()
    )

    result["bb_upper"] = (
        result["bb_middle"] +
        2 *
        result["bb_std"]
    )

    result["bb_lower"] = (
        result["bb_middle"] -
        2 *
        result["bb_std"]
    )

    # Stochastic
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
        highest -
        lowest
    ).replace(
        0,
        np.nan
    )

    result["stoch_k"] = (
        100 *
        (
            close -
            lowest
        ) /
        denominator
    )

    result["stoch_d"] = (
        result["stoch_k"]
        .rolling(3)
        .mean()
    )

    # ATR
    high = result["high"]

    low = result["low"]

    previous_close = (
        close.shift(1)
    )

    tr = pd.concat(
        [
            high - low,

            (
                high -
                previous_close
            ).abs(),

            (
                low -
                previous_close
            ).abs()
        ],
        axis=1
    ).max(
        axis=1
    )

    result["atr"] = (
        tr
        .rolling(14)
        .mean()
    )

    # Momentum
    result["momentum"] = (
        close -
        close.shift(4)
    )

    result = (
        result
        .replace(
            [np.inf, -np.inf],
            np.nan
        )
        .dropna()
        .reset_index(
            drop=True
        )
    )

    return result


# ============================================================
# STRATEGY VOTES
# ============================================================

def strategy_votes(df):

    if df is None:
        return None

    df = calculate_indicators(
        df
    )

    if df is None:
        return None

    if len(df) < 50:
        return None

    last = df.iloc[-1]

    previous = df.iloc[-2]

    votes = {}

    # --------------------------------------------------------
    # 1. EMA TREND
    # --------------------------------------------------------

    if (
        last["ema_5"] >
        last["ema_9"] >
        last["ema_21"] >
        last["ema_50"]
    ):

        votes["EMA Trend"] = "CALL"

    elif (
        last["ema_5"] <
        last["ema_9"] <
        last["ema_21"] <
        last["ema_50"]
    ):

        votes["EMA Trend"] = "PUT"

    else:

        votes["EMA Trend"] = "NEUTRAL"

    # --------------------------------------------------------
    # 2. RSI
    # --------------------------------------------------------

    if (
        50 <
        last["rsi"] <
        68
        and
        last["rsi"] >
        previous["rsi"]
    ):

        votes["RSI"] = "CALL"

    elif (
        32 <
        last["rsi"] <
        50
        and
        last["rsi"] <
        previous["rsi"]
    ):

        votes["RSI"] = "PUT"

    else:

        votes["RSI"] = "NEUTRAL"

    # --------------------------------------------------------
    # 3. MACD
    # --------------------------------------------------------

    if (
        last["macd"] >
        last["macd_signal"]
        and
        last["macd_hist"] >
        0
    ):

        votes["MACD"] = "CALL"

    elif (
        last["macd"] <
        last["macd_signal"]
        and
        last["macd_hist"] <
        0
    ):

        votes["MACD"] = "PUT"

    else:

        votes["MACD"] = "NEUTRAL"

    # --------------------------------------------------------
    # 4. BOLLINGER
    # --------------------------------------------------------

    if (
        last["close"] >
        last["bb_middle"]
        and
        last["close"] <
        last["bb_upper"]
    ):

        votes["Bollinger"] = "CALL"

    elif (
        last["close"] <
        last["bb_middle"]
        and
        last["close"] >
        last["bb_lower"]
    ):

        votes["Bollinger"] = "PUT"

    else:

        votes["Bollinger"] = "NEUTRAL"

    # --------------------------------------------------------
    # 5. STOCHASTIC
    # --------------------------------------------------------

    if (
        last["stoch_k"] >
        last["stoch_d"]
        and
        last["stoch_k"] <
        80
    ):

        votes["Stochastic"] = "CALL"

    elif (
        last["stoch_k"] <
        last["stoch_d"]
        and
        last["stoch_k"] >
        20
    ):

        votes["Stochastic"] = "PUT"

    else:

        votes["Stochastic"] = "NEUTRAL"

    # --------------------------------------------------------
    # 6. MOMENTUM
    # --------------------------------------------------------

    if (
        last["momentum"] >
        0
        and
        last["close"] >
        last["open"]
    ):

        votes["Momentum"] = "CALL"

    elif (
        last["momentum"] <
        0
        and
        last["close"] <
        last["open"]
    ):

        votes["Momentum"] = "PUT"

    else:

        votes["Momentum"] = "NEUTRAL"

    # --------------------------------------------------------
    # 7. CANDLE CONFIRMATION
    # --------------------------------------------------------

    candle_body = abs(
        last["close"] -
        last["open"]
    )

    candle_range = (
        last["high"] -
        last["low"]
    )

    if candle_range > 0:

        body_ratio = (
            candle_body /
            candle_range
        )

    else:

        body_ratio = 0

    if (
        last["close"] >
        last["open"]
        and
        body_ratio >= 0.55
    ):

        votes[
            "Candle Confirmation"
        ] = "CALL"

    elif (
        last["close"] <
        last["open"]
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

    # --------------------------------------------------------
    # 8. SUPPORT / RESISTANCE
    # --------------------------------------------------------

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

    distance_from_low = (
        price -
        recent_low
    )

    distance_from_high = (
        recent_high -
        price
    )

    if (
        distance_from_low >
        distance_from_high
        and
        price >
        last["ema_21"]
    ):

        votes[
            "Support/Resistance"
        ] = "CALL"

    elif (
        distance_from_high >
        distance_from_low
        and
        price <
        last["ema_21"]
    ):

        votes[
            "Support/Resistance"
        ] = "PUT"

    else:

        votes[
            "Support/Resistance"
        ] = "NEUTRAL"

    # --------------------------------------------------------
    # 9. TREND STRENGTH
    # --------------------------------------------------------

    distance = abs(
        last["ema_21"] -
        last["ema_50"]
    )

    atr = last["atr"]

    if (
        atr > 0
        and
        distance / atr > 0.8
        and
        last["ema_21"] >
        last["ema_50"]
    ):

        votes[
            "Trend Strength"
        ] = "CALL"

    elif (
        atr > 0
        and
        distance / atr > 0.8
        and
        last["ema_21"] <
        last["ema_50"]
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

def get_strong_signal(df):

    votes = strategy_votes(
        df
    )

    if votes is None:
        return None

    call_votes = sum(
        1
        for value in votes.values()
        if value == "CALL"
    )

    put_votes = sum(
        1
        for value in votes.values()
        if value == "PUT"
    )

    logger.info(
        "Votes -> CALL=%s PUT=%s",
        call_votes,
        put_votes
    )

    # --------------------------------------------------------
    # CALL
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # PUT
    # --------------------------------------------------------

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
    title,
    signal_direction=None,
    signal_timestamp=None
):

    try:

        chart_df = (
            df.tail(60)
            .copy()
        )

        if chart_df.empty:
            return None

        fig, ax = plt.subplots(
            figsize=(12, 6),
            dpi=150
        )

        times = [
            datetime.fromtimestamp(
                float(ts),
                tz=TURKEY_TZ
            )
            for ts in chart_df[
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
                0.78
            )

        else:

            candle_width = 0.0005

        # ----------------------------------------------------
        # Candles
        # ----------------------------------------------------

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

                candle_color = "#00c878"

            else:

                candle_color = "#ff3344"

            # wick
            ax.plot(
                [x, x],
                [l, h],
                color=candle_color,
                linewidth=1.0
            )

            # body
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

                facecolor=candle_color,

                edgecolor=candle_color
            )

            ax.add_patch(
                rect
            )

        # ----------------------------------------------------
        # Signal marker
        # ----------------------------------------------------

        if signal_timestamp is not None:

            signal_matches = chart_df[
                chart_df["timestamp"]
                ==
                signal_timestamp
            ]

            if not signal_matches.empty:

                signal_row = (
                    signal_matches.iloc[0]
                )

                signal_x = (
                    mdates.date2num(
                        datetime.fromtimestamp(
                            float(signal_timestamp),
                            tz=TURKEY_TZ
                        )
                    )
                )

                if signal_direction == "CALL":

                    ax.annotate(
                        "CALL",
                        xy=(
                            signal_x,
                            float(
                                signal_row["high"]
                            )
                        ),
                        xytext=(
                            signal_x,
                            float(
                                signal_row["high"]
                            )
                            +
                            (
                                float(
                                    signal_row["high"]
                                )
                                -
                                float(
                                    signal_row["low"]
                                )
                            )
                            * 1.5
                        ),
                        ha="center",
                        fontweight="bold",
                        arrowprops={
                            "arrowstyle": "->"
                        }
                    )

                elif signal_direction == "PUT":

                    ax.annotate(
                        "PUT",
                        xy=(
                            signal_x,
                            float(
                                signal_row["low"]
                            )
                        ),
                        xytext=(
                            signal_x,
                            float(
                                signal_row["low"]
                            )
                            -
                            (
                                float(
                                    signal_row["high"]
                                )
                                -
                                float(
                                    signal_row["low"]
                                )
                            )
                            * 1.5
                        ),
                        ha="center",
                        fontweight="bold",
                        arrowprops={
                            "arrowstyle": "->"
                        }
                    )

        ax.set_title(
            f"{ASSET_NAME} OTC | {title}",
            fontsize=13,
            fontweight="bold"
        )

        ax.grid(
            True,
            linestyle="--",
            alpha=0.35
        )

        ax.xaxis.set_major_formatter(
            mdates.DateFormatter(
                "%H:%M:%S",
                tz=TURKEY_TZ
            )
        )

        fig.autofmt_xdate()

        plt.tight_layout()

        file_path = (
            "eurusd_otc_"
            f"{int(time.time())}.png"
        )

        plt.savefig(
            file_path,
            dpi=150,
            bbox_inches="tight"
        )

        plt.close(fig)

        return file_path

    except Exception as e:

        logger.exception(
            "Chart generation error: %s",
            e
        )

        return None


# ============================================================
# WAIT UNTIL
# ============================================================

async def wait_until(
    timestamp
):

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

    closed_candle_timestamp = int(
        latest["timestamp"]
    )

    # --------------------------------------------------------
    # Target trade candle starts immediately after
    # the analyzed candle.
    # --------------------------------------------------------

    entry_timestamp = (
        closed_candle_timestamp +
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

    if signal["direction"] == "CALL":

        direction_text = (
            "🟢 CALL / UP"
        )

    else:

        direction_text = (
            "🔴 PUT / DOWN"
        )

    lines = [

        "🎯 <b>EUR/USD OTC</b>",

        "",

        "📡 <b>مصدر البيانات:</b> "
        "Pocket Option",

        "🕯️ <b>الشموع:</b> "
        "مغلقة فقط - بدون شموع وهمية",

        "",

        f"🚀 <b>الاتجاه:</b> "
        f"{direction_text}",

        f"⭐ <b>التأكيدات:</b> "
        f"{signal['votes']}/9",

        f"⏰ <b>بداية الصفقة:</b> "
        f"{entry_time.strftime('%H:%M:%S')}",

        f"🏁 <b>انتهاء الصفقة:</b> "
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

        lines.append(
            f"{icon} {name}: {vote}"
        )

    lines.extend([

        "",

        "🛡️ <b>بدون مضاعفات</b>",

        "🤖 <b>التنفيذ الآلي:</b> متوقف",

        "",

        "⚠️ الإشارة تحليلية "
        "وليست ضمانًا للربح."
    ])

    return (
        "\n".join(lines),
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

    # هامش صغير جدًا لتجنب مشاكل floating point
    epsilon = 1e-10

    difference = (
        exit_price -
        entry_price
    )

    if abs(difference) <= epsilon:

        return "TIE"

    if direction == "CALL":

        if difference > 0:
            return "WIN"

        return "LOSS"

    if direction == "PUT":

        if difference < 0:
            return "WIN"

        return "LOSS"

    return "TIE"


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

    expiry_time = (
        datetime.fromtimestamp(
            expiry_timestamp,
            tz=TURKEY_TZ
        )
    )

    result_message = (

        "📊 <b>نتيجة EUR/USD OTC</b>\n\n"

        f"🚀 <b>الإشارة:</b> "
        f"{signal['direction']}\n"

        f"🏁 <b>النتيجة:</b> "
        f"<b>{result_text}</b>\n\n"

        f"💵 <b>السعر المرجعي للدخول:</b> "
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

    chart_path = generate_chart(
        result_df,
        f"RESULT: {result}"
    )

    if chart_path:

        sent = send_telegram_photo(
            chart_path,
            result_message
        )

        try:
            os.remove(chart_path)
        except OSError:
            pass

        return sent

    return send_telegram_message(
        result_message
    )


# ============================================================
# CHECK RESULT CANDLE
# ============================================================

def get_trade_exit_price(
    result_df,
    entry_timestamp
):

    if result_df is None:
        return None

    result_df = normalize_candles(
        result_df
    )

    if result_df is None:
        return None

    # الصفقة تبدأ عند entry_timestamp
    # شمعة هذه الدقيقة هي شمعة انتهاء الصفقة
    matching = result_df[
        result_df["timestamp"]
        ==
        float(entry_timestamp)
    ]

    if matching.empty:

        return None

    return float(
        matching.iloc[0]["close"]
    )


# ============================================================
# WAIT FOR RESULT CANDLE
# ============================================================

async def fetch_confirmed_result(
    entry_timestamp
):

    # ننتظر إلى ما بعد انتهاء الشمعة
    expiry_timestamp = (
        entry_timestamp +
        CANDLE_PERIOD
    )

    await wait_until(
        expiry_timestamp +
        RESULT_GRACE_SECONDS
    )

    # نعيد المحاولة عدة مرات
    # لأن Pocket Option قد يتأخر لحظيًا
    for attempt in range(1, 8):

        try:

            raw_df = await asyncio.to_thread(
                fetch_pocket_option_candles
            )

            result_df = get_closed_candles(
                raw_df
            )

            if result_df is not None:

                exit_price = (
                    get_trade_exit_price(
                        result_df,
                        entry_timestamp
                    )
                )

                if exit_price is not None:

                    logger.info(
                        "Confirmed exit price: %.5f",
                        exit_price
                    )

                    return (
                        result_df,
                        exit_price
                    )

        except Exception as e:

            logger.warning(
                "Result attempt %s failed: %s",
                attempt,
                e
            )

        await asyncio.sleep(3)

    return None, None


# ============================================================
# START MESSAGE
# ============================================================

def send_start_message():

    message = (

        "🤖 <b>بوت EUR/USD OTC بدأ</b>\n\n"

        "📡 <b>مصدر البيانات:</b> "
        "Pocket Option\n"

        "🕯️ <b>الشموع:</b> "
        "حقيقية ومغلقة فقط\n"

        "🚫 <b>الشموع الوهمية:</b> متوقفة\n"

        "📊 <b>الفريم:</b> 1 دقيقة\n"

        "🎯 <b>الحد الأدنى للإشارة:</b> "
        "7/9\n"

        "⏱️ <b>مدة الصفقة:</b> "
        "1 دقيقة\n"

        "📸 <b>الشارت:</b> سيتم إرساله مع "
        "الإشارة والنتيجة\n"

        "📈 <b>الإحصائيات:</b> WIN / LOSS / TIE\n"

        "🤖 <b>التنفيذ الآلي:</b> متوقف\n\n"

        "⏳ <b>البوت الآن يراقب "
        "EUR/USD OTC...</b>"
    )

    send_telegram_message(
        message
    )


# ============================================================
# MAIN
# ============================================================

async def main():

    global total_signals

    load_stats()

    # --------------------------------------------------------
    # Validate environment
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
    # Connect
    # --------------------------------------------------------

    connect_pocket_option()

    send_start_message()

    last_processed_candle = None

    logger.info(
        "Bot is now monitoring %s...",
        ASSET
    )

    # --------------------------------------------------------
    # MAIN LOOP
    # --------------------------------------------------------

    while True:

        try:

            # ------------------------------------------------
            # Connection check
            # ------------------------------------------------

            if not is_pocket_option_connected():

                try:

                    reconnect_pocket_option()

                except Exception as e:

                    logger.error(
                        "Reconnect failed: %s",
                        e
                    )

                    await asyncio.sleep(10)

                    continue

            # ------------------------------------------------
            # Fetch real candles
            # ------------------------------------------------

            raw_df = await asyncio.to_thread(
                fetch_pocket_option_candles
            )

            df = get_closed_candles(
                raw_df
            )

            if df is None:

                logger.info(
                    "⏳ في انتظار بيانات "
                    "EUR/USD OTC الحقيقية..."
                )

                await asyncio.sleep(5)

                continue

            # ------------------------------------------------
            # Latest CLOSED candle
            # ------------------------------------------------

            latest_timestamp = int(
                df.iloc[-1]["timestamp"]
            )

            latest_time = datetime.fromtimestamp(
                latest_timestamp,
                tz=TURKEY_TZ
            )

            logger.info(
                "Latest closed candle: %s",
                latest_time.strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            )

            # ------------------------------------------------
            # Don't analyze same candle twice
            # ------------------------------------------------

            if (
                last_processed_candle
                ==
                latest_timestamp
            ):

                await asyncio.sleep(2)

                continue

            # ------------------------------------------------
            # Mark candle processed
            # ------------------------------------------------

            last_processed_candle = (
                latest_timestamp
            )

            logger.info(
                "Analyzing CLOSED candle %s...",
                latest_time.strftime(
                    "%H:%M:%S"
                )
            )

            # ------------------------------------------------
            # Generate signal
            # ------------------------------------------------

            signal = get_strong_signal(
                df
            )

            if signal is None:

                logger.info(
                    "No signal. "
                    "Less than %s confirmations.",
                    MIN_CONFIRMATIONS
                )

                await asyncio.sleep(2)

                continue

            # ------------------------------------------------
            # Build signal
            # ------------------------------------------------

            (
                message,
                entry_timestamp,
                expiry_timestamp,
                entry_price
            ) = build_signal_message(
                signal,
                df
            )

            # ------------------------------------------------
            # Ensure signal is for the FUTURE candle
            # ------------------------------------------------

            remaining_to_entry = (
                entry_timestamp -
                get_server_time()
            )

            if remaining_to_entry <= 2:

                logger.warning(
                    "Signal is too late. "
                    "Skipping this candle."
                )

                continue

            # ------------------------------------------------
            # Count signal
            # ------------------------------------------------

            total_signals += 1

            save_stats()

            logger.info(
                "======================================"
            )

            logger.info(
                "SIGNAL: %s | %s/9",
                signal["direction"],
                signal["votes"]
            )

            logger.info(
                "Entry: %s",
                datetime.fromtimestamp(
                    entry_timestamp,
                    tz=TURKEY_TZ
                ).strftime(
                    "%H:%M:%S"
                )
            )

            logger.info(
                "Expiry: %s",
                datetime.fromtimestamp(
                    expiry_timestamp,
                    tz=TURKEY_TZ
                ).strftime(
                    "%H:%M:%S"
                )
            )

            logger.info(
                "======================================"
            )

            # ------------------------------------------------
            # Generate signal chart
            # ------------------------------------------------

            chart_path = generate_chart(
                df,
                (
                    f"{signal['direction']} "
                    f"| {signal['votes']}/9"
                ),
                signal_direction=signal[
                    "direction"
                ],
                signal_timestamp=latest_timestamp
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

            # ------------------------------------------------
            # Wait for trade expiry and confirm
            # ------------------------------------------------

            (
                result_df,
                exit_price
            ) = await fetch_confirmed_result(
                entry_timestamp
            )

            # ------------------------------------------------
            # Result unavailable
            # ------------------------------------------------

            if (
                result_df is None
                or
                exit_price is None
            ):

                send_telegram_message(

                    "⚠️ <b>تعذر تأكيد نتيجة "
                    "EUR/USD OTC</b>\n\n"

                    f"🚀 الإشارة: "
                    f"{signal['direction']}\n"

                    "❌ لم تصل شمعة انتهاء "
                    "الصفقة من Pocket Option.\n\n"

                    "🛡️ <b>لم يتم احتساب "
                    "WIN أو LOSS</b> "
                    "حتى لا يتم تسجيل نتيجة خاطئة."
                )

                await asyncio.sleep(2)

                continue

            # ------------------------------------------------
            # Calculate result
            # ------------------------------------------------

            result = calculate_result(
                signal["direction"],
                entry_price,
                exit_price
            )

            # ------------------------------------------------
            # Send result + chart
            # ------------------------------------------------

            send_result(
                signal,
                entry_price,
                exit_price,
                expiry_timestamp,
                result_df,
                result
            )

            await asyncio.sleep(2)

        # ----------------------------------------------------
        # Keyboard stop
        # ----------------------------------------------------

        except KeyboardInterrupt:

            logger.info(
                "Bot stopped manually."
            )

            break

        # ----------------------------------------------------
        # Unexpected error
        # ----------------------------------------------------

        except Exception as e:

            logger.exception(
                "Main loop error: %s",
                e
            )

            try:

                send_telegram_message(

                    "⚠️ <b>حدث خطأ في البوت</b>\n\n"

                    f"<code>{str(e)[:800]}</code>\n\n"

                    "🔄 سيتم محاولة إعادة الاتصال "
                    "والاستمرار."
                )

            except Exception:
                pass

            await asyncio.sleep(10)

            try:

                if not is_pocket_option_connected():

                    reconnect_pocket_option()

            except Exception as reconnect_error:

                logger.error(
                    "Automatic reconnect error: %s",
                    reconnect_error
                )

                await asyncio.sleep(10)

    # --------------------------------------------------------
    # Shutdown
    # --------------------------------------------------------

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
