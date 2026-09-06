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

# استيراد مكتبة الاتصال بمنصة بوكت أوشن
from pocketoptionapi.stable_api import PocketOption

logging.basicConfig(level=logging.INFO)

TOKEN = "8341287362:AAF0hO6PMtcP5O2Y-sF34OffcN_zeLbIKNo"
CHAT_ID = "-1003151787212"

# بيانات الدخول لحسابك في بوكت أوشن (يمكن وضعها هنا أو كـ Secrets في جيثب)
EMAIL = "YOUR_EMAIL_HERE"
PASSWORD = "YOUR_PASSWORD_HERE"

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
        data.write(f'Content-Disposition: form-data; name="photo"; filename="pocket_live.png"\r\n'.encode('utf-8'))
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

def fetch_real_pocket_candles(api_client, asset="EURUSD_otc"):
    """جلب الشموع الحقيقية المباشرة من منصة بوكت أوشن للـ OTC"""
    try:
        # طلب الشموع بفريم 5 دقائق (300 ثانية)
        candles = api_client.get_candles(asset, 300)
        df = pd.DataFrame(candles)
        # تنسيق الأعمدة حسب ما ترد من المنصة
        if 'open' not in df.columns and 'o' in df.columns:
            df = df.rename(columns={'o': 'open', 'c': 'close', 'h': 'high', 'l': 'low'})
        
        # حساب المؤشرات الفنية بدقة على الشموع الحقيقية
        df['EMA_Fast'] = df['close'].ewm(span=5).mean()
        df['EMA_Slow'] = df['close'].ewm(span=12).mean()
        
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))
        df['RSI'] = df['RSI'].fillna(50)
        return df
    except Exception as e:
        logging.error(f"Error fetching real candles: {e}")
        return None

def check_multiple_strategies(df):
    last_row = df.iloc[-1]
    rsi = last_row['RSI']
    ema_fast = last_row['EMA_Fast']
    ema_slow = last_row['EMA_Slow']
    
    if ema_fast >= ema_slow and rsi >= 45:
        return "CALL"
    else:
        return "PUT"

def generate_pocket_option_style_chart(df, title):
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(7, 5), gridspec_kw={'height_ratios': [3, 1]}, sharex=True)
    bg_color = '#121824'
    grid_color = '#1e2636'
    fig.patch.set_facecolor(bg_color)
    ax1.set_facecolor(bg_color)
    ax2.set_facecolor(bg_color)
    
    for i in range(len(df)):
        o = df['open'].iloc[i]
        c = df['close'].iloc[i]
        h = df['high'].iloc[i]
        l = df['low'].iloc[i]
        color = '#00c853' if c >= o else '#ff5252'
        ax1.plot([i, i], [l, h], color=color, linewidth=1.2, zorder=1)
        body_bottom = min(o, c)
        body_height = max(abs(c - o), 0.00003)
        ax1.bar(i, body_height, bottom=body_bottom, color=color, width=0.7, zorder=2)

    ax1.set_title(title, fontsize=11, color='white', fontweight='bold', pad=10)
    ax1.tick_params(colors='#8b949e', labelsize=8)
    ax1.grid(True, color=grid_color, linestyle='-', linewidth=0.5, alpha=0.7)
    for spine in ax1.spines.values():
        spine.set_color('#2b3648')

    ax2.plot(df['RSI'].values, color='#00e5ff', linewidth=1.2)
    ax2.axhline(70, color='#ff5252', linestyle='--', linewidth=0.8, alpha=0.7)
    ax2.axhline(50, color='#ffeb3b', linestyle='-', linewidth=0.8, alpha=0.5)
    ax2.axhline(30, color='#00c853', linestyle='--', linewidth=0.8, alpha=0.7)
    ax2.set_ylabel('RSI (14)', color='#8b949e', fontsize=8)
    ax2.set_ylim(0, 100)
    ax2.tick_params(colors='#8b949e', labelsize=8)
    ax2.grid(True, color=grid_color, linestyle='-', linewidth=0.5, alpha=0.7)
    for spine in ax2.spines.values():
        spine.set_color('#2b3648')

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', facecolor=fig.get_facecolor(), edgecolor='none', dpi=150)
    buf.seek(0)
    plt.close()
    return buf.read()

total_wins = 0
total_losses = 0

def main():
    global total_wins, total_losses
    send_telegram_message("بسم الله الرحمن الرحيم - بدء ربط البوت المباشر بمنصة بوكت أوشن OTC 🚀")
    
    # الاتصال بالمنصة
    api = PocketOption(EMAIL, PASSWORD)
    api.connect()
    
    if not api.check_connect():
        send_telegram_message("❌ فشل الاتصال المباشر بمنصة بوكت أوشن، يرجى التحقق من البريد وكلمة المرور.")
        return

    send_telegram_message("✅ تم الاتصال بنجاح بسيرفرات بوكت أوشن OTC. البوت يعمل الآن بشكل مستمر ودائم.")

    while True:
        try:
            # جلب البيانات الحقيقية للزوج EURUSD_otc
            df = fetch_real_pocket_candles(api, "EURUSD_otc")
            if df is None or len(df) == 0:
                time.sleep(30)
                continue
                
            signal = check_multiple_strategies(df)
            
            now_tr = get_turkey_time()
            entry_time = now_tr + timedelta(minutes=2)
            entry_price = df['close'].iloc[-1]
            
            chart_img = generate_pocket_option_style_chart(df, f"EUR/USD OTC | Signal: {signal}")
            
            alert_msg = (
                f"⚠️ تنبيه صفقة قادمة (منصة Pocket Option حقيقي)\n"
                f"──────────────────\n"
                f"💱 الزوج: EUR/USD OTC\n"
                f"📈 الاتجاه: {signal} ({'صعود 🟢' if signal=='CALL' else 'هبوط 🔴'})\n"
                f"⏳ وقت الدخول (تركيا): {entry_time.strftime('%H:%M:%S')}\n"
                f"⏱ مدة الصفقة: 5 دقائق\n"
                f"──────────────────\n"
                f"📊 شارت حقيقي مسحوب مباشرة من المنصة"
            )
            
            send_telegram_photo(chart_img, caption=alert_msg)
            
            # الانتظار حتى تنتهي الصفقة (7 دقائق)
            time.sleep(420)
            
            # جلب السعر بعد الانتهاء للتقييم الحقيقي
            df_end = fetch_real_pocket_candles(api, "EURUSD_otc")
            end_price = df_end['close'].iloc[-1]
            
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
            chart_img_after = generate_pocket_option_style_chart(df_end, f"Result: {result_text}")
            
            summary_msg = (
                f"🏁 نتيجة صفقة EUR/USD OTC الحقيقية\n"
                f"──────────────────\n"
                f"النتيجة: {result_text}\n"
                f"⏰ وقت الانتهاء (تركيا): {end_tr.strftime('%H:%M:%S')}\n"
                f"──────────────────\n"
                f"📈 إجمالي الرابحة: {total_wins}\n"
                f"📉 إجمالي الخاسرة: {total_losses}\n"
                f"🎯 المجموع الكلي للصُفقات: {total_wins + total_losses}"
            )
            send_telegram_photo(chart_img_after, caption=summary_msg)
            
            time.sleep(60)
            
        except Exception as e:
            logging.error(f"Error in main loop: {e}")
            time.sleep(30)

if __name__ == "__main__":
    main()
