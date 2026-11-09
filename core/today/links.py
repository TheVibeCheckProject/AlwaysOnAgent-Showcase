"""Today §2: links and page previews — one link however it is written; a page's title, site, image.

Moved into core/today/links.py on 2026-11-04 (was core/today_links.py, split out of
core/today.py §2 on 2026-11-03); core/today_links.py is now a re-export shim.
"""
import datetime
import html
import re
import urllib.parse


def _now() -> datetime.datetime:
    return datetime.datetime.now().astimezone()


def _parse(value: str) -> datetime.datetime | None:
    try:
        t = datetime.datetime.fromisoformat(str(value))
        return t if t.tzinfo else t.astimezone()
    except (TypeError, ValueError):
        return None


def link_key(url: str) -> str:
    """The same page under small differences (scheme, www, tracking parameters) is one link."""
    u = re.sub(r"^https?://(www\.)?", "", (url or "").strip().lower()).split("#")[0]
    return re.sub(r"[?&](utm_[a-z]+|ref|fbclid|gclid)=[^&]*", "", u).rstrip("/?&")


def _web_get(url: str) -> tuple[bytes, str]:
    from core.web_tools import _get
    return _get(url, check_host=True)             # public hosts only: a page can't point the agent at the LAN


def page_preview(html_text: str, url: str) -> dict[str, str]:
    """The page's own title, site name and preview image link (Open Graph, then <title>)."""
    def meta(*names):
        for n in names:
            for pat in (rf'<meta[^>]+(?:property|name)=["\']{n}["\'][^>]*content=["\']([^"\']+)',
                        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name)=["\']{n}["\']'):
                m = re.search(pat, html_text, re.IGNORECASE)
                if m:
                    return html.unescape(m.group(1)).strip()
        return ""
    title = meta("og:title", "twitter:title")
    if not title:
        m = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.IGNORECASE | re.DOTALL)
        title = html.unescape(" ".join(m.group(1).split())) if m else ""
    image = meta("og:image:secure_url", "og:image", "twitter:image")
    site = meta("og:site_name") or re.sub(r"^www\.", "", urllib.parse.urlparse(url).netloc)
    return {"title": title[:200], "site": site[:80], "image": urllib.parse.urljoin(url, image) if image else ""}
