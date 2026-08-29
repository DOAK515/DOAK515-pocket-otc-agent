import os
import time
import logging
import asyncio
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
from pocket_option import AsyncPocketOptionClient, OrderDirection

# ============================================================ #
# CONFIG (بيانات التليجرام الخاصة بك يا أبو خالد)
# ============================================================ #
PO_SSID = os.getenv("PO_SSID", "").strip()
TELEGRAM_BOT_TOKEN = "8341287362:AAF0hO6PMtcP5O2Y-sF34OffcN_zeLbIKNo"
TELEGRAM_CHAT_ID = "-1003151787212"

ASSET = "EURUSD_otc"
ASSET_NAME = "EUR/USD (OTC)"
TURKEY_TZ = pytz.timezone("Europe/Istanbul")

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
# TELEGRAM FUNCTIONS
# ============================================================ #
def send_telegram_message(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return None
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        response = requests.post(url, json=payload, timeout=15)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error("Telegram message error: %s", e)
        return None

def send_telegram_photo(photo_path, caption):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return None
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    try:
        with open(photo_path, "rb") as photo_file:
            files = {"photo": photo_file}
            data = {
                "chat_id": TELEGRAM_CHAT_ID,
                "caption": caption,
                "parse_mode": "HTML"
            }
            response = requests.post(url, data=data, files=files, timeout=30)
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.error("Telegram photo error: %s", e)
        return None

# ============================================================ #
# STRATEGY LOGIC
# ============================================================ #
def verify_pocket_otc_strategies(df):
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
    rsi = 100.0 - (100.0 / (1.0 + rs))

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
# CHART GENERATOR
# ============================================================ #
def generate_pocket_option_otc_chart(asset_name, df, suffix_title):
    try:
        chart_df = df.tail(45).copy()
        fig, ax = plt.subplots(figsize=(11, 5.5), dpi=150)
        fig.patch.set_facecolor("#121212")
        ax.set_facecolor("#1e1e1e")

        times = [datetime.fromtimestamp(ts, tz=TURKEY_TZ) for ts in chart_df["timestamp"]]
        x_values = mdates.date2num(times)

        candle_width = (np.median(np.diff(x_values)) * 0.86) if len(x_values) > 1 else 0.0005

        for i, (_, row) in enumerate(chart_df.iterrows()):
            x = x_values[i]
            open_p = float(row["open"])
            high_p = float(row["high"])
            low_p = float(row["low"])
            close_p = float(row["close"])

            color = "#00df89" if close_p >= open_p else "#ff3344"

            ax.plot([x, x], [low_p, high_p], color=color, linewidth=1.1, zorder=1)

            body_bottom = min(open_p, close_p)
            body_height = abs(close_p - open_p)
            if body_height <= 0:
                body_height = max(abs(high_p - low_p) * 0.01, 1e-8)

            rect = Rectangle(
                (x - candle_width / 2, body_bottom),
                candle_width, body_height,
                facecolor=color, edgecolor=color, linewidth=0.5, zorder=2
            )
            ax.add_patch(rect)

        ax.set_title(f"Pocket Option OTC | {asset_name} | {suffix_title}", color="white", fontsize=13, fontweight="bold")
        ax.tick_params(colors="#aaaaaa", labelsize=9)
        ax.grid(True, color="#2a2a2a", linestyle="--", alpha=0.6)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=TURKEY_TZ))
        fig.autofmt_xdate()
        plt.tight_layout()

        file_path = f"pocket_otc_{int(time.time())}.png"
        plt.savefig(file_path, bbox_inches="tight", facecolor=fig.get_facecolor(), edgecolor="none", dpi=150)
        plt.close(fig)
        return file_path
    except Exception as e:
        logger.error("Chart error: %s", e)
        return None

# ============================================================ #
# ASYNC BOT MAIN WORKFLOW
# ============================================================ #
async def main_async():
    global total_wins, total_losses

    if not PO_SSID:
        logger.error("PO_SSID is missing!")
        return

    logger.info("Connecting to Pocket Option using Async Client...")
    client = AsyncPocketOptionClient(ssid=PO_SSID, is_demo=True)
    
    try:
        await client.connect()
        logger.info("Connected to Pocket Option successfully.")
    except Exception as e:
        logger.error("Failed to connect: %s", e)
        send_telegram_message(f"⚠️ <b>خطأ اتصال بالمنصة:</b>\n{str(e)[:300]}")
        return

    send_telegram_message(
        "🤖 <b>Pocket Option OTC Bot بدأ العمل</b>\n\n"
        "🕯️ مصدر الشموع: Pocket Option OTC\n"
        "⏱️ الإطار: 1 دقيقة\n"
        "🛡️ النظام: بدون مضاعفات\n"
        "📡 الوضع: إشارات فقط"
    )

    while True:
        try:
            candles_data = await client.get_candles(ASSET, period=60, count=80)
            if not candles_data:
                await asyncio.sleep(10)
                continue

            # تحويل البيانات إلى DataFrame
            df = pd.DataFrame(candles_data)
            df = df.rename(columns={"time": "timestamp", "o": "open", "h": "high", "l": "low", "c": "close"})
            for col in ["open", "high", "low", "close", "timestamp"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df = df.dropna().sort_values("timestamp").reset_index(drop=True)

            if len(df) < 25:
                await asyncio.sleep(10)
                continue

            signal = verify_pocket_otc_strategies(df)
            latest = df.iloc[-1]
            entry_price = float(latest["close"])
            signal_ts = float(latest["timestamp"])
            signal_time = datetime.fromtimestamp(signal_ts, tz=TURKEY_TZ)

            logger.info("Signal check: %s | Price: %s | Result: %s", signal_time.strftime("%H:%M:%S"), entry_price, signal)

            if signal != "NEUTRAL":
                direction_text = "شراء (CALL / UP) 🟢" if signal == "UP" else "بيع (PUT / DOWN) 🔴"
                entry_time = signal_time + timedelta(seconds=60)
                
                chart_entry = generate_pocket_option_otc_chart(ASSET_NAME, df, "Signal Entry")
                signal_msg = (
                    "🎯 <b>إشارة Pocket Option OTC</b> 🎯\n\n"
                    f"💱 <b>الزوج:</b> {ASSET_NAME}\n"
                    f"🚀 <b>القرار:</b> <b>{direction_text}</b>\n"
                    f"⏰ <b>وقت الدخول:</b> {entry_time.strftime('%H:%M')}\n"
                    f"⏱️ <b>مدة الصفقة:</b> 1 دقيقة\n"
                    f"💵 <b>سعر الإشارة:</b> {entry_price:.6f}\n\n"
                    "📸 تشارت OTC:"
                )

                if chart_entry:
                    send_telegram_photo(chart_entry, signal_msg)
                else:
                    send_telegram_message(signal_msg)

                # انتظار انتهاء الشمعة للتحقق من النتيجة
                await asyncio.sleep(65)
                
                # جلب الشمعة الجديدة لفحص النتيجة
                new_candles = await client.get_candles(ASSET, period=60, count=10)
                if new_candles:
                    ndf = pd.DataFrame(new_candles)
                    ndf = ndf.rename(columns={"time": "timestamp", "o": "open", "h": "high", "l": "low", "c": "close"})
                    exit_price = float(ndf.iloc[-1]["close"])
                    
                    is_win = (exit_price >= entry_price) if signal == "UP" else (exit_price <= entry_price)
                    if is_win:
                        total_wins += 1
                        res_text = "(+ WIN) ربح 🟢"
                    else:
                        total_losses += 1
                        res_text = "(- LOSS) خسارة 🔴"

                    res_msg = (
                        "📊 <b>نتيجة صفقة Pocket Option OTC</b> 📊\n\n"
                        f"💱 <b>الزوج:</b> {ASSET_NAME}\n"
                        f"🚀 <b>الإشارة:</b> {direction_text}\n"
                        f"💵 <b>سعر الدخول:</b> {entry_price:.6f}\n"
                        f"🏁 <b>سعر الإغلاق:</b> {exit_price:.6f}\n"
                        f"🏆 <b>الحالة:</b> <b>{res_text}</b>\n\n"
                        f"📈 <b>الإحصائيات:</b> ربح: {total_wins} | خسارة: {total_losses}"
                    )
                    send_telegram_message(res_msg)

            await asyncio.sleep(20)

        except Exception as e:
            logger.error("Loop error: %s", e)
            await asyncio.sleep(15)

if __name__ == "__main__":
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        logger.info("Bot stopped.")
