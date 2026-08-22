#!/usr/bin/env bash
# Relativize the native install tree after `make install`.
#
# PostgreSQL records absolute build-time paths: install names (macOS) and
# rpaths (Linux) point at the machine that ran configure. Once the tree is
# packed into a wheel, those paths do not exist on the user's machine, so
# client tools (psql, pg_dump, ...) cannot resolve libpq, and delocate
# "externalizes" PostgreSQL's own libraries because their references look
# like outside dependencies.
#
# macOS: rewrite every bundled dylib's install name to @loader_path/<name>
#        and every reference to a bundled dylib to a @loader_path location
#        (same directory, or ../lib from bin/), then re-sign the images.
# Linux: set $ORIGIN-based rpaths so every binary and library resolves its
#        bundled dependencies relative to itself.
#
# Idempotent. Usage: relativize_native_install.sh INSTALL_PREFIX
set -euo pipefail

PREFIX="${1:?usage: relativize_native_install.sh INSTALL_PREFIX}"
PLATFORM="$(uname -s)"

if [ "$PLATFORM" = "Darwin" ]; then
    libdir="$PREFIX/lib"

    # 1) Every bundled dylib gets an @loader_path install name. Real files
    #    only: following the unversioned compat symlinks would stamp the
    #    wrong name into the versioned file.
    while IFS= read -r dylib; do
        base="$(basename "$dylib")"
        install_name_tool -id "@loader_path/$base" "$dylib" 2>/dev/null || true
    done < <(find "$libdir" -type f -name '*.dylib' | sort)

    # 2) Rewrite absolute references that point at bundled dylibs. The
    #    reference's basename (e.g. libpq.5.dylib from a compat symlink)
    #    decides which bundled copy to use; prefer a same-directory match,
    #    else resolve against lib/ (bin/ -> ../lib, lib/postgresql -> ..).
    while IFS= read -r target; do
        dir="$(dirname "$target")"
        while IFS= read -r ref; do
            base="${ref##*/}"
            if [ -f "$dir/$base" ]; then
                new="@loader_path/$base"
            elif [ -f "$libdir/$base" ]; then
                new="@loader_path/$(python3 - "$dir" "$libdir" <<'PY'
import os.path, sys
print(os.path.relpath(sys.argv[2], sys.argv[1]))
PY
)/$base"
            elif [ -f "$libdir/postgresql/$base" ]; then
                new="@loader_path/$(python3 - "$dir" "$libdir/postgresql" <<'PY'
import os.path, sys
print(os.path.relpath(sys.argv[2], sys.argv[1]))
PY
)/$base"
            else
                continue
            fi
            install_name_tool -change "$ref" "$new" "$target" 2>/dev/null || true
        done < <(otool -L "$target" | awk 'NR>1 {print $1}' | grep -E '^/' || true)
        codesign --force --sign - --timestamp=none "$target" 2>/dev/null || true
    done < <(find "$PREFIX/bin" "$libdir" -type f -exec file {} \; 2>/dev/null | grep 'Mach-O' | cut -d: -f1)

    echo "relativized install names under $PREFIX"
elif [ "$PLATFORM" = "Linux" ]; then
    command -v patchelf >/dev/null || { echo "patchelf not found" >&2; exit 1; }
    find "$PREFIX/bin" -type f -exec file {} \; 2>/dev/null | grep 'ELF' | cut -d: -f1 |
        xargs -r -n1 patchelf --set-rpath '$ORIGIN/../lib'
    # lib/: bundled siblings in the same directory (libecpg -> libpgtypes).
    find "$PREFIX/lib" -maxdepth 1 -name '*.so*' | while IFS= read -r so; do
        patchelf --set-rpath '$ORIGIN' "$so"
    done
    # lib/postgresql/: extensions resolve ICU (same directory) and libpq
    # (one level up), so they need both origins.
    find "$PREFIX/lib/postgresql" -name '*.so*' | while IFS= read -r so; do
        patchelf --set-rpath '$ORIGIN:$ORIGIN/..' "$so"
    done
    echo "relativized rpaths under $PREFIX"
else
    echo "unsupported platform: $PLATFORM" >&2
    exit 1
fi
