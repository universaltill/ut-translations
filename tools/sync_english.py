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
  python3 tools/sync_english.py --surface NAME [--repo DIR] [--source URL] [--no-commit]

--surface syncs one non-till surface from config/surfaces.json (ADR-0177 §1;
website, cloud-*): main of that surface's repo -> <component>/en.json and
meta/<component>.json (+ meta/source-<surface>.json), restricted to the
surface's `namespaces` when it lists any. No supported-line union: those
repos are not versioned in lines. The till path above is unchanged.
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
import surfaces as surfaces_mod  # noqa: E402

EN_PATH = "web/locales/en.json"
META_PATH = "web/i18n-meta/en.meta.json"
AREAS_PATH = "web/i18n-meta/areas.json"
CONFIG_PATH = "config/lines.json"

LINE_RE = re.compile(r"^(\d+)\.(\d+)$")
TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")  # final releases only, no -rc


class SyncError(Exception):
    pass


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
    try:
        known |= {sp["component"] for sp in surfaces_mod.load(repo).values()}
    except surfaces_mod.SurfaceError as e:
        raise SyncError(str(e)) from None
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


def collect_surface(spec, claimed=()):
    """-> (files, info) for one surface."""
    src = GitSource(spec["repo"])
    try:
        branch = spec["branch"]
        src.fetch(branch, [])
        ref = f"refs/remotes/src/{branch}"
        en = src.read_json(ref, spec["english"])
        if not isinstance(en, dict):
            raise SyncError(f"{branch}:{spec['english']} is missing in {spec['repo']}")
        meta = src.read_json(ref, spec["metadata"]) or {}
        if not isinstance(meta, dict):
            raise SyncError(f"{branch}:{spec['metadata']} must be a JSON object")
        ns = spec.get("namespaces")
        catch_all = bool(spec.get("catch_all"))
        union = i18n_map.build_union([i18n_map.Line("main", en, meta)])
        english, out_meta = {}, {}
        for key, e in union.items():
            top = key.split(".")[0]
            if ns is not None and top not in ns:
                continue
            if catch_all and top in claimed:
                continue
            english[key] = e.english
            m = i18n_map.unit_metadata(key, e.english, e.meta, [], e.lines)
            m["lines"] = e.lines
            if (e.meta or {}).get("core") is True:
                m["core"] = True  # ADR-0177 §3: frame text a published page needs
            out_meta[key] = m
        comp = spec["component"]
        if not english:
            # A filter that matches nothing is a wrong namespace, not an empty
            # surface: fail instead of provisioning an empty component.
            raise SyncError(f"surface {comp}: no key of {spec['english']} matches its namespaces "
                            f"({'catch_all' if catch_all else ns or 'all'})")
        info = {"repo": spec["repo"], "branch": branch, "commit": src.commit(ref)}
        return {f"{comp}/en.json": i18n_map.dumps(english), f"meta/{comp}.json": i18n_map.dumps(out_meta)}, info
    finally:
        src.close()


def sync_surface(repo, name, commit=True, url=None, log=print):
    try:
        surfaces = surfaces_mod.load(repo)
    except surfaces_mod.SurfaceError as e:
        raise SyncError(str(e)) from None
    if name not in surfaces:
        raise SyncError(f"unknown surface {name!r}; config/surfaces.json has: {', '.join(sorted(surfaces)) or 'none'}")
    spec = dict(surfaces[name], repo=url) if url else surfaces[name]
    claimed = {n for other, sp in surfaces.items() if other != name
               and (sp["repo"], sp["english"]) == (surfaces[name]["repo"], surfaces[name]["english"])
               for n in sp.get("namespaces", [])}
    files, info = collect_surface(spec, claimed)
    changed = sorted(p for p, text in files.items() if _read(os.path.join(repo, p)) != text)
    if not changed:
        log(f"no change: {name} English and metadata already match {spec['repo']}")
        return False
    src_rel = f"meta/source-{name}.json"
    files[src_rel] = i18n_map.dumps(info)
    for rel in [*changed, src_rel]:
        path = os.path.join(repo, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(files[rel])
    log(f"updated: {', '.join(changed)}")
    if commit:
        _git(["add", "--", *sorted({*changed, src_rel})], cwd=repo)
        msg = (f"sync: {name} English from {info['commit'][:12]}\n\n"
               f"Source: {spec['repo']} ({info['branch']}).\nChanged: {', '.join(changed)}\n")
        _git(["commit", "-q", "-m", msg], cwd=repo)
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    ap.add_argument("--source", help="override config/lines.json `source` (e.g. a local clone)")
    ap.add_argument("--surface", help="sync one surface from config/surfaces.json instead of the till")
    ap.add_argument("--no-commit", action="store_true", help="write the files, don't commit")
    args = ap.parse_args(argv)
    try:
        if args.surface:
            changed = sync_surface(args.repo, args.surface, commit=not args.no_commit,
                                   url=args.source)
        else:
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
