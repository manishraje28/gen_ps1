"""Real web fetching for ``get_webpage`` (opt-in: ``WARRANT_REAL_WEB=1``).

Simulated pages in ``env.web.pages`` always win, so the demo scenarios behave
exactly as before. Anything else is fetched with a plain HTTP GET:
http/https only, no cookies, 10 s timeout, <= 2 MB, at most 3 redirects, and
every hop's host is resolved and refused if it points at a private, loopback,
link-local, metadata or otherwise non-public address. The URL is a gate sink
(``Sink.URL`` in ``warrant/policy.py``), so the gate checks its warrant before
this function ever runs.
"""
from __future__ import annotations

import ipaddress
import os
import re
import socket
from html.parser import HTMLParser
from typing import Annotated
from urllib.parse import urljoin, urlsplit

import httpx
from agentdojo.functions_runtime import Depends

from demo import env as sim
from demo.env import Web

TIMEOUT = 10.0
MAX_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 3
MAX_CHARS = 20_000
USER_AGENT = "WARRANT-demo/1.0 (+get_webpage)"
_TRANSPORT: httpx.BaseTransport | None = None  # tests inject httpx.MockTransport


def enabled() -> bool:
    return os.environ.get("WARRANT_REAL_WEB", "").strip().lower() in ("1", "true", "yes", "on")


def _resolve(host: str) -> list[str]:
    return [ai[4][0] for ai in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)]


def _blocked_ip(ip: str) -> bool:
    a = ipaddress.ip_address(ip.split("%")[0])
    if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped:
        a = a.ipv4_mapped
    return not a.is_global or a.is_private or a.is_loopback or a.is_link_local or a.is_multicast \
        or a.is_reserved or a.is_unspecified


def check_url(url: str) -> str | None:
    """Return an error string if ``url`` may not be fetched, else None."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return f"Refused: malformed URL {url!r}"
    if parts.scheme not in ("http", "https"):
        return f"Refused: only http/https URLs can be fetched (got {parts.scheme or 'no scheme'!r})"
    host = (parts.hostname or "").strip(".").lower()
    if not host:
        return f"Refused: URL has no host: {url}"
    if host == "localhost" or host.endswith(".localhost") or host in ("metadata.google.internal", "metadata"):
        return f"Refused: {host} is a local/metadata address"
    try:
        ips = [host] if _is_ip(host) else _resolve(host)
    except OSError as e:
        return f"Error: could not resolve {host}: {e}"
    if not ips:
        return f"Error: could not resolve {host}"
    for ip in ips:
        if _blocked_ip(ip):
            return f"Refused: {host} resolves to non-public address {ip}"
    return None


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


class _Text(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "header",
             "footer", "pre", "blockquote", "table", "ul", "ol", "hr", "title"}
    SKIP = {"script", "style", "noscript", "template", "svg", "head"}

    def __init__(self, base: str):
        super().__init__(convert_charrefs=True)
        self.base, self.out, self.skip, self.href = base, [], 0, None
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.out.append("\n")
        if tag == "a":
            href = dict(attrs).get("href")
            self.href = urljoin(self.base, href) if href and not href.startswith(("javascript:", "#")) else None

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag in self.BLOCK:
            self.out.append("\n")
        if tag == "a" and self.href:
            self.out.append(f" ({self.href})")
            self.href = None

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self.skip:
            self.out.append(data)


def html_to_text(html: str, base: str = "") -> str:
    p = _Text(base)
    p.feed(html)
    p.close()
    text = "".join(p.out)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    title = p.title.strip()
    return f"{title}\n\n{text}" if title and not text.startswith(title) else text


def fetch(url: str) -> str:
    """GET ``url`` with the safety limits above; raises ValueError with a clear message on failure."""
    current = url.strip()
    with httpx.Client(timeout=TIMEOUT, follow_redirects=False, transport=_TRANSPORT,
                      headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain;q=0.9,*/*;q=0.1"}) as client:
        for _hop in range(MAX_REDIRECTS + 1):
            err = check_url(current)
            if err:
                raise ValueError(err)
            client.cookies.clear()
            try:
                with client.stream("GET", current) as r:
                    if r.is_redirect:
                        loc = r.headers.get("location")
                        if not loc:
                            raise ValueError(f"Error: redirect from {current} without a Location header")
                        current = urljoin(current, loc)
                        continue
                    if r.status_code >= 400:
                        raise ValueError(f"{r.status_code} {r.reason_phrase}: {current}")
                    body = bytearray()
                    for chunk in r.iter_bytes():
                        body += chunk
                        if len(body) > MAX_BYTES:
                            raise ValueError(f"Refused: {current} is larger than {MAX_BYTES // (1024 * 1024)} MB")
                    ctype = r.headers.get("content-type", "").lower()
                    text = bytes(body).decode(r.encoding or "utf-8", errors="replace")
            except httpx.TimeoutException:
                raise ValueError(f"Error: timed out after {TIMEOUT:.0f}s fetching {current}") from None
            except httpx.HTTPError as e:
                raise ValueError(f"Error: could not fetch {current}: {e}") from None
            if "html" in ctype or (not ctype and "<html" in text[:2000].lower()):
                text = html_to_text(text, current)
            elif not (ctype.startswith("text/") or "json" in ctype or "xml" in ctype or not ctype):
                raise ValueError(f"Refused: {current} is not a text page ({ctype})")
            if len(text) > MAX_CHARS:
                text = text[:MAX_CHARS] + f"\n[... truncated at {MAX_CHARS} characters]"
            return text
    raise ValueError(f"Refused: more than {MAX_REDIRECTS} redirects starting at {url}")


def get_webpage(web: Annotated[Web, Depends("web")], url: str) -> str:
    """Fetches a web page and returns its content.

    :param url: the URL of the page, e.g. "https://acmesupplies.test/support".
    """
    try:
        return sim.get_webpage(web, url)
    except ValueError:
        if not enabled():
            raise
    if not urlsplit(url.strip()).scheme:
        url = "https://" + url.strip()
    return fetch(url)


def tools(base: list | None = None) -> list:
    """Swap the simulated ``get_webpage`` for the real one, only when WARRANT_REAL_WEB is on."""
    base = list(base or sim.PLAYGROUND_TOOLS)
    if not enabled():
        return base
    return [get_webpage if f.__name__ == "get_webpage" else f for f in base]
