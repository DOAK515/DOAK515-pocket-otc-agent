import time
import logging
import asyncio
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import io
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone

from pocket_option import PocketOptionClient
from pocket_option.models import Asset

logging.basicConfig(level=logging.INFO)

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
        data.write(f'Content-Disposition: form-data; name="photo"; filename="pocket_otc_live.png"\r\n'.encode('utf-8'))
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

def generate_chart(df, title):
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
        ax1.bar(i, abs(c - o), bottom=min(o, c), color=color, width=0.7, zorder=2)

    ax1.set_title(title, fontsize=11, color='white', fontweight='bold', pad=10)
    ax1.tick_params(colors='#8b949e', labelsize=8)
    ax1.grid(True, color=grid_color, alpha=0.7)
    
    ax2.plot(df['RSI'].values, color='#00e5ff', linewidth=1.2)
    ax2.axhline(70, color='#ff5252', linestyle='--', alpha=0.7)
    ax2.axhline(50, color='#ffeb3b', linestyle='-', alpha=0.5)
    ax2.axhline(30, color='#00c853', linestyle='--', alpha=0.7)
    ax2.set_ylabel('RSI', color='#8b949e', fontsize=8)
    ax2.set_ylim(0, 100)
    ax2.tick_params(colors='#8b949e', labelsize=8)
    ax2.grid(True, color=grid_color, alpha=0.7)

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', facecolor=fig.get_facecolor(), dpi=150)
    buf.seek(0)
    plt.close()
    return buf.read()

async def run_pocket_bot():
    send_telegram_message("🚀 بدء تشغيل بوت أبو خالد متصلاً مباشرة بسيرفرات Pocket Option OTC الحقيقية")
    
    client = PocketOptionClient(logger=True)
    
    while True:
        try:
            # الاتصال المباشر بالمنصة وسحب أسعار الأوتسي الحقيقية EURUSD_otc
            await client.connect()
            
            # جلب الشموع الفورية المباشرة من المنصة
            candles = await client.get_candles(Asset.EURUSD_OTC, period=300) # فريم 5 دقائق
            
            if not candles:
                await asyncio.sleep(30)
                continue
                
            df = pd.DataFrame(candles)
            
            # حساب المؤشرات الفنية بدقة على الشموع الحقيقية للـ OTC
            df['EMA_Fast'] = df['close'].ewm(span=5).mean()
            df['EMA_Slow'] = df['close'].ewm(span=12).mean()
            
            delta = df['close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            df['RSI'] = 100 - (100 / (1 + rs))
            df['RSI'] = df['RSI'].fillna(50)
            
            last = df.iloc[-1]
            signal = "CALL" if last['EMA_Fast'] >= last['EMA_Slow'] and last['RSI'] >= 45 else "PUT"
            
            now_tr = get_turkey_time()
            entry_time = now_tr + timedelta(minutes=2)
            entry_price = last['close']
            
            chart_img = generate_chart(df, f"EUR/USD OTC (Live) | Signal: {signal}")
            
            msg = (
                f"⚠️ تنبيه صفقة حقيقية (منصة Pocket Option OTC)\n"
                f"──────────────────\n"
                f"💱 الزوج: EUR/USD OTC\n"
                f"📈 الاتجاه: {signal} ({'صعود 🟢' if signal=='CALL' else 'هبوط 🔴'})\n"
                f"⏳ وقت الدخول (تركيا): {entry_time.strftime('%H:%M:%S')}\n"
                f"⏱ المدة: 5 دقائق\n"
                f"📍 السعر الفعلي من المنصة: {entry_price:.5f}\n"
                f"──────────────────"
            )
            send_telegram_photo(chart_img, caption=msg)
            
            # الانتظار حتى انتهاء الصفقة (7 دقائق)
            await asyncio.sleep(420)
            
            # جلب الشموع مجدداً لتقييم النتيجة الحقيقية بدقة تامة من المنصة
            candles_end = await client.get_candles(Asset.EURUSD_OTC, period=300)
            df_end = pd.DataFrame(candles_end)
            end_price = df_end['close'].iloc[-1]
            
            is_win = (end_price >= entry_price) if signal == "CALL" else (end_price <= entry_price)
            res = "✅ رابحة (WIN)" if is_win else "❌ خاسرة (LOSS)"
            
            res_img = generate_chart(df_end, f"Result: {res} | Close: {end_price:.5f}")
            summary = (
                f"🏁 نتيجة الصفقة الحقيقية (OTC)\n"
                f"النتيجة: {res}\n"
                f"📍 سعر الفتح: {entry_price:.5f}\n"
                f"📍 سعر الإغلاق: {end_price:.5f}"
            )
            send_telegram_photo(res_img, caption=summary)
            
            await client.disconnect()
            await asyncio.sleep(60)
            
        except Exception as e:
            logging.error(f"Pocket Bot Error: {e}")
            await asyncio.sleep(30)

if __name__ == "__main__":
    asyncio.run(run_pocket_bot())
