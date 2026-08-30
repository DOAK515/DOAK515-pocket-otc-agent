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
# CONFIG
# ============================================================

PO_SSID = os.getenv("PO_SSID", "").strip() or os.getenv("PO_UUID", "").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

ASSET = "EURUSD_otc"
ASSET_NAME = "EUR/USD (OTC)"

CANDLE_PERIOD = 60
HISTORY_CANDLES = 120

# 7 من 9 تأكيدات
MIN_CONFIRMATIONS = 7

TURKEY_TZ = pytz.timezone("Europe/Istanbul")
STATS_FILE = "trading_stats.json"

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("PO_OTC_MATCHED_BOT")


# ============================================================
# STATISTICS
# ============================================================

total_wins = 0
total_losses = 0
total_ties = 0


def load_stats():
    global total_wins, total_losses, total_ties

    try:
        if os.path.exists(STATS_FILE):
            with open(STATS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

            total_wins = int(data.get("wins", 0))
            total_losses = int(data.get("losses", 0))
            total_ties = int(data.get("ties", 0))

    except Exception as e:
        logger.warning("Could not load statistics: %s", e)


def save_stats():
    data = {
        "wins": total_wins,
        "losses": total_losses,
        "ties": total_ties,
        "updated_at": datetime.now(TURKEY_TZ).isoformat()
    }

    try:
        with open(STATS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    except Exception as e:
        logger.warning("Could not save statistics: %s", e)


def get_win_rate():
    total = total_wins + total_losses

    if total == 0:
        return 0.0

    return (total_wins / total) * 100.0


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram_message(message):

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram credentials are missing.")
        return None

    url = (
        f"https://api.telegram.org/"
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

        return response.json()

    except Exception as e:
        logger.error("Telegram message error: %s", e)

        return None


def send_telegram_photo(photo_path, caption):

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram credentials are missing.")
        return None

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    )

    try:

        with open(photo_path, "rb") as photo_file:

            files = {
                "photo": photo_file
            }

            data = {
                "chat_id": TELEGRAM_CHAT_ID,
                "caption": caption,
                "parse_mode": "HTML"
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

        logger.error("Telegram photo error: %s", e)

        return None


# ============================================================
# POCKET OPTION DATA
# ============================================================

def fetch_pocket_option_candles():

    """
    جلب شموع Pocket Option فقط.

    مهم:
    لا توجد أي شموع عشوائية كبديل.
    إذا فشل مصدر البيانات يتم إرجاع None.
    """

    if not PO_SSID:

        logger.error(
            "PO_SSID is missing. "
            "The bot will NOT generate fake candles."
        )

        return None

    url = "https://pocketoption.com/api/v1/candles"

    headers = {

        "User-Agent":
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36",

        "Referer":
            "https://pocketoption.com/",

        "Accept":
            "application/json, "
            "text/javascript, */*; q=0.01",

        "X-Requested-With":
            "XMLHttpRequest"
    }

    headers["Cookie"] = f"PHPSESSID={PO_SSID}"

    params = {

        "asset": ASSET,

        "period": CANDLE_PERIOD,

        "count": HISTORY_CANDLES
    }

    try:

        response = requests.get(
            url,
            headers=headers,
            params=params,
            timeout=15
        )

        if response.status_code != 200:

            logger.warning(
                "Pocket Option HTTP status: %s",
                response.status_code
            )

            return None

        try:
            data = response.json()

        except Exception:

            logger.warning(
                "Pocket Option returned non-JSON data."
            )

            return None

        if not isinstance(data, list):

            logger.warning(
                "Unexpected Pocket Option response."
            )

            return None

        if len(data) < 40:

            logger.warning(
                "Not enough Pocket Option candles: %s",
                len(data)
            )

            return None

        df = pd.DataFrame(data)

        df = normalize_candles(df)

        if df is None:

            logger.warning(
                "Could not normalize Pocket Option candles."
            )

            return None

        if len(df) < 40:

            return None

        return df

    except requests.RequestException as e:

        logger.warning(
            "Pocket Option connection error: %s",
            e
        )

        return None

    except Exception as e:

        logger.warning(
            "Pocket Option data error: %s",
            e
        )

        return None


# ============================================================
# CANDLE VALIDATION
# ============================================================

def validate_candles(df):

    if df is None or df.empty:
        return False

    required = [
        "timestamp",
        "open",
        "high",
        "low",
        "close"
    ]

    for col in required:

        if col not in df.columns:
            return False

        if not pd.api.types.is_numeric_dtype(df[col]):
            return False

    if len(df) < 40:
        return False

    # منع القيم غير المنطقية
    if (df["high"] < df["low"]).any():
        return False

    if (df["high"] < df["open"]).any():
        return False

    if (df["high"] < df["close"]).any():
        return False

    if (df["low"] > df["open"]).any():
        return False

    if (df["low"] > df["close"]).any():
        return False

    return True


# ============================================================
# NORMALIZATION
# ============================================================

def normalize_candles(df):

    if df is None or df.empty:
        return None

    df = df.copy()

    rename_map = {}

    for column in df.columns:

        c = str(column).lower().strip()

        if c in (
            "time",
            "timestamp",
            "created_at",
            "at",
            "time_"
        ):
            rename_map[column] = "timestamp"

        elif c in (
            "open",
            "o"
        ):
            rename_map[column] = "open"

        elif c in (
            "high",
            "max",
            "h"
        ):
            rename_map[column] = "high"

        elif c in (
            "low",
            "min",
            "l"
        ):
            rename_map[column] = "low"

        elif c in (
            "close",
            "c"
        ):
            rename_map[column] = "close"

    df = df.rename(columns=rename_map)

    required = [
        "timestamp",
        "open",
        "high",
        "low",
        "close"
    ]

    if any(c not in df.columns for c in required):

        logger.warning(
            "Missing candle columns. Received: %s",
            list(df.columns)
        )

        return None

    for col in [
        "open",
        "high",
        "low",
        "close"
    ]:

        df[col] = pd.to_numeric(
            df[col],
            errors="coerce"
        )

    ts = pd.to_numeric(
        df["timestamp"],
        errors="coerce"
    )

    if ts.dropna().empty:
        return None

    median_ts = ts.dropna().median()

    if median_ts > 10_000_000_000:
        ts = ts / 1000.0

    df["timestamp"] = ts

    df = (
        df
        .dropna(
            subset=[
                "timestamp",
                "open",
                "high",
                "low",
                "close"
            ]
        )
        .sort_values("timestamp")
        .drop_duplicates(
            subset=["timestamp"],
            keep="last"
        )
        .reset_index(drop=True)
    )

    if not validate_candles(df):
        return None

    return df


# ============================================================
# REMOVE CURRENT UNFINISHED CANDLE
# ============================================================

def get_closed_candles(df):

    """
    لا نحلل الشمعة الحالية التي ما زالت تتكون.
    """

    if df is None or len(df) < 10:
        return None

    df = df.copy()

    now = int(time.time())

    current_bucket = (
        now // CANDLE_PERIOD
    ) * CANDLE_PERIOD

    # نحتفظ فقط بالشموع التي انتهت
    closed = df[
        df["timestamp"] < current_bucket
    ].copy()

    if len(closed) < 40:
        return None

    return (
        closed
        .sort_values("timestamp")
        .drop_duplicates(
            subset=["timestamp"],
            keep="last"
        )
        .reset_index(drop=True)
    )


# ============================================================
# RSI
# ============================================================

def calculate_rsi(series, period=14):

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

    return (
        100 -
        (100 / (1 + rs))
    ).fillna(50)


# ============================================================
# INDICATORS
# ============================================================

def calculate_indicators(df):

    result = df.copy()

    close = result["close"]

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
    ).replace(0, np.nan)

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
    ).max(axis=1)

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

    return (
        result
        .dropna()
        .reset_index(drop=True)
    )


# ============================================================
# STRATEGY
# ============================================================

def strategy_votes(df):

    df = calculate_indicators(df)

    if df is None or len(df) < 50:
        return None

    last = df.iloc[-1]

    prev = df.iloc[-2]

    votes = {}

    # EMA

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

    # RSI

    if (
        50 < last["rsi"] < 68
        and last["rsi"] > prev["rsi"]
    ):

        votes["RSI"] = "CALL"

    elif (
        32 < last["rsi"] < 50
        and last["rsi"] < prev["rsi"]
    ):

        votes["RSI"] = "PUT"

    else:

        votes["RSI"] = "NEUTRAL"

    # MACD

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

    # Bollinger

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

    # Stochastic

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

    # Momentum

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

    # Candle

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

        votes["Candle Confirmation"] = "CALL"

    elif (
        last["close"] <
        last["open"]
        and
        body_ratio >= 0.55
    ):

        votes["Candle Confirmation"] = "PUT"

    else:

        votes["Candle Confirmation"] = "NEUTRAL"

    # Support / Resistance

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

        votes["Support/Resistance"] = "CALL"

    elif (
        (rh - p) >
        (p - rl)
        and
        p <
        last["ema_21"]
    ):

        votes["Support/Resistance"] = "PUT"

    else:

        votes["Support/Resistance"] = "NEUTRAL"

    # Trend strength

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

def generate_chart(df, title):

    try:

        chart_df = df.tail(50).copy()

        fig, ax = plt.subplots(
            figsize=(11, 5.5),
            dpi=150
        )

        fig.patch.set_facecolor("#121212")

        ax.set_facecolor("#1e1e1e")

        times = [
            datetime.fromtimestamp(
                float(ts),
                tz=TURKEY_TZ
            )
            for ts in chart_df["timestamp"]
        ]

        x_values = mdates.date2num(times)

        if len(x_values) > 1:

            candle_width = (
                np.median(
                    np.diff(x_values)
                ) * 0.82
            )

        else:

            candle_width = 0.0005

        for i, (_, row) in enumerate(
            chart_df.iterrows()
        ):

            x = x_values[i]

            o = float(row["open"])
            h = float(row["high"])
            l = float(row["low"])
            c = float(row["close"])

            color = (
                "#00df89"
                if c >= o
                else "#ff3344"
            )

            ax.plot(
                [x, x],
                [l, h],
                color=color,
                linewidth=1.1
            )

            rect = Rectangle(
                (
                    x - candle_width / 2,
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

            ax.add_patch(rect)

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
            f"eurusd_otc_"
            f"{int(time.time())}.png"
        )

        plt.savefig(
            file_path,
            bbox_inches="tight",
            facecolor=fig.get_facecolor(),
            edgecolor="none",
            dpi=150
        )

        plt.close(fig)

        return file_path

    except Exception as e:

        logger.error(
            "Chart error: %s",
            e
        )

        return None


# ============================================================
# RESULT
# ============================================================

def calculate_result(
    direction,
    entry_price,
    exit_price
):

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
# SEND SIGNAL
# ============================================================

def send_signal(df, signal):

    latest = df.iloc[-1]

    entry_price = float(
        latest["close"]
    )

    timestamp = float(
        latest["timestamp"]
    )

    candle_time = datetime.fromtimestamp(
        timestamp,
        tz=TURKEY_TZ
    )

    # الدخول في بداية الشمعة التالية
    entry_time = (
        candle_time +
        timedelta(seconds=CANDLE_PERIOD)
    )

    direction = signal["direction"]

    direction_text = (
        "🟢 CALL / UP"
        if direction == "CALL"
        else
        "🔴 PUT / DOWN"
    )

    msg = [

        "🎯 <b>إشارة قوية - EUR/USD OTC</b>",
        "",

        f"💱 <b>الزوج:</b> {ASSET_NAME}",

        f"🚀 <b>الاتجاه:</b> {direction_text}",

        f"⭐ <b>قوة التوافق:</b> "
        f"{signal['votes']}/9",

        "",

        f"⏰ <b>الدخول:</b> "
        f"{entry_time.strftime('%H:%M:%S')}",

        "⏱️ <b>المدة:</b> 1 دقيقة",

        f"💵 <b>السعر المرجعي:</b> "
        f"{entry_price:.5f}",

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

        msg.append(
            f"{icon} {name}: {vote}"
        )

    msg.extend(
        [
            "",
            "🛡️ <b>بدون مضاعفات</b>",
            "🤖 <b>التداول الآلي: متوقف</b>"
        ]
    )

    message = "\n".join(msg)

    chart = generate_chart(
        df,
        f"{direction} | "
        f"{signal['votes']}/9"
    )

    if chart:

        send_telegram_photo(
            chart,
            message
        )

        try:
            os.remove(chart)
        except:
            pass

    else:

        send_telegram_message(
            message
        )

    return {

        "direction": direction,

        "entry_price": entry_price,

        "signal_timestamp": timestamp,

        "entry_time": entry_time.timestamp(),

        "asset": ASSET
    }


# ============================================================
# SEND RESULT
# ============================================================

def send_result(
    trade,
    result_df
):

    global total_wins
    global total_losses
    global total_ties

    closed_df = get_closed_candles(
        result_df
    )

    if closed_df is None:
        return

    exit_candle = closed_df.iloc[-1]

    exit_price = float(
        exit_candle["close"]
    )

    exit_time = datetime.fromtimestamp(
        float(exit_candle["timestamp"]),
        tz=TURKEY_TZ
    )

    result = calculate_result(
        trade["direction"],
        trade["entry_price"],
        exit_price
    )

    if result == "WIN":

        total_wins += 1

        rt = "WIN 🟢"

    elif result == "LOSS":

        total_losses += 1

        rt = "LOSS 🔴"

    else:

        total_ties += 1

        rt = "TIE ⚪"

    save_stats()

    total = (
        total_wins +
        total_losses +
        total_ties
    )

    message = (

        "📊 <b>نتيجة الإشارة</b>\n\n"

        f"💱 <b>الزوج:</b> "
        f"{ASSET_NAME}\n"

        f"🚀 <b>الإشارة:</b> "
        f"{trade['direction']}\n"

        f"🏁 <b>النتيجة:</b> "
        f"<b>{rt}</b>\n\n"

        f"💵 <b>السعر المرجعي:</b> "
        f"{trade['entry_price']:.5f}\n"

        f"🏁 <b>سعر الإغلاق:</b> "
        f"{exit_price:.5f}\n"

        f"🕐 <b>وقت الإغلاق:</b> "
        f"{exit_time.strftime('%H:%M:%S')}\n\n"

        f"📈 <b>الإحصائيات:</b> "
        f"WIN: {total_wins} | "
        f"LOSS: {total_losses} | "
        f"TOTAL: {total} | "
        f"🎯 {get_win_rate():.2f}%"
    )

    chart = generate_chart(
        closed_df,
        f"RESULT: {result}"
    )

    if chart:

        send_telegram_photo(
            chart,
            message
        )

        try:
            os.remove(chart)
        except:
            pass

    else:

        send_telegram_message(
            message
        )


# ============================================================
# WAIT FOR NEW CLOSED CANDLE
# ============================================================

def wait_until_next_candle():

    now = time.time()

    next_bucket = (
        int(now / CANDLE_PERIOD) + 1
    ) * CANDLE_PERIOD

    wait_seconds = (
        next_bucket - now + 1
    )

    logger.info(
        "Waiting %.1f seconds for next candle...",
        wait_seconds
    )

    time.sleep(
        max(
            1,
            wait_seconds
        )
    )


# ============================================================
# MAIN
# ============================================================

def main():

    logger.info(
        "Starting Pocket Option "
        "EUR/USD OTC bot..."
    )

    load_stats()

    if not PO_SSID:

        logger.error(
            "PO_SSID is not configured."
        )

        send_telegram_message(
            "⚠️ <b>البوت لم يبدأ</b>\n\n"
            "PO_SSID غير موجود.\n"
            "لن يتم استخدام شموع وهمية."
        )

        return

    if (
        not TELEGRAM_BOT_TOKEN
        or
        not TELEGRAM_CHAT_ID
    ):

        logger.warning(
            "Telegram configuration is incomplete."
        )

    send_telegram_message(
        "🤖 <b>بوت EUR/USD OTC بدأ العمل</b>\n\n"
        "📡 مصدر البيانات: Pocket Option\n"
        "⏱️ الفريم: 1 دقيقة\n"
        "🛡️ الشموع الوهمية: متوقفة\n"
        "📊 تحليل الشموع المغلقة فقط"
    )

    last_signal_timestamp = None

    while True:

        try:

            df = fetch_pocket_option_candles()

            if df is None:

                logger.warning(
                    "No valid Pocket Option data. "
                    "Waiting..."
                )

                time.sleep(10)

                continue

            closed_df = get_closed_candles(
                df
            )

            if (
                closed_df is None
                or
                len(closed_df) < 50
            ):

                logger.warning(
                    "Not enough closed candles."
                )

                time.sleep(10)

                continue

            latest_timestamp = float(
                closed_df.iloc[-1]["timestamp"]
            )

            # نفس الشمعة تم تحليلها سابقًا
            if (
                last_signal_timestamp
                ==
                latest_timestamp
            ):

                time.sleep(5)

                continue

            # نثبت الشمعة حتى لا تتكرر الإشارة
            last_signal_timestamp = (
                latest_timestamp
            )

            logger.info(
                "Analyzing closed candle: %s",
                datetime.fromtimestamp(
                    latest_timestamp,
                    tz=TURKEY_TZ
                ).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            )

            signal = get_strong_signal(
                closed_df
            )

            if signal:

                logger.info(
                    "SIGNAL: %s | %s/9",
                    signal["direction"],
                    signal["votes"]
                )

                trade = send_signal(
                    closed_df,
                    signal
                )

                if trade:

                    # انتظار مدة الصفقة
                    time.sleep(
                        CANDLE_PERIOD + 5
                    )

                    result_df = (
                        fetch_pocket_option_candles()
                    )

                    if result_df is not None:

                        send_result(
                            trade,
                            result_df
                        )

            else:

                logger.info(
                    "No strong signal on this candle."
                )

            time.sleep(5)

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

            time.sleep(10)


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()
