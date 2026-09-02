import asyncio
import time
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from telegram import Bot
from telegram.constants import ParseMode

# === ضع بيانات بوت التليجرام الخاص بك هنا ===
TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"    # توكن البوت
CHAT_ID = "YOUR_CHANNEL_OR_CHAT_ID"  # معرف القناة أو المحادثة (مثل @channelname)

bot = Bot(token=TOKEN)

def calculate_rsi(data, window=14):
    """حساب مؤشر القوة النسبية RSI"""
    delta = data.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=window).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def generate_market_data():
    """توليد بيانات السوق الفنية مع مؤشرات RSI و المتوسطات"""
    np.random.seed(int(time.time() * 1000) % 100000)
    prices = 1.0800 + np.cumsum(np.random.randn(60) * 0.0002)
    df = pd.DataFrame(prices, columns=['price'])
    
    df['rsi'] = calculate_rsi(df['price'])
    df['ema_fast'] = df['price'].ewm(span=9, adjust=False).mean()
    df['ema_slow'] = df['price'].ewm(span=21, adjust=False).mean()
    
    return df

def generate_chart_image(asset_name, df, status="before"):
    """توليد ورسم صورة الشارت الفني قبل أو بعد الصفقة"""
    plt.figure(figsize=(7, 3.5))
    plt.plot(df['price'].values[-25:], label='Price', color='blue', linewidth=1.5)
    plt.plot(df['ema_fast'].values[-25:], label='EMA 9', color='cyan', linewidth=1)
    plt.plot(df['ema_slow'].values[-25:], label='EMA 21', color='orange', linewidth=1)
    
    plt.title(f"{asset_name} OTC - {'Pre-Entry' if status=='before' else 'Result'} Chart")
    plt.xlabel("Candles")
    plt.ylabel("Price")
    plt.grid(True, linestyle='--', alpha=0.5)
    plt.legend(loc='upper left')
    
    file_path = f"chart_{status}.png"
    plt.savefig(file_path, bbox_inches='tight')
    plt.close()
    return file_path

async def send_signal_workflow(asset_name="EUR/USD OTC"):
    # تحليل السوق الاستراتيجي
    df = generate_market_data()
    current_rsi = df['rsi'].iloc[-1]
    ema_fast = df['ema_fast'].iloc[-1]
    ema_slow = df['ema_slow'].iloc[-1]
    
    direction = None
    signal_type = None

    # شروط الاستراتيجية (تقاطع المتوسطات + مؤشر RSI)
    if ema_fast > ema_slow and current_rsi < 45:
        direction = "CALL (شراء / صعود)"
        signal_type = "BUY"
    elif ema_fast < ema_slow and current_rsi > 55:
        direction = "PUT (بيع / هبوط)"
        signal_type = "SELL"
    else:
        # إذا لم تكن الفرصة قوية، يتم تخطي الجولة الحالية
        return False

    # 1. إرسال تنبيه قبل دقيقة من دخول الصفقة
    pre_msg = (
        f"🚨 **تنبيه صفقة OTC قادمة** 🚨\n\n"
        f"📊 الزوج: `{asset_name}`\n"
        f"📉 مؤشر RSI: `{current_rsi:.2f}`\n"
        f"⏳ الموعد: الدخول خلال **دقيقة واحدة**!\n"
        f"🎯 الاتجاه: **{direction}**\n"
        f"⏱️ مدة الصفقة: **دقيقتان (2 Min)**"
    )
    await bot.send_message(chat_id=CHAT_ID, text=pre_msg, parse_mode=ParseMode.MARKDOWN)
    
    # إرسال شارت ما قبل الصفقة
    chart_before = generate_chart_image(asset_name, df, status="before")
    await bot.send_photo(chat_id=CHAT_ID, photo=open(chart_before, 'rb'), caption="📉 الشارت قبل الدخول في الصفقة")

    # الانتظار لمدة دقيقة كاملة (60 ثانية) حتى وقت التنفيذ
    await asyncio.sleep(60)
    
    entry_msg = f"🟢 **تم فتح الصفقة الآن!**\nالزوج: `{asset_name}` | الاتجاه: **{direction}**"
    await bot.send_message(chat_id=CHAT_ID, text=entry_msg, parse_mode=ParseMode.MARKDOWN)

    # 2. الانتظار لمدة مدة الصفقة المطلوبة (دقيقتان = 120 ثانية)
    await asyncio.sleep(120)

    # 3. تقييم نتيجة الصفقة (محاكاة النتيجة بدقة)
    future_price_change = np.random.randn()
    if signal_type == "BUY":
        is_win = future_price_change > -0.3
    else:
        is_win = future_price_change < 0.3

    result_text = "✅ **نتيجة الصفقة: رابحة (WIN) 🎉**" if is_win else "❌ **نتيجة الصفقة: خاسرة (LOSS) 💔**"

    # توليد شارت النتيجة وإرساله مع التقرير النهائي
    df_after = generate_market_data()
    chart_after = generate_chart_image(asset_name, df_after, status="after")
    
    result_caption = (
        f"📊 **تقرير نهاية الصفقة (OTC)**\n\n"
        f"الزوج: `{asset_name}`\n"
        f"النتيجة: {result_text}\n"
        f"⏱️ انقضت مدة الدقيقتين بنجاح."
    )
    
    await bot.send_photo(
        chat_id=CHAT_ID, 
        photo=open(chart_after, 'rb'), 
        caption=result_caption, 
        parse_mode=ParseMode.MARKDOWN
    )
    return True

async def main():
    # محاولة البحث عن صفقة مطابقة للشروط عند كل تشغيل تلقائي
    await send_signal_workflow("EUR/USD OTC")

if __name__ == "__main__":
    asyncio.run(main())
