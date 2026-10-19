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
"""Any other Dashboard API call, with the same read-only and confirmation rules."""

import re
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from ..api_client import MerakiClient
from ._common import DESTRUCTIVE, ClientFactory, ConfirmDestructive, run_tool, write_guard

# A relative Dashboard API path: no scheme, host, '//', '..' or query string (use `query`).
_PATH = re.compile(r"^/?[A-Za-z0-9_\-./{}~]+$")


def register(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(annotations=DESTRUCTIVE)
    async def meraki_raw_request(
        method: Annotated[Literal["GET", "POST", "PUT", "DELETE"], Field(description="HTTP method.")],
        path: Annotated[str, Field(description='Dashboard API v1 path, e.g. "/organizations/{id}/networks".', min_length=1)],
        query: Annotated[dict[str, Any] | None, Field(description="Query string parameters.")] = None,
        body: Annotated[dict[str, Any] | list[Any] | None, Field(description="JSON request body.")] = None,
        confirm_destructive_action: ConfirmDestructive = None,
    ) -> str:
        """Call any Meraki Dashboard API v1 endpoint. GET reads; POST / PUT / DELETE change data.

        Changes are refused in read-only mode and need confirm_destructive_action: true; only when the user
        explicitly asks.
        """

        async def action(client: MerakiClient) -> Any:
            clean = path.strip()
            if "//" in clean or ".." in clean or "?" in clean or not _PATH.match(clean):
                raise ValueError("path must be a relative Dashboard API path such as /organizations/123/networks; put parameters in query.")
            if method != "GET":
                refused = write_guard(confirm_destructive_action, f"{method} {clean}")
                if refused is not None:
                    return refused
            page = await client.request(method, clean, params=query, body=body)
            return {"data": page.data, "next_cursor": page.next_cursor} if page.next_cursor else page.data

        return await run_tool("meraki_raw_request", client_factory, action)
