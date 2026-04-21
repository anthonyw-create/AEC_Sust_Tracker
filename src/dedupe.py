import tldextract


def normalize_domain(url: str) -> str:
    """
    Extract a clean registrable domain from a URL.
    Example: https://www.example.co.uk/path -> example.co.uk
    """
    if not url:
        return ""
    ext = tldextract.extract(url)
    if not ext.domain or not ext.suffix:
        return ""
    return f"{ext.domain}.{ext.suffix}".lower()


def make_dedupe_key(record: dict) -> str:
    """
    Primary dedupe key is the company's website domain.
    Fallback is a normalized name key if no website is present.
    """
    domain = normalize_domain(record.get("website", ""))
    if domain:
        return domain

    name = (record.get("name") or "").strip().lower()
    if name:
        return f"name:{name}"

    return ""
