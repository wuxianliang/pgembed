#!/bin/sh
# Resolve a cargo-pgrx matching the pinned version and print its bin directory.
#
# The bundle build used to recompile cargo-pgrx into a build-local root on every
# clean build, even when the machine already had the very same version installed
# under ~/.cargo/bin. Compiling pgrx-plus-its-dependency-tree costs about ten
# minutes, so check the cheap places first:
#
#   1. the build-local root ($2), when it already has the pinned version,
#   2. a cargo-pgrx already on PATH (typically ~/.cargo/bin), so one install per
#      machine is enough,
#   3. cargo install into the build-local root, as the last resort.
#
# Usage: ensure-cargo-pgrx.sh <version> <root>
#
# Prints the directory to prepend to PATH; diagnostics go to stderr so the
# caller can capture the path with a command substitution.
set -eu

want=${1:?usage: ensure-cargo-pgrx.sh <version> <root>}
root=${2:?usage: ensure-cargo-pgrx.sh <version> <root>}

# cargo-pgrx --version prints "cargo-pgrx 0.19.1"; compare the exact token so
# 0.19.1 never matches 0.19.10.
version_of() {
	"$1" --version 2>/dev/null | awk 'NR == 1 { print $2 }'
}

if [ -x "$root/bin/cargo-pgrx" ] && [ "$(version_of "$root/bin/cargo-pgrx")" = "$want" ]; then
	printf '%s\n' "$root/bin"
	exit 0
fi

if command -v cargo-pgrx >/dev/null 2>&1; then
	on_path=$(command -v cargo-pgrx)
	if [ "$(version_of "$on_path")" = "$want" ]; then
		echo "ensure-cargo-pgrx: reusing $on_path ($want)" >&2
		dirname "$on_path"
		exit 0
	fi
fi

echo "ensure-cargo-pgrx: installing cargo-pgrx $want into $root" >&2
cargo install cargo-pgrx --version "$want" --locked --root "$root" >&2
printf '%s\n' "$root/bin"
