"""
Shared fixtures. Scaffold reads a base digest from Docker Hub; unit tests
supply a fixed one so they do not need a daemon.
"""

import pytest

STUB_DIGEST = "sha256:" + ("ab" * 32)

@pytest.fixture(autouse=True)
def _stub_base_digest(monkeypatch):
    monkeypatch.setattr("hg.scaffold.base_digest", lambda _tag: STUB_DIGEST)
