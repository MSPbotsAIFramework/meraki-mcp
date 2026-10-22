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
"""Switch ports: configuration and live status."""

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
    Offset,
    Serial,
    compact,
    local_page,
    run_tool,
    write_guard,
)


def register(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def meraki_switch_ports_list(serial: Serial, offset: Offset = None, limit: Limit = None) -> str:
        """List a switch's port configuration: name, enabled, type (access / trunk), VLANs, PoE.

        Meraki returns every port at once: continue a long switch with offset=next_offset.
        """

        async def action(client: MerakiClient) -> dict:
            rows = await client.get(f"devices/{serial}/switch/ports")
            return local_page(rows if isinstance(rows, list) else [], "ports", offset, limit, serial=serial)

        return await run_tool("meraki_switch_ports_list", client_factory, action)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def meraki_switch_ports_update(
        serial: Serial,
        port_id: Annotated[str, Field(description='Port id, e.g. "1".', min_length=1)],
        settings: Annotated[dict[str, Any], Field(description="Port settings to change, e.g. {name, enabled, vlan, type, poeEnabled}.")],
        confirm_destructive_action: ConfirmDestructive = None,
    ) -> str:
        """Change a switch port's settings (VLAN, enabled, PoE ...). Needs confirm_destructive_action: true.

        Only when the user explicitly asks; a wrong VLAN can cut devices off.
        """

        async def action(client: MerakiClient) -> Any:
            body = compact(settings)
            if not body:
                raise ValueError("Pass at least one setting to change.")
            refused = write_guard(confirm_destructive_action, f"Change port {port_id} on switch {serial}: {', '.join(body)}")
            if refused is not None:
                return refused
            return await client.put(f"devices/{serial}/switch/ports/{port_id}", body)

        return await run_tool("meraki_switch_ports_update", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def meraki_switch_port_statuses_list(
        serial: Serial,
        timespan: Annotated[int | None, Field(description="Counters over this many seconds (max 2678400; default 1 day).", ge=1, le=2678400)] = None,
        offset: Offset = None,
        limit: Limit = None,
    ) -> str:
        """Live status of a switch's ports: connected, speed, duplex, errors, traffic, LLDP / CDP neighbour.

        Meraki returns every port at once: continue a long switch with offset=next_offset.
        """

        async def action(client: MerakiClient) -> dict:
            rows = await client.get(f"devices/{serial}/switch/ports/statuses", compact({"timespan": timespan}))
            return local_page(rows if isinstance(rows, list) else [], "ports", offset, limit, serial=serial)

        return await run_tool("meraki_switch_port_statuses_list", client_factory, action)
