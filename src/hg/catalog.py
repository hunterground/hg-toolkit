"""
Bases and builders the local toolkit knows about.

Match the public base-images an author should know.
A base with `template` set can be scaffolded in this cut.
A builder is a compile-stage image, pulled and retagged like a base.
"""

from collections.abc    import Iterable
from dataclasses        import dataclass
from hg.errors          import ToolError
import re

# match the HunterGround strict slug rules for slug, with 3..64 characters.
ID_RE     = re.compile(r"^(?=.{3,64}$)[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
ID_HINT   = "^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$ and be 3..64 characters"
# Compose service names stay short (`db`, `web`); they are not platform slugs.
MEMBER_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
PLATFORMS = ("linux/amd64", "linux/arm64")

# Docker Hub org that publishes these images.
HUB_NAMESPACE = "hunterground"

# Backend categories. misc and range are local-only.
CATEGORIES = frozenset(
    {"web", "reversing", "pwn", "forensic", "crypto", "osint", "mobile", "blockchain"}
)


## Definitions -----------------------------------------------------------------
@dataclass(frozen=True)
class Port:
    name:           str
    container:      int
    protocol:       str  = "tcp"
    publish:        bool = True


@dataclass(frozen=True)
class Base:
    name:                 str
    image:                str
    template:             str       | None = None
    reason:               str       | None = None
    category:             str              = "misc"
    runtime:              str              = "container"
    member:               str              = "web"
    ports:          tuple[Port, ...]       = ()
    health_type:          str              = "tcp"
    health_port:          int       | None = None
    health_path:          str       | None = None
    connection:           str              = "{url}"
    flag_path:            str              = "/app/flag.txt"
    flag_owner:           str              = "ctf:ctf"
    flag_mode:            str              = "0400"
    memory_mb:            int              = 128
    cpu_mhz:              int              = 200
    platforms:      tuple[str, ...]        = PLATFORMS
    build_args:     tuple[tuple[str, str], ...] = ()
    extra_inspect:  tuple[str, ...]        = ()
    placement:            str       | None = None

    @property
    def entry_port(self) -> str:
        if self.ports:
            return self.ports[0].name
        return "tcp"


@dataclass(frozen=True)
class Builder:
    """A compile-stage image published on the hub and retagged locally."""

    name:             str
    image:            str
    platforms:  tuple[str, ...] = PLATFORMS
    note:             str       = ""


## Constants -------------------------------------------------------------------
_LATER_BUILDER = "needs a builder stage"
_LATER_BINARY  = "needs a compiled binary"
_LATER_CHAIN   = "needs a chain project"

# Column widths shared by the header and every row, so the two stay aligned.
# ``NOTE`` is the last column and is not padded: it is the only free-form one.
BASE_WIDTHS  = (24, 10, 28)
BASE_HEADER  = ("BASE", "STATUS", "LISTEN", "NOTE")


## Helper Functions ------------------------------------------------------------
def _arg_name(image_name: str) -> str:
    prefix, leaf = image_name.split("/", 1)
    bits         = [prefix, *leaf.split("-")]
    return "_".join(bit.upper() for bit in bits) + "_IMAGE"


def _base(
    name:                     str,
    *,
    template:                 str   | None = None,
    reason:                   str   | None = None,
    category:                 str          = "misc",
    runtime:                  str          = "container",
    member:                   str          = "web",
    ports:              tuple[Port, ...]   = (),
    health_type:              str          = "tcp",
    health_port:              int   | None = None,
    health_path:              str   | None = None,
    connection:               str          = "{url}",
    flag_path:                str          = "/app/flag.txt",
    flag_owner:               str          = "ctf:ctf",
    flag_mode:                str          = "0400",
    memory_mb:                int          = 128,
    cpu_mhz:                  int          = 200,
    platforms:          tuple[str, ...]    = PLATFORMS,
    extra_inspect:      tuple[str, ...]    = (),
    extra_build_args:   tuple[tuple[str, str], ...] = (),
    placement:                str   | None = None,
) -> Base:
    image = f"{name}:latest"
    build_args = ((_arg_name(name), image),) + extra_build_args
    return Base(
        name            = name,
        image           = image,
        template        = template,
        reason          = reason,
        category        = category,
        runtime         = runtime,
        member          = member,
        ports           = ports,
        health_type     = health_type,
        health_port     = health_port if health_port is not None else (ports[0].container if ports else None),
        health_path     = health_path,
        connection      = connection,
        flag_path       = flag_path,
        flag_owner      = flag_owner,
        flag_mode       = flag_mode,
        memory_mb       = memory_mb,
        cpu_mhz         = cpu_mhz,
        platforms       = platforms,
        build_args      = build_args,
        extra_inspect   = extra_inspect,
        placement       = placement,
    )


def _http(name: str, port: int, **kwargs) -> Base:
    return _base(
        name,
        category    = "web",
        ports       = (Port("http", port),),
        health_type = "http",
        health_path = "/",
        connection  = "{url}",
        **kwargs,
    )


def _tcp(name: str, **kwargs) -> Base:
    return _base(
        name,
        ports       = (Port("tcp", 5000),),
        connection  = "nc {host} {port}",
        **kwargs,
    )


BASES: tuple[Base, ...] = (
    _http(
        "base/web-nginx",   80,
        template   = "web-nginx",
        flag_owner = "nginx:nginx",
    ),
    _http(
        "base/web-php-nginx",   80,
        template = "web-php-nginx",
    ),
    _http(
        "base/web-php-apache",  80,
        template = "web-php-apache",
    ),
    _http(
        "base/web-flask",   5000,
        template = "web-flask",
    ),
    _http(
        "base/web-fastapi", 8000,
        template = "web-fastapi",
    ),
    _http(
        "base/web-express", 8000,
        template = "web-express",
    ),
    _http(
        "base/web-sinatra", 4567,
        template = "web-sinatra",
    ),
    _http("base/web-spring", 8080,          reason=_LATER_BUILDER),
    _http("base/web-native", 8080,          reason=_LATER_BUILDER),
    _tcp("base/pwn-socat",  category="pwn", reason=_LATER_BINARY),
    _tcp("base/pwn-xinetd", category="pwn", reason=_LATER_BINARY),
    _tcp("base/pwn-qemu",   category="pwn", reason=_LATER_BINARY),
    _tcp(
        "base/pwn-qemu-system",
        category = "pwn",
        reason   = _LATER_BINARY,
    ),
    _tcp(
        "base/python-socat",
        template = "python-socat",
    ),
    _tcp(
        "base/nodejs-socat",
        template = "nodejs-socat",
    ),
    # Language runtime bases. 8080 is only a default for `hg challenge scaffold`;
    # the versioned tag is chosen in the challenge Dockerfile via the build arg
    # (for example base/java pinned with `JAVA_VERSION=8`).
    _http("base/java",   8080, template="java", memory_mb=512, cpu_mhz=500),
    _http("base/python", 8080, template="python"),
    _http("base/nodejs", 8080, template="nodejs"),
    _http("base/ruby",   8080, template="ruby"),
    _http("base/php",    8080, template="php"),
    _base(
        "base/eth",
        category = "blockchain",
        reason   = _LATER_CHAIN,
        ports    = (Port("rpc", 8545), Port("http", 8080)),
    ),
    _base(
        "base/cairo",
        category = "blockchain",
        reason   = _LATER_CHAIN,
        ports    = (Port("rpc", 5050), Port("http", 8080)),
    ),
    _base(
        "base/solana",
        category  =  "blockchain",
        reason    =  _LATER_CHAIN,
        ports     =  (Port("rpc", 8899), Port("http", 8080)),
        platforms = ("linux/amd64",),
    ),
    _base(
        "bot/xss-puppet",
        category  = "web",
        reason    = "companion service, not a primary challenge base",
        ports     = (Port("report", 3000, publish=False),),
        memory_mb = 2048,
        cpu_mhz   = 500,
    ),
    _base(
        "range/root",
        category    = "range",
        runtime     = "scenario",
        member      = "box",
        reason      = "host template in this cut is range/debian",
        health_type = "ready-file",
        health_path = "/run/hg/ready",
        connection  = "",
        flag_path   = "/flag",
        flag_owner  = "root:root",
        memory_mb   = 256,
        cpu_mhz     = 500,
        placement   = "kata",
    ),
    _base(
        "range/debian",
        template         = "range-debian",
        category         = "range",
        runtime          = "scenario",
        member           = "box",
        ports            = (Port("ssh", 22),),
        health_type      = "ready-file",
        health_path      = "/run/hg/ready",
        connection       = "ssh -p {port} player@{host}",
        flag_path        = "/flag",
        flag_owner       = "root:root",
        memory_mb        = 256,
        cpu_mhz          = 500,
        extra_inspect    = ("range/pack/sshd:latest",),
        extra_build_args = (("RANGE_PACK_SSHD_IMAGE", "range/pack/sshd:latest"),),
        placement        = "kata",
    ),
)

BY_NAME   = {base.name:  base for base in BASES}
BY_IMAGE  = {base.image: base for base in BASES}
ALLOWLIST = frozenset(BY_IMAGE)


def _builder(
    name:                 str,
    *,
    note:                 str       = "",
    platforms:      tuple[str, ...] = PLATFORMS,
) -> Builder:
    return Builder(
        name        = name,
        image       = f"{name}:latest",
        platforms   = platforms,
        note        = note,
    )


BUILDERS: tuple[Builder, ...] = (
    _builder("builder/common",      note = "shared compile helpers"),
    _builder("builder/cpp",         note = "gcc, clang, cmake"),
    _builder("builder/cpp-cross",   note = "GNU cross gcc and sysroots"),
    _builder("builder/rust",        note = "rustc and cargo"),
    _builder("builder/go",          note = "Go with cgo"),
    _builder("builder/java",        note = "Temurin JDK, Maven, Gradle"),
    _builder("builder/zig",         note = "zig and zig cc"),
    _builder("builder/foundry",     note = "forge"),
    _builder("builder/scarb",       note = "scarb and starknet-devnet"),
    _builder("builder/kafel",       note = "kafel to BPF"),
    _builder("builder/anchor",      note = "Anchor and Agave", platforms = ("linux/amd64",)),
)

BY_BUILDER_NAME  = {builder.name:  builder for builder in BUILDERS}
BY_BUILDER_IMAGE = {builder.image: builder for builder in BUILDERS}


## Main Functions --------------------------------------------------------------
def hub_ref(image: str) -> str:
    """
    Docker Hub reference for a base tag.

    `base/web-nginx:latest` becomes `hunterground/base-web-nginx:latest`.
    The registry path is flattened: every `/` becomes `-`.
    """
    name, sep, tag = image.partition(":")
    leaf = name.replace("/", "-")
    return f"{HUB_NAMESPACE}/{leaf}{sep}{tag}" if sep else f"{HUB_NAMESPACE}/{leaf}"


def challenge_image_name(
    category:       str,
    challenge_id:   str,
    member:         str,
    *,
    multiple:       bool,
) -> str:
    """
    Generate a registry image name without a tag.

    One image is `challenges/<category>/<id>`. 
    Several images suffix the member: `challenges/<category>/<id>-<member>`.
    """
    slug = f"{challenge_id}-{member}" if multiple else challenge_id
    return f"challenges/{category}/{slug}"


def base_for_image(image: str) -> Base | None:
    """
    Resolve a FROM image to a catalog base.

    `base/ruby:latest` matches directly. A version tag such as
    `base/ruby:2.7` matches the same base; the challenge manifest's
    `fromDigest` selects the built image.
    """
    found = BY_IMAGE.get(image)
    if found is not None:
        return found

    name, sep, tag = image.rpartition(":")
    if not sep or "/" in tag or not tag or "@" in image:
        return None

    return BY_IMAGE.get(f"{name}:latest")


def find_base(name: str) -> Base:
    key = name.strip()
    if key in BY_NAME:
        return BY_NAME[key]

    matches = [base for base in BASES if base.name.split("/", 1)[-1] == key]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        known = ", ".join(base.name for base in BASES)
        raise ToolError(f"unknown base {name!r}. Known bases: {known}")

    names = ", ".join(base.name for base in matches)
    raise ToolError(f"base {name!r} is ambiguous: {names}")


def require_template(name: str) -> Base:
    base = find_base(name)
    if base.template is None:
        raise ToolError(f"{base.name} has no template in this cut: {base.reason}")
    return base


def _short_names(items: Iterable[Base | Builder]) -> list[str]:
    """
    The leaf names a user may type, dropping any that are ambiguous.

    ``find_base`` accepts the short form (`debian` for `range/debian`) only
    when it resolves to one base, so a name that would be rejected is not
    offered. 
    ``find_builder`` uses the same rule (`cpp` for `builder/cpp`).
    """
    leaves = [item.name.split("/", 1)[-1] for item in items]
    return sorted(name for name in set(leaves) if leaves.count(name) == 1)

def base_names() -> list[str]:
    """Every base name, in the short form `hg bases pull` accepts."""
    return _short_names(BASES)

def templates() -> list[str]:
    """
    Short names of bases that have a scaffold template.

    Only a base with a template can scaffold an image tree, and `scaffold`
    refuses the rest before writing. ``none`` is not a base; ``scaffold_bases``
    adds it for the scaffold flag.
    """
    return _short_names(base for base in BASES if base.template)


def scaffold_bases() -> list[str]:
    """The names ``hg challenge scaffold --base`` accepts, including ``none``."""
    return sorted((*templates(), "none"))

def base_rows() -> list[tuple[str, str, str, str]]:
    """
    The catalog as rows for a caller that wants to style the columns.

    Returns `(name, status, listen, note)`. `status` is `scaffold` when a
    template exists and `later` otherwise. The caller decides the color;
    `format_bases` is the plain-text rendering of the same rows.
    """
    rows: list[tuple[str, str, str, str]] = []
    for base in BASES:
        status = "scaffold" if base.template else "later"
        if base.ports:
            listen = " ".join(
                f"{port.container}/{port.protocol}" + ("" if port.publish else " unpublished")
                for port in base.ports
            )
        elif base.health_type == "ready-file":
            listen = "ready-file"
        else:
            listen = "-"
        note = base.reason or base.connection
        rows.append((base.name, status, listen, note))
    return rows


def base_header() -> tuple[str, str, str, str]:
    """The column labels for `base_rows`, padded to `BASE_WIDTHS`."""
    return (
        f"{BASE_HEADER[0]:<{BASE_WIDTHS[0]}}",
        f"{BASE_HEADER[1]:<{BASE_WIDTHS[1]}}",
        f"{BASE_HEADER[2]:<{BASE_WIDTHS[2]}}",
        BASE_HEADER[3],
    )

def builder_for_image(image: str) -> Builder | None:
    """
    Resolve a FROM image to a catalog builder.

    `builder/cpp:latest` matches directly. Another tag on the same name
    matches that builder too.
    """
    found = BY_BUILDER_IMAGE.get(image)
    if found is not None:
        return found

    name, sep, tag = image.rpartition(":")
    if not sep or "/" in tag or not tag or "@" in image:
        return None

    return BY_BUILDER_IMAGE.get(f"{name}:latest")


def find_builder(name: str) -> Builder:
    key = name.strip()
    if key in BY_BUILDER_NAME:
        return BY_BUILDER_NAME[key]

    matches = [builder for builder in BUILDERS if builder.name.split("/", 1)[-1] == key]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        known = ", ".join(builder.name for builder in BUILDERS)
        raise ToolError(f"unknown builder {name!r}. Known builders: {known}")

    names = ", ".join(builder.name for builder in matches)
    raise ToolError(f"builder {name!r} is ambiguous: {names}")


def builder_names() -> list[str]:
    """Every builder name, in the short form `hg builders pull` accepts."""
    return _short_names(BUILDERS)


def builder_rows() -> list[tuple[str, str, str]]:
    """
    The builder catalog as rows for a caller that wants to style the columns.

    Returns `(name, platforms, note)`.
    """
    return [
        (builder.name, ", ".join(builder.platforms), builder.note)
        for builder in BUILDERS
    ]


def format_bases() -> str:
    header = base_header()
    lines = [f"{header[0]} {header[1]} {header[2]} {header[3]}"]
    for name, status, listen, note in base_rows():
        lines.append(
            f"{name:<{BASE_WIDTHS[0]}} {status:<{BASE_WIDTHS[1]}} "
            f"{listen:<{BASE_WIDTHS[2]}} {note}"
        )
    return "\n".join(lines)
