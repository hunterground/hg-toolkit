"""
Help and usage layout for the `hg` command line.

Argparse's default help dumps every subcommand into the usage line and wraps
flag descriptions on hyphens. This module renders a short usage line, groups
commands into sections, and keeps option text in aligned columns.
"""

from collections.abc    import Sequence
import argparse
import re
import shutil
import textwrap


# How wide a label may grow before its description drops to the next line.
_LABEL_CAP = 36
# Sections print in this order. A section that is not listed follows them,
# in the order its first command was registered.
_SECTION_ORDER = (
    "Account",
    "Images",
    "Challenges",
    "Contests",
    "Shell",
    "Author",
    "Run",
    "Publish",
    "Platform",
    "Commands",
)
# Dest names whose metavar should read as a word, not the raw dest.
_METAVARS = {
    "path":          "dir",
    "dest":          "dir",
    "file":          "file",
    "script":        "path",
    "timeout":       "seconds",
    "email":         "email",
    "url":           "url",
    "slug":          "slug",
    "title":         "title",
    "starts_at":     "time",
    "ends_at":       "time",
    "description":   "text",
    "rules":         "text",
    "contest_type":  "type",
    "participation": "mode",
    "challenge":     "slug",
    "points":        "points",
    "sort_order":    "order",
    "base":          "name",
    "author":        "name",
    "image":         "ref",
    "difficulty":    "level",
    "host":          "host",
    "port":          "port",
    "shell":         "shell",
    "name":          "name",
    "id":            "id",
}

_REQUIRED_RE = re.compile(r"the following arguments are required: (.+)$")
_CHOICE_RE   = re.compile(r"argument (\S+): invalid choice: '([^']*)'")
_UNKNOWN_RE  = re.compile(r"unrecognized arguments: (.+)$")


## Helper Functions -------------------------------------------------------------------------------
def _page(parser: argparse.ArgumentParser, width: int) -> list[str]:
    lines: list[str] = []
    summary = getattr(parser, "summary", "")
    if summary:
        lines += _wrap_block(summary, width) + [""]
    lines += _usage(parser, width)

    sections = _command_sections(parser)
    if sections:
        label_width = max(len(name) for _, rows in sections for name, _ in rows)
        for title, rows in sections:
            lines += ["", f"{title}:"]
            lines += _column([(name, help_text) for name, help_text in rows], width, label_width)

    arguments = _argument_rows(parser)
    if arguments:
        lines += ["", "Arguments:"]
        lines += _column(arguments, width)

    required, optional = _option_rows(parser)
    shared = _shared_width(required + optional)
    if required:
        lines += ["", "Required:"]
        lines += _column(required, width, shared)
    if optional:
        lines += ["", "Options:"]
        lines += _column(optional, width, shared)

    example = getattr(parser, "example", "")
    if example:
        lines += ["", *_examples(example, width)]
    return lines


def _usage(parser: argparse.ArgumentParser, width: int) -> list[str]:
    parts  = [parser.prog]
    parts += [_positional_token(action) for action in _positionals(parser)]
    if _subparsers(parser) is not None:
        parts.append("<command>")
    if _options(parser):
        parts.append("[options]")
    text = " ".join(parts)
    wrapped = textwrap.wrap(
        text,
        width = max(20, width - 2),
        subsequent_indent = "  ",
        break_long_words = False,
        break_on_hyphens = False,
    ) or [text]
    return ["Usage:", *(f"  {line}" for line in wrapped)]


def _examples(example: str, width: int) -> list[str]:
    lines = ["Examples:"]
    room  = max(20, width - 2)
    for raw in example.splitlines():
        wrapped = textwrap.wrap(
            raw,
            width = room,
            subsequent_indent = "    ",
            break_long_words  = False,
            break_on_hyphens  = False,
        ) or [""]
        lines += [f"  {line}" for line in wrapped]
    return lines


def _wrap_block(text: str, width: int) -> list[str]:
    wrapped: list[str] = []
    for raw in text.splitlines():
        wrapped += textwrap.wrap(
            raw,
            width = width,
            break_long_words = False,
            break_on_hyphens = False,
        ) or [""]
    return wrapped


def _column(
    rows:           Sequence[tuple[str, str]],
    width:          int,
    label_width:    int | None = None,
) -> list[str]:
    if not rows:
        return []

    measured = max(len(label) for label, _ in rows)
    if label_width is None:
        label_width = min(measured, _LABEL_CAP)
    else:
        label_width = min(max(label_width, measured), _LABEL_CAP)

    gutter = 2
    indent = "  "
    help_at = len(indent) + label_width + gutter
    help_width = max(24, width - help_at)
    lines: list[str] = []
    for label, help_text in rows:
        wrapped = textwrap.wrap(
            help_text,
            width = help_width,
            break_long_words = False,
            break_on_hyphens = False,
        ) if help_text else []

        if len(label) > label_width:
            lines.append(indent + label)
            lines += [(" " * help_at) + item for item in wrapped]
            continue

        if not wrapped:
            lines.append(indent + label)
            continue
        lines.append(indent + label.ljust(label_width) + (" " * gutter) + wrapped[0])
        lines += [(" " * help_at) + item for item in wrapped[1:]]
    return lines


def _shared_width(rows: Sequence[tuple[str, str]]) -> int:
    if not rows:
        return 0
    return min(max(len(label) for label, _ in rows), _LABEL_CAP)


def _command_sections(parser: argparse.ArgumentParser) -> list[tuple[str, list[tuple[str, str]]]]:
    action = _subparsers(parser)
    if action is None:
        return []

    buckets: dict[str, list[tuple[str, str]]] = {}
    seen: list[str] = []
    for choice in action._choices_actions:
        child = action.choices[choice.dest]
        title = getattr(child, "section", "") or "Commands"
        if title not in buckets:
            buckets[title] = []
            seen.append(title)
        buckets[title].append((choice.dest, _help_text(choice.help)))

    order  = [title for title in _SECTION_ORDER if title in buckets]
    order += [title for title in seen if title not in order]
    return [(title, buckets[title]) for title in order]


def _argument_rows(parser: argparse.ArgumentParser) -> list[tuple[str, str]]:
    return [(_positional_token(action), _help_text(action.help)) for action in _positionals(parser)]


def _option_rows(parser: argparse.ArgumentParser) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    options  = _options(parser)
    pad_long = any(_shorts(action) for action in options)
    required: list[tuple[str, str]] = []
    optional: list[tuple[str, str]] = []
    for action in options:
        row = (_option_label(action, pad_long=pad_long), _help_text(action.help))
        if getattr(action, "required", False):
            required.append(row)
        else:
            optional.append(row)
    return required, optional


def _option_label(action: argparse.Action, *, pad_long: bool) -> str:
    shorts = _shorts(action)
    longs = [option for option in action.option_strings if option not in shorts]
    if shorts and longs:
        text = f"{', '.join(shorts)}, {', '.join(longs)}"
    elif shorts:
        text = ", ".join(shorts)
    else:
        text = ", ".join(longs)
        if pad_long:
            text = f"    {text}"

    meta = _option_metavar(action)
    if meta:
        text = f"{text} {meta}"
    return text


def _option_metavar(action: argparse.Action) -> str:
    if action.nargs == 0:
        return ""

    token = _token(action)
    if action.nargs in ("*", "+"):
        return f"{token}..."
    if action.nargs == "?":
        return f"[{token}]"
    if isinstance(action.nargs, int) and action.nargs > 1:
        return " ".join([token] * action.nargs)
    return token


def _positional_token(action: argparse.Action) -> str:
    token = _token(action)
    if action.nargs == "*":
        return f"[{token}...]"
    if action.nargs == "+":
        return f"{token}..."
    if action.nargs == "?":
        return f"[{token}]"
    if action.nargs == argparse.REMAINDER:
        return "[--]"
    return token


def _token(action: argparse.Action) -> str:
    meta = action.metavar
    if meta is None:
        meta = _METAVARS.get(action.dest, action.dest.replace("_", "-"))
    if isinstance(meta, tuple):
        return " ".join(f"<{item}>" for item in meta)
    return f"<{meta}>"


def _help_text(help_text: object) -> str:
    if help_text in (None, argparse.SUPPRESS):
        return ""
    text = str(help_text)
    if text == "show this help message and exit":
        return "Show this help message"
    return text


def _positionals(parser: argparse.ArgumentParser) -> list[argparse.Action]:
    found: list[argparse.Action] = []
    for action in parser._actions:
        if action.option_strings or isinstance(action, argparse._SubParsersAction):
            continue
        if action.help == argparse.SUPPRESS:
            continue
        found.append(action)
    return found


def _options(parser: argparse.ArgumentParser) -> list[argparse.Action]:
    return [
        action for action in parser._actions
        if action.option_strings and action.help != argparse.SUPPRESS
    ]


def _shorts(action: argparse.Action) -> list[str]:
    return [option for option in action.option_strings if len(option) == 2]


def _subparsers(parser: argparse.ArgumentParser) -> argparse.Action | None:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None


## Main Functions ---------------------------------------------------------------------------------
def render(parser: argparse.ArgumentParser, *, width: int = 80) -> str:
    """The full help page, ending with a newline."""
    return "\n".join(_page(parser, width)) + "\n"


def error_body(parser: argparse.ArgumentParser, *, width: int = 80) -> str:
    """The usage, summary, and example printed under a usage error."""
    parts: list[str] = ["", *_usage(parser, width)]
    summary = getattr(parser, "summary", "")
    if summary:
        parts += ["", summary]

    example = getattr(parser, "example", "")
    if example:
        parts += ["", *_examples(example, width)]

    parts += ["", f"See '{parser.prog} --help' for the full list."]
    return "\n".join(parts) + "\n"


def humanize(message: str) -> str:
    """Turn an argparse error into a sentence a person can act on."""
    choice = _CHOICE_RE.search(message)
    if choice and "choose from" in message:
        name, value = choice.group(1), choice.group(2)
        if name == "command" or name.endswith("_command"):
            return f"unknown command '{value}'"

        listed = re.search(r"\(choose from ([^)]*)\)", message)
        if listed:
            options = listed.group(1).replace("'", "")
            return f"invalid choice '{value}' (choose from {options})"

    required = _REQUIRED_RE.search(message)
    if required:
        names = [part.strip() for part in required.group(1).split(",")]
        if names and all(name == "command" or name.endswith("_command") for name in names):
            return "a command is required"

        if names and all(name.startswith("-") for name in names):
            listed = ", ".join(names)
            if len(names) == 1:
                return f"missing required option {listed}"
            return f"missing required options: {listed}"

        listed = ", ".join(names)
        if len(names) == 1:
            return f"missing required argument {listed}"
        return f"missing required arguments: {listed}"

    unknown = _UNKNOWN_RE.search(message)
    if unknown:
        args = unknown.group(1).split()
        if args and all(arg.startswith("-") for arg in args):
            if len(args) == 1:
                return f"unknown option {args[0]}"
            return "unknown options " + " ".join(args)
    return message


def terminal_width(file: object) -> int:
    """The width to wrap for, clamped so a very wide terminal stays readable."""
    isatty = getattr(file, "isatty", None)
    if not callable(isatty) or not isatty():
        return 80
    columns = shutil.get_terminal_size(fallback=(80, 24)).columns
    return min(100, max(72, columns))
