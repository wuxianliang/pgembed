SHELL := /bin/bash
INSTALL_PREFIX := $(shell pwd)/../src/pgembed/pginstall
BUILD := $(shell pwd)/pgbuild/
IS_MUSL ?= $(shell if command -v ldd >/dev/null 2>&1 && ldd --version 2>&1 | grep -qi musl; then echo 1; else echo 0; fi)
# pg_net links -lcurl; probe once so we can skip it gracefully when libcurl is absent.
HAS_LIBCURL ?= $(shell if command -v curl-config >/dev/null 2>&1 || [ "$$(uname -s)" = "Darwin" ]; then echo 1; else echo 0; fi)

# Detect Windows and add .exe extension for executables
ifeq ($(OS),Windows_NT)
    EXE_EXT := .exe
    PG_DLSUFFIX := dll
else ifeq ($(shell uname -s),Darwin)
    PG_DLSUFFIX := dylib
else
    EXE_EXT :=
    PG_DLSUFFIX := so
endif

ifeq ($(OS),Windows_NT)
	RUST_EXTENSIONS_SUPPORTED := 0
else ifeq ($(IS_MUSL),1)
	RUST_EXTENSIONS_SUPPORTED := 0
else
	RUST_EXTENSIONS_SUPPORTED := 1
endif

ifeq ($(RUST_EXTENSIONS_SUPPORTED),1)
EXTENSIONS ?= pgvector vectorchord pg_duckdb age psql_bm25s timescaledb pg_cron pg_net
else
EXTENSIONS ?= pgvector pg_duckdb age psql_bm25s timescaledb pg_cron pg_net
endif
ifeq ($(OS),Windows_NT)
EXTENSIONS := $(filter-out timescaledb pg_cron pg_net pg_duckdb,$(EXTENSIONS))
endif

REQUESTED_EXTENSIONS := $(strip $(EXTENSIONS))
UNSUPPORTED_EXTENSIONS :=
ifeq ($(HAS_LIBCURL),0)
UNSUPPORTED_EXTENSIONS += pg_net
endif
SKIPPED_EXTENSIONS := $(filter $(UNSUPPORTED_EXTENSIONS),$(REQUESTED_EXTENSIONS))
ifneq ($(strip $(SKIPPED_EXTENSIONS)),)
$(warning Skipping unsupported extensions on this platform: $(sort $(SKIPPED_EXTENSIONS)))
endif
EXTENSIONS := $(filter-out $(UNSUPPORTED_EXTENSIONS),$(REQUESTED_EXTENSIONS))

.PHONY: all
all: postgres $(EXTENSIONS)

.PHONY: pgvector pg_duckdb vectorchord age psql_bm25s timescaledb pg_cron pg_net
pgvector: postgres $(INSTALL_PREFIX)/lib/vector.so
pg_duckdb: postgres $(INSTALL_PREFIX)/lib/postgresql/pg_duckdb.$(PG_DLSUFFIX)
vectorchord: postgres $(INSTALL_PREFIX)/lib/vchord.so
age: postgres $(INSTALL_PREFIX)/lib/age.so
psql_bm25s: postgres $(INSTALL_PREFIX)/lib/psql_bm25s.so
timescaledb: postgres $(INSTALL_PREFIX)/lib/postgresql/timescaledb.$(PG_DLSUFFIX)
pg_cron: postgres $(INSTALL_PREFIX)/lib/postgresql/pg_cron.$(PG_DLSUFFIX)
pg_net: postgres $(INSTALL_PREFIX)/lib/postgresql/pg_net.$(PG_DLSUFFIX)

### postgres
POSTGRES_VERSION := 17-stable
POSTGRES_SRC := REL_17_STABLE
POSTGRES_BLD := $(POSTGRES_SRC)


## extract
$(POSTGRES_SRC)/configure:
	git clone https://github.com/postgres/postgres -b $(POSTGRES_SRC) --single-branch $(POSTGRES_SRC)
	touch $(POSTGRES_SRC)/configure

## configure
$(POSTGRES_BLD)/config.status: $(POSTGRES_SRC)/configure
	mkdir -p $(POSTGRES_BLD)
	cd $(POSTGRES_BLD) && ../$(POSTGRES_SRC)/configure --prefix=$(INSTALL_PREFIX) --without-readline --without-icu

## build
# https://stackoverflow.com/questions/68379786/
# for explanation of unsetting make env variables prior to calling postgres' own make
$(POSTGRES_BLD)/src/bin/initdb/initdb: $(POSTGRES_BLD)/config.status
	unset MAKELEVEL && unset MAKEFLAGS && unset MFLAGS && $(MAKE) -C $(POSTGRES_BLD) -j

## install to INSTALL_PREFIX
$(INSTALL_PREFIX)/bin/postgres: $(POSTGRES_BLD)/config.status
	mkdir -p $(INSTALL_PREFIX)
	unset MAKELEVEL && unset MAKEFLAGS && unset MFLAGS && $(MAKE) -C $(POSTGRES_BLD) install

.PHONY: postgres
postgres: $(INSTALL_PREFIX)/bin/postgres

### pgvector
PGVECTOR_TAG := v0.8.2
PGVECTOR_URL := https://github.com/pgvector/pgvector/archive/refs/tags/$(PGVECTOR_TAG).tar.gz
PGVECTOR_DIR := pgvector-$(PGVECTOR_TAG)

$(PGVECTOR_DIR).tar.gz:
	curl -L -o $(PGVECTOR_DIR).tar.gz $(PGVECTOR_URL)

$(PGVECTOR_DIR)/Makefile: $(PGVECTOR_DIR).tar.gz
	# tar extract into pgvector-$(PGVECTOR_TAG)
	mkdir -p $(PGVECTOR_DIR)
	tar xzf $(PGVECTOR_DIR).tar.gz -C $(PGVECTOR_DIR) --strip-components=1
	touch $(PGVECTOR_DIR)/Makefile

$(INSTALL_PREFIX)/lib/vector.so: $(PGVECTOR_DIR)/Makefile $(INSTALL_PREFIX)/bin/postgres
	unset MAKELEVEL && unset MAKEFLAGS && unset MFLAGS \
		&& $(MAKE) -C $(PGVECTOR_DIR) PG_CONFIG=$(INSTALL_PREFIX)/bin/pg_config$(EXE_EXT) -j \
		&& $(MAKE) -C $(PGVECTOR_DIR) PG_CONFIG=$(INSTALL_PREFIX)/bin/pg_config$(EXE_EXT) install

### pg_duckdb
PG_DUCKDB_TAG := v1.1.1
PG_DUCKDB_REPO := https://github.com/duckdb/pg_duckdb
PG_DUCKDB_DIR := pg_duckdb-$(PG_DUCKDB_TAG)
PG_DUCKDB_GEN ?= $(shell if command -v ninja >/dev/null 2>&1; then echo ninja; else echo make; fi)
PG_DUCKDB_LZ4_PREFIX ?= $(shell if [ -d /opt/homebrew/opt/lz4 ]; then echo /opt/homebrew/opt/lz4; elif [ -d /usr/local/opt/lz4 ]; then echo /usr/local/opt/lz4; fi)
PG_DUCKDB_OPENSSL_PREFIX ?= $(shell if [ -d /opt/homebrew/opt/openssl@3 ]; then echo /opt/homebrew/opt/openssl@3; elif [ -d /usr/local/opt/openssl@3 ]; then echo /usr/local/opt/openssl@3; fi)
PG_DUCKDB_LDFLAGS := $(if $(PG_DUCKDB_LZ4_PREFIX),-L$(PG_DUCKDB_LZ4_PREFIX)/lib,) $(if $(PG_DUCKDB_OPENSSL_PREFIX),-L$(PG_DUCKDB_OPENSSL_PREFIX)/lib -lssl -lcrypto,)

$(PG_DUCKDB_DIR)/Makefile:
	git clone --branch $(PG_DUCKDB_TAG) --single-branch $(PG_DUCKDB_REPO) $(PG_DUCKDB_DIR)
	cd $(PG_DUCKDB_DIR) && git submodule update --init --recursive
	touch $(PG_DUCKDB_DIR)/Makefile

$(INSTALL_PREFIX)/lib/postgresql/pg_duckdb.$(PG_DLSUFFIX): $(PG_DUCKDB_DIR)/Makefile $(INSTALL_PREFIX)/bin/postgres
ifneq ($(OS),Windows_NT)
	unset MAKELEVEL && unset MAKEFLAGS && unset MFLAGS \
		&& $(MAKE) -C $(PG_DUCKDB_DIR) PG_CONFIG=$(INSTALL_PREFIX)/bin/pg_config$(EXE_EXT) DUCKDB_BUILD=ReleaseStatic DUCKDB_GEN=$(PG_DUCKDB_GEN) LDFLAGS="$(PG_DUCKDB_LDFLAGS)" install
else
	$(error pg_duckdb build is not supported on Windows due to cargo-pgrx issues)
endif

### vectorchord
VECTORCHORD_TAG := 1.1.1
VECTORCHORD_URL := https://github.com/tensorchord/VectorChord/archive/refs/tags/$(VECTORCHORD_TAG).tar.gz
VECTORCHORD_DIR := vectorchord-$(VECTORCHORD_TAG)

$(VECTORCHORD_DIR).tar.gz:
	curl -L -o $(VECTORCHORD_DIR).tar.gz $(VECTORCHORD_URL)

$(VECTORCHORD_DIR)/Makefile: $(VECTORCHORD_DIR).tar.gz
	mkdir -p $(VECTORCHORD_DIR)
	tar xzf $(VECTORCHORD_DIR).tar.gz -C $(VECTORCHORD_DIR) --strip-components=1
	touch $(VECTORCHORD_DIR)/Makefile

$(INSTALL_PREFIX)/lib/vchord.so: $(VECTORCHORD_DIR)/Makefile $(INSTALL_PREFIX)/bin/postgres
ifeq ($(RUST_EXTENSIONS_SUPPORTED),1)
	unset MAKELEVEL && unset MAKEFLAGS && unset MFLAGS \
	&& export PATH="$$HOME/.cargo/bin:$$PATH" \
	&& export RUSTUP_TOOLCHAIN=nightly \
	&& PG_CONFIG=$(INSTALL_PREFIX)/bin/pg_config$(EXE_EXT) $(MAKE) -C $(VECTORCHORD_DIR) build \
	&& PG_CONFIG=$(INSTALL_PREFIX)/bin/pg_config$(EXE_EXT) $(MAKE) -C $(VECTORCHORD_DIR) install
else ifeq ($(OS),Windows_NT)
	$(error vectorchord build is not supported on Windows due to cargo-pgrx issues)
else
	$(error vectorchord build is not supported on musl libc distributions (e.g., Alpine) due to cargo-pgrx issues)
endif

### age (Apache AGE - Graph Extension)
AGE_TAG := release/PG17/1.7.0
AGE_REPO := https://github.com/apache/age.git
AGE_DIR := age-$(subst /,_,$(AGE_TAG))

$(AGE_DIR)/Makefile:
	git clone --branch $(AGE_TAG) --single-branch --depth 1 $(AGE_REPO) $(AGE_DIR)
	touch $(AGE_DIR)/Makefile

$(INSTALL_PREFIX)/lib/age.so: $(AGE_DIR)/Makefile $(INSTALL_PREFIX)/bin/postgres
	unset MAKELEVEL && unset MAKEFLAGS && unset MFLAGS \
	&& $(MAKE) -C $(AGE_DIR) PG_CONFIG=$(INSTALL_PREFIX)/bin/pg_config$(EXE_EXT) -j \
	&& $(MAKE) -C $(AGE_DIR) PG_CONFIG=$(INSTALL_PREFIX)/bin/pg_config$(EXE_EXT) install

### pg_cron (job scheduler; must be preloaded)
PG_CRON_TAG := v1.6.4
PG_CRON_REPO := https://github.com/citusdata/pg_cron.git
PG_CRON_DIR := pg_cron-$(PG_CRON_TAG)

$(PG_CRON_DIR)/Makefile:
	git clone --branch $(PG_CRON_TAG) --single-branch --depth 1 $(PG_CRON_REPO) $(PG_CRON_DIR)
	touch $(PG_CRON_DIR)/Makefile

# pg_cron must be in shared_preload_libraries; cron.database_name defaults to
# 'postgres', which matches pgembed's default database, so no extra config needed.
$(INSTALL_PREFIX)/lib/postgresql/pg_cron.$(PG_DLSUFFIX): $(PG_CRON_DIR)/Makefile $(INSTALL_PREFIX)/bin/postgres
	unset MAKELEVEL && unset MAKEFLAGS && unset MFLAGS \
	&& $(MAKE) -C $(PG_CRON_DIR) PG_CONFIG=$(INSTALL_PREFIX)/bin/pg_config$(EXE_EXT) -j \
	&& $(MAKE) -C $(PG_CRON_DIR) PG_CONFIG=$(INSTALL_PREFIX)/bin/pg_config$(EXE_EXT) install

### pg_net (async HTTP client; requires libcurl; must be preloaded)
PG_NET_TAG := v0.20.5
PG_NET_REPO := https://github.com/supabase/pg_net.git
PG_NET_DIR := pg_net-$(PG_NET_TAG)
# pg_net's Makefile uses SHLIB_LINK = -lcurl. Probe a homebrew curl prefix so
# headers/libs resolve on macOS; on Linux install libcurl-dev / libcurl-devel.
PG_NET_CURL_PREFIX ?= $(shell if [ -d /opt/homebrew/opt/curl ]; then echo /opt/homebrew/opt/curl; elif [ -d /usr/local/opt/curl ]; then echo /usr/local/opt/curl; fi)
PG_NET_CURL_CPPFLAGS := $(if $(PG_NET_CURL_PREFIX),-I$(PG_NET_CURL_PREFIX)/include,)
PG_NET_CURL_LDFLAGS := $(if $(PG_NET_CURL_PREFIX),-L$(PG_NET_CURL_PREFIX)/lib,)

$(PG_NET_DIR)/Makefile:
	git clone --branch $(PG_NET_TAG) --single-branch --depth 1 $(PG_NET_REPO) $(PG_NET_DIR)
	touch $(PG_NET_DIR)/Makefile

# pg_net is a background worker: it must be in shared_preload_libraries.
$(INSTALL_PREFIX)/lib/postgresql/pg_net.$(PG_DLSUFFIX): $(PG_NET_DIR)/Makefile $(INSTALL_PREFIX)/bin/postgres
ifneq ($(OS),Windows_NT)
	unset MAKELEVEL && unset MAKEFLAGS && unset MFLAGS \
	&& $(MAKE) -C $(PG_NET_DIR) PG_CONFIG=$(INSTALL_PREFIX)/bin/pg_config$(EXE_EXT) \
		CPPFLAGS="$(PG_NET_CURL_CPPFLAGS)" LDFLAGS="$(PG_NET_CURL_LDFLAGS)" -j \
	&& $(MAKE) -C $(PG_NET_DIR) PG_CONFIG=$(INSTALL_PREFIX)/bin/pg_config$(EXE_EXT) \
		CPPFLAGS="$(PG_NET_CURL_CPPFLAGS)" LDFLAGS="$(PG_NET_CURL_LDFLAGS)" install
else
	$(error pg_net build is not supported on Windows by this Makefile)
endif

### psql_bm25s (BM25S Full-Text Search)
PSQL_BM25S_TAG := main
PSQL_BM25S_REPO := https://github.com/Intelligent-Internet/psql_bm25s.git
PSQL_BM25S_DIR := psql_bm25s-$(PSQL_BM25S_TAG)

$(PSQL_BM25S_DIR)/Makefile:
	git clone --branch $(PSQL_BM25S_TAG) --single-branch --depth 1 $(PSQL_BM25S_REPO) $(PSQL_BM25S_DIR)
	touch $(PSQL_BM25S_DIR)/Makefile

$(INSTALL_PREFIX)/lib/psql_bm25s.so: $(PSQL_BM25S_DIR)/Makefile $(INSTALL_PREFIX)/bin/postgres
	unset MAKELEVEL && unset MAKEFLAGS && unset MFLAGS \
	&& $(MAKE) -C $(PSQL_BM25S_DIR) PG_CONFIG=$(INSTALL_PREFIX)/bin/pg_config$(EXE_EXT) MAKEOVERRIDES= LDFLAGS="-L/opt/homebrew/opt/icu4c@78/lib -licui18n -licuuc -licudata" -j \
	&& $(MAKE) -C $(PSQL_BM25S_DIR) PG_CONFIG=$(INSTALL_PREFIX)/bin/pg_config$(EXE_EXT) MAKEOVERRIDES= LDFLAGS="-L/opt/homebrew/opt/icu4c@78/lib -licui18n -licuuc -licudata" install

### timescaledb
TIMESCALEDB_TAG ?= 2.27.1
TIMESCALEDB_URL := https://github.com/timescale/timescaledb/archive/refs/tags/$(TIMESCALEDB_TAG).tar.gz
TIMESCALEDB_DIR := timescaledb-$(TIMESCALEDB_TAG)

$(TIMESCALEDB_DIR).tar.gz:
	curl -L -o $(TIMESCALEDB_DIR).tar.gz $(TIMESCALEDB_URL)

$(TIMESCALEDB_DIR)/bootstrap: $(TIMESCALEDB_DIR).tar.gz
	mkdir -p $(TIMESCALEDB_DIR)
	tar xzf $(TIMESCALEDB_DIR).tar.gz -C $(TIMESCALEDB_DIR) --strip-components=1
	touch $(TIMESCALEDB_DIR)/bootstrap

$(INSTALL_PREFIX)/lib/postgresql/timescaledb.$(PG_DLSUFFIX): $(TIMESCALEDB_DIR)/bootstrap $(INSTALL_PREFIX)/bin/postgres
ifneq ($(OS),Windows_NT)
	unset MAKELEVEL && unset MAKEFLAGS && unset MFLAGS \
		&& export PATH="$(INSTALL_PREFIX)/bin:$$PATH" \
		&& cd $(TIMESCALEDB_DIR) \
		&& BUILD_FORCE_REMOVE=true ./bootstrap -DUSE_OPENSSL=0 \
		&& $(MAKE) -C build -j \
		&& $(MAKE) -C build install
else
	$(error timescaledb build is not supported by this Makefile on Windows)
endif

### other
.PHONY: clean clean-all
clean:
	rm -rf $(INSTALL_PREFIX)
	rm -rf $(POSTGRES_SRC)
	rm -rf $(POSTGRES_BLD)
	rm -rf $(PGVECTOR_DIR)
	rm -rf $(PG_DUCKDB_DIR)
	rm -rf $(VECTORCHORD_DIR)
	rm -rf $(AGE_DIR)
	rm -rf $(PSQL_BM25S_DIR)
	rm -rf $(TIMESCALEDB_DIR)
	rm -rf $(PG_CRON_DIR)
	rm -rf $(PG_NET_DIR)

clean-all: clean
	rm -rf *.tar.gz
