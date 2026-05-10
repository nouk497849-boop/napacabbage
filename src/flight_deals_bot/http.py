from __future__ import annotations

import gzip
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


class HttpError(RuntimeError):
    def __init__(self, status: int, url: str, body: str):
        super().__init__(f"HTTP {status} for {url}: {body[:300]}")
        self.status = status
        self.url = url
        self.body = body


@dataclass
class JsonHttpClient:
    timeout_seconds: int = 30
    user_agent: str = "taiwan-flight-deals-bot/0.1"

    def get_json(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if params:
            query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}{query}"
        return self._request_json("GET", url, headers=headers)

    def post_json(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        merged = {"Content-Type": "application/json", **(headers or {})}
        return self._request_json("POST", url, data=json.dumps(payload).encode("utf-8"), headers=merged)

    def post_form(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        merged = {"Content-Type": "application/x-www-form-urlencoded", **(headers or {})}
        data = urllib.parse.urlencode(payload).encode("utf-8")
        return self._request_json("POST", url, data=data, headers=merged)

    def _request_json(
        self,
        method: str,
        url: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        request_headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "User-Agent": self.user_agent,
            **(headers or {}),
        }
        request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                text = body.decode("utf-8")
                return json.loads(text) if text else {}
        except urllib.error.HTTPError as exc:
            body_bytes = exc.read()
            if exc.headers.get("Content-Encoding") == "gzip":
                body_bytes = gzip.decompress(body_bytes)
            raise HttpError(exc.code, url, body_bytes.decode("utf-8", errors="replace")) from exc
        except urllib.error.URLError as exc:
            raise HttpError(0, url, str(exc)) from exc
