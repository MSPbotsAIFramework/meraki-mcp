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
"""Wireless: SSIDs and RF profiles."""

from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from ..api_client import MerakiClient
from ._common import (
    DESTRUCTIVE,
    READ_ONLY,
    ClientFactory,
    ConfirmDestructive,
    Limit,
    NetworkId,
    Offset,
    compact,
    local_page,
    run_tool,
    write_guard,
)

_SECRET_FIELDS = ("psk", "radiusServers", "radiusAccountingServers")


def register(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def meraki_wireless_ssids_list(network_id: NetworkId, offset: Offset = None, limit: Limit = None) -> str:
        """List a network's SSIDs (0-14): name, enabled, auth / encryption, VLAN, band; pre-shared keys hidden."""

        async def action(client: MerakiClient) -> dict:
            rows = await client.get(f"networks/{network_id}/wireless/ssids")
            rows = [{k: ("********" if k == "psk" and v else v) for k, v in r.items()} if isinstance(r, dict) else r for r in (rows if isinstance(rows, list) else [])]
            return local_page(rows, "ssids", offset, limit, network_id=network_id)

        return await run_tool("meraki_wireless_ssids_list", client_factory, action)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def meraki_wireless_ssids_update(
        network_id: NetworkId,
        number: Annotated[int, Field(description="SSID number (0-14).", ge=0, le=14)],
        settings: Annotated[dict[str, Any], Field(description="SSID settings to change, e.g. {name, enabled, authMode, psk, vlanId}.")],
        confirm_destructive_action: ConfirmDestructive = None,
    ) -> str:
        """Change an SSID (name, enabled, security, VLAN ...). Needs confirm_destructive_action: true.

        Only when the user explicitly asks; clients on that SSID can be disconnected.
        """

        async def action(client: MerakiClient) -> Any:
            body = compact(settings)
            if not body:
                raise ValueError("Pass at least one setting to change.")
            shown = ", ".join("psk" if k in _SECRET_FIELDS else k for k in body)
            refused = write_guard(confirm_destructive_action, f"Change SSID {number} on network {network_id}: {shown}")
            if refused is not None:
                return refused
            result = await client.put(f"networks/{network_id}/wireless/ssids/{number}", body)
            if isinstance(result, dict) and result.get("psk"):
                result = result | {"psk": "********"}
            return result

        return await run_tool("meraki_wireless_ssids_update", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def meraki_wireless_rf_profiles_list(network_id: NetworkId, offset: Offset = None, limit: Limit = None) -> str:
        """List a network's RF profiles: band selection, channel width, power and minimum bitrate per band."""

        async def action(client: MerakiClient) -> dict:
            rows = await client.get(f"networks/{network_id}/wireless/rfProfiles")
            return local_page(rows if isinstance(rows, list) else [], "rf_profiles", offset, limit, network_id=network_id)

        return await run_tool("meraki_wireless_rf_profiles_list", client_factory, action)
