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
"""Devices (appliances, switches, access points) by serial."""

from typing import Any

from mcp.server.fastmcp import FastMCP

from ..api_client import MerakiClient
from ._common import (
    DESTRUCTIVE,
    READ_ONLY,
    ClientFactory,
    ConfirmDestructive,
    Limit,
    NetworkId,
    Offset,
    Serial,
    local_page,
    run_tool,
    write_guard,
)


def register(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def meraki_devices_list(network_id: NetworkId, offset: Offset = None, limit: Limit = None) -> str:
        """List a network's devices: serial, model, name, MAC, LAN IP, firmware, address, tags."""

        async def action(client: MerakiClient) -> dict:
            rows = await client.get(f"networks/{network_id}/devices")
            return local_page(rows if isinstance(rows, list) else [], "devices", offset, limit)

        return await run_tool("meraki_devices_list", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def meraki_devices_get(serial: Serial) -> str:
        """Get one device by serial: model, name, network, MAC, IPs, firmware, location, tags."""

        async def action(client: MerakiClient) -> Any:
            return await client.get(f"devices/{serial}")

        return await run_tool("meraki_devices_get", client_factory, action)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def meraki_devices_reboot(serial: Serial, confirm_destructive_action: ConfirmDestructive = None) -> str:
        """Reboot a device (traffic through it drops for a few minutes). Needs confirm.

        Only when the user explicitly asks; pass confirm_destructive_action: true.
        """

        async def action(client: MerakiClient) -> Any:
            refused = write_guard(confirm_destructive_action, f"Reboot device {serial}")
            if refused is not None:
                return refused
            return await client.post(f"devices/{serial}/reboot")

        return await run_tool("meraki_devices_reboot", client_factory, action)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def meraki_devices_remove(network_id: NetworkId, serial: Serial, confirm_destructive_action: ConfirmDestructive = None) -> str:
        """Remove a device from a network (it stays in the organization's inventory). Needs confirm.

        Only when the user explicitly asks; pass confirm_destructive_action: true.
        """

        async def action(client: MerakiClient) -> Any:
            refused = write_guard(confirm_destructive_action, f"Remove device {serial} from network {network_id}")
            if refused is not None:
                return refused
            await client.post(f"networks/{network_id}/devices/remove", {"serial": serial})
            return {"done": True, "removed_serial": serial, "network_id": network_id}

        return await run_tool("meraki_devices_remove", client_factory, action)
