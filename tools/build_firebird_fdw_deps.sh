#!/usr/bin/env bash
# Stage a private Firebird client + libfq for firebird_fdw.
#
# firebird_fdw links -lfq -lfbclient. Host Firebird installs are not
# relocatable into a wheel, so this script copies a verified official
# Firebird client archive (same approach as TigerFS) and builds libfq
# against that copy. Runtime files land in:
#   $INSTALL_PREFIX/lib              libfbclient, libfq, libtommath, ...
#   $INSTALL_PREFIX/share/firebird   FIREBIRD root (msg, plugins, tzdata)
#   $DEPS_PREFIX/include             ibase.h + libfq.h for the PGXS build
#
# Linux official tarballs omit libtommath.so.1 (DT_NEEDED); pass
# --tommath-src to build a private shared copy. macOS packages already
# ship libtommath.dylib.
#
# Does not install Firebird server binaries into pginstall/bin.
#
# Usage:
#   build_firebird_fdw_deps.sh --firebird-archive FILE --libfq-src DIR \
#     --deps-prefix DIR --install-prefix DIR [--tommath-src DIR] [--jobs N]
set -euo pipefail

FIREBIRD_ARCHIVE=""
LIBFQ_SRC=""
TOMMATH_SRC=""
DEPS_PREFIX=""
INSTALL_PREFIX=""
JOBS="${BUILD_JOBS:-$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)}"
HOST_OS="$(uname -s)"

while [ "$#" -gt 0 ]; do
    case "$1" in
        --firebird-archive) FIREBIRD_ARCHIVE=$2; shift 2 ;;
        --libfq-src) LIBFQ_SRC=$2; shift 2 ;;
        --tommath-src) TOMMATH_SRC=$2; shift 2 ;;
        --deps-prefix) DEPS_PREFIX=$2; shift 2 ;;
        --install-prefix) INSTALL_PREFIX=$2; shift 2 ;;
        --jobs) JOBS=$2; shift 2 ;;
        *) echo "unknown argument: $1" >&2; exit 1 ;;
    esac
done

if [ -z "$FIREBIRD_ARCHIVE" ] || [ -z "$LIBFQ_SRC" ] || [ -z "$DEPS_PREFIX" ] || [ -z "$INSTALL_PREFIX" ]; then
    echo "usage: $0 --firebird-archive FILE --libfq-src DIR --deps-prefix DIR --install-prefix DIR [--tommath-src DIR] [--jobs N]" >&2
    exit 1
fi
for path in "$FIREBIRD_ARCHIVE" "$LIBFQ_SRC"; do
    if [ ! -e "$path" ]; then
        echo "missing input: $path" >&2
        exit 1
    fi
done
if [ ! -x "$LIBFQ_SRC/configure" ] && [ ! -f "$LIBFQ_SRC/configure" ]; then
    echo "libfq source is missing ./configure: $LIBFQ_SRC" >&2
    exit 1
fi

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT HUP INT TERM

rm -rf "$DEPS_PREFIX" "$INSTALL_PREFIX/share/firebird" \
    "$INSTALL_PREFIX/share/licenses/firebird-client" \
    "$INSTALL_PREFIX/share/licenses/libfq"
mkdir -p "$INSTALL_PREFIX/lib"
find "$INSTALL_PREFIX/lib" -maxdepth 1 \( -name 'libfbclient*' -o -name 'libfq*' -o -name 'libtommath*' -o -name 'libtomcrypt*' -o -name 'libib_util*' \) -exec rm -f {} +
mkdir -p "$DEPS_PREFIX/include" "$INSTALL_PREFIX/share/firebird" \
    "$INSTALL_PREFIX/share/licenses/firebird-client" \
    "$INSTALL_PREFIX/share/licenses/libfq"

fb_root="$work/firebird-root"
mkdir -p "$fb_root"

extract_firebird() {
    case "$FIREBIRD_ARCHIVE" in
        *.pkg)
            pkgutil --expand-full "$FIREBIRD_ARCHIVE" "$work/pkg"
            payload=$(find "$work/pkg" -type d -path '*/Payload/Versions/A' | head -n 1)
            if [ -z "$payload" ]; then
                echo "macOS Firebird pkg has no Payload/Versions/A tree" >&2
                exit 1
            fi
            mkdir -p "$fb_root/lib" "$fb_root/include" "$fb_root/plugins" "$fb_root/tzdata"
            cp -a "$payload/Headers/." "$fb_root/include/"
            cp -a "$payload/Resources/lib/." "$fb_root/lib/"
            cp -a "$payload/Resources/plugins/." "$fb_root/plugins/"
            cp -a "$payload/Resources/tzdata/." "$fb_root/tzdata/"
            cp "$payload/Resources/firebird.msg" "$fb_root/firebird.msg"
            cp "$payload/Resources/plugins.conf" "$fb_root/plugins.conf"
            if [ -f "$payload/Resources/firebird.conf" ]; then
                cp "$payload/Resources/firebird.conf" "$fb_root/firebird.conf"
            fi
            for license in "$payload/Resources/License.txt" \
                "$payload/Resources/doc/license/README.license.usage.txt" \
                "$payload/Resources/IPLicense.txt" \
                "$payload/Resources/IDPLicense.txt"; do
                if [ -f "$license" ]; then
                    cp "$license" "$INSTALL_PREFIX/share/licenses/firebird-client/"
                fi
            done
            ;;
        *.tar.gz|*.tgz)
            tar xzf "$FIREBIRD_ARCHIVE" -C "$work"
            buildroot=$(find "$work" -name buildroot.tar.gz | head -n 1)
            if [ -z "$buildroot" ]; then
                echo "Linux Firebird archive is missing buildroot.tar.gz" >&2
                exit 1
            fi
            mkdir -p "$work/buildroot"
            tar xzf "$buildroot" -C "$work/buildroot"
            src="$work/buildroot/opt/firebird"
            if [ ! -d "$src/lib" ] || [ ! -f "$src/include/ibase.h" ]; then
                echo "Linux Firebird buildroot is missing lib/ or include/ibase.h" >&2
                exit 1
            fi
            mkdir -p "$fb_root/lib" "$fb_root/include" "$fb_root/plugins" "$fb_root/tzdata"
            cp -a "$src/include/." "$fb_root/include/"
            cp -a "$src/lib/." "$fb_root/lib/"
            cp -a "$src/plugins/." "$fb_root/plugins/"
            if [ -d "$src/tzdata" ]; then
                cp -a "$src/tzdata/." "$fb_root/tzdata/"
            fi
            cp "$src/firebird.msg" "$fb_root/firebird.msg"
            cp "$src/plugins.conf" "$fb_root/plugins.conf"
            if [ -f "$src/firebird.conf" ]; then
                cp "$src/firebird.conf" "$fb_root/firebird.conf"
            fi
            for license in "$src/IPLicense.txt" "$src/IDPLicense.txt" "$src/License.txt"; do
                if [ -f "$license" ]; then
                    cp "$license" "$INSTALL_PREFIX/share/licenses/firebird-client/"
                fi
            done
            ;;
        *)
            echo "unsupported Firebird archive: $FIREBIRD_ARCHIVE" >&2
            exit 1
            ;;
    esac
}

copy_client_runtime() {
    local dest=$1
    mkdir -p "$dest/lib" "$dest/plugins" "$dest/tzdata"
    # Client libraries only. Skip ICU (engine-only) and keep SONAME symlinks.
    for pattern in 'libfbclient*' 'libtommath*' 'libtomcrypt*' 'libib_util*'; do
        find "$fb_root/lib" -maxdepth 1 \( -type f -o -type l \) -name "$pattern" -exec cp -a {} "$dest/lib/" \;
    done
    # Remote-client plugins: auth + wire crypto. Do not copy Engine (server).
    for pattern in 'libChaCha*' 'libSrp*' 'libLegacy_Auth*'; do
        find "$fb_root/plugins" -maxdepth 1 \( -type f -o -type l \) -name "$pattern" -exec cp -a {} "$dest/plugins/" \;
    done
    cp "$fb_root/firebird.msg" "$dest/firebird.msg"
    cp "$fb_root/plugins.conf" "$dest/plugins.conf"
    if [ -f "$fb_root/firebird.conf" ]; then
        cp "$fb_root/firebird.conf" "$dest/firebird.conf"
    fi
    if [ -d "$fb_root/tzdata" ]; then
        cp -a "$fb_root/tzdata/." "$dest/tzdata/"
    fi
}

build_tommath_linux() {
    if find "$INSTALL_PREFIX/lib" -name 'libtommath.so*' | grep -q .; then
        return 0
    fi
    if [ -z "$TOMMATH_SRC" ] || [ ! -d "$TOMMATH_SRC" ]; then
        echo "Linux libfbclient needs libtommath.so.1; pass --tommath-src" >&2
        exit 1
    fi
    echo "building private libtommath from $TOMMATH_SRC"
    mkdir -p "$work/tommath"
    cp -a "$TOMMATH_SRC/." "$work/tommath/"
    (
        cd "$work/tommath"
        # makefile.shared needs GNU libtool; compile a PIC shared object directly.
        sources=$(ls bn_*.c 2>/dev/null || true)
        if [ -z "$sources" ]; then
            echo "libtommath source has no bn_*.c files" >&2
            exit 1
        fi
        # shellcheck disable=SC2086
        ${CC:-gcc} -shared -fPIC -O2 -Wall -Wno-unused-parameter \
            -Wl,-soname,libtommath.so.1 -o libtommath.so.1 $sources
        cp libtommath.so.1 "$INSTALL_PREFIX/lib/libtommath.so.1"
        ln -sfn libtommath.so.1 "$INSTALL_PREFIX/lib/libtommath.so"
        mkdir -p "$INSTALL_PREFIX/share/firebird/lib"
        cp libtommath.so.1 "$INSTALL_PREFIX/share/firebird/lib/libtommath.so.1"
        ln -sfn libtommath.so.1 "$INSTALL_PREFIX/share/firebird/lib/libtommath.so"
    )
}

rewrite_darwin_copied_libs() {
    local dir=$1
    find "$dir" -maxdepth 1 -type f -name '*.dylib' | while IFS= read -r dylib; do
        base=$(basename "$dylib")
        install_name_tool -id "$dylib" "$dylib" 2>/dev/null || true
        otool -L "$dylib" | awk 'NR>1 {print $1}' | while IFS= read -r ref; do
            case "$ref" in
                @rpath/lib/*)
                    dep=${ref##*/}
                    if [ -e "$dir/$dep" ]; then
                        install_name_tool -change "$ref" "$dir/$dep" "$dylib" 2>/dev/null || true
                    fi
                    ;;
            esac
        done
    done
}

extract_firebird
cp -a "$fb_root/include/." "$DEPS_PREFIX/include/"
if [ ! -f "$DEPS_PREFIX/include/ibase.h" ]; then
    echo "ibase.h was not installed into $DEPS_PREFIX/include" >&2
    exit 1
fi

copy_client_runtime "$INSTALL_PREFIX/share/firebird"
# Linker search path for libfq / firebird_fdw: same SONAME/symlink set in lib/.
find "$INSTALL_PREFIX/share/firebird/lib" \( -type f -o -type l \) -exec cp -a {} "$INSTALL_PREFIX/lib/" \;

if [ "$HOST_OS" = "Linux" ]; then
    build_tommath_linux
fi

if [ "$HOST_OS" = "Darwin" ]; then
    rewrite_darwin_copied_libs "$INSTALL_PREFIX/lib"
fi

if ! find "$INSTALL_PREFIX/lib" \( -name 'libfbclient.dylib' -o -name 'libfbclient.so*' \) | grep -q .; then
    echo "libfbclient is missing from $INSTALL_PREFIX/lib after Firebird client staging" >&2
    exit 1
fi

echo "building libfq in $LIBFQ_SRC"
(
    cd "$LIBFQ_SRC"
    if [ -f Makefile ]; then
        make distclean >/dev/null 2>&1 || make clean >/dev/null 2>&1 || true
    fi
    ./configure --prefix="$DEPS_PREFIX" --libdir="$DEPS_PREFIX/lib" \
        --with-ibase="$DEPS_PREFIX/include" \
        --with-fbclient="$INSTALL_PREFIX/lib"
    make -j"$JOBS"
    make install
)

find "$DEPS_PREFIX/lib" \( -name 'libfq*.dylib' -o -name 'libfq.so*' \) ! -name '*.la' | while IFS= read -r lib; do
    cp -a "$lib" "$INSTALL_PREFIX/lib/"
done
if [ "$HOST_OS" = "Darwin" ]; then
    rewrite_darwin_copied_libs "$INSTALL_PREFIX/lib"
fi

if ! find "$INSTALL_PREFIX/lib" \( -name 'libfq.dylib' -o -name 'libfq*.dylib' -o -name 'libfq.so*' \) | grep -q .; then
    echo "libfq is missing from $INSTALL_PREFIX/lib after libfq install" >&2
    exit 1
fi

# libfq has no standalone LICENSE file; record the in-source notice.
cat > "$INSTALL_PREFIX/share/licenses/libfq/NOTICE" <<'EOF'
libfq is released under the PostgreSQL Licence.
Copyright (c) 2013-2023 Ian Barwick
EOF

echo "firebird_fdw native dependencies staged under $INSTALL_PREFIX"
