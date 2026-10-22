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
"""Request shapes, paging, redirects, retries, the write guard and routes against a mocked Meraki API."""

import json

import httpx
import pytest

from meraki_mcp import api_client
from meraki_mcp.api_client import MerakiClient, MerakiCredentials, MerakiError
from meraki_mcp.server import _credentials_var, create_mcp_server

CREDS = MerakiCredentials("key-123", "549236")
PREFIX = "/api/v1/"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    async def instant(_seconds):
        return None

    monkeypatch.setattr(api_client.asyncio, "sleep", instant)


@pytest.fixture
def writable(monkeypatch):
    monkeypatch.setenv("READ_ONLY_MODE", "false")


class Recorder:
    """Mock transport: replies by method + path below /api/v1/."""

    def __init__(self, routes=None, default=None):
        self.requests: list[httpx.Request] = []
        self.routes = routes or {}
        self.default = default if default is not None else []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.removeprefix(PREFIX)
        reply = self.routes.get(f"{request.method} {path}", self.routes.get(path, self.default))
        if callable(reply):
            return reply(request)
        return httpx.Response(200, json=reply)


async def _call(monkeypatch, recorder: Recorder, tool: str, args: dict):
    http = httpx.AsyncClient(transport=httpx.MockTransport(recorder))
    monkeypatch.setattr(api_client, "_get_shared_http_client", lambda: http)
    ctx = _credentials_var.set(CREDS)
    try:
        content, _ = await create_mcp_server().call_tool(tool, args)
    finally:
        _credentials_var.reset(ctx)
    return json.loads(content[0].text)


def _client(recorder: Recorder) -> MerakiClient:
    return MerakiClient(CREDS, httpx.AsyncClient(transport=httpx.MockTransport(recorder)))


async def test_bearer_key_and_base_url():
    rec = Recorder()
    await _client(rec).get("organizations", {"perPage": 5})
    assert rec.requests[0].headers["authorization"] == "Bearer key-123"
    assert str(rec.requests[0].url) == "https://api.meraki.com/api/v1/organizations?perPage=5"


async def test_redirect_to_shard_keeps_key():
    def first(request):
        if request.url.host == "api.meraki.com":
            return httpx.Response(308, headers={"Location": "https://n123.meraki.com/api/v1/organizations"})
        return httpx.Response(200, json=[{"id": "1"}])

    rec = Recorder({"organizations": first})
    assert await _client(rec).get("organizations") == [{"id": "1"}]
    assert rec.requests[1].url.host == "n123.meraki.com"
    assert rec.requests[1].headers["authorization"] == "Bearer key-123"


async def test_redirect_to_foreign_host_is_refused():
    rec = Recorder({"organizations": lambda r: httpx.Response(302, headers={"Location": "https://evil.example.net/x"})})
    with pytest.raises(MerakiError) as exc:
        await _client(rec).get("organizations")
    assert exc.value.status_code == 502
    assert len(rec.requests) == 1


async def test_get_is_retried_on_429_and_5xx():
    replies = iter([httpx.Response(429, headers={"Retry-After": "1"}), httpx.Response(503), httpx.Response(200, json=[])])
    rec = Recorder({"organizations": lambda r: next(replies)})
    assert await _client(rec).get("organizations") == []
    assert len(rec.requests) == 3


async def test_write_is_not_retried_on_5xx():
    rec = Recorder({"POST devices/Q2/reboot": lambda r: httpx.Response(502)})
    with pytest.raises(MerakiError):
        await _client(rec).post("devices/Q2/reboot")
    assert len(rec.requests) == 1


async def test_write_is_retried_on_429():
    replies = iter([httpx.Response(429), httpx.Response(202, json={"success": True})])
    rec = Recorder({"POST devices/Q2/reboot": lambda r: next(replies)})
    assert await _client(rec).post("devices/Q2/reboot") == {"success": True}
    assert len(rec.requests) == 2


async def test_errors_list_becomes_message():
    rec = Recorder({"networks/N_1": lambda r: httpx.Response(400, json={"errors": ["Invalid time zone", "Bad tag"]})})
    with pytest.raises(MerakiError) as exc:
        await _client(rec).get("networks/N_1")
    assert "Invalid time zone; Bad tag" in exc.value.message


async def test_link_header_gives_next_cursor(monkeypatch):
    link = '<https://api.meraki.com/api/v1/organizations/1/networks?perPage=2&startingAfter=L_9>; rel=next, <https://api.meraki.com/api/v1/organizations/1/networks?perPage=2>; rel=first'
    rec = Recorder({"organizations/1/networks": lambda r: httpx.Response(200, json=[{"id": "L_1"}, {"id": "L_9"}], headers={"Link": link})})
    out = await _call(monkeypatch, rec, "meraki_networks_list", {"organization_id": "1", "per_page": 2, "starting_after": "L_0"})
    assert out == {"networks": [{"id": "L_1"}, {"id": "L_9"}], "count": 2, "has_more": True, "next_cursor": "L_9"}
    assert dict(rec.requests[0].url.params) == {"perPage": "2", "startingAfter": "L_0"}


@pytest.mark.parametrize(
    "tool,args,method,path",
    [
        ("meraki_organizations_get", {"organization_id": "1"}, "GET", "organizations/1"),
        ("meraki_organizations_inventory_list", {"organization_id": "1"}, "GET", "organizations/1/inventory/devices"),
        ("meraki_networks_get", {"network_id": "N_1"}, "GET", "networks/N_1"),
        ("meraki_devices_list", {"network_id": "N_1"}, "GET", "networks/N_1/devices"),
        ("meraki_devices_get", {"serial": "Q2"}, "GET", "devices/Q2"),
        ("meraki_clients_list", {"network_id": "N_1", "timespan": 3600}, "GET", "networks/N_1/clients"),
        ("meraki_clients_get", {"network_id": "N_1", "client_id": "k1"}, "GET", "networks/N_1/clients/k1"),
        ("meraki_clients_get_policy", {"network_id": "N_1", "client_id": "k1"}, "GET", "networks/N_1/clients/k1/policy"),
        ("meraki_switch_ports_list", {"serial": "Q2"}, "GET", "devices/Q2/switch/ports"),
        ("meraki_switch_port_statuses_list", {"serial": "Q2"}, "GET", "devices/Q2/switch/ports/statuses"),
        ("meraki_wireless_ssids_list", {"network_id": "N_1"}, "GET", "networks/N_1/wireless/ssids"),
        ("meraki_wireless_rf_profiles_list", {"network_id": "N_1"}, "GET", "networks/N_1/wireless/rfProfiles"),
        ("meraki_appliance_firewall_l3_get", {"network_id": "N_1"}, "GET", "networks/N_1/appliance/firewall/l3FirewallRules"),
        ("meraki_appliance_vpn_status_get", {"network_id": "N_1"}, "GET", "networks/N_1/appliance/vpn/siteToSiteVpn"),
    ],
)
async def test_read_routes(monkeypatch, tool, args, method, path):
    rec = Recorder()
    out = await _call(monkeypatch, rec, tool, args)
    assert "error" not in out
    assert (rec.requests[0].method, rec.requests[0].url.path) == (method, PREFIX + path)


async def test_status(monkeypatch):
    rec = Recorder({"organizations": [{"id": "1"}, {"id": "2"}]})
    out = await _call(monkeypatch, rec, "meraki_status", {})
    assert out["connected"] is True and out["organizations"] == 2
    assert out["default_organization_id"] == "549236" and out["read_only_mode"] is True


async def test_ssid_psk_is_masked(monkeypatch):
    rec = Recorder({"networks/N_1/wireless/ssids": [{"number": 0, "name": "Corp", "psk": "hunter22"}, {"number": 1, "psk": ""}]})
    out = await _call(monkeypatch, rec, "meraki_wireless_ssids_list", {"network_id": "N_1"})
    assert out["ssids"][0]["psk"] == "********"
    assert "hunter22" not in json.dumps(out)


WRITES = [
    ("meraki_networks_update", {"network_id": "N_1", "name": "HQ", "tags": ["a"]}, "PUT", "networks/N_1", {"name": "HQ", "tags": ["a"]}),
    ("meraki_networks_delete", {"network_id": "N_1"}, "DELETE", "networks/N_1", None),
    ("meraki_devices_reboot", {"serial": "Q2"}, "POST", "devices/Q2/reboot", None),
    ("meraki_devices_remove", {"network_id": "N_1", "serial": "Q2"}, "POST", "networks/N_1/devices/remove", {"serial": "Q2"}),
    (
        "meraki_clients_update_policy",
        {"network_id": "N_1", "client_id": "k1", "device_policy": "Blocked"},
        "PUT",
        "networks/N_1/clients/k1/policy",
        {"devicePolicy": "Blocked"},
    ),
    ("meraki_switch_ports_update", {"serial": "Q2", "port_id": "3", "settings": {"vlan": 20}}, "PUT", "devices/Q2/switch/ports/3", {"vlan": 20}),
    ("meraki_wireless_ssids_update", {"network_id": "N_1", "number": 2, "settings": {"enabled": False}}, "PUT", "networks/N_1/wireless/ssids/2", {"enabled": False}),
    (
        "meraki_appliance_firewall_l3_update",
        {"network_id": "N_1", "rules": [{"policy": "deny", "protocol": "any", "srcCidr": "Any", "destCidr": "10.0.0.0/8"}]},
        "PUT",
        "networks/N_1/appliance/firewall/l3FirewallRules",
        {"rules": [{"policy": "deny", "protocol": "any", "srcCidr": "Any", "destCidr": "10.0.0.0/8"}]},
    ),
    ("meraki_raw_request", {"method": "POST", "path": "/networks/N_1/splitPolicy", "body": {"x": 1}}, "POST", "networks/N_1/splitPolicy", {"x": 1}),
]


@pytest.mark.parametrize("tool,args,method,path,body", WRITES)
async def test_writes_refused_in_read_only_mode(monkeypatch, tool, args, method, path, body):
    rec = Recorder()
    out = await _call(monkeypatch, rec, tool, args | {"confirm_destructive_action": True})
    assert out["error"]["code"] == "not_configured"
    assert "READ_ONLY_MODE=false" in out["error"]["message"]
    assert rec.requests == []


@pytest.mark.parametrize("tool,args,method,path,body", WRITES)
async def test_writes_need_confirmation(monkeypatch, writable, tool, args, method, path, body):
    rec = Recorder()
    out = await _call(monkeypatch, rec, tool, args)
    assert out["done"] is False and out["action"]
    assert rec.requests == []


@pytest.mark.parametrize("tool,args,method,path,body", WRITES)
async def test_confirmed_writes(monkeypatch, writable, tool, args, method, path, body):
    rec = Recorder(default={})
    out = await _call(monkeypatch, rec, tool, args | {"confirm_destructive_action": True})
    assert "error" not in out
    request = rec.requests[0]
    assert (request.method, request.url.path) == (method, PREFIX + path)
    assert (json.loads(request.content) if request.content else None) == body


async def test_raw_get_runs_in_read_only_mode(monkeypatch):
    rec = Recorder({"organizations/1/admins": [{"id": "a"}]})
    out = await _call(monkeypatch, rec, "meraki_raw_request", {"method": "GET", "path": "/organizations/1/admins", "query": {"perPage": 3}})
    assert out == [{"id": "a"}]
    assert dict(rec.requests[0].url.params) == {"perPage": "3"}


@pytest.mark.parametrize("path", ["https://evil.example.net/x", "/organizations/../x", "/organizations?x=1", "//evil.example.net/x", "/a b"])
async def test_raw_rejects_non_relative_paths(monkeypatch, path):
    rec = Recorder()
    out = await _call(monkeypatch, rec, "meraki_raw_request", {"method": "GET", "path": path})
    assert out["error"]["code"] == "invalid_argument"
    assert rec.requests == []


async def test_group_policy_needs_id(monkeypatch, writable):
    rec = Recorder()
    out = await _call(
        monkeypatch, rec, "meraki_clients_update_policy", {"network_id": "N_1", "client_id": "k1", "device_policy": "Group policy", "confirm_destructive_action": True}
    )
    assert out["error"]["code"] == "invalid_argument"
    assert rec.requests == []


async def test_network_update_needs_a_field(monkeypatch, writable):
    out = await _call(monkeypatch, Recorder(), "meraki_networks_update", {"network_id": "N_1", "confirm_destructive_action": True})
    assert out["error"]["code"] == "invalid_argument"


@pytest.mark.parametrize("value,expected", [(None, True), ("false", False), ("0", False), ("off", False), ("true", True), ("yes", True)])
def test_read_only_mode_env(monkeypatch, value, expected):
    from meraki_mcp.config import read_only_mode

    monkeypatch.delenv("READ_ONLY", raising=False)
    if value is None:
        monkeypatch.delenv("READ_ONLY_MODE", raising=False)
    else:
        monkeypatch.setenv("READ_ONLY_MODE", value)
    assert read_only_mode() is expected


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, "https://api.meraki.com/api/v1"),
        ("https://api.meraki.cn/api/v1/", "https://api.meraki.cn/api/v1"),
        ("https://api.meraki.ca/api/v1", "https://api.meraki.ca/api/v1"),
        ("http://api.meraki.com/api/v1", None),
        ("https://api.meraki.com.example.net/api/v1", None),
    ],
)
def test_base_url_env(monkeypatch, value, expected):
    from meraki_mcp.config import api_base_url

    if value is None:
        monkeypatch.delenv("MERAKI_BASE_URL", raising=False)
    else:
        monkeypatch.setenv("MERAKI_BASE_URL", value)
    if expected is None:
        with pytest.raises(ValueError):
            create_mcp_server()
    else:
        assert api_base_url() == expected


@pytest.mark.parametrize("per_page,sent", [(None, "50"), (120, "120"), (1000, "200")])
async def test_page_size_default_and_cap(monkeypatch, per_page, sent):
    rec = Recorder({"organizations/1/networks": []})
    await _call(monkeypatch, rec, "meraki_networks_list", {"organization_id": "1"} | ({"per_page": per_page} if per_page else {}))
    assert rec.requests[0].url.params["perPage"] == sent



# ---- PRD-21073: whole-list tools continue by offset ----------------------------------------------------
# Checklist: a 54-port switch walked by next_offset returns every port once (cut along the way); offset
# inside the list; offset past the end; no offset returns the same records as before; limit; no retry hint
# for a page size these tools do not have.

SERIAL = "Q2JW-4JKM-J65L"
NET = "L_828099381482759220"


def _ports(n=54):
    return [{"portId": str(i), "name": f"Port {i}", "enabled": True, "type": "trunk", "vlan": 1,
             "allowedVlans": "all", **{f"setting{k}": f"value {k}" for k in range(30)}} for i in range(1, n + 1)]


WHOLE_LIST_TOOLS = [
    ("meraki_switch_ports_list", f"devices/{SERIAL}/switch/ports", "ports", {"serial": SERIAL}, "portId"),
    ("meraki_switch_port_statuses_list", f"devices/{SERIAL}/switch/ports/statuses", "ports", {"serial": SERIAL, "timespan": 3600}, "portId"),
    ("meraki_devices_list", f"networks/{NET}/devices", "devices", {"network_id": NET}, "portId"),
    ("meraki_wireless_rf_profiles_list", f"networks/{NET}/wireless/rfProfiles", "rf_profiles", {"network_id": NET}, "portId"),
    ("meraki_wireless_ssids_list", f"networks/{NET}/wireless/ssids", "ssids", {"network_id": NET}, "portId"),
    ("meraki_organizations_list", "organizations", "organizations", {}, "portId"),
]


@pytest.mark.parametrize("tool,path,key,args,idf", WHOLE_LIST_TOOLS)
async def test_cut_whole_lists_continue_by_next_offset(monkeypatch, tool, path, key, args, idf):
    rows = _ports(54)
    rec = Recorder({path: rows})
    ids, cut, call_args, calls = [], 0, dict(args), 0
    while True:
        out = await _call(monkeypatch, rec, tool, call_args)
        calls += 1
        assert "retry_page_size" not in out, out.get("note")
        cut += bool(out.get("truncated"))
        ids += [r[idf] for r in out[key]]
        if out.get("next_offset") is None:
            break
        call_args = args | {"offset": out["next_offset"]}
        assert calls < 60
    assert cut > 0  # 54 wide ports do not fit one response
    assert ids == [r[idf] for r in rows]  # all 54, no gaps, no duplicates


@pytest.mark.parametrize("tool,path,key,args,idf", WHOLE_LIST_TOOLS)
async def test_offset_limit_and_past_the_end(monkeypatch, tool, path, key, args, idf):
    rows = [{"portId": str(i)} for i in range(1, 55)]
    rec = Recorder({path: rows})
    middle = await _call(monkeypatch, rec, tool, args | {"offset": 20, "limit": 5})
    assert [r[idf] for r in middle[key]] == ["21", "22", "23", "24", "25"]
    assert (middle["offset"], middle["next_offset"], middle["total"], middle["has_more"]) == (20, 25, 54, True)
    past = await _call(monkeypatch, rec, tool, args | {"offset": 100})
    assert (past[key], past["has_more"], past.get("next_offset")) == ([], False, None)
    whole = await _call(monkeypatch, rec, tool, args)
    assert [r[idf] for r in whole[key]] == [r["portId"] for r in rows] and whole["count"] == 54


async def test_switch_ports_keep_their_fields(monkeypatch):
    rec = Recorder({f"devices/{SERIAL}/switch/ports": [{"portId": "1"}]})
    out = await _call(monkeypatch, rec, "meraki_switch_ports_list", {"serial": SERIAL})
    assert (out["serial"], out["count"], out["ports"]) == (SERIAL, 1, [{"portId": "1"}])


async def test_ssid_keys_stay_hidden_when_paged(monkeypatch):
    rec = Recorder({f"networks/{NET}/wireless/ssids": [{"number": 0, "psk": "secret"}, {"number": 1, "psk": "x"}]})
    out = await _call(monkeypatch, rec, "meraki_wireless_ssids_list", {"network_id": NET, "offset": 1})
    assert out["ssids"] == [{"number": 1, "psk": "********"}]


async def test_organizations_continue_to_the_next_meraki_page(monkeypatch):
    link = '<https://api.meraki.com/api/v1/organizations?perPage=1000&startingAfter=O_2>; rel=next'

    def reply(request):
        if request.url.params.get("startingAfter") == "O_2":
            return httpx.Response(200, json=[{"id": "O_3"}])
        return httpx.Response(200, json=[{"id": "O_1"}, {"id": "O_2"}], headers={"Link": link})

    rec = Recorder({"organizations": reply})
    first = await _call(monkeypatch, rec, "meraki_organizations_list", {"limit": 1})
    assert ([o["id"] for o in first["organizations"]], first["next_offset"]) == (["O_1"], 1) and "next_cursor" not in first
    end_of_page = await _call(monkeypatch, rec, "meraki_organizations_list", {"offset": 1})
    assert [o["id"] for o in end_of_page["organizations"]] == ["O_2"]
    assert end_of_page["has_more"] is True and end_of_page["next_cursor"] == "O_2" and "next_offset" not in end_of_page
    second = await _call(monkeypatch, rec, "meraki_organizations_list", {"starting_after": "O_2"})
    assert [o["id"] for o in second["organizations"]] == ["O_3"] and second["has_more"] is False


# ---- PRD-21074: 404 on an organization, raw GET truncation -------------------------------------------------


@pytest.mark.parametrize(
    "path,explained",
    [("organizations/1667841/networks", True), ("organizations/1667841", True), ("networks/L_1/devices", False)],
)
async def test_404_on_an_organization_explains_the_likely_cause(monkeypatch, path, explained):
    rec = Recorder({path: lambda r: httpx.Response(404, json={"errors": ["Not found"]})})
    tool, args = {
        "organizations/1667841/networks": ("meraki_networks_list", {"organization_id": "1667841"}),
        "organizations/1667841": ("meraki_organizations_get", {"organization_id": "1667841"}),
        "networks/L_1/devices": ("meraki_devices_list", {"network_id": "L_1"}),
    }[path]
    out = await _call(monkeypatch, rec, tool, args)
    assert out["error"]["code"] == "not_found"
    assert ("API access disabled" in out["error"]["message"]) is explained


async def test_cut_raw_get_says_how_to_continue(monkeypatch):
    rows = [{"serial": f"S{i:03d}", **{f"f{k}": f"value {k}" for k in range(30)}} for i in range(196)]
    rec = Recorder({"organizations/1209397/devices/statuses": rows})
    out = await _call(monkeypatch, rec, "meraki_raw_request", {"method": "GET", "path": "/organizations/1209397/devices/statuses"})
    assert out["truncated"] is True and "retry_page_size" not in out
    assert 0 < len(out["items"]) < 196 and out["original_count"] == 196
    assert f"perPage={len(out['items'])}" in out["note"] and "startingAfter" in out["note"]


async def test_small_raw_get_is_unchanged(monkeypatch):
    rec = Recorder({"organizations/1/networks": [{"id": "L_1"}]})
    out = await _call(monkeypatch, rec, "meraki_raw_request", {"method": "GET", "path": "/organizations/1/networks"})
    assert out == [{"id": "L_1"}]
