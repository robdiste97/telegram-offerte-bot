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

# ----- Web server per Render (keep-alive) -----
app = Flask(__name__)

@app.get("/")
def home():
    return "ok", 200

@app.get("/health")
def health():
    return "ok", 200

# -------- Telegram --------
TOKEN = os.getenv("BOT_TOKEN")

def tg_send(chat_id: str, text: str):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    r = requests.post(
        url,
        data={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=25,
    )
    return r.status_code, r.text

# ----- Config / Stato -----
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

def short(s: str, max_len: int) -> str:
    s = " ".join((s or "").strip().split())
    return s if len(s) <= max_len else (s[: max_len - 1] + "…")

def make_hash(title: str, link: str) -> str:
    raw = (title + "|" + link).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()

def passes_filters(cfg, title: str, summary: str) -> bool:
    text = (title + " " + summary).lower()
    for kw in cfg.get("filters", {}).get("blocked_keywords", []) or []:
        if kw.lower() in text:
            return False
    return True

def format_post_it(source_name: str, title: str, link: str) -> str:
    return (
        "💰 <b>OFFERTA</b>\n\n"
        f"🧩 <b>{title}</b>\n"
        f"🔗 {link}\n\n"
        f"📌 Fonte: {source_name}"
    )

def fetch_rss(url: str):
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; OfferteBot/1.0)"
    }
    r = requests.get(url, headers=headers, timeout=25)
    if r.status_code >= 400:
        raise RuntimeError(f"RSS fetch failed {r.status_code} for {url}")
    return feedparser.parse(r.content)

def bot_loop():
    cfg = load_config()
    tz = cfg.get("timezone", "Europe/Rome")
    poll = int(cfg.get("posting", {}).get("poll_interval_seconds", 1800))
    cooldown = int(cfg.get("posting", {}).get("cooldown_seconds", 1800))
    max_posts = int(cfg.get("max_posts_per_day", 3))

    channel_it = (cfg.get("channels", {}).get("it") or "").strip()

    if not TOKEN:
        print("BOT ERROR: Missing BOT_TOKEN env var")
        return
    if not channel_it.startswith("@"):
        print("BOT ERROR: channels.it must be like @nomecanale")
        return

    state = load_state()
    sources = cfg.get("sources", []) or []

    def reset_daily(dt: datetime):
        day = dt.strftime("%Y-%m-%d")
        if state.get("day") != day:
            state["day"] = day
            state["posts_today"] = 0
            state["recent_hashes"] = (state.get("recent_hashes", []) or [])[-500:]
            save_state(state)

    while True:
        dt = now_local(tz)
        reset_daily(dt)

        try:
            if not in_windows(cfg, dt):
                time.sleep(60)
                continue

            if state["posts_today"] >= max_posts:
                time.sleep(300)
                continue

            candidates = []
            for s in sources:
                if s.get("type") != "rss":
                    continue
                if s.get("region") != "IT" or s.get("lang") != "it":
                    continue
                name = s.get("name", "Fonte")
                rank = int(s.get("rank", 1000))
                url = s["url"]
                try:
                    parsed = fetch_rss(url)
                except Exception:
                    continue
                for e in (parsed.entries or [])[:30]:
                    title = short(e.get("title", ""), int(cfg.get("filters", {}).get("max_title_len", 120)))
                    link = (e.get("link") or "").strip()
                    summary = (e.get("summary") or e.get("description") or "").strip()
                    if not title or not link:
                        continue
                    if not passes_filters(cfg, title, summary):
                        continue
                    h = make_hash(title, link)
                    if h in state["recent_hashes"]:
                        continue
                    candidates.append((rank, name, title, link, h))

            candidates.sort(key=lambda x: x[0])

            for _, source_name, title, link, h in candidates:
                if state["posts_today"] >= max_posts:
                    break
                msg = format_post_it(source_name, title, link)
                code, _ = tg_send(channel_it, msg)
                if code == 200:
                    state["posts_today"] += 1
                    state["recent_hashes"].append(h)
                    state["recent_hashes"] = state["recent_hashes"][-1500:]
                    save_state(state)
                    # solo un post per ciclo
                    break

            time.sleep(poll)
        except Exception:
            # se qualcosa va storto, aspetta un minuto e riprova
            time.sleep(60)

# ---- Keep-alive thread per Render Free ----
def keep_alive():
    url = os.getenv("RENDER_EXTERNAL_URL") or os.getenv("SELF_URL", "")
    if not url:
        return
    while True:
        try:
            requests.get(url + "/health", timeout=10)
        except Exception:
            pass
        time.sleep(300)  # ping ogni 5 minuti

if __name__ == "__main__":
    # avvia bot in background
    threading.Thread(target=bot_loop, daemon=True).start()
    # avvia keep-alive in background (se URL presente)
    threading.Thread(target=keep_alive, daemon=True).start()
    # avvia web server
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
