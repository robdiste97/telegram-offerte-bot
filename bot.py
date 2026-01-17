import os
import time
import json
import hashlib
import threading
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo

import requests
import feedparser
import yaml
from flask import Flask


# ----------------------------------------------------------------------------
# Telegram deals bot
#
# This script implements a Telegram bot that reads offers from multiple RSS
# sources, filters them according to keywords, and posts them to a Telegram
# channel. It runs as a Flask web service solely to satisfy Render's free tier
# requirement for an open port. The actual bot runs in a background thread.
#
# Configuration is loaded from a YAML file (config.yaml). The state
# (posts per day and hashes of recent posts) is persisted in a JSON file
# (state.json) so the bot won't repost the same offer or exceed the daily
# posting limit across restarts.
#
# The bot uses environment variable BOT_TOKEN for the Telegram bot token.
# The target channel is specified in config.yaml under channels.it. The
# English channel is disabled by default.
# ----------------------------------------------------------------------------

# Create Flask app for Render to detect an open port
app = Flask(__name__)


@app.get("/")
def home():
    """Return OK for root requests."""
    return "ok", 200


@app.get("/health")
def health():
    """Return OK for health checks."""
    return "ok", 200


def load_config(config_path: str = "config.yaml") -> dict:
    """Load the configuration from a YAML file."""
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_state(state_path: str = "state.json") -> dict:
    """Load or initialize the state from a JSON file."""
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"day": "", "posts_today": 0, "recent_hashes": []}


def save_state(state: dict, state_path: str = "state.json") -> None:
    """Persist the state to a JSON file."""
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def current_local_time(timezone: str) -> datetime:
    """Return the current local datetime in the specified timezone."""
    return datetime.now(ZoneInfo(timezone))


def in_posting_window(cfg: dict, dt: datetime) -> bool:
    """Check if the current time is within any allowed posting window."""
    windows = cfg.get("windows", [])
    if not windows:
        return True
    for w in windows:
        start_h, start_m = map(int, w["start"].split(":"))
        end_h, end_m = map(int, w["end"].split(":"))
        start = dt_time(start_h, start_m)
        end = dt_time(end_h, end_m)
        if start <= dt.time() <= end:
            return True
    return False


def short_text(s: str, max_len: int) -> str:
    """Return a shortened version of s limited to max_len characters."""
    s = " ".join((s or "").strip().split())
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


def compute_hash(title: str, link: str) -> str:
    """Compute a stable hash from the title and link."""
    raw = f"{title}|{link}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def passes_filters(cfg: dict, title: str, summary: str) -> bool:
    """Return True if the offer passes the required and blocked keyword filters."""
    text = f"{title} {summary}".lower()
    required_keywords = cfg.get("filters", {}).get("required_keywords_any", []) or []
    if required_keywords:
        if not any(keyword.lower() in text for keyword in required_keywords):
            return False
    blocked_keywords = cfg.get("filters", {}).get("blocked_keywords", []) or []
    if any(keyword.lower() in text for keyword in blocked_keywords):
        return False
    return True


def format_post(source_name: str, title: str, link: str) -> str:
    """Format a Telegram message for an offer."""
    return (
        "💰 <b>OFFERTA</b>\n\n"
        f"🧩 <b>{title}</b>\n"
        f"🔗 {link}\n\n"
        f"📌 Fonte: {source_name}"
    )


def fetch_rss(url: str) -> feedparser.FeedParserDict:
    """Fetch an RSS feed using a requests User-Agent to avoid bot blocking."""
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; OfferteBonusBot/1.0)"
    }
    resp = requests.get(url, headers=headers, timeout=25)
    if resp.status_code >= 400:
        raise RuntimeError(f"RSS fetch failed {resp.status_code} for {url}")
    return feedparser.parse(resp.content)


def telegram_send_message(chat_id: str, text: str) -> tuple[int, str]:
    """Send a message via Telegram and return the status code and response."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    response = requests.post(
        url,
        data={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=25,
    )
    return response.status_code, response.text


def run_bot():
    """Main bot loop running in a background thread."""
    cfg = load_config()
    state = load_state()
    tz = cfg.get("timezone", "Europe/Rome")
    poll_interval = int(cfg.get("posting", {}).get("poll_interval_seconds", 900))
    cooldown_seconds = int(cfg.get("posting", {}).get("cooldown_seconds", 3600))
    max_posts_per_day = int(cfg.get("max_posts_per_day", 2))
    channel_it = cfg.get("channels", {}).get("it", "").strip()

    if not BOT_TOKEN:
        print("[BOT] Error: BOT_TOKEN is not set.")
        return
    if not channel_it.startswith("@"): 
        print("[BOT] Error: channels.it must start with '@'. Check your config.yaml.")
        return

    def reset_daily(now: datetime):
        day = now.strftime("%Y-%m-%d")
        if state.get("day") != day:
            state["day"] = day
            state["posts_today"] = 0
            # Keep only the last 500 hashes to avoid memory bloat
            state["recent_hashes"] = state.get("recent_hashes", [])[-500:]
            save_state(state)

    while True:
        now = current_local_time(tz)
        reset_daily(now)
        try:
            print(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] tick posts_today={state['posts_today']}")
            # Skip posting outside of permitted windows
            if not in_posting_window(cfg, now):
                time.sleep(60)
                continue
            # Respect the daily post limit
            if state["posts_today"] >= max_posts_per_day:
                time.sleep(300)
                continue

            candidates = []
            for src in cfg.get("sources", []):
                if src.get("type") != "rss":
                    continue
                # Only handle Italian sources for now
                if src.get("region") != "IT" or src.get("lang") != "it":
                    continue
                try:
                    feed = fetch_rss(src["url"])
                except Exception as e:
                    print(f"[BOT] RSS error for {src['name']}: {e}")
                    continue
                for entry in feed.entries[:25]:
                    title = short_text(entry.get("title", ""), cfg.get("filters", {}).get("max_title_len", 120))
                    link = (entry.get("link") or "").strip()
                    summary = (entry.get("summary") or entry.get("description") or "").strip()
                    if not title or not link:
                        continue
                    if not passes_filters(cfg, title, summary):
                        continue
                    h = compute_hash(title, link)
                    if h in state["recent_hashes"]:
                        continue
                    candidates.append((src.get("rank", 999), src["name"], title, link, h))
            # Sort by rank to prioritize better sources
            candidates.sort(key=lambda x: x[0])
            print(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] candidates={len(candidates)}")
            # Post only one offer per poll cycle
            for _, source_name, title, link, h in candidates:
                if state["posts_today"] >= max_posts_per_day:
                    break
                message = format_post(source_name, title, link)
                code, body = telegram_send_message(channel_it, message)
                if code == 200:
                    state["posts_today"] += 1
                    state["recent_hashes"].append(h)
                    state["recent_hashes"] = state["recent_hashes"][-1500:]
                    save_state(state)
                    print(f"[BOT] Posted: {title}")
                    # Stop after one post per cycle to respect cooldown
                    break
                else:
                    print(f"[BOT] Telegram error: {code} {body[:300]}")
                    # If Telegram returns an error, sleep a bit
                    time.sleep(120)
            # Sleep before the next poll
            time.sleep(poll_interval)
        except Exception as e:
            print(f"[BOT] Unexpected error: {e}")
            time.sleep(60)


# Read bot token from environment
BOT_TOKEN = os.getenv("BOT_TOKEN")


if __name__ == "__main__":
    # Start the bot loop in a separate thread
    thread = threading.Thread(target=run_bot, daemon=True)
    thread.start()
    # Run the Flask app to keep Render instance alive
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
