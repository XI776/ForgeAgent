import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pytest

from forge_agent import config
from forge_agent import secrets as secret_module


def test_legacy_configuration_fallback(monkeypatch):
    monkeypatch.delenv("FORGEAGENT_MODEL", raising=False)
    monkeypatch.setenv("KORENEXT_MODEL", "previous-model")
    assert config.env("FORGEAGENT_MODEL", "default") == "previous-model"
    monkeypatch.setenv("FORGEAGENT_MODEL", "new-model")
    assert config.env("FORGEAGENT_MODEL", "default") == "new-model"


def test_existing_data_is_copied_to_new_directory(tmp_path: Path, monkeypatch):
    old_dir = tmp_path / ".koreagent-next"
    new_dir = tmp_path / ".forgeagent"
    old_dir.mkdir()
    (old_dir / "artifacts").mkdir()
    (old_dir / "artifacts" / "existing.txt").write_text("preserved", encoding="utf-8")
    with closing(sqlite3.connect(old_dir / "koreagent-next.db")) as database:
        database.execute("CREATE TABLE sample(value TEXT)")
        database.execute("INSERT INTO sample VALUES ('preserved')")
        database.commit()
    monkeypatch.setattr(config, "DATA_DIR", new_dir)
    monkeypatch.setattr(config, "LEGACY_DATA_DIR", old_dir)
    settings = config.Settings(data_dir=new_dir, workspace_dir=tmp_path / "workspace", user_skills_dir=tmp_path / "skills", api_key="")
    settings.ensure_dirs()
    assert (new_dir / "artifacts" / "existing.txt").read_text(encoding="utf-8") == "preserved"
    with closing(sqlite3.connect(settings.database)) as database:
        assert database.execute("SELECT value FROM sample").fetchone() == ("preserved",)
    assert (old_dir / "koreagent-next.db").is_file()
    settings.ensure_dirs()


def test_custom_data_directory_keeps_existing_database(tmp_path: Path):
    directory = tmp_path / "custom-data"
    directory.mkdir()
    with closing(sqlite3.connect(directory / "koreagent-next.db")) as database:
        database.execute("CREATE TABLE sample(value TEXT)")
        database.execute("INSERT INTO sample VALUES ('preserved')")
        database.commit()
    settings = config.Settings(data_dir=directory, workspace_dir=tmp_path / "workspace", user_skills_dir=tmp_path / "skills", api_key="")
    settings.ensure_dirs()
    with closing(sqlite3.connect(settings.database)) as database:
        assert database.execute("SELECT value FROM sample").fetchone() == ("preserved",)
    assert (directory / "koreagent-next.db").is_file()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI only")
def test_legacy_dpapi_secret_can_be_read(monkeypatch):
    current_service = secret_module.SERVICE
    monkeypatch.setattr(secret_module, "SERVICE", secret_module.LEGACY_SERVICE)
    encrypted = secret_module.SecretStore._dpapi_protect("existing-token")
    monkeypatch.setattr(secret_module, "SERVICE", current_service)
    assert secret_module.SecretStore._dpapi_unprotect(encrypted) == "existing-token"
