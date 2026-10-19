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
"""Tool discovery (kept for compatibility: every tool is always listed) and the connection check."""

from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from .._json import compact as to_json
from ..api_client import MerakiClient
from ..config import read_only_mode
from ._common import LOCAL, READ_ONLY, ClientFactory, run_tool

DOMAINS: dict[str, tuple[str, list[str]]] = {
    "organizations": ("Organizations and their device inventory.", ["meraki_organizations_list", "meraki_organizations_get", "meraki_organizations_inventory_list"]),
    "networks": ("Networks of an organization.", ["meraki_networks_list", "meraki_networks_get", "meraki_networks_update", "meraki_networks_delete"]),
    "devices": ("Devices of a network, by serial.", ["meraki_devices_list", "meraki_devices_get", "meraki_devices_reboot", "meraki_devices_remove"]),
    "clients": ("Clients seen on a network and their policies.", ["meraki_clients_list", "meraki_clients_get", "meraki_clients_get_policy", "meraki_clients_update_policy"]),
    "wireless": ("SSIDs and RF profiles.", ["meraki_wireless_ssids_list", "meraki_wireless_ssids_update", "meraki_wireless_rf_profiles_list"]),
    "switch": ("Switch ports and their live status.", ["meraki_switch_ports_list", "meraki_switch_ports_update", "meraki_switch_port_statuses_list"]),
    "appliance": ("Security appliance firewall and site-to-site VPN.", ["meraki_appliance_firewall_l3_get", "meraki_appliance_firewall_l3_update", "meraki_appliance_vpn_status_get"]),
}

Domain = Literal["organizations", "networks", "devices", "clients", "wireless", "switch", "appliance"]


def register(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(annotations=LOCAL)
    async def meraki_navigate(domain: Annotated[Domain, Field(description="Area to describe.")]) -> str:
        """Describe the tools of one Meraki area. All tools are already callable; this changes nothing."""
        summary, tools = DOMAINS[domain]
        if domain == "networks":
            tools = tools + ["meraki_raw_request"]
        return to_json({"domain": domain, "summary": summary, "tools": tools})

    @mcp.tool(annotations=READ_ONLY)
    async def meraki_status() -> str:
        """Check the Meraki connection: organizations the key can see, default organization, read-only mode."""

        async def action(client: MerakiClient) -> dict:
            orgs = await client.get("organizations", {"perPage": 1000})
            orgs = orgs if isinstance(orgs, list) else []
            return {
                "connected": True,
                "organizations": len(orgs),
                "default_organization_id": client.org_id,
                "read_only_mode": read_only_mode(),
                "domains": list(DOMAINS),
            }

        return await run_tool("meraki_status", client_factory, action)
