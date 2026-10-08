"""環境変数から設定を読み込む。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    return int(value) if value else default


@dataclass(frozen=True)
class Settings:
    claude_model: str
    claude_effort: str
    claude_fallbacks: bool

    tenant_id: str
    client_id: str
    client_secret: str
    site_url: str

    sync_interval_minutes: int
    search_top_k: int
    full_context_max_chars: int
    data_dir: Path

    company_name: str
    fallback_contact: str

    @property
    def sharepoint_configured(self) -> bool:
        return all([self.tenant_id, self.client_id, self.client_secret, self.site_url])


def load_settings() -> Settings:
    return Settings(
        claude_model=os.getenv("CLAUDE_MODEL", "claude-opus-5-5"),
        claude_effort=os.getenv("CLAUDE_EFFORT", "low"),
        claude_fallbacks=os.getenv("CLAUDE_FALLBACKS", "true").lower() != "false",
        tenant_id=os.getenv("AZURE_TENANT_ID", ""),
        client_id=os.getenv("AZURE_CLIENT_ID", ""),
        client_secret=os.getenv("AZURE_CLIENT_SECRET", ""),
        site_url=os.getenv("SHAREPOINT_SITE_URL", "").rstrip("/"),
        sync_interval_minutes=_int("SYNC_INTERVAL_MINUTES", 60),
        search_top_k=_int("SEARCH_TOP_K", 8),
        full_context_max_chars=_int("FULL_CONTEXT_MAX_CHARS", 60000),
        data_dir=Path(os.getenv("DATA_DIR", "data")),
        company_name=os.getenv("COMPANY_NAME", "当社"),
        fallback_contact=os.getenv("FALLBACK_CONTACT", "担当部署"),
    )


settings = load_settings()
