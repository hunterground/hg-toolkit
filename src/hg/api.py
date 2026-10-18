"""
HunterGround API client for Backend service.
"""

from collections.abc    import Callable, Mapping
from pathlib            import Path
from typing             import Any
from hg.config          import Profile, config_home, load_profile, resolve_origin, save_profile
from hg.credentials     import CredentialStore, Secret
from hg.errors          import ToolError
import json
import os
import ssl
import threading
import time
import urllib.error
import urllib.request
import certifi


Transport = Callable[[str, str, dict[str, str], bytes | None], tuple[int, bytes]]


class APIError(ToolError):
    def __init__(self, status: int, code: str, message: str):
        self.status = status
        self.code   = code
        super().__init__(message or code or f"HTTP {status}")


class NotFound(APIError):
    pass


def _ssl_context() -> ssl.SSLContext:
    """
    Build the TLS context for platform calls.

    Python does not use the macOS system trust store, and a python.org build
    ships without a CA bundle, so the default context fails every https call
    with "unable to get local issuer certificate". certifi carries the roots
    a stock interpreter is missing, which is why it is a hard dependency.
    """
    return ssl.create_default_context(cafile=certifi.where())

# One context is enough for the whole process. Building it reads the bundle,
# so reuse it instead of paying that cost on every request.
_SSL_CONTEXT: ssl.SSLContext | None = None
_SSL_CONTEXT_LOCK                   = threading.Lock()

def _tls_context() -> ssl.SSLContext:
    global _SSL_CONTEXT
    if _SSL_CONTEXT is None:
        with _SSL_CONTEXT_LOCK:
            if _SSL_CONTEXT is None:
                _SSL_CONTEXT = _ssl_context()
    return _SSL_CONTEXT


def _decode(status: int, raw: bytes) -> Any:
    if not raw:
        if status >= 400:
            raise APIError(status, "", f"HTTP {status}")
        return None

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise APIError(status, "", "API response was not JSON") from exc

    if not isinstance(payload, dict) or not payload.get("success"):
        err  = payload.get("error") if isinstance(payload, dict) else None
        code = ""
        message = f"HTTP {status}"
        if isinstance(err, dict):
            code    = err.get("code", "")
            message = err.get("message", message)
        if status == 404 or code == "NOT_FOUND":
            raise NotFound(status or 404, code, message)
        raise APIError(status, code, message)
    return payload.get("data")


def urllib_transport(method: str, url: str, headers: dict[str, str], body: bytes | None) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    context = _tls_context() if url.lower().startswith("https:") else None
    try:
        with urllib.request.urlopen(request, timeout=60, context=context) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except urllib.error.URLError as exc:
        raise ToolError(f"could not reach {url}: {exc.reason}") from exc


class Client:
    def __init__(self,
        base_url:       str,
        token:          str,
        *,
        transport:      Transport           | None = None,
        refresh:        Callable[[], str]   | None = None,
    ):
        self.base_url  = base_url.rstrip("/")
        self.token     = token
        self.transport = transport or urllib_transport
        self.refresh   = refresh

    def request(self,
        method:         str,
        path:           str,
        *,
        json_body:      object  | None = None,
        body:           bytes   | None = None,
        content_type:   str     | None = None,
    ) -> Any:
        retried = False
        while True:
            headers = {"Accept": "application/json"}
            if self.token:
                headers["Authorization"] = f"Bearer {self.token}"

            payload = body
            if json_body is not None:
                payload = json.dumps(json_body).encode()
                headers["Content-Type"] = "application/json"
            elif content_type:
                headers["Content-Type"] = content_type

            status, raw = self.transport(method, self.base_url + path, headers, payload)
            if status == 401 and self.refresh is not None and not retried:
                self.token = self.refresh()
                retried = True
                continue
            return _decode(status, raw)


class Session:
    """A logged-in staff session. CI may set HG_API_URL and HG_ACCESS_TOKEN instead."""

    def __init__(self,
        *,
        url:            str,
        email:          str,
        directory:      Path,
        store:          CredentialStore,
        transport:      Transport           | None = None,
        env_token:      str = "",
    ):
        self.url       = url
        self.email     = email
        self.directory = directory
        self.store     = store
        self.transport = transport
        self.env_token = env_token

    @classmethod
    def open(cls,
        *,
        directory:      Path                | None = None,
        store:          CredentialStore     | None = None,
        transport:      Transport           | None = None,
        env:            Mapping[str, str]   | None = None,
    ) -> "Session":
        source = os.environ if env is None else env
        home  = config_home(source) if directory is None else directory
        creds = store or CredentialStore(home)
        token = source.get("HG_ACCESS_TOKEN", "").strip()
        if token:
            return cls(
                url       = resolve_origin(source),
                email     = "",
                directory = home,
                store     = creds,
                transport = transport,
                env_token = token,
            )
        profile = load_profile(home)
        if profile is None:
            raise ToolError("not logged in; run hg login")

        return cls(
            url       = resolve_origin(source, profile.url),
            email     = profile.email,
            directory = home,
            store     = creds,
            transport = transport,
        )

    @classmethod
    def blank(cls,
        *,
        directory:      Path                | None = None,
        store:          CredentialStore     | None = None,
        transport:      Transport           | None = None,
        env:            Mapping[str, str]   | None = None,
    ) -> "Session":
        source = os.environ if env is None else env
        home   = config_home(source) if directory is None else directory
        creds  = store or CredentialStore(home)
        return cls(url="", email="", directory=home, store=creds, transport=transport)

    def _refresh(self, secret: Secret) -> str:
        if not secret.refresh_token:
            raise ToolError("access token expired; run hg login")

        data = Client(self.url, "", transport=self.transport).request(
            "POST",
            "/api/v1/internal/auth/refresh",
            json_body = {"refresh_token": secret.refresh_token},
        )

        if not isinstance(data, dict) or not data.get("access_token"):
            raise ToolError("refresh response did not include an access token")

        expires_in = int(data.get("expires_in") or 0)
        if expires_in <= 0:
            raise ToolError("refresh response did not include expires_in")

        secret.access_token = str(data["access_token"])
        secret.expires_at = time.time() + expires_in
        self.store.save(self.url, secret)
        return secret.access_token

    def _refresh_for_client(self) -> str:
        secret = self.store.load(self.url)
        if secret is None:
            raise ToolError("not logged in; run hg login")
        return self._refresh(secret)


    ## Main API methods ---------------------------------------------------------------------------------
    def client(self) -> Client:
        refresh = None if self.env_token else self._refresh_for_client
        return Client(self.url, self.access_token(), transport=self.transport, refresh=refresh)

    def access_token(self) -> str:
        if self.env_token:
            return self.env_token

        secret = self.store.load(self.url)
        if secret is None or not secret.access_token:
            raise ToolError("not logged in; run hg login")
        if secret.fresh():
            return secret.access_token
        return self._refresh(secret)

    def login(self, url: str | None, email: str, password: str) -> dict:
        if not password:
            raise ToolError("password is required")

        origin = resolve_origin(os.environ, url.strip() if url else None)
        data = Client(origin, "", transport=self.transport).request(
            "POST",
            "/api/v1/internal/auth/login",
            json_body={"email": email, "password": password},
        )

        if not isinstance(data, dict) or not data.get("access_token") or not data.get("refresh_token"):
            raise ToolError("login response did not include tokens")

        expires_in = int(data.get("expires_in") or 0)
        if expires_in <= 0:
            raise ToolError("login response did not include expires_in")

        self.url   = origin
        self.email = email
        self.env_token = ""
        self.store.save(
            origin,
            Secret(
                refresh_token = str(data["refresh_token"]),
                access_token  = str(data["access_token"]),
                expires_at    = time.time() + expires_in,
            ),
        )
        save_profile(self.directory, Profile(url=origin, email=email))
        user = data.get("user")
        return user if isinstance(user, dict) else {"email": email}

    def whoami(self) -> dict:
        data = self.client().request("GET", "/api/v1/internal/me")
        if not isinstance(data, dict):
            raise ToolError("whoami response was empty")
        return data

    def logout(self) -> None:
        secret  = self.store.load(self.url)
        token   = self.env_token or (secret.access_token if secret else "")
        refresh = secret.refresh_token if secret else ""
        if not token and not refresh:
            raise ToolError("not logged in; run hg login")

        try:
            Client(self.url, token, transport=self.transport).request(
                "POST",
                "/api/v1/internal/auth/logout",
                json_body={"refresh_token": refresh},
            )
        except (APIError, ToolError):
            pass
        finally:
            self.store.delete(self.url)
