SHELL := /bin/bash
INSTALL_PREFIX ?= $(shell pwd)/../src/pgembed/pginstall
POSTGRES_BUNDLE_CONFIG_STAMP ?= .postgres-bundle-config.stamp
TIGERFS_CONFIG_STAMP ?= .tigerfs-config.stamp
BUNDLE_COMPLETION_MARKER ?= $(INSTALL_PREFIX)/share/pgembed/.bundle-complete
BUNDLE_COMPLETION_REQUIRED ?= $(if $(filter all bundle-metadata,$(MAKECMDGOALS)),1,0)

HOST_OS ?= $(shell uname -s)
HOST_ARCH ?= $(shell uname -m)
SUPPORTED_HOST_OSES := Darwin Linux
ifeq ($(filter $(HOST_OS),$(SUPPORTED_HOST_OSES)),)
$(error unsupported build host $(HOST_OS); supported hosts are Darwin and Linux)
endif

IS_MUSL ?= $(shell if command -v ldd >/dev/null 2>&1 && ldd --version 2>&1 | grep -qi musl; then echo 1; else echo 0; fi)
HAS_LIBCURL ?= $(shell if command -v curl-config >/dev/null 2>&1 || [ "$(HOST_OS)" = "Darwin" ]; then echo 1; else echo 0; fi)
LIBC_NAME := $(if $(filter 1,$(IS_MUSL)),musl,$(if $(filter Linux,$(HOST_OS)),glibc,system))
PG_DLSUFFIX := $(if $(filter Darwin,$(HOST_OS)),dylib,so)
SHA256_CMD := $(if $(filter Darwin,$(HOST_OS)),shasum -a 256,sha256sum)
POSTGRES_CONFIGURE_FLAGS := --without-readline --without-icu
MACOSX_DEPLOYMENT_TARGET ?=

RUST_TOOLCHAIN := 1.95.0
CARGO_PGRX_VERSION := 0.17.0
RUST_EXTENSIONS_SUPPORTED := $(if $(filter 1,$(IS_MUSL)),0,1)

ifeq ($(RUST_EXTENSIONS_SUPPORTED),1)
EXTENSIONS ?= pgvector vectorchord pg_duckdb age psql_bm25s timescaledb pg_cron pg_net tigerfs
else
EXTENSIONS ?= pgvector pg_duckdb age psql_bm25s timescaledb pg_cron pg_net tigerfs
endif
REQUESTED_EXTENSIONS := $(strip $(EXTENSIONS))
UNSUPPORTED_EXTENSIONS :=
ifeq ($(HAS_LIBCURL),0)
UNSUPPORTED_EXTENSIONS += pg_net
endif
ifeq ($(RUST_EXTENSIONS_SUPPORTED),0)
UNSUPPORTED_EXTENSIONS += vectorchord
endif
SKIPPED_EXTENSIONS := $(sort $(filter $(UNSUPPORTED_EXTENSIONS),$(REQUESTED_EXTENSIONS)))
FINAL_EXTENSIONS := $(filter-out $(UNSUPPORTED_EXTENSIONS),$(REQUESTED_EXTENSIONS))
EXTENSIONS := $(FINAL_EXTENSIONS)
ifneq ($(strip $(SKIPPED_EXTENSIONS)),)
$(warning Skipping unsupported extensions on this platform: $(SKIPPED_EXTENSIONS))
endif

POSTGRES_MAJOR := 18
POSTGRES_VERSION := 18.4
POSTGRES_SRC_REF := REL_18_4
POSTGRES_SOURCE_COMMIT := f5cc81719e6da4cbdb1f797c48b693e91018153a
POSTGRES_SRC := postgres-$(POSTGRES_SRC_REF)
POSTGRES_BLD := postgres-build-$(POSTGRES_SRC_REF)
POSTGRES_SOURCE_VERIFIED := $(POSTGRES_SRC)/.pgembed-source-verified

PGVECTOR_TAG := v0.8.2
PGVECTOR_SHA256 := 69f4019389af05dc1c9548deb8628e62878e6e207c03907f2b8af2016472cdaa
PGVECTOR_URL := https://github.com/pgvector/pgvector/archive/refs/tags/$(PGVECTOR_TAG).tar.gz
PGVECTOR_DIR := pgvector-$(PGVECTOR_TAG)

PG_DUCKDB_TAG := v1.1.1
PG_DUCKDB_COMMIT := 7b0db3737a1ab2dfb182b742322426e3c4b500af
PG_DUCKDB_DUCKDB_COMMIT := d1dc88f950d456d72493df452dabdcd13aa413dd
PG_DUCKDB_REPO := https://github.com/duckdb/pg_duckdb
PG_DUCKDB_DIR := pg_duckdb-$(PG_DUCKDB_TAG)
PG_DUCKDB_PATCH := patches/pg_duckdb-v1.1.1-planner-hook-chain.patch
PG_DUCKDB_PATCH_SHA256 := e8caa2ae4b17269678da33a59ff9362f33f67a3bd870789ae3bd34b45fadee00
PG_DUCKDB_SOURCE_VERIFIED := $(PG_DUCKDB_DIR)/.pgembed-source-verified
PG_DUCKDB_GEN ?= $(shell if command -v ninja >/dev/null 2>&1; then echo ninja; else echo make; fi)
PG_DUCKDB_LZ4_PREFIX ?= $(shell if [ -d /opt/homebrew/opt/lz4 ]; then echo /opt/homebrew/opt/lz4; elif [ -d /usr/local/opt/lz4 ]; then echo /usr/local/opt/lz4; fi)
PG_DUCKDB_OPENSSL_PREFIX ?= $(shell if [ -d /opt/homebrew/opt/openssl@3 ]; then echo /opt/homebrew/opt/openssl@3; elif [ -d /usr/local/opt/openssl@3 ]; then echo /usr/local/opt/openssl@3; fi)
PG_DUCKDB_LDFLAGS := $(if $(PG_DUCKDB_LZ4_PREFIX),-L$(PG_DUCKDB_LZ4_PREFIX)/lib,) $(if $(PG_DUCKDB_OPENSSL_PREFIX),-L$(PG_DUCKDB_OPENSSL_PREFIX)/lib -lssl -lcrypto,)

VECTORCHORD_TAG := 1.1.1
VECTORCHORD_SHA256 := d70b5595bfc852f1f24c05c0a40272e7deecbb0ddf8ffdddec5afa42c2392b1e
VECTORCHORD_URL := https://github.com/tensorchord/VectorChord/archive/refs/tags/$(VECTORCHORD_TAG).tar.gz
VECTORCHORD_DIR := vectorchord-$(VECTORCHORD_TAG)

AGE_TAG := release/PG18/1.8.0
AGE_COMMIT := e43dc1a12b78fba4acef9835b2b10379b8d243b4
AGE_REPO := https://github.com/apache/age.git
AGE_DIR := age-release_PG18_1.8.0
AGE_SOURCE_VERIFIED := $(AGE_DIR)/.pgembed-source-verified
AGE_BISON ?= $(shell if [ -x /opt/homebrew/opt/bison/bin/bison ]; then echo /opt/homebrew/opt/bison/bin/bison; elif [ -x /usr/local/opt/bison/bin/bison ]; then echo /usr/local/opt/bison/bin/bison; else command -v bison 2>/dev/null; fi)
AGE_BISONFLAGS ?= --defines=src/include/parser/cypher_gram_def.h -Werror -Wno-error=conflicts-sr -Wno-error=conflicts-rr -Wno-error=deprecated -Wno-error=other

PSQL_BM25S_COMMIT := d1c1db7e6c2a92c2a909e97c51cf2f45c0da808b
PSQL_BM25S_REPO := https://github.com/Intelligent-Internet/psql_bm25s.git
PSQL_BM25S_DIR := psql_bm25s-$(PSQL_BM25S_COMMIT)
PSQL_BM25S_SOURCE_VERIFIED := $(PSQL_BM25S_DIR)/.pgembed-source-verified
PSQL_BM25S_ICU_PREFIX ?= $(shell if [ -d /opt/homebrew/opt/icu4c@78 ]; then echo /opt/homebrew/opt/icu4c@78; elif [ -d /usr/local/opt/icu4c@78 ]; then echo /usr/local/opt/icu4c@78; fi)
PSQL_BM25S_LDFLAGS := $(if $(PSQL_BM25S_ICU_PREFIX),-L$(PSQL_BM25S_ICU_PREFIX)/lib,) -licui18n -licuuc -licudata

TIMESCALEDB_TAG := 2.27.1
TIMESCALEDB_SHA256 := f0a940720bb5b0b635dae4d8aeceb13e83b196b8aab8717876af0f45efa47ab6
TIMESCALEDB_URL := https://github.com/timescale/timescaledb/archive/refs/tags/$(TIMESCALEDB_TAG).tar.gz
TIMESCALEDB_DIR := timescaledb-$(TIMESCALEDB_TAG)

PG_CRON_TAG := v1.6.7
PG_CRON_COMMIT := 465b38c737f584d520229f5a1d69d1d44649e4e5
PG_CRON_REPO := https://github.com/citusdata/pg_cron.git
PG_CRON_DIR := pg_cron-$(PG_CRON_TAG)
PG_CRON_SOURCE_VERIFIED := $(PG_CRON_DIR)/.pgembed-source-verified

PG_NET_TAG := v0.20.5
PG_NET_COMMIT := a8299b11182ea5c974f5e89ae83e70e9e44e9e8f
PG_NET_REPO := https://github.com/supabase/pg_net.git
PG_NET_DIR := pg_net-$(PG_NET_TAG)
PG_NET_SOURCE_VERIFIED := $(PG_NET_DIR)/.pgembed-source-verified
PG_NET_CURL_PREFIX ?= $(shell if [ -d /opt/homebrew/opt/curl ]; then echo /opt/homebrew/opt/curl; elif [ -d /usr/local/opt/curl ]; then echo /usr/local/opt/curl; fi)
PG_NET_CURL_CPPFLAGS := $(if $(PG_NET_CURL_PREFIX),-I$(PG_NET_CURL_PREFIX)/include,)
PG_NET_CURL_LDFLAGS := $(if $(PG_NET_CURL_PREFIX),-L$(PG_NET_CURL_PREFIX)/lib,)

TIGERFS_TAG ?= v0.7.0
TIGERFS_OS ?= $(HOST_OS)
TIGERFS_ARCH ?= $(if $(filter arm64 aarch64,$(HOST_ARCH)),arm64,$(if $(filter x86_64 amd64,$(HOST_ARCH)),x86_64,$(error unsupported TIGERFS_ARCH for tigerfs: $(HOST_ARCH))))
TIGERFS_ARCHIVE := tigerfs_$(TIGERFS_OS)_$(TIGERFS_ARCH).tar.gz
TIGERFS_BASE := https://github.com/timescale/tigerfs/releases/download/$(TIGERFS_TAG)
TIGERFS_SHA256_Linux_x86_64 := fe30c217a5467003d4a9c8602d0f46ce2a59fe14ba142934137bffb36f965a7d
TIGERFS_SHA256_Linux_arm64 := 3527c554203f8bacc0a2104c1a6905ac60233e980dfa488cb03693ad061e1033
TIGERFS_SHA256_Darwin_arm64 := 5093a167654626d6d04b9dbe78cb29c385f1621a1c68160a1cbd75014afcc78d
TIGERFS_SHA256_Darwin_x86_64 := c62f7017e63750de4ae9375fb9becb1a50f0006ed44dfaee23d697f806094df9
TIGERFS_EXPECTED_SHA256 := $(TIGERFS_SHA256_$(TIGERFS_OS)_$(TIGERFS_ARCH))
TIGERFS_SELECTED := $(filter tigerfs,$(EXTENSIONS) $(MAKECMDGOALS))

NATIVE_BUILD_DIRS := $(POSTGRES_SRC) $(POSTGRES_BLD) $(PGVECTOR_DIR) $(PG_DUCKDB_DIR) $(VECTORCHORD_DIR) $(AGE_DIR) $(PSQL_BM25S_DIR) $(TIMESCALEDB_DIR) $(PG_CRON_DIR) $(PG_NET_DIR)

.PHONY: FORCE
FORCE:

# Stable, content-checked bundle identity. A changed recipe removes the entire
# native prefix and every unpacked source/build tree before any target is tested.
$(POSTGRES_BUNDLE_CONFIG_STAMP): FORCE
	@set -eu; \
	stamp_dir=$$(dirname "$@"); mkdir -p "$$stamp_dir"; \
	tmp="$@.tmp.$$$$"; trap 'rm -f "$$tmp"' EXIT HUP INT TERM; \
	printf '%s\n' \
		'schema=1' \
		'recipe=pgembed-postgresql-18-bundle-v1' \
		'postgres_major=$(POSTGRES_MAJOR)' \
		'postgres_version=$(POSTGRES_VERSION)' \
		'postgres_ref=$(POSTGRES_SRC_REF)' \
		'postgres_commit=$(POSTGRES_SOURCE_COMMIT)' \
		'postgres_configure=$(POSTGRES_CONFIGURE_FLAGS)' \
		'install_prefix=$(abspath $(INSTALL_PREFIX))' \
		'host_os=$(HOST_OS)' \
		'host_arch=$(HOST_ARCH)' \
		'libc=$(LIBC_NAME)' \
		'dlsuffix=$(PG_DLSUFFIX)' \
		'macosx_deployment_target=$(MACOSX_DEPLOYMENT_TARGET)' \
		'is_musl=$(IS_MUSL)' \
		'has_libcurl=$(HAS_LIBCURL)' \
		'requested_extensions=$(REQUESTED_EXTENSIONS)' \
		'final_extensions=$(FINAL_EXTENSIONS)' \
		'skipped_extensions=$(SKIPPED_EXTENSIONS)' \
		'pgvector=$(PGVECTOR_TAG):$(PGVECTOR_SHA256)' \
		'pg_duckdb=$(PG_DUCKDB_TAG):$(PG_DUCKDB_COMMIT):duckdb=$(PG_DUCKDB_DUCKDB_COMMIT):patch=$(PG_DUCKDB_PATCH_SHA256)' \
		'pg_duckdb_gen=$(PG_DUCKDB_GEN)' \
		'pg_duckdb_lz4_prefix=$(PG_DUCKDB_LZ4_PREFIX)' \
		'pg_duckdb_openssl_prefix=$(PG_DUCKDB_OPENSSL_PREFIX)' \
		'pg_duckdb_ldflags=$(PG_DUCKDB_LDFLAGS)' \
		'vectorchord=$(VECTORCHORD_TAG):$(VECTORCHORD_SHA256)' \
		'age=$(AGE_TAG):$(AGE_COMMIT)' \
		'psql_bm25s=$(PSQL_BM25S_COMMIT)' \
		'psql_bm25s_icu_prefix=$(PSQL_BM25S_ICU_PREFIX)' \
		'psql_bm25s_ldflags=$(PSQL_BM25S_LDFLAGS)' \
		'timescaledb=$(TIMESCALEDB_TAG):$(TIMESCALEDB_SHA256)' \
		'pg_cron=$(PG_CRON_TAG):$(PG_CRON_COMMIT)' \
		'pg_net=$(PG_NET_TAG):$(PG_NET_COMMIT)' \
		'pg_net_curl_prefix=$(PG_NET_CURL_PREFIX)' \
		'pg_net_curl_cppflags=$(PG_NET_CURL_CPPFLAGS)' \
		'pg_net_curl_ldflags=$(PG_NET_CURL_LDFLAGS)' \
		'tigerfs=$(TIGERFS_TAG):$(TIGERFS_OS):$(TIGERFS_ARCH):$(TIGERFS_EXPECTED_SHA256)' \
		'rust_toolchain=$(RUST_TOOLCHAIN)' \
		'cargo_pgrx_version=$(CARGO_PGRX_VERSION)' > "$$tmp"; \
	if cmp -s "$$tmp" "$@"; then \
		if test -d "$(INSTALL_PREFIX)" && { ! test -x "$(INSTALL_PREFIX)/bin/postgres" || ! test -x "$(INSTALL_PREFIX)/bin/pg_config" || ! test -x "$(INSTALL_PREFIX)/bin/pg_ctl" || ! test -x "$(INSTALL_PREFIX)/bin/psql"; }; then rm -rf "$(INSTALL_PREFIX)"; fi; \
		if test "$(BUNDLE_COMPLETION_REQUIRED)" = 1 && test -d "$(INSTALL_PREFIX)" && { ! test -f "$(BUNDLE_COMPLETION_MARKER)" || ! test -f "$(INSTALL_PREFIX)/share/pgembed/build-metadata.json"; }; then rm -rf "$(INSTALL_PREFIX)"; fi; \
		exit 0; \
	fi; \
	rm -rf "$(INSTALL_PREFIX)" $(NATIVE_BUILD_DIRS); \
	mv -f "$$tmp" "$@"

.PHONY: all postgres pgvector pg_duckdb vectorchord age psql_bm25s timescaledb pg_cron pg_net tigerfs bundle-metadata
all: postgres $(EXTENSIONS) bundle-metadata
postgres: $(INSTALL_PREFIX)/bin/postgres
pgvector: postgres $(INSTALL_PREFIX)/lib/postgresql/vector.$(PG_DLSUFFIX)
pg_duckdb: postgres $(INSTALL_PREFIX)/lib/postgresql/pg_duckdb.$(PG_DLSUFFIX)
vectorchord: postgres $(INSTALL_PREFIX)/lib/postgresql/vchord.$(PG_DLSUFFIX)
age: postgres $(INSTALL_PREFIX)/lib/postgresql/age.$(PG_DLSUFFIX)
psql_bm25s: postgres $(INSTALL_PREFIX)/lib/postgresql/psql_bm25s.$(PG_DLSUFFIX)
timescaledb: postgres $(INSTALL_PREFIX)/lib/postgresql/timescaledb.$(PG_DLSUFFIX)
pg_cron: postgres $(INSTALL_PREFIX)/lib/postgresql/pg_cron.$(PG_DLSUFFIX)
pg_net: postgres $(INSTALL_PREFIX)/lib/postgresql/pg_net.$(PG_DLSUFFIX)
tigerfs: postgres $(INSTALL_PREFIX)/bin/tigerfs

$(POSTGRES_SOURCE_VERIFIED): $(POSTGRES_BUNDLE_CONFIG_STAMP)
	@set -eu; \
	rm -rf "$(POSTGRES_SRC)"; \
	git clone --branch "$(POSTGRES_SRC_REF)" --single-branch https://github.com/postgres/postgres "$(POSTGRES_SRC)"; \
	test "$$(git -C "$(POSTGRES_SRC)" rev-parse HEAD)" = "$(POSTGRES_SOURCE_COMMIT)"; \
	test -f "$(POSTGRES_SRC)/configure"; \
	touch "$@"

$(POSTGRES_BLD)/config.status: $(POSTGRES_BUNDLE_CONFIG_STAMP) $(POSTGRES_SOURCE_VERIFIED)
	mkdir -p $(POSTGRES_BLD)
	cd $(POSTGRES_BLD) && ../$(POSTGRES_SRC)/configure --prefix=$(INSTALL_PREFIX) $(POSTGRES_CONFIGURE_FLAGS)

$(POSTGRES_BLD)/src/bin/initdb/initdb: $(POSTGRES_BUNDLE_CONFIG_STAMP) $(POSTGRES_BLD)/config.status
	unset MAKELEVEL MAKEFLAGS MFLAGS && $(MAKE) -C $(POSTGRES_BLD) -j

$(INSTALL_PREFIX)/bin/postgres: $(POSTGRES_BUNDLE_CONFIG_STAMP) $(POSTGRES_BLD)/src/bin/initdb/initdb
	mkdir -p $(INSTALL_PREFIX)
	unset MAKELEVEL MAKEFLAGS MFLAGS && $(MAKE) -C $(POSTGRES_BLD) install

$(PGVECTOR_DIR).tar.gz:
	@set -eu; \
	tmp="$@.tmp.$$$$"; trap 'rm -f "$$tmp"' EXIT HUP INT TERM; \
	curl --fail --location --show-error --proto '=https' --proto-redir '=https' --retry 3 -o "$$tmp" "$(PGVECTOR_URL)"; \
	printf '%s  %s\n' '$(PGVECTOR_SHA256)' "$$tmp" | $(SHA256_CMD) -c -; \
	mv -f "$$tmp" "$@"

$(PGVECTOR_DIR)/Makefile: $(POSTGRES_BUNDLE_CONFIG_STAMP) $(PGVECTOR_DIR).tar.gz
	printf '%s  %s\n' '$(PGVECTOR_SHA256)' '$(PGVECTOR_DIR).tar.gz' | $(SHA256_CMD) -c -
	mkdir -p $(PGVECTOR_DIR)
	tar xzf $(PGVECTOR_DIR).tar.gz -C $(PGVECTOR_DIR) --strip-components=1
	touch $@

$(INSTALL_PREFIX)/lib/postgresql/vector.$(PG_DLSUFFIX): $(POSTGRES_BUNDLE_CONFIG_STAMP) $(PGVECTOR_DIR)/Makefile $(INSTALL_PREFIX)/bin/postgres
	unset MAKELEVEL MAKEFLAGS MFLAGS && $(MAKE) -C $(PGVECTOR_DIR) PG_CONFIG=$(INSTALL_PREFIX)/bin/pg_config -j && $(MAKE) -C $(PGVECTOR_DIR) PG_CONFIG=$(INSTALL_PREFIX)/bin/pg_config install

$(PG_DUCKDB_SOURCE_VERIFIED): $(POSTGRES_BUNDLE_CONFIG_STAMP) $(PG_DUCKDB_PATCH)
	@set -eu; \
	rm -rf "$(PG_DUCKDB_DIR)"; \
	printf '%s  %s\n' '$(PG_DUCKDB_PATCH_SHA256)' '$(PG_DUCKDB_PATCH)' | $(SHA256_CMD) -c -; \
	git clone --branch "$(PG_DUCKDB_TAG)" --single-branch "$(PG_DUCKDB_REPO)" "$(PG_DUCKDB_DIR)"; \
	git -C "$(PG_DUCKDB_DIR)" submodule update --init --recursive; \
	test "$$(git -C "$(PG_DUCKDB_DIR)" rev-parse HEAD)" = "$(PG_DUCKDB_COMMIT)"; \
	test "$$(git -C "$(PG_DUCKDB_DIR)/third_party/duckdb" rev-parse HEAD)" = "$(PG_DUCKDB_DUCKDB_COMMIT)"; \
	patch -d "$(PG_DUCKDB_DIR)" -p1 < "$(PG_DUCKDB_PATCH)"; \
	test -f "$(PG_DUCKDB_DIR)/Makefile"; \
	grep -q 'PlanPostgresQuery' "$(PG_DUCKDB_DIR)/include/pgduckdb/pgduckdb_hooks.hpp"; \
	grep -q 'postgres_planner_nest_level' "$(PG_DUCKDB_DIR)/src/pgduckdb_hooks.cpp"; \
	grep -q 'PG_FINALLY' "$(PG_DUCKDB_DIR)/src/pgduckdb_hooks.cpp"; \
	grep -q 'PlanPostgresQuery(query, table_scan_query' "$(PG_DUCKDB_DIR)/src/scan/postgres_table_reader.cpp"; \
	! grep -q 'standard_planner(query, table_scan_query' "$(PG_DUCKDB_DIR)/src/scan/postgres_table_reader.cpp"; \
	grep -q 'TimescaleDBIsInstalled' "$(PG_DUCKDB_DIR)/src/scan/postgres_table_reader.cpp"; \
	grep -q 'run_scan_with_parallel_workers &= !TimescaleDBIsInstalled();' "$(PG_DUCKDB_DIR)/src/scan/postgres_table_reader.cpp"; \
	touch "$@"

$(INSTALL_PREFIX)/lib/postgresql/pg_duckdb.$(PG_DLSUFFIX): $(POSTGRES_BUNDLE_CONFIG_STAMP) $(PG_DUCKDB_SOURCE_VERIFIED) $(INSTALL_PREFIX)/bin/postgres
	unset MAKELEVEL MAKEFLAGS MFLAGS && $(MAKE) -C $(PG_DUCKDB_DIR) PG_CONFIG=$(INSTALL_PREFIX)/bin/pg_config DUCKDB_BUILD=ReleaseStatic DUCKDB_GEN=$(PG_DUCKDB_GEN) LDFLAGS="$(PG_DUCKDB_LDFLAGS)" install

$(VECTORCHORD_DIR).tar.gz:
	@set -eu; \
	tmp="$@.tmp.$$$$"; trap 'rm -f "$$tmp"' EXIT HUP INT TERM; \
	curl --fail --location --show-error --proto '=https' --proto-redir '=https' --retry 3 -o "$$tmp" "$(VECTORCHORD_URL)"; \
	printf '%s  %s\n' '$(VECTORCHORD_SHA256)' "$$tmp" | $(SHA256_CMD) -c -; \
	mv -f "$$tmp" "$@"

$(VECTORCHORD_DIR)/Makefile: $(POSTGRES_BUNDLE_CONFIG_STAMP) $(VECTORCHORD_DIR).tar.gz
	printf '%s  %s\n' '$(VECTORCHORD_SHA256)' '$(VECTORCHORD_DIR).tar.gz' | $(SHA256_CMD) -c -
	mkdir -p $(VECTORCHORD_DIR)
	tar xzf $(VECTORCHORD_DIR).tar.gz -C $(VECTORCHORD_DIR) --strip-components=1
	touch $@

$(INSTALL_PREFIX)/lib/postgresql/vchord.$(PG_DLSUFFIX): $(POSTGRES_BUNDLE_CONFIG_STAMP) $(VECTORCHORD_DIR)/Makefile $(INSTALL_PREFIX)/bin/postgres
ifeq ($(RUST_EXTENSIONS_SUPPORTED),1)
	unset MAKELEVEL MAKEFLAGS MFLAGS && export PATH="$$HOME/.cargo/bin:$$PATH" RUSTUP_TOOLCHAIN=$(RUST_TOOLCHAIN) && PG_CONFIG=$(INSTALL_PREFIX)/bin/pg_config $(MAKE) -C $(VECTORCHORD_DIR) build && PG_CONFIG=$(INSTALL_PREFIX)/bin/pg_config $(MAKE) -C $(VECTORCHORD_DIR) install
else
	$(error vectorchord is unsupported on musl)
endif

$(AGE_SOURCE_VERIFIED): $(POSTGRES_BUNDLE_CONFIG_STAMP)
	@set -eu; \
	rm -rf "$(AGE_DIR)"; \
	git clone --branch "$(AGE_TAG)" --single-branch "$(AGE_REPO)" "$(AGE_DIR)"; \
	test "$$(git -C "$(AGE_DIR)" rev-parse HEAD)" = "$(AGE_COMMIT)"; \
	test -f "$(AGE_DIR)/Makefile"; \
	touch "$@"

$(INSTALL_PREFIX)/lib/postgresql/age.$(PG_DLSUFFIX): $(POSTGRES_BUNDLE_CONFIG_STAMP) $(AGE_SOURCE_VERIFIED) $(INSTALL_PREFIX)/bin/postgres
	unset MAKELEVEL MAKEFLAGS MFLAGS && $(MAKE) -C $(AGE_DIR) PG_CONFIG=$(INSTALL_PREFIX)/bin/pg_config BISON="$(AGE_BISON)" BISONFLAGS="$(AGE_BISONFLAGS)" -j && $(MAKE) -C $(AGE_DIR) PG_CONFIG=$(INSTALL_PREFIX)/bin/pg_config BISON="$(AGE_BISON)" BISONFLAGS="$(AGE_BISONFLAGS)" install

$(PSQL_BM25S_SOURCE_VERIFIED): $(POSTGRES_BUNDLE_CONFIG_STAMP)
	@set -eu; \
	rm -rf "$(PSQL_BM25S_DIR)"; \
	git init "$(PSQL_BM25S_DIR)"; \
	git -C "$(PSQL_BM25S_DIR)" remote add origin "$(PSQL_BM25S_REPO)"; \
	git -C "$(PSQL_BM25S_DIR)" fetch --depth 1 origin "$(PSQL_BM25S_COMMIT)"; \
	git -C "$(PSQL_BM25S_DIR)" checkout --detach FETCH_HEAD; \
	test "$$(git -C "$(PSQL_BM25S_DIR)" rev-parse HEAD)" = "$(PSQL_BM25S_COMMIT)"; \
	test -f "$(PSQL_BM25S_DIR)/Makefile"; \
	touch "$@"

$(INSTALL_PREFIX)/lib/postgresql/psql_bm25s.$(PG_DLSUFFIX): $(POSTGRES_BUNDLE_CONFIG_STAMP) $(PSQL_BM25S_SOURCE_VERIFIED) $(INSTALL_PREFIX)/bin/postgres
	unset MAKELEVEL MAKEFLAGS MFLAGS && $(MAKE) -C $(PSQL_BM25S_DIR) PG_CONFIG=$(INSTALL_PREFIX)/bin/pg_config MAKEOVERRIDES= LDFLAGS="$(PSQL_BM25S_LDFLAGS)" -j && $(MAKE) -C $(PSQL_BM25S_DIR) PG_CONFIG=$(INSTALL_PREFIX)/bin/pg_config MAKEOVERRIDES= LDFLAGS="$(PSQL_BM25S_LDFLAGS)" install

$(TIMESCALEDB_DIR).tar.gz:
	@set -eu; \
	tmp="$@.tmp.$$$$"; trap 'rm -f "$$tmp"' EXIT HUP INT TERM; \
	curl --fail --location --show-error --proto '=https' --proto-redir '=https' --retry 3 -o "$$tmp" "$(TIMESCALEDB_URL)"; \
	printf '%s  %s\n' '$(TIMESCALEDB_SHA256)' "$$tmp" | $(SHA256_CMD) -c -; \
	mv -f "$$tmp" "$@"

$(TIMESCALEDB_DIR)/bootstrap: $(POSTGRES_BUNDLE_CONFIG_STAMP) $(TIMESCALEDB_DIR).tar.gz
	printf '%s  %s\n' '$(TIMESCALEDB_SHA256)' '$(TIMESCALEDB_DIR).tar.gz' | $(SHA256_CMD) -c -
	mkdir -p $(TIMESCALEDB_DIR)
	tar xzf $(TIMESCALEDB_DIR).tar.gz -C $(TIMESCALEDB_DIR) --strip-components=1
	touch $@

$(INSTALL_PREFIX)/lib/postgresql/timescaledb.$(PG_DLSUFFIX): $(POSTGRES_BUNDLE_CONFIG_STAMP) $(TIMESCALEDB_DIR)/bootstrap $(INSTALL_PREFIX)/bin/postgres
	unset MAKELEVEL MAKEFLAGS MFLAGS && export PATH="$(INSTALL_PREFIX)/bin:$$PATH" && cd $(TIMESCALEDB_DIR) && BUILD_FORCE_REMOVE=true ./bootstrap -DUSE_OPENSSL=0 && $(MAKE) -C build -j && $(MAKE) -C build install

$(PG_CRON_SOURCE_VERIFIED): $(POSTGRES_BUNDLE_CONFIG_STAMP)
	@set -eu; \
	rm -rf "$(PG_CRON_DIR)"; \
	git clone --branch "$(PG_CRON_TAG)" --single-branch "$(PG_CRON_REPO)" "$(PG_CRON_DIR)"; \
	test "$$(git -C "$(PG_CRON_DIR)" rev-parse HEAD)" = "$(PG_CRON_COMMIT)"; \
	test -f "$(PG_CRON_DIR)/Makefile"; \
	touch "$@"

$(INSTALL_PREFIX)/lib/postgresql/pg_cron.$(PG_DLSUFFIX): $(POSTGRES_BUNDLE_CONFIG_STAMP) $(PG_CRON_SOURCE_VERIFIED) $(INSTALL_PREFIX)/bin/postgres
	unset MAKELEVEL MAKEFLAGS MFLAGS && $(MAKE) -C $(PG_CRON_DIR) PG_CONFIG=$(INSTALL_PREFIX)/bin/pg_config -j && $(MAKE) -C $(PG_CRON_DIR) PG_CONFIG=$(INSTALL_PREFIX)/bin/pg_config install

$(PG_NET_SOURCE_VERIFIED): $(POSTGRES_BUNDLE_CONFIG_STAMP)
	@set -eu; \
	rm -rf "$(PG_NET_DIR)"; \
	git clone --branch "$(PG_NET_TAG)" --single-branch "$(PG_NET_REPO)" "$(PG_NET_DIR)"; \
	test "$$(git -C "$(PG_NET_DIR)" rev-parse HEAD)" = "$(PG_NET_COMMIT)"; \
	test -f "$(PG_NET_DIR)/Makefile"; \
	touch "$@"

$(INSTALL_PREFIX)/lib/postgresql/pg_net.$(PG_DLSUFFIX): $(POSTGRES_BUNDLE_CONFIG_STAMP) $(PG_NET_SOURCE_VERIFIED) $(INSTALL_PREFIX)/bin/postgres
	unset MAKELEVEL MAKEFLAGS MFLAGS && $(MAKE) -C $(PG_NET_DIR) PG_CONFIG=$(INSTALL_PREFIX)/bin/pg_config CPPFLAGS="$(PG_NET_CURL_CPPFLAGS)" LDFLAGS="$(PG_NET_CURL_LDFLAGS)" -j && $(MAKE) -C $(PG_NET_DIR) PG_CONFIG=$(INSTALL_PREFIX)/bin/pg_config CPPFLAGS="$(PG_NET_CURL_CPPFLAGS)" LDFLAGS="$(PG_NET_CURL_LDFLAGS)" install

$(TIGERFS_CONFIG_STAMP): FORCE
	@set -eu; \
	if test -z "$(TIGERFS_EXPECTED_SHA256)"; then echo 'no pinned SHA-256 for TigerFS $(TIGERFS_OS)/$(TIGERFS_ARCH)' >&2; exit 1; fi; \
	stamp_dir=$$(dirname "$@"); mkdir -p "$$stamp_dir"; \
	tmp="$@.tmp.$$$$"; trap 'rm -f "$$tmp"' EXIT HUP INT TERM; \
	printf '%s\n' 'schema=1' 'recipe=tigerfs-config-stamp-v1' 'tag=$(TIGERFS_TAG)' 'os=$(TIGERFS_OS)' 'arch=$(TIGERFS_ARCH)' 'archive=$(TIGERFS_ARCHIVE)' 'base=$(TIGERFS_BASE)' 'sha256_linux_x86_64=$(TIGERFS_SHA256_Linux_x86_64)' 'sha256_linux_arm64=$(TIGERFS_SHA256_Linux_arm64)' 'sha256_darwin_arm64=$(TIGERFS_SHA256_Darwin_arm64)' 'sha256_darwin_x86_64=$(TIGERFS_SHA256_Darwin_x86_64)' 'expected_sha256=$(TIGERFS_EXPECTED_SHA256)' > "$$tmp"; \
	if cmp -s "$$tmp" "$@"; then exit 0; fi; \
	rm -f "$(INSTALL_PREFIX)/bin/tigerfs"; mv -f "$$tmp" "$@"

$(INSTALL_PREFIX)/bin/tigerfs: $(POSTGRES_BUNDLE_CONFIG_STAMP) $(TIGERFS_CONFIG_STAMP) | $(INSTALL_PREFIX)/bin/postgres
	@set -eu; \
	if test -f "$@"; then exit 0; fi; \
	tmp_dir=".tigerfs-install.$$$$"; tmp_archive="$$tmp_dir/$(TIGERFS_ARCHIVE)"; tmp_bin="$(INSTALL_PREFIX)/bin/.tigerfs.$$$$"; \
	trap 'rm -rf "$$tmp_dir" "$$tmp_bin"' EXIT HUP INT TERM; \
	rm -rf "$$tmp_dir"; mkdir -p "$$tmp_dir"; \
	curl --fail --location --show-error --proto '=https' --proto-redir '=https' --retry 3 -o "$$tmp_archive" "$(TIGERFS_BASE)/$(TIGERFS_ARCHIVE)"; \
	printf '%s  %s\n' "$(TIGERFS_EXPECTED_SHA256)" "$$tmp_archive" | $(SHA256_CMD) -c -; \
	tar xzf "$$tmp_archive" -C "$$tmp_dir" tigerfs; \
	install -m 0755 "$$tmp_dir/tigerfs" "$$tmp_bin"; mv -f "$$tmp_bin" "$@"

bundle-metadata: postgres $(EXTENSIONS)
	rm -f "$(BUNDLE_COMPLETION_MARKER)" "$(INSTALL_PREFIX)/share/pgembed/build-metadata.json"
	python3 ../tools/generate_bundle_metadata.py \
		--install-prefix "$(INSTALL_PREFIX)" \
		--output "$(INSTALL_PREFIX)/share/pgembed/build-metadata.json" \
		--postgres-version "$(POSTGRES_VERSION)" \
		--postgres-ref "$(POSTGRES_SRC_REF)" \
		--postgres-commit "$(POSTGRES_SOURCE_COMMIT)" \
		--configure-flags "$(POSTGRES_CONFIGURE_FLAGS)" \
		--requested "$(REQUESTED_EXTENSIONS)" \
		--built "$(FINAL_EXTENSIONS)" \
		--skipped "$(SKIPPED_EXTENSIONS)" \
		--host-os "$(HOST_OS)" \
		--arch "$(HOST_ARCH)" \
		--libc "$(LIBC_NAME)" \
		--deployment-target "$(MACOSX_DEPLOYMENT_TARGET)" \
		--rust-toolchain "$(RUST_TOOLCHAIN)" \
		--cargo-pgrx-version "$(CARGO_PGRX_VERSION)" \
		--tigerfs-version "$(TIGERFS_TAG)" \
		--tigerfs-sha256 "$(if $(filter tigerfs,$(FINAL_EXTENSIONS)),$(TIGERFS_EXPECTED_SHA256),)"
	@mkdir -p "$$(dirname "$(BUNDLE_COMPLETION_MARKER)")"; tmp="$(BUNDLE_COMPLETION_MARKER).tmp.$$$$"; trap 'rm -f "$$tmp"' EXIT HUP INT TERM; printf '%s\n' 'schema=1' 'postgres=$(POSTGRES_VERSION)' 'metadata=build-metadata.json' > "$$tmp"; mv -f "$$tmp" "$(BUNDLE_COMPLETION_MARKER)"

.PHONY: clean clean-all
clean:
	rm -rf $(INSTALL_PREFIX) $(NATIVE_BUILD_DIRS)
	rm -f tigerfs tigerfs_*.tar.gz tigerfs_*.tar.gz.sha256 $(TIGERFS_CONFIG_STAMP) $(POSTGRES_BUNDLE_CONFIG_STAMP)

clean-all: clean
	rm -f *.tar.gz
