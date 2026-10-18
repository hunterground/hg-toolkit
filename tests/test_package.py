"""
    HunterGround Toolkit
    Packaging smoke test: the wheel installs and works standalone.

The schema and challenge templates must resolve through ``importlib.resources``
inside the installed package, not by walking ``__file__`` parents, so that a
wheel install has no dependency on the base-images repo layout.
"""

from pathlib    import Path
import subprocess
import sys
import pytest

TOOLKIT_ROOT = Path(__file__).resolve().parents[1]


## Helper Functions ----------------------------------------------------------------------
def _run(argv, cwd):
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True)


def _build_wheel(outdir: Path) -> Path:
    result = _run([sys.executable, "-m", "build", "--wheel", "--outdir", str(outdir), "."], TOOLKIT_ROOT)
    assert result.returncode == 0, result.stderr

    wheels = list(outdir.glob("*.whl"))
    assert len(wheels) == 1, wheels
    return wheels[0]


def _build_available() -> bool:
    return _run([sys.executable, "-m", "build", "--version"], TOOLKIT_ROOT).returncode == 0


## Main Tests ---------------------------------------------------------------------------
@pytest.mark.skipif(not _build_available(), reason="python-build is not installed; pip install build")
def test_wheel_install_is_standalone(tmp_path):
    wheel = _build_wheel(tmp_path / "dist")

    venv = tmp_path / "venv"
    result = _run([sys.executable, "-m", "venv", str(venv)], tmp_path)
    assert result.returncode == 0, result.stderr

    py = venv / "bin" / "python"
    result = _run([str(py), "-m", "pip", "install", "--quiet", str(wheel)], tmp_path)
    assert result.returncode == 0, result.stderr

    hg = venv / "bin" / "hg"
    challenge_dir = tmp_path / "smoke-01"

    # hg bases exercises the catalog (no resources).
    result = _run([str(hg), "bases"], tmp_path)
    assert result.returncode == 0, result.stderr
    assert "base/web-nginx" in result.stdout

    # hg challenge scaffold copies a template through importlib.resources.
    result = _run(
        [str(hg), "challenge", "scaffold", "--base", "web-nginx", "--id", "smoke-01", "--dest", str(challenge_dir)],
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert (challenge_dir / "services" / "web" / "Dockerfile").is_file()

    # hg challenge lint loads the schema through importlib.resources.
    result = _run([str(hg), "challenge", "lint", "--path", str(challenge_dir)], tmp_path)
    assert result.returncode == 0, result.stderr
