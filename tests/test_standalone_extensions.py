from __future__ import annotations

import importlib
from pathlib import Path
import sys
import types

import pytest


@pytest.mark.parametrize(
    ("module_name", "library_name", "control_name"),
    [
        ("pgembed_pgvector", "vector.dylib", "pgvector.control"),
    ],
)
def test_standalone_helpers_never_fall_back_to_bundled_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    library_name: str,
    control_name: str,
) -> None:
    module = importlib.import_module(module_name)
    package_dir = tmp_path / module_name
    package_dir.mkdir()
    monkeypatch.setattr(module, "__file__", str(package_dir / "__init__.py"))

    bundled_lib = tmp_path / "bundled" / "lib"
    bundled_share = tmp_path / "bundled" / "share" / "postgresql" / "extension"
    bundled_lib.mkdir(parents=True)
    bundled_share.mkdir(parents=True)
    (bundled_lib / library_name).write_bytes(b"stale bundled library")
    (bundled_share / control_name).write_text("default_version = 'fixture'\n")
    fake_pgembed = types.SimpleNamespace(
        EXTENSION_LIB_PATH=bundled_lib,
        POSTGRES_INSTALL_PATH=tmp_path / "bundled",
    )
    monkeypatch.setitem(sys.modules, "pgembed", fake_pgembed)

    assert module.BUILT_FOR_POSTGRES_MAJOR == 18
    assert module.get_extension_path() is None
    assert module.get_extension_share_path() is None
