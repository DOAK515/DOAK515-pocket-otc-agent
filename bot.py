import requests

TELEGRAM_BOT_TOKEN = "8341287362:AAF0h06PMtcP5O2Y-sF34OffcN_zeLbIKNo"
TELEGRAM_CHAT_ID = "-1003151787212"

url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
payload = {
    "chat_id": TELEGRAM_CHAT_ID, 
    "text": "تجربة اتصال بوت أبو خالد", 
    "parse_mode": "HTML"
}

response = requests.post(url, json=payload)
print("Telegram API Response Code:", response.status_code)
print("Telegram API Response Text:", response.text)
