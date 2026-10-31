"""
scrape_ig.py
============
Fetches new @japanhabba Instagram posts and logs announcements to events.json.

Runs in GitHub Actions after monitor.py. Non-blocking — exits 0 on any failure.

Auth, tried in order:
  INSTAGRAM_SESSION_FILE : path to an instaloader session file (local runs)
  INSTAGRAM_SESSION_B64  : base64-encoded session file
  INSTAGRAM_USERNAME / INSTAGRAM_PASSWORD : password login, also used when a
    loaded session turns out to be logged out. On success the session is saved
    to INSTAGRAM_SESSION_FILE (if set) so later runs reuse it.
IG_MAX_NEW_POSTS : posts to scan per run (default 10; raise to catch up).
State: last logged shortcode is the most recent entry in events.json.

Runs locally via cron/local_events.sh. Instagram rate-limits and logs out
sessions used from cloud IPs, so this no longer runs in GitHub Actions.

To generate the secret locally (run in ticketing-jh-2027):
  base64 -i konfhub/scripts/instagram-session-vanishinggradient | tr -d '\\n' | pbcopy
Then add as INSTAGRAM_SESSION_B64 in GitHub Secrets for the status page repo.

ADR reference: ADR-059
"""

import base64
import json
import os
import pathlib
import sys
import tempfile
from datetime import datetime, timezone, timedelta

try:
    import instaloader
except ImportError:
    print("[scrape_ig] instaloader not installed — skipping")
    sys.exit(0)

TARGET_ACCOUNT = "japanhabba"
MAX_NEW_POSTS = int(os.environ.get("IG_MAX_NEW_POSTS", "10"))
SESSION_USER = os.environ.get("INSTAGRAM_USERNAME") or "vanishinggradient"

IST = timezone(timedelta(hours=5, minutes=30))
last_fetch_error = ""
REPO_ROOT = pathlib.Path(__file__).parent.parent
EVENTS_FILE = REPO_ROOT / "events.json"

# Keyword classifier — same logic as monitor-japanhabba-instagram.py in ticketing-jh-2027.
# Listed in priority order. First match wins per keyword set, all matches collected.
CLASSIFIERS = [
    (
        {"sold out", "cancelled", "postponed", "cancel"},
        "URGENT",
    ),
    (
        {"artist", "guest", "lineup", "announce", "performer", "music", "concert", "habba live"},
        "SPEAKER/ARTIST",
    ),
    (
        {"ticket", "early bird", "sale", "on sale", "register", "registration", "buy now"},
        "TICKETS",
    ),
    (
        {"schedule", "timing", "time slot", "day 1", "day 2", "timetable", "programme"},
        "SCHEDULE",
    ),
    (
        {"sponsor", "partner", "collab", "collaboration", "supported by", "powered by"},
        "SPONSOR/PARTNER",
    ),
    (
        {"exhibitor", "stall", "vendor", "merchandise", "merch"},
        "EXHIBITOR",
    ),
]


def log(msg: str):
    ts = datetime.now(IST).strftime("%H:%M:%S IST")
    print(f"[scrape_ig] [{ts}] {msg}")


def load_events() -> list:
    if EVENTS_FILE.exists():
        with open(EVENTS_FILE) as f:
            return json.load(f)
    return []


def save_events(events: list):
    with open(EVENTS_FILE, "w") as f:
        json.dump(events, f, indent=2)


def classify(caption: str) -> str | None:
    """Return the first matching category label, or None."""
    lower = caption.lower()
    for keywords, label in CLASSIFIERS:
        if any(kw in lower for kw in keywords):
            return label
    return None


def make_label(category: str, caption: str) -> str:
    """'SPEAKER/ARTIST: Introducing our fifth performer…' for the sheet's Events column."""
    first = next((line.strip() for line in caption.splitlines() if line.strip()), "")
    if len(first) > 50:
        first = first[:50].rstrip() + "…"
    return f"{category}: {first}" if first else category


def get_last_shortcode(events: list) -> str | None:
    if not events:
        return None
    # Events are sorted oldest-first; last entry is most recent.
    return events[-1].get("shortcode")


def _make_loader() -> instaloader.Instaloader:
    return instaloader.Instaloader(
        quiet=True,
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        post_metadata_txt_pattern="",
        max_connection_attempts=1,  # fail fast — no infinite retry loops in CI
    )


def load_session_file(path: str) -> instaloader.Instaloader | None:
    loader = _make_loader()
    try:
        loader.load_session_from_file(SESSION_USER, path)
        log("Session file loaded.")
        return loader
    except Exception as e:
        log(f"Session file load failed: {e}")
        return None


def load_session(session_b64: str) -> instaloader.Instaloader | None:
    try:
        session_bytes = base64.b64decode(session_b64)
    except Exception as e:
        log(f"Failed to decode INSTAGRAM_SESSION_B64: {e}")
        return None

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".session")
    tmp.write(session_bytes)
    tmp.flush()
    tmp.close()

    loader = _make_loader()
    try:
        # Username stored in the session — instaloader needs it to load.
        # The session file was saved as "vanishinggradient".
        loader.load_session_from_file("vanishinggradient", tmp.name)
        log("Session loaded.")
        return loader
    except Exception as e:
        log(f"Session load failed: {e}")
        return None


def login_with_credentials(username: str, password: str,
                           save_to: str = "") -> instaloader.Instaloader | None:
    """Fallback: password login when session is absent or stale."""
    if not username or not password:
        log("INSTAGRAM_USERNAME / INSTAGRAM_PASSWORD not set — cannot fall back")
        return None
    loader = _make_loader()
    try:
        loader.login(username, password)
        log(f"Logged in as {username} via password.")
        if save_to:
            loader.save_session_to_file(save_to)
            log("Session saved for next run.")
        return loader
    except Exception as e:
        log(f"Password login failed: {e}")
        return None


def fetch_new_posts(
    loader: instaloader.Instaloader, since_shortcode: str | None
) -> list[dict] | None:
    """New posts, newest first. None means the fetch failed (e.g. logged out)."""
    global last_fetch_error
    try:
        profile = instaloader.Profile.from_username(loader.context, TARGET_ACCOUNT)
    except Exception as e:
        log(f"Could not fetch @{TARGET_ACCOUNT}: {e}")
        last_fetch_error = str(e)
        return None

    new_posts = []
    try:
        for post in profile.get_posts():
            if since_shortcode and post.shortcode == since_shortcode:
                break
            new_posts.append({
                "shortcode": post.shortcode,
                "date_utc": post.date_utc,
                "caption": (post.caption or "")[:500],
            })
            if len(new_posts) >= MAX_NEW_POSTS:
                break
    except Exception as e:
        # All or nothing: a partial page would move the "last shortcode"
        # marker past posts we never saw.
        log(f"Could not list posts: {e}")
        last_fetch_error = str(e)
        return None

    return new_posts


def main():
    session_file = os.environ.get("INSTAGRAM_SESSION_FILE", "")
    session_b64 = os.environ.get("INSTAGRAM_SESSION_B64", "")
    username = os.environ.get("INSTAGRAM_USERNAME", "")
    password = os.environ.get("INSTAGRAM_PASSWORD", "")

    events = load_events()
    since = get_last_shortcode(events)
    log(f"Last logged shortcode: {since or 'none (first run)'}")

    loader = None
    if session_file and os.path.exists(session_file):
        loader = load_session_file(session_file)
    elif session_b64:
        loader = load_session(session_b64)
    else:
        log("No saved session — trying password login")

    used_password = False
    if loader is None:
        loader = login_with_credentials(username, password, session_file)
        used_password = True

    if loader is None:
        log("All auth methods failed — skipping")
        sys.exit(0)

    new_posts = fetch_new_posts(loader, since)
    if new_posts is None and not used_password and "login" in last_fetch_error.lower():
        # Session loaded fine but Instagram has logged it out; log in again.
        # Rate limits ("please wait") don't retry: another login makes them worse.
        log("Saved session rejected — retrying with password login")
        loader = login_with_credentials(username, password, session_file)
        new_posts = fetch_new_posts(loader, since) if loader else None
    if new_posts is None:
        log("Fetch failed — skipping")
        sys.exit(0)
    log(f"New posts since last check: {len(new_posts)}")

    # Dedup guard: never re-add a shortcode already in events.json.
    # Protects against placeholder shortcodes and re-runs on the same posts.
    existing_shortcodes = {ev.get("shortcode") for ev in events}

    # Posts come newest-first from Instagram. Reverse so we append oldest-first.
    added = 0
    for post in reversed(new_posts):
        if post["shortcode"] in existing_shortcodes:
            log(f"  Skip {post['shortcode']}: already logged")
            continue
        category = classify(post["caption"])
        if category is None:
            log(f"  Skip {post['shortcode']}: no classifier match")
            continue

        date_ist = post["date_utc"].replace(tzinfo=timezone.utc).astimezone(IST).date()
        entry = {
            "date": date_ist.isoformat(),
            "shortcode": post["shortcode"],
            "category": category,
            "label": make_label(category, post["caption"]),
            "caption_preview": post["caption"][:150].replace("\n", " "),
            "url": f"https://www.instagram.com/p/{post['shortcode']}/",
        }
        events.append(entry)
        log(f"  Logged [{category}] {post['shortcode']} ({date_ist})")
        added += 1

    if added > 0:
        save_events(events)
        log(f"events.json updated: {added} new entries, {len(events)} total")
    else:
        log("No new classified posts. events.json unchanged.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # Never crash the pipeline
        log(f"Unhandled error (non-fatal): {e}")
        sys.exit(0)
