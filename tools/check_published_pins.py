#!/usr/bin/env python3
# Copyright (C) 2026 Xianliang Wu
#
# See LICENSE in the repository root for license terms.

"""Check that every git-pinned source in pgbuild/Makefile is published.

A pin is only a candidate identity if the commit exists on the remote the
recipe fetches from. It is easy to point one at a commit that is still local —
the bundle build then fails minutes into a platform's build with
`upload-pack: not our ref`, after the parts that could have been checked in
seconds have already been paid for.

    python3 tools/check_published_pins.py

Exits non-zero and names every unpublished pin, or prints one line per pin and
exits zero. Runs offline-safe: it only contacts the remotes the Makefile names,
and refuses to guess a remote.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAKEFILE = ROOT / "pgbuild" / "Makefile"

# The PostgreSQL checkout is a branch clone whose tip is then asserted against
# POSTGRES_SOURCE_COMMIT, so its reachability is a separate shape.
POSTGRES_REPO = "https://github.com/postgres/postgres"


def makefile_variables() -> dict[str, str]:
    """`NAME := value` assignments, comments stripped, recipe lines ignored."""
    pins: dict[str, str] = {}
    for line in MAKEFILE.read_text(encoding="utf-8").splitlines():
        stripped = line.split("#", 1)[0].rstrip()
        if ":=" not in stripped or stripped.startswith("\t"):
            continue
        key, _, value = stripped.partition(":=")
        key, value = key.strip(), value.strip()
        if key and not value.startswith("$("):
            pins[key] = value
    return pins


def git_pins(variables: dict[str, str]) -> list[tuple[str, str, str]]:
    """(component, repository, commit) for every component pinned by commit."""
    pins = []
    for name in sorted({key[: -len("_REPO")] for key in variables if key.endswith("_REPO")}):
        commit = variables.get(f"{name}_COMMIT")
        repo = variables[f"{name}_REPO"]
        if commit and re.fullmatch(r"[0-9a-f]{40}", commit):
            pins.append((name, repo, commit))
    return pins


def reachable(repo: str, ref: str) -> subprocess.CompletedProcess[str]:
    """Dry-run fetch: contacts the server, downloads nothing."""
    with tempfile.TemporaryDirectory(prefix="pin-check-") as directory:
        subprocess.run(
            ["git", "init", "--quiet", "."],
            cwd=directory,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "remote", "add", "origin", repo],
            cwd=directory,
            check=True,
            capture_output=True,
            text=True,
        )
        return subprocess.run(
            ["git", "fetch", "--quiet", "--dry-run", "origin", ref],
            cwd=directory,
            capture_output=True,
            text=True,
            timeout=180,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()

    variables = makefile_variables()
    checks: list[tuple[str, str, str]] = git_pins(variables)
    checks.append(
        (
            "POSTGRES",
            POSTGRES_REPO,
            variables.get("POSTGRES_SOURCE_COMMIT", ""),
        )
    )

    unpublished = []
    for name, repo, commit in checks:
        if not commit:
            print(f"{name}: NOT PINNED — no commit in the Makefile")
            unpublished.append(name)
            continue
        result = reachable(repo, commit)
        if result.returncode == 0:
            print(f"{name}: {commit[:12]} published in {repo}")
        else:
            detail = (result.stderr or result.stdout).strip().splitlines()
            reason = detail[-1] if detail else f"exit {result.returncode}"
            print(f"{name}: {commit[:12]} NOT published in {repo} — {reason}")
            unpublished.append(name)

    if unpublished:
        print(
            f"\n{len(unpublished)} unpublished pin(s): {', '.join(unpublished)}.\n"
            "A pin must name a commit the recipe can already fetch; push it, or\n"
            "point the Makefile at a commit that is already on the remote."
        )
        return 1
    print(f"\nAll {len(checks)} git pins are published.")
    return 0


if __name__ == "__main__":
    if shutil.which("git") is None:
        sys.exit("git is required")
    sys.exit(main())
