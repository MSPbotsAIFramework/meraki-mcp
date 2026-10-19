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
"""FastMCP factory and the per-request credential middleware."""

import contextvars
import re
import uuid
from collections.abc import Callable

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .api_client import MerakiClient, MerakiCredentials
from .config import Settings, api_base_url

HEADER_API_KEY = "X-Meraki-Api-Key"
HEADER_ORG_ID = "X-Meraki-Org-Id"  # optional
REQUIRED_HEADERS = [HEADER_API_KEY]

_PLACEHOLDER = re.compile(r"^\$\{.*\}$")
_ORG_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# Per-request isolation: the middleware sets these for the duration of one
# HTTP request and resets them in `finally`. Nothing credential-related is
# ever stored at module level.
_credentials_var: contextvars.ContextVar[MerakiCredentials | None] = contextvars.ContextVar("meraki_credentials", default=None)
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("meraki_request_id", default="-")

INSTRUCTIONS = (
    "Cisco Meraki is a cloud-managed network: organizations contain networks, "
    "networks contain devices (appliances MX, switches MS, access points MR) "
    "identified by serial, and clients connect to them.\n"
    "Usage:\n"
    "- meraki_organizations_list -> meraki_networks_list(organization_id) -> "
    "meraki_devices_list(network_id) -> meraki_devices_get(serial).\n"
    "- Who is connected: meraki_clients_list(network_id, timespan).\n"
    "- Switch ports: meraki_switch_ports_list / _port_statuses_list(serial); "
    "SSIDs: meraki_wireless_ssids_list; firewall: meraki_appliance_firewall_l3_get.\n"
    "- Anything else in the Dashboard API: meraki_raw_request(method, path).\n"
    "Writes (updates, reboot, remove, delete) are refused while the server runs in "
    "read-only mode, and each needs confirm_destructive_action: true after the "
    "user approved that exact change. The API allows about 10 calls per second "
    "per organization."
)


def get_client_from_context() -> MerakiClient | None:
    """The client for the current request, or None outside a request."""
    credentials = _credentials_var.get()
    if credentials is None:
        return None
    return MerakiClient(credentials)


def _clean(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text or _PLACEHOLDER.match(text):
        return None
    return text


def _error(scope: Scope, receive: Receive, send: Send, status: int, error: str, message: str):
    return JSONResponse({"error": error, "message": message, "required_headers": REQUIRED_HEADERS}, status_code=status)(
        scope, receive, send
    )


class GatewayCredentialsMiddleware:
    """Reads the Meraki API key (and optional default organization) on /mcp requests."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith("/mcp"):
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        api_key = _clean(request.headers.get(HEADER_API_KEY))
        if not api_key:
            await _error(scope, receive, send, 401, "Missing credentials", f"Missing header(s): {HEADER_API_KEY}")
            return
        org_id = _clean(request.headers.get(HEADER_ORG_ID))
        if org_id and not _ORG_ID.match(org_id):
            await _error(scope, receive, send, 400, "Invalid credentials", f"{HEADER_ORG_ID} must be a Meraki organization id")
            return

        request_id = (request.headers.get("x-request-id") or "").strip()[:64] or uuid.uuid4().hex[:12]
        creds_token = _credentials_var.set(MerakiCredentials(api_key, org_id))
        rid_token = request_id_var.set(request_id)
        try:
            await self.app(scope, receive, send)
        finally:
            _credentials_var.reset(creds_token)
            request_id_var.reset(rid_token)


def create_mcp_server(settings: Settings | None = None) -> FastMCP:
    """Build the FastMCP server and register every tool."""
    api_base_url()  # a wrong MERAKI_BASE_URL stops the server at start, not on every call
    mcp = FastMCP(
        name="meraki-mcp",
        instructions=INSTRUCTIONS,
        # The service sits behind the gateway, so Host is never localhost;
        # DNS-rebinding protection would reject those requests with 421.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        stateless_http=True,
    )

    client_factory: Callable[[], MerakiClient | None] = get_client_from_context

    from .tools import appliance, clients, devices, navigation, networks, organizations, passthrough, switch, wireless

    for module in (navigation, organizations, networks, devices, clients, switch, wireless, appliance, passthrough):
        module.register(mcp, client_factory)

    return mcp
