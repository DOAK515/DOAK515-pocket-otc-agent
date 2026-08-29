import os
import time
import logging
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import pytz
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle
from pocketoptionapi import PocketOption

# ============================================================ #
# CONFIG (تمت إضافة بيانات التليجرام الخاصة بك مباشرة يا أبو خالد)
# ============================================================ #
PO_SSID = os.getenv("PO_SSID", "").strip()
TELEGRAM_BOT_TOKEN = "8341287362:AAF0hO6PMtcP5O2Y-sF34OffcN_zeLbIKNo"
TELEGRAM_CHAT_ID = "-1003151787212"

ASSET = "EURUSD_otc"
ASSET_NAME = "EUR/USD (OTC)"
CANDLE_PERIOD = 60 # 1 minute
HISTORY_CANDLES = 80 # عدد الشموع المستخدمة للتحليل
EXPIRATION_SECONDS = 60 # مدة الإشارة دقيقة
TURKEY_TZ = pytz.timezone("Europe/Istanbul")

# بدون مضاعفات
total_wins = 0
total_losses = 0

# ============================================================ #
# LOGGING
# ============================================================ #
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("PO_OTC_BOT")

# ============================================================ #
# TELEGRAM
# ============================================================ #
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
            url, json=payload, timeout=15
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error("Telegram message error: %s", e)
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
                url, data=data, files=files, timeout=30
            )
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.error("Telegram photo error: %s", e)
        return None

# ============================================================ #
# POCKET OPTION CONNECTION
# ============================================================ #
def connect_pocket_option():
    if not PO_SSID:
        raise RuntimeError(
            "PO_SSID غير موجود. "
            "ضع SSID الخاص بجلسة Pocket Option في متغير البيئة."
        )
    logger.info("Connecting to Pocket Option...")
    api = PocketOption(PO_SSID)
    ok, error = api.connect()
    if not ok:
        raise RuntimeError(
            f"Pocket Option connection failed: {error}"
        )
    
    # انتظار مزامنة الاتصال والوقت
    timeout = time.time() + 30
    while time.time() < timeout:
        if api.check_connect() and api.is_time_synced():
            logger.info("Pocket Option connected and time synchronized.")
            break
        time.sleep(0.25)
    else:
        raise RuntimeError(
            "Pocket Option connected but server time was not synchronized."
        )
    
    # الاشتراك في EURUSD OTC - شموع دقيقة
    api.subscribe(
        ASSET, period=CANDLE_PERIOD
    )
    time.sleep(2)
    return api

# ============================================================ #
# CANDLE DATA
# ============================================================ #
def normalize_candles(raw_candles):
    """
    تحويل بيانات Pocket Option إلى DataFrame موحد.
    المطلوب: timestamp open high low close
    """
    if raw_candles is None:
        return None
    if isinstance(raw_candles, pd.DataFrame):
        df = raw_candles.copy()
    elif isinstance(raw_candles, list):
        df = pd.DataFrame(raw_candles)
    else:
        try:
            df = pd.DataFrame(raw_candles)
        except Exception:
            return None

    if df.empty:
        return None

    # توحيد أسماء الأعمدة
    rename_map = {}
    for column in df.columns:
        c = str(column).lower()
        if c in ("time", "timestamp", "created_at", "at"):
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
        "timestamp", "open", "high", "low", "close"
    ]
    missing = [
        c for c in required if c not in df.columns
    ]
    if missing:
        logger.error(
            "Missing candle columns: %s | received=%s",
            missing, list(df.columns)
        )
        return None

    # تحويل OHLC إلى أرقام
    for column in [
        "open", "high", "low", "close"
    ]:
        df[column] = pd.to_numeric(
            df[column], errors="coerce"
        )

    # timestamp
    ts = pd.to_numeric(
        df["timestamp"], errors="coerce"
    )

    # Pocket Option عادةً يستخدم Unix timestamp.
    # إذا كانت القيم صغيرة جدًا نفترض أنها بالميلي ثانية.
    if ts.dropna().empty:
        return None

    median_ts = ts.dropna().median()
    if median_ts > 10_000_000_000:
        ts = ts / 1000.0

    df["timestamp"] = ts
    df = df.dropna(
        subset=[
            "timestamp", "open", "high", "low", "close"
        ]
    )
    df = df.sort_values("timestamp")
    df = df.drop_duplicates(
        subset=["timestamp"], keep="last"
    )
    df = df.reset_index(drop=True)
    return df

def get_completed_candles(api, count=HISTORY_CANDLES):
    """
    جلب آخر الشموع من Pocket Option.
    يتم حذف الشمعة الحالية غير المكتملة حتى لا يدخل التحليل على شمعة ما زالت تتحرك.
    """
    raw = api.get_historical_candles(
        ASSET, period=CANDLE_PERIOD, offset=45000, count_request=1
    )
    df = normalize_candles(raw)
    if df is None or len(df) < 30:
        return None

    # نستخدم وقت خادم Pocket Option إذا توفر
    try:
        server_ts = float(
            api.get_server_timestamp()
        )
    except Exception:
        server_ts = time.time()

    # الشمعة التي ما زالت تتكون
    current_bucket = (
        int(server_ts) // CANDLE_PERIOD
    ) * CANDLE_PERIOD

    # الاحتفاظ بالشموع المغلقة فقط
    df = df[
        df["timestamp"] < current_bucket
    ].copy()

    if len(df) < 30:
        return None

    return df.tail(count).reset_index(drop=True)

# ============================================================ #
# STRATEGY
# ============================================================ #
def verify_pocket_otc_strategies(df):
    """
    نفس فكرة الاستراتيجية الموجودة في الكود الأصلي:
    RSI, MA Short, MA Long, Momentum, SMA 20, Standard Deviation
    """
    if df is None or len(df) < 25:
        return "NEUTRAL"

    prices = df["close"].astype(float).to_numpy()
    deltas = np.diff(prices)
    if len(deltas) < 14:
        return "NEUTRAL"

    seed = deltas[-14:]
    up = seed[seed >= 0].sum() / 14.0
    down = -seed[seed < 0].sum() / 14.0
    rs = up / (down if down != 0 else 0.001)
    rsi = 100.0 - (
        100.0 / (1.0 + rs)
    )

    ma_short = np.mean(prices[-3:])
    ma_long = np.mean(prices[-10:])
    momentum = prices[-1] - prices[-4]
    
    sma_20 = np.mean(prices[-20:])
    std_20 = np.std(prices[-20:])
    current_price = prices[-1]

    is_up = (
        ma_short > ma_long and 60 > rsi > 46 and momentum > 0 and current_price <= sma_20 + std_20
    )
    is_down = (
        ma_short < ma_long and 54 > rsi > 40 and momentum < 0 and current_price >= sma_20 - std_20
    )

    if is_up:
        return "UP"
    if is_down:
        return "DOWN"
    return "NEUTRAL"

# ============================================================ #
# CHART
# ============================================================ #
def generate_pocket_option_otc_chart(
    asset_name, df, suffix_title
):
    try:
        chart_df = df.tail(45).copy()
        fig, ax = plt.subplots(
            figsize=(11, 5.5), dpi=150
        )
        fig.patch.set_facecolor("#121212")
        ax.set_facecolor("#1e1e1e")

        times = [
            datetime.fromtimestamp(
                ts, tz=TURKEY_TZ
            ) for ts in chart_df["timestamp"]
        ]
        x_values = mdates.date2num(times)

        if len(x_values) > 1:
            candle_width = (
                np.median(np.diff(x_values)) * 0.86
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

            # الفتيل
            ax.plot(
                [x, x], [low_p, high_p],
                color=color, linewidth=1.1, zorder=1
            )

            body_bottom = min(
                open_p, close_p
            )
            body_height = abs(
                close_p - open_p
            )
            if body_height <= 0:
                body_height = max(
                    abs(high_p - low_p) * 0.01, 1e-8
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
                linewidth=0.5,
                zorder=2
            )
            ax.add_patch(rect)

        ax.set_title(
            f"Pocket Option OTC | {asset_name} | {suffix_title}",
            color="white", fontsize=13, fontweight="bold"
        )
        ax.tick_params(
            colors="#aaaaaa", labelsize=9
        )
        ax.grid(
            True, color="#2a2a2a", linestyle="--", alpha=0.6
        )
        ax.xaxis.set_major_formatter(
            mdates.DateFormatter( "%H:%M", tz=TURKEY_TZ )
        )
        fig.autofmt_xdate()
        plt.tight_layout()

        file_path = (
            f"pocket_otc_"
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
            "Chart error: %s", e
        )
        return None

# ============================================================ #
# WAIT FOR NEXT CLOSED CANDLE
# ============================================================ #
def wait_for_next_closed_candle(
    api, previous_timestamp, timeout=90
):
    """
    ينتظر إغلاق شمعة الدقيقة التالية.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            df = get_completed_candles(
                api, count=HISTORY_CANDLES
            )
            if df is not None and not df.empty:
                latest_timestamp = float(
                    df.iloc[-1]["timestamp"]
                )
                if latest_timestamp > previous_timestamp:
                    return df
        except Exception as e:
            logger.warning(
                "Waiting candle error: %s", e
            )
        time.sleep(2)
    return None

# ============================================================ #
# RESULT
# ============================================================ #
def calculate_result(
    direction, entry_price, exit_price
):
    if direction == "UP":
        return exit_price >= entry_price
    if direction == "DOWN":
        return exit_price <= entry_price
    return False

# ============================================================ #
# MAIN CYCLE
# ============================================================ #
def run_bot_cycle(api):
    global total_wins
    global total_losses

    logger.info(
        "Analyzing %s using Pocket Option OTC candles...", ASSET_NAME
    )
    df = get_completed_candles(
        api, count=HISTORY_CANDLES
    )
    if df is None:
        logger.warning(
            "Could not obtain enough OTC candles."
        )
        return

    if len(df) < 25:
        logger.warning(
            "Not enough candles: %s", len(df)
        )
        return

    signal = verify_pocket_otc_strategies(df)
    latest = df.iloc[-1]
    entry_price = float(
        latest["close"]
    )
    signal_candle_timestamp = float(
        latest["timestamp"]
    )
    signal_candle_time = datetime.fromtimestamp(
        signal_candle_timestamp, tz=TURKEY_TZ
    )

    logger.info(
        "Last closed candle: %s | Close=%s | Signal=%s",
        signal_candle_time.strftime("%H:%M:%S"),
        entry_price,
        signal
    )

    if signal == "NEUTRAL":
        logger.info(
            "No signal. Conditions not satisfied."
        )
        return

    if signal == "UP":
        direction = "شراء (CALL / UP) 🟢"
    else:
        direction = "بيع (PUT / DOWN) 🔴"

    entry_time = signal_candle_time + timedelta(
        seconds=CANDLE_PERIOD
    )
    formatted_entry_time = (
        entry_time.strftime("%H:%M")
    )

    chart_entry = generate_pocket_option_otc_chart(
        ASSET_NAME, df, "Signal Entry"
    )

    signal_msg = (
        "🎯 <b>إشارة Pocket Option OTC</b> 🎯\n\n"
        f"💱 <b>الزوج:</b> {ASSET_NAME}\n"
        f"🚀 <b>القرار:</b> <b>{direction}</b>\n"
        f"⏰ <b>وقت الدخول:</b> {formatted_entry_time}\n"
        f"⏱️ <b>مدة الصفقة:</b> 1 دقيقة\n"
        f"💵 <b>سعر الإشارة:</b> {entry_price:.6f}\n\n"
        "🕯️ الشموع مأخوذة من بيانات Pocket Option OTC.\n"
        "🛡️ بدون مضاعفات.\n\n"
        "📸 تشارت OTC:"
    )

    if chart_entry:
        send_telegram_photo(
            chart_entry, signal_msg
        )
    else:
        send_telegram_message(
            signal_msg
        )

    result_df = wait_for_next_closed_candle(
        api, signal_candle_timestamp, timeout=90
    )
    if result_df is None:
        logger.warning(
            "Could not obtain next completed candle."
        )
        send_telegram_message(
            "⚠️ لم أستطع الحصول على شمعة الإغلاق التالية "
            "من Pocket Option، لذلك لم يتم احتساب WIN/LOSS."
        )
        return

    exit_candle = result_df.iloc[-1]
    exit_price = float(
        exit_candle["close"]
    )
    exit_timestamp = float(
        exit_candle["timestamp"]
    )
    exit_time = datetime.fromtimestamp(
        exit_timestamp, tz=TURKEY_TZ
    )

    is_win = calculate_result(
        signal, entry_price, exit_price
    )
    if is_win:
        total_wins += 1
        result_text = (
            "(+ WIN) ربح 🟢"
        )
    else:
        total_losses += 1
        result_text = (
            "(- LOSS) خسارة 🔴"
        )

    chart_result = generate_pocket_option_otc_chart(
        ASSET_NAME, result_df, "Execution Result"
    )
    result_msg = (
        "📊 <b>نتيجة صفقة Pocket Option OTC</b> 📊\n\n"
        f"💱 <b>الزوج:</b> {ASSET_NAME}\n"
        f"🚀 <b>الإشارة:</b> {direction}\n"
        f"💵 <b>سعر الدخول:</b> {entry_price:.6f}\n"
        f"🏁 <b>سعر الإغلاق:</b> {exit_price:.6f}\n"
        f"🏆 <b>الحالة:</b> <b>{result_text}</b>\n\n"
        f"📈 <b>إحصائيات:</b>\n"
        f"✅ الربح: {total_wins}\n"
        f"❌ الخسارة: {total_losses}\n"
        f"📌 الإجمالي: {total_wins + total_losses}\n\n"
        f"🕐 <b>وقت الإغلاق:</b> "
        f"{exit_time.strftime('%H:%M:%S')}"
    )

    if chart_result:
        send_telegram_photo(
            chart_result, result_msg
        )
    else:
        send_telegram_message(
            result_msg
        )

# ============================================================ #
# MAIN
# ============================================================ #
def main():
    logger.info(
        "Starting Pocket Option OTC signal bot..."
    )
    send_telegram_message(
        "🤖 <b>Pocket Option OTC Bot بدأ العمل</b>\n\n"
        "🕯️ مصدر الشموع: Pocket Option OTC\n"
        "⏱️ الإطار: 1 دقيقة\n"
        "🛡️ النظام: بدون مضاعفات\n"
        "📡 الوضع: إشارات فقط"
    )

    api = None
    while True:
        try:
            if (
                api is None
                or not api.check_connect()
            ):
                api = connect_pocket_option()

            run_bot_cycle(api)
            time.sleep(10)

        except KeyboardInterrupt:
            logger.info(
                "Bot stopped by user."
            )
            break
        except Exception as e:
            logger.exception(
                "Main loop error: %s", e
            )
            send_telegram_message(
                f"⚠️ <b>خطأ في البوت:</b>\n{str(e)[:500]}"
            )
            try:
                if api is not None:
                    api.disconnect_websocket()
            except Exception:
                pass
            api = None
            time.sleep(15)

if __name__ == "__main__":
    main()
