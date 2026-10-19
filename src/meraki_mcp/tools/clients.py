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
"""Clients seen on a network, and their access policy."""

from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from ..api_client import MerakiClient
from ._common import DESTRUCTIVE, READ_ONLY, ClientFactory, ConfirmDestructive, NetworkId, PerPage, StartingAfter, compact, listing, page_size, run_tool, write_guard

ClientId = Annotated[str, Field(description="Client id or MAC address.", min_length=1)]


def register(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def meraki_clients_list(
        network_id: NetworkId,
        timespan: Annotated[int | None, Field(description="Look back this many seconds (max 2592000 = 31 days; default 1 day).", ge=1, le=2592000)] = None,
        per_page: PerPage = None,
        starting_after: StartingAfter = None,
    ) -> str:
        """List clients seen on a network: description, MAC, IP, user, OS, VLAN, SSID / switchport, usage."""

        async def action(client: MerakiClient) -> dict:
            params = compact({"timespan": timespan, "perPage": page_size(per_page), "startingAfter": starting_after})
            return listing(await client.get_page(f"networks/{network_id}/clients", params), "clients")

        return await run_tool("meraki_clients_list", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def meraki_clients_get(network_id: NetworkId, client_id: ClientId) -> str:
        """Get one client: MAC, IPs, user, OS, manufacturer, first / last seen, recent device."""

        async def action(client: MerakiClient) -> Any:
            return await client.get(f"networks/{network_id}/clients/{client_id}")

        return await run_tool("meraki_clients_get", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def meraki_clients_get_policy(network_id: NetworkId, client_id: ClientId) -> str:
        """Get a client's access policy (Normal, Blocked, Allowed or a group policy)."""

        async def action(client: MerakiClient) -> Any:
            return await client.get(f"networks/{network_id}/clients/{client_id}/policy")

        return await run_tool("meraki_clients_get_policy", client_factory, action)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def meraki_clients_update_policy(
        network_id: NetworkId,
        client_id: ClientId,
        device_policy: Annotated[Literal["Normal", "Blocked", "Allowed", "Group policy"], Field(description="New policy; Blocked cuts the client off.")],
        group_policy_id: Annotated[str | None, Field(description="Group policy id, with device_policy 'Group policy'.")] = None,
        confirm_destructive_action: ConfirmDestructive = None,
    ) -> str:
        """Change a client's access policy (e.g. block it). Needs confirm_destructive_action: true.

        Only when the user explicitly asks.
        """

        async def action(client: MerakiClient) -> Any:
            if device_policy == "Group policy" and not group_policy_id:
                raise ValueError("Pass group_policy_id with device_policy 'Group policy'.")
            refused = write_guard(confirm_destructive_action, f"Set client {client_id} on network {network_id} to {device_policy}")
            if refused is not None:
                return refused
            body = compact({"devicePolicy": device_policy, "groupPolicyId": group_policy_id})
            return await client.put(f"networks/{network_id}/clients/{client_id}/policy", body)

        return await run_tool("meraki_clients_update_policy", client_factory, action)
