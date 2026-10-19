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
"""Shared argument types, the write guard, paging and the error-handling wrapper."""

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from mcp.types import ToolAnnotations
from pydantic import Field

from .._json import dump_json_capped, error_envelope
from ..api_client import MerakiClient, MerakiError, Page
from ..config import MAX_PAGE_SIZE, read_only_mode

logger = logging.getLogger("meraki_mcp.tools")

ClientFactory = Callable[[], MerakiClient | None]

READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=True)
LOCAL = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True)

NOT_CONFIGURED = error_envelope("not_configured", "Meraki credentials are missing for this request (X-Meraki-Api-Key).", False)

OrganizationId = Annotated[str, Field(description="Meraki organization id (meraki_organizations_list).", min_length=1)]
NetworkId = Annotated[str, Field(description="Meraki network id, e.g. L_123... or N_123...", min_length=1)]
Serial = Annotated[str, Field(description="Device serial, e.g. Q2XX-XXXX-XXXX.", min_length=1)]
PerPage = Annotated[int | None, Field(description=f"Records per page (max {MAX_PAGE_SIZE}).", ge=1, le=MAX_PAGE_SIZE)]
StartingAfter = Annotated[str | None, Field(description="next_cursor from the previous page.")]
ConfirmDestructive = Annotated[
    bool | None, Field(description="Must be true, set only after the user approved this exact change.")
]


def compact(values: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in values.items() if v is not None and v != ""}


def write_guard(confirm: bool | None, action: str) -> str | dict | None:
    """None when the write may run; otherwise the refusal to return."""
    if read_only_mode():
        return error_envelope(
            "read_only_mode",
            "This server runs in read-only mode, so changes are disabled. An operator can set READ_ONLY_MODE=false to allow them.",
            False,
        )
    if confirm is not True:
        return {
            "done": False,
            "action": action,
            "message": "Not done. This change is applied on the live network: describe it to the user and call again "
            "with confirm_destructive_action: true only after they approve it.",
        }
    return None


def listing(page: Page, key: str) -> dict:
    rows = page.data if isinstance(page.data, list) else []
    return compact({key: rows, "count": len(rows), "has_more": bool(page.next_cursor), "next_cursor": page.next_cursor})


async def run_tool(
    name: str,
    client_factory: ClientFactory,
    action: Callable[[MerakiClient], Awaitable[Any]],
) -> str:
    """Resolve the caller's client, run ``action``, and always return a string.

    Errors never escape as exceptions: they become the standard error envelope.
    """
    client = client_factory()
    if client is None:
        return NOT_CONFIGURED
    started = time.monotonic()
    try:
        value = await action(client)
    except MerakiError as e:
        logger.info("tool=%s outcome=error status=%s ms=%d", name, e.status_code, _ms(started))
        return e.to_envelope()
    except ValueError as e:
        logger.info("tool=%s outcome=invalid_argument ms=%d", name, _ms(started))
        return error_envelope("invalid_argument", str(e), False)
    except Exception as e:  # noqa: BLE001 - tools must never raise
        logger.warning("tool=%s outcome=unexpected error=%s ms=%d", name, type(e).__name__, _ms(started))
        return error_envelope("upstream_error", f"Unexpected error: {type(e).__name__}", True)
    logger.info("tool=%s outcome=ok ms=%d", name, _ms(started))
    if value is None:
        value = {"done": True}
    return value if isinstance(value, str) else dump_json_capped(value)


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
