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

# 1-minute candles
CANDLE_PERIOD = 60

# عدد الشموع المستخدمة للتحليل
HISTORY_CANDLES = 150

# لا ترسل إشارة إلا عند وجود 7 تأكيدات أو أكثر
MIN_CONFIRMATIONS = 7

# أقل عدد شموع مقبول للتحليل
MIN_CANDLES = 60

# المنطقة الزمنية
TURKEY_TZ = pytz.timezone("Europe/Istanbul")

# ملف الإحصائيات
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
            with open(STATS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

            total_wins = int(data.get("wins", 0))
            total_losses = int(data.get("losses", 0))
            total_ties = int(data.get("ties", 0))
            total_signals = int(data.get("signals", 0))

    except Exception as e:
        logger.warning("Could not load statistics: %s", e)


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
        logger.warning("Could not save statistics: %s", e)


def get_win_rate():
    completed = total_wins + total_losses

    if completed == 0:
        return 0.0

    return (total_wins / completed) * 100.0


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
        return None

    if not photo_path or not os.path.exists(photo_path):
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
    مهم جدًا:

    هذه الدالة لا تولّد شموعًا اصطناعية.

    يجب أن تعيد شموع OTC الحقيقية من مصدر بيانات
    Pocket Option الفعلي.

    إذا لم تتوفر البيانات الحقيقية ترجع None.

    لا يوجد fallback عشوائي.
    """

    # --------------------------------------------------------
    # ملاحظة:
    #
    # لا نستخدم:
    #
    # generate_live_synced_candles()
    #
    # لأن تلك الدالة كانت تنتج بيانات مصطنعة.
    #
    # --------------------------------------------------------

    if not PO_SSID:
        logger.error(
            "PO_SSID / PO_UUID غير موجود. "
            "لن يتم استخدام بيانات مصطنعة."
        )
        return None

    # --------------------------------------------------------
    # لا نضع API غير موثوق هنا ونعتبره بيانات Pocket Option.
    #
    # يجب ربط هذه الدالة بمصدر بيانات Pocket Option الحقيقي.
    # --------------------------------------------------------

    logger.error(
        "لم يتم إعداد مصدر بيانات Pocket Option الحقيقي."
    )

    return None


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

        elif c in ("open", "o"):
            rename_map[column] = "open"

        elif c in ("high", "max", "h"):
            rename_map[column] = "high"

        elif c in ("low", "min", "l"):
            rename_map[column] = "low"

        elif c in ("close", "c"):
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

    df = df.dropna(
        subset=[
            "timestamp",
            "open",
            "high",
            "low",
            "close"
        ]
    )

    df = df.sort_values(
        "timestamp"
    )

    df = df.drop_duplicates(
        subset=["timestamp"],
        keep="last"
    )

    return df.reset_index(drop=True)


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

    # الشمعة التي لم تغلق بعد
    current_bucket = (
        now // CANDLE_PERIOD
    ) * CANDLE_PERIOD

    # نستخدم فقط الشموع التي انتهت
    closed = df[
        df["timestamp"] < current_bucket
    ].copy()

    if len(closed) < MIN_CANDLES:
        return None

    return closed.tail(
        HISTORY_CANDLES
    ).reset_index(drop=True)


# ============================================================
# INDICATORS
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


def calculate_indicators(df):

    result = df.copy()

    close = result["close"]

    # EMA
    result["ema_5"] = close.ewm(
        span=5,
        adjust=False
    ).mean()

    result["ema_9"] = close.ewm(
        span=9,
        adjust=False
    ).mean()

    result["ema_21"] = close.ewm(
        span=21,
        adjust=False
    ).mean()

    result["ema_50"] = close.ewm(
        span=50,
        adjust=False
    ).mean()

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
        ema12 - ema26
    )

    result["macd_signal"] = (
        result["macd"]
        .ewm(span=9, adjust=False)
        .mean()
    )

    result["macd_hist"] = (
        result["macd"] -
        result["macd_signal"]
    )

    # Bollinger
    result["bb_middle"] = (
        close.rolling(20).mean()
    )

    result["bb_std"] = (
        close.rolling(20).std()
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
        tr.rolling(14).mean()
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

    # --------------------------------------------------------
    # EMA TREND
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

    # --------------------------------------------------------
    # MACD
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # MOMENTUM
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # CANDLE CONFIRMATION
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # SUPPORT / RESISTANCE
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

    if (
        (price - recent_low) >
        (recent_high - price)
        and
        price > last["ema_21"]
    ):

        votes["Support/Resistance"] = "CALL"

    elif (
        (recent_high - price) >
        (price - recent_low)
        and
        price < last["ema_21"]
    ):

        votes["Support/Resistance"] = "PUT"

    else:

        votes["Support/Resistance"] = "NEUTRAL"

    # --------------------------------------------------------
    # TREND STRENGTH
    # --------------------------------------------------------

    ema_distance = abs(
        last["ema_21"] -
        last["ema_50"]
    )

    atr = last["atr"]

    if pd.notna(atr) and atr > 0:

        strength_ratio = (
            ema_distance / atr
        )

        if (
            strength_ratio > 0.8
            and
            last["ema_21"] >
            last["ema_50"]
        ):

            votes["Trend Strength"] = "CALL"

        elif (
            strength_ratio > 0.8
            and
            last["ema_21"] <
            last["ema_50"]
        ):

            votes["Trend Strength"] = "PUT"

        else:

            votes["Trend Strength"] = "NEUTRAL"

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
        1 for value in votes.values()
        if value == "CALL"
    )

    put_votes = sum(
        1 for value in votes.values()
        if value == "PUT"
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
# SIGNAL
# ============================================================

def send_signal(
    df,
    signal,
    entry_timestamp
):

    global total_signals

    latest = df.iloc[-1]

    entry_price = float(
        latest["close"]
    )

    last_candle_timestamp = float(
        latest["timestamp"]
    )

    # دخول الدقيقة التالية
    entry_time = (
        datetime.fromtimestamp(
            last_candle_timestamp,
            tz=TURKEY_TZ
        )
        +
        timedelta(
            seconds=CANDLE_PERIOD
        )
    )

    expiry_time = (
        entry_time +
        timedelta(
            seconds=CANDLE_PERIOD
        )
    )

    direction = signal["direction"]

    if direction == "CALL":

        direction_text = (
            "🟢 CALL / UP"
        )

    else:

        direction_text = (
            "🔴 PUT / DOWN"
        )

    total_signals += 1
    save_stats()

    message = [
        "🎯 <b>إشارة OTC قوية</b>",
        "",
        f"💱 <b>الزوج:</b> {ASSET_NAME}",
        f"🚀 <b>الاتجاه:</b> {direction_text}",
        "",
        (
            f"⭐ <b>التأكيدات:</b> "
            f"{signal['votes']}/9"
        ),
        "",
        (
            f"⏰ <b>الدخول:</b> "
            f"{entry_time.strftime('%H:%M:%S')}"
        ),
        (
            f"🏁 <b>الانتهاء:</b> "
            f"{expiry_time.strftime('%H:%M:%S')}"
        ),
        "⏱️ <b>المدة:</b> 1 دقيقة",
        "",
        (
            f"💵 <b>سعر الدخول المرجعي:</b> "
            f"{entry_price:.5f}"
        ),
        "",
        "📊 <b>التأكيدات:</b>"
    ]

    for name, vote in (
        signal["strategies"].items()
    ):

        if vote == "CALL":
            icon = "🟢"

        elif vote == "PUT":
            icon = "🔴"

        else:
            icon = "⚪"

        message.append(
            f"{icon} {name}: {vote}"
        )

    message.extend(
        [
            "",
            "🛡️ <b>بدون مضاعفات</b>",
            "🤖 <b>التنفيذ الآلي: متوقف</b>",
            "",
            (
                "⚠️ هذه إشارة تحليلية "
                "وليست ضمانًا للربح."
            )
        ]
    )

    message = "\n".join(message)

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

        except Exception:
            pass

    else:

        send_telegram_message(
            message
        )

    return {
        "direction": direction,
        "entry_price": entry_price,
        "entry_timestamp": entry_timestamp,
        "entry_time": entry_time,
        "expiry_time": expiry_time
    }


# ============================================================
# RESULT MESSAGE
# ============================================================

def send_result(
    trade,
    result_df
):

    global total_wins
    global total_losses
    global total_ties

    if result_df is None:
        return

    result_df = normalize_candles(
        result_df
    )

    if result_df is None or result_df.empty:
        return

    # نبحث عن شمعة الإغلاق المناسبة
    expiry_timestamp = int(
        trade["expiry_time"].timestamp()
    )

    candidates = result_df[
        result_df["timestamp"] >=
        expiry_timestamp
    ]

    if candidates.empty:
        exit_candle = result_df.iloc[-1]

    else:
        exit_candle = candidates.iloc[0]

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
        "📊 <b>نتيجة الإشارة</b>\n\n"
        f"💱 <b>الزوج:</b> {ASSET_NAME}\n"
        f"🚀 <b>الإشارة:</b> "
        f"{trade['direction']}\n"
        f"🏁 <b>النتيجة:</b> "
        f"<b>{result_text}</b>\n\n"
        f"💵 <b>سعر الدخول:</b> "
        f"{trade['entry_price']:.5f}\n"
        f"🏁 <b>سعر الإغلاق:</b> "
        f"{exit_price:.5f}\n"
        f"🕐 <b>وقت الإغلاق:</b> "
        f"{exit_time.strftime('%H:%M:%S')}\n\n"
        f"📈 <b>الإحصائيات:</b>\n"
        f"🟢 WIN: {total_wins}\n"
        f"🔴 LOSS: {total_losses}\n"
        f"⚪ TIE: {total_ties}\n"
        f"📊 TOTAL: {completed}\n"
        f"🎯 WIN RATE: "
        f"{get_win_rate():.2f}%"
    )

    chart = generate_chart(
        result_df,
        f"RESULT: {result}"
    )

    if chart:

        send_telegram_photo(
            chart,
            message
        )

        try:
            os.remove(chart)

        except Exception:
            pass

    else:

        send_telegram_message(
            message
        )


# ============================================================
# DATA VALIDATION
# ============================================================

def validate_data(df):

    if df is None:
        return False

    if len(df) < MIN_CANDLES:
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

    if df["timestamp"].duplicated().any():
        return False

    if not df["timestamp"].is_monotonic_increasing:
        return False

    return True


# ============================================================
# MAIN
# ============================================================

def main():

    logger.info(
        "Starting Pocket Option OTC signal bot..."
    )

    load_stats()

    if not TELEGRAM_BOT_TOKEN:
        logger.error(
            "TELEGRAM_BOT_TOKEN is missing."
        )

    if not TELEGRAM_CHAT_ID:
        logger.error(
            "TELEGRAM_CHAT_ID is missing."
        )

    send_telegram_message(
        "🤖 <b>بوت EUR/USD OTC بدأ</b>\n\n"
        "⏳ في انتظار بيانات OTC الحقيقية...\n"
        "⚠️ لن يتم توليد شموع وهمية."
    )

    last_analyzed_candle = None
    active_trade = None

    while True:

        try:

            raw_df = (
                fetch_pocket_option_candles()
            )

            if raw_df is None:

                logger.warning(
                    "No real Pocket Option candle data."
                )

                time.sleep(10)
                continue

            df = get_closed_candles(
                raw_df
            )

            if not validate_data(df):

                logger.warning(
                    "Invalid or insufficient "
                    "candle data."
                )

                time.sleep(10)
                continue

            latest_candle_timestamp = int(
                df.iloc[-1]["timestamp"]
            )

            # ------------------------------------------------
            # لا نحلل نفس الشمعة مرتين
            # ------------------------------------------------

            if (
                last_analyzed_candle ==
                latest_candle_timestamp
            ):

                time.sleep(3)
                continue

            last_analyzed_candle = (
                latest_candle_timestamp
            )

            # ------------------------------------------------
            # إذا توجد صفقة فعالة لا نرسل صفقة أخرى
            # ------------------------------------------------

            if active_trade is not None:

                logger.info(
                    "Trade still active."
                )

                time.sleep(3)
                continue

            # ------------------------------------------------
            # تحليل
            # ------------------------------------------------

            signal = get_strong_signal(
                df
            )

            if signal is None:

                logger.info(
                    "No strong signal."
                )

                time.sleep(3)
                continue

            # ------------------------------------------------
            # إرسال الإشارة
            # ------------------------------------------------

            active_trade = send_signal(
                df,
                signal,
                latest_candle_timestamp
            )

            if active_trade is None:

                time.sleep(3)
                continue

            # ------------------------------------------------
            # الانتظار حتى انتهاء الدقيقة
            # ------------------------------------------------

            expiry_timestamp = int(
                active_trade[
                    "expiry_time"
                ].timestamp()
            )

            while int(time.time()) < (
                expiry_timestamp + 3
            ):

                time.sleep(1)

            # ------------------------------------------------
            # جلب بيانات النتيجة
            # ------------------------------------------------

            result_raw = (
                fetch_pocket_option_candles()
            )

            if result_raw is not None:

                result_df = get_closed_candles(
                    result_raw
                )

                if result_df is not None:

                    send_result(
                        active_trade,
                        result_df
                    )

            else:

                send_telegram_message(
                    "⚠️ <b>تعذر تأكيد نتيجة الصفقة</b>\n\n"
                    "لم تصل بيانات OTC الحقيقية "
                    "بعد انتهاء الصفقة.\n"
                    "لم يتم احتساب WIN أو LOSS."
                )

            active_trade = None

            time.sleep(3)

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
