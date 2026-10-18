"""Non-secret staff profile for the platform API."""

from collections.abc    import Mapping
from dataclasses        import dataclass
from pathlib            import Path
from urllib.parse       import urlsplit
from hg.errors          import ToolError
import json
import os

# The production HunterGround API origin. Swap this one line when the
# platform moves to its production host.
DEFAULT_API_ORIGIN = "https://hntr-backend.archonlabs.id"


@dataclass
class Profile:
    url:        str
    email:      str


def config_home(env: Mapping[str, str] | None = None) -> Path:
    source = os.environ if env is None else env
    raw    = source.get("XDG_CONFIG_HOME")
    base   = Path(raw) if raw else Path.home() / ".config"
    return base / "hg"


def write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)
    path.chmod(0o600)


def normalize_api_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    host   = parsed.hostname
    if parsed.scheme not in ("https", "http") or not host:
        raise ToolError(f"API URL {url!r} is not an http(s) URL")
    if parsed.username or parsed.password:
        raise ToolError("API URL must not include credentials")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ToolError("API URL must be an origin, without a path")
    if parsed.scheme != "https" and host.lower() not in ("localhost", "127.0.0.1"):
        raise ToolError("API URL must use https (http is only allowed for localhost)")

    name = host.lower()
    if ":" in name:
        name = f"[{name}]"

    default_port = 443 if parsed.scheme == "https" else 80
    try:
        port = parsed.port
    except ValueError as exc:
        raise ToolError(f"API URL {url!r} has an invalid port") from exc

    netloc = name if port in (None, default_port) else f"{name}:{port}"
    return f"{parsed.scheme}://{netloc}"


def resolve_origin(env: Mapping[str, str], stored: str | None = None) -> str:
    raw = env.get("HG_API_URL", "").strip() or stored or DEFAULT_API_ORIGIN
    return normalize_api_url(raw)


def load_profile(directory: Path) -> Profile | None:
    path = directory / "config.json"
    if not path.is_file():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ToolError(f"{path} is not valid JSON") from exc

    if not isinstance(data, dict):
        raise ToolError(f"{path} must be a JSON object")

    url   = data.get("url")
    email = data.get("email")
    if not isinstance(url, str) or not isinstance(email, str) or not url or not email:
        raise ToolError(f"{path} needs url and email")
    return Profile(url=url, email=email)


def save_profile(directory: Path, profile: Profile) -> None:
    write_private(
        directory / "config.json",
        json.dumps({"url": profile.url, "email": profile.email}, indent=2) + "\n",
    )
