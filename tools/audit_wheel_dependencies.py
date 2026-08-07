#!/usr/bin/env python3
"""Audit native dependencies in repaired pgembed wheels."""

from __future__ import annotations

import argparse
from pathlib import Path
import platform
import subprocess
import tempfile
import zipfile


ELF_MAGIC = b"\x7fELF"
MACHO_MAGICS = {
    b"\xfe\xed\xfa\xce",
    b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf",
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",
    b"\xbe\xba\xfe\xca",
    b"\xca\xfe\xba\xbf",
    b"\xbf\xba\xfe\xca",
}


def _magic(path: Path) -> bytes:
    try:
        with path.open("rb") as handle:
            return handle.read(4)
    except OSError:
        return b""


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False, timeout=30)


def _audit_elf(path: Path) -> None:
    result = _run(["ldd", str(path)])
    output = (result.stdout + result.stderr).strip()
    print(f"\n[{path}]\n{output}")
    if "not a dynamic executable" in output or "statically linked" in output:
        return
    if result.returncode != 0:
        raise RuntimeError(f"ldd failed for {path} with exit code {result.returncode}")
    if "not found" in output:
        raise RuntimeError(f"unresolved dynamic dependency in {path}")


def _audit_macho(path: Path) -> None:
    result = _run(["otool", "-L", str(path)])
    output = (result.stdout + result.stderr).strip()
    print(f"\n[{path}]\n{output}")
    if result.returncode != 0:
        raise RuntimeError(f"otool -L failed for {path} with exit code {result.returncode}")
    for line in result.stdout.splitlines()[1:]:
        dependency = line.strip().split(" ", 1)[0]
        if not dependency or not dependency.startswith("/"):
            continue
        if dependency.startswith(("/usr/lib/", "/System/Library/")):
            continue
        raise RuntimeError(f"non-system absolute dependency in {path}: {dependency}")


def audit_wheel(wheel: Path) -> int:
    count = 0
    with tempfile.TemporaryDirectory(prefix="pgembed-wheel-audit-") as temporary:
        root = Path(temporary)
        with zipfile.ZipFile(wheel) as archive:
            archive.extractall(root)
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            magic = _magic(path)
            if magic == ELF_MAGIC:
                _audit_elf(path)
                count += 1
            elif magic in MACHO_MAGICS:
                _audit_macho(path)
                count += 1
    if count == 0:
        raise RuntimeError(f"wheel contains no ELF or Mach-O payloads: {wheel}")
    return count


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheels", nargs="+", type=Path)
    args = parser.parse_args()
    system = platform.system()
    if system not in {"Darwin", "Linux"}:
        raise SystemExit(f"unsupported audit host: {system}")
    for wheel in args.wheels:
        if not wheel.is_file():
            raise FileNotFoundError(wheel)
        count = audit_wheel(wheel)
        print(f"audited {count} native files in {wheel}")


if __name__ == "__main__":
    main()
