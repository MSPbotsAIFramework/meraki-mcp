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
"""Process settings. Holds no credentials: those arrive per request as headers."""

import os
import re
from urllib.parse import urlparse

from pydantic_settings import BaseSettings, SettingsConfigDict

API_BASE_URL = "https://api.meraki.com/api/v1"
MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 50
# Earlier versions accepted up to 1000 per page; such values are still accepted and capped at MAX_PAGE_SIZE.
ACCEPTED_PAGE_SIZE = 1000

# Meraki dashboard hosts: the global cloud (meraki.com) and the China, Canada and India clouds.
MERAKI_HOST = re.compile(r"^(?:[a-z0-9-]+\.)*meraki\.(?:com|cn|ca|in)$")


class Settings(BaseSettings):
    # extra="ignore": the runtime injects variables this project does not declare.
    model_config = SettingsConfigDict(extra="ignore")

    mcp_http_port: int = 8080
    mcp_http_host: str = "0.0.0.0"
    log_level: str = "INFO"


def get_settings() -> Settings:
    return Settings()


def read_only_mode() -> bool:
    """Writes are refused unless READ_ONLY_MODE is explicitly false / 0 / no / off (default: read-only)."""
    raw = (os.environ.get("READ_ONLY_MODE") or os.environ.get("READ_ONLY") or "").strip()
    if not raw:
        return True
    return raw.lower() not in ("false", "0", "no", "off")


def api_base_url() -> str:
    """MERAKI_BASE_URL for a deployment on another Meraki cloud, e.g. https://api.meraki.cn/api/v1."""
    raw = (os.environ.get("MERAKI_BASE_URL") or "").strip().rstrip("/")
    if not raw:
        return API_BASE_URL
    parsed = urlparse(raw)
    if parsed.scheme != "https" or not MERAKI_HOST.match(parsed.hostname or ""):
        raise ValueError("MERAKI_BASE_URL must be an https URL on a Meraki host, e.g. https://api.meraki.cn/api/v1")
    return raw
