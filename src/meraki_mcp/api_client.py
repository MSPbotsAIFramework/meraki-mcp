# Copyright 2026 MSPbots.ai
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Async client for the Cisco Meraki Dashboard API (v1)."""

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from ._json import error_envelope
from .config import MERAKI_HOST, api_base_url

logger = logging.getLogger("meraki_mcp.api")

_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)
_MAX_RETRIES = 3
_MAX_BACKOFF_SECONDS = 20.0
# Whole-call budget including retries; the gateway times a tool call out at 120s.
_CALL_BUDGET_SECONDS = 100.0

# One connection pool for the process. It never holds credentials: the
# Authorization header is set per request from the caller's own key. Meraki
# redirects to the organization's shard host; httpx drops the Authorization
# header on cross-host redirects, so redirects are followed by hand below.
_shared_http_client: httpx.AsyncClient | None = None


def _get_shared_http_client() -> httpx.AsyncClient:
    global _shared_http_client
    if _shared_http_client is None:
        _shared_http_client = httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False)
    return _shared_http_client


# status_code -> (error code, retryable). 0 means no HTTP response at all
# (connection error or timeout).
_STATUS_TO_CODE: dict[int, tuple[str, bool]] = {
    0: ("upstream_error", True),
    400: ("invalid_argument", False),
    401: ("unauthorized", False),
    403: ("unauthorized", False),
    404: ("not_found", False),
    409: ("invalid_argument", False),
    422: ("invalid_argument", False),
    429: ("rate_limited", True),
}


def classify_status(status_code: int) -> tuple[str, bool]:
    if status_code in _STATUS_TO_CODE:
        return _STATUS_TO_CODE[status_code]
    if status_code >= 500:
        return "upstream_error", True
    if status_code >= 400:
        return "invalid_argument", False
    return "upstream_error", True


class MerakiError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"Meraki API error {status_code}: {message}")

    def to_envelope(self) -> str:
        code, retryable = classify_status(self.status_code)
        return error_envelope(code, self.message, retryable)


@dataclass(frozen=True)
class MerakiCredentials:
    api_key: str
    org_id: str | None = None

    def __repr__(self) -> str:  # never leak the key into logs or tracebacks
        return f"MerakiCredentials(org_id={self.org_id!r}, api_key=<redacted>)"


@dataclass
class Page:
    data: Any
    next_cursor: str | None


class MerakiClient:
    """Thin wrapper over the Meraki Dashboard API for one caller's key."""

    def __init__(self, credentials: MerakiCredentials, http_client: httpx.AsyncClient | None = None):
        self._credentials = credentials
        self._http = http_client
        self.org_id = credentials.org_id
        self.base_url = api_base_url()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._credentials.api_key}", "Accept": "application/json", "User-Agent": "MSPbotsMerakiMCP/1.0 MSPbots"}

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return (await self.request("GET", path, params=params)).data

    async def get_page(self, path: str, params: dict[str, Any] | None = None) -> Page:
        return await self.request("GET", path, params=params)

    async def put(self, path: str, body: Any = None) -> Any:
        return (await self.request("PUT", path, body=body)).data

    async def post(self, path: str, body: Any = None) -> Any:
        return (await self.request("POST", path, body=body)).data

    async def delete(self, path: str) -> Any:
        return (await self.request("DELETE", path)).data

    async def request(self, method: str, path: str, params: dict[str, Any] | None = None, body: Any = None) -> Page:
        http = self._http or _get_shared_http_client()
        url = f"{self.base_url}/{path.lstrip('/')}"
        query = {k: _param_value(v) for k, v in (params or {}).items() if v is not None}
        started = time.monotonic()
        attempt = 0
        redirects = 0
        while True:
            t0 = time.monotonic()
            resp: httpx.Response | None = None
            error: MerakiError | None = None
            try:
                kwargs: dict[str, Any] = {"params": query, "headers": self._headers()}
                if body is not None:
                    kwargs["json"] = body
                resp = await http.request(method, url, **kwargs)
                status = resp.status_code
            except httpx.TimeoutException:
                status, error = 0, MerakiError(0, f"Timed out calling Meraki ({method} {path})")
            except httpx.RequestError as e:
                status, error = 0, MerakiError(0, f"Network error calling Meraki ({method} {path}): {type(e).__name__}")

            logger.info("meraki_api method=%s path=%s status=%s ms=%d attempt=%d", method, path.split("?")[0], status, (time.monotonic() - t0) * 1000, attempt)

            # Follow shard redirects ourselves, only to Meraki hosts, keeping the key.
            if resp is not None and status in (301, 302, 307, 308) and redirects < 3:
                target = resp.headers.get("location", "")
                host = urlparse(target).hostname or ""
                if not MERAKI_HOST.match(host):
                    raise MerakiError(502, f"Meraki redirected to an unexpected host for {path}")
                url, query, redirects = target, {}, redirects + 1
                continue

            # 429 is refused before processing, so any method may retry it; other
            # failures are retried only for reads, so a write never runs twice.
            retryable = status == 429 or (method == "GET" and (status == 0 or status >= 500))
            if retryable and attempt < _MAX_RETRIES:
                delay = _retry_delay(resp, attempt)
                if time.monotonic() - started + delay < _CALL_BUDGET_SECONDS:
                    attempt += 1
                    await asyncio.sleep(delay)
                    continue
            if error is not None:
                raise error
            assert resp is not None
            if resp.status_code >= 400:
                raise MerakiError(resp.status_code, _error_message(resp, path))
            data: Any = None
            if resp.content and resp.content.strip():
                try:
                    data = resp.json()
                except ValueError:
                    data = {"raw_response": resp.text[:2000]}
            return Page(data, _next_cursor(resp))


def _param_value(value: Any) -> Any:
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def _next_cursor(resp: httpx.Response) -> str | None:
    """The startingAfter value of the Link header's rel=next URL, if any."""
    link = resp.headers.get("link", "")
    for part in link.split(","):
        if 'rel=next' in part.replace('"', ""):
            match = re.search(r"<([^>]+)>", part)
            if match:
                values = parse_qs(urlparse(match.group(1)).query).get("startingAfter")
                if values:
                    return values[0]
    return None


def _retry_delay(resp: httpx.Response | None, attempt: int) -> float:
    if resp is not None:
        retry_after = resp.headers.get("Retry-After")
        if retry_after:
            try:
                return max(0.0, min(float(retry_after), _MAX_BACKOFF_SECONDS))
            except ValueError:
                pass
    return min(float(2**attempt), _MAX_BACKOFF_SECONDS)


def _error_message(resp: httpx.Response, path: str) -> str:
    fallback = f"Meraki returned HTTP {resp.status_code} for {path.split('?')[0]}"
    try:
        payload = resp.json()
    except ValueError:
        text = resp.text.strip()
        if text.lower().startswith("<"):
            return fallback  # an HTML error page, not useful to the agent
        return f"{fallback}: {text[:300]}" if text else fallback
    if isinstance(payload, dict):
        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            return f"{fallback}: {'; '.join(map(str, errors))[:500]}"
        message = payload.get("message") or payload.get("error")
        if message:
            return f"{fallback}: {str(message)[:500]}"
    return fallback
