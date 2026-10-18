"""Build, run, and confirm the solver prints the flag."""

from collections.abc    import Callable
from pathlib            import Path
from hg.build           import build_challenge
from hg.clean           import clean_challenge
from hg.errors          import ToolError
from hg.lint            import require_lint
from hg.run             import ContentFor, PasswordFor, RunResult, run_challenge
from hg.solve           import Solve, _env_slug, run_solver, solver_matches
import os


def verify_challenge(
    root:           Path,
    runner:         Callable[[list[str]], int]       | None = None,
    *,
    solve:          Solve                            | None = None,
    contents:       ContentFor                       | None = None,
    timeout:        float                                   = 60,
    host_port_for:  Callable[[int], int]             | None = None,
    read_port:      Callable[[str, int], int | None] | None = None,
    password_for:   PasswordFor                      | None = None,
) -> RunResult | None:
    data  = require_lint(root, strict=True)
    spec  = data["spec"]
    logic = spec.get("logic") or "any"
    runtime = spec.get("runtime")
    script  = spec["scripts"]["test"]
    if runtime == "static":
        build_challenge(root, runner)
        code, stdout = run_solver(root, os.environ.copy(), script, solve)
        if code != 0 or not solver_matches(stdout, spec.get("flags") or [], {}, logic):
            detail = stdout.strip()
            raise ToolError("solver did not produce the flag" + (f"\n{detail}" if detail else ""))
        return None

    build_challenge(root, runner)
    result: RunResult | None = None
    try:
        result = run_challenge(
            root,
            runner,
            contents      = contents,
            timeout       = timeout,
            host_port_for = host_port_for,
            read_port     = read_port,
            password_for  = password_for,
        )
        env = os.environ.copy()
        env["CHALLENGE_HOST"] = result.host
        env["CHALLENGE_PORT"] = str(result.port)
        env["CHALLENGE_CONNECTION"] = result.connection
        injected = {flag.id: flag.content for flag in result.flags}
        for flag in result.flags:
            env[f"CHALLENGE_FLAG_{_env_slug(flag.id)}"] = flag.content
        for user in result.users:
            env[f"CHALLENGE_USER_{_env_slug(user.member)}_{_env_slug(user.name)}"] = user.password
        code, stdout = run_solver(root, env, script, solve)
        if code != 0 or not solver_matches(stdout, spec.get("flags") or [], injected, logic):
            detail = stdout.strip()
            raise ToolError("solver did not produce the flag" + (f"\n{detail}" if detail else ""))
        return result
    finally:
        clean_challenge(root, runner)
