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
"""Networks of an organization."""

from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from ..api_client import MerakiClient
from ._common import (
    DESTRUCTIVE,
    READ_ONLY,
    ClientFactory,
    ConfirmDestructive,
    NetworkId,
    OrganizationId,
    PerPage,
    StartingAfter,
    compact,
    listing,
    run_tool,
    write_guard,
)


def register(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def meraki_networks_list(organization_id: OrganizationId, per_page: PerPage = None, starting_after: StartingAfter = None) -> str:
        """List an organization's networks: id, name, product types, time zone, tags."""

        async def action(client: MerakiClient) -> dict:
            params = compact({"perPage": per_page, "startingAfter": starting_after})
            return listing(await client.get_page(f"organizations/{organization_id}/networks", params), "networks")

        return await run_tool("meraki_networks_list", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def meraki_networks_get(network_id: NetworkId) -> str:
        """Get one network: name, organization, product types, time zone, tags, notes."""

        async def action(client: MerakiClient) -> Any:
            return await client.get(f"networks/{network_id}")

        return await run_tool("meraki_networks_get", client_factory, action)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def meraki_networks_update(
        network_id: NetworkId,
        name: Annotated[str | None, Field(description="New name.")] = None,
        timeZone: Annotated[str | None, Field(description="IANA time zone, e.g. America/Chicago.")] = None,
        tags: Annotated[list[str] | None, Field(description="Replacement tag list.")] = None,
        notes: Annotated[str | None, Field(description="Network notes.")] = None,
        confirm_destructive_action: ConfirmDestructive = None,
    ) -> str:
        """Rename a network or change its time zone, tags or notes. Needs confirm_destructive_action: true.

        Only when the user explicitly asks.
        """

        async def action(client: MerakiClient) -> Any:
            body = compact({"name": name, "timeZone": timeZone, "tags": tags, "notes": notes})
            if not body:
                raise ValueError("Nothing to update: pass name, timeZone, tags or notes.")
            refused = write_guard(confirm_destructive_action, f"Update network {network_id}: {', '.join(body)}")
            if refused is not None:
                return refused
            return await client.put(f"networks/{network_id}", body)

        return await run_tool("meraki_networks_update", client_factory, action)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def meraki_networks_delete(network_id: NetworkId, confirm_destructive_action: ConfirmDestructive = None) -> str:
        """Delete a network and its configuration (irreversible). Needs confirm_destructive_action: true.

        Only when the user explicitly asks for that network.
        """

        async def action(client: MerakiClient) -> Any:
            refused = write_guard(confirm_destructive_action, f"Delete network {network_id} and all its configuration")
            if refused is not None:
                return refused
            await client.delete(f"networks/{network_id}")
            return {"done": True, "deleted_network_id": network_id}

        return await run_tool("meraki_networks_delete", client_factory, action)
