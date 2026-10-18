"""
Remove base and builder images from the local machine.

Pull stores two names for one image: the catalog tag (`base/web-nginx:latest`)
and the Docker Hub tag (`hunterground/base-web-nginx:latest`). Removal deletes
those tags. A tag that is already gone is success. Version tags, architecture
tags, and challenge images are left alone.
"""

from collections.abc    import Callable, Iterable
from hg.build           import Runner, default_runner
from hg.catalog         import BASES, BUILDERS, find_base, find_builder, hub_ref
from hg.errors          import ToolError
import subprocess


Inspector = Callable[[str], str | None]


class ImageRemoveError(ToolError):
    """Some requested images were kept or could not be removed."""

    def __init__(self, message: str, removed: tuple[str, ...]) -> None:
        super().__init__(message)
        self.removed = removed


## Helper Functions ---------------------------------------------------------------------------------
def _unique(images: Iterable[str]) -> list[str]:
    seen:   set[str]  = set()
    unique: list[str] = []
    for image in images:
        if image in seen:
            continue
        seen.add(image)
        unique.append(image)
    return unique


def _remove_images(
    images:     list[str],
    runner:     Runner    | None,
    inspector:  Inspector | None,
) -> tuple[str, ...]:
    run    = runner    or default_runner
    lookup = inspector or default_inspector

    removed: list[str] = []
    kept:    list[str] = []
    failed:  list[str] = []
    for image in images:
        tags = _tags_to_remove(image, lookup)
        if tags is None:
            kept.append(image)
            continue

        if not tags:
            continue

        if run(["docker", "image", "rm", *tags]) != 0:
            failed.append(image)
            continue

        removed.extend(tags)

    if kept or failed:
        raise ImageRemoveError(_problem(kept, failed), tuple(removed))
    return tuple(removed)


def _tags_to_remove(image: str, lookup: Inspector) -> list[str] | None:
    """
    The tags to pass to `docker image rm`.

    None means the local tag and the hub tag are different images, so
    neither tag is removed. An empty list means neither tag is present.
    """
    local    = image
    hub      = hub_ref(image)
    local_id = lookup(local)
    hub_id   = lookup(hub)
    if local_id is not None and hub_id is not None and local_id != hub_id:
        return None

    tags: list[str] = []
    if local_id is not None:
        tags.append(local)
    if hub_id is not None:
        tags.append(hub)
    return tags


def _problem(kept: list[str], failed: list[str]) -> str:
    lines: list[str] = []
    for image in kept:
        lines.append(f"kept {image}; the local tag and the hub tag are different images")
    for image in failed:
        lines.append(f"docker image rm failed for {image}")
    return "\n".join(lines)


## Main Functions ---------------------------------------------------------------------------------
def default_inspector(tag: str) -> str | None:
    """The image id for `tag`, or None when the tag is not present."""
    completed = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", tag],
        check  = False,
        stdout = subprocess.PIPE,
        stderr = subprocess.DEVNULL,
        text   = True,
    )
    if completed.returncode != 0:
        return None
    text = completed.stdout.strip()
    return text or None


def remove_bases(
    names:      list[str] | None = None,
    runner:     Runner    | None = None,
    inspector:  Inspector | None = None,
) -> tuple[str, ...]:
    """
    Remove the named bases.

    Each name resolves through the catalog, accepting the short form
    (`web-nginx` for `base/web-nginx`). A builder name is rejected.
    An empty list is an error; removing every base is `clean_bases`.
    A base's extra images, such as a pack, are not removed.
    """
    if not names:
        raise ToolError("name a base to remove, or run hg bases clean")

    images = _unique(find_base(name).image for name in names)
    return _remove_images(images, runner, inspector)


def remove_builders(
    names:      list[str] | None = None,
    runner:     Runner    | None = None,
    inspector:  Inspector | None = None,
) -> tuple[str, ...]:
    """
    Remove the named builders.

    Each name resolves through the catalog, accepting the short form
    (`cpp` for `builder/cpp`). A base name is rejected.
    An empty list is an error; removing every builder is `clean_builders`.
    """
    if not names:
        raise ToolError("name a builder to remove, or run hg builders clean")

    images = _unique(find_builder(name).image for name in names)
    return _remove_images(images, runner, inspector)


def clean_bases(
    runner:     Runner    | None = None,
    inspector:  Inspector | None = None,
) -> tuple[str, ...]:
    """
    Remove every base a no-argument `hg bases pull` would fetch.

    That set is each catalog base plus each extra image it inspects.
    """
    images: list[str] = []
    for base in BASES:
        images.append(base.image)
        images.extend(base.extra_inspect)
    return _remove_images(_unique(images), runner, inspector)


def clean_builders(
    runner:     Runner    | None = None,
    inspector:  Inspector | None = None,
) -> tuple[str, ...]:
    """Remove every catalog builder."""
    images = [builder.image for builder in BUILDERS]
    return _remove_images(images, runner, inspector)
