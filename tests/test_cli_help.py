"""
    HunterGround Toolkit
    Test for the command-line help text and usage errors
"""

from hg.cli import main
import pytest

## Fixtures ----------------------------------------------------------------------------------------
# Every leaf command, its help path, and the summary the help must show.
LEAF_COMMANDS = [
    (["login"],  "Sign in and store an API token."),
    (["logout"], "Sign out and remove the stored token."),
    (["whoami"], "Show the signed-in account."),
    (["bases", "list"], "List the base images this toolkit knows."),
    (["bases", "pull"], "Pull base images from Docker Hub and retag them locally."),
    (["bases", "remove"], "Remove base images pulled onto this machine."),
    (["bases", "clean"], "Remove every base image pulled onto this machine."),
    (["builders", "list"], "List the builder images this toolkit knows."),
    (["builders", "pull"], "Pull builder images from Docker Hub and retag them locally."),
    (["builders", "remove"], "Remove builder images pulled onto this machine."),
    (["builders", "clean"], "Remove every builder image pulled onto this machine."),
    (["contest", "create"], "Create a draft contest on the platform."),
    (["contest", "add-challenge"], "Link a challenge that is already on the platform."),
    (["contest", "publish"], "Publish a contest so players can see it."),
    (["contest", "scaffold"], "Write a contest tree from a type."),
    (["contest", "lint"], "Check contest.yaml and every challenge in it."),
    (["contest", "digest"], "Print the canonical contest manifest digest."),
    (["contest", "challenges"], "List discovered challenges, order, and points."),
    (["contest", "deploy"], "Push the contest, its challenges, and their handouts."),
    (["challenge", "scaffold"], "Write a new challenge tree from a base image and pin its digest."),
    (["challenge", "lint"], "Check a local challenge tree."),
    (["challenge", "digest"], "Print the canonical manifest digest."),
    (["challenge", "build"], "Build the challenge images with docker buildx and load them."),
    (["challenge", "run"], "Start the challenge and inject a random flag into each dynamic slot."),
    (["challenge", "status"], "Show whether the challenge containers are running."),
    (["challenge", "verify"], "Run strict lint, build, run, and confirm the solver prints the flag."),
    (["challenge", "solve"], "Run the solver against the challenge started by run."),
    (["challenge", "stop"], "Stop the challenge started by run and keep the images."),
    (["challenge", "clean"], "Stop the challenge and remove its local image."),
    (["challenge", "push"], "Create or update the challenge on the platform."),
    (["challenge", "material"], "Upload the player handout."),
]

def _description_column(line: str) -> int:
    """The column where the description starts, after the padded command name."""
    text = line.lstrip()
    _, rest = text.split(None, 1)
    return line.index(rest.lstrip())

## Tests -------------------------------------------------------------------------------------------
def test_root_help_is_grouped_and_has_an_example(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0

    out = capsys.readouterr().out
    assert "Author, run, and publish HunterGround challenges and contests." in out
    assert "Usage:\n  hg <command> [options]" in out
    assert "{login,logout" not in out
    assert "Account:" in out
    assert "Images:" in out
    assert "Challenges:" in out
    assert "Contests:" in out
    assert "Examples:" in out
    assert "hg challenge run --path ../web-xss-01" in out

    login = next(line for line in out.splitlines() if line.strip().startswith("login "))
    challenge = next(line for line in out.splitlines() if line.strip().startswith("challenge "))
    assert _description_column(login) == _description_column(challenge)

def test_group_help_shows_summary_and_example(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["challenge", "--help"])
    assert exc.value.code == 0

    out = capsys.readouterr().out
    assert "Scaffold, check, run, and publish a challenge." in out
    assert "Usage:\n  hg challenge <command> [options]" in out
    assert "Author:" in out
    assert "Run:" in out
    assert "Publish:" in out
    assert "Examples:" in out
    assert "hg challenge scaffold --base web-nginx" in out

def test_bases_group_help_shows_summary_and_example(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["bases", "--help"])
    assert exc.value.code == 0

    out = capsys.readouterr().out
    assert "List, pull, or remove the base images this toolkit knows." in out
    assert "Examples:" in out
    assert "hg bases pull" in out


def test_builders_group_help_shows_summary_and_example(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["builders", "--help"])
    assert exc.value.code == 0

    out = capsys.readouterr().out
    assert "List, pull, or remove the builder images this toolkit knows." in out
    assert "Examples:" in out
    assert "hg builders pull" in out

def test_leaf_help_shows_summary_options_and_example(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["challenge", "run", "--help"])
    assert exc.value.code == 0

    out = capsys.readouterr().out
    assert "Start the challenge and inject a random flag into each dynamic slot." in out
    assert "Usage:\n  hg challenge run [options]" in out
    assert "--path <dir>" in out
    assert "--timeout <seconds>" in out
    assert "Examples:" in out
    assert "hg challenge run --path ../web-xss-01" in out

def test_required_options_are_separated_and_hyphens_stay_whole(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["contest", "create", "--help"])
    assert exc.value.code == 0

    out = capsys.readouterr().out
    assert "Required:" in out
    assert "--slug <slug>" in out
    assert "--type <type>" in out
    assert "private-competition" in out
    assert "private-\n" not in out
    assert "Options:" in out
    # Long flags line up with the long flag in `-h, --help`, not with `-h`.
    slug = next(line for line in out.splitlines() if "--slug" in line)
    help_line = next(line for line in out.splitlines() if "--help" in line)
    assert slug.index("--slug") == help_line.index("--help")

@pytest.mark.parametrize(("path", "summary"), LEAF_COMMANDS)
def test_every_leaf_command_has_summary_and_example(path, summary, capsys):
    with pytest.raises(SystemExit) as exc:
        main([*path, "--help"])
    assert exc.value.code == 0

    out = capsys.readouterr().out
    assert summary in out, f"{' '.join(path)} help is missing its summary"
    assert "Examples:" in out, f"{' '.join(path)} help is missing an example"
    assert f"hg {' '.join(path)}" in out

def test_bare_root_errors_with_summary_and_example(capsys):
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2

    err = capsys.readouterr().err
    assert "error: hg: a command is required" in err
    assert "Usage:" in err
    assert "hg <command> [options]" in err
    assert "Author, run, and publish HunterGround challenges and contests." in err
    assert "Examples:" in err
    assert "hg --help" in err

def test_bare_group_errors_with_summary_and_example(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["challenge"])
    assert exc.value.code == 2

    err = capsys.readouterr().err
    assert "error: hg challenge: a command is required" in err
    assert "Usage:" in err
    assert "Scaffold, check, run, and publish a challenge." in err
    assert "Examples:" in err

def test_unknown_command_errors_with_summary_and_example(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["challenge", "frobnicate"])
    assert exc.value.code == 2

    err = capsys.readouterr().err
    assert "error: hg challenge: unknown command 'frobnicate'" in err
    assert "Scaffold, check, run, and publish a challenge." in err
    assert "Examples:" in err

def test_unknown_flag_on_leaf_reports_the_leaf(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["challenge", "run", "--bogus"])
    assert exc.value.code == 2

    err = capsys.readouterr().err
    assert "error: hg challenge run: unknown option --bogus" in err
    assert "Start the challenge and inject a random flag into each dynamic slot." in err
    assert "hg challenge run --path ../web-xss-01" in err

def test_help_and_errors_carry_no_ansi_when_captured(capsys):
    """Help and usage errors are captured, not a terminal, so they stay plain."""
    with pytest.raises(SystemExit):
        main(["challenge", "run", "--help"])
    assert "\x1b[" not in capsys.readouterr().out

    with pytest.raises(SystemExit):
        main(["challenge", "frobnicate"])
    assert "\x1b[" not in capsys.readouterr().err
