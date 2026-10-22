from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
DATA_DIR = ROOT / ".forgeagent"
LEGACY_DATA_DIR = ROOT / ".koreagent-next"


def env(name: str, default: str = "") -> str:
    suffix = name.removeprefix("FORGEAGENT_")
    return os.getenv(name) or os.getenv(f"KORENEXT_{suffix}") or default


def _path(name: str, default: Path) -> Path:
    raw = env(name).strip()
    return Path(raw).expanduser().resolve() if raw else default.resolve()


def _user_skills_dir() -> Path:
    current = Path.home() / ".forgeagent" / "skills"
    legacy = Path.home() / ".koreagent-next" / "skills"
    return current if current.exists() or not legacy.exists() else legacy


@dataclass(frozen=True)
class Settings:
    provider: str = env("FORGEAGENT_PROVIDER", "openai")
    model: str = env("FORGEAGENT_MODEL", "gpt-4.1-mini")
    api_key: str = env("FORGEAGENT_API_KEY")
    base_url: str = env("FORGEAGENT_BASE_URL", "https://api.openai.com/v1")
    search_provider: str = env("FORGEAGENT_SEARCH_PROVIDER", "auto")
    tavily_api_key: str = env("FORGEAGENT_TAVILY_API_KEY")
    http_allow_private_network: bool = env("FORGEAGENT_HTTP_ALLOW_PRIVATE_NETWORK", "false").lower() == "true"
    host: str = env("FORGEAGENT_HOST", "127.0.0.1")
    port: int = int(env("FORGEAGENT_PORT", "8765"))
    data_dir: Path = _path("FORGEAGENT_DATA_DIR", DATA_DIR)
    user_skills_dir: Path = _path("FORGEAGENT_USER_SKILLS_DIR", _user_skills_dir())
    workspace_dir: Path = _path("FORGEAGENT_WORKSPACE_DIR", ROOT / "workspace")
    web_dir: Path = _path("FORGEAGENT_WEB_DIR", ROOT.parent / "desktop" / "dist")
    trusted_clients: str = env("FORGEAGENT_TRUSTED_CLIENTS", "127.0.0.0/8,::1/128,localhost,testclient")
    allowed_origins: str = env("FORGEAGENT_ALLOWED_ORIGINS")
    sandbox_backend: str = env("FORGEAGENT_SANDBOX_BACKEND", "auto").lower()
    max_steps: int = int(env("FORGEAGENT_MAX_STEPS", "12"))
    run_warning_tokens: int = int(env("FORGEAGENT_RUN_WARNING_TOKENS", "80000"))
    run_finalization_tokens: int = int(env("FORGEAGENT_RUN_FINALIZATION_TOKENS", "100000"))
    run_hard_tokens: int = int(env("FORGEAGENT_RUN_HARD_TOKENS", "120000"))
    run_warning_model_calls: int = int(env("FORGEAGENT_RUN_WARNING_MODEL_CALLS", "16"))
    run_finalization_model_calls: int = int(env("FORGEAGENT_RUN_FINALIZATION_MODEL_CALLS", "20"))
    run_hard_model_calls: int = int(env("FORGEAGENT_RUN_HARD_MODEL_CALLS", "24"))

    @property
    def database(self) -> Path:
        return self.data_dir / "forgeagent.db"

    @property
    def sandbox_home(self) -> Path:
        raw = env("FORGEAGENT_SANDBOX_HOME").strip()
        return Path(raw).expanduser().resolve() if raw else self.data_dir / "sandbox-home"

    def ensure_dirs(self) -> None:
        if self.data_dir == DATA_DIR.resolve() and not DATA_DIR.exists() and LEGACY_DATA_DIR.is_dir():
            with tempfile.TemporaryDirectory(prefix="forgeagent-migration-", dir=DATA_DIR.parent) as temporary:
                destination = Path(temporary) / DATA_DIR.name
                shutil.copytree(LEGACY_DATA_DIR, destination, ignore=shutil.ignore_patterns("*.db", "*.db-wal", "*.db-shm"))
                old_database = LEGACY_DATA_DIR / "koreagent-next.db"
                if old_database.is_file():
                    with closing(sqlite3.connect(old_database)) as source, closing(sqlite3.connect(destination / "forgeagent.db")) as target:
                        source.backup(target)
                destination.rename(DATA_DIR)
        old_database = self.data_dir / "koreagent-next.db"
        if old_database.is_file() and not self.database.exists():
            with closing(sqlite3.connect(old_database)) as source, closing(sqlite3.connect(self.database)) as target:
                source.backup(target)
        for path in (
            self.data_dir,
            self.workspace_dir,
            self.sandbox_home,
            self.data_dir / "artifacts",
            self.data_dir / "memory",
            self.data_dir / "skills",
            self.data_dir / "settings",
        ):
            path.mkdir(parents=True, exist_ok=True)
