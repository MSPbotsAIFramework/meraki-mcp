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
"""Organizations and their device inventory."""

from typing import Any

from mcp.server.fastmcp import FastMCP

from ..api_client import MerakiClient
from ._common import (
    READ_ONLY,
    ClientFactory,
    Limit,
    Offset,
    OrganizationId,
    PerPage,
    StartingAfter,
    compact,
    listing,
    local_page,
    page_size,
    run_tool,
)


def register(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def meraki_organizations_list(
        offset: Offset = None, limit: Limit = None, starting_after: StartingAfter = None
    ) -> str:
        """List the organizations the API key can access: id, name, dashboard URL, licensing model.

        Reads up to 1,000 at once: continue with offset=next_offset, then starting_after=next_cursor.
        """

        async def action(client: MerakiClient) -> dict:
            page = await client.get_page("organizations", compact({"perPage": 1000, "startingAfter": starting_after}))
            rows = page.data if isinstance(page.data, list) else []
            result = local_page(rows, "organizations", offset, limit)
            if not result["has_more"] and page.next_cursor:
                # This Meraki page is done; the next one starts at next_cursor (offset 0 there).
                result |= {"has_more": True, "next_cursor": page.next_cursor}
            return result

        return await run_tool("meraki_organizations_list", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def meraki_organizations_get(organization_id: OrganizationId) -> str:
        """Get one organization: name, URL, API access, licensing, cloud region."""

        async def action(client: MerakiClient) -> Any:
            return await client.get(f"organizations/{organization_id}")

        return await run_tool("meraki_organizations_get", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def meraki_organizations_inventory_list(
        organization_id: OrganizationId, per_page: PerPage = None, starting_after: StartingAfter = None
    ) -> str:
        """List all devices claimed in an organization (used or not): serial, model, MAC, network, order."""

        async def action(client: MerakiClient) -> dict:
            params = compact({"perPage": page_size(per_page), "startingAfter": starting_after})
            return listing(await client.get_page(f"organizations/{organization_id}/inventory/devices", params), "devices")

        return await run_tool("meraki_organizations_inventory_list", client_factory, action)
