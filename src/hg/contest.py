"""
HunterGround Contest Management

Load, lint, digest, and scaffold a contest tree.

A contest is one `contest.yaml` at the contest root plus one directory per challenge.
Every immediate child directory that holds a `challenge.yaml` is a contest challenge; 
`spec.challenges` only orders them and overrides points. `state` is a local preview. 
The contest manifest itself is pinned by `schema/contest-v1.json` and resolves to 
platform fields through the table in `schema/contest-types.yaml`.
"""

from dataclasses            import dataclass
from datetime               import datetime
from importlib.resources    import files
from pathlib                import Path
from typing                 import Any
from jsonschema             import Draft202012Validator
from jsonschema.exceptions  import ValidationError
from hg.catalog             import ID_HINT, ID_RE
from hg.digest              import manifest_digest
from hg.errors              import ToolError
from hg.lint                import lint_challenge, load_manifest
from hg.scaffold            import GITIGNORE, ensure_new_dir
import yaml
import json


## Constants --------------------------------------------------------------------
# manifest filename at the contest root
CONTEST_NAME   = "contest.yaml"
PARTICIPATIONS = ("individual", "team")
CONTEST_README = """# Contest

Each subdirectory with a `challenge.yaml` is a contest challenge.
`spec.challenges` in `contest.yaml` orders them and overrides points.
"""


## Helper Functions ------------------------------------------------------------
def _load_schema() -> dict:
    raw = (files("hg") / "schema" / "contest-v1.json").read_text(encoding="utf-8")
    return json.loads(raw)

def _load_type_table() -> dict:
    raw = (files("hg") / "schema" / "contest-types.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ToolError("contest-types.yaml must be a mapping")
    return data

_SCHEMA    = _load_schema()
_VALIDATOR = Draft202012Validator(_SCHEMA)
_TYPES     = _load_type_table()

@dataclass(frozen=True)
class ContestType:
    name:                str
    kind:                str
    mode:                str
    default:             str
    allowed:        tuple[str, ...]

def _schema_errors(data: dict) -> list[str]:
    errors: list[str] = []
    found: list[ValidationError] = sorted(
        _VALIDATOR.iter_errors(data),
        key=lambda err: [str(part) for part in err.path],
    )
    for err in found:
        loc = ".".join(str(part) for part in err.path) or "file"
        errors.append(f"{loc}: {err.message}")
    return errors

def contest_types() -> list[str]:
    """
    The contest types ``contest.yaml`` accepts.

    Read from the same table lint reads, so a type added to the YAML is
    completable without touching code.
    """
    return sorted(str(name) for name in _TYPES)

def contest_type(name: str) -> ContestType:
    """Resolve a manifest type to platform fields and allowed participation."""
    table = _TYPES.get(name)
    if not isinstance(table, dict):
        known = ", ".join(sorted(_TYPES))
        raise ToolError(f"unknown contest type {name!r}. Known types: {known}")

    participations = table.get("participations")
    if not isinstance(participations, list) or not participations:
        raise ToolError(f"contest-types.yaml entry {name!r} needs participations")

    default = table.get("participation")
    if default not in participations:
        raise ToolError(
            f"contest-types.yaml entry {name!r} default {default!r} is not in participations"
        )
    return ContestType(
        name    = name,
        kind    = str(table.get("kind") or name),
        mode    = str(table.get("mode") or "jeopardy"),
        default = str(default),
        allowed = tuple(str(item) for item in participations),
    )

def contest_root(path: Path | None) -> Path:
    start = (path or Path.cwd()).resolve()
    if start.is_file() and start.name == CONTEST_NAME:
        return start.parent

    manifest = start / CONTEST_NAME
    if manifest.is_file():
        return start
    raise ToolError(f"no {CONTEST_NAME} in {start}")

def load_contest(root: Path) -> dict:
    path = root / CONTEST_NAME
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ToolError(f"{CONTEST_NAME} is not valid YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise ToolError(f"{CONTEST_NAME} must be a mapping")
    return data

def contest_slug(root: Path) -> str:
    metadata = load_contest(root).get("metadata")
    slug = metadata.get("id") if isinstance(metadata, dict) else None
    if not isinstance(slug, str) or not slug:
        raise ToolError(f"{CONTEST_NAME} metadata.id is required")
    return slug


## Discovery ---------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Entry:
    """One challenge in the contest, resolved in order."""

    path:           str
    root:           Path
    slug:           str
    points:         int
    sort_order:     int
    state:          str

def _discovered(root: Path) -> list[str]:
    """Immediate child directories that hold a challenge.yaml, name-sorted."""
    found: list[str] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if (child / "challenge.yaml").is_file():
            found.append(child.name)
    return found

def _challenge_slug(root: Path) -> str:
    metadata = load_manifest(root).get("metadata")
    if not isinstance(metadata, dict):
        raise ToolError(f"{root / 'challenge.yaml'} needs metadata")

    slug = metadata.get("id")
    if not isinstance(slug, str) or not slug:
        raise ToolError(f"{root / 'challenge.yaml'} metadata.id is required")
    return slug

def _challenge_value(root: Path) -> int:
    metadata = load_manifest(root).get("metadata")
    value = metadata.get("value") if isinstance(metadata, dict) else None
    if not isinstance(value, int) or isinstance(value, bool):
        return 0
    return value

def discover_challenges(root: Path) -> list[Entry]:
    """Resolve every contest challenge in sortOrder, with points and state."""
    manifest = load_contest(root)
    spec     = manifest.get("spec")   if isinstance(manifest.get("spec"), dict)   else {}
    listed   = spec.get("challenges") if isinstance(spec.get("challenges"), list) else []

    overrides: dict[str, dict] = {}
    for index, item in enumerate(listed):
        if not isinstance(item, dict):
            continue

        raw = item.get("path")
        if not isinstance(raw, str) or not raw:
            raise ToolError(f"spec.challenges[{index}].path is required")
        if raw in overrides:
            raise ToolError(f"spec.challenges lists {raw!r} more than once")
        overrides[raw] = item

    names = _discovered(root)
    known = set(names)
    for raw in overrides:
        if raw not in known:
            raise ToolError(f"spec.challenges path {raw!r} has no challenge.yaml")

    # Listed entries lead, in their listed order. The rest follow, name-sorted.
    ordered = [raw for raw in overrides] + [name for name in names if name not in overrides]

    entries: list[Entry] = []
    for index, path in enumerate(ordered):
        item  = overrides.get(path, {})
        child = root / path

        points = item.get("points")
        if points is None:
            points = _challenge_value(child)
        if not isinstance(points, int) or isinstance(points, bool) or points < 0:
            raise ToolError(f"challenge {path!r} points must be an integer >= 0")

        sort_order = item.get("sortOrder", index)
        if not isinstance(sort_order, int) or isinstance(sort_order, bool) or sort_order < 0:
            raise ToolError(f"challenge {path!r} sortOrder must be an integer >= 0")

        state = item.get("state", "hidden")
        if state not in ("visible", "hidden"):
            raise ToolError(f"challenge {path!r} state must be visible or hidden")

        entries.append(
            Entry(
                path       = path,
                root       = child,
                slug       = _challenge_slug(child),
                points     = points,
                sort_order = sort_order,
                state      = state,
            )
        )
    return entries


## Type and body -----------------------------------------------------------------------------------
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

def resolve_participation(manifest: dict) -> tuple[str, ContestType]:
    """Apply the type table and the metadata override to pick participation."""
    metadata  = manifest["metadata"]
    kind      = contest_type(metadata["type"])

    requested = metadata.get("participation", kind.default)
    if requested not in PARTICIPATIONS:
        raise ToolError(f"metadata.participation must be one of {PARTICIPATIONS}")
    if requested not in kind.allowed:
        allowed = ", ".join(kind.allowed)
        raise ToolError(
            f"contest type {kind.name!r} does not allow participation {requested!r} (allowed: {allowed})"
        )
    return requested, kind

def contest_body(manifest: dict, *, publish: bool = False) -> dict:
    """Map the contest manifest onto the platform contest record."""
    metadata = manifest["metadata"]
    spec     = manifest["spec"]
    participation, kind = resolve_participation(manifest)

    start = _rfc3339(spec["startsAt"], "spec.startsAt")
    end   = _rfc3339(spec["endsAt"], "spec.endsAt")
    if not end > start:
        raise ToolError("spec.endsAt must be after spec.startsAt")

    description = metadata.get("description")
    rules = metadata.get("rules")
    published = publish or spec.get("visibility") == "published"
    return {
        "slug":          metadata["id"],
        "title":         metadata["title"],
        "description":   description if isinstance(description, str) else "",
        "rules":         rules       if isinstance(rules, str)       else "",
        "mode":          kind.mode,
        "kind":          kind.kind,
        "starts_at":     spec["startsAt"],
        "ends_at":       spec["endsAt"],
        "published":     published,
        "participation": participation,
    }


## Lint and digest ---------------------------------------------------------------------------------
def lint_contest(root: Path, *, strict: bool = False) -> list[str]:
    try:
        manifest = load_contest(root)
    except ToolError as exc:
        return [str(exc)]

    errors = _schema_errors(manifest)
    if errors:
        return errors

    try:
        resolve_participation(manifest)
    except ToolError as exc:
        errors.append(str(exc))

    try:
        contest_body(manifest)
    except ToolError as exc:
        if str(exc) not in errors:
            errors.append(str(exc))

    try:
        entries = discover_challenges(root)
        if not entries:
            errors.append("no challenge directories found; add one with a challenge.yaml")
    except ToolError as exc:
        errors.append(str(exc))
        entries = []

    for entry in entries:
        for message in lint_challenge(entry.root, strict=strict):
            errors.append(f"{entry.path}: {message}")
    return errors

def require_lint(root: Path, *, strict: bool = False) -> dict:
    errors = lint_contest(root, strict=strict)
    if errors:
        raise ToolError("\n".join(errors))
    return load_contest(root)

def digest_contest(path: Path | None) -> str:
    root = contest_root(path)
    return manifest_digest(load_contest(root))


## Scaffold ----------------------------------------------------------------------------------------
def _contest_manifest(
    contest_id:     str,
    title:          str,
    kind:           str,
    starts_at:      str,
    ends_at:        str,
    description:    str,
    rules:          str,
    participation:  str | None,
) -> dict:
    contest_type(kind)
    metadata: dict[str, Any] = {
        "id":          contest_id,
        "title":       title,
        "description": description or "Replace this description.",
        "type":        kind,
    }
    if rules:
        metadata["rules"] = rules
    if participation is not None:
        metadata["participation"] = participation

    return {
        "apiVersion": "hg.contest/v1",
        "kind":       "Contest",
        "metadata":   metadata,
        "spec": {
            "startsAt":   starts_at,
            "endsAt":     ends_at,
            "visibility": "draft",
        },
    }

def create_contest_tree(
    contest_id:     str,
    dest:           Path,
    *,
    title:          str | None = None,
    kind:           str        = "open",
    starts_at:      str,
    ends_at:        str,
    description:    str        = "",
    rules:          str        = "",
    participation:  str | None = None,
) -> Path:
    if not ID_RE.fullmatch(contest_id):
        raise ToolError(
            f"id must match {ID_HINT} "
            f"(got {contest_id!r})"
        )

    resolved = contest_type(kind)
    if participation is not None and participation not in resolved.allowed:
        allowed = ", ".join(resolved.allowed)
        raise ToolError(
            f"contest type {kind!r} does not allow participation {participation!r} (allowed: {allowed})"
        )

    # Validate the window the same way push will.
    contest_body(
        _contest_manifest(
            contest_id,
            title or contest_id,
            kind,
            starts_at,
            ends_at,
            description,
            rules,
            participation,
        )
    )

    dest = dest.expanduser().resolve()
    ensure_new_dir(dest)
    dest.mkdir(parents=True, exist_ok=True)
    (dest / CONTEST_NAME).write_text(
        yaml.safe_dump(
            _contest_manifest(
                contest_id,
                title or contest_id,
                kind,
                starts_at,
                ends_at,
                description,
                rules,
                participation,
            ),
            sort_keys=False,
            default_flow_style=False,
        ),
        encoding="utf-8",
    )
    (dest / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    (dest / "README.md").write_text(CONTEST_README, encoding="utf-8")
    return dest
