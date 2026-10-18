"""
Colored messages for the `hg` command line.

Every message the toolkit prints goes through this module, so an important
field (the player connection, a generated flag, a password, a digest) stands
out on a terminal and the same bytes reach a pipe unchanged.

Color is automatic. 
Rich turns it off when the stream is not a terminal, when `NO_COLOR` is set, 
when `FORCE_COLOR` is `0`, and when `TERM` is `dumb`, 
so the helpers carry no policy of their own:

* `say` and `ok` write to stdout.
* `warn` and `fail` write to stderr.
* Values are passed as rich `Text` segments, never as markup, because a flag
  content or a path is arbitrary. `markup` and `highlight` stay off for the
  same reason: rich would otherwise recolor `/app/flag.txt` and bare numbers.
"""

from rich.console   import Console
from rich.text      import Text

## Palette ------------------------------------------------------------------------------------------------
OK    = "bold green"
WARN  = "yellow"
ERROR = "bold red"
LABEL = "cyan"
VALUE = "bold"
DIM   = "dim"

# Streams are resolved per call so the helpers keep working under a redirect
# and under pytest's `capsys`, which replaces `sys.stdout` at test time.
def _stdout_console() -> Console:
    return Console(
        file         = None,
        no_color     = None,
        markup       = False,
        highlight    = False,
        soft_wrap    = True,
        force_terminal = None,
    )

def _stderr_console() -> Console:
    return Console(
        file         = None,
        stderr       = True,
        no_color     = None,
        markup       = False,
        highlight    = False,
        soft_wrap    = True,
        force_terminal = None,
    )

def _coerce(part: object) -> Text:
    if isinstance(part, Text):
        return part
    return Text(str(part))

def say(*parts: object) -> None:
    """Print a line to stdout."""
    line = Text()
    for part in parts:
        line.append_text(_coerce(part))
    _stdout_console().print(line)

def ok(*parts: object) -> None:
    """Print a success line to stdout, marked with an `ok` prefix."""
    say(Text("ok ", style=OK), *parts)

def warn(message: object) -> None:
    """Print a warning line to stderr, prefixed with `warning:`."""
    _stderr_console().print(Text("warning: ", style=WARN) + Text(str(message)))

def fail(message: object) -> None:
    """Print an error to stderr. Continuation lines line up under the text."""
    lines = str(message).splitlines() or [""]
    console = _stderr_console()
    for index, line in enumerate(lines):
        prefix = "error: " if index == 0 else "       "
        console.print(Text(prefix, style=ERROR) + Text(line))

def dim_err(message: object) -> None:
    """Print a secondary detail line to stderr."""
    _stderr_console().print(Text(str(message), style=DIM))

def value(text: object) -> Text:
    """Mark a field that deserves attention: a connection, a flag, a password."""
    return Text(str(text), style=VALUE)

def success(text: object) -> Text:
    """Build a success-styled cell for `row` without printing it."""
    return Text(str(text), style=OK)

def label(text: object) -> Text:
    """Mark a field name, such as `worker` or `sha256`."""
    return Text(str(text), style=LABEL)

def dim(text: object) -> Text:
    """Mark secondary detail: a container name, an image, a path."""
    return Text(str(text), style=DIM)

def row(*cells: object, sep: str = " ") -> None:
    """Print a row of cells separated by `sep`, each cell already styled."""
    line = Text()
    for index, cell in enumerate(cells):
        if index:
            line.append(sep)
        line.append_text(_coerce(cell))
    _stdout_console().print(line)

def table(rows: list[tuple[object, ...]], *, header: tuple[str, ...] | None = None) -> None:
    """
    Print aligned columns.

    The last column is not padded. A header, when given, uses the label color
    and shares its column starts with the rows under it.
    """
    rendered: list[list[Text]] = []
    if header:
        rendered.append([Text(cell, style=LABEL) for cell in header])

    rendered += [[_coerce(cell) for cell in row] for row in rows]
    if not rendered:
        return

    columns = max(len(row) for row in rendered)
    while columns > 1 and all(len(row) < columns or not row[columns - 1].plain for row in rendered):
        columns -= 1
    widths = [0] * columns

    for row in rendered:
        for index, cell in enumerate(row[:columns]):
            widths[index] = max(widths[index], len(cell.plain))

    console = _stdout_console()
    for row in rendered:
        line = Text()
        visible = min(columns, len(row))
        for index in range(visible):
            if index:
                line.append("  ")
            line.append_text(row[index])
            if index < columns - 1:
                line.append(" " * (widths[index] - len(row[index].plain)))
        console.print(line)
