"""
Shell completion for the `hg` command line.

`hg completion <shell>` prints a script, and that script asks
`hg __complete` for the words that may follow a partial line. The engine
walks the parser tree the CLI already builds, so a new subcommand or flag is
completable the moment it exists: there is no second list to keep in sync. A
value that needs more than the tree offers (a base name, a contest type, a
path on disk) carries a `completer` on its action, set where the flag is
declared.

The protocol is the one the generated scripts speak:

    hg __complete --current=<the word under the cursor> -- <the words before it>

`--current` is empty at the start of a word. Everything after `--` is the
rest of the line, passed through verbatim, so a flag being completed never
becomes an option of `__complete` itself. The program name is dropped when
it is the first word, so a caller may pass the words on their own. One
candidate is printed per line, and a candidate is the whole text that replaces
the word under the cursor.

The engine reads the tree through `_actions`, `_option_string_actions`,
and `_SubParsersAction`: the same de facto stable internals every completion
bridge uses, pinned here by the tests.

What the shell adds on top is only quoting. The engine prints the whole text
that replaces the word under the cursor, so a directory keeps its trailing
slash and the next tab descends into it, and a path with a space is offered as
one candidate: bash quotes it through `complete -o filenames`, zsh through
`compadd`, and fish on its own. The fish script passes `-f` so that fish does
not also offer its own files on top of the engine's answer.
"""

from collections.abc    import Callable, Iterable, Sequence
from dataclasses        import dataclass
from pathlib            import Path
from hg.errors          import ToolError
import argparse
import os

## Constants ---------------------------------------------------------------------------------------
# The private command the generated scripts call. It is handled before the
# parser sees the line, so it never becomes a command of the tree: a user never
# types it, and it stays out of the command list and out of the usage line.
COMMAND = "__complete"
SHELLS  = ("bash", "zsh", "fish")

# Directories that hold no solver and no handout. A `--script` walk skips them.
SKIP = frozenset({".git", ".hg", ".venv", "__pycache__", "dist", "node_modules"})

# The trees `--path` accepts, next to a directory.
MANIFESTS = ("challenge.yaml", "contest.yaml")

# `--script` is a path into the challenge tree, so the walk lists the root and
# two levels below it (`private/solve.sh`, `private/poc/probe.py`) and stops.
SCRIPT_DEPTH = 2

## The question ------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Ask:
    """
    The word a completer is asked about.

    `word` is the value being completed, with any `--flag=` in front of it
    already removed, so `--path ../web` and `--path=../web` ask the same
    question. `tokens` is the line up to the cursor, program name dropped,
    for a completer that has to read a sibling flag.
    """

    word:             str
    tokens:     tuple[str, ...]

    @property
    def head(self) -> str:
        """
        The directory part of `word`, ending with a slash, or empty.

        A word that is a directory of its own right (`.` or `..`) is
        treated as the head, so the entries under it keep the prefix the user
        typed and the shell replaces the whole word.
        """
        if self.word.endswith("/"):
            return self.word
        if self.word in (".", ".."):
            return f"{self.word}/"
        head, found, _leaf = self.word.rpartition("/")
        return f"{head}/" if found else ""

    @property
    def base(self) -> Path:
        """The directory `word` names an entry of."""
        return Path(self.head or ".").expanduser()

    def option(self, name: str) -> str | None:
        """The last value the line gives `name`, for a completer that reads a sibling flag."""
        answer: str | None = None
        prefix = f"{name}="
        for index, token in enumerate(self.tokens):
            if token == name and index + 1 < len(self.tokens):
                answer = self.tokens[index + 1]
            elif token.startswith(prefix):
                answer = token[len(prefix):]
        return answer

# A completer is a fixed list of candidates, or a callable that reads the line.
Completer = Sequence[str] | Callable[[Ask], Iterable[str]]

## Completers --------------------------------------------------------------------------------------
def _unique(values: Iterable[str]) -> list[str]:
    seen:  set[str]  = set()
    found: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            found.append(value)
    return found

def _entries(ask: Ask, *, files: bool, extra: Sequence[str] = ()) -> list[str]:
    """
    The directory entries under `ask.head`.

    A directory keeps a trailing slash so the shell can descend into it without
    adding a space. A hidden entry is offered only once the word itself starts
    with a dot.
    """
    try:
        entries = sorted(ask.base.iterdir())
    except OSError:
        return []

    found: list[str] = []
    for entry in entries:
        if entry.name.startswith(".") and not ask.word.startswith("."):
            continue
        if entry.is_dir():
            found.append(f"{ask.head}{entry.name}/")
        elif files:
            found.append(f"{ask.head}{entry.name}")
    for name in extra:
        if (ask.base / name).is_file():
            found.append(f"{ask.head}{name}")
    return _unique(found)

def directories(ask: Ask) -> list[str]:
    """
    The directories under the word, plus a manifest when one is there.

    `--path` takes a challenge or a contest directory, or the manifest file
    of one. Every directory is offered, not only a tree root, because the shell
    has to be able to walk down to the root.
    """
    return _entries(ask, files=False, extra=MANIFESTS)

def files(ask: Ask) -> list[str]:
    """The files and directories under the word, for a flag that takes any path."""
    return _entries(ask, files=True)

def _challenge_root(ask: Ask) -> Path | None:
    """The challenge root the line names, or `None` when it names none."""
    given = ask.option("--path")
    if given is None:
        here = Path.cwd()
        return here if (here / "challenge.yaml").is_file() else None

    start = Path(given).expanduser()
    if start.is_file() and start.name == "challenge.yaml":
        start = start.parent
    return start if (start / "challenge.yaml").is_file() else None

def challenge_files(ask: Ask) -> list[str]:
    """
    The files of a challenge tree, relative to its root.

    `spec.scripts.test` and `--script` are resolved against the challenge
    root and not the shell's directory, and the root may already be on the line
    as `--path`. Outside a challenge tree there is nothing to be relative to,
    so the shell's own path completion answers instead.
    """
    root = _challenge_root(ask)
    if root is None:
        return files(ask)

    found: list[str] = []
    for here, dirs, names in os.walk(root):
        current = Path(here)
        if len(current.relative_to(root).parts) >= SCRIPT_DEPTH:
            dirs[:] = []
        else:
            dirs[:] = sorted(name for name in dirs if name not in SKIP and not name.startswith("."))
        for name in sorted(names):
            found.append(str((current / name).relative_to(root)))
    return found

## The engine --------------------------------------------------------------------------------------
def _subparsers(parser: argparse.ArgumentParser) -> argparse.Action | None:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None

def _commands(parser: argparse.ArgumentParser) -> list[str]:
    """
    The commands of a group, in the order the parser added them.

    A subcommand added without help is private, and argparse already leaves it
    out of `_choices_actions`, which is the same list the help prints.
    """
    action = _subparsers(parser)
    if action is None:
        return []
    return [entry.dest for entry in action._choices_actions]

def _child(parser: argparse.ArgumentParser, token: str) -> argparse.ArgumentParser | None:
    action = _subparsers(parser)
    if action is None:
        return None
    return action.choices.get(token)

def _option(parser: argparse.ArgumentParser, token: str) -> argparse.Action | None:
    return parser._option_string_actions.get(token)

def _takes_value(action: argparse.Action) -> bool:
    """Whether the action takes the next word as a value."""
    return action.nargs not in (0, "?", argparse.REMAINDER)

def _flags(parser: argparse.ArgumentParser) -> list[str]:
    """Every option string the parser accepts, `-h` and `--help` included."""
    found: list[str] = []
    for action in parser._actions:
        if action.help == argparse.SUPPRESS:
            continue
        for option in action.option_strings:
            if option not in found:
                found.append(option)
    return found

def _positional(parser: argparse.ArgumentParser) -> argparse.Action | None:
    """The positional of a command that says what may go there."""
    for action in parser._actions:
        if not action.option_strings and getattr(action, "completer", None) is not None:
            return action
    return None

def _program(parser: argparse.ArgumentParser, tokens: Sequence[str]) -> tuple[str, ...]:
    """
    Drop the program name the shell puts first.

    A caller that passes the words on their own keeps them: a token that is not
    the program is a word of the line.
    """
    if tokens and Path(tokens[0]).name == parser.prog:
        return tuple(tokens[1:])
    return tuple(tokens)

def _walk(
    parser: argparse.ArgumentParser,
    words:  Sequence[str],
) -> tuple[argparse.ArgumentParser | None, argparse.Action | None]:
    """
    Follow the line down the parser tree.

    Returns the parser the line has reached and, when the last word is a flag
    that takes a value, the action waiting for that value. `None` for the
    parser means the line names a command the tree does not have, so nothing
    more on it can be resolved.
    """
    context: argparse.ArgumentParser | None = parser
    pending: argparse.Action | None = None
    for word in words:
        if context is None:
            break
        if pending is not None:
            pending = None
        elif word.startswith("-") and word != "-":
            if "=" not in word:
                action = _option(context, word)
                if action is not None and _takes_value(action):
                    pending = action
        elif _subparsers(context) is not None:
            # A group takes a command and nothing else, so a word that is not
            # one of its commands is a typo, and the line has no meaning after
            # it. A leaf takes values instead, and those are simply passed over.
            context = _child(context, word)
        else:
            pending = None
    return context, pending

def _read(
    action: argparse.Action | None,
    word:   str,
    tokens: tuple[str, ...],
) -> list[str]:
    """The candidates an action offers for `word`, filtered by what is typed."""
    completer = getattr(action, "completer", None)
    if completer is None:
        return []

    ask     = Ask(word=word, tokens=tokens)
    offered = completer(ask) if callable(completer) else completer
    return _unique([candidate for candidate in offered if candidate.startswith(word)])

class Value(argparse.Action):
    """
    A `store` action that also says what the shell should offer for its value.

    `completer` is a candidate list, or a callable that takes the
    :class:`Ask` and returns a candidate list.
    """

    def __init__(
        self,
        option_strings: Sequence[str],
        dest:           str,
        *,
        completer:      Completer | None = None,
        **kwargs:       object,
    ) -> None:
        self.completer = completer
        super().__init__(option_strings, dest, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None) -> None:  # type: ignore[no-untyped-def]
        setattr(namespace, self.dest, values)

def candidates(
    parser:  argparse.ArgumentParser,
    tokens:  Sequence[str],
    current: str,
) -> list[str]:
    """
    The words that may follow `current`.

    `tokens` is the line up to the cursor, the program name first, exactly as
    the shell has it. `current` is the word under the cursor, empty at the
    start of a word.
    """
    words            = _program(parser, tokens)
    context, pending = _walk(parser, words)
    if context is None:
        return []

    # `--flag=value` completes the value, and the flag stays in front of it.
    if current.startswith("-") and "=" in current:
        flag, _sep, word = current.partition("=")
        action = _option(context, flag)
        if action is None:
            return []
        return [f"{flag}={value}" for value in _read(action, word, words)]

    if pending is not None:
        return _read(pending, current, words)

    if current.startswith("-"):
        return [flag for flag in _flags(context) if flag.startswith(current)]

    commands = _commands(context)
    if commands:
        return [name for name in commands if name.startswith(current)]
    return _read(_positional(context), current, words)

def answer(parser: argparse.ArgumentParser, tokens: Sequence[str], current: str) -> str:
    """The newline-terminated answer the generated scripts read."""
    return "".join(f"{word}\n" for word in candidates(parser, tokens, current))

## The scripts -------------------------------------------------------------------------------------
_BASH = """# hg completion for bash.
#
#   eval "$(hg completion bash)"
#
# The engine prints one candidate per line. -o filenames lets readline quote a
# path, and keep the cursor after a directory instead of adding a space.
_hg_complete() {
    local candidate
    COMPREPLY=()
    while IFS= read -r candidate; do
        COMPREPLY+=("$candidate")
    done < <(hg __complete --current="${COMP_WORDS[COMP_CWORD]}" -- "${COMP_WORDS[@]:0:COMP_CWORD}" 2>/dev/null)
    return 0
}

# -o nosort keeps the order the engine printed, and needs bash 4.4.
if (( BASH_VERSINFO[0] > 4 || (BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] >= 4) )); then
    complete -o filenames -o nosort -F _hg_complete hg
else
    complete -o filenames -F _hg_complete hg
fi
"""

_ZSH = """#compdef hg
# hg completion for zsh.
#
#   eval "$(hg completion zsh)"
#
# A candidate that ends with a slash is a directory: -S '' leaves the cursor
# next to it so the next tab descends, while a value is followed by a space.
_hg() {
    local -a candidates others paths
    local candidate
    candidates=("${(@f)$(hg __complete --current="${words[CURRENT]}" -- "${(@)words[1,CURRENT-1]}" 2>/dev/null)}")
    for candidate in "${candidates[@]}"; do
        [[ -z $candidate ]] && continue
        if [[ $candidate == */ ]]; then
            paths+=("$candidate")
        else
            others+=("$candidate")
        fi
    done
    (( $#others )) && compadd -- "${others[@]}"
    (( $#paths )) && compadd -S '' -- "${paths[@]}"
    return 0
}

# compdef comes from compinit, which the user runs before this script.
if (( $+functions[compdef] )); then
    compdef _hg hg
fi
"""

_FISH = """# hg completion for fish.
#
#   hg completion fish > ~/.config/fish/completions/hg.fish
#
# The engine already answers with the paths a flag takes, so fish adds no files
# of its own. -opc is the line before the word under the cursor, which is what
# the engine expects after `--`; -ct is that word.
function __hg_complete
    set -l tokens (commandline -opc)
    set -l current (commandline -ct)
    hg __complete --current="$current" -- $tokens 2>/dev/null
end

# -f keeps fish from adding its own paths to every word, which would double the
# candidates the engine already prints.
complete -c hg -f -a '(__hg_complete)'
"""

_SCRIPTS: dict[str, str] = {
    "bash": _BASH,
    "zsh":  _ZSH,
    "fish": _FISH,
}

def script(shell: str) -> str:
    """The completion script for `shell`: bash, zsh, or fish."""
    found = _SCRIPTS.get(shell.strip().lower())
    if found is None:
        known = ", ".join(SHELLS)
        raise ToolError(f"unknown shell {shell!r}. Known shells: {known}")
    return found
