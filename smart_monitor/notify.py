"""ntfy.sh / self-hosted ntfy HTTP publisher. stdlib only."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Optional


class NotifyError(Exception):
    pass


def send_ntfy(
    server: str,
    topic: str,
    title: str,
    message: str,
    priority: int = 3,
    tags: Optional[str] = None,
    token: str = "",
    timeout: int = 20,
) -> None:
    if not topic:
        raise NotifyError("ntfy topic is empty")
    url = f"{server.rstrip('/')}/{topic}"
    headers = {
        "Title": title.encode("utf-8").decode("latin-1", "replace"),
        "Priority": str(int(priority)),
        "Content-Type": "text/plain; charset=utf-8",
    }
    if tags:
        headers["Tags"] = tags
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = message.encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read()
            if resp.status >= 400:
                raise NotifyError(f"ntfy HTTP {resp.status}: {payload[:200]!r}")
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:200] if exc.fp else b""
        raise NotifyError(f"ntfy HTTP {exc.code}: {detail!r}") from exc
    except urllib.error.URLError as exc:
        raise NotifyError(f"ntfy request failed: {exc}") from exc
    except TimeoutError as exc:
        raise NotifyError("ntfy request timed out") from exc
    # ntfy returns JSON on success; ignore parse errors
    try:
        json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        pass
