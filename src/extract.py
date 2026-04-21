import os
import json
import httpx
import feedparser
from bs4 import BeautifulSoup
from readability import Document
from urllib.parse import urljoin, urlparse

from openai import OpenAI

UA = "Mozilla/5.0 (compatible; AECStartupAgent/1.0)"
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))


def fetch_url(url: str) -> str:
    with httpx.Client(headers={"User-Agent": UA}, timeout=25, follow_redirects=True) as c:
        r = c.get(url)
        r.raise_for_status()
        return r.text


def readable_text(html: str) -> str:
    """
    Convert full HTML page into main-article text using Readability.
    """
    doc = Document(html)
    content_html = doc.summary()
    soup = BeautifulSoup(content_html, "html.parser")
    text = soup.get_text("\n")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines)[:12000]  # cap to keep costs bounded


def harvest_links(page_url: str, html: str, limit: int = 30) -> list[str]:
    """
    Simple link harvesting from a seed page.
    (Used for pages like 'top contech startups', accelerators, etc.)
    """
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

    # De-dupe preserving order
    seen = set()
    out = []
    for u in links:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def collect_items(rss_feeds: list[str], seed_pages: list[str], max_items: int = 40) -> list[dict]:
    items = []

    # RSS ingestion
    for feed_url in rss_feeds:
        try:
            feed = feedparser.parse(feed_url)
            for e in feed.entries[:12]:
                items.append({
                    "title": getattr(e, "title", "")[:200],
                    "url": getattr(e, "link", ""),
                    "source": feed_url,
                    # NOTE: keep key name "snippet" (your pipeline uses this)
                    "snippet": (getattr(e, "summary", "") or "")[:1500],
                })
        except Exception:
            continue

    # Seed page ingestion (optional)
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

    # De-dupe by URL, keep first max_items
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
# Tier 1: cheap classifier
# ----------------------------

def is_signal_candidate(item: dict) -> bool:
    title = (item.get("title") or "")[:200]
    snippet = (item.get("snippet") or "")[:900]

    if not title.strip() and len(snippet.strip()) < 40:
        return True

    prompt = (
        "You are screening content for a sustainable residential development intelligence tracker.\n"
        "Decide if this content is worth deeper analysis.\n\n"
        "YES if it relates to:\n"
        "- residential or greenfield developments\n"
        "- sustainable infrastructure (water, drainage, landscape)\n"
        "- construction materials (low carbon, permeable paving, etc.)\n"
        "- applied sustainability in housing or subdivision design\n\n"
        "NO if it is:\n"
        "- generic ESG or climate news\n"
        "- policy or politics\n"
        "- unrelated industries\n\n"
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
            return True  # fail open

        raw = r.json()["choices"][0]["message"]["content"].strip().upper()
        return raw.startswith("YES")

    except Exception as e:
        print("Tier-1 error:", e)
        return True


# ----------------------------
# Tier 2: full extraction
# ----------------------------

def extract_company_record(item: dict) -> dict | None:
    """
    Given a feed item or webpage link, try to extract a startup/company record.
    Return None if content isn't about a startup/company.
    """
    url = item.get("url")
    if not url:
        return None

    # Fetch page text (fallback to snippet)
    text = ""
    try:
        html = fetch_url(url)
        text = readable_text(html)
    except Exception:
        text = (item.get("snippet") or "")[:4000]

    if not text.strip():
        return None

    # Prompt payload (stringified to keep this simple)
    payload = {
        "niche": "Startups focussed on the AEC and construction tech industry",
        "source_url": url,
        "content": text,
    }

    resp = client.chat.completions.create(
        model=os.getenv("TIER2_MODEL", "gpt-4o-mini"),
        messages=[
            {
                "role": "system",
                "content": (
                    "You extract startup/company information from noisy content for an AEC/Construction Tech tracker.\n"
                    "If the content is NOT about a startup/company (e.g. generic industry news, policy, a big contractor not a product company), return null.\n"
                    "If it IS a company, return a JSON object matching the schema below.\n"
                    "Be conservative: if uncertain, return null.\n"
                    "Return ONLY valid JSON (either null or an object). No markdown, no commentary.\n\n"
                    "Schema:\n"
                    "{\n"
                    '  "name": string,\n'
                    '  "website": string,\n'
                    '  "summary": string,\n'
                    '  "tags": string[],\n'
                    '  "niche_fit": "High"|"Medium"|"Low",\n'
                    '  "signals": string[],\n'
                    '  "hq": string,\n'
                    '  "stage": string,\n'
                    '  "confidence": number\n'
                    "}\n"
                ),
            },
            {"role": "user", "content": str(payload)},
        ],
        temperature=0.2,
    )

    raw = (resp.choices[0].message.content or "").strip()
    if not raw:
        return None

    # Parse JSON safely
    try:
        data = json.loads(raw)
    except Exception:
        return None

    if data is None:
        return None

    # Basic sanity checks
    if not isinstance(data, dict):
        return None
    if not data.get("name"):
        return None

    # Normalise fields
    if not isinstance(data.get("website"), str):
        data["website"] = ""
    if not isinstance(data.get("tags"), list):
        data["tags"] = []
    if not isinstance(data.get("signals"), list):
        data["signals"] = []
    if data.get("niche_fit") not in ("High", "Medium", "Low"):
        data["niche_fit"] = "Medium"
    if not isinstance(data.get("confidence"), (int, float)):
        data["confidence"] = 0.5

    return data
