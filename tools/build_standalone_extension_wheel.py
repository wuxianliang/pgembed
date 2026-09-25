#!/usr/bin/env python3
# Copyright (C) 2026 Ben Weis <ben@springbird.app>
# Based on Lead, copyright (C) 2026 PlanetScale
#
# See LICENSE in the repository root for license terms.

"""Stage and build a standalone pgembed-<extension> wheel (packaging phase 2).

The base pgembed wheel bundles the extension's Python package through the
root pyproject's ``pgembed*`` include glob, but the extension's native
artifacts live in the shared bundled PostgreSQL prefix. This tool builds the
companion wheel that owns its extension's artifacts: the library lands at
the staged package root (where ``get_extension_path`` looks) and the control
+ SQL files under the staged package's
``pginstall/share/postgresql/extension`` (where ``get_extension_share_path``
looks), so the wheel's helpers stay fail-closed on every other machine.

Staging always targets an isolated directory outside the source tree: the
root pyproject's include glob would otherwise sweep staged binaries into
every base wheel. Modes:

* default - stage into ``--stage-dir`` and build the wheel;
* ``--stage-only`` - stage and stop (CI needs the complete project on disk);
* ``--skeleton-only`` - stage only the Python project, no artifacts (used
  on CI hosts that have no built prefix yet);
* ``--stage-artifacts-only`` - copy only the artifacts into an existing
  staged project (used inside a manylinux container after its prefix build).
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Extension:
    """One extension's standalone-wheel identity."""

    name: str  # The pgembed extension name, e.g. "stannum".
    module: str  # The Python package directory, e.g. "pgembed_stannum".


EXTENSIONS = {
    "stannum": Extension(name="stannum", module="pgembed_stannum"),
}

LIBRARY_SUFFIXES = (".dylib", ".so", ".dll")


def library_candidates(prefix: Path, extension: Extension) -> list[Path]:
    lib_dir = prefix / "lib" / "postgresql"
    return [lib_dir / f"{extension.name}{suffix}" for suffix in LIBRARY_SUFFIXES]


def discover_library(prefix: Path, extension: Extension):
    for candidate in library_candidates(prefix, extension):
        if candidate.is_file():
            return candidate
    return None


def discover_share_files(prefix: Path, extension: Extension) -> list[Path]:
    share = prefix / "share" / "postgresql" / "extension"
    control = share / f"{extension.name}.control"
    if not control.is_file():
        return []
    return [control, *sorted(share.glob(f"{extension.name}--*.sql"))]


def stage_skeleton(stage_dir: Path, extension: Extension) -> list[Path]:
    """Copy the Python project (no artifacts) into the staged layout.

    The staged layout mirrors other standalone packages: ``pyproject.toml``
    and ``README.md`` at the root with the package under ``src/``.
    """
    package_src = REPO_ROOT / "src" / extension.module
    package_dst = stage_dir / "src" / extension.module
    package_dst.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for name in ("pyproject.toml", "README.md"):
        shutil.copy2(package_src / name, stage_dir / name)
        copied.append(stage_dir / name)
    for source in sorted(package_src.glob("*.py")):
        shutil.copy2(source, package_dst / source.name)
        copied.append(package_dst / source.name)
    return copied


def stage_artifacts(stage_dir: Path, prefix: Path, extension: Extension) -> list[Path]:
    """Copy the library and control/SQL files into the staged project."""
    library = discover_library(prefix, extension)
    share_files = discover_share_files(prefix, extension)
    if library is None or not share_files:
        expected = ", ".join(
            str(path) for path in library_candidates(prefix, extension)
        )
        raise SystemExit(
            f"no built {extension.name} artifacts under {prefix} (looked for"
            f" {expected} and share/postgresql/extension/{extension.name}.control);"
            " build the bundle first (make -C pgbuild all, or make build from"
            " the repository root)"
        )
    package_dst = stage_dir / "src" / extension.module
    if not (stage_dir / "pyproject.toml").is_file() or not package_dst.is_dir():
        raise SystemExit(
            f"{stage_dir} is not a staged {extension.module} project; run"
            " without --stage-artifacts-only first"
        )
    copied = [package_dst / library.name]
    shutil.copy2(library, copied[0])
    share_dst = package_dst / "pginstall" / "share" / "postgresql" / "extension"
    share_dst.mkdir(parents=True, exist_ok=True)
    for source in share_files:
        shutil.copy2(source, share_dst / source.name)
        copied.append(share_dst / source.name)
    return copied


def macho_min_os(library: Path):
    """The library's macOS minimum OS from vtool, e.g. "26.0"."""
    try:
        result = subprocess.run(
            ["vtool", "-show-build", str(library)],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) == 2 and fields[0] == "minos":
            return fields[1].strip()
    return None


def default_platform_tag(library: Path) -> str:
    system = platform.system()
    machine = platform.machine()
    if system == "Darwin":
        # pip only accepts major-versioned macOS tags (macosx_{major}_0_{arch}
        # for 11+), so the minor from vtool/mac_ver maps down to the major.
        minimum = macho_min_os(library) or platform.mac_ver()[0] or "0.0"
        major = minimum.split(".")[0]
        return f"macosx_{major}_0_{machine}"
    if system == "Linux":
        # A host-built Linux wheel is not manylinux-claimable; the CI path
        # builds inside the manylinux container and auditwheel tags it.
        return f"linux_{machine}"
    raise SystemExit(f"unsupported build host {system}")


def build_wheel(stage_dir: Path, output_dir: Path, platform_tag: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="pgembed-standalone-wheel-"))
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "wheel",
                str(stage_dir),
                "--no-deps",
                "--wheel-dir",
                str(scratch),
                f"--config-settings=--build-option=--plat-name={platform_tag}",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise SystemExit(f"pip wheel failed:\n{completed.stdout}{completed.stderr}")
        wheels = sorted(scratch.glob("*.whl"))
        if len(wheels) != 1:
            raise SystemExit(f"expected exactly one wheel, found {wheels}")
        destination = output_dir / wheels[0].name
        shutil.move(str(wheels[0]), destination)
        return destination
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--extension",
        choices=sorted(EXTENSIONS),
        default="stannum",
        help="which extension's standalone wheel to build",
    )
    parser.add_argument(
        "--install-prefix",
        type=Path,
        default=REPO_ROOT / "src" / "pgembed" / "pginstall",
        help="the built bundle prefix providing the extension artifacts",
    )
    parser.add_argument(
        "--stage-dir",
        type=Path,
        default=None,
        help="staging directory outside the source tree"
        " (default: a fresh temporary directory)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "dist-standalone",
        help="where the built wheel is written",
    )
    parser.add_argument(
        "--platform-tag",
        default=None,
        help="wheel platform tag (default: derived from the built library)",
    )
    parser.add_argument(
        "--stage-only",
        action="store_true",
        help="stage the complete project and stop",
    )
    parser.add_argument(
        "--skeleton-only",
        action="store_true",
        help="stage only the Python project (no native artifacts) and stop",
    )
    parser.add_argument(
        "--stage-artifacts-only",
        action="store_true",
        help="copy only the artifacts into an existing staged project",
    )
    args = parser.parse_args()
    extension = EXTENSIONS[args.extension]
    prefix = args.install_prefix.resolve()
    stage_dir = (
        args.stage_dir.resolve()
        if args.stage_dir is not None
        else Path(tempfile.mkdtemp(prefix=f"pgembed-{extension.name}-wheel-"))
    )

    if args.stage_artifacts_only:
        if args.stage_dir is None:
            parser.error("--stage-artifacts-only requires --stage-dir")
        copied = stage_artifacts(stage_dir, prefix, extension)
        print(f"staged {len(copied)} artifacts into {stage_dir}")
        return

    print(f"staging {extension.module} at {stage_dir}")
    stage_skeleton(stage_dir, extension)
    if args.skeleton_only:
        return
    copied = stage_artifacts(stage_dir, prefix, extension)
    print(f"staged {len(copied)} artifacts")
    if args.stage_only:
        return

    library = discover_library(prefix, extension)
    platform_tag = args.platform_tag or default_platform_tag(library)
    wheel = build_wheel(stage_dir, args.output_dir.resolve(), platform_tag)
    print(f"built {wheel} (tag py3-none-{platform_tag})")


if __name__ == "__main__":
    main()
