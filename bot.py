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

# =========================================================
# FLASK APP
# =========================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "BOT ONLINE", 200

@app.route("/health")
def health():
    return "OK", 200


# =========================================================
# CONFIG
# =========================================================

CONFIG_PATH = "config.yaml"
STATE_PATH = "state.json"

BOT_TOKEN = os.getenv("BOT_TOKEN")

def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def load_state():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {
            "day": "",
            "posts_today": 0,
            "recent_hashes": []
        }

def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# =========================================================
# HELPERS
# =========================================================

def now_local(tz_name):
    return datetime.now(ZoneInfo(tz_name))

def in_windows(cfg, dt):
    windows = cfg.get("windows", [])

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

def short(text, max_len):
    text = " ".join((text or "").strip().split())

    if len(text) <= max_len:
        return text

    return text[:max_len - 1] + "…"

def make_hash(title, link):
    raw = (title + "|" + link).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()

def passes_filters(cfg, title, summary):

    text = (title + " " + summary).lower()

    blocked = cfg.get("filters", {}).get("blocked_keywords", [])

    for kw in blocked:
        if kw.lower() in text:
            return False

    return True

def format_post(source_name, title, link):

    return (
        "💰 <b>OFFERTA</b>\n\n"
        f"🧩 <b>{title}</b>\n"
        f"🔗 {link}\n\n"
        f"📌 Fonte: {source_name}"
    )


# =========================================================
# TELEGRAM
# =========================================================

def tg_send(chat_id, text):

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    r = requests.post(
        url,
        data={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        },
        timeout=30
    )

    return r.status_code, r.text


# =========================================================
# RSS
# =========================================================

def fetch_rss(url):

    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    r = requests.get(
        url,
        headers=headers,
        timeout=30
    )

    print(f"RSS {url} -> {r.status_code}")

    if r.status_code >= 400:
        raise RuntimeError(f"RSS ERROR {r.status_code}")

    return feedparser.parse(r.content)


# =========================================================
# KEEP ALIVE
# =========================================================

def keep_alive():

    while True:

        try:

            url = os.getenv("RENDER_EXTERNAL_URL")

            if url:
                requests.get(url + "/health", timeout=20)
                print("KEEP ALIVE PING")

        except Exception as ex:
            print("KEEP ALIVE ERROR", repr(ex))

        time.sleep(240)


# =========================================================
# BOT LOOP
# =========================================================

def bot_loop():

    print("BOT LOOP STARTED")

    cfg = load_config()

    tz = cfg.get("timezone", "Europe/Rome")

    poll = int(cfg.get("posting", {}).get("poll_interval_seconds", 1800))

    max_posts = int(cfg.get("max_posts_per_day", 3))

    channel_it = cfg.get("channels", {}).get("it", "").strip()

    state = load_state()

    sources = cfg.get("sources", [])

    while True:

        try:

            dt = now_local(tz)

            print(f"TICK {dt}")

            # reset daily
            current_day = dt.strftime("%Y-%m-%d")

            if state.get("day") != current_day:

                state["day"] = current_day
                state["posts_today"] = 0

                save_state(state)

                print("RESET DAILY")

            if not in_windows(cfg, dt):

                print("OUTSIDE WINDOW")

                time.sleep(60)
                continue

            if state["posts_today"] >= max_posts:

                print("MAX POSTS REACHED")

                time.sleep(300)
                continue

            candidates = []

            for s in sources:

                try:

                    parsed = fetch_rss(s["url"])

                    for e in parsed.entries[:20]:

                        title = short(
                            e.get("title", ""),
                            120
                        )

                        link = (e.get("link") or "").strip()

                        summary = (
                            e.get("summary")
                            or e.get("description")
                            or ""
                        )

                        if not title or not link:
                            continue

                        if not passes_filters(cfg, title, summary):
                            continue

                        h = make_hash(title, link)

                        if h in state["recent_hashes"]:
                            continue

                        candidates.append((
                            s.get("rank", 999),
                            s.get("name", "Fonte"),
                            title,
                            link,
                            h
                        ))

                except Exception as ex:

                    print("SOURCE ERROR", s["name"], repr(ex))

            candidates.sort(key=lambda x: x[0])

            print("CANDIDATES", len(candidates))

            if candidates:

                rank, source_name, title, link, h = candidates[0]

                msg = format_post(
                    source_name,
                    title,
                    link
                )

                code, body = tg_send(channel_it, msg)

                print("TELEGRAM", code)

                if code == 200:

                    state["posts_today"] += 1

                    state["recent_hashes"].append(h)

                    state["recent_hashes"] = state["recent_hashes"][-1000:]

                    save_state(state)

                    print("POSTED", title)

            time.sleep(poll)

        except Exception as ex:

            print("BOT LOOP ERROR", repr(ex))

            time.sleep(60)


# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":

    threading.Thread(
        target=bot_loop,
        daemon=True
    ).start()

    threading.Thread(
        target=keep_alive,
        daemon=True
    ).start()

    port = int(os.getenv("PORT", "10000"))

    app.run(
        host="0.0.0.0",
        port=port
    )
