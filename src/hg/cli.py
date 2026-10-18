"""
Command line for the local challenge toolkit.

This module provides the command line interface for the local challenge toolkit.
It is used to create, build, run, and clean challenges.
It also provides commands to interact with the contest system.
"""

from collections.abc    import Callable
from dataclasses        import asdict
from pathlib            import Path
from hg.api             import Session
from hg.build           import build_challenge
from hg.catalog         import (
    CATEGORIES, 
    base_names, 
    base_rows, 
    builder_names, 
    builder_rows, 
    scaffold_bases,
    templates,
)
from hg.clean           import clean_challenge
from hg.pull            import pull_bases, pull_builders
from hg.remove          import (
    ImageRemoveError,
    clean_bases,
    clean_builders,
    remove_bases,
    remove_builders,
)
from hg.complete        import (
    COMMAND as COMPLETE_COMMAND,
    SHELLS,
    Completer,
    Value,
    answer,
    challenge_files,
    directories,
    files,
    script,
)
from hg.contest         import (
    PARTICIPATIONS,
    contest_root,
    contest_type,
    contest_types,
    create_contest_tree,
    digest_contest,
    discover_challenges,
    lint_contest,
)
from hg.digest          import digest_challenge
from hg.errors          import ToolError
from hg.help            import error_body, humanize, render, terminal_width
from hg.images          import list_images, push_images
from hg.lint            import challenge_root, lint_challenge
from hg.remote          import (
    add_challenge,
    create_contest,
    deploy_contest,
    publish_contest,
    push_challenge,
    push_material,
    upload_handout,
)
from hg.run             import run_challenge, stop_challenge
from hg.scaffold        import create_challenge
from hg.status          import status_challenge
from hg.solve           import solve_challenge
from hg.state           import write_state
from hg                 import style
from hg.verify          import verify_challenge
import argparse
import getpass
import sys


class _HelpParser(argparse.ArgumentParser):
    """
    Argument parser with a short usage line, grouped commands, and an example.

    `summary` is the sentence at the top of `--help`. `example` is the
    trailing examples block. `section` is the heading this command is listed
    under in its parent's help. A missing or unknown command repeats the
    summary and the example, so the error still shows what to run.
    """

    _leaf: "_HelpParser"

    def __init__(self,
        *args:      object,
        summary:    str = "",
        example:    str = "",
        section:    str = "",
        **kwargs:   object,
    ) -> None:
        kwargs.setdefault("formatter_class", argparse.RawDescriptionHelpFormatter)
        super().__init__(*args, **kwargs)
        self.summary = summary
        self.example = example
        self.section = section

    def parse_known_args(self, args=None, namespace=None):  # type: ignore[no-untyped-def]
        _HelpParser._leaf = self
        return super().parse_known_args(args, namespace)

    def parse_args(self, args=None, namespace=None):  # type: ignore[no-untyped-def]
        args, argv = self.parse_known_args(args, namespace)
        if argv:
            _HelpParser._leaf.error("unrecognized arguments: " + " ".join(argv))
        return args

    def format_help(self) -> str:
        return render(self, width=terminal_width(sys.stdout))

    def print_help(self, file: object = None) -> None:
        stream = sys.stdout if file is None else file
        text = render(self, width=terminal_width(stream))
        _write_help(stream, text)

    def error(self, message: str) -> None:
        style.fail(f"{self.prog}: {humanize(message)}")
        sys.stderr.write(error_body(self, width=terminal_width(sys.stderr)))
        self.exit(2)


## Helper Functions ---------------------------------------------------------------------------------
def _session() -> Session:
    return Session.open()


def _write_help(stream: object, text: str) -> None:
    """Write help, with section headings in bold when the stream is a terminal."""
    isatty = getattr(stream, "isatty", None)
    if not callable(isatty) or not isatty():
        stream.write(text)  # type: ignore[attr-defined]
        return

    from rich.console import Console
    from rich.text    import Text

    console = Console(file=stream, markup=False, highlight=False, soft_wrap=True)
    first = True
    for line in text.splitlines():
        if first and line.strip():
            console.print(Text(line, style="bold"))
            first = False
            continue
        if line.endswith(":") and line == line.lstrip() and line.strip():
            console.print(Text(line, style="bold"))
            continue
        console.print(line)


## Commands ------------------------------------------------------------------------------------------

#### -- Authorization --------------------------------------------------------------------------------
def _cmd_login(args: argparse.Namespace) -> None:
    """Authenticate to the platform and store the token for the API access"""
    password = getpass.getpass("Password: ")
    user     = Session.blank().login(args.url, args.email, password)
    style.ok("logged in as ", style.value(user.get("email") or args.email))


def _cmd_logout(_args: argparse.Namespace) -> None:
    """Revoke the session and remove stored token"""
    _session().logout()
    style.ok("logged out")


def _cmd_whoami(_args: argparse.Namespace) -> None:
    """Print the account information"""
    user = _session().whoami()
    style.table([
        (style.dim("email"),    style.value(user.get("email", ""))),
        (style.dim("username"), style.value(user.get("username", ""))),
        (style.dim("role"),     style.label(user.get("role", ""))),
    ])


## -- Contest ------------------------------------------------------------------------------------------
## ---- Contest Administration -------------------------------------------------------------
def _cmd_contest_create(args: argparse.Namespace) -> None:
    """
    Create a contest draft on platform via API.
    Contest must be published to be visible to the players.
    """
    resolved = contest_type(args.contest_type)
    participation = args.participation or resolved.default
    if participation not in resolved.allowed:
        allowed = ", ".join(resolved.allowed)
        raise ToolError(
            f"contest type {resolved.name!r} does not allow participation {participation!r} "
            f"(allowed: {allowed})"
        )
    data = create_contest(
        _session().client(),
        slug          = args.slug,
        title         = args.title,
        starts_at     = args.starts_at,
        ends_at       = args.ends_at,
        description   = args.description,
        rules         = args.rules,
        kind          = resolved.kind,
        mode          = resolved.mode,
        participation = participation,
    )
    style.ok("created ", style.value(data.get("slug", args.slug)))


def _cmd_contest_add(args: argparse.Namespace) -> None:
    """
    Link a challenge that is already on the platform.
    """
    root = challenge_root(Path(args.path)) if args.path else None
    linked = add_challenge(
        _session().client(),
        args.slug,
        args.challenge,
        root       = root,
        points     = args.points,
        sort_order = args.sort_order,
    )
    style.ok(
        "linked ",
        style.value(linked["challenge"]),
        " to ",
        style.value(linked["contest"]),
        " (",
        style.label(linked["points"]),
        " points)",
    )


def _cmd_contest_publish(args: argparse.Namespace) -> None:
    """
    Publish a contest
    """
    data = publish_contest(_session().client(), args.slug)
    style.ok("published ", style.value(data.get("slug") or args.slug))

## ---- Manifest Loading ----------------------------------------------------------------
def _cmd_contest_scaffold(args: argparse.Namespace) -> None:
    """
    Write a contest tree from a type.
    """
    dest = create_contest_tree(
        args.id,
        Path(args.dest),
        title         = args.title,
        kind          = args.contest_type,
        starts_at     = args.starts_at,
        ends_at       = args.ends_at,
        description   = args.description,
        rules         = args.rules,
        participation = args.participation,
    )
    style.ok("created ", style.value(dest))

def _cmd_contest_lint(args: argparse.Namespace) -> None:
    root   = contest_root(Path(args.path) if args.path else None)
    errors = lint_contest(root, strict=args.strict)
    if errors:
        raise ToolError("\n".join(errors))
    style.ok("lint passed")

def _cmd_contest_digest(args: argparse.Namespace) -> None:
    style.say(style.value(digest_contest(Path(args.path) if args.path else None)))

def _cmd_contest_challenges(args: argparse.Namespace) -> None:
    root = contest_root(Path(args.path) if args.path else None)
    lint_errors = lint_contest(root)
    if lint_errors:
        raise ToolError("\n".join(lint_errors))

    entries = discover_challenges(root)
    if not entries:
        style.say(style.dim("no challenges"))
        return
    style.table(
        [
            (
                style.dim(entry.sort_order),
                style.value(entry.slug),
                style.label(entry.points),
                style.dim(entry.state),
                style.dim(entry.path),
            )
            for entry in entries
        ],
        header = ("ORDER", "CHALLENGE", "POINTS", "STATE", "PATH"),
    )

def _cmd_contest_deploy(args: argparse.Namespace) -> None:
    root = contest_root(Path(args.path) if args.path else None)
    result = deploy_contest(_session().client(), root)
    if result["action"]:
        style.ok(style.value(result["action"]), " ", style.value(result["contest"]))

    for item in result["linked"]:
        style.ok(
            "linked ",
            style.value(item["challenge"]),
            " to ",
            style.value(item["contest"]),
            " (",
            style.label(item["points"]),
            " points)",
        )
        if item.get("handout"):
            style.ok(
                "uploaded ",
                style.value(item["handout"]),
                " (",
                style.label(item.get("handout_bytes", 0)),
                " bytes)",
            )
    if result["published"]:
        style.ok("published ", style.value(result["contest"]))


def _cmd_push(args: argparse.Namespace) -> None:
    root = challenge_root(Path(args.path) if args.path else None)
    client = _session().client()
    action, data = push_challenge(
        client,
        root,
        image      = args.image,
        difficulty = args.difficulty,
        publish    = args.publish,
    )
    style.ok(
        style.value(action),
        " ",
        style.value(data.get("slug", "")),
        " ",
        style.dim(data.get("status", "")),
    )
    handout = Path(args.file) if args.file else None
    try:
        uploaded = upload_handout(client, root, handout)
    except ToolError as exc:
        raise ToolError(f"{root.name}: challenge record {action}; {exc}") from exc
    if uploaded is not None:
        name, size = uploaded
        style.ok("uploaded ", style.value(name), " (", style.label(size), " bytes)")


def _cmd_material(args: argparse.Namespace) -> None:
    name, size = push_material(
        _session().client(),
        challenge_root(Path(args.path)  if args.path else None),
        Path(args.file)                 if args.file else None,
    )
    style.ok("uploaded ", style.value(name), " (", style.label(size), " bytes)")


## -- Completion ------------------------------------------------------------------------------------
def _cmd_completion(args: argparse.Namespace) -> None:
    """Print the shell script, so the caller can eval or source it."""
    sys.stdout.write(script(args.shell))

# The arguments of the private command, parsed on its own.
#
# The command is not in the parser tree on purpose: it is not a command a user
# runs, so it stays out of the command list and out of the usage line. The
# scripts the toolkit prints are the only callers.
_COMPLETE = argparse.ArgumentParser(prog=f"hg {COMPLETE_COMMAND}", add_help=False)
_COMPLETE.add_argument("--current", default="")
# Everything after `--` is the line, passed through verbatim.
_COMPLETE.add_argument("tokens", nargs=argparse.REMAINDER)

def _cmd_complete(argv: list[str]) -> None:
    """
    Print the candidates for a partial line, one per line.

    A shell script calls this on every completion, so every failure prints
    nothing: a completion that cannot answer must not write over the prompt.
    """
    args   = _COMPLETE.parse_args(argv)
    tokens = args.tokens[1:] if args.tokens[:1] == ["--"] else args.tokens
    try:
        sys.stdout.write(answer(build_parser(), tokens, args.current))
    except Exception:
        return
    sys.stdout.flush()

## -- Images Helper --------------------------------------------------------------------------------
def _cmd_bases(_args: argparse.Namespace) -> None:

    style.table(
        [
            (
                style.value(name),
                style.success(status) if status == "scaffold" else style.dim(status),
                style.dim(listen),
                style.dim(note),
            )
            for name, status, listen, note in base_rows()
        ],
        header = ("BASE", "STATUS", "LISTEN", "NOTE"),
    )

def _cmd_bases_pull(args: argparse.Namespace) -> None:
    result = pull_bases(args.name or None)
    for tag in result.pulled:
        style.say(style.dim("pulled"), "  ", style.value(tag))
    if result.missing:
        listed = ", ".join(result.missing)
        raise ToolError(f"cannot fetch base image(s): {listed}")


def _cmd_builders(_args: argparse.Namespace) -> None:
    style.table(
        [
            (style.value(name), style.dim(platforms), style.dim(note))
            for name, platforms, note in builder_rows()
        ],
        header = ("BUILDER", "PLATFORMS", "NOTE"),
    )


def _cmd_builders_pull(args: argparse.Namespace) -> None:
    result = pull_builders(args.name or None)
    for tag in result.pulled:
        style.say(style.dim("pulled"), "  ", style.value(tag))
    if result.missing:
        listed = ", ".join(result.missing)
        raise ToolError(f"cannot fetch builder image(s): {listed}")


def _say_removed(tags: tuple[str, ...]) -> None:
    for tag in tags:
        style.say(style.dim("removed"), "  ", style.value(tag))


def _finish_remove(action: Callable[[], tuple[str, ...]]) -> None:
    try:
        removed = action()
    except ImageRemoveError as exc:
        _say_removed(exc.removed)
        raise
    _say_removed(removed)


def _cmd_bases_remove(args: argparse.Namespace) -> None:
    _finish_remove(lambda: remove_bases(args.name))


def _cmd_bases_clean(_args: argparse.Namespace) -> None:
    _finish_remove(clean_bases)


def _cmd_builders_remove(args: argparse.Namespace) -> None:
    _finish_remove(lambda: remove_builders(args.name))


def _cmd_builders_clean(_args: argparse.Namespace) -> None:
    _finish_remove(clean_builders)


def _cmd_images_list(args: argparse.Namespace) -> None:
    root = challenge_root(Path(args.path) if args.path else None)
    rows = list_images(root)
    style.table(
        [
            (
                style.value(row.member),
                style.value(row.local) if row.present else style.dim(row.local),
                style.value(row.reference) if row.reference else style.dim("-"),
            )
            for row in rows
        ],
        header = ("MEMBER", "LOCAL", "REFERENCE"),
    )


def _cmd_images_push(args: argparse.Namespace) -> None:
    root = challenge_root(Path(args.path) if args.path else None)
    for item in push_images(root, args.namespace):
        style.say(style.dim(item.member), "  ", style.value(item.reference))


def _cmd_scaffold(args: argparse.Namespace) -> None:
    dest = create_challenge(
        base_name    = args.base,
        challenge_id = args.id,
        dest         = Path(args.dest),
        title        = args.title,
        author       = args.author,
        category     = args.category,
    )
    style.ok("created ", style.value(dest))


def _cmd_lint(args: argparse.Namespace) -> None:
    root = challenge_root(Path(args.path) if args.path else None)
    errors = lint_challenge(root, strict=args.strict)
    if errors:
        raise ToolError("\n".join(errors))
    style.ok("lint passed")


def _cmd_digest(args: argparse.Namespace) -> None:
    style.say(style.value(digest_challenge(Path(args.path) if args.path else None)))


def _cmd_build(args: argparse.Namespace) -> None:
    root = challenge_root(Path(args.path) if args.path else None)
    tags = build_challenge(root)
    for tag in tags:
        style.say(style.dim("built"), "  ", style.value(tag))


def _cmd_run(args: argparse.Namespace) -> None:
    root = challenge_root(Path(args.path) if args.path else None)
    result = run_challenge(root, timeout=args.timeout)
    write_state(
        root,
        {
            "connection":           result.connection,
            "host":                 result.host,
            "port":                 result.port,
            "entry_container":      result.entry_container,
            "entry_container_port": result.entry_container_port,
            "flags":                [asdict(flag)      for flag      in result.flags],
            "users":                [asdict(user)      for user      in result.users],
            "containers":           [asdict(container) for container in result.containers],
        },
    )
    rows: list[tuple[object, ...]] = []
    if result.connection:
        rows.append((style.dim("connection"), style.value(result.connection)))
    for flag in result.flags:
        rows.append((style.dim("flag"), style.label(flag.id), style.value(flag.content)))
    for user in result.users:
        who = f"{user.member} {user.name}".strip()
        rows.append((style.dim("user"), style.dim(who), style.value(user.password)))
    for container in result.containers:
        rows.append((style.dim("container"), style.dim(container.name), style.dim(container.image)))
    if rows:
        style.table(rows)


def _cmd_solve(args: argparse.Namespace) -> None:
    root = challenge_root(Path(args.path) if args.path else None)
    outcome = solve_challenge(
        root,
        args.script,
        host = args.host,
        port = args.port,
    )
    env = outcome.env
    for warning in outcome.warnings:
        style.warn(warning)
    if outcome.stdout:
        # Solver output is raw and must reach the pipe byte for byte, so it is
        # written straight through rather than rendered.
        sys.stdout.write(outcome.stdout)
        if not outcome.stdout.endswith("\n"):
            sys.stdout.write("\n")
    if env.port:
        style.dim_err(f"# target {env.connection}")
    if not outcome.ok:
        reason = "solver exited non-zero" if outcome.code != 0 else "solver did not print the flag"
        style.fail(reason)
        sys.exit(1)


def _cmd_verify(args: argparse.Namespace) -> None:
    root = challenge_root(Path(args.path) if args.path else None)
    verify_challenge(root, timeout=args.timeout)
    style.ok("verified")

def _cmd_status(args: argparse.Namespace) -> None:
    root = challenge_root(Path(args.path) if args.path else None)
    report = status_challenge(root)
    rows: list[tuple[object, ...]] = [
        (style.dim("status"), style.value(report.summary), style.dim(report.challenge_id)),
    ]
    if report.connection:
        rows.append((style.dim("connection"), style.value(report.connection)))
    for flag_id, content in report.flags:
        rows.append((style.dim("flag"), style.label(flag_id), style.value(content)))
    for who, password in report.users:
        rows.append((style.dim("user"), style.dim(who), style.value(password)))
    for container in report.containers:
        state = style.success(container.state) if container.state == "running" else style.dim(container.state)
        rows.append((
            style.dim("container"),
            style.dim(container.name),
            style.dim(container.image),
            state,
            style.dim(container.detail),
            style.dim(container.ports),
        ))
    for name, present in report.networks:
        rows.append((style.dim("network"), style.dim(name), style.dim("present" if present else "missing")))
    if report.health:
        health = style.success(report.health) if report.health == "ok" else style.dim(report.health)
        rows.append((style.dim("health"), health))
    if rows:
        style.table(rows)
    for warning in report.warnings:
        style.warn(warning)
    if not report.ok:
        sys.exit(1)


def _cmd_stop(args: argparse.Namespace) -> None:
    root = challenge_root(Path(args.path) if args.path else None)
    for name in stop_challenge(root):
        style.say(style.dim("stopped"), "  ", style.dim(name))


def _cmd_clean(args: argparse.Namespace) -> None:
    root = challenge_root(Path(args.path) if args.path else None)
    for tag in clean_challenge(root):
        style.say(style.dim("removed"), "  ", style.value(tag))


## Parser ------------------------------------------------------------------------------------------
def _flag(
    parser:         argparse.ArgumentParser,
    *flags:         str,
    completer:      Completer   | None = None,
    help:           str         | None = None,
    **kwargs:       object,
) -> None:
    """
    Register a flag whose value the shell can complete.

    Every value that is a base name, a contest type, or a path on disk goes
    through here, so `hg __complete` offers the same list the command reads.
    """
    parser.add_argument(*flags, action=Value, completer=completer, help=help, **kwargs)

_CONTEST_TYPES = "open, mini-open, public-competition, private-competition, or hiring-test"
_PARTICIPATION = "individual or team. Defaults to the type's default"
_CONTEST_PATH  = "Contest directory. Defaults to the current directory"
_CHALLENGE_PATH = "Challenge directory. Defaults to the current directory"


def build_parser() -> _HelpParser:
    """
    Build the `hg` command tree.

    The tree is the single source of truth for the command line: parsing
    arguments and completing a partial line both read it, so a new command is
    completable as soon as it is added here.
    """
    parser = _HelpParser(
        prog    = "hg",
        summary = "Author, run, and publish HunterGround challenges and contests.",
        example = "hg challenge run --path ../web-xss-01",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    ## hg login
    login = sub.add_parser(
        "login",
        help    = "Sign in and store an API token",
        summary = "Sign in and store an API token.",
        example = "hg login --email staff@example.com",
        section = "Account",
    )
    login.add_argument("--url",   help=argparse.SUPPRESS)
    login.add_argument("--email", required=True, help="Account email")
    login.set_defaults(func=_cmd_login)

    ## hg logout
    logout = sub.add_parser(
        "logout",
        help    = "Sign out and remove the stored token",
        summary = "Sign out and remove the stored token.",
        example = "hg logout",
        section = "Account",
    )
    logout.set_defaults(func=_cmd_logout)

    ## hg whoami
    whoami = sub.add_parser(
        "whoami",
        help    = "Show the signed-in account",
        summary = "Show the signed-in account.",
        example = "hg whoami",
        section = "Account",
    )
    whoami.set_defaults(func=_cmd_whoami)

    ## hg contest
    contest = sub.add_parser(
        "contest",
        help    = "Scaffold, check, and deploy a contest",
        summary = "Scaffold, check, and deploy a contest.",
        example = "hg contest scaffold --id spring-2026 --type open --dest ../spring-2026",
        section = "Contests",
    )
    contest_sub = contest.add_subparsers(dest="contest_command", required=True)

    ## hg contest create
    contest_create = contest_sub.add_parser(
        "create",
        help    = "Create a draft contest on the platform",
        summary = "Create a draft contest on the platform.",
        example = "\n".join((
            "hg contest create \\",
            "  --type private-competition \\",
            "  --slug acme-2026 \\",
            '  --title "Acme 2026" \\',
            "  --starts-at 2026-04-01T00:00:00Z \\",
            "  --ends-at 2026-04-03T00:00:00Z",
        )),
        section = "Platform",
    )
    contest_create.add_argument("--slug",        required=True, help="Contest slug")
    contest_create.add_argument("--title",       required=True, help="Contest title")
    contest_create.add_argument("--starts-at",   required=True, help="Start time, RFC 3339 (2026-04-01T00:00:00Z)")
    contest_create.add_argument("--ends-at",     required=True, help="End time, RFC 3339")
    contest_create.add_argument("--description", default="",    help="Contest description")
    contest_create.add_argument("--rules",       default="",    help="Contest rules")
    _flag(contest_create, "--type",
        dest      = "contest_type",
        required  = True,
        completer = tuple(contest_types()),
        help      = _CONTEST_TYPES,
    )
    _flag(contest_create, "--participation",
        completer = PARTICIPATIONS,
        help      = _PARTICIPATION,
    )
    contest_create.set_defaults(func=_cmd_contest_create)

    ## hg contest add-challenge
    contest_add = contest_sub.add_parser(
        "add-challenge",
        help    = "Link a challenge that is already on the platform",
        summary = "Link a challenge that is already on the platform.",
        example = "hg contest add-challenge acme-2026 web-xss-01 --points 250",
        section = "Platform",
    )
    contest_add.add_argument("slug",      help="Contest slug")
    contest_add.add_argument("challenge", help="Platform challenge slug")
    _flag(contest_add, "--path", completer=directories,
          help="Local challenge tree. Points then default to metadata.value")
    contest_add.add_argument("--points",     type=int, help="Contest points. Required when --path is omitted")
    contest_add.add_argument("--sort-order", type=int, default=0, help="Position in the contest. Defaults to 0")
    contest_add.set_defaults(func=_cmd_contest_add)

    ## hg contest publish
    contest_publish = contest_sub.add_parser(
        "publish",
        help    = "Publish a contest so players can see it",
        summary = "Publish a contest so players can see it.",
        example = "hg contest publish spring-2026",
        section = "Platform",
    )
    contest_publish.add_argument("slug", help="Contest slug")
    contest_publish.set_defaults(func=_cmd_contest_publish)

    ## hg contest scaffold
    contest_scaffold = contest_sub.add_parser(
        "scaffold",
        help    = "Write a contest tree from a type",
        summary = "Write a contest tree from a type.",
        example = "\n".join((
            "hg contest scaffold \\",
            "  --id spring-2026 \\",
            '  --title "Spring 2026" \\',
            "  --type open \\",
            "  --starts-at 2026-04-01T00:00:00Z \\",
            "  --ends-at 2026-04-03T00:00:00Z \\",
            "  --dest ../spring-2026",
        )),
        section = "Author",
    )
    contest_scaffold.add_argument("--id",    required=True, help="Contest id, stored as metadata.id")
    contest_scaffold.add_argument("--title", help="Contest title. Defaults to the id")
    _flag(contest_scaffold, "--type",
        dest      = "contest_type",
        required  = True,
        completer = tuple(contest_types()),
        help      = _CONTEST_TYPES,
    )
    contest_scaffold.add_argument("--starts-at",   required=True, help="Start time, RFC 3339 (2026-04-01T00:00:00Z)")
    contest_scaffold.add_argument("--ends-at",     required=True, help="End time, RFC 3339")
    contest_scaffold.add_argument("--description", default="",    help="Contest description")
    contest_scaffold.add_argument("--rules",       default="",    help="Contest rules")
    _flag(contest_scaffold, "--participation",
        completer = PARTICIPATIONS,
        help      = _PARTICIPATION,
    )
    _flag(contest_scaffold, "--dest", required=True, completer=directories, help="Directory to create")
    contest_scaffold.set_defaults(func=_cmd_contest_scaffold)

    ## hg contest lint
    contest_lint = contest_sub.add_parser(
        "lint",
        help    = "Check contest.yaml and every challenge in it",
        summary = "Check contest.yaml and every challenge in it.",
        example = "hg contest lint --path ../spring-2026",
        section = "Author",
    )
    _flag(contest_lint, "--path", completer=directories, help=_CONTEST_PATH)
    contest_lint.add_argument("--strict",
        action = "store_true",
        help   = "Also run strict lint on every challenge",
    )
    contest_lint.set_defaults(func=_cmd_contest_lint)

    ## hg contest digest
    contest_digest = contest_sub.add_parser(
        "digest",
        help    = "Print the canonical contest manifest digest",
        summary = "Print the canonical contest manifest digest.",
        example = "hg contest digest --path ../spring-2026",
        section = "Author",
    )
    _flag(contest_digest, "--path", completer=directories, help=_CONTEST_PATH)
    contest_digest.set_defaults(func=_cmd_contest_digest)

    ## hg contest challenges
    contest_challenges = contest_sub.add_parser(
        "challenges",
        help    = "List discovered challenges, order, and points",
        summary = "List discovered challenges, order, and points.",
        example = "hg contest challenges --path ../spring-2026",
        section = "Author",
    )
    _flag(contest_challenges, "--path", completer=directories, help=_CONTEST_PATH)
    contest_challenges.set_defaults(func=_cmd_contest_challenges)

    ## hg contest deploy
    contest_deploy = contest_sub.add_parser(
        "deploy",
        help    = "Push the contest, its challenges, and their handouts",
        summary = "Push the contest, its challenges, and their handouts.",
        example = "hg contest deploy --path ../spring-2026",
        section = "Platform",
    )
    _flag(contest_deploy, "--path", completer=directories, help=_CONTEST_PATH)
    contest_deploy.set_defaults(func=_cmd_contest_deploy)

    ## hg bases
    bases = sub.add_parser(
        "bases",
        help    = "List, pull, or remove base images",
        summary = "List, pull, or remove the base images this toolkit knows.",
        example = "hg bases pull",
        section = "Images",
    )
    bases_sub = bases.add_subparsers(dest="bases_command", required=True)

    ## hg bases list
    bases_list = bases_sub.add_parser(
        "list",
        help    = "List the base images this toolkit knows",
        summary = "List the base images this toolkit knows.",
        example = "hg bases list",
    )
    bases_list.set_defaults(func=_cmd_bases)

    ## hg bases pull
    bases_pull = bases_sub.add_parser(
        "pull",
        help    = "Pull base images and retag them locally",
        summary = "Pull base images from Docker Hub and retag them locally.",
        example = "hg bases pull web-nginx",
    )
    bases_pull.add_argument("name", nargs="*", completer=base_names(), action=Value,
                            help="Base to pull. Repeat to pull several. Defaults to every base")
    bases_pull.set_defaults(func=_cmd_bases_pull)

    ## hg bases remove
    bases_remove = bases_sub.add_parser(
        "remove",
        help    = "Remove base images from this machine",
        summary = "Remove base images pulled onto this machine.",
        example = "hg bases remove web-nginx",
    )
    bases_remove.add_argument("name", nargs="+", completer=base_names(), action=Value,
                              help="Base to remove. Repeat to remove several")
    bases_remove.set_defaults(func=_cmd_bases_remove)

    ## hg bases clean
    bases_clean = bases_sub.add_parser(
        "clean",
        help    = "Remove every base image from this machine",
        summary = "Remove every base image pulled onto this machine.",
        example = "hg bases clean",
    )
    bases_clean.set_defaults(func=_cmd_bases_clean)

    ## hg builders
    builders = sub.add_parser(
        "builders",
        help    = "List, pull, or remove builder images",
        summary = "List, pull, or remove the builder images this toolkit knows.",
        example = "hg builders pull",
        section = "Images",
    )
    builders_sub = builders.add_subparsers(dest="builders_command", required=True)

    ## hg builders list
    builders_list = builders_sub.add_parser(
        "list",
        help    = "List the builder images this toolkit knows",
        summary = "List the builder images this toolkit knows.",
        example = "hg builders list",
    )
    builders_list.set_defaults(func=_cmd_builders)

    ## hg builders pull
    builders_pull = builders_sub.add_parser(
        "pull",
        help    = "Pull builder images and retag them locally",
        summary = "Pull builder images from Docker Hub and retag them locally.",
        example = "hg builders pull cpp",
    )
    builders_pull.add_argument("name", nargs="*", completer=builder_names(), action=Value,
                               help="Builder to pull. Repeat to pull several. Defaults to every builder")
    builders_pull.set_defaults(func=_cmd_builders_pull)

    ## hg builders remove
    builders_remove = builders_sub.add_parser(
        "remove",
        help    = "Remove builder images from this machine",
        summary = "Remove builder images pulled onto this machine.",
        example = "hg builders remove cpp",
    )
    builders_remove.add_argument("name", nargs="+", completer=builder_names(), action=Value,
                                 help="Builder to remove. Repeat to remove several")
    builders_remove.set_defaults(func=_cmd_builders_remove)

    ## hg builders clean
    builders_clean = builders_sub.add_parser(
        "clean",
        help    = "Remove every builder image from this machine",
        summary = "Remove every builder image pulled onto this machine.",
        example = "hg builders clean",
    )
    builders_clean.set_defaults(func=_cmd_builders_clean)

    ## hg images
    images = sub.add_parser(
        "images",
        help    = "List or push challenge images",
        summary = "List or push challenge images to the author's public registry.",
        example = "hg images push --namespace xathrya",
        section = "Images",
    )
    images_sub = images.add_subparsers(dest="images_command", required=True)

    ## hg images list
    images_list = images_sub.add_parser(
        "list",
        help    = "List built challenge images",
        summary = "List each built image, its local tag, and the published reference.",
        example = "hg images list",
    )
    _flag(images_list, "--path", completer=directories, help=_CHALLENGE_PATH)
    images_list.set_defaults(func=_cmd_images_list)

    ## hg images push
    images_push = images_sub.add_parser(
        "push",
        help    = "Push built challenge images to the public registry",
        summary = "Push built images and record their registry digests.",
        example = "hg images push --namespace xathrya",
    )
    _flag(images_push, "--path", completer=directories, help=_CHALLENGE_PATH)
    images_push.add_argument(
        "--namespace",
        help = "Docker Hub user or org. Defaults to HG_REGISTRY",
    )
    images_push.set_defaults(func=_cmd_images_push)

    ## hg challenge
    challenge = sub.add_parser(
        "challenge",
        help    = "Scaffold, check, run, and publish a challenge",
        summary = "Scaffold, check, run, and publish a challenge.",
        example = "hg challenge scaffold --base web-nginx --id web-xss-01 --dest ../web-xss-01",
        section = "Challenges",
    )
    challenge_sub = challenge.add_subparsers(dest="challenge_command", required=True)

    ## hg challenge scaffold
    scaffold = challenge_sub.add_parser(
        "scaffold",
        help    = "Write a new challenge tree",
        summary = "Write a new challenge tree. The default has no instance.",
        example = "hg challenge scaffold --base web-nginx --id web-xss-01 --dest ../web-xss-01",
        section = "Author",
    )
    _flag(scaffold, "--base", default="none", completer=scaffold_bases(),
          help = "Base with a template, such as web-nginx or range/debian. Defaults to none")
    scaffold.add_argument("--id",     required=True,    help="Challenge id, stored as metadata.id")
    _flag(scaffold, "--category", completer=sorted(CATEGORIES),
          help = "Platform category. Required when --base is none")
    _flag(scaffold, "--dest", required=True, completer=directories, help="Directory to create")
    scaffold.add_argument("--title",                    help="Challenge title. Defaults to the id")
    scaffold.add_argument("--author", default="author", help="Author name. Defaults to author")
    scaffold.set_defaults(func=_cmd_scaffold)

    ## hg challenge lint
    lint = challenge_sub.add_parser(
        "lint",
        help    = "Check a local challenge tree",
        summary = "Check a local challenge tree.",
        example = "hg challenge lint --path ../web-xss-01",
        section = "Author",
    )
    _flag(lint, "--path", completer=directories, help=_CHALLENGE_PATH)
    lint.add_argument("--strict",
        action  = "store_true",
        help    = "Also require base digests, a real writeup and solver, and git checks",
    )
    lint.set_defaults(func=_cmd_lint)

    ## hg challenge digest
    digest = challenge_sub.add_parser(
        "digest",
        help    = "Print the canonical manifest digest",
        summary = "Print the canonical manifest digest.",
        example = "hg challenge digest --path ../web-xss-01",
        section = "Author",
    )
    _flag(digest, "--path", completer=directories, help=_CHALLENGE_PATH)
    digest.set_defaults(func=_cmd_digest)

    ## hg challenge build
    build = challenge_sub.add_parser(
        "build",
        help    = "Build the challenge images",
        summary = "Build the challenge images with docker buildx and load them.",
        example = "hg challenge build --path ../web-xss-01",
        section = "Run",
    )
    _flag(build, "--path", completer=directories, help=_CHALLENGE_PATH)
    build.set_defaults(func=_cmd_build)

    ## hg challenge run
    run = challenge_sub.add_parser(
        "run",
        help    = "Start the challenge locally",
        summary = "Start the challenge and inject a random flag into each dynamic slot.",
        example = "hg challenge run --path ../web-xss-01",
        section = "Run",
    )
    _flag(run, "--path", completer=directories, help=_CHALLENGE_PATH)
    run.add_argument("--timeout",
        type    = float,
        default = 60,
        help    = "Seconds to wait until the player entry is healthy. Defaults to 60",
    )
    run.set_defaults(func=_cmd_run)

    ## hg challenge status
    status = challenge_sub.add_parser(
        "status",
        help    = "Show whether the local challenge is running",
        summary = "Show whether the challenge containers are running.",
        example = "hg challenge status --path ../web-xss-01",
        section = "Run",
    )
    _flag(status, "--path", completer=directories, help=_CHALLENGE_PATH)
    status.set_defaults(func=_cmd_status)

    ## hg challenge verify
    verify = challenge_sub.add_parser(
        "verify",
        help    = "Lint, build, run, and check the solver",
        summary = "Run strict lint, build, run, and confirm the solver prints the flag.",
        example = "hg challenge verify --path ../web-xss-01",
        section = "Run",
    )
    _flag(verify, "--path", completer=directories, help=_CHALLENGE_PATH)
    verify.add_argument("--timeout",
        type    = float,
        default = 60,
        help    = "Seconds to wait for health before running the solver. Defaults to 60",
    )
    verify.set_defaults(func=_cmd_verify)

    ## hg challenge solve
    solve = challenge_sub.add_parser(
        "solve",
        help    = "Run the solver against a running challenge",
        summary = "Run the solver against the challenge started by run.",
        example = "hg challenge solve --path ../web-xss-01",
        section = "Run",
    )
    _flag(solve, "--path", completer=directories, help=_CHALLENGE_PATH)
    _flag(solve, "--script", completer=challenge_files,
        help = "Solver to run, relative to the challenge root. Defaults to spec.scripts.test",
    )
    solve.add_argument("--host", help="Override CHALLENGE_HOST. Defaults to the host recorded by run")
    solve.add_argument("--port",
        type = int,
        help = "Override CHALLENGE_PORT. Defaults to the published host port",
    )
    solve.set_defaults(func=_cmd_solve)

    ## hg challenge stop
    stop = challenge_sub.add_parser(
        "stop",
        help    = "Stop the running challenge",
        summary = "Stop the challenge started by run and keep the images.",
        example = "hg challenge stop --path ../web-xss-01",
        section = "Run",
    )
    _flag(stop, "--path", completer=directories, help=_CHALLENGE_PATH)
    stop.set_defaults(func=_cmd_stop)

    ## hg challenge clean
    clean = challenge_sub.add_parser(
        "clean",
        help    = "Stop the challenge and remove its image",
        summary = "Stop the challenge and remove its local image.",
        example = "hg challenge clean --path ../web-xss-01",
        section = "Run",
    )
    _flag(clean, "--path", completer=directories, help=_CHALLENGE_PATH)
    clean.set_defaults(func=_cmd_clean)

    ## hg challenge push
    push = challenge_sub.add_parser(
        "push",
        help    = "Create or update the platform challenge",
        summary = "Create or update the challenge on the platform.",
        example = "hg challenge push --path ../web-xss-01 --publish",
        section = "Publish",
    )
    _flag(push, "--path", completer=directories, help=_CHALLENGE_PATH)
    push.add_argument("--image", help="Registry reference the orchestrator can pull")
    push.add_argument("--difficulty", type=int,
        help = "Platform difficulty from 1 to 10. Defaults to metadata.difficulty (easy is 3, medium is 6, hard is 9)",
    )
    push.add_argument("--publish", action="store_true",
        help = "Set the status to published. Defaults to leaving the current status, or draft on create",
    )
    _flag(push, "--file", completer=files, help="Handout to upload instead of spec.files from dist/")
    push.set_defaults(func=_cmd_push)

    ## hg challenge material
    material = challenge_sub.add_parser(
        "material",
        help    = "Upload the player handout",
        summary = "Upload the player handout.",
        example = "hg challenge material --path ../web-xss-01",
        section = "Publish",
    )
    _flag(material, "--path", completer=directories, help=_CHALLENGE_PATH)
    _flag(material, "--file", completer=files, help="File to upload. Defaults to spec.files from dist/")
    material.set_defaults(func=_cmd_material)

    ## hg completion
    completion = sub.add_parser(
        "completion",
        help    = "Print a shell completion script",
        summary = "Print the shell completion script for bash, zsh, or fish.",
        example = 'eval "$(hg completion bash)"',
        section = "Shell",
    )
    completion.add_argument(
        "shell",
        choices = list(SHELLS),
        help    = "bash, zsh, or fish",
    )
    completion.set_defaults(func=_cmd_completion)

    return parser

def main(argv: list[str] | None = None) -> None:
    # The private completion command is not in the tree, so it is handled
    # before the parser sees the line.
    words = list(sys.argv[1:] if argv is None else argv)
    if words[:1] == [COMPLETE_COMMAND]:
        _cmd_complete(words[1:])
        return

    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except ToolError as exc:
        style.fail(exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
