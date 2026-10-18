"""
    HunterGround Toolkit
    Test for solving the challenge
"""

from hg.errors      import ToolError
from hg.scaffold    import create_challenge
from hg.solve       import check_graph, plan_solve, recover_flags, solve_challenge, solver_matches
from hg.state       import clear_state, read_state, state_path, write_state
import json
import pytest


## Helper Functions ----------------------------------------------------------------------
def _root(tmp_path):
    return create_challenge("web-nginx", "web-xss-01", tmp_path / "web-xss-01")


def _record(root, *, port=32780, connection="http://127.0.0.1:32780", flag="hg{live}"):
    write_state(
        root,
        {
            "connection": connection,
            "host": "127.0.0.1",
            "port": port,
            "entry_container": "hg-web-xss-01-web",
            "entry_container_port": 80,
            "flags": [
                {
                    "id": "flag",
                    "service": "web",
                    "path": "/app/flag.txt",
                    "owner": "nginx:nginx",
                    "mode": "0400",
                    "content": flag,
                }
            ],
            "users": [],
            "containers": [
                {"name": "hg-web-xss-01-web", "image": "challenges/web/web-xss-01:local"}
            ],
        },
    )


def _reader(content="hg{live}"):
    return lambda _container, _path: content


def _solver(stdout="", code=0):
    calls = []

    def run(script, env):
        calls.append((script, env))
        return code, stdout

    run.calls = calls
    return run


## Main Tests ---------------------------------------------------------------------------
def test_plan_solve_exports_the_recorded_endpoint(tmp_path):
    root = _root(tmp_path)
    _record(root)
    env = plan_solve(root, read_file=_reader()).env
    assert env["CHALLENGE_HOST"] == "127.0.0.1"
    assert env["CHALLENGE_PORT"] == "32780"
    assert env["CHALLENGE_CONNECTION"] == "http://127.0.0.1:32780"
    assert env["CHALLENGE_FLAG_FLAG"] == "hg{live}"

def test_plan_solve_exports_a_recorded_user_password(tmp_path):
    root = _root(tmp_path)
    state = {
        "connection": "http://127.0.0.1:32780",
        "host": "127.0.0.1",
        "port": 32780,
        "entry_container": "hg-web-xss-01-web",
        "entry_container_port": 80,
        "flags": [],
        "users": [
            {
                "member": "web",
                "name": "player",
                "password": "secretpw",
                "fields": {"name": "player", "password": "secretpw", "uid": 2000},
            }
        ],
        "containers": [{"name": "hg-web-xss-01-web", "image": "x:local"}],
    }
    write_state(root, state)
    env = plan_solve(root, read_file=_reader()).env
    assert env["CHALLENGE_USER_WEB_PLAYER"] == "secretpw"


def test_plan_solve_reads_the_flag_out_of_the_graph(tmp_path):
    root = _root(tmp_path)
    _record(root, flag="stale")
    env = plan_solve(root, read_file=_reader("hg{fresh}")).env
    assert env["CHALLENGE_FLAG_FLAG"] == "hg{fresh}"


def test_plan_solve_needs_a_run(tmp_path):
    root = _root(tmp_path)
    with pytest.raises(ToolError, match="hg challenge run"):
        plan_solve(root, read_file=_reader())


def test_plan_solve_asks_docker_for_the_port_when_not_recorded(tmp_path):
    root = _root(tmp_path)
    state = {
        "connection": "",
        "host": "127.0.0.1",
        "port": None,
        "entry_container": "hg-web-xss-01-web",
        "entry_container_port": 80,
        "flags": [],
        "users": [],
        "containers": [{"name": "hg-web-xss-01-web", "image": "x:local"}],
    }
    write_state(root, state)
    seen = []

    def read_port(container, container_port):
        seen.append((container, container_port))
        return 40000

    result = plan_solve(root, read_port=read_port, read_file=_reader())
    assert seen == [("hg-web-xss-01-web", 80)]
    assert result.port == 40000
    assert result.connection == "http://127.0.0.1:40000"


def test_plan_solve_rerenders_the_connection_when_the_port_is_overridden(tmp_path):
    root = _root(tmp_path)
    _record(root, port=32780, connection="http://127.0.0.1:32780")
    result = plan_solve(root, port=9999, read_file=_reader())
    assert result.port == 9999
    assert result.connection == "http://127.0.0.1:9999"
    assert any("overridden" in warning for warning in result.warnings)


def test_plan_solve_keeps_the_recorded_connection_untouched(tmp_path):
    """A non-HTTP connection template would be wrong to re-derive, so trust the run."""
    root = _root(tmp_path)
    _record(root, connection="nc 127.0.0.1 32780")
    result = plan_solve(root, read_file=_reader())
    assert result.connection == "nc 127.0.0.1 32780"


def test_plan_solve_raises_when_the_graph_is_gone(tmp_path):
    root = _root(tmp_path)
    _record(root)
    with pytest.raises(ToolError, match="hg challenge run"):
        plan_solve(root, read_file=lambda _c, _p: None)


def test_recover_flags_is_empty_without_dynamic_flags(tmp_path):
    root = _root(tmp_path)
    assert read_state(root) is None
    assert recover_flags({"flags": [{"id": "x", "type": "static", "content": "hg{x}"}]}, "web-xss-01", {}) == ()


def test_recover_flags_keeps_every_flag_on_one_service(tmp_path):
    """A service may own several dynamic flags; a per-service dict would drop all but one."""
    flag = {"id": "a", "type": "dynamic", "path": "/app/a.txt", "service": "web"}
    second = {"id": "b", "type": "dynamic", "path": "/app/b.txt", "service": "web"}
    state = {"containers": [{"name": "hg-web-xss-01-web", "image": "x:local"}]}
    seen = {}

    def reader(container, path):
        seen[(container, path)] = True
        return f"hg{{{path}}}"

    recovered = recover_flags({"flags": [flag, second]}, "web-xss-01", state, reader)
    assert [(item.id, item.path) for item in recovered] == [
        ("a", "/app/a.txt"),
        ("b", "/app/b.txt"),
    ]
    assert seen == {
        ("hg-web-xss-01-web", "/app/a.txt"): True,
        ("hg-web-xss-01-web", "/app/b.txt"): True,
    }


def test_recover_flags_reports_a_service_outside_the_run(tmp_path):
    state = {"containers": [{"name": "hg-web-xss-01-web", "image": "x:local"}]}
    flag = {"id": "a", "type": "dynamic", "path": "/app/a.txt", "service": "bot"}
    with pytest.raises(ToolError, match="not part of the run"):
        recover_flags({"flags": [flag]}, "web-xss-01", state, _reader())


def test_solve_runs_the_manifest_script_by_default(tmp_path):
    root = _root(tmp_path)
    _record(root)
    solve = _solver(stdout="hg{live}\n")
    outcome = solve_challenge(root, solve=solve, read_file=_reader(), check=False)
    assert outcome.ok
    assert solve.calls[0][0] == root / "private" / "solve.sh"


def test_solve_honors_a_script_override(tmp_path):
    root = _root(tmp_path)
    _record(root)
    (root / "private" / "probe.sh").write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    solve = _solver(stdout="hi\n")
    solve_challenge(root, "private/probe.sh", solve=solve, read_file=_reader(), check=False)
    assert solve.calls[0][0] == root / "private" / "probe.sh"


def test_solve_reports_a_missing_script(tmp_path):
    root = _root(tmp_path)
    _record(root)
    with pytest.raises(ToolError, match="solver script not found"):
        solve_challenge(root, "private/nope.sh", read_file=_reader(), check=False)


def test_solve_fails_when_the_solver_prints_nothing_useful(tmp_path):
    root = _root(tmp_path)
    _record(root)
    outcome = solve_challenge(root, solve=_solver(stdout="no flag here"), read_file=_reader(), check=False)
    assert not outcome.ok
    assert outcome.code == 0


def test_solve_fails_when_the_solver_exits_non_zero(tmp_path):
    root = _root(tmp_path)
    _record(root)
    outcome = solve_challenge(root, solve=_solver(stdout="hg{live}", code=2), read_file=_reader(), check=False)
    assert not outcome.ok
    assert outcome.code == 2


def test_check_graph_warns_about_stopped_containers(tmp_path):
    root = _root(tmp_path)
    _record(root)
    assert check_graph(root, runner=lambda _argv: 1) == ("hg-web-xss-01-web is not running",)


def test_check_graph_is_silent_when_everything_is_up(tmp_path):
    root = _root(tmp_path)
    _record(root)
    assert check_graph(root, runner=lambda _argv: 0) == ()


def test_state_round_trip_and_clear(tmp_path):
    root = _root(tmp_path)
    _record(root)
    assert state_path(root).is_file()
    assert read_state(root)["port"] == 32780
    clear_state(root)
    assert read_state(root) is None
    assert not state_path(root).exists()


def test_solver_matches_static_and_regex_flags():
    flags = [
        {"id": "a", "type": "static", "content": "hg{fixed}"},
        {"id": "b", "type": "regex", "content": r"hg\{[0-9a-f]+\}"},
    ]
    assert solver_matches("hg{fixed}", flags, {}, "any")
    assert solver_matches("hg{deadbeef}", flags, {}, "any")
    assert not solver_matches("nothing", flags, {}, "any")
    assert solver_matches("hg{fixed} hg{deadbeef}", flags, {}, "all")
    assert not solver_matches("hg{fixed}", flags, {}, "all")


def test_cli_run_records_state_for_solve(tmp_path, monkeypatch):
    from hg import cli
    from hg.run import InjectedFlag, RunResult, RunningContainer

    root = _root(tmp_path)
    result = RunResult(
        connection="http://127.0.0.1:32780",
        host="127.0.0.1",
        port=32780,
        flags=(
            InjectedFlag(
                id="flag",
                service="web",
                path="/app/flag.txt",
                owner="nginx:nginx",
                mode="0400",
                content="hg{live}",
            ),
        ),
        users=(),
        containers=(RunningContainer(name="hg-web-xss-01-web", image="x:local"),),
        entry_container="hg-web-xss-01-web",
        entry_container_port=80,
    )
    monkeypatch.setattr(cli, "run_challenge", lambda *a, **k: result)

    cli.main(["challenge", "run", "--path", str(root)])
    recorded = json.loads(state_path(root).read_text(encoding="utf-8"))
    assert recorded["port"] == 32780
    assert recorded["entry_container"] == "hg-web-xss-01-web"
    assert recorded["flags"][0]["content"] == "hg{live}"


def test_stop_clears_the_recorded_state(tmp_path):
    from hg.run import stop_challenge

    root = _root(tmp_path)
    _record(root)
    stop_challenge(root, runner=lambda _argv: 1)
    assert read_state(root) is None


def test_clean_clears_the_recorded_state(tmp_path):
    from hg.clean import clean_challenge

    root = _root(tmp_path)
    _record(root)
    clean_challenge(root, runner=lambda _argv: 1)
    assert read_state(root) is None
