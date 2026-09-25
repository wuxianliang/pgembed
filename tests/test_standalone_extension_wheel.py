"""Standalone pgembed-stannum wheel: staging layout, build, and installation.

The staging helpers must copy exactly the artifact set the package's path
helpers look for — the library at the package root, the control and SQL
files under ``pginstall/share/postgresql/extension`` — and never write into
the source tree, because the base wheel's include glob would sweep staged
binaries into every pgembed wheel.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

from tools import build_standalone_extension_wheel as builder

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "src" / "pgembed_stannum"
REAL_PREFIX = REPO_ROOT / "src" / "pgembed" / "pginstall"
STANNUM = builder.EXTENSIONS["stannum"]
REAL_LIBRARY = builder.discover_library(REAL_PREFIX, STANNUM)


def _fixture_prefix(tmp_path: Path) -> Path:
    prefix = tmp_path / "prefix"
    lib_dir = prefix / "lib" / "postgresql"
    lib_dir.mkdir(parents=True)
    (lib_dir / "stannum.dylib").write_bytes(b"fixture library bytes")
    share = prefix / "share" / "postgresql" / "extension"
    share.mkdir(parents=True)
    (share / "stannum.control").write_text("default_version = '9.9.9'\n")
    (share / "stannum--9.9.9.sql").write_text("-- base\n")
    (share / "stannum--0.1.0--9.9.9.sql").write_text("-- update\n")
    (share / "neighbor.control").write_text("not stannum\n")
    return prefix


def test_staging_copies_exactly_the_artifact_set_outside_the_source_tree(
    tmp_path: Path,
) -> None:
    prefix = _fixture_prefix(tmp_path)
    stage = tmp_path / "stage"
    builder.stage_skeleton(stage, STANNUM)
    copied = builder.stage_artifacts(stage, prefix, STANNUM)

    package = stage / "src" / "pgembed_stannum"
    assert (package / "stannum.dylib").read_bytes() == b"fixture library bytes"
    extension_dir = package / "pginstall" / "share" / "postgresql" / "extension"
    assert sorted(path.name for path in extension_dir.iterdir()) == [
        "stannum--0.1.0--9.9.9.sql",
        "stannum--9.9.9.sql",
        "stannum.control",
    ]
    assert (stage / "pyproject.toml").read_bytes() == (
        PACKAGE_SRC / "pyproject.toml"
    ).read_bytes()
    assert (stage / "README.md").is_file()
    assert (package / "__init__.py").is_file()
    # The library plus control + two SQL files; nothing else is copied.
    assert len(copied) == 4
    # The source tree stays artifact-free: the root pyproject's include glob
    # must never sweep staged binaries into a base wheel.
    assert not (PACKAGE_SRC / "stannum.dylib").exists()
    assert not (PACKAGE_SRC / "pginstall").exists()


def test_missing_prefix_fails_with_a_bundle_build_hint(tmp_path: Path) -> None:
    stage = tmp_path / "stage"
    builder.stage_skeleton(stage, STANNUM)
    with pytest.raises(SystemExit, match="build the bundle first"):
        builder.stage_artifacts(stage, tmp_path / "empty-prefix", STANNUM)


def test_stage_artifacts_only_requires_an_existing_staged_project(
    tmp_path: Path,
) -> None:
    with pytest.raises(SystemExit, match="not a staged"):
        builder.stage_artifacts(tmp_path / "missing-stage", _fixture_prefix(tmp_path), STANNUM)


def test_built_wheel_carries_the_staged_artifacts_and_helpers_find_them(
    tmp_path: Path,
) -> None:
    prefix = _fixture_prefix(tmp_path)
    stage = tmp_path / "stage"
    builder.stage_skeleton(stage, STANNUM)
    builder.stage_artifacts(stage, prefix, STANNUM)
    tag = builder.default_platform_tag(builder.discover_library(prefix, STANNUM))
    wheel = builder.build_wheel(stage, tmp_path / "out", tag)
    assert wheel.name.endswith(f"-{tag}.whl")

    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        assert "pgembed_stannum/stannum.dylib" in names
        assert (
            "pgembed_stannum/pginstall/share/postgresql/extension/stannum.control"
            in names
        )
        assert (
            "pgembed_stannum/pginstall/share/postgresql/extension/stannum--9.9.9.sql"
            in names
        )
        wheel_metadata = archive.read(
            "pgembed_stannum-0.3.0rc2.dist-info/WHEEL"
        ).decode()
    assert f"Tag: py3-none-{tag}" in wheel_metadata

    # Installing the wheel into a target directory must make the fail-closed
    # helpers resolve the package-local artifacts, exactly as on a real
    # standalone install (source-tree imports keep returning None).
    site = tmp_path / "site"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--quiet",
            "--no-deps",
            "--target",
            str(site),
            str(wheel),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    probe = (
        "import pgembed_stannum as m;"
        " lib = m.get_extension_path();"
        " share = m.get_extension_share_path();"
        " assert lib is not None and lib.name == 'stannum.dylib', lib;"
        " assert share is not None and (share / 'stannum.control').is_file();"
        " assert m.BUILT_FOR_POSTGRES_MAJOR == 18;"
        " print(m.__version__)"
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(site), environment.get("PYTHONPATH", "")) if part
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert result.stdout.strip() == "0.3.0rc2"


@pytest.mark.skipif(
    REAL_LIBRARY is None, reason="stannum is not built in this checkout"
)
def test_real_bundle_wheel_matches_the_built_prefix(tmp_path: Path) -> None:
    stage = tmp_path / "stage"
    builder.stage_skeleton(stage, STANNUM)
    copied = builder.stage_artifacts(stage, REAL_PREFIX, STANNUM)
    assert len(copied) >= 3  # the library, the control file, and at least one SQL
    wheel = builder.build_wheel(
        stage, tmp_path / "out", builder.default_platform_tag(REAL_LIBRARY)
    )

    share = REAL_PREFIX / "share" / "postgresql" / "extension"
    prefix_control = (share / "stannum.control").read_text()
    assert "default_version" in prefix_control
    prefix_sqls = sorted(path.name for path in share.glob("stannum--*.sql"))
    assert prefix_sqls  # a release prefix always ships base + update SQL
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        assert f"pgembed_stannum/{REAL_LIBRARY.name}" in names
        assert (
            archive.read("pgembed_stannum/pginstall/share/postgresql/extension/stannum.control").decode()
            == prefix_control
        )
        for name in prefix_sqls:
            assert (
                f"pgembed_stannum/pginstall/share/postgresql/extension/{name}" in names
            )
        digest = hashlib.sha256(
            archive.read(f"pgembed_stannum/{REAL_LIBRARY.name}")
        ).hexdigest()
    assert digest == hashlib.sha256(REAL_LIBRARY.read_bytes()).hexdigest()
