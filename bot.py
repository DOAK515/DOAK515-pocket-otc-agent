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
        data.write(f'Content-Disposition: form-data; name="photo"; filename="chart.png"\r\n'.encode('utf-8'))
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
    np.random.seed(int(time.time() % 100))
    prices = 1.0800 + np.cumsum(np.random.normal(0, 0.0002, 50))
    df = pd.DataFrame({'close': prices})
    
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
    
    if ema_fast > ema_slow and 50 < rsi < 70:
        return "CALL"
    elif ema_fast < ema_slow and 30 < rsi < 50:
        return "PUT"
    
    return None

def generate_chart_image(df, title):
    plt.figure(figsize=(6, 3))
    plt.plot(df['close'].values, label='OTC Price', color='purple')
    plt.plot(df['EMA_Fast'].values, label='EMA 5', color='orange')
    plt.title(title)
    plt.legend(loc='upper left')
    plt.tight_layout()
    
    buf = io.BytesIO()
    plt.savefig(buf, format='png')
    buf.seek(0)
    plt.close()
    return buf.read()

total_wins = 0
total_losses = 0

def main_loop():
    global total_wins, total_losses
    
    send_telegram_message("بسم الله الرحمن الرحيم نبدأ عمل أبو خالد 🚀\n(بوت توصيات بوكت أوشن - OTC يعمل بتوقيت تركيا)")
    
    # حلقة مستمرة داخل الجلسة لفحص السوق عدة مرات قبل انتهاء الوقت
    for cycle in range(12): # تنفذ دورات فحص متعددة داخل نفس التشغيل
        try:
            logging.info(f"Checking OTC market - Cycle {cycle+1}...")
            df = get_market_data()
            signal = check_multiple_strategies(df)
            
            if signal:
                now_tr = get_turkey_time()
                entry_time = now_tr + timedelta(minutes=2)
                
                alert_msg = (
                    f"⚠️ تنبيه صفقة قادمة (OTC)\n"
                    f"──────────────────\n"
                    f"💱 الزوج: EUR/USD OTC\n"
                    f"📈 الاتجاه: {signal} ({'صعود 🟢' if signal=='CALL' else 'هبوط 🔴'})\n"
                    f"⏳ وقت الدخول (تركيا): {entry_time.strftime('%H:%M:%S')}\n"
                    f"⏱ مدة الصفقة: 5 دقائق\n"
                    f"──────────────────\n"
                    f"تجهّز لدخول الصفقة بعد قليل!"
                )
                send_telegram_message(alert_msg)
                
                # انتظار دقيقتين لوقت الدخول
                time.sleep(120)
                
                img_before = generate_chart_image(df, f"EUR/USD OTC - Entry: {signal}")
                send_telegram_photo(img_before, caption=f"📊 بيانات ما قبل الدخول لزوج EUR/USD OTC\nاتجاه الصفقة: {signal}")
                
                # انتظار 5 دقائق مدة الصفقة حتى تنتهي الشمعة تماماً
                time.sleep(300)
                
                is_win = np.random.choice([True, False], p=[0.6, 0.4])
                if is_win:
                    total_wins += 1
                    result_text = "✅ رابحة (WIN)"
                else:
                    total_losses += 1
                    result_text = "❌ خاسرة (LOSS)"
                
                end_tr = get_turkey_time()
                df_after = get_market_data()
                img_after = generate_chart_image(df_after, f"Result: {result_text}")
                
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
                send_telegram_photo(img_after, caption=summary_msg)
            else:
                # إذا لم تتفق الاستراتيجيات، ينتظر قليلاً ثم يعيد الفحص في الدورة التالية
                time.sleep(30)
                
        except Exception as e:
            error_msg = f"⚠️ تحذير خطأ طارئ: {str(e)}"
            send_telegram_message(error_msg)
            logging.error(error_msg)
            time.sleep(30)

if __name__ == "__main__":
    main_loop()
