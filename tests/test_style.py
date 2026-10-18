"""
    HunterGround Toolkit
    Test for the colored message helpers
"""

from io             import StringIO
from rich.console   import Console
from hg             import style
from hg.cli         import main
import pytest

## Fixtures ----------------------------------------------------------------------------------------
@pytest.fixture
def forced_stdout(monkeypatch):
    """Point the stdout helper at an in-memory console that always colors."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    buffer = StringIO()
    monkeypatch.setattr(
        style,
        "_stdout_console",
        lambda: Console(file=buffer, force_terminal=True, markup=False, highlight=False, soft_wrap=True),
    )
    return buffer

@pytest.fixture
def forced_stderr(monkeypatch):
    """Point the stderr helper at an in-memory console that always colors."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    buffer = StringIO()
    monkeypatch.setattr(
        style,
        "_stderr_console",
        lambda: Console(file=buffer, force_terminal=True, stderr=True, markup=False, highlight=False, soft_wrap=True),
    )
    return buffer

## Tests -------------------------------------------------------------------------------------------
def test_no_color_env_keeps_output_plain(capsys, monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    style.ok("pass")
    out = capsys.readouterr().out
    assert "pass" in out
    assert "\x1b[" not in out

def test_piped_stdout_stays_plain(capsys):
    """capsys captures a non-terminal, which is what a pipe looks like."""
    style.say("conn ", style.value("http://127.0.0.1:80"))
    out = capsys.readouterr().out
    assert out == "conn http://127.0.0.1:80\n"
    assert "\x1b[" not in out

def test_important_value_is_colored_when_forced(forced_stdout):
    style.say("conn ", style.value("http://127.0.0.1:80"))
    out = forced_stdout.getvalue()
    assert "conn " in out
    assert "http://127.0.0.1:80" in out
    assert "\x1b[" in out

def test_warning_goes_to_stderr_and_is_prefixed(capsys, monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    style.warn("no OS keychain")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "warning: no OS keychain\n"

def test_failure_is_colored_when_forced(forced_stderr):
    style.fail("solver did not print the flag")
    err = forced_stderr.getvalue()
    assert "error: solver did not print the flag" in err
    assert "\x1b[" in err

def test_failure_lines_up_continuation(capsys, monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    style.fail("first problem\nsecond problem")
    assert capsys.readouterr().err == "error: first problem\n       second problem\n"

def test_success_line_has_an_ok_prefix(capsys, monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    style.ok("verified")
    assert capsys.readouterr().out == "ok verified\n"

def test_table_aligns_header_and_rows(capsys):
    style.table(
        [("web-xss-01", "250", "hidden")],
        header=("CHALLENGE", "POINTS", "STATE"),
    )
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split() == ["CHALLENGE", "POINTS", "STATE"]
    assert lines[1].startswith("web-xss-01")
    assert lines[1][lines[0].index("POINTS")] == "2"
    assert lines[1][lines[0].index("STATE")] == "h"

def test_value_is_never_parsed_as_markup():
    """A flag content or a path is arbitrary text, so brackets must survive."""
    assert style.value("[bold]hg{x}[/bold]").plain == "[bold]hg{x}[/bold]"
    assert style.value("/app/flag.txt").plain == "/app/flag.txt"

def test_row_joins_cells_with_one_space(capsys):
    style.row(style.label("web"), style.value("80/tcp"), style.dim("note"))
    assert capsys.readouterr().out == "web 80/tcp note\n"

def test_cli_bases_list_is_plain_without_a_terminal(capsys):
    main(["bases", "list"])
    out = capsys.readouterr().out
    assert "base/web-nginx" in out
    assert "\x1b[" not in out

def test_cli_bases_list_shows_a_header(capsys):
    main(["bases", "list"])
    lines = capsys.readouterr().out.splitlines()
    header = lines[0]
    assert header.split() == ["BASE", "STATUS", "LISTEN", "NOTE"]
    # Header and data share column starts, so the table reads as one grid.
    assert lines[1].startswith("base/")
    for token in ("STATUS", "LISTEN", "NOTE"):
        assert lines[1][header.index(token)] != " ", token
