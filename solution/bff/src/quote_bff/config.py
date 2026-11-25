"""BFF configuration, from environment variables only (README has the table).

An empty URL or endpoint selects the in-process fake or in-memory adapter, so the BFF runs and tests with no
network and no Azure. Local values are obviously fake (``dev-only-...``); real keys come from Key Vault.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Every setting of the BFF; each field is read from the environment variable of the same name (upper case)."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore")  # environment only, no .env file; extras ignored

    # Keys: HMAC for session_ref (shared with mcp-quoting) and for the qsid cookie.
    session_ref_key: str = "dev-only-local-key"  # mcp-quoting README uses the same
    session_cookie_key: str = "dev-only-session-cookie-key"
    session_ref_ttl_seconds: int = 900  # lifetime of a signed session_ref: 15 minutes
    cookie_secure: bool = True  # False only for plain-http local dev
    cors_origins: str = "http://localhost:5173"  # comma-separated web origins, credentials allowed

    # Hosted agent: a local URL (http://localhost:8088), else the Foundry project endpoint, else the fake agent.
    hosted_agent_url: str = ""
    foundry_project_endpoint: str = ""  # also the base of the document agent (Responses API)
    hosted_agent_name: str = "quote-agent"  # agent name in the Foundry project
    document_agent_name: str = "document-agent"  # used only with foundry_project_endpoint; else the fake extractor

    # MCP servers (streamable HTTP). Empty: in-process fakes. The scope enables Entra tokens (Azure only).
    a2ui_mcp_url: str = ""
    a2ui_mcp_scope: str = ""
    quoting_mcp_url: str = ""
    quoting_mcp_scope: str = ""

    # Storage. Empty endpoint/account: in-memory store and a local folder.
    cosmos_endpoint: str = ""
    cosmos_database: str = "quoting"  # holds the containers listed in stores.CONTAINERS
    blob_account_url: str = ""
    blob_container: str = "uploads"
    local_upload_dir: str = ""  # default: a temp folder

    max_message_chars: int = 2000  # longest user.message accepted (422 above)
    max_upload_bytes: int = 10 * 1024 * 1024  # largest upload accepted (413 above)
    log_level: str = "INFO"
    otel_enabled: bool = False  # True: also export logs and traces to Azure Monitor (needs the telemetry extra)

    @property
    def cors_origin_list(self) -> list[str]:
        """The ``cors_origins`` setting as a list: split on commas, blanks dropped."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]
