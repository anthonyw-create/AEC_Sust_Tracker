import os
import httpx
from datetime import datetime


def _as_int(x) -> int:
    # main.py might pass counts, lists, or something else
    if isinstance(x, bool):
        return int(x)
    if isinstance(x, int):
        return x
    if isinstance(x, float):
        return int(x)
    if isinstance(x, (list, tuple, set)):
        return len(x)
    if x is None:
        return 0
    try:
        s = str(x).strip()
        return int(s)
    except Exception:
        return 0


def _as_records(x):
    # records might be None, a list of dicts, a dict, etc.
    if x is None:
        return []
    if isinstance(x, list):
        return [r for r in x if isinstance(r, dict)]
    if isinstance(x, dict):
        return [x]
    return []


def _safe_join(items, limit=3):
    if not items:
        return ""
    out = []
    for x in list(items)[:limit]:
        s = str(x).strip()
        if s:
            out.append(s)
    return ", ".join(out)


def _html_escape(s: str) -> str:
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def send_digest_email(
    created=0,
    updated=0,
    records=None,
    sendgrid_key: str | None = None,
    email_from: str | None = None,
    email_to: str | None = None,
    notion_db_url: str | None = None,
    **kwargs,
):
    """
    High-signal digest email (defensive against main.py passing odd types).
    """

    # Coerce values defensively
    created_i = _as_int(created)
    updated_i = _as_int(updated)
    records_list = _as_records(records)

    # Env fallback
    sendgrid_key = sendgrid_key or os.environ.get("SENDGRID_API_KEY")
    email_from = email_from or os.environ.get("EMAIL_FROM")
    email_to = email_to or os.environ.get("EMAIL_TO")
    notion_db_url = notion_db_url or os.environ.get("NOTION_DB_URL")

    if not sendgrid_key or not email_from or not email_to:
        raise Exception("Missing SENDGRID_API_KEY / EMAIL_FROM / EMAIL_TO (check GitHub Secrets)")

    if created_i == 0 and updated_i == 0:
        print("No changes — no email sent.")
        return

    subject = f"AEC Startup Digest — {created_i} New | {updated_i} Updated"

    # Rank records (if provided) by niche_fit + confidence
    def score(r):
        niche = (r.get("niche_fit") or "").strip().lower()
        niche_w = 3 if niche == "high" else 2 if niche == "medium" else 1
        try:
            conf = float(r.get("confidence") or 0)
        except Exception:
            conf = 0.0
        return niche_w * 100 + conf

    ranked = sorted(records_list, key=score, reverse=True)
    high_fit = [r for r in ranked if (r.get("niche_fit") or "").strip().lower() == "high"]
    medium_fit = [r for r in ranked if (r.get("niche_fit") or "").strip().lower() == "medium"]

    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    def render_company_li(r: dict) -> str:
        name = _html_escape(r.get("name") or "Unknown")
        website = (r.get("website") or "").strip()
        stage = _html_escape((r.get("stage") or "").strip())
        hq = _html_escape((r.get("hq") or "").strip())
        summary = _html_escape((r.get("summary") or "").strip())[:320]
        signals = _html_escape(_safe_join(r.get("signals") or [], limit=3))
        tags = _html_escape(_safe_join(r.get("tags") or [], limit=5))

        website_link = f'<a href="{_html_escape(website)}">{_html_escape(website)}</a>' if website else ""
        meta = " — ".join([p for p in [stage, hq] if p])

        parts = [f"<strong>{name}</strong>"]
        if meta:
            parts.append(f"<span style='color:#555'> ({meta})</span>")
        if website_link:
            parts.append(f"<div>{website_link}</div>")
        if summary:
            parts.append(f"<div>{summary}</div>")
        if signals:
            parts.append(f"<div><em>Signals:</em> {signals}</div>")
        if tags:
            parts.append(f"<div><em>Tags:</em> {tags}</div>")

        return "<li style='margin:10px 0;'>" + "".join(parts) + "</li>"

    def render_section(title: str, rows: list[dict], max_items: int) -> str:
        if not rows:
            return ""
        lis = "\n".join(render_company_li(r) for r in rows[:max_items])
        more = f"<p style='color:#666'>…and {len(rows) - max_items} more in Notion.</p>" if len(rows) > max_items else ""
        return f"<h3>{_html_escape(title)}</h3><ul>{lis}</ul>{more}"

    summary_lines = [
        f"<li><strong>{created_i}</strong> new companies captured</li>",
        f"<li><strong>{updated_i}</strong> companies updated</li>",
    ]
    if high_fit:
        summary_lines.append(f"<li><strong>{min(len(high_fit), 8)}</strong> high-fit highlights below</li>")
    summary_html = "<ul>" + "\n".join(summary_lines) + "</ul>"

    body = f"""
    <html>
    <body style="font-family: Arial, sans-serif;">
        <h2>AEC Startup Intelligence — Daily Digest</h2>
        <p style="color:#666"><strong>Run:</strong> {now}</p>
        {summary_html}
        {render_section("High Fit Highlights", high_fit, max_items=8)}
        {render_section("Medium Fit (Worth a scan)", medium_fit, max_items=6)}
        <h3>Updates</h3>
        <p>{updated_i} existing records updated. See Notion for full details.</p>
    """

    if notion_db_url:
        body += f"""
        <p><strong>Notion database:</strong>
        <a href="{_html_escape(notion_db_url)}">{_html_escape(notion_db_url)}</a></p>
        """

    body += """
        <hr>
        <p style="color:#888; font-size:12px;">
            You’re receiving this because you run the AEC Startup Tracker.
        </p>
    </body>
    </html>
    """

    payload = {
        "personalizations": [{"to": [{"email": email_to}], "subject": subject}],
        "from": {"email": email_from},
        "content": [{"type": "text/html", "value": body}],
    }

    r = httpx.post(
        "https://api.sendgrid.com/v3/mail/send",
        headers={"Authorization": f"Bearer {sendgrid_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )

    print("SENDGRID STATUS:", r.status_code)
    if r.status_code >= 400:
        print("SENDGRID ERROR BODY:", r.text)
        r.raise_for_status()
