"""
    HunterGround Toolkit
    Test Suite
    Test for shell completion: the engine, the protocol, and the scripts
"""

from pathlib        import Path
from hg.catalog     import HUB_NAMESPACE, templates
from hg.cli         import build_parser, main
from hg.complete    import (
    SHELLS,
    Ask,
    candidates,
    directories,
    script,
)
from hg.errors      import ToolError
import pytest

## Fixtures ----------------------------------------------------------------------------------------
PARSER = build_parser()

def _tree(root: Path) -> Path:
    """A challenge tree with a solver, a nested script, and a handout."""
    (root / "services" / "web").mkdir(parents=True)
    (root / "private").mkdir()
    (root / "dist").mkdir()
    (root / "private" / "writeup.md").write_text("path\n", encoding="utf-8")
    (root / "private" / "solve.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (root / "private" / "poc" / "deep").mkdir(parents=True)
    (root / "private" / "poc" / "deep" / "too-far.py").write_text("x\n", encoding="utf-8")
    (root / "private" / "poc" / "probe.py").write_text("x\n", encoding="utf-8")
    (root / "dist" / "handout.zip").write_text("x\n", encoding="utf-8")
    (root / "challenge.yaml").write_text("apiVersion: hg.challenge/v1\n", encoding="utf-8")
    return root

def _answers(tokens, current):
    return candidates(PARSER, tokens, current)

## The command tree --------------------------------------------------------------------------------
def test_every_command_is_offered():
    assert _answers(["hg"], "contest") == ["contest"]
    assert _answers(["hg", "challenge"], "") == [
        "scaffold", "lint", "digest", "build", "run", "status", "verify", "solve", "stop", "clean", "push", "material",
    ]

def test_a_group_offers_its_subcommands_and_nothing_else():
    assert _answers(["hg", "bases"], "") == ["list", "pull", "remove", "clean"]
    assert _answers(["hg", "contest"], "") == [
        "create", "add-challenge", "publish", "scaffold", "lint", "digest", "challenges", "deploy",
    ]

def test_challenges_is_a_preview_with_no_subcommand():
    assert _answers(["hg", "contest", "challenges"], "") == []
    assert "--path" in _answers(["hg", "contest", "challenges"], "--")

def test_the_completion_command_is_offered():
    assert "completion" in _answers(["hg"], "")

def test_the_private_engine_is_not_offered():
    """A command a user never types must not appear in the list or the usage."""
    assert "__complete" not in _answers(["hg"], "")
    assert "__complete" not in PARSER.format_usage()
    assert "__complete" not in _answers(["hg"], "__")

## Flags -------------------------------------------------------------------------------------------
def test_a_dash_offers_the_flags_of_the_command_reached():
    assert _answers(["hg", "challenge", "run"], "--") == ["--help", "--path", "--timeout"]

def test_a_single_dash_offers_the_short_forms_too():
    offered = _answers(["hg", "challenge", "run"], "-")
    assert "-h" in offered
    assert "--help" in offered

def test_a_flag_can_be_completed_by_its_prefix():
    assert _answers(["hg", "challenge", "run"], "--ti") == ["--timeout"]

def test_a_flag_of_the_wrong_command_is_not_offered():
    assert _answers(["hg", "bases", "list"], "--path") == []

def test_a_hidden_flag_is_not_offered():
    """`hg login --url` is suppressed, so the shell must stay quiet about it."""
    assert "--url" not in _answers(["hg", "login"], "--")

def test_the_leaf_flags_come_from_the_command_reached():
    """A flag of `run` must not leak into the group above it."""
    group = _answers(["hg", "challenge"], "--timeout")
    assert group == []

## Flag values -------------------------------------------------------------------------------------
def test_base_completer_offers_the_documented_short_forms():
    offered = _answers(["hg", "challenge", "scaffold", "--base"], "")
    assert "web-nginx" in offered
    assert "debian" in offered
    assert "base/web-nginx" not in offered
    assert offered == templates()

def test_base_completer_filters_by_what_is_typed():
    assert _answers(["hg", "challenge", "scaffold", "--base"], "web-ng") == ["web-nginx"]

def test_bases_pull_completes_a_positional_base_name():
    assert _answers(["hg", "bases", "pull"], "web-ng") == ["web-nginx"]

def test_builders_pull_completes_a_positional_builder_name():
    assert _answers(["hg", "builders"], "") == ["list", "pull", "remove", "clean"]
    assert _answers(["hg", "builders", "pull"], "cp") == ["cpp", "cpp-cross"]

def test_remove_completes_a_short_name():
    assert _answers(["hg", "bases", "remove"], "web-ng") == ["web-nginx"]
    assert _answers(["hg", "builders", "remove"], "cp") == ["cpp", "cpp-cross"]

def test_clean_offers_no_image_names():
    assert _answers(["hg", "bases", "clean"], "") == []
    assert _answers(["hg", "builders", "clean"], "") == []
    assert _answers(["hg", "bases", "clean"], "web") == []
    assert _answers(["hg", "builders", "clean"], "cp") == []

def test_contest_type_completer_reads_the_type_table():
    offered = _answers(["hg", "contest", "scaffold", "--type"], "")
    assert offered == ["hiring-test", "mini-open", "open", "private-competition", "public-competition"]

def test_participation_completer_offers_both_values():
    assert _answers(["hg", "contest", "scaffold", "--participation"], "") == ["individual", "team"]

def test_a_flag_that_takes_a_free_value_offers_nothing():
    assert _answers(["hg", "challenge", "run", "--timeout"], "") == []
    assert _answers(["hg", "challenge", "push", "--image"], "reg") == []

def test_a_flag_equal_value_completes_the_value_and_keeps_the_flag():
    assert _answers(["hg", "challenge", "scaffold"], "--base=web-ng") == ["--base=web-nginx"]
    assert _answers(["hg", "contest", "scaffold"], "--type=open") == ["--type=open"]

def test_a_store_true_flag_never_swallows_the_next_word():
    """`--strict` takes no value, so the command after it is still a command."""
    assert _answers(["hg", "challenge", "lint", "--strict"], "--path") == ["--path"]

## Paths on disk -----------------------------------------------------------------------------------
def test_path_lists_directories_with_a_trailing_slash(tmp_path, monkeypatch):
    (tmp_path / "spring-2026" / "web-xss-01").mkdir(parents=True)
    (tmp_path / "spring-2026" / "notes.txt").write_text("x\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    offered = _answers(["hg", "contest", "lint", "--path"], "")
    assert "spring-2026/" in offered
    assert "notes.txt" not in offered

def test_path_descends_into_a_directory(tmp_path, monkeypatch):
    (tmp_path / "spring-2026" / "web-xss-01").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    assert _answers(["hg", "contest", "lint", "--path"], "spring-2026/") == ["spring-2026/web-xss-01/"]

def test_path_offers_the_manifest_in_a_tree_root(tmp_path, monkeypatch):
    _tree(tmp_path / "web-xss-01")
    monkeypatch.chdir(tmp_path)

    offered = _answers(["hg", "challenge", "lint", "--path"], "web-xss-01/")
    assert "web-xss-01/challenge.yaml" in offered

def test_path_hides_a_dot_entry_until_a_dot_is_typed(tmp_path, monkeypatch):
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "plain").mkdir()
    monkeypatch.chdir(tmp_path)

    assert _answers(["hg", "challenge", "run", "--path"], "") == ["plain/"]
    assert _answers(["hg", "challenge", "run", "--path"], ".") == ["./.hidden/", "./plain/"]

def test_file_flag_offers_files_not_only_directories(tmp_path, monkeypatch):
    (tmp_path / "handout.zip").write_text("x\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    offered = _answers(["hg", "challenge", "material", "--file"], "")
    assert offered == ["handout.zip"]

def test_a_path_that_does_not_exist_offers_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert _answers(["hg", "challenge", "run", "--path"], "ghost/") == []

## The solver script -------------------------------------------------------------------------------
def test_script_lists_the_files_of_the_challenge_on_the_line(tmp_path):
    root = _tree(tmp_path / "web-xss-01")
    tokens = ["hg", "challenge", "solve", "--path", str(root), "--script"]

    offered = _answers(tokens, "")
    assert "private/solve.sh" in offered
    assert "private/writeup.md" in offered
    assert "challenge.yaml" in offered

def test_script_skips_an_output_directory(tmp_path):
    root = _tree(tmp_path / "web-xss-01")
    offered = _answers(["hg", "challenge", "solve", "--path", str(root), "--script"], "")
    assert not any(name.startswith("dist/") for name in offered)

def test_script_stops_before_the_tree_gets_deep(tmp_path):
    root = _tree(tmp_path / "web-xss-01")
    offered = _answers(["hg", "challenge", "solve", "--path", str(root), "--script"], "")
    assert "private/poc/probe.py" in offered
    assert "private/poc/deep/too-far.py" not in offered

def test_script_filters_by_the_word_being_typed(tmp_path):
    root = _tree(tmp_path / "web-xss-01")
    tokens = ["hg", "challenge", "solve", "--path", str(root), "--script"]
    assert _answers(tokens, "private/sol") == ["private/solve.sh"]

def test_script_reads_the_path_from_a_sibling_flag(tmp_path):
    root = _tree(tmp_path / "web-xss-01")
    tokens = ["hg", "challenge", "solve", "--path", str(root), "--script"]
    # The answer is relative to the tree, and the tree is already on the line.
    offered = _answers(tokens, "")
    assert "private/solve.sh" in offered
    assert "challenge.yaml" in offered

def test_script_reads_the_path_from_a_sibling_flag_written_with_an_equals_sign(tmp_path):
    root = _tree(tmp_path / "web-xss-01")
    tokens = ["hg", "challenge", "solve", f"--path={root}", "--script"]
    assert "private/solve.sh" in _answers(tokens, "")

def test_script_falls_back_to_the_shell_directory_outside_a_challenge(tmp_path, monkeypatch):
    (tmp_path / "loose.txt").write_text("x\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert _answers(["hg", "challenge", "solve", "--script"], "") == ["loose.txt"]

## The protocol ------------------------------------------------------------------------------------
def test_a_caller_may_omit_the_program_name():
    assert _answers(["challenge"], "ne") == _answers(["hg", "challenge"], "ne")

def test_a_word_the_tree_does_not_know_ends_the_line():
    """
    A group takes a command and nothing else: a word that is not one of its
    commands is a typo, so nothing after it can be resolved or offered.
    """
    assert _answers(["hg", "challenge", "frobnicate", "--path"], "--str") == []
    assert _answers(["hg", "frobnicate"], "") == []

def test_a_question_keeps_the_directory_and_the_leaf_apart():
    assert Ask(word="../web-xss/priv", tokens=()).head == "../web-xss/"
    assert Ask(word="priv", tokens=()).head == ""
    assert Ask(word="./priv", tokens=()).head == "./"
    assert Ask(word=".", tokens=()).head == "./"
    assert Ask(word="..", tokens=()).head == "../"
    assert Ask(word="../", tokens=()).head == "../"

def test_a_question_reads_a_sibling_flag_from_the_line():
    ask = Ask(word="private/", tokens=("--path", "../web-xss-01", "--script"))
    assert ask.option("--path") == "../web-xss-01"

def test_a_question_reads_a_sibling_flag_written_with_an_equals_sign():
    ask = Ask(word="private/", tokens=("--path=../web-xss-01",))
    assert ask.option("--path") == "../web-xss-01"

def test_a_question_takes_the_last_value_of_a_repeated_flag():
    ask = Ask(word="", tokens=("--path", "one", "--path", "two"))
    assert ask.option("--path") == "two"

## The scripts -------------------------------------------------------------------------------------
def test_every_supported_shell_has_a_script():
    for shell in SHELLS:
        assert script(shell).strip()

def test_a_script_calls_the_private_command_with_the_current_word_and_the_line():
    for shell in SHELLS:
        assert "hg __complete --current" in script(shell)

def test_an_unknown_shell_is_rejected():
    with pytest.raises(ToolError, match="unknown shell"):
        script("csh")

def test_the_shell_name_is_case_and_space_insensitive():
    assert script("  ZSH ") == script("zsh")

## The command -------------------------------------------------------------------------------------
def test_completion_command_prints_the_script(capsys):
    main(["completion", "bash"])
    out = capsys.readouterr().out
    assert out == script("bash")

def test_completion_command_rejects_an_unknown_shell(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["completion", "csh"])
    assert exc.value.code == 2
    assert "invalid choice" in capsys.readouterr().err

def test_private_command_answers_on_stdout(capsys):
    main(["__complete", "--current=ch", "--", "hg"])
    assert capsys.readouterr().out == "challenge\n"

def test_private_command_answers_without_error_for_an_unknown_word(capsys):
    main(["__complete", "--current=", "--", "hg", "frobnicate"])
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""

def test_private_command_is_silent_when_it_cannot_answer(capsys, monkeypatch):
    """A broken engine must leave the prompt alone, not print a traceback."""
    def explode(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("hg.cli.answer", explode)
    main(["__complete", "--current=", "--", "hg"])
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""

def test_the_registry_path_is_not_a_base_name():
    """`hub_ref` prefixes the org: a base name must never carry it."""
    offered = _answers(["hg", "challenge", "scaffold", "--base"], "")
    assert not any(name.startswith(f"{HUB_NAMESPACE}-") for name in offered)

def test_directory_completer_keeps_the_directory_part_of_the_word(tmp_path, monkeypatch):
    (tmp_path / "outer" / "inner").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    assert directories(Ask(word="out", tokens=())) == ["outer/"]
    assert directories(Ask(word="./out", tokens=())) == ["./outer/"]
