from __future__ import annotations

import subprocess

import pytest

from tools import audit_wheel_dependencies as audit


def _completed(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["dependency-tool"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


@pytest.mark.parametrize("message", ["not a dynamic executable", "statically linked"])
def test_elf_audit_allows_non_dynamic_payloads(monkeypatch, tmp_path, message):
    payload = tmp_path / "payload"
    monkeypatch.setattr(audit, "_run", lambda command: _completed(1, stderr=message))

    audit._audit_elf(payload)


def test_elf_audit_rejects_unresolved_dependency(monkeypatch, tmp_path):
    payload = tmp_path / "payload.so"
    monkeypatch.setattr(
        audit,
        "_run",
        lambda command: _completed(
            0,
            stdout="libmissing.so => not found\nlibc.so.6 => /lib64/libc.so.6",
        ),
    )

    with pytest.raises(RuntimeError, match="unresolved dynamic dependency"):
        audit._audit_elf(payload)


def test_macho_audit_allows_loader_and_system_dependencies(monkeypatch, tmp_path):
    payload = tmp_path / "payload.dylib"
    monkeypatch.setattr(
        audit,
        "_run",
        lambda command: _completed(
            0,
            stdout=(
                f"{payload}:\n"
                "\t@rpath/libpgembed.dylib (compatibility version 1.0.0, current version 1.0.0)\n"
                "\t@loader_path/libhelper.dylib (compatibility version 1.0.0, current version 1.0.0)\n"
                "\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0, current version 1.0.0)\n"
                "\t/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation "
                "(compatibility version 150.0.0, current version 3500.0.0)\n"
            ),
        ),
    )

    audit._audit_macho(payload)


def test_macho_audit_rejects_homebrew_dependency(monkeypatch, tmp_path):
    payload = tmp_path / "payload.dylib"
    monkeypatch.setattr(
        audit,
        "_run",
        lambda command: _completed(
            0,
            stdout=(
                f"{payload}:\n"
                "\t/opt/homebrew/opt/icu4c@78/lib/libicui18n.78.dylib "
                "(compatibility version 78.0.0, current version 78.1.0)\n"
            ),
        ),
    )

    with pytest.raises(RuntimeError, match="non-system absolute dependency"):
        audit._audit_macho(payload)


def test_macho_audit_skips_install_name_entry(monkeypatch, tmp_path):
    payload = tmp_path / "payload.dylib"
    monkeypatch.setattr(
        audit,
        "_run",
        lambda command: (
            _completed(0, stdout=f"{payload}:\n/tmp/build/.dylibs/payload.dylib\n")
            if command[1] == "-D"
            else _completed(
                0,
                stdout=(
                    f"{payload}:\n"
                    "\t/tmp/build/.dylibs/payload.dylib (compatibility version 6.0.0)\n"
                    "\t@loader_path/libhelper.dylib (compatibility version 1.0.0)\n"
                    "\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0)\n"
                ),
            )
        ),
    )

    audit._audit_macho(payload)
