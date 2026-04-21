import os
import re
import json
from pathlib import Path
from datetime import datetime
from dateutil import tz

from .sources import (
    RSS_FEEDS,
    SEED_WEBPAGES,
    WATCH_WEBPAGES,
    collect_watch_items,
)
from .extract import (
    collect_items,
    extract_company_record,
    is_signal_candidate,
)
from .dedupe import make_dedupe_key
from .notion_client import NotionDB
from .emailer import send_digest_email


# Force state.json to live at repo root (one level above /src)
STATE_PATH = Path(__file__).resolve().parents[1] / "state.json"


# ----------------------------
# URL gating (noise reduction)
# ----------------------------

URL_DENYLIST = [
    r"/privacy",
    r"/terms",
    r"/cookies",
    r"/contact",
    r"/about",
    r"/careers",
    r"/jobs",
    r"/login",
    r"/signin",
    r"/signup",
    r"/account",
    r"/newsletter",
    r"/subscribe",
    r"/tag/",
    r"/category/",
    r"\.pdf$",
]


def url_is_worth_processing(url: str) -> bool:
    u = (url or "").strip().lower()
    if not u:
        return False
    for pat in URL_DENYLIST:
        if re.search(pat, u):
            return False
    return True


def now_local():
    tz_name = os.getenv("AGENT_TZ", "Australia/Melbourne")
    return datetime.now(tz.gettz(tz_name))


def load_state():
    if STATE_PATH.exists():
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                data.setdefault("watch_pages", {})
                return data
        except Exception as e:
            print(f"Warning: failed to load {STATE_PATH}: {e}")
    return {"watch_pages": {}}


def save_state(state: dict):
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Warning: failed to save {STATE_PATH}: {e}")


def dedupe_by_url(items):
    seen = set()
    out = []
    for it in items:
        url = (it.get("url") or "").strip()
        if not url:
            continue
        if url in seen:
            continue
        seen.add(url)
        out.append(it)
    return out


def run():
    notion = NotionDB(
        token=os.environ["NOTION_TOKEN"],
        database_id=os.environ["NOTION_DATABASE_ID"],
    )

    # ============================================================
    # DEBUG: trace a keyword through the pipeline (e.g. "neara")
    # Set env var DEBUG_MATCH=neara
    # ============================================================
    debug_match = os.getenv("DEBUG_MATCH", "").strip().lower()

    # ----------------------------
    # 1) Watch pages (delta emission)
    # ----------------------------
    state = load_state()

    watch_items = []
    if WATCH_WEBPAGES:
        print(f"Checking {len(WATCH_WEBPAGES)} watch pages for changes...")
        watch_items, state = collect_watch_items(WATCH_WEBPAGES, state)
    else:
        print("No WATCH_WEBPAGES configured.")

    save_state(state)

    # ----------------------------
    # 2) Collect RSS + seed pages
    # ----------------------------
    rss_max = int(os.getenv("RSS_MAX_ITEMS", "200"))
    print(f"Collecting items from RSS + seed webpages (max_items={rss_max})...")
    base_items = collect_items(RSS_FEEDS, SEED_WEBPAGES, max_items=rss_max)

    merged_items = dedupe_by_url(watch_items + base_items)

    # ----------------------------
    # 3) URL gating
    # ----------------------------
    before = len(merged_items)
    merged_items = [it for it in merged_items if url_is_worth_processing(it.get("url"))]
    after = len(merged_items)
    print(f"URL gating: kept {after}/{before} items")

    # Optional global cap
    max_total = int(os.getenv("MAX_ITEMS_TOTAL", "300"))
    merged_items = merged_items[:max_total]
    print(f"Post-cap items: {len(merged_items)} (MAX_ITEMS_TOTAL={max_total})")

    # ----------------------------
    # 4) Tier-1 + Tier-2 processing with counters
    # ----------------------------
    created = []
    updated = []

    screened = 0
    tier1_yes = 0
    extracted = 0
    debug_hits = 0

    for item in merged_items:
        screened += 1
        url = (item.get("url") or "")
        title = (item.get("title") or "")
        snippet = (item.get("snippet") or "")

        # Debug trace: show any items matching keyword anywhere in url/title/snippet
        if debug_match:
            hay = f"{url}\n{title}\n{snippet}".lower()
            if debug_match in hay:
                debug_hits += 1
                print(f"DEBUG_MATCH HIT ({debug_hits}): url={url} | title={title[:140]}")

        print(f"Screening: {url}")

        if not is_signal_candidate(item):
            print("Tier-1: Not relevant — skipping")
            continue

        tier1_yes += 1
        print("Tier-1: Relevant — extracting")

        record = extract_company_record(item)
        if not record:
            continue

        extracted += 1

        dedupe_key = make_dedupe_key(record)
        record["dedupe_key"] = dedupe_key

        result = notion.upsert(record, item)

        if result == "created":
            created.append(record)
        elif result == "updated":
            updated.append(record)

    if debug_match:
        print(f"DEBUG_MATCH summary: '{debug_match}' hits seen in candidates: {debug_hits}")

    print(f"Screened: {screened} | Tier1 YES: {tier1_yes} | Extracted: {extracted}")
    print(f"Created: {len(created)} | Updated: {len(updated)}")

    # ----------------------------
    # 5) Email digest
    # ----------------------------
    if created or updated:
        send_digest_email(
            sendgrid_key=os.environ["SENDGRID_API_KEY"],
            email_from=os.environ["EMAIL_FROM"],
            email_to=os.environ["EMAIL_TO"],
            created=created,
            updated=updated,
            run_time=now_local(),
        )
    else:
        print("No changes — no email sent.")


if __name__ == "__main__":
    run()
