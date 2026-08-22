#!/usr/bin/env bash
# Build and install a private libcurl for pg_net / pgsql-http in CI containers.
#
# Distibution libcurls are older than the 7.83 API floor those extensions
# require (AlmaLinux 8 ships 7.61, AlmaLinux 9 ships 7.76). This script builds
# a current curl into a dedicated prefix; pgbuild picks it up via
# PG_NET_CURL_PREFIX / PGSQL_HTTP_CURL_CONFIG, and auditwheel vendors the
# resulting libcurl.so.4 into the wheel (libcurl is not in the manylinux
# policy whitelist, so nothing silently depends on the build container).
#
# curl >= 8.21 requires OpenSSL 3, but the manylinux_2_28 image only has
# 1.1.1, so a static OpenSSL 3 is built first and linked into libcurl. That
# keeps libssl.so.3/libcrypto.so.3 out of the wheel entirely.
#
# Idempotent: exits without rebuilding when the prefix is already populated.
#
# Usage: build_curl.sh [PREFIX] [OPENSSL_PREFIX]
#   PREFIX          install prefix (default /usr/local/curl-pg)
#   OPENSSL_PREFIX  optional OpenSSL prefix for non-default installs (macOS
#                   development hosts); Linux containers find the system
#                   OpenSSL through pkg-config on the default paths.
set -euo pipefail

CURL_VERSION=8.21.0
CURL_SHA256=aa1b66a70eace83dc624508745646c08ae561de512ab403adffb93ac87fc72e6
OPENSSL_VERSION=3.5.7
OPENSSL_SHA256=a8c0d28a529ca480f9f36cf5792e2cd21984552a3c8e4aa11a24aa31aeac98e8
PREFIX="${1:-/usr/local/curl-pg}"
OPENSSL_PREFIX="${2:-}"

checksum() {
    echo "$2  $1" | (sha256sum -c - 2>/dev/null || shasum -a 256 -c -)
}

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

if [ -x "$PREFIX/bin/curl-config" ] && "$PREFIX/bin/curl-config" --version >/dev/null 2>&1; then
    echo "curl already installed: $("$PREFIX/bin/curl-config" --version) at $PREFIX"
    exit 0
fi

openssl_dir="$PREFIX/openssl"

if [ ! -f "$openssl_dir/lib/libssl.a" ]; then
    echo "downloading OpenSSL ${OPENSSL_VERSION}..."
    curl --fail --location --show-error --proto '=https' --retry 3 \
        -o "$work/openssl.tar.gz" \
        "https://github.com/openssl/openssl/releases/download/openssl-${OPENSSL_VERSION}/openssl-${OPENSSL_VERSION}.tar.gz"
    checksum "$work/openssl.tar.gz" "$OPENSSL_SHA256"

    tar -xzf "$work/openssl.tar.gz" -C "$work"
    (
        cd "$work/openssl-${OPENSSL_VERSION}"
        ./Configure --prefix="$openssl_dir" --libdir=lib no-shared no-tests \
            "-Wl,-rpath,$openssl_dir/lib"
        make -j"$(getconf _NPROCESSORS_ONLN 2>/dev/null || nproc 2>/dev/null || echo 4)" >/dev/null
        make install_sw >/dev/null
    )
    echo "openssl ${OPENSSL_VERSION} (static) installed at $openssl_dir"
fi

echo "downloading curl ${CURL_VERSION}..."
curl --fail --location --show-error --proto '=https' --retry 3 \
    -o "$work/curl.tar.xz" "https://curl.se/download/curl-${CURL_VERSION}.tar.xz"
checksum "$work/curl.tar.xz" "$CURL_SHA256"

tar -xJf "$work/curl.tar.xz" -C "$work"
cd "$work/curl-${CURL_VERSION}"

if [ -n "$OPENSSL_PREFIX" ]; then
    # Non-default OpenSSL for local verification on macOS hosts.
    openssl_dir="$OPENSSL_PREFIX"
fi

# Keep the dependency surface minimal: everything optional that would drag in
# another shared library is disabled; TLS stays on (static OpenSSL 3).
./configure --prefix="$PREFIX" --with-openssl="$openssl_dir" --with-zlib \
    --without-libpsl --without-libidn2 --without-brotli --without-zstd --without-nghttp2 \
    --disable-ldap --disable-ldaps --disable-manual --disable-dependency-tracking
make -j"$(getconf _NPROCESSORS_ONLN 2>/dev/null || nproc 2>/dev/null || echo 4)"
make install
echo "installed: $("$PREFIX/bin/curl-config" --version) at $PREFIX"
