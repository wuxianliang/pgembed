from pathlib import Path

import pytest

import pgembed
import pgembed._commands as commands


@pytest.mark.parametrize(
    ("executable_name", "expected"),
    [
        ("pg_restore", "pg_restore"),
        ("pg_restore.exe", "pg_restore"),
        ("pg_restore.EXE", "pg_restore"),
        ("tigerfs.exe", "tigerfs"),
        ("service", "service"),
        ("toolx", "toolx"),
        ("name.", "name."),
    ],
)
def test_normalize_executable_name(executable_name: str, expected: str):
    assert commands._normalize_executable_name(executable_name) == expected


def test_command_exports_exclude_tigerfs_and_keep_postgres_wrappers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    executable_names = (
        "tigerfs",
        "tigerfs.exe",
        "pg_restore.exe",
        "psql",
        "createdb",
        "vacuumdb",
        "service",
        "toolx",
        "name.",
    )
    for executable_name in executable_names:
        (tmp_path / executable_name).touch()

    generated_names = {
        commands._normalize_executable_name(name) for name in executable_names
    }
    original_all = list(commands.__all__)
    original_attributes = {
        name: getattr(commands, name)
        for name in generated_names
        if hasattr(commands, name)
    }

    monkeypatch.setattr(commands, "POSTGRES_BIN_PATH", tmp_path)
    monkeypatch.setattr(commands, "_postgres_binaries_available", True)

    try:
        commands.__all__.clear()
        for name in generated_names:
            if hasattr(commands, name):
                delattr(commands, name)

        commands._init()

        assert "tigerfs" not in commands.__all__
        assert not hasattr(commands, "tigerfs")

        expected_wrappers = {
            "pg_restore",
            "psql",
            "createdb",
            "vacuumdb",
            "service",
            "toolx",
            "name.",
        }
        assert expected_wrappers <= set(commands.__all__)
        for wrapper_name in expected_wrappers:
            assert callable(getattr(commands, wrapper_name))
    finally:
        commands.__all__[:] = original_all
        for name in generated_names:
            if name in original_attributes:
                setattr(commands, name, original_attributes[name])
            elif hasattr(commands, name):
                delattr(commands, name)


def test_postgres_bin_path_remains_public_without_tigerfs_wrapper():
    assert pgembed.POSTGRES_BIN_PATH == commands.POSTGRES_BIN_PATH
    assert not hasattr(pgembed, "tigerfs")
