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

def tg_send(chat_id: str, text: str):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    r = requests.post(
        url,
        data={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        },
        timeout=25,
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
    windows = cfg.get("windows", []) or []
    if not windows:
        return True

    for w in windows:
        sh, sm = map(int, w["start"].split(":"))
        eh, em = map(int, w["end"].split(":"))
        start = dtime(sh, sm)
        end = dtime(eh, em)
        if start <= dt.time() <= end:
            return True
    return False

def short(s: str, max_len: int) -> str:
    s = " ".join((s or "").strip().split())
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"

def make_hash(title: str, link: str) -> str:
    raw = (title + "|" + link).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()

def passes_filters(cfg, title: str, summary: str) -> bool:
    text = (title + " " + summary).lower()
    for kw in (cfg.get("filters", {}).get("blocked_keywords", []) or []):
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
    # Fetch robusto: molti siti bloccano user-agent vuoti dei parser
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; offerte_bonus_italia/1.0)"
    }
    r = requests.get(url, headers=headers, timeout=25)
    if r.status_code >= 400:
        raise RuntimeError(f"RSS fetch failed {r.status_code} for {url}")
    return feedparser.parse(r.content)

def bot_loop():
    cfg = load_config()
    tz = cfg.get("timezone", "Europe/Rome")

    poll = int(cfg.get("posting", {}).get("poll_interval_seconds", 900))
    cooldown = int(cfg.get("posting", {}).get("cooldown_seconds", 60))
    max_posts = int(cfg.get("max_posts_per_day", 2))

    channel_it = (cfg.get("channels", {}).get("it") or "").strip()

    if not TOKEN:
        print("BOT ERROR: Missing BOT_TOKEN env var")
        while True:
            time.sleep(60)

    if not channel_it.startswith("@"):
        print("BOT ERROR: channels.it must be like @nomecanale")
        while True:
            time.sleep(60)

    state = load_state()

    def reset_daily(dt: datetime):
        day = dt.strftime("%Y-%m-%d")
        if state.get("day") != day:
            state["day"] = day
            state["posts_today"] = 0
            state["recent_hashes"] = (state.get("recent_hashes", []) or [])[-500:]
            save_state(state)

    sources = cfg.get("sources", []) or []

    while True:
        dt = now_local(tz)
        reset_daily(dt)

        try:
            print(f"[{dt.strftime('%Y-%m-%d %H:%M:%S')}] tick posts_today={state['posts_today']}")

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

                # SOLO IT (per ora)
                if s.get("region") != "IT" or s.get("lang") != "it":
                    continue

                url = s["url"]
                name = s.get("name", "Fonte")
                rank = int(s.get("rank", 1000))

                try:
                    parsed = fetch_rss(url)
                except Exception as ex:
                    print(f"RSS ERROR: {name} -> {repr(ex)}")
                    continue

                entries = parsed.entries or []
                for e in entries[:30]:
                    title = short(e.get("title", ""), int(cfg.get("filters", {}).get("max_title_len", 110)))
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
            print(f"[{dt.strftime('%Y-%m-%d %H:%M:%S')}] candidates={len(candidates)}")

            for _, source_name, title, link, h in candidates:
                if state["posts_today"] >= max_posts:
                    break

                msg = format_post_it(source_name, title, link)
                code, body = tg_send(channel_it, msg)

                if code == 200:
                    state["posts_today"] += 1
                    state["recent_hashes"].append(h)
                    state["recent_hashes"] = state["recent_hashes"][-1500:]
                    save_state(state)
                    print(f"POSTED OK: {title}")
                    time.sleep(cooldown)
                else:
                    print(f"TELEGRAM ERROR: code={code} body={body[:300]}")
                    time.sleep(120)

            time.sleep(poll)

        except Exception as ex:
            print("BOT ERROR:", repr(ex))
            time.sleep(60)

# ---------------- Main ----------------
if __name__ == "__main__":
    t = threading.Thread(target=bot_loop, daemon=True)
    t.start()

    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
