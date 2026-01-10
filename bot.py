import os
import requests
from datetime import datetime

TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_IT = os.getenv("CHANNEL_IT")

def send_message(chat_id, text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    requests.post(url, data={
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    })

if __name__ == "__main__":
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    send_message(CHANNEL_IT, f"🤖 Bot online\nUltimo check: {now}")
