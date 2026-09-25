"""Person identity keys, per the design: the normalized LinkedIn URL when
present (lowercase, no query, no trailing slash); otherwise lower(name) +
firm id as a provisional key, replaced when a profile is attached."""

from __future__ import annotations

from urllib.parse import urlparse


def normalize_linkedin(url: str) -> str:
    raw = url.strip().lower()
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    parsed = urlparse(raw)
    # hostname (not netloc): strips userinfo and port, so neither can poison
    # the identity key or reject a legitimate URL
    host = (parsed.hostname or "").rstrip(".").removeprefix("www.")
    path = parsed.path.rstrip("/")
    if (host != "linkedin.com" and not host.endswith(".linkedin.com")) or not path:
        raise ValueError(f"not a LinkedIn profile URL: {url!r}")
    return f"{host}{path}"


def identity_key(
    name: str, linkedin_url: str | None = None, firm_id: int | None = None
) -> str:
    if linkedin_url:
        return normalize_linkedin(linkedin_url)
    return f"{name.strip().lower()}|firm:{firm_id if firm_id is not None else 'none'}"
