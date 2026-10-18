"""Map a local challenge tree onto the staff contest and challenge APIs."""

from datetime       import datetime
from pathlib        import Path
from urllib.parse   import quote
from hg.api         import Client, NotFound
from hg.catalog     import CATEGORIES
from hg.compose     import load_compose, parse_services
from hg.errors      import ToolError
from hg.lint        import load_manifest
from hg.scaffold    import WRITEUP
import io
import re
import uuid
import zipfile

DIFFICULTY       = {"easy": 3, "medium": 6, "hard": 9}
FLAG_TEMPLATE    = "hg{%random%}"
MAX_UPLOAD_BYTES = 25 << 20
MAX_HINTS        = 4
# Fields the contest manifest does not own. An update replaces the row, so
# these are copied from the current record instead of being zeroed.
_CONTEST_KEEP = (
    "freeze_minutes",
    "register_by",
    "requires_approval",
    "team_size_max",
    "invite_ttl_minutes",
    "allow_late_join",
)

_DIGEST          = re.compile(r"^sha256:[0-9a-f]{64}$")
_DIGEST_REF      = re.compile(r"^.+@sha256:[0-9a-f]{64}$")


## Helper Functions ---------------------------------------------------------------------------------
def _slug(metadata: dict) -> str:
    slug = metadata.get("id")
    if not isinstance(slug, str) or not slug:
        raise ToolError("metadata.id is required")
    return slug


def _difficulty(metadata: dict, override: int | None) -> int:
    if override is not None:
        if not 1 <= override <= 10:
            raise ToolError("difficulty must be 1..10")
        return override

    value = metadata.get("difficulty")
    if isinstance(value, int) and not isinstance(value, bool):
        if not 1 <= value <= 10:
            raise ToolError("metadata.difficulty must be 1..10")
        return value
    if isinstance(value, str) and value in DIFFICULTY:
        return DIFFICULTY[value]
    raise ToolError("metadata.difficulty must be 1..10 or easy, medium, hard")


def _parts(manifest: dict) -> tuple[dict, dict]:
    metadata = manifest.get("metadata")
    spec = manifest.get("spec")
    if not isinstance(metadata, dict) or not isinstance(spec, dict):
        raise ToolError("challenge.yaml needs metadata and spec")
    return metadata, spec


def _apply_flag(spec: dict, body: dict) -> None:
    flags = spec.get("flags")
    if not isinstance(flags, list) or len(flags) != 1:
        count = len(flags) if isinstance(flags, list) else 0
        raise ToolError(f"the platform stores one flag; this challenge has {count}")

    flag = flags[0]
    if not isinstance(flag, dict):
        raise ToolError("spec.flags[0] must be a mapping")

    kind = flag.get("type")
    if kind == "regex":
        raise ToolError("the platform does not accept regex flags")
    if kind == "static":
        content = flag.get("content")
        if not isinstance(content, str) or not content:
            raise ToolError("static flag needs content")

        body["flag"] = content
        body["flag_mode"] = "static"
        return
    if kind == "dynamic":
        if not body["requires_instance"]:
            raise ToolError("a dynamic flag requires an instance")
        body["flag_mode"] = "dynamic"
        body["flag_template"] = FLAG_TEMPLATE
        return
    raise ToolError(f"spec.flags[0].type {kind!r} is not static or dynamic")


def _writeup(root: Path) -> str | None:
    path = root / "private" / "writeup.md"
    if not path.is_file():
        return None

    text = path.read_text(encoding="utf-8")
    if text == WRITEUP:
        return None
    return text


def _entry_instance(root: Path, spec: dict, image: str | None) -> tuple[int, str, str]:
    player = spec.get("player")
    if not isinstance(player, dict):
        raise ToolError("spec.player is required")

    entry = player.get("entry")
    if not isinstance(entry, str) or "." not in entry:
        raise ToolError("spec.player.entry must be <member>.<port>")

    member_name, port_name = entry.split(".", 1)
    connection = player.get("connection") if isinstance(player.get("connection"), str) else ""
    if spec.get("spawn") == "compose":
        return _compose_entry(root, spec, member_name, port_name, connection, image)

    runtime = spec.get("runtime")
    members = spec.get("hosts") if runtime == "scenario" else spec.get("services")
    if not isinstance(members, list):
        raise ToolError("the player entry service is not in this challenge")

    member = next((item for item in members if isinstance(item, dict) and item.get("name") == member_name), None)
    if member is None:
        raise ToolError(f"spec.player.entry service {member_name!r} is not in this challenge")
    ports = member.get("ports") or []
    port  = next(
        (item for item in ports if isinstance(item, dict) and item.get("name") == port_name),
        None,
    )
    if port is None or not isinstance(port.get("container"), int):
        raise ToolError(f"spec.player.entry port {port_name!r} is not on {member_name}")
    health = member.get("health") if isinstance(member.get("health"), dict) else {}
    protocol = _protocol(
        port_protocol=str(port.get("protocol") or "tcp"),
        health     = health.get("type") if isinstance(health.get("type"), str) else None,
        connection = connection,
    )
    image_info = member.get("image") if isinstance(member.get("image"), dict) else {}
    ref = registry_image(
        override = image,
        name     = image_info.get("name")     if isinstance(image_info.get("name"), str)     else None,
        digest   = image_info.get("digest")   if isinstance(image_info.get("digest"), str)   else None,
        registry = image_info.get("registry") if isinstance(image_info.get("registry"), str) else None,
    )
    return port["container"], protocol, ref


def _compose_entry(
    root:           Path,
    spec:           dict,
    member_name:    str,
    port_name:      str,
    connection:     str,
    image:          str | None,
) -> tuple[int, str, str]:
    compose  = spec.get("compose") if isinstance(spec.get("compose"), dict) else {}
    filename = compose.get("file") if isinstance(compose.get("file"), str) else "compose.yaml"
    path = root / filename
    if not path.is_file():
        raise ToolError(f"missing {filename}")

    services, errors = parse_services(load_compose(path))
    if errors:
        raise ToolError("\n".join(errors))

    service = next((item for item in services if item.name == member_name), None)
    if service is None:
        raise ToolError(f"spec.player.entry service {member_name!r} is not in this challenge")

    port = service.port_labels.get(port_name)
    if port is None:
        raise ToolError(f"spec.player.entry port {port_name!r} is not on {member_name}")

    protocol = _protocol(
        port_protocol=_compose_port_protocol(service.raw, port),
        health     = None,
        connection = connection,
    )
    ref = registry_image(override=image, name=service.image, digest=None)
    return port, protocol, ref


def _compose_port_protocol(service: dict, container_port: int) -> str:
    ports = service.get("ports") or []
    if not isinstance(ports, list):
        return "tcp"

    for entry in ports:
        if _container_port(entry) != container_port:
            continue

        protocol = "tcp"
        if isinstance(entry, str) and "/" in entry.split(":")[-1]:
            protocol = entry.rsplit("/", 1)[-1]
        elif isinstance(entry, dict) and isinstance(entry.get("protocol"), str):
            protocol = entry["protocol"]
        if protocol == "udp":
            return "udp"
    return "tcp"


def _container_port(entry: object) -> int | None:
    if isinstance(entry, int) and not isinstance(entry, bool):
        return entry

    if isinstance(entry, str):
        token = entry.split("/", 1)[0].split(":")[-1]
        if token.isdigit():
            return int(token)
        return None

    if isinstance(entry, dict):
        target = entry.get("target")
        if isinstance(target, int) and not isinstance(target, bool):
            return target
        if isinstance(target, str) and target.isdigit():
            return int(target)
    return None


def _protocol(*, port_protocol: str, health: str | None, connection: str) -> str:
    if port_protocol == "udp":
        raise ToolError("player entry port is udp; the platform only accepts http or tcp")
    if health == "http" or "{url}" in connection:
        return "http"
    return "tcp"


def _require_registry(ref: str, source: str) -> str:
    if _tag(ref) == "local":
        raise ToolError(f"refusing :local image tag in {source}; the orchestrator cannot pull it")
    if "/" not in ref and "@" not in ref:
        raise ToolError(
            f"{source} must be a registry reference such as "
            "registry.example/challenges/web/id@sha256:…"
        )
    return ref


def _tag(ref: str) -> str | None:
    body = ref.split("@", 1)[0]
    last = body.rsplit("/", 1)[-1]
    if ":" not in last:
        return None
    return last.split(":", 1)[1]


def _name_without_tag(name: str) -> str:
    body  = name.split("@", 1)[0]
    slash = body.rfind("/")
    last  = body[slash + 1 :]
    if ":" not in last:
        return body
    return body[: slash + 1 + last.rfind(":")]


def _files_from_tree(root: Path) -> tuple[str, bytes, str]:
    manifest = load_manifest(root)
    metadata, spec = _parts(manifest)
    files = spec.get("files")
    if not isinstance(files, list) or not files:
        raise ToolError("no spec.files to upload; pass --file")

    payloads: list[tuple[str, Path]] = []
    for index, item in enumerate(files):
        if not isinstance(item, dict):
            raise ToolError(f"spec.files[{index}] must be a mapping")

        source = item.get("source")
        if source == "staging":
            raise ToolError(
                "source: staging lives in the player bucket; this command only uploads source: build files from dist/"
            )
        if source != "build":
            raise ToolError(f"spec.files[{index}].source {source!r} is not build")

        name = item.get("name")
        if not isinstance(name, str) or not name or "/" in name or "\\" in name or name in (".", ".."):
            raise ToolError(f"spec.files[{index}].name is not a file name")

        path = root / "dist" / name
        if not path.is_file():
            raise ToolError(f"missing dist/{name}")
        payloads.append((name, path))

    if len(payloads) == 1:
        name, path = payloads[0]
        return name, path.read_bytes(), "application/octet-stream"

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, path in payloads:
            archive.write(path, arcname=name)
    return f"{_slug(metadata)}-handout.zip", buffer.getvalue(), "application/zip"


def _rfc3339(value: str, field: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ToolError(f"{field} must be RFC3339") from exc
    if parsed.tzinfo is None:
        raise ToolError(f"{field} must be RFC3339")
    return parsed


## Main Functions ---------------------------------------------------------------------------------
def create_contest(
    client:         Client,
    *,
    slug:           str,
    title:          str,
    starts_at:      str,
    ends_at:        str,
    description:    str     = "",
    rules:          str     = "",
    kind:           str     = "open",
    mode:           str     = "jeopardy",
    participation:  str     = "individual",
) -> dict:
    """Create a draft contest on platform."""
    start = _rfc3339(starts_at, "starts_at")
    end   = _rfc3339(ends_at,   "ends_at")
    if not end > start:
        raise ToolError("ends_at must be after starts_at")

    body = {
        "slug":         slug,
        "title":        title,
        "description":  description,
        "rules":        rules,
        "mode":         mode,
        "kind":         kind,
        "starts_at":    starts_at,
        "ends_at":      ends_at,
        "published":    False,
        "participation": participation,
    }
    data = client.request("POST", "/api/v1/admin/contests", json_body=body)
    if not isinstance(data, dict):
        raise ToolError("contest create response was empty")
    return data


def publish_contest(client: Client, slug: str) -> dict:
    """Publish a contest on platform."""
    data = client.request("POST", f"/api/v1/admin/contests/{quote(slug, safe='')}/publish")
    if not isinstance(data, dict):
        raise ToolError("contest publish response was empty")
    return data

def _merge_contest(body: dict, existing: object) -> dict:
    """Keep server-owned contest fields the manifest does not represent."""
    if not isinstance(existing, dict):
        return body
    merged = dict(body)
    for key in _CONTEST_KEEP:
        if key not in existing or existing[key] is None:
            continue
        merged[key] = existing[key]
    return merged


def upsert_contest(client: Client, body: dict) -> tuple[str, dict]:
    """
    Create the contest, or update it when the slug already exists.

    `body` comes from `contest_body` (the manifest mapped to the platform
    contest fields). The caller owns the field mapping; this function owns the
    create-or-update decision. Existence is `GET /api/v1/contests/:slug`:
    the admin router has no get-by-slug.
    """
    slug = body.get("slug")
    if not isinstance(slug, str) or not slug:
        raise ToolError("contest body needs slug")

    encoded = quote(slug, safe="")
    try:
        existing = client.request("GET", f"/api/v1/contests/{encoded}")
    except NotFound:
        data = client.request("POST", "/api/v1/admin/contests", json_body=body)
        if not isinstance(data, dict):
            raise ToolError("contest create response was empty")
        return "created", data

    data = client.request(
        "PUT",
        f"/api/v1/admin/contests/{encoded}",
        json_body=_merge_contest(body, existing),
    )
    if not isinstance(data, dict):
        raise ToolError("contest update response was empty")
    return "updated", data

def link_challenge(
    client:         Client,
    contest:        str,
    slug:           str,
    *,
    points:         int,
    sort_order:     int = 0,
) -> dict:
    """Resolve a challenge slug to its platform id and link it to the contest."""
    if points < 0:
        raise ToolError("points must be >= 0")

    encoded = quote(slug, safe="")
    try:
        detail = client.request("GET", f"/api/v1/admin/challenges/{encoded}")
    except NotFound as exc:
        raise ToolError(f"challenge {slug} is not on the platform; run `hg challenge push` first") from exc

    if not isinstance(detail, dict) or not detail.get("id"):
        raise ToolError(f"challenge {slug} response did not include an id")

    client.request(
        "PUT",
        f"/api/v1/admin/contests/{quote(contest, safe='')}/challenges",
        json_body={
            "challenge_id": detail["id"],
            "points":       points,
            "sort_order":   sort_order,
        },
    )
    return {
        "contest":    contest,
        "challenge":  slug,
        "points":     points,
        "sort_order": sort_order,
    }


def add_challenge(
    client:         Client,
    contest:        str,
    slug:           str,
    *,
    root:           Path | None = None,
    points:         int | None  = None,
    sort_order:     int         = 0,
) -> dict:
    """Link a challenge that is already on the platform. Does not push content."""
    if points is None and root is not None:
        manifest = load_manifest(root)
        metadata, _spec = _parts(manifest)
        value = metadata.get("value")
        if not isinstance(value, int) or isinstance(value, bool):
            raise ToolError("metadata.value is required when --points is omitted")
        points = value
    if points is None:
        raise ToolError("points are required when --path is omitted")

    return link_challenge(
        client,
        contest,
        slug,
        points     = points,
        sort_order = sort_order,
    )

def add_manifest_challenges(
    client:         Client,
    contest:        str,
    root:           Path,
) -> list[dict]:
    """Push each contest challenge, upload its handout, then link it.

    Catalogue status is left alone. `spec.challenges[].state` is a local
    preview, not a publish switch. A public competition rejects a link to a
    challenge that is already published.
    """
    from hg.contest import discover_challenges

    linked: list[dict] = []
    for entry in discover_challenges(root):
        try:
            push_challenge(client, entry.root)
            uploaded = upload_handout(client, entry.root)
        except ToolError as exc:
            raise ToolError(f"{entry.path}: {exc}") from exc

        item = link_challenge(
            client,
            contest,
            entry.slug,
            points     = entry.points,
            sort_order = entry.sort_order,
        )
        if uploaded is not None:
            item["handout"] = uploaded[0]
            item["handout_bytes"] = uploaded[1]
        linked.append(item)
    return linked

def deploy_contest(client: Client, root: Path) -> dict:
    """
    Create or update the contest, push each challenge and its handout, link
    them, then publish the contest when `spec.visibility` is `published`.
    """
    from hg.contest import contest_body, load_contest

    manifest = load_contest(root)
    if not isinstance(manifest.get("metadata"), dict):
        raise ToolError("contest.yaml needs metadata")

    result: dict = {"contest": manifest["metadata"]["id"], "action": None, "linked": [], "published": False}
    body = contest_body(manifest)
    action, data = upsert_contest(client, body)
    result["action"] = action
    result["contest"] = data.get("slug", result["contest"])
    result["linked"] = add_manifest_challenges(client, result["contest"], root)
    if body["published"]:
        publish_contest(client, result["contest"])
        result["published"] = True
    return result


def challenge_body(
    root:           Path,
    *,
    image:          str | None = None,
    difficulty:     int | None = None,
    publish:        bool       = False,
) -> dict:
    manifest = load_manifest(root)
    metadata, spec = _parts(manifest)
    slug = _slug(metadata)
    title = metadata.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ToolError("metadata.title is required")

    category = metadata.get("category")
    if category not in CATEGORIES:
        known = ", ".join(sorted(CATEGORIES))
        raise ToolError(f"category {category!r} is not on the platform ({known})")

    description = metadata.get("description")
    if not isinstance(description, str):
        description = ""

    body: dict = {
        "slug":         slug,
        "title":        title.strip(),
        "description":  description,
        "category":     category,
        "difficulty":   _difficulty(metadata, difficulty),
        "requires_instance": spec.get("runtime") in ("container", "scenario"),
    }
    topics = spec.get("topics")
    if isinstance(topics, list):
        tags = [item for item in topics if isinstance(item, str) and item]
        if tags:
            body["tags"] = tags
    # Omit status unless this push is publishing. Create defaults to draft.
    # An update with an empty status keeps the current one, so a later deploy
    # does not unpublish a live challenge or publish one ahead of its contest.
    if publish:
        body["status"] = "published"
    _apply_flag(spec, body)
    if body["requires_instance"]:
        port, protocol, ref = _entry_instance(root, spec, image)
        body["docker_image"]   = ref
        body["container_port"] = port
        body["protocol"]       = protocol
    elif image:
        raise ToolError("a static challenge has no image to push")

    writeup = _writeup(root)
    if writeup is not None:
        body["writeup"] = writeup
    return body


def _wanted_hints(spec: dict) -> list[dict] | None:
    """Manifest hints as platform rows. None means the manifest does not list any."""
    hints = spec.get("hints")
    if not isinstance(hints, list):
        return None
    if len(hints) > MAX_HINTS:
        raise ToolError(f"the platform stores at most {MAX_HINTS} hints")

    wanted: list[dict] = []
    for index, item in enumerate(hints, start=1):
        if not isinstance(item, dict):
            raise ToolError(f"spec.hints[{index - 1}] must be a mapping")
        content = item.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ToolError(f"spec.hints[{index - 1}].content is required")
        wanted.append({"position": index, "content": content.strip()})
    return wanted


def _keep_challenge(body: dict, existing: dict) -> None:
    """Keep review adjustments and fields this push is not replacing."""
    factor = existing.get("review_factor")
    if isinstance(factor, (int, float)) and not isinstance(factor, bool) and factor != 0:
        body["review_factor"] = factor
    if existing.get("is_legacy") is True:
        body["is_legacy"] = True
    if "tags" not in body:
        tags = existing.get("tags")
        if isinstance(tags, list) and tags:
            body["tags"] = [item for item in tags if isinstance(item, str)]
    if "writeup" not in body:
        writeup = existing.get("writeup")
        if isinstance(writeup, str) and writeup:
            body["writeup"] = writeup


def _sync_hints(client: Client, slug: str, wanted: list[dict] | None, existing: dict | None) -> None:
    """Replace stored hints so they match the manifest. This resets unlocks."""
    if wanted is None:
        return
    encoded = quote(slug, safe="")
    stored = existing.get("hints") if isinstance(existing, dict) else None
    if isinstance(stored, list):
        for hint in stored:
            hint_id = hint.get("id") if isinstance(hint, dict) else None
            if isinstance(hint_id, str) and hint_id:
                client.request(
                    "DELETE",
                    f"/api/v1/admin/challenges/{encoded}/hints/{quote(hint_id, safe='')}",
                )
    for item in wanted:
        client.request(
            "POST",
            f"/api/v1/admin/challenges/{encoded}/hints",
            json_body=item,
        )


def push_challenge(
    client:         Client,
    root:           Path,
    *,
    image:          str | None = None,
    difficulty:     int | None = None,
    publish:        bool       = False,
) -> tuple[str, dict]:
    manifest = load_manifest(root)
    spec = manifest.get("spec") if isinstance(manifest.get("spec"), dict) else {}
    wanted = _wanted_hints(spec)
    body = challenge_body(root, image=image, difficulty=difficulty, publish=publish)
    slug = quote(body["slug"], safe="")
    try:
        existing = client.request("GET", f"/api/v1/admin/challenges/{slug}")
    except NotFound:
        data = client.request("POST", "/api/v1/admin/challenges", json_body=body)
        if not isinstance(data, dict):
            raise ToolError("challenge create response was empty")
        _sync_hints(client, body["slug"], wanted, None)
        return "created", data

    if isinstance(existing, dict):
        _keep_challenge(body, existing)

    data = client.request("PUT", f"/api/v1/admin/challenges/{slug}", json_body=body)
    if not isinstance(data, dict):
        raise ToolError("challenge update response was empty")
    _sync_hints(client, body["slug"], wanted, existing if isinstance(existing, dict) else None)
    return "updated", data


def _has_handout(root: Path) -> bool:
    manifest = load_manifest(root)
    spec = manifest.get("spec")
    files = spec.get("files") if isinstance(spec, dict) else None
    return isinstance(files, list) and len(files) > 0


def upload_handout(
    client: Client,
    root:   Path,
    file:   Path | None = None,
) -> tuple[str, int] | None:
    """Upload the handout when the tree has `spec.files` or `--file` names one."""
    if file is None and not _has_handout(root):
        return None
    return push_material(client, root, file)


def handout_bytes(root: Path, file: Path | None) -> tuple[str, bytes, str]:
    if file is not None:
        path = file.expanduser()
        if not path.is_file():
            raise ToolError(f"no handout file at {path}")
        name = path.name
        data = path.read_bytes()
        content_type = "application/zip" if name.lower().endswith(".zip") else "application/octet-stream"
    else:
        name, data, content_type = _files_from_tree(root)

    if not data:
        raise ToolError("handout is empty")

    if len(data) > MAX_UPLOAD_BYTES:
        raise ToolError(
            f"handout is {len(data)} bytes; the platform limit is {MAX_UPLOAD_BYTES} bytes"
        )
    return name, data, content_type


def push_material(client: Client, root: Path, file: Path | None = None) -> tuple[str, int]:
    manifest = load_manifest(root)
    metadata, _spec = _parts(manifest)
    slug = quote(_slug(metadata), safe="")
    name, data, content_type = handout_bytes(root, file)
    try:
        client.request("GET", f"/api/v1/admin/challenges/{slug}")
    except NotFound as exc:
        raise ToolError("challenge is not on the platform; run hg challenge push first") from exc

    payload, mime = encode_multipart(name, data, content_type)
    client.request(
        "POST",
        f"/api/v1/admin/challenges/{slug}/attachment",
        body         = payload,
        content_type = mime,
    )
    return name, len(data)


def encode_multipart(name: str, data: bytes, content_type: str) -> tuple[bytes, str]:
    if any(char in name for char in ('"', "\r", "\n")):
        raise ToolError(f"attachment name {name!r} cannot be sent")

    boundary = "----hg" + uuid.uuid4().hex
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode()
    body = head + data + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def registry_image(
    *,
    override:       str | None,
    name:           str | None,
    digest:         str | None,
    registry:       str | None = None,
) -> str:
    if override is not None and override.strip():
        return _require_registry(override.strip(), "--image")
    if isinstance(registry, str) and registry.strip():
        if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
            raise ToolError("image.digest must be sha256:<64 hex>")
        if _tag(registry) == "local":
            raise ToolError("refusing :local image tag; pass --image with a registry reference")
        return _require_registry(f"{registry.strip()}@{digest}", "image.registry")
    if isinstance(name, str) and _tag(name) == "local":
        raise ToolError("refusing :local image tag; pass --image with a registry reference")
    if isinstance(name, str) and isinstance(digest, str) and digest:
        if not _DIGEST.fullmatch(digest):
            raise ToolError("image.digest must be sha256:<64 hex>")
        if _tag(name) == "local":
            raise ToolError("refusing :local image tag; pass --image with a registry reference")
        return f"{_name_without_tag(name)}@{digest}"
    if isinstance(name, str) and _DIGEST_REF.fullmatch(name):
        if _tag(name) == "local":
            raise ToolError("refusing :local image tag; pass --image with a registry reference")
        return name
    raise ToolError(
        "pass --image with a registry reference, or set image.registry and image.digest on the player entry"
    )

