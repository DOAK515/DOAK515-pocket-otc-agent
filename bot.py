import requests

TOKEN = "8341287362:AAF0h06PMtcP5O2Y-sF34OffcN_zeLbIKNo"
CHAT_ID = "-1003151787212"

url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
data = {
    "chat_id": CHAT_ID,
    "text": "تجربة اتصال بوت أبو خالد"
}

print("جاري إرسال الطلب إلى تيليجرام...")
response = requests.post(url, data=data)
print("حالة الرد من تيليجرام:", response.status_code)
print("نص الرد من تيليجرام:", response.text)
