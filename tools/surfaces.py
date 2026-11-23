"""config/surfaces.json: the non-till translation surfaces (ADR-0177 §1;
ut-docs#5405). Loaded and validated here for sync_english.py and
provision_weblate.py. A surface's path and component are used to build file
paths and Weblate slugs from a hand-kept file, so they are checked as hard as
the till's area ids are."""

import json
import os
import posixpath
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import i18n_map  # noqa: E402

CONFIG_PATH = "config/surfaces.json"
BRANCH_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")
REQUIRED = ("repo", "english", "metadata", "component", "name")


class SurfaceError(Exception):
    pass


def _rel_path(value, what):
    if (not isinstance(value, str) or not value or value.startswith(("/", "-")) or "\\" in value
            or ".." in posixpath.normpath(value).split("/")):
        raise SurfaceError(f"{what} {value!r} must be a relative path inside the source repo")
    return value


def validate(doc, till_areas=()):
    """-> {surface: spec with defaults}. Raises SurfaceError."""
    if not isinstance(doc, dict) or doc.get("schema") != 1:
        raise SurfaceError(f"{CONFIG_PATH}: schema must be 1")
    surfaces = doc.get("surfaces")
    if not isinstance(surfaces, dict):
        raise SurfaceError(f"{CONFIG_PATH}: `surfaces` must be an object")
    out, components = {}, {}
    for name, sp in surfaces.items():
        if not isinstance(sp, dict):
            raise SurfaceError(f"surface {name!r} must be an object")
        for field in REQUIRED:
            if not isinstance(sp.get(field), str) or not sp[field]:
                raise SurfaceError(f"surface {name!r}: `{field}` is required")
        repo = sp["repo"]
        if not (repo.startswith("https://") or os.path.isabs(repo)):
            raise SurfaceError(f"surface {name!r}: repo {repo!r} must be an https:// URL (or an absolute path in tests)")
        _rel_path(sp["english"], f"surface {name!r}: english")
        _rel_path(sp["metadata"], f"surface {name!r}: metadata")
        comp = sp["component"]
        try:
            i18n_map.validate_area_id(comp)
        except ValueError as e:
            raise SurfaceError(f"surface {name!r}: {e}") from None
        if comp in till_areas:
            raise SurfaceError(f"surface {name!r}: component {comp!r} is already a till area")
        if comp in components:
            raise SurfaceError(f"surfaces {components[comp]!r} and {name!r} share component {comp!r}")
        components[comp] = name
        ns = sp.get("namespaces")
        if ns is not None and not (isinstance(ns, list) and ns and all(isinstance(x, str) and x for x in ns)):
            raise SurfaceError(f"surface {name!r}: `namespaces` must be a non-empty list of strings")
        try:
            i18n_map.validate_area_id(name)
        except ValueError as e:
            raise SurfaceError(f"surface name {name!r}: {e}") from None
        branch = sp.get("branch") or "main"
        if not BRANCH_RE.fullmatch(branch):
            raise SurfaceError(f"surface {name!r}: branch {branch!r} is not a plain branch name")
        if sp.get("catch_all") is not None and sp["catch_all"] is not True:
            raise SurfaceError(f"surface {name!r}: `catch_all` must be true when present")
        if sp.get("catch_all") and ns is not None:
            raise SurfaceError(f"surface {name!r}: `catch_all` and `namespaces` are exclusive")
        out[name] = dict(sp, branch=branch)
    return out


def till_areas(repo):
    try:
        with open(os.path.join(repo, "meta/areas.json"), encoding="utf-8") as f:
            return set(json.load(f))
    except (OSError, ValueError):
        return set()


def load(repo):
    """The validated surfaces of a repo checkout; {} when it has no file."""
    path = os.path.join(repo, CONFIG_PATH)
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        raise SurfaceError(f"cannot read {path}: {e}") from None
    return validate(doc, till_areas(repo))
