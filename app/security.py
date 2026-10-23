from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SENSITIVE_QUERY_KEYS = {
    "token", "access_token", "auth", "authorization", "password", "passwd", "session",
    "signature", "sig", "expires", "key", "api_key", "apikey", "jwt", "credential",
}


def sanitize_url(url: str) -> str:
    """Keep a history-safe URL without common credential-like query parameters."""
    parsed = urlsplit(url.strip())
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
             if key.lower() not in SENSITIVE_QUERY_KEYS and not key.lower().endswith("_token")]
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query, doseq=True), ""))[:1800]


def _validate_host_sync(url: str) -> tuple[bool, str]:
    try:
        parsed = urlsplit(url.strip())
        if parsed.scheme.lower() != "https":
            return False, "Only HTTPS links are accepted."
        if not parsed.hostname or parsed.username or parsed.password:
            return False, "Invalid URL host or embedded credentials."
        if parsed.port not in (None, 443):
            return False, "Only standard HTTPS port 443 is allowed."
        host = parsed.hostname.rstrip(".").lower()
        if host in {"localhost", "localhost.localdomain"} or host.endswith((".localhost", ".local", ".internal")):
            return False, "Local hosts are not allowed."
        try:
            ip = ipaddress.ip_address(host)
            if not ip.is_global:
                return False, "Private or reserved IP addresses are not allowed."
            return True, ""
        except ValueError:
            pass
        answers = socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
        addresses = {ipaddress.ip_address(answer[4][0]) for answer in answers}
        if not addresses or any(not address.is_global for address in addresses):
            return False, "Host resolves to a private or reserved address."
        return True, ""
    except (OSError, ValueError, UnicodeError):
        return False, "Could not safely resolve this host."


async def validate_public_https_url(url: str) -> tuple[bool, str]:
    if not url or len(url) > 2048:
        return False, "Invalid URL length."
    if any(ord(char) < 32 for char in url):
        return False, "Control characters are not allowed."
    try:
        return await asyncio.wait_for(asyncio.to_thread(_validate_host_sync, url), timeout=5)
    except asyncio.TimeoutError:
        return False, "Host validation timed out."
