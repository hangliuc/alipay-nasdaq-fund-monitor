"""Shared HTTP doubles for independent direct-sales adapter tests.

The adapters intentionally use different public endpoints, but their tests all
need the same small response/session contract.  Keeping the doubles here makes
request assertions consistent while still allowing GET-only, POST-only, and
method-aware fixtures.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import requests


class FakeResponse:
    """Minimal ``requests.Response`` stand-in used by adapter unit tests."""

    def __init__(
        self,
        *args: Any,
        content: Any = None,
        payload: Any = None,
        status_code: int = 200,
        url: str = "",
        headers: Mapping[str, str] | None = None,
        text: str | None = None,
    ) -> None:
        # Retain the convenient positional forms used by existing fixtures:
        # FakeResponse(html, url) and FakeResponse(json_payload).
        if args:
            first = args[0]
            if isinstance(first, (dict, list, tuple, Exception)):
                payload = first
            else:
                content = first
            if len(args) > 1:
                second = args[1]
                if isinstance(second, str):
                    url = second
                elif isinstance(second, int):
                    status_code = second
            if len(args) > 2:
                status_code = args[2]
            if len(args) > 3:
                url = args[3]

        if content is None:
            content = b""
        if isinstance(content, str):
            content = content.encode("utf-8")
        self.content = content
        self.status_code = status_code
        self.url = url
        self.headers = dict(headers or {})
        self._payload = payload
        # A few official adapters use the public attribute while others use
        # the private one; exposing both keeps the test double honest.
        self.payload = payload
        self._text = text

    @property
    def text(self) -> str:
        if self._text is not None:
            return self._text
        if isinstance(self.content, (bytes, bytearray)):
            return self.content.decode("utf-8", errors="replace")
        return str(self.content)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)

    def json(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        if self._payload is None:
            raise ValueError("JSON payload was not configured")
        return self._payload


class FakeSession:
    """URL-mapped session double supporting GET, POST, and callable fixtures."""

    def __init__(
        self,
        responses: Mapping[str, Any] | None = None,
        *,
        gets: Mapping[str, Any] | None = None,
        posts: Mapping[str, Any] | None = None,
        record_method: bool | None = None,
    ) -> None:
        self.responses = dict(responses or {})
        self.gets = dict(gets or {})
        self.posts = dict(posts or {})
        if record_method is None:
            record_method = gets is not None or posts is not None
        self.record_method = record_method
        self.calls: list[tuple] = []

    def _request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        mapping = self.gets if method == "GET" else self.posts
        response = mapping.get(url)
        if response is None:
            response = self.responses.get(url)
        if response is None:
            raise AssertionError(f"unexpected {method} URL: {url}")
        if self.record_method:
            self.calls.append((method, url, kwargs))
        else:
            self.calls.append((url, kwargs))
        return response(url, kwargs) if callable(response) else response

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        return self._request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        return self._request("POST", url, **kwargs)
