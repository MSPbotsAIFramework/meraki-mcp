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
"""tools/list snapshot, annotations and error-envelope mapping. No network access."""

import asyncio
import json

import pytest

from meraki_mcp.api_client import MerakiError
from meraki_mcp.server import INSTRUCTIONS, create_mcp_server
from meraki_mcp.tools.navigation import DOMAINS

C = "confirm_destructive_action"

# Tool name -> (parameters the earlier version declared, required parameters).
# Names and these parameters are a public contract.
EXPECTED: dict[str, tuple[set[str], set[str]]] = {
    "meraki_navigate": ({"domain"}, {"domain"}),
    "meraki_status": (set(), set()),
    "meraki_organizations_list": (set(), set()),
    "meraki_organizations_get": ({"organization_id"}, {"organization_id"}),
    "meraki_organizations_inventory_list": ({"organization_id", "per_page", "starting_after"}, {"organization_id"}),
    "meraki_networks_list": ({"organization_id", "per_page", "starting_after"}, {"organization_id"}),
    "meraki_networks_get": ({"network_id"}, {"network_id"}),
    "meraki_networks_update": ({"network_id", "name", "timeZone", "tags", "notes", C}, {"network_id"}),
    "meraki_networks_delete": ({"network_id", C}, {"network_id"}),
    "meraki_devices_list": ({"network_id"}, {"network_id"}),
    "meraki_devices_get": ({"serial"}, {"serial"}),
    "meraki_devices_reboot": ({"serial", C}, {"serial"}),
    "meraki_devices_remove": ({"network_id", "serial", C}, {"network_id", "serial"}),
    "meraki_clients_list": ({"network_id", "timespan", "per_page"}, {"network_id"}),
    "meraki_clients_get": ({"network_id", "client_id"}, {"network_id", "client_id"}),
    "meraki_clients_get_policy": ({"network_id", "client_id"}, {"network_id", "client_id"}),
    "meraki_clients_update_policy": (
        {"network_id", "client_id", "device_policy", "group_policy_id", C},
        {"network_id", "client_id", "device_policy"},
    ),
    "meraki_switch_ports_list": ({"serial"}, {"serial"}),
    "meraki_switch_ports_update": ({"serial", "port_id", "settings", C}, {"serial", "port_id", "settings"}),
    "meraki_switch_port_statuses_list": ({"serial", "timespan"}, {"serial"}),
    "meraki_wireless_ssids_list": ({"network_id"}, {"network_id"}),
    "meraki_wireless_ssids_update": ({"network_id", "number", "settings", C}, {"network_id", "number", "settings"}),
    "meraki_wireless_rf_profiles_list": ({"network_id"}, {"network_id"}),
    "meraki_appliance_firewall_l3_get": ({"network_id"}, {"network_id"}),
    "meraki_appliance_firewall_l3_update": ({"network_id", "rules", C}, {"network_id", "rules"}),
    "meraki_appliance_vpn_status_get": ({"network_id"}, {"network_id"}),
    "meraki_raw_request": ({"method", "path", "query", "body", C}, {"method", "path"}),
}

WRITES = {name for name, (params, _) in EXPECTED.items() if C in params}


@pytest.fixture(scope="module")
def tools():
    return {t.name: t for t in asyncio.run(create_mcp_server().list_tools())}


def test_tool_names_are_stable(tools):
    assert set(tools) == set(EXPECTED)
    assert len(tools) == 27
    assert len(WRITES) == 9


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_params(tools, name):
    schema = tools[name].inputSchema
    params, required = EXPECTED[name]
    assert params <= set(schema["properties"])
    assert set(schema.get("required", [])) == required


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_description_budget(tools, name):
    description = (tools[name].description or "").strip()
    assert description, f"{name} has no description"
    assert len(description) <= 500
    assert len(description.splitlines()[0]) <= 100
    if name in WRITES:
        assert "explicitly ask" in description


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_annotations(tools, name):
    annotations = tools[name].annotations
    assert annotations is not None
    if name in WRITES:
        assert annotations.readOnlyHint is False and annotations.destructiveHint is True
    else:
        assert annotations.readOnlyHint is True and not annotations.destructiveHint
    assert (annotations.openWorldHint is False) is (name == "meraki_navigate")


def test_enums(tools):
    props = tools["meraki_clients_update_policy"].inputSchema["properties"]
    assert props["device_policy"]["enum"] == ["Normal", "Blocked", "Allowed", "Group policy"]
    assert tools["meraki_raw_request"].inputSchema["properties"]["method"]["enum"] == ["GET", "POST", "PUT", "DELETE"]
    number = tools["meraki_wireless_ssids_update"].inputSchema["properties"]["number"]
    assert (number["minimum"], number["maximum"]) == (0, 14)
    assert tools["meraki_networks_list"].inputSchema["properties"]["per_page"]["anyOf"][0]["maximum"] == 1000


def test_navigate_lists_every_tool():
    listed = {tool for _, names in DOMAINS.values() for tool in names}
    assert listed | {"meraki_navigate", "meraki_status", "meraki_raw_request"} == set(EXPECTED)


def test_instructions_budget():
    assert len(INSTRUCTIONS) <= 1500
    assert "\n" in INSTRUCTIONS


@pytest.mark.parametrize(
    "status,code,retryable",
    [
        (401, "unauthorized", False),
        (403, "unauthorized", False),
        (404, "not_found", False),
        (400, "invalid_argument", False),
        (429, "rate_limited", True),
        (500, "upstream_error", True),
        (0, "upstream_error", True),
    ],
)
def test_error_envelope(status, code, retryable):
    envelope = json.loads(MerakiError(status, "boom").to_envelope())
    assert envelope == {"error": {"code": code, "message": "boom", "retryable": retryable}}


def test_tool_without_credentials_returns_not_configured():
    content, _ = asyncio.run(create_mcp_server().call_tool("meraki_organizations_list", {}))
    assert json.loads(content[0].text)["error"]["code"] == "not_configured"
