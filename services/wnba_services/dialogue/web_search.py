"""Public web search for the projection dialogue.

DuckDuckGo's lite HTML endpoint needs no API key and no account, which is what a private
single-owner console can operate indefinitely without a billing dependency. Results are
parsed down to (title, url, snippet) triples and nothing else leaves the page.

Record-only in spirit: search results are shown to the model and stored in the dialogue
transcript; they are never written to the market archive, where only structured feeds belong.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

import httpx

LITE_ENDPOINT = "https://lite.duckduckgo.com/lite/"

# The lite endpoint is plain table markup: result links carry class "result-link", snippets
# class "result-snippet". Anything that stops matching means the page shape changed, and the
# honest answer is zero results rather than a pile of half-parsed fragments.
# Attribute order and quote style vary across responses (href may precede class, and both
# single and double quotes appear), so the anchor tag is matched on its class and the URL is
# then read out of its attributes separately.
_LINK_RE = re.compile(
    r"<a\b(?P<attrs>[^>]*\bclass=[\"']result-link[\"'][^>]*)>(?P<title>.*?)</a>",
    re.DOTALL,
)
_HREF_RE = re.compile(r"href=[\"'](?P<url>[^\"']+)[\"']")
_SNIPPET_RE = re.compile(
    r"<td\b[^>]*\bclass=[\"']result-snippet[\"'][^>]*>(?P<snippet>.*?)</td>",
    re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str

    def to_payload(self) -> dict[str, str]:
        return {"title": self.title, "url": self.url, "snippet": self.snippet}


def _clean(fragment: str) -> str:
    return " ".join(html.unescape(_TAG_RE.sub("", fragment)).split())


def parse_results(page: str, *, limit: int = 5) -> list[SearchResult]:
    """Extract result triples from a lite-endpoint page. Empty input parses to zero results."""
    links = []
    for match in _LINK_RE.finditer(page):
        href = _HREF_RE.search(match.group("attrs"))
        if href is not None:
            links.append((href.group("url"), match.group("title")))
    snippets = _SNIPPET_RE.findall(page)
    results: list[SearchResult] = []
    for index, (url, title) in enumerate(links[:limit]):
        snippet = _clean(snippets[index]) if index < len(snippets) else ""
        cleaned_url = html.unescape(url).strip()
        # DuckDuckGo wraps outbound links in a redirect that carries the real address in the
        # `uddg` parameter; unwrap it so the transcript records where the answer actually lives.
        redirect = re.search(r"[?&]uddg=([^&]+)", cleaned_url)
        if redirect:
            from urllib.parse import unquote

            cleaned_url = unquote(redirect.group(1))
        results.append(SearchResult(title=_clean(title), url=cleaned_url, snippet=snippet))
    return results


def search_web(
    query: str,
    *,
    limit: int = 5,
    timeout_seconds: float = 20.0,
    transport: httpx.BaseTransport | None = None,
) -> list[SearchResult]:
    """Search the public web. Raises on transport failure -- the caller reports the tool as
    unavailable for the turn rather than letting the model pretend it searched."""
    query = query.strip()
    if not query:
        return []
    with httpx.Client(
        timeout=timeout_seconds,
        transport=transport,
        headers={"User-Agent": "courtside-edge-dialogue/1.0"},
        follow_redirects=True,
    ) as client:
        response = client.post(LITE_ENDPOINT, data={"q": query})
        response.raise_for_status()
        return parse_results(response.text, limit=limit)
