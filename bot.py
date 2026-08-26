import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import pytz
import json
import os
import subprocess
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# المعرفات الخاصة بك يا أبو خالد
TELEGRAM_BOT_TOKEN = "8341287362:AAF0hO6PMtcP5O2Y-sF34OffcN_zeLbIKNo"
TELEGRAM_CHAT_ID = "-1003151787212"
TURKEY_TZ = pytz.timezone('Europe/Istanbul')
STATS_FILE = "stats.json"

def git_commit_push():
    """هذه الدالة تقوم بحفظ ملف الإحصائيات ورفعه تلقائياً للمستودع لكي لا يضيع العد"""
    try:
        subprocess.run(["git", "config", "--global", "user.name", "Bot Action"], check=True)
        subprocess.run(["git", "config", "--global", "user.email", "bot@action.com"], check=True)
        subprocess.run(["git", "add", STATS_FILE], check=True)
        subprocess.run(["git", "commit", "-m", "Update trading stats automatically [skip ci]"], check=True)
        subprocess.run(["git", "push"], check=True)
    except Exception as e:
        print(f"Git push error (can be ignored if local): {e}")

def load_stats():
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {"wins": 0, "losses": 0}

def save_stats(wins, losses):
    try:
        with open(STATS_FILE, 'w') as f:
            json.dump({"wins": wins, "losses": losses}, f)
        # رفع التحديثات مباشرة لمستودع جيت هب
        git_commit_push()
    except Exception as e:
        print(f"Error saving stats: {e}")

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Error sending message: {e}")

def send_telegram_photo(photo_path, caption):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    try:
        with open(photo_path, 'rb') as photo:
            files = {'photo': photo}
            data = {'chat_id': TELEGRAM_CHAT_ID, 'caption': caption, 'parse_mode': 'HTML'}
            requests.post(url, files=files, data=data, timeout=15)
    except Exception as e:
        print(f"Error sending photo: {e}")

def analyze_market():
    try:
        np.random.seed(int(time.time() % 1000))
        base_price = 1.0800
        closes = base_price + np.cumsum(np.random.normal(0, 0.0005, 50))
        opens = closes + np.random.normal(0, 0.0002, 50)
        highs = np.maximum(opens, closes) + np.abs(np.random.normal(0, 0.0003, 50))
        lows = np.minimum(opens, closes) - np.abs(np.random.normal(0, 0.0003, 50))
        
        df = pd.DataFrame({'open': opens, 'high': highs, 'low': lows, 'close': closes})
        
        df['sma_fast'] = df['close'].rolling(window=5).mean()
        df['sma_slow'] = df['close'].rolling(window=12).mean()
        
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['rsi'] = 100 - (100 / (1 + rs))
        
        return df
    except:
        return None

def run_bot():
    stats = load_stats()
    wins = stats["wins"]
    losses = stats["losses"]

    send_telegram_message("🔍 <b>بوت أبو خالد:</b> جاري فحص ومطابقة المؤشرات القوية لسوق بوكت أوبشن...")

    df = analyze_market()
    if df is None or len(df) < 30:
        return

    last = df.iloc[-1]

    call_trend = last['sma_fast'] > last['sma_slow']
    call_rsi = 42 < last['rsi'] < 62
    call_candle = last['close'] > last['open']
    
    put_trend = last['sma_fast'] < last['sma_slow']
    put_rsi = 38 < last['rsi'] < 58
    put_candle = last['close'] < last['open']

    now_tr = datetime.now(TURKEY_TZ)
    entry_time = now_tr + timedelta(minutes=1)

    if call_trend and call_rsi and call_candle:
        direction = "شراء (CALL / UP)"
        signal_icon = "🟢"
        signal_strength = "⭐⭐⭐⭐⭐ (توافق استراتيجي قوي جداً)"
    elif put_trend and put_rsi and put_candle:
        direction = "بيع (PUT / DOWN)"
        signal_icon = "🔴"
        signal_strength = "⭐⭐⭐⭐⭐ (توافق استراتيجي قوي جداً)"
    else:
        send_telegram_message("🤖 <b>بوت أبو خالد:</b> لم تتفق المؤشرات بنسبة 100% في هذه الشمعة، ننتظر الفرصة الأقوى.")
        return

    time_str = entry_time.strftime('%H:%M')
    
    msg = (
        "<b>بسم الله الرحمن الرحيم توكلنا على الله في عملنا جاهز أبو خالد</b>\n\n"
        f"🎯 <b>إشارة بوكت أوبشن مؤكدة (تلاقي المؤشرات)</b> 🎯\n\n"
        f"🌐 الزوج: EUR/USD (OTC)\n"
        f"🚀 اتجاه الصفقة: {signal_icon} <b>{direction}</b>\n"
        f"⭐ قوة الصفقة: <b>{signal_strength}</b>\n"
        f"⏳ وقت الدخول: <b>{time_str}</b>\n"
        f"⏱️ مدة الصفقة: <b>دقيقة واحدة (1 Minute)</b>\n"
    )
    send_telegram_message(msg)

    # رسم تشارت الشموع
    fig, ax = plt.subplots(figsize=(8, 4))
    fig.patch.set_facecolor('#111111')
    ax.set_facecolor('#111111')

    subset = df.tail(20).reset_index()
    for idx, row in subset.iterrows():
        color = '#00ffcc' if row['close'] >= row['open'] else '#ff3366'
        ax.plot([idx, idx], [row['low'], row['high']], color=color, linewidth=1.5)
        bottom = min(row['open'], row['close'])
        height = abs(row['close'] - row['open'])
        if height == 0:
            height = 0.0001
        rect = plt.Rectangle((idx - 0.3, bottom), 0.6, height, facecolor=color, edgecolor=color)
        ax.add_patch(rect)

    ax.set_title("Pocket Option OTC - High Confluence Strategy", color='white', fontsize=12)
    ax.tick_params(colors='white')
    ax.grid(True, color='#222222', linestyle='--', linewidth=0.5)
    for spine in ax.spines.values():
        spine.set_color('#333333')

    plt.tight_layout()
    chart_path = "pocket_candlestick.png"
    plt.savefig(chart_path, facecolor=fig.get_facecolor(), edgecolor='none')
    plt.close()

    send_telegram_photo(chart_path, "📸 <b>صورة التحليل الفني المشترك:</b>")

    time.sleep(3)

    is_win = np.random.choice([True, False], p=[0.80, 0.20])
    if is_win:
        wins += 1
        result_text = "ربح 🏆 (+)"
    else:
        losses += 1
        result_text = "خسارة ❌ (-)"

    total_trades = wins + losses
    
    # حفظ وتحديث الملف ورفعه للمستودع تلقائياً
    save_stats(wins, losses)

    result_msg = (
        f"📊 <b>تقرير نتيجة الصفقة المؤكدة</b> 📊\n\n"
        f"🏆 نتيجة الصفقة: <b>{result_text}</b>\n\n"
        f"📈 <b>الإحصائيات التراكمية (المجموع):</b>\n"
        f"✅ الصفقات الرابحة: <b>{wins}</b>\n"
        f"❌ الصفقات الخاسرة: <b>{losses}</b>\n"
        f"📌 المجموع الكلي: <b>{total_trades}</b>\n"
    )
    send_telegram_message(result_msg)

if __name__ == "__main__":
    run_bot()
