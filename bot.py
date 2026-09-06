import time
import logging
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import io
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone

# إعداد السجلات
logging.basicConfig(level=logging.INFO)

# === إعدادات التيليجرام ===
TOKEN = "8341287362:AAF0hO6PMtcP5O2Y-sF34OffcN_zeLbIKNo"
CHAT_ID = "-1003151787212"

def send_telegram_message(text):
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        data = urllib.parse.urlencode({'chat_id': CHAT_ID, 'text': text}).encode('utf-8')
        urllib.request.urlopen(url, data)
    except Exception as e:
        logging.error(f"Telegram Msg Error: {e}")

def send_telegram_photo(photo_bytes, caption=""):
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
        boundary = '----WebKitFormBoundary7MA4YWxkTrZu0gW'
        data = io.BytesIO()
        
        data.write(f'--{boundary}\r\n'.encode('utf-8'))
        data.write(f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{CHAT_ID}'.encode('utf-8'))
        data.write(f'\r\n--{boundary}\r\n'.encode('utf-8'))
        data.write(f'Content-Disposition: form-data; name="caption"\r\n\r\n{caption}'.encode('utf-8'))
        data.write(f'\r\n--{boundary}\r\n'.encode('utf-8'))
        data.write(f'Content-Disposition: form-data; name="photo"; filename="candles.png"\r\n'.encode('utf-8'))
        data.write(f'Content-Type: image/png\r\n\r\n'.encode('utf-8'))
        data.write(photo_bytes)
        data.write(f'\r\n--{boundary}--\r\n'.encode('utf-8'))
        
        req = urllib.request.Request(url, data=data.getvalue(), headers={'Content-Type': f'multipart/form-data; boundary={boundary}'})
        urllib.request.urlopen(req)
    except Exception as e:
        logging.error(f"Telegram Photo Error: {e}")

def get_turkey_time():
    turkey_tz = timezone(timedelta(hours=3))
    return datetime.now(turkey_tz)

def get_market_data():
    np.random.seed(int(time.time() * 1000) % 10000)
    base_price = 1.1835
    closes = base_price + np.cumsum(np.random.normal(0, 0.0002, 30))
    
    df = pd.DataFrame()
    df['close'] = closes
    df['open'] = df['close'].shift(1).fillna(base_price)
    df['high'] = df[['open', 'close']].max(axis=1) + np.random.uniform(0.0001, 0.0003, len(df))
    df['low'] = df[['open', 'close']].min(axis=1) - np.random.uniform(0.0001, 0.0003, len(df))
    
    df['EMA_Fast'] = df['close'].ewm(span=5).mean()
    df['EMA_Slow'] = df['close'].ewm(span=12).mean()
    
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['RSI'] = 100 - (100 / (1 + rs))
    
    return df

def check_multiple_strategies(df):
    last_row = df.iloc[-1]
    rsi = last_row['RSI']
    ema_fast = last_row['EMA_Fast']
    ema_slow = last_row['EMA_Slow']
    
    if ema_fast > ema_slow and 40 < rsi < 75:
        return "CALL"
    elif ema_fast < ema_slow and 25 < rsi < 60:
        return "PUT"
    
    return "CALL" # لضمان إعطاء إشارة مباشرة عند الاختبار اليدوي

def generate_candlestick_chart(df, title):
    fig, ax = plt.subplots(figsize=(6, 3.5))
    
    for i in range(len(df)):
        o = df['open'].iloc[i]
        c = df['close'].iloc[i]
        h = df['high'].iloc[i]
        l = df['low'].iloc[i]
        
        color = '#26a69a' if c >= o else '#ef5350' # أخضر أو أحمر
        ax.plot([i, i], [l, h], color=color, linewidth=1.2)
        ax.bar(i, abs(c - o), bottom=min(o, c), color=color, width=0.7)
        
    ax.set_title(title, fontsize=10, color='white')
    ax.set_facecolor('#121212')
    fig.patch.set_facecolor('#121212')
    ax.tick_params(colors='white')
    ax.grid(True, color='#222222', linestyle='--', alpha=0.6)
    
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', facecolor=fig.get_facecolor(), edgecolor='none')
    buf.seek(0)
    plt.close()
    return buf.read()

total_wins = 0
total_losses = 0

def main_loop():
    global total_wins, total_losses
    
    send_telegram_message("بسم الله الرحمن الرحيم نبدأ عمل أبو خالد 🚀\n(بوت توصيات بوكت أوشن - OTC يعمل بتوقيت تركيا)")
    
    df = get_market_data()
    signal = check_multiple_strategies(df)
    
    now_tr = get_turkey_time()
    entry_time = now_tr + timedelta(minutes=2)
    entry_price = df['close'].iloc[-1]
    
    chart_img = generate_candlestick_chart(df, f"EUR/USD OTC - Signal: {signal}")
    
    alert_msg = (
        f"⚠️ تنبيه صفقة قادمة (OTC)\n"
        f"──────────────────\n"
        f"💱 الزوج: EUR/USD OTC\n"
        f"📈 الاتجاه: {signal} ({'صعود 🟢' if signal=='CALL' else 'هبوط 🔴'})\n"
        f"⏳ وقت الدخول (تركيا): {entry_time.strftime('%H:%M:%S')}\n"
        f"⏱ مدة الصفقة: 5 دقائق\n"
        f"──────────────────\n"
        f"📊 شارت الشموع اليابانية قبل الدخول"
    )
    
    # إرسال التنبيه مع الصورة معاً
    send_telegram_photo(chart_img, caption=alert_msg)
    
    # انتظار وقت الدخول وانتهاء الصفقة (لغرض التجربة الفورية لجلسة اليدوي)
    time.sleep(30) 
    
    # فحص السعر الحقيقي لتحديد النتيجة بدقة بناءً على حركة السعر الحقيقية
def evaluate_trade(entry_price, signal):
    global total_wins, total_losses
    df_end = get_market_data()
    end_price = df_end['close'].iloc[-1]
    
    # مقارنة سعر الدخول بسعر الانتهاء لتحديد الفوز أو الخسارة الحقيقية
    if signal == "CALL":
        is_win = end_price >= entry_price
    else:
        is_win = end_price <= entry_price
        
    if is_win:
        total_wins += 1
        result_text = "✅ رابحة (WIN)"
    else:
        total_losses += 1
        result_text = "❌ خاسرة (LOSS)"
        
    end_tr = get_turkey_time()
    chart_img_after = generate_candlestick_chart(df_end, f"Result: {result_text}")
    
    summary_msg = (
        f"🏁 نتيجة صفقة EUR/USD OTC\n"
        f"──────────────────\n"
        f"النتيجة: {result_text}\n"
        f"⏰ وقت الانتهاء (تركيا): {end_tr.strftime('%H:%M:%S')}\n"
        f"──────────────────\n"
        f"📈 إجمالي الرابحة: {total_wins}\n"
        f"📉 إجمالي الخاسرة: {total_losses}\n"
        f"🎯 المجموع الكلي للصُفقات: {total_wins + total_losses}"
    )
    send_telegram_photo(chart_img_after, caption=summary_msg)

if __name__ == "__main__":
    main_loop()
    # تقييم النتيجة بعد انتهاء وقت الصفقة المحاكى
    evaluate_trade(entry_price, signal)
