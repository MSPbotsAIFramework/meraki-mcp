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
"""Security appliance (MX): layer 3 firewall rules and site-to-site VPN."""

from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from ..api_client import MerakiClient
from ._common import DESTRUCTIVE, READ_ONLY, ClientFactory, ConfirmDestructive, NetworkId, run_tool, write_guard


def register(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def meraki_appliance_firewall_l3_get(network_id: NetworkId) -> str:
        """Get the appliance's layer 3 firewall rules in order: policy, protocol, source, destination, port."""

        async def action(client: MerakiClient) -> Any:
            return await client.get(f"networks/{network_id}/appliance/firewall/l3FirewallRules")

        return await run_tool("meraki_appliance_firewall_l3_get", client_factory, action)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def meraki_appliance_firewall_l3_update(
        network_id: NetworkId,
        rules: Annotated[
            list[dict[str, Any]],
            Field(description="The complete ordered rule list (it replaces the current one), each {policy, protocol, srcCidr, destCidr, destPort, comment}."),
        ],
        confirm_destructive_action: ConfirmDestructive = None,
    ) -> str:
        """Replace the appliance's layer 3 firewall rules with this list. Needs confirm.

        Pass confirm_destructive_action: true. Rules not in the list are removed. Only when the user explicitly asks; read the current rules first.
        """

        async def action(client: MerakiClient) -> Any:
            refused = write_guard(confirm_destructive_action, f"Replace the layer 3 firewall rules of network {network_id} with {len(rules)} rule(s)")
            if refused is not None:
                return refused
            return await client.put(f"networks/{network_id}/appliance/firewall/l3FirewallRules", {"rules": rules})

        return await run_tool("meraki_appliance_firewall_l3_update", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def meraki_appliance_vpn_status_get(network_id: NetworkId) -> str:
        """Get the network's site-to-site VPN settings: mode (hub / spoke / none), hubs and subnets."""

        async def action(client: MerakiClient) -> Any:
            return await client.get(f"networks/{network_id}/appliance/vpn/siteToSiteVpn")

        return await run_tool("meraki_appliance_vpn_status_get", client_factory, action)
