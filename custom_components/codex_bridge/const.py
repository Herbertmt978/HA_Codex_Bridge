DOMAIN = "codex_bridge"

# Integration-to-Bridge protocol contract. Keep this independent from component
# release versions so Supervisor discovery can reject incompatible updates.
API_CURRENT = 1
API_MINIMUM = 1
API_MAXIMUM = 1
LEGACY_API_VERSION = 0
BRIDGE_API_HEADER = "X-Codex-Bridge-Api"
BRIDGE_PROBLEM_BODY_MAX_BYTES = 16 * 1024
BRIDGE_EVENT_BATCH_MAX_BYTES = 8 * 1024 * 1024
BRIDGE_EVENT_BATCH_LIMIT = 256
BRIDGE_EVENT_CURSOR_MAX = 2**63 - 1
BRIDGE_EVENT_WAIT_SECONDS = 15

DISCOVERY_SOURCE = "hassio"
DISCOVERY_SERVICE = "codex_bridge"
DISCOVERY_SLUG_SUFFIX = "codex_bridge"
BRIDGE_TOKEN_MIN_LENGTH = 32
BRIDGE_TOKEN_MAX_LENGTH = 512

BRIDGE_TIMEOUT_TOTAL_SECONDS = 30
BRIDGE_TIMEOUT_POOL_SECONDS = 10
BRIDGE_TIMEOUT_CONNECT_SECONDS = 10
BRIDGE_TIMEOUT_READ_SECONDS = 20
# Plugin catalogues are populated by a cold Codex app-server request. Keep the
# Integration responsive beyond the App's bounded 60-second request limit
# without relaxing the shorter timeout applied to every other Bridge endpoint.
BRIDGE_PLUGIN_LIST_MAX_BYTES = 8 * 1024 * 1024
BRIDGE_PLUGIN_LIST_TIMEOUT_TOTAL_SECONDS = 75
BRIDGE_PLUGIN_LIST_TIMEOUT_READ_SECONDS = 70

CONF_BRIDGE_URL = "bridge_url"
CONF_BRIDGE_TOKEN = "bridge_token"
CONF_CONNECTION_TYPE = "connection_type"
CONF_DISCOVERY_UUID = "discovery_uuid"
CONF_WEB_SEARCH_MODE = "web_search_mode"

CONNECTION_TYPE_SUPERVISOR = "supervisor"
CONNECTION_TYPE_EXTERNAL_LEGACY = "external_legacy"

WEB_SEARCH_CAPABILITY = "web_search_v1"
WEB_SEARCH_MODE_DISABLED = "disabled"
WEB_SEARCH_MODE_LIVE = "live"

DEFAULT_BRIDGE_URL = "http://127.0.0.1:8766"

PANEL_COMPONENT_NAME = "codex-bridge-panel"
PANEL_URL_PATH = "codex-bridge"
PANEL_ICON = "mdi:robot-outline"
STATIC_URL_BASE = "/codex_bridge_static"
PANEL_ASSET_VERSION = "0.7.3"
PANEL_MODULE_URL = f"{STATIC_URL_BASE}/codex-bridge-panel.js?v={PANEL_ASSET_VERSION}"

DATA_ENTRIES = "entries"
DATA_PANEL_REGISTERED = "panel_registered"
DATA_VIEWS_REGISTERED = "views_registered"
DATA_WS_REGISTERED = "ws_registered"
EVENT_CURSOR_STORAGE_VERSION = 1
