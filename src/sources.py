import os
import hashlib
from urllib.parse import urlparse, urljoin, quote_plus

import requests
from bs4 import BeautifulSoup

import csv
from io import StringIO


# ============================================================
# GOOGLE NEWS RSS (Structured Recall Layer)
# ============================================================

def google_news_rss_url(query: str, hl: str = "en-AU", gl: str = "AU", ceid: str = "AU:en") -> str:
    q = quote_plus(query)
    return f"https://news.google.com/rss/search?q={q}&hl={hl}&gl={gl}&ceid={ceid}"


def google_news_feeds() -> list[str]:
    hl = os.getenv("GNEWS_HL", "en-AU")
    gl = os.getenv("GNEWS_GL", "AU")
    ceid = os.getenv("GNEWS_CEID", "AU:en")

    queries = [
        # --- Greenfield / residential developments ---
        '(greenfield development OR "masterplanned community") (sustainable OR "net zero" OR renewable) (Australia OR UK OR US OR Europe)',
        '(residential development) (sustainability OR "net zero community") (Australia OR UK OR US)',
        '(housing development) (sustainable OR "low carbon" OR "climate resilient")',

        # --- Subdivision / planning ---
        '(subdivision design OR land development) (sustainable OR drainage OR water)',
        '(urban planning) (greenfield OR "new community") (sustainability OR resilience)',

        # --- Water / WSUD ---
        '(stormwater management) (residential OR subdivision) (sustainable OR innovation)',
        '("water sensitive urban design" OR WSUD) (project OR development)',
        '(flood mitigation) (housing development OR community)',

        # --- Materials / products ---
        '(permeable paving OR porous concrete) (development OR project)',
        '(green concrete OR low carbon concrete) (residential OR housing)',
        '(sustainable construction materials) (housing OR residential)',

        # --- Lower priority: energy (context only) ---
        '(residential development) (solar OR battery OR microgrid OR EV infrastructure)',
    ]

    return [google_news_rss_url(q, hl=hl, gl=gl, ceid=ceid) for q in queries]


# ============================================================
# RSS FEEDS (Precision Layer)
# ============================================================

RSS_FEEDS = [
    # --- Australian development / planning ---
    "https://www.theurbandeveloper.com/feed",
    "https://www.urban.com.au/news.rss",
    "https://www.propertycouncil.com.au/Web/Content/News/News_RSS.aspx",

    # --- Architecture / housing projects ---
    "https://www.archdaily.com/rss/tag/housing",
    "https://www.architectureanddesign.com.au/news/rss",

    # --- Sustainability applied ---
    "https://www.greenbiz.com/rss.xml",
    "https://www.climatechangenews.com/feed/",

    # --- Water / WSUD / landscape ---
    "https://watersensitivecities.org.au/news/feed/",
    "https://www.stormwater.com/rss/all",
    "https://www.landscapeaustralia.com/feed/",

    # --- Materials / products ---
    "https://www.constructiondive.com/topic/materials/rss/",
    "https://www.pbctoday.co.uk/news/category/materials/feed/",
    "https://www.dezeen.com/tag/sustainable-materials/feed/",

    # --- General construction / infrastructure ---
    "https://www.globalconstructionreview.com/feed/",
    "https://www.infrastructuremagazine.com.au/feed/",

    # --- Google News recall layer ---
    *google_news_feeds(),
]


# ============================================================
# SEED WEBPAGES
# ============================================================

SEED_WEBPAGES = [
    "https://www.theurbandeveloper.com/",
    "https://www.archdaily.com/",
]


# ============================================================
# WATCH WEBPAGES (High Precision Tracking)
# ============================================================

WATCH_WEBPAGES = [
    # --- Australian developers ---
    "https://www.stockland.com.au/residential/news",
    "https://www.mirvac.com/residential/news",
    "https://www.lendlease.com/au/media-centre/",
    "https://www.frasersproperty.com.au/news",

    # --- Sustainability case studies ---
    "https://www.gbca.org.au/case-studies/",
    "https://www.worldgbc.org/case-study-library",

    # --- Water-focused org ---
    "https://watersensitivecities.org.au/resources/",
]


# ============================================================
# WATCH HELPERS
# ============================================================

def fetch_url(url: str) -> str:
    headers = {"User-Agent": "Mozilla/5.0"}
    r = requests.get(url, headers=headers, timeout=20)
    r.raise_for_status()
    return r.text


def extract_main_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "noscript"]):
        tag.decompose()
    return soup.get_text(separator=" ", strip=True)[:8000]


def compute_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def harvest_same_host_links(html: str, base_url: str, limit: int = 80) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    base_host = urlparse(base_url).netloc
    links, seen = [], set()

    for a in soup.find_all("a", href=True):
        full_url = urljoin(base_url, a["href"])
        parsed = urlparse(full_url)

        if parsed.scheme not in ("http", "https"):
            continue
        if parsed.netloc != base_host:
            continue
        if full_url in seen:
            continue

        seen.add(full_url)
        links.append(full_url)

        if len(links) >= limit:
            break

    return links


def collect_watch_items(watch_pages: list[str], state: dict):
    new_items = []
    state.setdefault("watch_pages", {})
    seen_cap = int(os.getenv("WATCH_SEEN_URLS_CAP", "400"))

    for url in watch_pages:
        try:
            html = fetch_url(url)
            text = extract_main_text(html)
            new_hash = compute_hash(text)

            page_state = state["watch_pages"].get(url, {})
            old_hash = page_state.get("hash")
            seen_list = page_state.get("seen_urls") or []
            seen_set = set(seen_list)

            if new_hash != old_hash:
                if url not in seen_set:
                    new_items.append({"url": url, "source": url, "kind": "watch_page"})

                links = harvest_same_host_links(html, url)
                fresh_links = [l for l in links if l not in seen_set and l != url]

                for link in fresh_links:
                    new_items.append({"url": link, "source": url, "kind": "watch_link"})

                state["watch_pages"][url] = {
                    "hash": new_hash,
                    "seen_urls": (seen_list + [url] + fresh_links)[-seen_cap:],
                }

        except Exception as e:
            print(f"Error checking watch page {url}: {e}")

    return new_items, state


# ============================================================
# GOOGLE SHEET INGESTION
# ============================================================

GOOGLE_SHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vQbs9IJWh5JSxJXmzpr8AhPS8Pwxq3tHdOYWZ_I1qBulyZbGnF1X3JWdj8GmmTi-gAHbHEWy-dqskKV/pub?output=csv"


def fetch_published_sheet(csv_url: str):
    r = requests.get(csv_url, timeout=20)
    r.raise_for_status()
    return list(csv.DictReader(StringIO(r.text)))


def collect_sheet_items(rows, state):
    new_items = []
    state.setdefault("sheet_seen", [])
    seen_set = set(state["sheet_seen"])
    seen_cap = int(os.getenv("SHEET_SEEN_CAP", "1000"))

    for row in rows:
        if not isinstance(row, dict):
            continue

        name = (row.get("startup_name") or row.get("name") or row.get("Startup") or "").strip()
        website = (row.get("website") or row.get("url") or row.get("Website") or "").strip()

        key = website or name

        if not key or key in seen_set:
            continue

        new_items.append({
            "url": website or None,
            "source": "google_sheet",
            "kind": "startup_seed",
            "title": name,
            "metadata": row,
        })

        seen_set.add(key)

    state["sheet_seen"] = list(seen_set)[-seen_cap:]
    return new_items, state


# ============================================================
# MAIN COLLECTION
# ============================================================

def collect_all(state: dict):
    all_items = []

    watch_items, state = collect_watch_items(WATCH_WEBPAGES, state)
    all_items.extend(watch_items)

    try:
        rows = fetch_published_sheet(GOOGLE_SHEET_CSV_URL)
        sheet_items, state = collect_sheet_items(rows, state)
        all_items.extend(sheet_items)
    except Exception as e:
        print(f"Sheet error: {e}")

    return all_items, state
