import json
import time
import httpx
from datetime import datetime

NOTION_VERSION = "2022-06-28"


def clean_option_name(s: str) -> str:
    """
    Notion multi-select option names have constraints.
    - Commas are not allowed.
    - Keep them short and clean.
    """
    if not s:
        return ""
    s = str(s).strip()
    s = s.replace(",", " -")      # remove commas
    s = " ".join(s.split())       # collapse whitespace/newlines
    return s[:50]                 # keep option names short


class NotionDB:
    def __init__(self, token: str, database_id: str):
        self.database_id = database_id
        self.client = httpx.Client(
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            },
            timeout=30,
        )

    def _raise_with_details(self, r: httpx.Response, context: str, payload: dict | None = None):
        """
        Notion often returns a helpful JSON error body on 4xx/5xx.
        Print it so we can diagnose schema/property issues quickly.
        """
        if r.status_code < 400:
            return

        print(f"\n=== NOTION ERROR ({context}) ===")
        print("Status:", r.status_code)

        # Notion returns JSON error bodies; keep a safe fallback
        try:
            err = r.json()
            print("Response JSON:", json.dumps(err, indent=2)[:4000])
        except Exception:
            print("Response text:", (r.text or "")[:4000])

        if payload is not None:
            try:
                print("Request payload:", json.dumps(payload, indent=2)[:4000])
            except Exception:
                print("Request payload (raw):", str(payload)[:4000])

        r.raise_for_status()

    def _request_with_retry(self, method: str, url: str, payload: dict, context: str) -> httpx.Response:
        """
        Retry on transient Notion/edge failures (e.g. 502 Cloudflare), and rate limits.
        """
        last_exc = None
        for attempt in range(1, 6):  # 5 attempts
            try:
                r = self.client.request(method, url, json=payload)

                # Retry on transient errors
                if r.status_code in (429, 500, 502, 503, 504):
                    wait = min(2 ** attempt, 20)
                    print(
                        f"NOTION transient {r.status_code} on {context}, "
                        f"retrying in {wait}s (attempt {attempt}/5)"
                    )
                    time.sleep(wait)
                    continue

                # Raise (with details) on non-transient errors
                self._raise_with_details(r, context, payload)
                return r

            except Exception as e:
                last_exc = e
                wait = min(2 ** attempt, 20)
                print(
                    f"NOTION exception on {context}: {e}. "
                    f"Retrying in {wait}s (attempt {attempt}/5)"
                )
                time.sleep(wait)

        raise last_exc if last_exc else RuntimeError(f"Notion request failed: {context}")

    def _query_by_dedupe_key(self, dedupe_key: str):
        url = f"https://api.notion.com/v1/databases/{self.database_id}/query"
        payload = {
            "filter": {
                "property": "Dedupe Key",
                "rich_text": {"equals": dedupe_key},
            }
        }
        r = self._request_with_retry("POST", url, payload, "query_by_dedupe_key")
        results = r.json().get("results", [])
        return results[0] if results else None

    def _create_page(self, props: dict):
        url = "https://api.notion.com/v1/pages"
        payload = {"parent": {"database_id": self.database_id}, "properties": props}
        self._request_with_retry("POST", url, payload, "create_page")

    def _update_page(self, page_id: str, props: dict):
        url = f"https://api.notion.com/v1/pages/{page_id}"
        payload = {"properties": props}
        self._request_with_retry("PATCH", url, payload, "update_page")

    def _props(self, record: dict, item: dict, is_new: bool):
        today = datetime.utcnow().date().isoformat()

        # Sanitize multi-select option names (Notion disallows commas)
        tags = [
            {"name": clean_option_name(t)}
            for t in (record.get("tags") or [])[:12]
            if clean_option_name(t)
        ]
        signals = [
            {"name": clean_option_name(s)}
            for s in (record.get("signals") or [])[:12]
            if clean_option_name(s)
        ]

        stage_value = (record.get("stage") or "Unknown").strip() or "Unknown"
        niche_fit_value = (record.get("niche_fit") or "Medium").strip() or "Medium"

        props = {
            "Name": {"title": [{"text": {"content": (record.get("name") or "")[:200]}}]},
            "Website": {"url": record.get("website") or None},
            "Domain": {
                "rich_text": [{"text": {"content": (record.get("dedupe_key") or "")[:200]}}]
            },
            "Summary": {
                "rich_text": [{"text": {"content": (record.get("summary") or "")[:1900]}}]
            },
            "Tags": {"multi_select": tags},
            "Niche Fit": {"select": {"name": niche_fit_value[:50]}},
            "Signals": {"multi_select": signals},
            "HQ": {"rich_text": [{"text": {"content": (record.get("hq") or "")[:200]}}]},
            "Stage": {"select": {"name": stage_value[:50]}},
            "Source": {"rich_text": [{"text": {"content": (item.get("source") or "")[:200]}}]},
            "Source URL": {"url": item.get("url") or None},
            "Last Seen": {"date": {"start": today}},
            "Dedupe Key": {
                "rich_text": [{"text": {"content": (record.get("dedupe_key") or "")[:200]}}]
            },
        }

        if is_new:
            props["Status"] = {"select": {"name": "New"}}
            props["Date Found"] = {"date": {"start": today}}

        return props

    def upsert(self, record: dict, item: dict) -> str:
        key = record.get("dedupe_key")
        if not key:
            return "skipped"

        existing = self._query_by_dedupe_key(key)

        if existing:
            self._update_page(existing["id"], self._props(record, item, is_new=False))
            return "updated"

        self._create_page(self._props(record, item, is_new=True))
        return "created"
