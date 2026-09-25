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
        ("pgembed_stannum", "stannum.dylib", "stannum.control"),
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


def test_standalone_discovery_when_metadata_marks_extension_not_built(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bundle that never built stannum must still discover the companion package.

    Phase-2 acceptance: bundle metadata says the extension was neither built
    nor skipped, no stale bundled artifacts exist, and the installed
    pgembed_stannum package provides a compatible artifact — so
    has_extension() must report the extension and get_extension_path() must
    return the package-local library.
    """
    import pgembed

    assert pgembed.BUNDLED_PG_MAJOR == 18  # premise: the attested major

    package_dir = tmp_path / "pgembed_stannum"
    package_dir.mkdir()
    library = package_dir / "stannum.dylib"
    library.write_bytes(b"standalone library bytes")
    fake_package = types.SimpleNamespace(
        BUILT_FOR_POSTGRES_MAJOR=pgembed.BUNDLED_PG_MAJOR,
        get_extension_path=lambda: library,
    )
    monkeypatch.setitem(sys.modules, "pgembed_stannum", fake_package)
    # _detect_extensions loads the metadata itself; patch the loader.
    not_built = types.SimpleNamespace(built=False, skipped=False)
    metadata = types.SimpleNamespace(extensions={"stannum": not_built})
    monkeypatch.setattr(pgembed, "load_bundle_metadata", lambda: metadata)
    # Empty artifact roots: a real bundled prefix must not shadow the point
    # of the test (metadata says not built, and no stale artifacts exist).
    empty_share = tmp_path / "share"
    empty_share.mkdir()
    empty_lib = tmp_path / "lib"
    empty_lib.mkdir()
    monkeypatch.setattr(pgembed, "EXTENSION_SHARE_PATH", empty_share)
    monkeypatch.setattr(pgembed, "EXTENSION_POSTGRES_LIB_PATH", empty_lib)

    available_before = pgembed.AVAILABLE_EXTENSIONS.copy()
    paths_before = pgembed._EXTENSION_PATHS.copy()
    try:
        pgembed._detect_extensions()
        assert pgembed.has_extension("stannum") is True
        assert pgembed.get_extension_path("stannum") == library
    finally:
        # _detect_extensions mutates module state; restore it for other tests.
        pgembed.AVAILABLE_EXTENSIONS.clear()
        pgembed.AVAILABLE_EXTENSIONS.update(available_before)
        pgembed._EXTENSION_PATHS.clear()
        pgembed._EXTENSION_PATHS.update(paths_before)
