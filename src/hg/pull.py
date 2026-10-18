"""
Fetch base and builder images from the public hub and retag them locally.

Images are published under a Docker Hub org with the registry path flattened
(`base/web-nginx:latest` -> `hunterground/base-web-nginx:latest`,
`builder/cpp:latest` -> `hunterground/builder-cpp:latest`).
This module pulls them and retags them locally so the templates and the lint
allowlist can keep using the bare names.
"""

from dataclasses    import dataclass
from hg.build       import Runner, default_runner, fetch_image
from hg.catalog     import BASES, BUILDERS, find_base, find_builder


@dataclass(frozen=True)
class PullResult:
    """Bare tags that are available locally, and tags the hub did not have."""

    pulled:  tuple[str, ...]
    missing: tuple[str, ...]


def pull_bases(names: list[str] | None = None, runner: Runner | None = None) -> PullResult:
    """
    Pull bases from the hub and retag them locally.

    With no `names`, every catalog base (and each pack it inspects) is pulled.
    Otherwise each name resolves through the catalog, accepting the short form 
    (`web-nginx` for `base/web-nginx`).
    A missing hub image is recorded and the rest of the set is still pulled.
    A failed local retag stops immediately.
    """
    run = runner or default_runner

    bases: list = []
    if not names:
        bases = list(BASES)
    else:
        for name in names:
            bases.append(find_base(name))

    pulled:     list[str] = []
    missing:    list[str] = []
    for base in bases:
        for tag in (base.image, *base.extra_inspect):
            if fetch_image(run, tag):
                pulled.append(tag)
            else:
                missing.append(tag)
    return PullResult(tuple(pulled), tuple(missing))


def pull_builders(names: list[str] | None = None, runner: Runner | None = None) -> PullResult:
    """
    Pull builders from the hub and retag them locally.

    With no `names`, every catalog builder is pulled.
    Otherwise each name resolves through the catalog, accepting the short form
    (`cpp` for `builder/cpp`).
    A missing hub image is recorded and the rest of the set is still pulled.
    A failed local retag stops immediately.
    A published builder is already built, so its `FROM` parent is not pulled.
    """
    run = runner or default_runner

    builders: list = []
    if not names:
        builders = list(BUILDERS)
    else:
        for name in names:
            builders.append(find_builder(name))

    pulled:  list[str] = []
    missing: list[str] = []
    for builder in builders:
        if fetch_image(run, builder.image):
            pulled.append(builder.image)
        else:
            missing.append(builder.image)
    return PullResult(tuple(pulled), tuple(missing))
