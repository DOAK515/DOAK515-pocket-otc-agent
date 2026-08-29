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

from pocketoptionapi import PocketOption


# ============================================================
# CONFIG
# ============================================================

PO_SSID = os.getenv("PO_SSID", "").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

ASSET = "EURUSD_otc"
ASSET_NAME = "EUR/USD (OTC)"

CANDLE_PERIOD = 60
HISTORY_CANDLES = 120

# يجب أن تتفق 7 استراتيجيات على الأقل
MIN_CONFIRMATIONS = 7

# مدة الإشارة
EXPIRATION_SECONDS = 60

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

logger = logging.getLogger("EURUSD_OTC_SIGNAL_BOT")


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

            logger.info(
                "Loaded statistics: WIN=%s LOSS=%s TIE=%s",
                total_wins,
                total_losses,
                total_ties
            )

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
        logger.warning("Telegram credentials are not configured.")
        return None

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
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
        logger.error(
            "Telegram message error: %s",
            e
        )

        return None


def send_telegram_photo(photo_path, caption):

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return None

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendPhoto"
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

        logger.error(
            "Telegram photo error: %s",
            e
        )

        return None


# ============================================================
# POCKET OPTION CONNECTION
# ============================================================

def connect_pocket_option():

    if not PO_SSID:
        raise RuntimeError(
            "PO_SSID غير موجود في GitHub Secrets."
        )

    logger.info(
        "Connecting to Pocket Option..."
    )

    api = PocketOption(PO_SSID)

    ok, error = api.connect()

    if not ok:
        raise RuntimeError(
            f"Pocket Option connection failed: {error}"
        )

    deadline = time.time() + 30

    while time.time() < deadline:

        try:

            if (
                api.check_connect()
                and api.is_time_synced()
            ):

                logger.info(
                    "Pocket Option connected and synchronized."
                )

                break

        except Exception:
            pass

        time.sleep(0.5)

    else:

        raise RuntimeError(
            "Pocket Option time synchronization failed."
        )

    api.subscribe(
        ASSET,
        period=CANDLE_PERIOD
    )

    time.sleep(2)

    return api


# ============================================================
# CANDLE NORMALIZATION
# ============================================================

def normalize_candles(raw_candles):

    if raw_candles is None:
        return None

    try:

        if isinstance(raw_candles, pd.DataFrame):

            df = raw_candles.copy()

        else:

            df = pd.DataFrame(raw_candles)

    except Exception:

        return None

    if df.empty:
        return None

    rename_map = {}

    for column in df.columns:

        c = str(column).lower().strip()

        if c in (
            "time",
            "timestamp",
            "created_at",
            "at"
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

        logger.error(
            "Invalid candle columns: %s",
            list(df.columns)
        )

        return None

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

    df = df.sort_values("timestamp")

    df = df.drop_duplicates(
        subset=["timestamp"],
        keep="last"
    )

    return df.reset_index(drop=True)


# ============================================================
# GET COMPLETED CANDLES
# ============================================================

def get_completed_candles(
    api,
    count=HISTORY_CANDLES
):

    raw = api.get_historical_candles(
        ASSET,
        period=CANDLE_PERIOD,
        offset=45000,
        count_request=1
    )

    df = normalize_candles(raw)

    if df is None or len(df) < 40:
        return None

    try:
        server_ts = float(
            api.get_server_timestamp()
        )

    except Exception:
        server_ts = time.time()

    current_bucket = (
        int(server_ts) // CANDLE_PERIOD
    ) * CANDLE_PERIOD

    df = df[
        df["timestamp"] < current_bucket
    ].copy()

    if len(df) < 40:
        return None

    return df.tail(count).reset_index(drop=True)


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

    rs = avg_gain / avg_loss.replace(
        0,
        np.nan
    )

    rsi = 100 - (
        100 / (1 + rs)
    )

    return rsi.fillna(50)


def calculate_indicators(df):

    result = df.copy()

    close = result["close"]

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

    result["macd"] = ema12 - ema26

    result["macd_signal"] = result[
        "macd"
    ].ewm(
        span=9,
        adjust=False
    ).mean()

    result["macd_hist"] = (
        result["macd"]
        - result["macd_signal"]
    )

    result["bb_middle"] = close.rolling(
        20
    ).mean()

    result["bb_std"] = close.rolling(
        20
    ).std()

    result["bb_upper"] = (
        result["bb_middle"]
        + 2 * result["bb_std"]
    )

    result["bb_lower"] = (
        result["bb_middle"]
        - 2 * result["bb_std"]
    )

    lowest = result["low"].rolling(
        14
    ).min()

    highest = result["high"].rolling(
        14
    ).max()

    denominator = (
        highest - lowest
    ).replace(0, np.nan)

    result["stoch_k"] = (
        100
        * (
            close - lowest
        )
        / denominator
    ).fillna(50)

    result["stoch_d"] = result[
        "stoch_k"
    ].rolling(3).mean().fillna(50)

    high = result["high"]
    low = result["low"]

    tr1 = high - low
    tr2 = (
        high
        - close.shift(1)
    ).abs()

    tr3 = (
        low
        - close.shift(1)
    ).abs()

    tr = pd.concat(
        [tr1, tr2, tr3],
        axis=1
    ).max(axis=1)

    result["atr"] = tr.rolling(
        14
    ).mean()

    result["momentum"] = (
        close
        - close.shift(4)
    )

    result["volume_proxy"] = (
        result["close"]
        - result["open"]
    ).abs()

    return result.dropna().reset_index(
        drop=True
    )


# ============================================================
# STRATEGY VOTES
# ============================================================

def strategy_votes(df):

    df = calculate_indicators(df)

    if df is None or len(df) < 60:
        return None

    last = df.iloc[-1]
    prev = df.iloc[-2]

    votes = {}

    # --------------------------------------------------------
    # 1. EMA TREND
    # --------------------------------------------------------

    if (
        last["ema_5"] > last["ema_9"]
        and last["ema_9"] > last["ema_21"]
        and last["ema_21"] > last["ema_50"]
    ):

        votes["EMA Trend"] = "CALL"

    elif (
        last["ema_5"] < last["ema_9"]
        and last["ema_9"] < last["ema_21"]
        and last["ema_21"] < last["ema_50"]
    ):

        votes["EMA Trend"] = "PUT"

    else:

        votes["EMA Trend"] = "NEUTRAL"

    # --------------------------------------------------------
    # 2. RSI
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # 3. MACD
    # --------------------------------------------------------

    if (
        last["macd"] > last["macd_signal"]
        and last["macd_hist"] > 0
    ):

        votes["MACD"] = "CALL"

    elif (
        last["macd"] < last["macd_signal"]
        and last["macd_hist"] < 0
    ):

        votes["MACD"] = "PUT"

    else:

        votes["MACD"] = "NEUTRAL"

    # --------------------------------------------------------
    # 4. BOLLINGER
    # --------------------------------------------------------

    if (
        last["close"] > last["bb_middle"]
        and last["close"] < last["bb_upper"]
    ):

        votes["Bollinger"] = "CALL"

    elif (
        last["close"] < last["bb_middle"]
        and last["close"] > last["bb_lower"]
    ):

        votes["Bollinger"] = "PUT"

    else:

        votes["Bollinger"] = "NEUTRAL"

    # --------------------------------------------------------
    # 5. STOCHASTIC
    # --------------------------------------------------------

    if (
        last["stoch_k"] > last["stoch_d"]
        and last["stoch_k"] < 80
    ):

        votes["Stochastic"] = "CALL"

    elif (
        last["stoch_k"] < last["stoch_d"]
        and last["stoch_k"] > 20
    ):

        votes["Stochastic"] = "PUT"

    else:

        votes["Stochastic"] = "NEUTRAL"

    # --------------------------------------------------------
    # 6. MOMENTUM
    # --------------------------------------------------------

    if (
        last["momentum"] > 0
        and last["close"] > last["open"]
    ):

        votes["Momentum"] = "CALL"

    elif (
        last["momentum"] < 0
        and last["close"] < last["open"]
    ):

        votes["Momentum"] = "PUT"

    else:

        votes["Momentum"] = "NEUTRAL"

    # --------------------------------------------------------
    # 7. CANDLE CONFIRMATION
    # --------------------------------------------------------

    candle_body = abs(
        last["close"] - last["open"]
    )

    candle_range = (
        last["high"] - last["low"]
    )

    if candle_range > 0:

        body_ratio = (
            candle_body
            / candle_range
        )

    else:

        body_ratio = 0

    if (
        last["close"] > last["open"]
        and body_ratio >= 0.55
    ):

        votes["Candle Confirmation"] = "CALL"

    elif (
        last["close"] < last["open"]
        and body_ratio >= 0.55
    ):

        votes["Candle Confirmation"] = "PUT"

    else:

        votes["Candle Confirmation"] = "NEUTRAL"

    # --------------------------------------------------------
    # 8. SUPPORT / RESISTANCE
    # --------------------------------------------------------

    recent_high = df["high"].tail(20).max()
    recent_low = df["low"].tail(20).min()

    price = last["close"]

    distance_from_low = price - recent_low
    distance_from_high = recent_high - price

    if (
        distance_from_low > distance_from_high
        and price > last["ema_21"]
    ):

        votes["Support/Resistance"] = "CALL"

    elif (
        distance_from_high > distance_from_low
        and price < last["ema_21"]
    ):

        votes["Support/Resistance"] = "PUT"

    else:

        votes["Support/Resistance"] = "NEUTRAL"

    # --------------------------------------------------------
    # 9. ADX / TREND STRENGTH PROXY
    # --------------------------------------------------------

    plus_dm = df["high"].diff()
    minus_dm = -df["low"].diff()

    plus_dm = plus_dm.where(
        (plus_dm > minus_dm)
        & (plus_dm > 0),
        0
    )

    minus_dm = minus_dm.where(
        (minus_dm > plus_dm)
        & (minus_dm > 0),
        0
    )

    atr = df["atr"].replace(
        0,
        np.nan
    )

    plus_di = (
        100
        * plus_dm.rolling(14).mean()
        / atr
    )

    minus_di = (
        100
        * minus_dm.rolling(14).mean()
        / atr
    )

    dx = (
        100
        * (
            (plus_di - minus_di).abs()
            / (plus_di + minus_di).replace(
                0,
                np.nan
            )
        )
    )

    adx = dx.rolling(14).mean().iloc[-1]

    if pd.isna(adx):

        votes["Trend Strength"] = "NEUTRAL"

    elif (
        adx >= 20
        and plus_di.iloc[-1]
        > minus_di.iloc[-1]
    ):

        votes["Trend Strength"] = "CALL"

    elif (
        adx >= 20
        and minus_di.iloc[-1]
        > plus_di.iloc[-1]
    ):

        votes["Trend Strength"] = "PUT"

    else:

        votes["Trend Strength"] = "NEUTRAL"

    return votes


# ============================================================
# SIGNAL DECISION
# ============================================================

def get_strong_signal(df):

    votes = strategy_votes(df)

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

    total_strategies = len(votes)

    logger.info(
        "Strategy votes | CALL=%s PUT=%s TOTAL=%s",
        call_votes,
        put_votes,
        total_strategies
    )

    if (
        call_votes >= MIN_CONFIRMATIONS
        and call_votes > put_votes
    ):

        return {
            "direction": "CALL",
            "votes": call_votes,
            "opposite": put_votes,
            "strategies": votes
        }

    if (
        put_votes >= MIN_CONFIRMATIONS
        and put_votes > call_votes
    ):

        return {
            "direction": "PUT",
            "votes": put_votes,
            "opposite": call_votes,
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
                )
                * 0.82
            )

        else:

            candle_width = 0.0005

        for i, (_, row) in enumerate(
            chart_df.iterrows()
        ):

            x = x_values[i]

            open_p = float(row["open"])
            high_p = float(row["high"])
            low_p = float(row["low"])
            close_p = float(row["close"])

            if close_p >= open_p:

                color = "#00df89"

            else:

                color = "#ff3344"

            ax.plot(
                [x, x],
                [low_p, high_p],
                color=color,
                linewidth=1.1
            )

            body_bottom = min(
                open_p,
                close_p
            )

            body_height = abs(
                close_p - open_p
            )

            if body_height <= 0:

                body_height = max(
                    abs(high_p - low_p)
                    * 0.01,
                    1e-8
                )

            rect = Rectangle(
                (
                    x - candle_width / 2,
                    body_bottom
                ),
                candle_width,
                body_height,
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
# WAIT FOR NEW CLOSED CANDLE
# ============================================================

def wait_for_next_closed_candle(
    api,
    previous_timestamp,
    timeout=100
):

    deadline = time.time() + timeout

    while time.time() < deadline:

        try:

            df = get_completed_candles(
                api
            )

            if (
                df is not None
                and not df.empty
            ):

                latest_timestamp = float(
                    df.iloc[-1]["timestamp"]
                )

                if latest_timestamp > previous_timestamp:

                    return df

        except Exception as e:

            logger.warning(
                "Waiting candle error: %s",
                e
            )

        time.sleep(2)

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

def send_signal(
    df,
    signal
):

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

    entry_time = candle_time + timedelta(
        seconds=CANDLE_PERIOD
    )

    direction = signal["direction"]

    if direction == "CALL":

        direction_text = "🟢 CALL / UP"

    else:

        direction_text = "🔴 PUT / DOWN"

    strength = signal["votes"]

    message_lines = [
        "🎯 <b>إشارة قوية - EUR/USD OTC</b>",
        "",
        f"💱 <b>الزوج:</b> {ASSET_NAME}",
        f"🚀 <b>الاتجاه:</b> {direction_text}",
        f"⭐ <b>قوة التوافق:</b> {strength}/9",
        "",
        f"⏰ <b>الدخول:</b> {entry_time.strftime('%H:%M:%S')}",
        "⏱️ <b>المدة:</b> 1 دقيقة",
        f"💵 <b>السعر المرجعي:</b> {entry_price:.6f}",
        "",
        "📊 <b>الاستراتيجيات:</b>"
    ]

    for name, vote in signal["strategies"].items():

        if vote == "CALL":
            icon = "🟢"

        elif vote == "PUT":
            icon = "🔴"

        else:
            icon = "⚪"

        message_lines.append(
            f"{icon} {name}: {vote}"
        )

    message_lines.extend([
        "",
        "🛡️ <b>بدون مضاعفات</b>",
        "🤖 <b>التداول الآلي: متوقف</b>",
        "📢 <b>هذه توصية فقط - لا يتم فتح أي صفقة تلقائيًا.</b>"
    ])

    message = "\n".join(
        message_lines
    )

    chart = generate_chart(
        df,
        f"{direction} | {strength}/9"
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
        "signal_timestamp": timestamp,
        "entry_time": entry_time
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

    exit_candle = result_df.iloc[-1]

    exit_price = float(
        exit_candle["close"]
    )

    exit_timestamp = float(
        exit_candle["timestamp"]
    )

    exit_time = datetime.fromtimestamp(
        exit_timestamp,
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

    total = (
        total_wins
        + total_losses
        + total_ties
    )

    message = (
        "📊 <b>نتيجة الإشارة</b>\n\n"
        f"💱 <b>الزوج:</b> {ASSET_NAME}\n"
        f"🚀 <b>الإشارة:</b> "
        f"{trade['direction']}\n"
        f"🏁 <b>النتيجة:</b> "
        f"<b>{result_text}</b>\n\n"
        f"💵 <b>السعر المرجعي:</b> "
        f"{trade['entry_price']:.6f}\n"
        f"🏁 <b>سعر الإغلاق:</b> "
        f"{exit_price:.6f}\n"
        f"🕐 <b>وقت الإغلاق:</b> "
        f"{exit_time.strftime('%H:%M:%S')}\n\n"
        "📈 <b>الإحصائيات</b>\n"
        f"✅ WIN: {total_wins}\n"
        f"❌ LOSS: {total_losses}\n"
        f"⚪ TIE: {total_ties}\n"
        f"📊 TOTAL: {total}\n"
        f"🎯 <b>نسبة النجاح:</b> "
        f"{get_win_rate():.2f}%\n\n"
        "🛡️ بدون مضاعفات\n"
        "🤖 التداول الآلي: متوقف"
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
# MAIN CYCLE
# ============================================================

def run_bot_cycle(api):

    logger.info(
        "Analyzing %s...",
        ASSET_NAME
    )

    df = get_completed_candles(
        api
    )

    if df is None:

        logger.warning(
            "Not enough completed candles."
        )

        return None

    signal = get_strong_signal(
        df
    )

    if signal is None:

        logger.info(
            "No strong signal. Nothing sent to Telegram."
        )

        return None

    logger.info(
        "STRONG SIGNAL: %s | %s/9",
        signal["direction"],
        signal["votes"]
    )

    trade = send_signal(
        df,
        signal
    )

    if trade is None:
        return None

    # انتظار الشمعة التالية المغلقة
    result_df = wait_for_next_closed_candle(
        api,
        trade["signal_timestamp"],
        timeout=100
    )

    if result_df is None:

        logger.warning(
            "Could not obtain result candle."
        )

        send_telegram_message(
            "⚠️ لم أستطع الحصول على شمعة النتيجة، "
            "لذلك لم يتم احتساب WIN/LOSS."
        )

        return trade

    send_result(
        trade,
        result_df
    )

    return trade


# ============================================================
# MAIN
# ============================================================

def main():

    logger.info(
        "Starting EUR/USD OTC signal bot..."
    )

    logger.info(
        "Minimum confirmations: %s/9",
        MIN_CONFIRMATIONS
    )

    logger.info(
        "AUTOMATIC TRADING: DISABLED"
    )

    logger.info(
        "MARTINGALE: DISABLED"
    )

    load_stats()

    send_telegram_message(
        "🤖 <b>بوت EUR/USD OTC بدأ العمل</b>\n\n"
        "📊 نظام توافق 9 استراتيجيات\n"
        f"⭐ الحد الأدنى للإشارة: "
        f"{MIN_CONFIRMATIONS}/9\n"
        "⏱️ الإطار: 1 دقيقة\n"
        "🛡️ بدون مضاعفات\n"
        "🚫 لا يوجد تداول تلقائي\n"
        "📢 توصيات Telegram فقط"
    )

    api = None

    last_signal_timestamp = None

    while True:

        try:

            if (
                api is None
                or not api.check_connect()
            ):

                api = connect_pocket_option()

            df = get_completed_candles(
                api
            )

            if df is None:

                time.sleep(10)
                continue

            latest_timestamp = float(
                df.iloc[-1]["timestamp"]
            )

            # منع تحليل نفس الشمعة أكثر من مرة
            if (
                last_signal_timestamp
                == latest_timestamp
            ):

                time.sleep(5)
                continue

            last_signal_timestamp = latest_timestamp

            run_bot_cycle(
                api
            )

            # الانتظار قبل فحص الشمعة التالية
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

            try:

                send_telegram_message(
                    "⚠️ <b>خطأ في البوت</b>\n"
                    f"{str(e)[:700]}"
                )

            except Exception:
                pass

            try:

                if api is not None:
                    api.disconnect_websocket()

            except Exception:
                pass

            api = None

            time.sleep(15)


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()
