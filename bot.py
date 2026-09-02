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
# CONFIGURATION
# ============================================================

# ------------------------------------------------------------
# Twelve Data
# ------------------------------------------------------------

TWELVE_DATA_API_KEY = os.getenv(
    "TWELVE_DATA_API_KEY",
    ""
).strip()

TWELVE_DATA_URL = (
    "https://api.twelvedata.com/time_series"
)

# ------------------------------------------------------------
# REAL FOREX
# ------------------------------------------------------------

SYMBOL = "EUR/USD"
SYMBOL_NAME = "EUR/USD"

# 1-minute candles
INTERVAL = "1min"

# Number of candles requested
OUTPUTSIZE = 180

# Minimum candles required
MIN_CANDLES = 80

# ------------------------------------------------------------
# TRADE
# ------------------------------------------------------------

# Trade duration = 2 minutes
TRADE_DURATION_SECONDS = 120

# Signal is for the next candle.
# Therefore, when a closed candle is detected,
# the next candle starts 1 minute later.
SIGNAL_LEAD_SECONDS = 60

# ------------------------------------------------------------
# SCORE
# ------------------------------------------------------------

# Do NOT send unless score >= 80/100
MIN_SCORE = 80

# Minimum difference between CALL and PUT scores
MIN_SCORE_EDGE = 10

# ------------------------------------------------------------
# Telegram
# ------------------------------------------------------------

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
).strip()

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    ""
).strip()

# ------------------------------------------------------------
# Timezone
# ------------------------------------------------------------

TURKEY_TZ = pytz.timezone(
    "Europe/Istanbul"
)

# ------------------------------------------------------------
# Statistics
# ------------------------------------------------------------

STATS_FILE = "trading_stats.json"

# ------------------------------------------------------------
# Polling
# ------------------------------------------------------------

POLL_SECONDS = 10

# Wait a little after candle expiry
RESULT_GRACE_SECONDS = 5

# API request timeout
REQUEST_TIMEOUT = 20


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format=(
        "%(asctime)s | "
        "%(levelname)s | "
        "%(message)s"
    )
)

logger = logging.getLogger(
    "EURUSD_REAL_FOREX_BOT"
)


# ============================================================
# GLOBAL STATE
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
            "Statistics loaded | "
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

def send_telegram_message(
    message
):

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

        "chat_id":
            TELEGRAM_CHAT_ID,

        "text":
            message,

        "parse_mode":
            "HTML",

        "disable_web_page_preview":
            True
    }

    try:

        response = requests.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        data = response.json()

        if not data.get("ok", False):

            logger.error(
                "Telegram API rejected message: %s",
                data
            )

            return False

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

    if not os.path.exists(
        photo_path
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

        data = response.json()

        if not data.get("ok", False):

            logger.error(
                "Telegram photo rejected: %s",
                data
            )

            return False

        return True

    except Exception as e:

        logger.error(
            "Telegram photo error: %s",
            e
        )

        return False


# ============================================================
# TIME
# ============================================================

def utc_now_timestamp():

    return int(
        datetime.now(
            timezone.utc
        ).timestamp()
    )


def format_time(
    timestamp
):

    return datetime.fromtimestamp(
        float(timestamp),
        tz=TURKEY_TZ
    ).strftime(
        "%H:%M:%S"
    )


def format_datetime(
    timestamp
):

    return datetime.fromtimestamp(
        float(timestamp),
        tz=TURKEY_TZ
    ).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


# ============================================================
# TWELVE DATA
# ============================================================

def fetch_forex_candles():

    if not TWELVE_DATA_API_KEY:

        logger.error(
            "TWELVE_DATA_API_KEY is missing."
        )

        return None

    params = {

        "symbol":
            SYMBOL,

        "interval":
            INTERVAL,

        "outputsize":
            OUTPUTSIZE,

        "apikey":
            TWELVE_DATA_API_KEY,

        "format":
            "JSON",

        "timezone":
            "UTC"
    }

    try:

        response = requests.get(
            TWELVE_DATA_URL,
            params=params,
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        data = response.json()

        if "status" in data:

            if data.get("status") == "error":

                logger.error(
                    "Twelve Data error: %s",
                    data.get(
                        "message",
                        "unknown error"
                    )
                )

                return None

        if "code" in data:

            logger.error(
                "Twelve Data API error: %s",
                data
            )

            return None

        values = data.get(
            "values"
        )

        if not values:

            logger.warning(
                "Twelve Data returned no candles."
            )

            return None

        df = pd.DataFrame(
            values
        )

        if df.empty:

            return None

        # ----------------------------------------------------
        # Rename
        # ----------------------------------------------------

        rename = {

            "datetime":
                "timestamp",

            "open":
                "open",

            "high":
                "high",

            "low":
                "low",

            "close":
                "close",

            "volume":
                "volume"
        }

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

                logger.error(
                    "Missing Twelve Data column: %s",
                    column
                )

                return None

        # ----------------------------------------------------
        # Timestamp
        # ----------------------------------------------------

        df["timestamp"] = pd.to_datetime(
            df["timestamp"],
            utc=True,
            errors="coerce"
        )

        df.dropna(
            subset=[
                "timestamp"
            ],
            inplace=True
        )

        df["timestamp"] = (
            df["timestamp"]
            .astype("int64")
            // 10**9
        )

        # ----------------------------------------------------
        # Numeric
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

        if "volume" in df.columns:

            df["volume"] = pd.to_numeric(
                df["volume"],
                errors="coerce"
            )

        # ----------------------------------------------------
        # Clean
        # ----------------------------------------------------

        df.dropna(
            subset=[
                "open",
                "high",
                "low",
                "close"
            ],
            inplace=True
        )

        df = df[
            (df["high"] >= df["open"]) &
            (df["high"] >= df["close"]) &
            (df["low"] <= df["open"]) &
            (df["low"] <= df["close"])
        ]

        df.sort_values(
            "timestamp",
            inplace=True
        )

        df.drop_duplicates(
            subset=[
                "timestamp"
            ],
            keep="last",
            inplace=True
        )

        df.reset_index(
            drop=True,
            inplace=True
        )

        if len(df) < MIN_CANDLES:

            logger.warning(
                "Only %s candles received. "
                "Need at least %s.",
                len(df),
                MIN_CANDLES
            )

            return None

        return df

    except requests.RequestException as e:

        logger.error(
            "Twelve Data HTTP error: %s",
            e
        )

        return None

    except Exception as e:

        logger.exception(
            "Candle fetch error: %s",
            e
        )

        return None


# ============================================================
# CLOSED CANDLES ONLY
# ============================================================

def get_closed_candles(
    df
):

    if df is None:
        return None

    if df.empty:
        return None

    df = df.copy()

    now = utc_now_timestamp()

    current_bucket = (
        now // 60
    ) * 60

    # --------------------------------------------------------
    # Remove currently forming minute.
    #
    # Only candles strictly before the current minute
    # are considered closed.
    # --------------------------------------------------------

    df = df[
        df["timestamp"]
        <
        current_bucket
    ].copy()

    if len(df) < MIN_CANDLES:

        logger.warning(
            "Closed candles=%s, "
            "minimum=%s",
            len(df),
            MIN_CANDLES
        )

        return None

    return df.tail(
        OUTPUTSIZE
    ).reset_index(
        drop=True
    )


# ============================================================
# INDICATORS
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


def calculate_atr(
    df,
    period=14
):

    high = df["high"]

    low = df["low"]

    close = df["close"]

    previous_close = close.shift(1)

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

    return tr.rolling(
        period
    ).mean()


def calculate_indicators(
    df
):

    result = df.copy()

    close = result["close"]

    # --------------------------------------------------------
    # EMA
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    result["rsi"] = calculate_rsi(
        close,
        14
    )

    # --------------------------------------------------------
    # MACD
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Bollinger
    # --------------------------------------------------------

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
        (
            2 *
            result["bb_std"]
        )
    )

    result["bb_lower"] = (
        result["bb_middle"] -
        (
            2 *
            result["bb_std"]
        )
    )

    # --------------------------------------------------------
    # Stochastic
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # ATR
    # --------------------------------------------------------

    result["atr"] = calculate_atr(
        result,
        14
    )

    # --------------------------------------------------------
    # Momentum
    # --------------------------------------------------------

    result["momentum"] = (
        close -
        close.shift(4)
    )

    # --------------------------------------------------------
    # ROC
    # --------------------------------------------------------

    result["roc"] = (
        close.pct_change(
            periods=5
        ) *
        100
    )

    # --------------------------------------------------------
    # Candle body
    # --------------------------------------------------------

    result["candle_body"] = (
        result["close"] -
        result["open"]
    )

    result["candle_range"] = (
        result["high"] -
        result["low"]
    )

    result["body_ratio"] = (
        result["candle_body"].abs() /
        result["candle_range"].replace(
            0,
            np.nan
        )
    )

    # --------------------------------------------------------
    # Trend slope
    # --------------------------------------------------------

    result["ema21_slope"] = (
        result["ema_21"] -
        result["ema_21"].shift(3)
    )

    # --------------------------------------------------------
    # Remove invalid rows
    # --------------------------------------------------------

    result.replace(
        [np.inf, -np.inf],
        np.nan,
        inplace=True
    )

    result.dropna(
        inplace=True
    )

    result.reset_index(
        drop=True,
        inplace=True
    )

    return result


# ============================================================
# SIGNAL ENGINE
# ============================================================

def analyze_market(
    df
):

    if df is None:
        return None

    if len(df) < MIN_CANDLES:
        return None

    data = calculate_indicators(
        df
    )

    if data is None:
        return None

    if len(data) < 60:
        return None

    last = data.iloc[-1]

    previous = data.iloc[-2]

    call_score = 0
    put_score = 0

    confirmations = []

    # ========================================================
    # 1. EMA STRUCTURE
    # ========================================================

    if (
        last["ema_9"] >
        last["ema_21"] >
        last["ema_50"]
    ):

        call_score += 12

        confirmations.append(
            "EMA Trend: CALL"
        )

    elif (
        last["ema_9"] <
        last["ema_21"] <
        last["ema_50"]
    ):

        put_score += 12

        confirmations.append(
            "EMA Trend: PUT"
        )

    else:

        confirmations.append(
            "EMA Trend: NEUTRAL"
        )

    # ========================================================
    # 2. EMA MOMENTUM
    # ========================================================

    if (
        last["ema_21"] >
        previous["ema_21"]
        and
        last["ema_9"] >
        previous["ema_9"]
    ):

        call_score += 8

        confirmations.append(
            "EMA Momentum: CALL"
        )

    elif (
        last["ema_21"] <
        previous["ema_21"]
        and
        last["ema_9"] <
        previous["ema_9"]
    ):

        put_score += 8

        confirmations.append(
            "EMA Momentum: PUT"
        )

    else:

        confirmations.append(
            "EMA Momentum: NEUTRAL"
        )

    # ========================================================
    # 3. RSI
    # ========================================================

    rsi = float(
        last["rsi"]
    )

    if (
        52 <= rsi <= 68
        and
        rsi > previous["rsi"]
    ):

        call_score += 10

        confirmations.append(
            f"RSI: CALL ({rsi:.1f})"
        )

    elif (
        32 <= rsi <= 48
        and
        rsi < previous["rsi"]
    ):

        put_score += 10

        confirmations.append(
            f"RSI: PUT ({rsi:.1f})"
        )

    else:

        confirmations.append(
            f"RSI: NEUTRAL ({rsi:.1f})"
        )

    # ========================================================
    # 4. MACD
    # ========================================================

    if (
        last["macd"] >
        last["macd_signal"]
        and
        last["macd_hist"] >
        0
        and
        last["macd_hist"] >
        previous["macd_hist"]
    ):

        call_score += 12

        confirmations.append(
            "MACD: CALL"
        )

    elif (
        last["macd"] <
        last["macd_signal"]
        and
        last["macd_hist"] <
        0
        and
        last["macd_hist"] <
        previous["macd_hist"]
    ):

        put_score += 12

        confirmations.append(
            "MACD: PUT"
        )

    else:

        confirmations.append(
            "MACD: NEUTRAL"
        )

    # ========================================================
    # 5. BOLLINGER
    # ========================================================

    if (
        last["close"] >
        last["bb_middle"]
        and
        last["close"] <
        last["bb_upper"]
    ):

        call_score += 8

        confirmations.append(
            "Bollinger: CALL"
        )

    elif (
        last["close"] <
        last["bb_middle"]
        and
        last["close"] >
        last["bb_lower"]
    ):

        put_score += 8

        confirmations.append(
            "Bollinger: PUT"
        )

    else:

        confirmations.append(
            "Bollinger: NEUTRAL"
        )

    # ========================================================
    # 6. STOCHASTIC
    # ========================================================

    k = float(
        last["stoch_k"]
    )

    d = float(
        last["stoch_d"]
    )

    previous_k = float(
        previous["stoch_k"]
    )

    previous_d = float(
        previous["stoch_d"]
    )

    if (
        k > d
        and
        previous_k <= previous_d
        and
        k < 80
    ):

        call_score += 10

        confirmations.append(
            "Stochastic: CALL"
        )

    elif (
        k < d
        and
        previous_k >= previous_d
        and
        k > 20
    ):

        put_score += 10

        confirmations.append(
            "Stochastic: PUT"
        )

    else:

        confirmations.append(
            "Stochastic: NEUTRAL"
        )

    # ========================================================
    # 7. MOMENTUM
    # ========================================================

    if (
        last["momentum"] > 0
        and
        last["roc"] > 0
    ):

        call_score += 10

        confirmations.append(
            "Momentum: CALL"
        )

    elif (
        last["momentum"] < 0
        and
        last["roc"] < 0
    ):

        put_score += 10

        confirmations.append(
            "Momentum: PUT"
        )

    else:

        confirmations.append(
            "Momentum: NEUTRAL"
        )

    # ========================================================
    # 8. CANDLE CONFIRMATION
    # ========================================================

    body_ratio = float(
        last["body_ratio"]
    )

    if (
        last["close"] >
        last["open"]
        and
        body_ratio >= 0.60
    ):

        call_score += 10

        confirmations.append(
            "Candle: CALL"
        )

    elif (
        last["close"] <
        last["open"]
        and
        body_ratio >= 0.60
    ):

        put_score += 10

        confirmations.append(
            "Candle: PUT"
        )

    else:

        confirmations.append(
            "Candle: NEUTRAL"
        )

    # ========================================================
    # 9. TREND STRENGTH / ATR
    # ========================================================

    atr = float(
        last["atr"]
    )

    ema_distance = abs(
        last["ema_21"] -
        last["ema_50"]
    )

    if (
        atr > 0
        and
        ema_distance / atr >= 0.50
        and
        last["ema_21"] >
        last["ema_50"]
        and
        last["ema21_slope"] > 0
    ):

        call_score += 10

        confirmations.append(
            "Trend Strength: CALL"
        )

    elif (
        atr > 0
        and
        ema_distance / atr >= 0.50
        and
        last["ema_21"] <
        last["ema_50"]
        and
        last["ema21_slope"] < 0
    ):

        put_score += 10

        confirmations.append(
            "Trend Strength: PUT"
        )

    else:

        confirmations.append(
            "Trend Strength: NEUTRAL"
        )

    # ========================================================
    # 10. SUPPORT / RESISTANCE
    # ========================================================

    recent_high = (
        data["high"]
        .tail(30)
        .max()
    )

    recent_low = (
        data["low"]
        .tail(30)
        .min()
    )

    price = float(
        last["close"]
    )

    total_range = (
        recent_high -
        recent_low
    )

    if total_range > 0:

        position = (
            price -
            recent_low
        ) / total_range

    else:

        position = 0.5

    if (
        position > 0.50
        and
        price > last["ema_21"]
    ):

        call_score += 10

        confirmations.append(
            "Structure: CALL"
        )

    elif (
        position < 0.50
        and
        price < last["ema_21"]
    ):

        put_score += 10

        confirmations.append(
            "Structure: PUT"
        )

    else:

        confirmations.append(
            "Structure: NEUTRAL"
        )

    # ========================================================
    # FINAL SCORE
    # ========================================================

    maximum_possible = 110

    if call_score > put_score:

        direction = "CALL"

        raw_score = call_score

    elif put_score > call_score:

        direction = "PUT"

        raw_score = put_score

    else:

        logger.info(
            "Market is tied. No signal."
        )

        return None

    # Convert to 100-point scale
    score = (
        raw_score /
        maximum_possible
    ) * 100

    score = round(
        score,
        2
    )

    edge = abs(
        call_score -
        put_score
    )

    logger.info(
        "Market analysis | "
        "CALL=%s PUT=%s SCORE=%s EDGE=%s",
        call_score,
        put_score,
        score,
        edge
    )

    # ========================================================
    # STRICT FILTER
    # ========================================================

    if score < MIN_SCORE:

        logger.info(
            "No signal: score %.2f < %s",
            score,
            MIN_SCORE
        )

        return None

    if edge < MIN_SCORE_EDGE:

        logger.info(
            "No signal: score edge %s < %s",
            edge,
            MIN_SCORE_EDGE
        )

        return None

    # --------------------------------------------------------
    # Additional trend sanity filter
    # --------------------------------------------------------

    if direction == "CALL":

        if not (
            last["ema_9"] >
            last["ema_21"]
        ):

            logger.info(
                "CALL rejected by trend filter."
            )

            return None

    else:

        if not (
            last["ema_9"] <
            last["ema_21"]
        ):

            logger.info(
                "PUT rejected by trend filter."
            )

            return None

    return {

        "direction":
            direction,

        "score":
            score,

        "call_score":
            call_score,

        "put_score":
            put_score,

        "edge":
            edge,

        "strategies":
            confirmations,

        "signal_candle_timestamp":
            int(
                last["timestamp"]
            ),

        "reference_price":
            float(
                last["close"]
            )
    }


# ============================================================
# CHART
# ============================================================

def generate_chart(
    df,
    title,
    signal_direction=None
):

    try:

        chart_df = (
            df.tail(70)
            .copy()
        )

        if chart_df.empty:

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
                0.72
            )

        else:

            candle_width = 0.0004

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

                candle_color = "#16a34a"

            else:

                candle_color = "#dc2626"

            # Wick

            ax.plot(
                [x, x],
                [l, h],
                color=candle_color,
                linewidth=1
            )

            # Body

            rect = Rectangle(

                (
                    x -
                    candle_width / 2,

                    min(o, c)
                ),

                candle_width,

                max(
                    abs(c - o),
                    0.000001
                ),

                facecolor=candle_color,

                edgecolor=candle_color
            )

            ax.add_patch(
                rect
            )

        # ----------------------------------------------------
        # EMA lines
        # ----------------------------------------------------

        indicator_df = calculate_indicators(
            chart_df
        )

        if (
            indicator_df is not None
            and
            not indicator_df.empty
        ):

            indicator_times = [

                datetime.fromtimestamp(
                    float(ts),
                    tz=timezone.utc
                )

                for ts in indicator_df[
                    "timestamp"
                ]
            ]

            indicator_x = (
                mdates.date2num(
                    indicator_times
                )
            )

            ax.plot(
                indicator_x,
                indicator_df[
                    "ema_9"
                ],
                linewidth=1.1,
                label="EMA 9"
            )

            ax.plot(
                indicator_x,
                indicator_df[
                    "ema_21"
                ],
                linewidth=1.1,
                label="EMA 21"
            )

            ax.plot(
                indicator_x,
                indicator_df[
                    "ema_50"
                ],
                linewidth=1.1,
                label="EMA 50"
            )

        ax.set_title(
            f"{SYMBOL_NAME} | {title}",
            fontsize=14,
            fontweight="bold"
        )

        ax.set_ylabel(
            "Price"
        )

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

        file_path = (
            "eurusd_real_"
            f"{int(time.time())}.png"
        )

        plt.savefig(
            file_path,
            dpi=140,
            bbox_inches="tight"
        )

        plt.close(
            fig
        )

        return file_path

    except Exception as e:

        logger.exception(
            "Chart generation error: %s",
            e
        )

        return None


# ============================================================
# SIGNAL MESSAGE
# ============================================================

def build_signal_message(
    signal
):

    signal_candle = (
        signal[
            "signal_candle_timestamp"
        ]
    )

    # --------------------------------------------------------
    # Signal is based on the last closed candle.
    #
    # The next candle begins exactly 60 seconds later.
    # --------------------------------------------------------

    entry_timestamp = (
        signal_candle +
        SIGNAL_LEAD_SECONDS
    )

    expiry_timestamp = (
        entry_timestamp +
        TRADE_DURATION_SECONDS
    )

    direction = signal[
        "direction"
    ]

    if direction == "CALL":

        direction_text = (
            "🟢 CALL / UP"
        )

    else:

        direction_text = (
            "🔴 PUT / DOWN"
        )

    signal_time = format_time(
        signal_candle
    )

    entry_time = format_time(
        entry_timestamp
    )

    expiry_time = format_time(
        expiry_timestamp
    )

    message_lines = [

        "🎯 <b>EUR/USD — REAL FOREX</b>",

        "",

        "📡 <b>مصدر البيانات:</b> "
        "Forex market data",

        "🕯️ <b>الفريم:</b> "
        "1 دقيقة",

        "🚫 <b>OTC:</b> لا",

        "🚫 <b>شموع وهمية:</b> لا",

        "",

        f"🚀 <b>الإشارة:</b> "
        f"{direction_text}",

        f"⭐ <b>قوة الإشارة:</b> "
        f"{signal['score']:.1f}/100",

        f"📊 <b>CALL Score:</b> "
        f"{signal['call_score']}",

        f"📊 <b>PUT Score:</b> "
        f"{signal['put_score']}",

        "",

        f"🕯️ <b>الشمعة المحللة:</b> "
        f"{signal_time}",

        f"🔔 <b>بداية الصفقة:</b> "
        f"{entry_time}",

        f"🏁 <b>انتهاء الصفقة:</b> "
        f"{expiry_time}",

        "⏱️ <b>مدة الصفقة:</b> "
        "دقيقتان",

        "",

        f"💵 <b>السعر المرجعي:</b> "
        f"{signal['reference_price']:.5f}",

        "",

        "🧠 <b>التأكيدات:</b>"
    ]

    for item in signal[
        "strategies"
    ]:

        if "CALL" in item:

            icon = "🟢"

        elif "PUT" in item:

            icon = "🔴"

        else:

            icon = "⚪"

        message_lines.append(
            f"{icon} {item}"
        )

    message_lines.extend([

        "",

        "🛡️ <b>بدون مضاعفات</b>",

        "🤖 <b>التنفيذ الآلي:</b> متوقف",

        "",

        "⚠️ هذا تقييم فني آلي، "
        "وليس ضمانًا للربح."
    ])

    return (
        "\n".join(
            message_lines
        ),
        entry_timestamp,
        expiry_timestamp
    )


# ============================================================
# WAIT
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
# GET ENTRY CANDLE
# ============================================================

def get_candle_by_timestamp(
    df,
    timestamp
):

    if df is None:
        return None

    if df.empty:
        return None

    matches = df[
        df["timestamp"]
        ==
        int(timestamp)
    ]

    if matches.empty:
        return None

    return matches.iloc[0]


# ============================================================
# RESULT
# ============================================================

def calculate_result(
    direction,
    entry_price,
    exit_price
):

    epsilon = 1e-8

    difference = (
        exit_price -
        entry_price
    )

    if abs(
        difference
    ) <= epsilon:

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
# WAIT AND CONFIRM TRADE RESULT
# ============================================================

async def confirm_trade_result(
    entry_timestamp,
    expiry_timestamp
):

    # --------------------------------------------------------
    # Wait until the 2-minute trade has completely expired.
    # --------------------------------------------------------

    await wait_until(
        expiry_timestamp +
        RESULT_GRACE_SECONDS
    )

    for attempt in range(
        1,
        9
    ):

        try:

            raw_df = await asyncio.to_thread(
                fetch_forex_candles
            )

            df = get_closed_candles(
                raw_df
            )

            if df is None:

                await asyncio.sleep(
                    3
                )

                continue

            # ------------------------------------------------
            # Entry candle:
            # starts at entry_timestamp
            #
            # Its OPEN is used as entry price.
            # ------------------------------------------------

            entry_candle = (
                get_candle_by_timestamp(
                    df,
                    entry_timestamp
                )
            )

            # ------------------------------------------------
            # Expiry candle:
            # the second candle of the 2-minute period
            #
            # starts at entry + 60 seconds.
            # Its CLOSE is the expiry price.
            # ------------------------------------------------

            expiry_candle_timestamp = (
                entry_timestamp +
                60
            )

            expiry_candle = (
                get_candle_by_timestamp(
                    df,
                    expiry_candle_timestamp
                )
            )

            if (
                entry_candle is not None
                and
                expiry_candle is not None
            ):

                entry_price = float(
                    entry_candle[
                        "open"
                    ]
                )

                exit_price = float(
                    expiry_candle[
                        "close"
                    ]
                )

                logger.info(
                    "Trade result prices | "
                    "ENTRY=%.5f EXIT=%.5f",
                    entry_price,
                    exit_price
                )

                return (
                    entry_price,
                    exit_price,
                    df
                )

            logger.info(
                "Result attempt %s: "
                "required candles not available yet.",
                attempt
            )

        except Exception as e:

            logger.warning(
                "Result attempt %s failed: %s",
                attempt,
                e
            )

        await asyncio.sleep(
            3
        )

    return (
        None,
        None,
        None
    )


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

    message = (

        "📊 <b>نتيجة EUR/USD REAL FOREX</b>\n\n"

        f"🚀 <b>الإشارة:</b> "
        f"{signal['direction']}\n"

        f"⭐ <b>Score:</b> "
        f"{signal['score']:.1f}/100\n\n"

        f"🏁 <b>النتيجة:</b> "
        f"<b>{result_text}</b>\n\n"

        f"💵 <b>سعر الدخول:</b> "
        f"{entry_price:.5f}\n"

        f"🏁 <b>سعر الانتهاء:</b> "
        f"{exit_price:.5f}\n\n"

        f"⏰ <b>الانتهاء:</b> "
        f"{format_time(expiry_timestamp)}\n\n"

        "📈 <b>الإحصائيات</b>\n"

        f"🟢 WIN: {total_wins}\n"

        f"🔴 LOSS: {total_losses}\n"

        f"⚪ TIE: {total_ties}\n"

        f"🔢 إجمالي الإشارات: "
        f"{total_signals}\n"

        f"📊 الصفقات المكتملة: "
        f"{completed}\n"

        f"🎯 نسبة الفوز: "
        f"{get_win_rate():.2f}%\n\n"

        "🛡️ <b>بدون مضاعفات</b>"
    )

    chart_path = generate_chart(
        result_df,
        f"RESULT {result}"
    )

    if chart_path:

        sent = send_telegram_photo(
            chart_path,
            message
        )

        try:

            os.remove(
                chart_path
            )

        except OSError:
            pass

        return sent

    return send_telegram_message(
        message
    )


# ============================================================
# START MESSAGE
# ============================================================

def send_start_message():

    message = (

        "🤖 <b>EUR/USD REAL FOREX BOT</b>\n\n"

        "✅ <b>السوق:</b> Forex الحقيقي\n"

        "💱 <b>الزوج:</b> EUR/USD\n"

        "🕯️ <b>الفريم:</b> 1 دقيقة\n"

        "⏱️ <b>مدة الصفقة:</b> دقيقتان\n"

        "🔔 <b>التنبيه:</b> قبل بداية الصفقة "
        "بدقيقة كاملة\n"

        f"⭐ <b>أقل Score:</b> "
        f"{MIN_SCORE}/100\n"

        "🚫 <b>OTC:</b> متوقف\n"

        "🚫 <b>شموع وهمية:</b> متوقفة\n"

        "🛡️ <b>المضاعفات:</b> متوقفة\n"

        "📸 <b>الشارت:</b> نعم\n"

        "📊 <b>النتائج:</b> WIN / LOSS / TIE\n\n"

        "⏳ <b>البوت بدأ مراقبة EUR/USD...</b>"
    )

    send_telegram_message(
        message
    )


# ============================================================
# VALIDATE CONFIG
# ============================================================

def validate_config():

    errors = []

    if not TWELVE_DATA_API_KEY:

        errors.append(
            "TWELVE_DATA_API_KEY"
        )

    if not TELEGRAM_BOT_TOKEN:

        errors.append(
            "TELEGRAM_BOT_TOKEN"
        )

    if not TELEGRAM_CHAT_ID:

        errors.append(
            "TELEGRAM_CHAT_ID"
        )

    if errors:

        raise RuntimeError(
            "Missing required environment variables: "
            +
            ", ".join(errors)
        )


# ============================================================
# TEST DATA CONNECTION
# ============================================================

def test_data_connection():

    logger.info(
        "Testing EUR/USD real Forex data..."
    )

    df = fetch_forex_candles()

    if df is None:

        raise RuntimeError(
            "Could not receive EUR/USD "
            "Forex candles from Twelve Data."
        )

    closed = get_closed_candles(
        df
    )

    if closed is None:

        raise RuntimeError(
            "Received data, but there are "
            "not enough closed candles."
        )

    latest = closed.iloc[-1]

    logger.info(
        "Data connection OK | "
        "Closed candles=%s | "
        "Latest=%s | Close=%.5f",
        len(closed),
        format_datetime(
            latest["timestamp"]
        ),
        float(
            latest["close"]
        )
    )

    return closed


# ============================================================
# PROCESS ONE SIGNAL
# ============================================================

async def process_signal(
    signal,
    df
):

    global total_signals

    (
        message,
        entry_timestamp,
        expiry_timestamp
    ) = build_signal_message(
        signal
    )

    # --------------------------------------------------------
    # IMPORTANT:
    #
    # Signal is generated from a closed candle.
    # The next candle begins exactly 60 seconds later.
    # Therefore this message is the "one minute before"
    # notification for that future trade candle.
    # --------------------------------------------------------

    now = time.time()

    remaining = (
        entry_timestamp -
        now
    )

    logger.info(
        "Signal timing | "
        "entry=%s | remaining=%.1fs",
        format_time(
            entry_timestamp
        ),
        remaining
    )

    # If the system is too late, do not send a bad signal.

    if remaining < 45:

        logger.warning(
            "Signal arrived too late "
            "(%.1f seconds before entry). "
            "Skipping.",
            remaining
        )

        return

    # If somehow the signal is too early due to API timing,
    # wait until exactly 60 seconds before entry.

    if remaining > 65:

        await wait_until(
            entry_timestamp -
            60
        )

    # Recalculate.

    remaining = (
        entry_timestamp -
        time.time()
    )

    if (
        remaining < 45
        or
        remaining > 65
    ):

        logger.warning(
            "Signal timing invalid after synchronization. "
            "Skipping."
        )

        return

    # --------------------------------------------------------
    # Count
    # --------------------------------------------------------

    total_signals += 1

    save_stats()

    logger.info(
        "================================================"
    )

    logger.info(
        "STRONG SIGNAL | %s | SCORE %.1f/100",
        signal["direction"],
        signal["score"]
    )

    logger.info(
        "Entry: %s",
        format_datetime(
            entry_timestamp
        )
    )

    logger.info(
        "Expiry: %s",
        format_datetime(
            expiry_timestamp
        )
    )

    logger.info(
        "================================================"
    )

    # --------------------------------------------------------
    # Chart
    # --------------------------------------------------------

    chart_path = generate_chart(

        df,

        (
            f"{signal['direction']} "
            f"| SCORE {signal['score']:.1f}/100"
        ),

        signal_direction=
            signal["direction"]
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

    # --------------------------------------------------------
    # Wait until expiry and get result
    # --------------------------------------------------------

    (
        entry_price,
        exit_price,
        result_df
    ) = await confirm_trade_result(

        entry_timestamp,

        expiry_timestamp
    )

    # --------------------------------------------------------
    # Result unavailable
    # --------------------------------------------------------

    if (
        entry_price is None
        or
        exit_price is None
        or
        result_df is None
    ):

        send_telegram_message(

            "⚠️ <b>تعذر تأكيد نتيجة "
            "EUR/USD</b>\n\n"

            f"🚀 الإشارة: "
            f"{signal['direction']}\n"

            f"⭐ Score: "
            f"{signal['score']:.1f}/100\n\n"

            "❌ لم تصل شموع فترة انتهاء "
            "الصفقة بشكل مؤكد من مصدر البيانات.\n\n"

            "🛡️ <b>لم يتم احتساب WIN أو LOSS</b> "
            "حتى لا يتم تسجيل نتيجة غير مؤكدة."
        )

        return

    # --------------------------------------------------------
    # Calculate
    # --------------------------------------------------------

    result = calculate_result(

        signal["direction"],

        entry_price,

        exit_price
    )

    # --------------------------------------------------------
    # Send
    # --------------------------------------------------------

    send_result(

        signal,

        entry_price,

        exit_price,

        expiry_timestamp,

        result_df,

        result
    )


# ============================================================
# MAIN LOOP
# ============================================================

async def main():

    global last_processed_candle

    validate_config()

    load_stats()

    logger.info(
        "================================================"
    )

    logger.info(
        "EUR/USD REAL FOREX SIGNAL BOT"
    )

    logger.info(
        "No OTC"
    )

    logger.info(
        "No PO_SSID"
    )

    logger.info(
        "No Pocket Option API"
    )

    logger.info(
        "No fake candles"
    )

    logger.info(
        "Trade duration = 2 minutes"
    )

    logger.info(
        "Signal lead = 1 minute"
    )

    logger.info(
        "Minimum score = %s/100",
        MIN_SCORE
    )

    logger.info(
        "================================================"
    )

    # --------------------------------------------------------
    # Test
    # --------------------------------------------------------

    initial_df = test_data_connection()

    # --------------------------------------------------------
    # Telegram start
    # --------------------------------------------------------

    send_start_message()

    # --------------------------------------------------------
    # Main monitoring
    # --------------------------------------------------------

    while True:

        try:

            raw_df = await asyncio.to_thread(
                fetch_forex_candles
            )

            df = get_closed_candles(
                raw_df
            )

            if df is None:

                logger.info(
                    "⏳ Waiting for real "
                    "EUR/USD closed candles..."
                )

                await asyncio.sleep(
                    POLL_SECONDS
                )

                continue

            latest = df.iloc[-1]

            latest_timestamp = int(
                latest["timestamp"]
            )

            latest_time = format_datetime(
                latest_timestamp
            )

            logger
