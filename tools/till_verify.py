#!/usr/bin/env python3
"""Run the till's own verifier and locale loader against a built bundle
(ADR-0160 §5; ut-docs#4367).

Copies tools/till_harness/utt_bundle_harness_test.go into a throwaway copy
of universal-till (git archive of --commit, so a local checkout is never
modified) and runs it with `go test`. The harness signs the bundle the way
the marketplace does, installs it through the till's real marketplace
install path, loads it through the real locale loader, and checks that
tampered copies are refused. See the harness file for why it is a copy-in.

Usage:
  python3 tools/till_verify.py --till ../universal-till --commit <sha> \
      --bundle dist/com.universaltill.language-de_2.x_universal.tar.gz --locale de
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.join(HERE, "till_harness", "utt_bundle_harness_test.go")
HARNESS_DEST = os.path.join("internal", "plugins", "zz_utt_bundle_harness_test.go")


MAIN_REF = "refs/remotes/origin/main"
RELEASE_TAG_RE = re.compile(r"v\d+\.\d+\.\d+")


class VerifyError(Exception):
    pass


def _git(till, *args):
    return subprocess.run(["git", "-C", till, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def require_released_commit(till, commit, main_ref=MAIN_REF):
    """-> the full sha of `commit`, when it is on universal-till main
    (an ancestor of `main_ref`, which `git fetch origin main` sets) or is a
    final release tag's commit (vX.Y.Z). Raises VerifyError otherwise.

    GitHub serves a fork's commit by sha through the parent repository, so a
    sha alone says nothing about who wrote it; this runs before any of the
    till's code (its guards, its Go tests) does."""
    r = _git(till, "rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}")
    sha = r.stdout.decode().strip()
    if r.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise VerifyError(f"{commit} is not a commit in {till}")
    if _git(till, "rev-parse", "--verify", "--quiet", main_ref).returncode != 0:
        raise VerifyError(f"{till} has no {main_ref}: run `git fetch origin main --tags` there first "
                          "(fails closed: the commit can't be shown to be on main)")
    r = _git(till, "merge-base", "--is-ancestor", sha, main_ref)
    if r.returncode == 0:
        return sha
    if r.returncode != 1:
        raise VerifyError(f"git merge-base {sha} {main_ref}: {r.stderr.decode(errors='replace').strip()}")
    tags = _git(till, "tag", "--points-at", sha).stdout.decode().split()
    if any(RELEASE_TAG_RE.fullmatch(t) for t in tags):
        return sha
    raise VerifyError(f"{sha} is not on universal-till main ({main_ref}) and no vX.Y.Z release tag points "
                      "at it: a fork's commit is fetchable by sha, so its code is never run")


def export_tree(till, commit, dest):
    """git archive <commit> of the till checkout into dest."""
    tar_path = os.path.join(dest, "src.tar")
    with open(tar_path, "wb") as out:
        r = subprocess.run(["git", "-C", till, "archive", "--format=tar", commit],
                           stdout=out, stderr=subprocess.PIPE)
    if r.returncode != 0:
        raise VerifyError(f"git archive {commit} in {till}: {r.stderr.decode(errors='replace').strip()}")
    root = os.path.join(dest, "universal-till")
    os.makedirs(root)
    with tarfile.open(tar_path) as tf:
        tf.extractall(root, filter="data")
    os.remove(tar_path)
    return root


def verify(till, commit, bundle, locale, timeout=900):
    """-> (ok, output). Raises VerifyError when the harness can't run."""
    commit = require_released_commit(till, commit)
    if shutil.which("go") is None:
        raise VerifyError("go is not on PATH")
    bundle = os.path.abspath(bundle)
    if not os.path.isfile(bundle):
        raise VerifyError(f"no bundle at {bundle}")
    with tempfile.TemporaryDirectory(prefix="utt-till-") as tmp:
        root = export_tree(till, commit, tmp)
        if not os.path.isdir(os.path.join(root, "internal", "plugins")):
            raise VerifyError(f"{commit} has no internal/plugins: not a universal-till tree")
        shutil.copyfile(HARNESS, os.path.join(root, HARNESS_DEST))
        env = dict(os.environ, UTT_BUNDLE=bundle, UTT_LOCALE=locale)
        r = subprocess.run(
            ["go", "test", "-count=1", "-run", "^TestUTTBundle", "-v", "./internal/plugins/"],
            cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
        out = r.stdout.decode(errors="replace")
        ran = "--- PASS: TestUTTBundleTillVerifier" in out or "--- FAIL: TestUTTBundleTillVerifier" in out
        if not ran and r.returncode != 0:
            raise VerifyError("the harness did not run (build failure?):\n" + out[-4000:])
        if not ran:
            raise VerifyError("go test matched no harness test:\n" + out[-2000:])
        return r.returncode == 0, out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--till", required=True, help="a universal-till git checkout")
    ap.add_argument("--commit", default="HEAD", help="the commit to verify against (the bundle's source_commit)")
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--locale", required=True)
    args = ap.parse_args(argv)
    try:
        ok, out = verify(args.till, args.commit, args.bundle, args.locale)
    except (VerifyError, OSError, subprocess.TimeoutExpired) as e:
        print(f"till verify failed to run: {e}", file=sys.stderr)
        return 2
    print(out)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
