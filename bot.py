import os
import time
import json
import hashlib
import threading
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

import requests
import feedparser
import yaml
from flask import Flask

# ---------------- Render keep-alive (porta richiesta) ----------------
app = Flask(__name__)

@app.get("/")
def home():
    return "ok", 200

@app.get("/health")
def health():
    return "ok", 200

# ---------------- Telegram ----------------
TOKEN = os.getenv("BOT_TOKEN")  # obbligatoria
TELEGRAM_API = f"https://api.telegram.org/bot{TOKEN}"

def tg_send(chat_id: str, text: str):
    r = requests.post(
        f"{TELEGRAM_API}/sendMessage",
        data={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        },
        timeout=20,
    )
    return r.status_code, r.text

# ---------------- Config / State ----------------
CONFIG_PATH = "config.yaml"
STATE_PATH = "state.json"

def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def load_state():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"day": "", "posts_today": 0, "recent_hashes": []}

def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def now_local(tz_name: str):
    return datetime.now(ZoneInfo(tz_name))

def in_windows(cfg, dt: datetime) -> bool:
    for w in cfg.get("windows", []):
        sh, sm = map(int, w["start"].split(":"))
        eh, em = map(int, w["end"].split(":"))
        start = dtime(sh, sm)
        end = dtime(eh, em)
        if start <= dt.time() <= end:
            return True
    return False

def normalize_text(s: str) -> str:
    return " ".join((s or "").strip().split())

def short(s: str, max_len: int) -> str:
    s = normalize_text(s)
    return s if len(s) <= max_len else (s[: max_len - 1] + "…")

def item_hash(title: str, link: str) -> str:
    raw = (title + "|" + link).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()

def passes_filters(cfg, title: str, summary: str) -> bool:
    text = (title + " " + summary).lower()
    for kw in cfg["filters"].get("blocked_keywords", []):
        if kw.lower() in text:
            return False

    required_any = cfg["filters"].get("required_keywords_any", []) or []
    if required_any:
        return any(k.lower() in text for k in required_any)

    return True

def format_post_it(source_name: str, title: str, link: str) -> str:
    return (
        "💰 <b>OFFERTA</b>\n\n"
        f"🧩 <b>{title}</b>\n"
        f"🔗 {link}\n\n"
        f"📌 Fonte: {source_name}"
    )

def fetch_rss(url: str):
    return feedparser.parse(url)

def bot_loop():
    cfg = load_config()
    tz = cfg.get("timezone", "Europe/Rome")
    state = load_state()

    channel_it = cfg["channels"].get("it", "").strip()
    if not TOKEN or not channel_it:
        # Non crashare: resta vivo e riprova più tardi (così puoi sistemare env/config)
        while True:
            time.sleep(60)

    def reset_daily(dt: datetime):
        day = dt.strftime("%Y-%m-%d")
        if state.get("day") != day:
            state["day"] = day
            state["posts_today"] = 0
            # conserva un po’ di storia per evitare repost al reboot
            state["recent_hashes"] = (state.get("recent_hashes", []) or [])[-300:]
            save_state(state)

    poll = int(cfg.get("posting", {}).get("poll_interval_seconds", 900))
    cooldown = int(cfg.get("posting", {}).get("cooldown_seconds", 60))
    max_posts = int(cfg.get("max_posts_per_day", 2))

    while True:
        try:
            dt = now_local(tz)
            reset_daily(dt)

            # fuori finestra = non pubblicare
            if not in_windows(cfg, dt):
                time.sleep(60)
                continue

            # raggiunto limite giornaliero
            if state["posts_today"] >= max_posts:
                time.sleep(300)
                continue

            sources = cfg.get("sources", [])
            candidates = []

            for s in sources:
                if s.get("type") != "rss":
                    continue
                parsed = fetch_rss(s["url"])
                for e in (parsed.entries or [])[:25]:
                    title = short(e.get("title", ""), cfg["filters"].get("max_title_len", 110))
                    link = (e.get("link") or "").strip()
                    summary = short(e.get("summary", "") or e.get("description", ""),
                                    cfg["filters"].get("max_summary_len", 240))

                    if not title or not link:
                        continue

                    h = item_hash(title, link)
                    if h in state["recent_hashes"]:
                        continue

                    if not passes_filters(cfg, title, summary):
                        continue

                    # SOLO IT (come da config)
                    if s.get("region") != "IT" or s.get("lang") != "it":
                        continue

                    candidates.append((s.get("rank", 1000), s.get("name", "Fonte"), title, link, h))

            # scegli i migliori (rank più basso prima)
            candidates.sort(key=lambda x: x[0])

            for _, source_name, title, link, h in candidates:
                if state["posts_today"] >= max_posts:
                    break

                msg = format_post_it(source_name, title, link)
                code, body = tg_send(channel_it, msg)

                if code == 200:
                    state["posts_today"] += 1
                    state["recent_hashes"].append(h)
                    state["recent_hashes"] = state["recent_hashes"][-800:]
                    save_state(state)
                    time.sleep(cooldown)
                else:
                    # se Telegram rifiuta, non andare a raffica
                    time.sleep(120)

            time.sleep(poll)

        except Exception:
            time.sleep(60)

# ---------------- Main ----------------
if __name__ == "__main__":
    # Avvia bot in background
    t = threading.Thread(target=bot_loop, daemon=True)
    t.start()

    # Avvia web server richiesto da Render
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
