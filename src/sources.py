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
        '(greenfield development OR "masterplanned community") (sustainable OR "net zero")',
        '(residential development) (sustainability OR "low carbon")',
        '(stormwater management) (residential OR subdivision)',
        '(permeable paving OR porous concrete) (development OR project)',
    ]

    return [google_news_rss_url(q, hl=hl, gl=gl, ceid=ceid) for q in queries]


# ============================================================
# NEW: TARGETED SEARCH (NON-NEWS DISCOVERY)
# ============================================================

SEARCH_QUERIES = [
    "greenfield residential development sustainability case study",
    "masterplanned community water sensitive urban design",
    "permeable paving residential subdivision product",
    "low carbon concrete housing development",
    "stormwater management residential subdivision design",
]


def google_search(query: str, num_results: int = 5):
    headers = {"User-Agent": "Mozilla/5.0"}
    url = f"https://www.google.com/search?q={quote_plus(query)}"

    results = []

    try:
        r = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")

        for a in soup.select("a"):
            href = a.get("href")
            if not href:
                continue

            if "/url?q=" in href:
                link = href.split("/url?q=")[1].split("&")[0]
                if "google.com" in link:
                    continue
                results.append(link)

            if len(results) >= num_results:
                break

    except Exception as e:
        print(f"Search error ({query}): {e}")

    return results


# ============================================================
# RSS FEEDS
# ============================================================

RSS_FEEDS = [
    "https://www.theurbandeveloper.com/feed",
    "https://www.archdaily.com/rss/tag/housing",
    "https://watersensitivecities.org.au/news/feed/",
    "https://www.dezeen.com/tag/sustainable-materials/feed/",
    *google_news_feeds(),
]


# ============================================================
# SEED WEBPAGES (WITH PLANTED LINKS)
# ============================================================

SEED_WEBPAGES = [
    "https://www.theurbandeveloper.com/",
    "https://www.archdaily.com/",

    # Planted high-signal test links
    "https://en.wikipedia.org/wiki/Currumbin_Ecovillage",
    "https://en.wikipedia.org/wiki/BedZED",
    "https://en.wikipedia.org/wiki/Sharjah_Sustainable_City",
]


# ============================================================
# WATCH WEBPAGES
# ============================================================

WATCH_WEBPAGES = [
    "https://www.stockland.com.au/residential/news",
    "https://www.mirvac.com/residential/news",
    "https://www.gbca.org.au/case-studies/",
    "https://watersensitivecities.org.au/resources/",
]


# ============================================================
# HELPERS
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

    for url in watch_pages:
        try:
            html = fetch_url(url)
            links = harvest_same_host_links(html, url)

            for link in links:
                new_items.append({"url": link, "source": url})

        except Exception as e:
            print(f"Watch error {url}: {e}")

    return new_items, state


# ============================================================
# MAIN COLLECTION (UPDATED - NO SHEET)
# ============================================================

def collect_all(state: dict):
    all_items = []

    # Watch pages
    watch_items, state = collect_watch_items(WATCH_WEBPAGES, state)
    all_items.extend(watch_items)

    # Targeted search ingestion
    for q in SEARCH_QUERIES:
        links = google_search(q, num_results=5)
        for link in links:
            all_items.append({
                "url": link,
                "source": "search",
                "query": q,
            })

    return all_items, state
