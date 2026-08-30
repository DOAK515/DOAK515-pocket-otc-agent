import os
import time
import json
import logging
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

# ============================================================
# POCKET OPTION WEBSOCKET
# ============================================================

try:
    from pocketoptionapi import PocketOption
except ImportError:
    PocketOption = None


# ============================================================
# CONFIG
# ============================================================

PO_SSID = (
    os.getenv("PO_SSID", "").strip()
    or os.getenv("PO_UUID", "").strip()
)

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
).strip()

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    ""
).strip()


# ============================================================
# ASSET
# ============================================================

ASSET = "EURUSD_otc"
ASSET_NAME = "EUR/USD (OTC)"

# 1 دقيقة
CANDLE_PERIOD = 60

# عدد الشموع المستخدمة للتحليل
HISTORY_CANDLES = 120

# يجب الحصول على 7 تأكيدات على الأقل
MIN_CONFIRMATIONS = 7

# مدة الصفقة
TRADE_DURATION = 60

# الانتظار بعد بداية شمعة الدخول
RESULT_EXTRA_WAIT = 5

# المنطقة الزمنية للرسائل
TURKEY_TZ = pytz.timezone(
    "Europe/Istanbul"
)

# ملف الإحصائيات
STATS_FILE = "trading_stats.json"

# منع إرسال إشارات متكررة
SIGNAL_COOLDOWN = 3


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
    "POCKET_OPTION_OTC_BOT"
)


# ============================================================
# GLOBAL STATE
# ============================================================

po_client = None

po_connected = False

last_analyzed_candle = None

last_signal_time = 0

trade_number = 0


# ============================================================
# STATISTICS
# ============================================================

total_wins = 0
total_losses = 0
total_ties = 0


def load_stats():

    global total_wins
    global total_losses
    global total_ties
    global trade_number

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

        trade_number = int(
            data.get("trade_number", 0)
        )

        logger.info(
            "Statistics loaded: "
            "WIN=%s LOSS=%s TIE=%s TOTAL=%s",
            total_wins,
            total_losses,
            total_ties,
            get_total_trades()
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

        "trade_number": trade_number,

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


def get_total_trades():

    return (
        total_wins
        + total_losses
        + total_ties
    )


def get_win_rate():

    completed = (
        total_wins
        + total_losses
    )

    if completed == 0:

        return 0.0

    return (
        total_wins /
        completed
    ) * 100.0


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram_message(message):

    if (
        not TELEGRAM_BOT_TOKEN
        or
        not TELEGRAM_CHAT_ID
    ):

        logger.warning(
            "Telegram credentials are missing."
        )

        return None

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

        return response.json()

    except Exception as e:

        logger.error(
            "Telegram message error: %s",
            e
        )

        return None


def send_telegram_photo(
    photo_path,
    caption
):

    if (
        not TELEGRAM_BOT_TOKEN
        or
        not TELEGRAM_CHAT_ID
    ):

        return None

    if not os.path.exists(photo_path):

        return None

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
                "photo": photo_file
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

        return response.json()

    except Exception as e:

        logger.error(
            "Telegram photo error: %s",
            e
        )

        return None


# ============================================================
# POCKET OPTION CONNECTION
# ============================================================

def connect_pocket_option():

    global po_client
    global po_connected

    if PocketOption is None:

        logger.error(
            "PocketOption library is not installed."
        )

        return False

    if not PO_SSID:

        logger.error(
            "PO_SSID is missing."
        )

        return False

    try:

        logger.info(
            "Connecting to Pocket Option..."
        )

        po_client = PocketOption(
            PO_SSID
        )

        ok, error = po_client.connect()

        if not ok:

            logger.error(
                "Pocket Option connection failed: %s",
                error
            )

            po_connected = False

            return False

        # انتظار WebSocket ومزامنة الوقت
        deadline = (
            time.time() + 30
        )

        while (
            time.time() < deadline
        ):

            try:

                connected = (
                    po_client.check_connect()
                )

                synced = (
                    po_client.is_time_synced()
                )

                if connected and synced:

                    break

            except Exception:

                pass

            time.sleep(0.25)

        try:

            connected = (
                po_client.check_connect()
            )

            synced = (
                po_client.is_time_synced()
            )

        except Exception:

            connected = False
            synced = False

        if not connected:

            logger.error(
                "Pocket Option WebSocket "
                "is not connected."
            )

            po_connected = False

            return False

        if not synced:

            logger.error(
                "Pocket Option server time "
                "is not synchronized."
            )

            po_connected = False

            return False

        # الاشتراك في EURUSD OTC
        po_client.subscribe(
            ASSET,
            period=CANDLE_PERIOD
        )

        time.sleep(2)

        po_connected = True

        try:

            server_time = (
                po_client.get_server_datetime()
            )

            logger.info(
                "Pocket Option server time: %s",
                server_time
            )

        except Exception:

            pass

        logger.info(
            "Connected to Pocket Option: %s",
            ASSET
        )

        return True

    except Exception as e:

        logger.exception(
            "Pocket Option connection error: %s",
            e
        )

        po_connected = False

        return False


# ============================================================
# SERVER TIME
# ============================================================

def get_server_timestamp():

    if (
        po_client is None
        or
        not po_connected
    ):

        return int(
            time.time()
        )

    try:

        return int(
            po_client.get_server_timestamp()
        )

    except Exception:

        return int(
            time.time()
        )


# ============================================================
# GET POCKET OPTION CANDLES
# ============================================================

def fetch_pocket_option_candles():

    global po_connected

    if not po_connected:

        if not connect_pocket_option():

            return None

    try:

        candles = (
            po_client
            .get_historical_candles(
                ASSET,
                period=CANDLE_PERIOD,
                offset=45000,
                count_request=1
            )
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

                row = {}

                for field in [
                    "timestamp",
                    "time",
                    "open",
                    "high",
                    "low",
                    "close"
                ]:

                    if hasattr(
                        candle,
                        field
                    ):

                        row[field] = getattr(
                            candle,
                            field
                        )

                rows.append(row)

        df = pd.DataFrame(
            rows
        )

        df = normalize_candles(
            df
        )

        if df is None:

            return None

        if len(df) < 50:

            logger.warning(
                "Not enough candles: %s",
                len(df)
            )

            return None

        # إزالة الشمعة الحالية
        df = remove_forming_candle(
            df
        )

        if df is None:

            return None

        if len(df) < 50:

            return None

        return df

    except Exception as e:

        logger.error(
            "Pocket Option candle error: %s",
            e
        )

        po_connected = False

        return None


# ============================================================
# NORMALIZE CANDLES
# ============================================================

def normalize_candles(df):

    if df is None or df.empty:

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
            "at",
            "created_at"
        ):

            rename_map[column] = (
                "timestamp"
            )

        elif c in (
            "open",
            "o"
        ):

            rename_map[column] = "open"

        elif c in (
            "high",
            "h",
            "max"
        ):

            rename_map[column] = "high"

        elif c in (
            "low",
            "l",
            "min"
        ):

            rename_map[column] = "low"

        elif c in (
            "close",
            "c"
        ):

            rename_map[column] = "close"

    df = df.rename(
        columns=rename_map
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

        logger.error(
            "Unknown candle format: %s",
            list(df.columns)
        )

        return None

    for col in [
        "timestamp",
        "open",
        "high",
        "low",
        "close"
    ]:

        df[col] = pd.to_numeric(
            df[col],
            errors="coerce"
        )

    df = df.dropna(
        subset=required
    )

    if df.empty:

        return None

    # milliseconds -> seconds
    if (
        df["timestamp"].median()
        > 10_000_000_000
    ):

        df["timestamp"] = (
            df["timestamp"] /
            1000.0
        )

    df = (
        df
        .sort_values("timestamp")
        .drop_duplicates(
            subset=["timestamp"],
            keep="last"
        )
        .reset_index(drop=True)
    )

    # فحص OHLC
    if (
        df["high"] < df["low"]
    ).any():

        logger.error(
            "Invalid candle: high < low"
        )

        return None

    if (
        df["high"] < df["open"]
    ).any():

        return None

    if (
        df["high"] < df["close"]
    ).any():

        return None

    if (
        df["low"] > df["open"]
    ).any():

        return None

    if (
        df["low"] > df["close"]
    ).any():

        return None

    return df


# ============================================================
# REMOVE FORMING CANDLE
# ============================================================

def remove_forming_candle(df):

    if df is None or df.empty:

        return None

    server_ts = (
        get_server_timestamp()
    )

    current_bucket = (
        server_ts //
        CANDLE_PERIOD
    ) * CANDLE_PERIOD

    closed = df[
        df["timestamp"]
        < current_bucket
    ].copy()

    closed = (
        closed
        .sort_values("timestamp")
        .drop_duplicates(
            subset=["timestamp"],
            keep="last"
        )
        .reset_index(drop=True)
    )

    if len(closed) < 50:

        return None

    return closed


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

    close = result["close"]

    # EMA
    result["ema_5"] = (
        close
        .ewm(
            span=5,
            adjust=False
        )
        .mean()
    )

    result["ema_9"] = (
        close
        .ewm(
            span=9,
            adjust=False
        )
        .mean()
    )

    result["ema_21"] = (
        close
        .ewm(
            span=21,
            adjust=False
        )
        .mean()
    )

    result["ema_50"] = (
        close
        .ewm(
            span=50,
            adjust=False
        )
        .mean()
    )

    # RSI
    result["rsi"] = calculate_rsi(
        close,
        14
    )

    # MACD
    ema12 = (
        close
        .ewm(
            span=12,
            adjust=False
        )
        .mean()
    )

    ema26 = (
        close
        .ewm(
            span=26,
            adjust=False
        )
        .mean()
    )

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

    # Bollinger
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
        2 * result["bb_std"]
    )

    result["bb_lower"] = (
        result["bb_middle"] -
        2 * result["bb_std"]
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
        highest - lowest
    ).replace(
        0,
        np.nan
    )

    result["stoch_k"] = (
        100 *
        (
            close - lowest
        ) /
        denominator
    ).fillna(50)

    result["stoch_d"] = (
        result["stoch_k"]
        .rolling(3)
        .mean()
        .fillna(50)
    )

    # ATR
    high = result["high"]

    low = result["low"]

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
        .dropna()
        .reset_index(drop=True)
    )

    return result


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

    # --------------------------------------------------------
    # EMA
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
    # RSI
    # --------------------------------------------------------

    if (
        50 <
        last["rsi"] <
        68
        and
        last["rsi"] >
        prev["rsi"]
    ):

        votes["RSI"] = "CALL"

    elif (
        32 <
        last["rsi"] <
        50
        and
        last["rsi"] <
        prev["rsi"]
    ):

        votes["RSI"] = "PUT"

    else:

        votes["RSI"] = "NEUTRAL"

    # --------------------------------------------------------
    # MACD
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
    # BOLLINGER
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
    # STOCHASTIC
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
    # MOMENTUM
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
    # CANDLE CONFIRMATION
    # --------------------------------------------------------

    body = abs(
        last["close"] -
        last["open"]
    )

    candle_range = (
        last["high"] -
        last["low"]
    )

    body_ratio = (
        body /
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
    # SUPPORT / RESISTANCE
    # --------------------------------------------------------

    rh = (
        df["high"]
        .tail(20)
        .max()
    )

    rl = (
        df["low"]
        .tail(20)
        .min()
    )

    p = last["close"]

    if (
        (p - rl) >
        (rh - p)
        and
        p >
        last["ema_21"]
    ):

        votes[
            "Support/Resistance"
        ] = "CALL"

    elif (
        (rh - p) >
        (p - rl)
        and
        p <
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
    # TREND STRENGTH
    # --------------------------------------------------------

    # نتركها محايدة بدل اختراع تأكيد.
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
        for v in votes.values()
        if v == "CALL"
    )

    put_votes = sum(
        1
        for v in votes.values()
        if v == "PUT"
    )

    # يجب أن تكون هناك أفضلية واضحة
    if (
        call_votes >=
        MIN_CONFIRMATIONS
        and
        call_votes >
        put_votes
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
        put_votes >=
        MIN_CONFIRMATIONS
        and
        put_votes >
        call_votes
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
    title,
    signal_index=None,
    result_index=None,
    direction=None,
    result=None
):

    try:

        chart_df = (
            df
            .tail(50)
            .copy()
        )

        fig, ax = plt.subplots(
            figsize=(12, 6),
            dpi=150
        )

        fig.patch.set_facecolor(
            "#121212"
        )

        ax.set_facecolor(
            "#1e1e1e"
        )

        times = [
            datetime.fromtimestamp(
                float(ts),
                tz=TURKEY_TZ
            )
            for ts
            in chart_df["timestamp"]
        ]

        x_values = (
            mdates.date2num(
                times
            )
        )

        if len(x_values) > 1:

            candle_width = (
                np.median(
                    np.diff(
                        x_values
                    )
                ) * 0.78
            )

        else:

            candle_width = 0.0005

        for i, (
            idx,
            row
        ) in enumerate(
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

            color = (
                "#00df89"
                if c >= o
                else "#ff3344"
            )

            ax.plot(
                [x, x],
                [l, h],
                color=color,
                linewidth=1.2
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
                edgecolor=color,
                linewidth=0.5
            )

            ax.add_patch(
                rect
            )

        # عنوان
        ax.set_title(
            f"EUR/USD OTC | {title}",
            color="white",
            fontsize=13,
            fontweight="bold"
        )

        ax.tick_params(
            colors="#aaaaaa",
            labelsize=9
        )

        ax.grid(
            True,
            color="#2a2a2a",
            linestyle="--",
            alpha=0.6
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
            "eurusd_otc_"
            f"{int(time.time())}.png"
        )

        plt.savefig(
            file_path,
            bbox_inches="tight",
            facecolor=fig.get_facecolor(),
            edgecolor="none",
            dpi=150
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
# FIND CANDLE BY TIMESTAMP
# ============================================================

def find_candle(
    df,
    timestamp
):

    if df is None:

        return None

    matches = df[
        np.isclose(
            df["timestamp"],
            timestamp
        )
    ]

    if matches.empty:

        return None

    return matches.iloc[-1]


# ============================================================
# SIGNAL MESSAGE
# ============================================================

def build_signal_message(
    trade,
    signal
):

    direction = (
        "🟢 CALL / UP"
        if signal["direction"]
        == "CALL"
        else
        "🔴 PUT / DOWN"
    )

    entry_dt = datetime.fromtimestamp(
        trade["entry_timestamp"],
        tz=TURKEY_TZ
    )

    signal_dt = datetime.fromtimestamp(
        trade["signal_timestamp"],
        tz=TURKEY_TZ
    )

    msg = [

        f"🎯 <b>صفقة #{trade['number']}</b>",

        "",

        "💱 <b>الزوج:</b> "
        f"{ASSET_NAME}",

        f"🚀 <b>الاتجاه:</b> "
        f"{direction}",

        "",

        f"🕐 <b>وقت الإشارة:</b> "
        f"{signal_dt.strftime('%H:%M:%S')}",

        f"⏰ <b>وقت الدخول:</b> "
        f"{entry_dt.strftime('%H:%M:%S')}",

        "⏱️ <b>مدة الصفقة:</b> 1 دقيقة",

        "",

        f"⭐ <b>التأكيد:</b> "
        f"{signal['votes']}/9",

        f"💵 <b>السعر المرجعي:</b> "
        f"{trade['signal_price']:.5f}",

        "",

        "📊 <b>التأكيدات:</b>"
    ]

    for name, vote in (
        signal["strategies"]
        .items()
    ):

        icon = (
            "🟢"
            if vote == "CALL"
            else
            "🔴"
            if vote == "PUT"
            else
            "⚪"
        )

        msg.append(
            f"{icon} {name}: {vote}"
        )

    msg.extend(
        [
            "",
            "🛡️ <b>بدون مضاعفات</b>",
            "🤖 <b>التداول الآلي: متوقف</b>",
            "",
            "📡 <b>مصدر الشموع:</b> "
            "Pocket Option OTC"
        ]
    )

    return "\n".join(
        msg
    )


# ============================================================
# SEND SIGNAL
# ============================================================

def send_signal(
    df,
    signal
):

    global trade_number

    latest = df.iloc[-1]

    signal_timestamp = float(
        latest["timestamp"]
    )

    signal_price = float(
        latest["close"]
    )

    # --------------------------------------------------------
    # الدخول هو بداية الشمعة التالية
    # --------------------------------------------------------

    entry_timestamp = (
        signal_timestamp +
        CANDLE_PERIOD
    )

    trade_number += 1

    trade = {

        "number":
            trade_number,

        "direction":
            signal["direction"],

        "signal_timestamp":
            signal_timestamp,

        "entry_timestamp":
            entry_timestamp,

        "signal_price":
            signal_price,

        "entry_price":
            None,

        "exit_price":
            None,

        "result":
            None
    }

    save_stats()

    message = build_signal_message(
        trade,
        signal
    )

    chart = generate_chart(
        df,
        (
            f"SIGNAL "
            f"{signal['direction']} "
            f"| {signal['votes']}/9"
        )
    )

    if chart:

        send_telegram_photo(
            chart,
            message
        )

        try:

            os.remove(
                chart
            )

        except Exception:

            pass

    else:

        send_telegram_message(
            message
        )

    logger.info(
        "Signal #%s: %s | %s/9",
        trade_number,
        signal["direction"],
        signal["votes"]
    )

    return trade


# ============================================================
# CALCULATE RESULT
# ============================================================

def calculate_result(
    direction,
    entry_price,
    exit_price
):

    if (
        entry_price is None
        or
        exit_price is None
    ):

        return None

    if direction == "CALL":

        if exit_price > entry_price:

            return "WIN"

        if exit_price < entry_price:

            return "LOSS"

        return "TIE"

    if direction == "PUT":

        if exit_price < entry_price:

            return "WIN"

        if exit_price > entry_price:

            return "LOSS"

        return "TIE"

    return "TIE"


# ============================================================
# RESULT MESSAGE
# ============================================================

def build_result_message(
    trade,
    result
):

    if result == "WIN":

        result_text = (
            "WIN 🟢"
        )

    elif result == "LOSS":

        result_text = (
            "LOSS 🔴"
        )

    else:

        result_text = (
            "TIE ⚪"
        )

    entry_dt = datetime.fromtimestamp(
        trade["entry_timestamp"],
        tz=TURKEY_TZ
    )

    exit_timestamp = (
        trade["entry_timestamp"] +
        TRADE_DURATION
    )

    exit_dt = datetime.fromtimestamp(
        exit_timestamp,
        tz=TURKEY_TZ
    )

    return (

        f"📊 <b>نتيجة الصفقة "
        f"#{trade['number']}</b>\n\n"

        f"💱 <b>الزوج:</b> "
        f"{ASSET_NAME}\n"

        f"🚀 <b>الإشارة:</b> "
        f"{trade['direction']}\n"

        f"🏁 <b>النتيجة:</b> "
        f"<b>{result_text}</b>\n\n"

        f"⏰ <b>الدخول:</b> "
        f"{entry_dt.strftime('%H:%M:%S')}\n"

        f"🏁 <b>الإغلاق:</b> "
        f"{exit_dt.strftime('%H:%M:%S')}\n\n"

        f"💵 <b>سعر الدخول:</b> "
        f"{trade['entry_price']:.5f}\n"

        f"🏁 <b>سعر الإغلاق:</b> "
        f"{trade['exit_price']:.5f}\n\n"

        "📈 <b>الإحصائيات:</b>\n"

        f"🟢 WIN: {total_wins}\n"

        f"🔴 LOSS: {total_losses}\n"

        f"⚪ TIE: {total_ties}\n"

        f"📊 TOTAL: "
        f"{get_total_trades()}\n"

        f"🎯 WIN RATE: "
        f"{get_win_rate():.2f}%"
    )


# ============================================================
# WAIT UNTIL ENTRY
# ============================================================

def wait_until_entry(
    entry_timestamp
):

    while True:

        now = get_server_timestamp()

        remaining = (
            entry_timestamp -
            now
        )

        if remaining <= 0:

            break

        # لا ننتظر أكثر من اللازم
        time.sleep(
            min(
                1.0,
                max(
                    0.1,
                    remaining
                )
            )
        )


# ============================================================
# WAIT UNTIL RESULT
# ============================================================

def wait_until_result(
    entry_timestamp
):

    result_timestamp = (
        entry_timestamp +
        TRADE_DURATION
    )

    while True:

        now = get_server_timestamp()

        remaining = (
            result_timestamp -
            now
        )

        if remaining <= 0:

            break

        time.sleep(
            min(
                1.0,
                max(
                    0.1,
                    remaining
                )
            )
        )

    # وقت إضافي حتى تصل الشمعة
    time.sleep(
        RESULT_EXTRA_WAIT
    )


# ============================================================
# GET REAL ENTRY PRICE
# ============================================================

def get_real_entry_price(
    df,
    entry_timestamp
):

    # الأفضل: Open الشمعة التي بدأ
    # عندها الدخول

    candle = find_candle(
        df,
        entry_timestamp
    )

    if candle is not None:

        return float(
            candle["open"]
        )

    # إذا لم تظهر الشمعة في البيانات
    # نستخدم أول tick بعد وقت الدخول
    try:

        ticks = (
            po_client
            .get_realtime_ticks(
                ASSET,
                limit=100
            )
        )

        valid = [
            tick
            for tick in ticks
            if (
                len(tick) >= 2
                and
                float(tick[0])
                >= entry_timestamp
            )
        ]

        if valid:

            valid.sort(
                key=lambda x: x[0]
            )

            return float(
                valid[0][1]
            )

    except Exception as e:

        logger.warning(
            "Could not get entry tick: %s",
            e
        )

    return None


# ============================================================
# PROCESS RESULT
# ============================================================

def process_trade_result(
    trade
):

    global total_wins
    global total_losses
    global total_ties

    logger.info(
        "Waiting for entry of trade #%s...",
        trade["number"]
    )

    # --------------------------------------------------------
    # ننتظر بداية شمعة الدخول
    # --------------------------------------------------------

    wait_until_entry(
        trade["entry_timestamp"]
    )

    # جلب أحدث شموع
    entry_df = (
        fetch_pocket_option_candles()
    )

    if entry_df is None:

        logger.error(
            "Could not confirm entry candle."
        )

        send_telegram_message(
            f"⚠️ <b>الصفقة #{trade['number']}</b>\n\n"
            "تعذر الحصول على شمعة الدخول "
            "من Pocket Option.\n"
            "لم يتم احتساب النتيجة."
        )

        return

    # السعر الحقيقي/المرجعي للدخول
    entry_price = (
        get_real_entry_price(
            entry_df,
            trade["entry_timestamp"]
        )
    )

    if entry_price is None:

        logger.error(
            "Entry price unavailable."
        )

        send_telegram_message(
            f"⚠️ <b>الصفقة #{trade['number']}</b>\n\n"
            "تعذر تأكيد سعر الدخول."
        )

        return

    trade["entry_price"] = (
        entry_price
    )

    logger.info(
        "Trade #%s entered at %.5f",
        trade["number"],
        entry_price
    )

    # --------------------------------------------------------
    # انتظار انتهاء الدقيقة
    # --------------------------------------------------------

    wait_until_result(
        trade["entry_timestamp"]
    )

    # --------------------------------------------------------
    # جلب الشموع بعد انتهاء الصفقة
    # --------------------------------------------------------

    result_df = (
        fetch_pocket_option_candles()
    )

    if result_df is None:

        logger.error(
            "Could not fetch result candles."
        )

        send_telegram_message(
            f"⚠️ <b>الصفقة #{trade['number']}</b>\n\n"
            "انتهت مدة الصفقة، لكن تعذر "
            "الحصول على شمعة النتيجة.\n"
            "لن يتم اختلاق نتيجة."
        )

        return

    # شمعة النتيجة = شمعة الدخول
    # بعد إغلاقها
    result_timestamp = (
        trade["entry_timestamp"]
    )

    result_candle = find_candle(
        result_df,
        result_timestamp
    )

    if result_candle is None:

        # محاولة أخذ آخر شمعة إذا كانت
        # متوافقة زمنيًا
        candidates = result_df[
            result_df["timestamp"]
            >= result_timestamp
        ]

        if not candidates.empty:

            result_candle = (
                candidates.iloc[0]
            )

    if result_candle is None:

        logger.error(
            "Result candle not found."
        )

        send_telegram_message(
            f"⚠️ <b>الصفقة #{trade['number']}</b>\n\n"
            "تعذر تأكيد شمعة النتيجة.\n"
            "لم يتم احتساب WIN/LOSS."
        )

        return

    exit_price = float(
        result_candle["close"]
    )

    trade["exit_price"] = (
        exit_price
    )

    # --------------------------------------------------------
    # حساب النتيجة
    # --------------------------------------------------------

    result = calculate_result(
        trade["direction"],
        trade["entry_price"],
        trade["exit_price"]
    )

    if result is None:

        return

    trade["result"] = result

    if result == "WIN":

        total_wins += 1

    elif result == "LOSS":

        total_losses += 1

    else:

        total_ties += 1

    save_stats()

    # --------------------------------------------------------
    # رسالة النتيجة
    # --------------------------------------------------------

    message = build_result_message(
        trade,
        result
    )

    # --------------------------------------------------------
    # رسم النتيجة
    # --------------------------------------------------------

    chart = generate_chart(
        result_df,
        (
            f"RESULT "
            f"{result} "
            f"| TRADE #{trade['number']}"
        )
    )

    if chart:

        send_telegram_photo(
            chart,
            message
        )

        try:

            os.remove(
                chart
            )

        except Exception:

            pass

    else:

        send_telegram_message(
            message
        )

    logger.info(
        "Trade #%s result: %s | "
        "Entry %.5f | Exit %.5f",
        trade["number"],
        result,
        trade["entry_price"],
        trade["exit_price"]
    )


# ============================================================
# WAIT FOR NEXT CLOSED CANDLE
# ============================================================

def wait_for_next_candle():

    server_ts = (
        get_server_timestamp()
    )

    current_bucket = (
        server_ts //
        CANDLE_PERIOD
    ) * CANDLE_PERIOD

    next_candle = (
        current_bucket +
        CANDLE_PERIOD
    )

    wait_seconds = (
        next_candle -
        server_ts
    )

    if wait_seconds < 0:

        wait_seconds = 1

    logger.info(
        "Waiting %.1f seconds "
        "for next candle...",
        wait_seconds
    )

    time.sleep(
        wait_seconds + 1
    )


# ============================================================
# STARTUP MESSAGE
# ============================================================

def send_startup_message():

    message = (

        "🤖 <b>Pocket Option OTC Bot</b>\n\n"

        "💱 <b>EUR/USD OTC</b>\n"

        "🕯️ <b>الفريم:</b> 1 دقيقة\n"

        "📡 <b>مصدر البيانات:</b> "
        "Pocket Option WebSocket\n"

        "⏱️ <b>الوقت:</b> "
        "Pocket Option Server\n"

        "⭐ <b>الحد الأدنى:</b> "
        f"{MIN_CONFIRMATIONS}/9\n\n"

        "🚫 الشموع الوهمية: متوقفة\n"

        "🚫 الصفقات العشوائية: متوقفة\n"

        "🛡️ بدون مضاعفات\n"

        "🤖 التداول الآلي: متوقف\n\n"

        f"📈 <b>الصفقات السابقة:</b> "
        f"{get_total_trades()}\n"

        f"🟢 WIN: {total_wins}\n"

        f"🔴 LOSS: {total_losses}\n"

        f"⚪ TIE: {total_ties}\n"

        f"🎯 WIN RATE: "
        f"{get_win_rate():.2f}%"
    )

    send_telegram_message(
        message
    )


# ============================================================
# MAIN
# ============================================================

def main():

    global po_connected
    global last_analyzed_candle
    global last_signal_time

    logger.info(
        "Starting Pocket Option "
        "EUR/USD OTC Signal Bot..."
    )

    load_stats()

    # --------------------------------------------------------
    # التحقق من SSID
    # --------------------------------------------------------

    if not PO_SSID:

        logger.error(
            "PO_SSID is missing."
        )

        send_telegram_message(
            "❌ <b>البوت لم يبدأ</b>\n\n"
            "PO_SSID غير موجود."
        )

        return

    # --------------------------------------------------------
    # التحقق من Telegram
    # --------------------------------------------------------

    if (
        not TELEGRAM_BOT_TOKEN
        or
        not TELEGRAM_CHAT_ID
    ):

        logger.warning(
            "Telegram credentials are incomplete."
        )

    # --------------------------------------------------------
    # الاتصال
    # --------------------------------------------------------

    if not connect_pocket_option():

        logger.error(
            "Could not connect to Pocket Option."
        )

        send_telegram_message(
            "❌ <b>فشل الاتصال بـ Pocket Option</b>\n\n"
            "لن يتم إرسال أي صفقة.\n"
            "الشموع الوهمية متوقفة."
        )

        return

    send_startup_message()

    # --------------------------------------------------------
    # MAIN LOOP
    # --------------------------------------------------------

    while True:

        try:

            # ------------------------------------------------
            # فحص الاتصال
            # ------------------------------------------------

            try:

                if not (
                    po_client.check_connect()
                ):

                    logger.warning(
                        "Pocket Option disconnected."
                    )

                    po_connected = False

            except Exception:

                po_connected = False

            # ------------------------------------------------
            # إعادة الاتصال
            # ------------------------------------------------

            if not po_connected:

                logger.info(
                    "Attempting reconnection..."
                )

                if not connect_pocket_option():

                    time.sleep(10)

                    continue

            # ------------------------------------------------
            # جلب الشموع الحقيقية
            # ------------------------------------------------

            df = (
                fetch_pocket_option_candles()
            )

            if df is None:

                logger.warning(
                    "No valid Pocket Option "
                    "candles."
                )

                time.sleep(5)

                continue

            if len(df) < 50:

                logger.warning(
                    "Not enough candles."
                )

                time.sleep(5)

                continue

            # ------------------------------------------------
            # آخر شمعة مغلقة
            # ------------------------------------------------

            latest = df.iloc[-1]

            candle_timestamp = float(
                latest["timestamp"]
            )

            candle_dt = (
                datetime.fromtimestamp(
                    candle_timestamp,
                    tz=TURKEY_TZ
                )
            )

            # ------------------------------------------------
            # منع تحليل نفس الشمعة
            # ------------------------------------------------

            if (
                last_analyzed_candle
                ==
                candle_timestamp
            ):

                time.sleep(2)

                continue

            # ثبتنا الشمعة
            last_analyzed_candle = (
                candle_timestamp
            )

            logger.info(
                "Analyzing closed candle: %s",
                candle_dt.strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            )

            # ------------------------------------------------
            # الإشارة
            # ------------------------------------------------

            signal = (
                get_strong_signal(
                    df
                )
            )

            if signal is None:

                logger.info(
                    "No strong signal."
                )

                time.sleep(2)

                continue

            # ------------------------------------------------
            # حماية إضافية
            # ------------------------------------------------

            now = time.time()

            if (
                now -
                last_signal_time
                <
                SIGNAL_COOLDOWN
            ):

                time.sleep(2)

                continue

            last_signal_time = now

            # ------------------------------------------------
            # إرسال الصفقة
            # ------------------------------------------------

            trade = send_signal(
                df,
                signal
            )

            if trade is None:

                continue

            # ------------------------------------------------
            # معالجة الصفقة والنتيجة
            # ------------------------------------------------

            process_trade_result(
                trade
            )

            # ------------------------------------------------
            # بعد انتهاء الصفقة ننتظر
            # الشمعة التالية
            # ------------------------------------------------

            time.sleep(2)

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

            po_connected = False

            time.sleep(10)


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    main()
