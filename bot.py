import os
import time
import threading
import requests
from datetime import datetime
from flask import Flask

TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_IT = os.getenv("CHANNEL_IT")
CHANNEL_EN = os.getenv("CHANNEL_EN")

app = Flask(__name__)

def send_message(chat_id: str, text: str):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    r = requests.post(
        url,
        data={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        timeout=20,
    )
    return r.status_code, r.text

def bot_main():
    # Messaggio di test all'avvio
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    if TOKEN and CHANNEL_IT:
        code, body = send_message(CHANNEL_IT, f"🤖 Bot online su Render\nUltimo check: {now}")
        print("Telegram response:", code, body)
    else:
        print("Missing env vars: BOT_TOKEN or CHANNEL_IT")

    # Loop “vivo” (qui poi metteremo le regole e i post)
    while True:
        time.sleep(3600)

@app.get("/health")
def health():
    return "ok", 200

if __name__ == "__main__":
    # Avvia il bot in un thread separato
    t = threading.Thread(target=bot_main, daemon=True)
    t.start()

    # Avvia il web server richiesto da Render
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
