#!/usr/bin/env python3
"""Extract the build metadata and bundle stamp from a built wheel.

The wheel packages the whole pginstall tree, and `make bundle-metadata`
stages both attestation files into pginstall/share/pgembed/ precisely so
they survive the cibuildwheel container boundary. This script pulls them
back out next to the wheels, where the release-evidence and
bundle-attestation workflow steps expect them.
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

PAYLOAD_NAMES = (
    "pgembed/pginstall/share/pgembed/build-metadata.json",
    "pgembed/pginstall/share/pgembed/postgres-bundle-config.stamp",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel_dir", type=Path, help="directory containing the built wheels")
    args = parser.parse_args()

    wheels = sorted(args.wheel_dir.glob("*.whl"))
    if not wheels:
        print(f"no wheels found in {args.wheel_dir}", file=sys.stderr)
        return 1

    with zipfile.ZipFile(wheels[0]) as archive:
        names = set(archive.namelist())
        for payload in PAYLOAD_NAMES:
            if payload not in names:
                print(f"{wheels[0].name} does not contain {payload}", file=sys.stderr)
                return 1
            destination = args.wheel_dir / Path(payload).name
            destination.write_bytes(archive.read(payload))
            print(f"extracted {payload} -> {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
