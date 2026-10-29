#!/usr/bin/env python3
"""Sync universal-till's English strings and key metadata into this repo's
area files (ADR-0160 §2, §5; ut-docs#4365).

Reads (never writes) the public universal-till repo:
  main                    web/locales/en.json, web/i18n-meta/{en.meta,areas}.json
  each supported line     the newest final release tag vX.Y.Z of that line,
                          same files (a tag cut before the metadata existed
                          simply has no en.meta.json)
and writes, for every area in main's areas.json:
  <area>/en.json          flat key -> English, union over main + supported lines
  meta/<area>.json        key -> {explanation, flags, labels, lines} for Weblate
  meta/areas.json         component names, descriptions, key counts
  meta/source.json        which commits the current files came from
Weblate's own <area>/<code>.json files are never touched.

Commits only when an area or meta file changes: a till commit that doesn't
change English or its metadata leaves this repo alone (source.json is only
rewritten together with a real change).

Usage:
  python3 tools/sync_english.py [--repo DIR] [--source URL] [--no-commit]
  git diff --raw -z --no-renames ... | python3 tools/sync_english.py --check-paths --raw --areas-file F
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import i18n_map  # noqa: E402

EN_PATH = "web/locales/en.json"
META_PATH = "web/i18n-meta/en.meta.json"
AREAS_PATH = "web/i18n-meta/areas.json"
CONFIG_PATH = "config/lines.json"

LINE_RE = re.compile(r"^(\d+)\.(\d+)$")
TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")  # final releases only, no -rc


class SyncError(Exception):
    pass


# The only paths the sync owns, and so the only ones an auto-merge PR may
# carry (ut-docs#4367, owner decision Option A). Anything else is a code or
# config change and needs a human review. Deliberately a strict allowlist:
# one path segment per level, lower-case, no dots at the start, no traversal.
_AREA_EN_RE = re.compile(r"\A([a-z0-9][a-z0-9_-]*)/en\.json\Z")
_META_RE = re.compile(r"\Ameta/[a-z0-9][a-z0-9_-]*\.json\Z")
# Top-level folders that hold code or config, never an area, whatever the
# areas.json (an area named `tools` must not turn tools/en.json into "data").
_NOT_AREAS = frozenset({"tools", "config", "scripts", "tests", "docs", "meta"})


def auto_merge_allowed(paths, areas):
    """-> (allowed, offenders). `allowed` only when `paths` is non-empty and
    every path is `<area>/en.json` for an area ALREADY on main (`areas`, read
    from main's meta/areas.json, never from the PR) or `meta/<name>.json`.
    A brand-new area folder needs a human anyway (it needs a Weblate
    component), so it is refused here."""
    paths = list(paths)
    offenders = []
    for p in paths:
        m = _AREA_EN_RE.match(p)
        if m and m.group(1) in areas and m.group(1) not in _NOT_AREAS:
            continue
        if _META_RE.match(p):
            continue
        offenders.append(p)
    return (bool(paths) and not offenders), offenders


# `git diff --raw -z --no-renames` entries the sync may carry: a regular
# file (100644) added, modified or deleted. A typechange (symlink, exec bit,
# submodule) under a sync-owned name is not data (review MEDIUM-1, LOW-1).
_RAW_META_RE = re.compile(r"\A:(\d{6}) (\d{6}) [0-9a-f]+ [0-9a-f]+ ([A-Z])\Z")
_RAW_OK = {"A": ("000000", "100644"), "M": ("100644", "100644"), "D": ("100644", "000000")}


def parse_raw(raw):
    """-> (paths, problems) from `git diff --raw -z --no-renames` output."""
    toks = raw.split("\0")
    if toks and toks[-1] == "":
        toks.pop()
    elif toks and toks != [""]:
        return [], ["input is not NUL-terminated"]
    paths, problems = [], []
    if len(toks) % 2:
        return [], ["input is not <meta> NUL <path> NUL pairs"]
    for meta, path in zip(toks[0::2], toks[1::2]):
        m = _RAW_META_RE.match(meta)
        if not m:
            problems.append(f"{path!r}: unreadable diff entry {meta!r}")
            continue
        src, dst, status = m.groups()
        if _RAW_OK.get(status) != (src, dst):
            problems.append(f"{path!r}: status {status} mode {src}->{dst} is not a regular-file add/modify/delete")
            continue
        paths.append(path)
    return paths, problems


def _git(args, cwd=None):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SyncError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout


def load_config(repo):
    path = os.path.join(repo, CONFIG_PATH)
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError) as e:
        raise SyncError(f"cannot read {path}: {e}")
    if not isinstance(cfg.get("source"), str) or not cfg["source"]:
        raise SyncError(f"{path}: `source` (the universal-till clone URL) is required")
    lines = cfg.get("supported_lines")
    if not isinstance(lines, list) or not all(isinstance(x, str) for x in lines):
        raise SyncError(f"{path}: `supported_lines` must be a list of \"MAJOR.MINOR\" strings")
    cfg.setdefault("branch", "main")
    return cfg


def resolve_lines(tags, supported):
    """{line: newest final tag of that line}. A supported line with no tag is
    an error: the list names lines that shipped."""
    best = {}
    for t in tags:
        m = TAG_RE.match(t)
        if m:
            line = f"{m.group(1)}.{m.group(2)}"
            patch = int(m.group(3))
            if line not in best or patch > best[line][0]:
                best[line] = (patch, t)
    out = {}
    for line in supported:
        if not LINE_RE.match(line):
            raise SyncError(f"supported line {line!r} is not MAJOR.MINOR (e.g. \"0.34\")")
        if line not in best:
            raise SyncError(f"supported line {line} has no final release tag v{line}.N in the source repo")
        out[line] = best[line][1]
    return out


class GitSource:
    """Read-only view of the till repo: ls-remote + shallow fetch into a
    throwaway bare repo. Holds no credential; the URL is the public clone."""

    def __init__(self, url):
        self.url = url
        self._tmp = tempfile.TemporaryDirectory(prefix="ut-translations-src-")
        self.dir = self._tmp.name
        _git(["init", "-q", "--bare", self.dir])

    def close(self):
        self._tmp.cleanup()

    def tags(self):
        out = _git(["ls-remote", "--tags", "--refs", self.url])
        return [line.split("refs/tags/", 1)[1] for line in out.splitlines() if "refs/tags/" in line]

    def fetch(self, branch, tags):
        specs = [f"+refs/heads/{branch}:refs/remotes/src/{branch}"]
        specs += [f"+refs/tags/{t}:refs/tags/{t}" for t in tags]
        _git(["fetch", "-q", "--depth=1", "--no-tags", self.url, *specs], cwd=self.dir)

    def commit(self, ref):
        return _git(["rev-parse", f"{ref}^{{commit}}"], cwd=self.dir).strip()

    def read_json(self, ref, path):
        r = subprocess.run(["git", "show", f"{ref}:{path}"], cwd=self.dir, capture_output=True, text=True)
        if r.returncode != 0:
            return None
        try:
            return json.loads(r.stdout)
        except ValueError as e:
            raise SyncError(f"{ref}:{path} is not valid JSON: {e}")


def _line_key(line):
    a, b = line.split(".")
    return (int(a), int(b))


def collect(cfg, source=None):
    """-> (files, source_info, warnings)."""
    src = source or GitSource(cfg["source"])
    try:
        line_tags = resolve_lines(src.tags(), cfg["supported_lines"])
        branch = cfg["branch"]
        src.fetch(branch, sorted(set(line_tags.values())))
        main_ref = f"refs/remotes/src/{branch}"
        en = src.read_json(main_ref, EN_PATH)
        areas = src.read_json(main_ref, AREAS_PATH)
        if not isinstance(en, dict):
            raise SyncError(f"{branch}:{EN_PATH} is missing")
        if not isinstance(areas, dict) or not areas.get("areas"):
            raise SyncError(f"{branch}:{AREAS_PATH} is missing — the area map is required (ut-docs#4362)")
        lines = [i18n_map.Line("main", en, src.read_json(main_ref, META_PATH) or {})]
        info = {"main": {"branch": branch, "commit": src.commit(main_ref)}, "lines": {}}
        for line in sorted(line_tags, key=_line_key, reverse=True):
            tag = line_tags[line]
            ref = f"refs/tags/{tag}"
            line_en = src.read_json(ref, EN_PATH)
            if not isinstance(line_en, dict):
                raise SyncError(f"{tag}:{EN_PATH} is missing")
            lines.append(i18n_map.Line(line, line_en, src.read_json(ref, META_PATH) or {}))
            info["lines"][line] = {"tag": tag, "commit": src.commit(ref)}
        try:
            files = i18n_map.render(i18n_map.build_union(lines), areas)
        except ValueError as e:
            raise SyncError(f"{branch}:{AREAS_PATH}: {e}") from None
        return files, info
    finally:
        if source is None:
            src.close()


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None


def sync(repo, cfg, commit=True, source=None, log=print):
    files, info = collect(cfg, source)
    changed = sorted(p for p, text in files.items() if _read(os.path.join(repo, p)) != text)
    known = {p.split("/")[0] for p in files if p.endswith("/en.json")}
    for name in sorted(os.listdir(repo)):
        if (os.path.isfile(os.path.join(repo, name, "en.json")) and name not in known):
            log(f"warning: {name}/ is no longer an area in areas.json; its component and files "
                "need a manual decision (not deleted)")
    if not changed:
        log("no change: English and metadata already match universal-till")
        return False
    files["meta/source.json"] = i18n_map.dumps(info)
    for rel in list(changed) + ["meta/source.json"]:
        path = os.path.join(repo, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(files[rel])
    areas = json.loads(files["meta/areas.json"])
    log("updated: " + ", ".join(changed))
    log("areas: " + ", ".join(f"{a}={v['keys']}" for a, v in areas.items()))
    if commit:
        paths = sorted(set(changed) | {"meta/source.json"})
        _git(["add", "--", *paths], cwd=repo)
        lines = ", ".join(f"{k} {v['tag']}" for k, v in info["lines"].items()) or "none"
        msg = (f"sync: English from universal-till {info['main']['commit'][:12]}\n\n"
               f"Supported lines: {lines}.\nChanged: {', '.join(changed)}\n")
        _git(["commit", "-q", "-m", msg], cwd=repo)
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    ap.add_argument("--source", help="override config/lines.json `source` (e.g. a local clone)")
    ap.add_argument("--no-commit", action="store_true", help="write the files, don't commit")
    ap.add_argument("--areas-file", help="with --check-paths: main's meta/areas.json (the known areas)")
    ap.add_argument("--raw", action="store_true",
                    help="with --check-paths: stdin is `git diff --raw -z --no-renames` (modes checked too)")
    ap.add_argument("--check-paths", action="store_true",
                    help="read NUL-separated changed paths on stdin; exit 1 unless every one is "
                         "<area>/en.json or meta/*.json (the auto-merge gate)")
    args = ap.parse_args(argv)
    if args.check_paths:
        try:
            with open(args.areas_file, encoding="utf-8") as f:
                doc = json.load(f)
            if not isinstance(doc, dict):
                raise ValueError("not a JSON object")
            areas = set(doc)
        except (TypeError, OSError, ValueError) as e:
            print(f"refused: cannot read the known areas (--areas-file): {e}", file=sys.stderr)
            return 1
        raw = sys.stdin.buffer.read().decode("utf-8", "surrogateescape")
        if args.raw:
            paths, problems = parse_raw(raw)
            for pr in problems:
                print(f"refused: {pr}", file=sys.stderr)
            if problems or not paths:
                if not paths and not problems:
                    print("refused: no changed paths", file=sys.stderr)
                return 1
        else:
            paths = [p for p in raw.split("\0") if p != ""] if raw else []
        allowed, bad = auto_merge_allowed(paths, areas)
        if allowed:
            print(f"auto-merge allowed: {len(paths)} sync-owned path(s)")
            return 0
        if not paths:
            print("refused: no changed paths", file=sys.stderr)
        for p in bad:
            print(f"refused: {p!r} is not <area>/en.json or meta/*.json", file=sys.stderr)
        return 1
    try:
        cfg = load_config(args.repo)
        if args.source:
            cfg["source"] = args.source
        changed = sync(args.repo, cfg, commit=not args.no_commit)
    except SyncError as e:
        print(f"sync failed: {e}", file=sys.stderr)
        return 1
    if "GITHUB_OUTPUT" in os.environ:
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"changed={'true' if changed else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
