# Cisco Meraki MCP Server

An [MCP](https://modelcontextprotocol.io) server for the [Cisco Meraki](https://meraki.cisco.com)
Dashboard API (v1), the cloud-managed networking platform (security appliances, switches, wireless
access points) MSPs run for their clients. It lets an AI agent browse organizations, networks,
devices and connected clients, read switch ports, SSIDs, RF profiles, firewall rules and VPN
settings, and - when an operator allows it - change them, reboot or remove devices, or call any
other Dashboard API endpoint.

- Transport: MCP Streamable HTTP at `POST /mcp`, health probe at `GET /health`
- Stateless and multi-tenant: credentials arrive on every request as HTTP headers and are
  never stored; every tool is listed from the first `tools/list`
- Read-only by default: every change is refused until the operator sets `READ_ONLY_MODE=false`,
  and even then each one needs `confirm_destructive_action: true`
- Python 3.12, [uv](https://docs.astral.sh/uv/), FastMCP, httpx
- License: Apache-2.0, Copyright 2026 MSPbots.ai

## Authentication headers

| Header | Required | Type | Default | Allowed values | Description | Example |
|---|---|---|---|---|---|---|
| `X-Meraki-Api-Key` | Yes | string | none | - | Dashboard API key | `your-api-key` |
| `X-Meraki-Org-Id` | No | string | none | letters, digits, `_`, `-` | Default organization, reported by `meraki_status` | `549236` |

Where to get them: enable API access under Organization > Settings in the Meraki dashboard, then
generate a key under My profile > API access. The key has the same permissions as the dashboard
user who created it (a read-only admin gives a read-only key). The organization id is in
`meraki_organizations_list`.

How the key is sent: every call goes to `https://api.meraki.com/api/v1/...` with
`Authorization: Bearer <key>`. Meraki may redirect to the organization's own shard host; those
redirects are followed only to Meraki hosts, keeping the key.

A request to `/mcp` without the key gets HTTP 401; a malformed organization id gets HTTP 400.

## Tools

All tools return a JSON string. Paged lists take `per_page` (default 50, max 200; larger values are capped) and `starting_after`, and
return `{count, has_more, next_cursor}`; pass `next_cursor` back as `starting_after`.

| Area | Tools |
|---|---|
| Overview | `meraki_navigate` (**domain**), `meraki_status` |
| Organizations | `meraki_organizations_list`, `meraki_organizations_get` (**organization_id**), `meraki_organizations_inventory_list` (**organization_id**) |
| Networks | `meraki_networks_list` (**organization_id**), `meraki_networks_get` (**network_id**), `meraki_networks_update` (**network_id**, name, timeZone, tags, notes) W, `meraki_networks_delete` (**network_id**) W |
| Devices | `meraki_devices_list` (**network_id**), `meraki_devices_get` (**serial**), `meraki_devices_reboot` (**serial**) W, `meraki_devices_remove` (**network_id**, **serial**) W |
| Clients | `meraki_clients_list` (**network_id**, timespan), `meraki_clients_get` / `meraki_clients_get_policy` (**network_id**, **client_id**), `meraki_clients_update_policy` (**network_id**, **client_id**, **device_policy**, group_policy_id) W |
| Switch | `meraki_switch_ports_list` (**serial**), `meraki_switch_port_statuses_list` (**serial**, timespan), `meraki_switch_ports_update` (**serial**, **port_id**, **settings**) W |
| Wireless | `meraki_wireless_ssids_list` (**network_id**), `meraki_wireless_rf_profiles_list` (**network_id**), `meraki_wireless_ssids_update` (**network_id**, **number**, **settings**) W |
| Appliance | `meraki_appliance_firewall_l3_get` (**network_id**), `meraki_appliance_firewall_l3_update` (**network_id**, **rules**) W, `meraki_appliance_vpn_status_get` (**network_id**) |
| Any endpoint | `meraki_raw_request` (**method**, **path**, query, body); GET reads, POST / PUT / DELETE are W |

**Bold** parameters are required. W marks a change: it is refused in read-only mode and returns
`{"done": false, "action": ...}` until it is called again with `confirm_destructive_action: true`
after the user approved that exact change. Pre-shared SSID keys are masked in results.

Errors come back as a JSON envelope instead of an exception:

```json
{"error": {"code": "unauthorized", "message": "Meraki returned HTTP 401 for organizations", "retryable": false}}
```

`code` is one of `not_configured`, `unauthorized`, `not_found`, `invalid_argument`,
`rate_limited`, `upstream_error`; a change refused in read-only mode returns `not_configured`. Reads are retried with backoff on 429, 5xx
and network errors; changes only on 429, so a change never runs twice. Meraki allows about 10
calls per second per organization.

## Run locally

```bash
uv sync
uv run python -m meraki_mcp          # listens on 0.0.0.0:8080
```

or with Docker:

```bash
docker build --platform linux/amd64 -t meraki-mcp:dev .
docker run --rm -p 8080:8080 meraki-mcp:dev
```

Configuration (environment variables, all optional): `MCP_HTTP_HOST` (default `0.0.0.0`),
`MCP_HTTP_PORT` (default `8080`), `LOG_LEVEL` (default `INFO`), `READ_ONLY_MODE` (default
`true`; `false` allows changes), `MERAKI_BASE_URL` (default `https://api.meraki.com/api/v1`; for
the China, Canada or India clouds, e.g. `https://api.meraki.cn/api/v1`, Meraki hosts only).
Credentials are never read from the environment.

## Testing example

```bash
# list tools
curl -s http://localhost:8080/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "X-Meraki-Api-Key: your-api-key" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'

# networks of an organization
curl -s http://localhost:8080/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "X-Meraki-Api-Key: your-api-key" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"meraki_networks_list","arguments":{"organization_id":"549236"}}}'
```

Unit tests (no network access needed):

```bash
uv run pytest
```

## Delivery notes

- **Tool count (27).** Kept as is because the tool names are an existing contract; every earlier
  parameter is still accepted with the same required set.
- **API source.** The official Meraki Dashboard API OpenAPI specification
  (`github.com/meraki/openapi`, v1); every route was checked against it.
- **Safety model kept.** Read-only by default (`READ_ONLY_MODE` / `READ_ONLY`), and every change
  needs `confirm_destructive_action: true`, as before. `meraki_raw_request` accepts only a
  relative Dashboard API path (no host, `//`, `..` or query string - use `query`).
- **New optional parameter**: `starting_after` on `meraki_clients_list`, so its pages can be
  followed like the other lists.
- **Redirects** to the organization's shard host are followed by hand (HTTP clients drop the
  `Authorization` header on a cross-host redirect), only to Meraki hosts.
- **Not included**: the device "card" UI resource of older versions (an MCP Apps HTML view);
  `meraki_devices_get` returns the same data as JSON. Credentials from environment variables
  (`MERAKI_API_KEY` / `MERAKI_ORG_ID`) are not supported: they would be shared by every caller.

## License

Apache License 2.0 - see [LICENSE](LICENSE) and [NOTICE](NOTICE).
