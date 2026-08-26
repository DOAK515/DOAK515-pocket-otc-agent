import requests

TOKEN = "8341287362:AAF0hO6PMtcP5O2Y-sF34OffcN_zeLbIKNo"
CHAT_ID = "-1003151787212"

url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
data = {
    "chat_id": CHAT_ID,
    "text": "🚀 أهلاً بك يا أبو خالد! تم ربط البوت وتشغيله بنجاح تام وكل شيء يعمل بانتظام."
}

print("جاري إرسال الطلب إلى تيليجرام...")
response = requests.post(url, data=data)
print("حالة الرد من تيليجرام:", response.status_code)
print("نص الرد من تيليجرام:", response.text)
