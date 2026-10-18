
"""
Credential Storage.
Support OS keychain with fallback to mode-0600 file.
"""

from dataclasses    import dataclass
from pathlib        import Path
from hg.config      import write_private
from hg.errors      import ToolError
from hg             import style
import json
import time


SERVICE = "hg-toolkit"
# Refresh an access token this many seconds before it expires.
REFRESH_SKEW = 60


@dataclass
class Secret:
    refresh_token:      str
    access_token:       str
    expires_at:         float

    def fresh(self, now: float | None = None) -> bool:
        current = time.time() if now is None else now
        return self.expires_at - REFRESH_SKEW > current

    def to_json(self) -> str:
        return json.dumps(
            {
                "refresh_token":    self.refresh_token,
                "access_token":     self.access_token,
                "expires_at":       self.expires_at,
            }
        )

    @classmethod
    def from_json(cls, raw: str) -> "Secret":
        try:
            data = json.loads(raw)
            expires_at = float(data["expires_at"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ToolError("stored credentials are malformed; run hg login again") from exc

        return cls(
            refresh_token = data.get("refresh_token", ""),
            access_token  = data.get("access_token",  ""),
            expires_at    = expires_at,
        )


def keyring_available() -> bool:
    """check the availability of keyring"""
    try:
        import keyring
        from keyring.backends.fail import Keyring as FailKeyring
    except Exception:
        return False

    try:
        backend = keyring.get_keyring()
    except Exception:
        return False
    return not isinstance(backend, FailKeyring)


class CredentialStore:
    def __init__(self, directory: Path, *, keyring_module: object | None = None, force_file: bool = False):
        self.directory  = directory
        self._keyring   = keyring_module
        self.force_file = force_file
        self._warned    = False

    def _file(self) -> Path:
        return self.directory / "credentials.json"

    def _use_keyring(self) -> bool:
        if self.force_file:
            return False
        if self._keyring is not None:
            return True
        return keyring_available()

    def _warn(self) -> None:
        if self._warned:
            return
        self._warned = True
        style.warn("no OS keychain; storing the staff token in credentials.json")

    def _read_file(self) -> dict:
        path = self._file()
        if not path.is_file():
            return {}

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

        if not isinstance(data, dict):
            return {}
        return data

    def _write_file(self, data: dict) -> None:
        write_private(self._file(), json.dumps(data, indent=2) + "\n")

    def _keyring_module(self):
        if self._keyring is not None:
            return self._keyring
        import keyring
        return keyring

    def load(self, account: str) -> Secret | None:
        """load the secret/credentials from keyring or file depends on availability"""
        raw: str | None = None
        if self._use_keyring():
            try:
                raw = self._keyring_module().get_password(SERVICE, account)
            except Exception:
                raw = None

        if not raw:
            stored = self._read_file().get(account)
            if isinstance(stored, str):
                raw = stored
            elif isinstance(stored, dict):
                raw = json.dumps(stored)

        if not raw:
            return None
        return Secret.from_json(raw)

    def save(self, account: str, secret: Secret) -> None:
        """save the secret/credentials into keyring or file depends on availability"""
        payload = secret.to_json()
        if self._use_keyring():
            try:
                self._keyring_module().set_password(SERVICE, account, payload)
                self._drop_file(account)
                return
            except Exception:
                pass
        self._warn()
        data = self._read_file()
        data[account] = json.loads(payload)
        self._write_file(data)

    def _drop_file(self, account: str) -> None:
        """Remove one account from the file fallback after the keychain took it."""
        data = self._read_file()
        if account not in data:
            return
        del data[account]
        if data:
            self._write_file(data)
        elif self._file().exists():
            self._file().unlink()

    def delete(self, account: str) -> None:
        """delete secret/credentials from keyring or file"""
        if self._use_keyring():
            try:
                self._keyring_module().delete_password(SERVICE, account)
            except Exception:
                pass

        data = self._read_file()
        if account not in data:
            return
        del data[account]
        if data:
            self._write_file(data)
        elif self._file().exists():
            self._file().unlink()
