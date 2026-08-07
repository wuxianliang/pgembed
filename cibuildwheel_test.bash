#!/bin/bash
set -euo pipefail

PROJECT=${1:?"project path is required"}

echo "Running installed-wheel smoke on OSTYPE=$OSTYPE with UID=$UID"

# Gate 2 is intentionally limited to installed-wheel binary/metadata/API smoke
# for every CPython wheel. Native lifecycle/extension integration and the real
# TigerFS mount run later on dedicated runners with the cp312 wheel.
pytest -s -v --log-cli-level=INFO "$PROJECT/tests/test_bundled_tools.py"
