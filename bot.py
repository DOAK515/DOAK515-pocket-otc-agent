import requests

TOKEN = "8341287362:AAF0h06PMtcP5O2Y-sF34OffcN_zeLbIKNo"
CHAT_ID = "-1003151787212"

url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
data = {
    "chat_id": CHAT_ID,
    "text": "🚀 أهلاً بك يا أبو خالد! تم تشغيل البوت بنجاح تام عبر GitHub Actions وكل شي تمام."
}

response = requests.post(url, data=data)
print("Response:", response.text)
