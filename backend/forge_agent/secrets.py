from __future__ import annotations

import base64
import binascii
import json
import os
import secrets
import tempfile
from pathlib import Path

SIGNING_KEY_FILE = "permission-signing-key"


class SecretStore:
    """Store model credentials and the permission signing key in private files.

    The data directory is the protection boundary: the settings directory is
    0700 and secret files are 0600. Environment variables always take precedence
    for model credentials, which is the recommended approach in containers.
    """

    def __init__(self, settings_dir: Path):
        self.path = settings_dir / "secrets.json"
        self.signing_key_path = settings_dir / SIGNING_KEY_FILE

    def permission_signing_key(self) -> bytes | None:
        """Return a stable signing key used to match persisted approval rules."""
        return self._file_signing_key()

    def _file_signing_key(self) -> bytes:
        self.signing_key_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            encoded = self.signing_key_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            encoded = ""
        if not encoded:
            encoded = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
            self._write_private_file(self.signing_key_path, encoded + "\n")
        else:
            self._restrict_permissions(self.signing_key_path)
        return self._decode_signing_key(encoded)

    @staticmethod
    def _decode_signing_key(encoded: str) -> bytes:
        try:
            key = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise RuntimeError("permission signing key is invalid") from exc
        if len(key) < 32:
            raise RuntimeError("permission signing key is too short")
        return key

    def get(self, provider: str, *, allow_environment: bool = True) -> str:
        env = ""
        if allow_environment:
            env = os.getenv(f"FORGEAGENT_{provider.upper()}_API_KEY") or os.getenv(f"KORENEXT_{provider.upper()}_API_KEY", "")
        if env:
            return env
        value = self._read_file().get(provider, "")
        if isinstance(value, dict):
            return str(value.get("value") or "") if value.get("encoding") in {"file", "plaintext"} else ""
        return value if isinstance(value, str) else ""

    def source(self, provider: str) -> str:
        if os.getenv(f"FORGEAGENT_{provider.upper()}_API_KEY") or os.getenv(f"KORENEXT_{provider.upper()}_API_KEY"):
            return "environment"
        value = self._read_file().get(provider)
        if isinstance(value, dict):
            return "file" if value.get("encoding") in {"file", "plaintext"} else "none"
        return "file" if value else "none"

    def set(self, provider: str, secret: str) -> None:
        data = self._read_file()
        data[provider] = {"encoding": "file", "value": secret}
        self._write_file(data)

    def _read_file(self) -> dict:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return {}
        self._restrict_permissions(self.path)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _write_file(self, payload: dict) -> None:
        self._write_private_file(self.path, json.dumps(payload, ensure_ascii=False))

    def _write_private_file(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._restrict_permissions(path.parent, 0o700)
        handle, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
        try:
            os.fchmod(handle, 0o600)
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(content)
            os.replace(temporary, path)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
        self._restrict_permissions(path, 0o600)

    @staticmethod
    def _restrict_permissions(path: Path, mode: int = 0o600) -> None:
        try:
            path.chmod(mode)
        except OSError:
            pass
