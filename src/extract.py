import os
import json
import httpx
import feedparser
import requests
from bs4 import BeautifulSoup
from readability import Document
from urllib.parse import urljoin, urlparse

UA = "Mozilla/5.0 (compatible; AECStartupAgent/1.0)"


def fetch_url(url: str) -> str:
    with httpx.Client(headers={"User-Agent": UA}, timeout=25, follow_redirects=True) as c:
        r = c.get(url)
        r.raise_for_status()
        return r.text


def readable_text(html: str) -> str:
    doc = Document(html)
    content_html = doc.summary()
    soup = BeautifulSoup(content_html, "html.parser")
    text = soup.get_text("\n")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines)[:12000]


def harvest_links(page_url: str, html: str, limit: int = 30) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    links = []

    for a in soup.select("a[href]"):
        href = a.get("href")
        if not href:
            continue

        abs_url = urljoin(page_url, href)
        p = urlparse(abs_url)

        if p.scheme in ("http", "https"):
            links.append(abs_url)

        if len(links) >= limit:
            break

    seen = set()
    out = []
    for u in links:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def collect_items(rss_feeds: list[str], seed_pages: list[str], max_items: int = 40) -> list[dict]:
    items = []

    for feed_url in rss_feeds:
        try:
            feed = feedparser.parse(feed_url)
            for e in feed.entries[:12]:
                items.append({
                    "title": getattr(e, "title", "")[:200],
                    "url": getattr(e, "link", ""),
                    "source": feed_url,
                    "snippet": (getattr(e, "summary", "") or "")[:1500],
                })
        except Exception:
            continue

    for page in seed_pages:
        try:
            html = fetch_url(page)
            for link in harvest_links(page, html, limit=25):
                items.append({
                    "title": "",
                    "url": link,
                    "source": page,
                    "snippet": "",
                })
        except Exception:
            continue

    seen = set()
    out = []
    for it in items:
        u = it.get("url")
        if u and u not in seen:
            seen.add(u)
            out.append(it)
        if len(out) >= max_items:
            break

    return out


# ----------------------------
# Tier 1: relevance filter
# ----------------------------

def is_signal_candidate(item: dict) -> bool:
    title = (item.get("title") or "")[:200]
    snippet = (item.get("snippet") or "")[:900]

    if not title.strip() and len(snippet.strip()) < 40:
        return True

    prompt = (
        "Decide if this content is relevant to sustainable GREENFIELD RESIDENTIAL development.\n\n"

        "YES only if it clearly involves:\n"
        "- residential housing developments or masterplanned communities\n"
        "- subdivision-scale infrastructure (roads, drainage, utilities, landscape)\n"
        "- sustainable materials or products used in housing developments\n\n"

        "NO if it is primarily:\n"
        "- large infrastructure (rail, airports, metros, stadiums)\n"
        "- commercial or retail developments\n"
        "- generic construction news\n"
        "- corporate or financial news\n\n"

        f"TITLE:\n{title}\n\n"
        f"SNIPPET:\n{snippet}\n\n"
        "Answer YES or NO."
    )

    try:
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": "Answer YES or NO only."},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 3,
                "temperature": 0,
            },
            timeout=30,
        )

        if r.status_code != 200:
            return True

        raw = r.json()["choices"][0]["message"]["content"].strip().upper()
        return raw.startswith("YES")

    except Exception as e:
        print("Tier-1 error:", e)
        return True


# ----------------------------
# Tier 2: extraction (UPDATED)
# ----------------------------

def extract_company_record(item: dict) -> dict | None:
    url = item.get("url")
    if not url:
        return None

    text = ""
    try:
        html = fetch_url(url)
        text = readable_text(html)
    except Exception:
        text = (item.get("snippet") or "")[:4000]

    if not text.strip():
        return None

    prompt = (
        "Extract structured information from the content below.\n\n"

        "Only return results if the content describes ONE of the following:\n"
        "1. A residential development project (housing, subdivision, masterplanned community)\n"
        "2. A physical product or material used in residential development (e.g. paving, concrete, drainage systems)\n\n"

        "Ignore and return null if the content is:\n"
        "- marketing or lifestyle-focused\n"
        "- general real estate advice\n"
        "- investment, retail, or commercial content\n"
        "- generic architecture or design discussion\n\n"

        "Focus ONLY on:\n"
        "- physical development features\n"
        "- sustainability measures (water, energy, materials, biodiversity)\n"
        "- infrastructure systems\n\n"

        "Return JSON:\n"
        "{\n"
        '  "name": string,\n'
        '  "type": "Project"|"Product"|"Company",\n'
        '  "summary": string,\n'
        '  "tags": string[],\n'
        '  "location": string,\n'
        '  "confidence": number\n'
        "}\n\n"

        "Return null if the content does not meet the criteria.\n\n"
        f"CONTENT:\n{text[:6000]}"
    )

    try:
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": "Return JSON only."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.2,
            },
            timeout=30,
        )

        if r.status_code != 200:
            print("Extraction API error:", r.text)
            return None

        raw = r.json()["choices"][0]["message"]["content"].strip()

    except Exception as e:
        print("Extraction failed:", e)
        return None

    try:
        data = json.loads(raw)
    except Exception:
        return None

    if not data or not isinstance(data, dict):
        return None

    if not data.get("name"):
        return None

    if not isinstance(data.get("tags"), list):
        data["tags"] = []

    if not isinstance(data.get("confidence"), (int, float)):
        data["confidence"] = 0.5

    return data
