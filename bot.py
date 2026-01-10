import os
import time
import requests
from datetime import datetime

TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_IT = os.getenv("CHANNEL_IT")
CHANNEL_EN = os.getenv("CHANNEL_EN")  # anche se non usato ora

def send_message(chat_id, text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    r = requests.post(url, data={
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }, timeout=20)
    return r.status_code, r.text

if __name__ == "__main__":
    # Messaggio di test una sola volta all'avvio
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    if TOKEN and CHANNEL_IT:
        code, body = send_message(CHANNEL_IT, f"🤖 Bot online su Render\nUltimo check: {now}")
        print("Telegram response:", code, body)
    else:
        print("Missing env vars: BOT_TOKEN or CHANNEL_IT")

    # Mantieni vivo il processo (non posta nulla, dorme e basta)
    while True:
        time.sleep(3600)
