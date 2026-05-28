SHELL := /bin/bash
INSTALL_PREFIX := $(shell pwd)/../src/pgembed/pginstall
BUILD := $(shell pwd)/pgbuild/
IS_MUSL ?= $(shell if command -v ldd >/dev/null 2>&1 && ldd --version 2>&1 | grep -qi musl; then echo 1; else echo 0; fi)

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
EXTENSIONS ?= pgvector pgvectorscale pgtextsearch vectorchord graph age psql_bm25s timescaledb
else
EXTENSIONS ?= pgvector age psql_bm25s timescaledb
endif
ifeq ($(OS),Windows_NT)
EXTENSIONS := $(filter-out timescaledb,$(EXTENSIONS))
endif
ifneq ($(OS),Windows_NT)
EXTENSIONS ?= $(EXTENSIONS) pg_duckdb
endif

REQUESTED_EXTENSIONS := $(strip $(EXTENSIONS))
UNSUPPORTED_EXTENSIONS :=
ifeq ($(IS_MUSL),1)
UNSUPPORTED_EXTENSIONS += pgvectorscale pgtextsearch pg_search
endif
SKIPPED_EXTENSIONS := $(filter $(UNSUPPORTED_EXTENSIONS),$(REQUESTED_EXTENSIONS))
ifneq ($(strip $(SKIPPED_EXTENSIONS)),)
$(warning Skipping unsupported extensions on this platform: $(sort $(SKIPPED_EXTENSIONS)))
endif
EXTENSIONS := $(filter-out $(UNSUPPORTED_EXTENSIONS),$(REQUESTED_EXTENSIONS))

.PHONY: all
all: postgres $(EXTENSIONS)

.PHONY: pgvector pgvectorscale pgtextsearch pg_search pg_duckdb vectorchord graph age psql_bm25s timescaledb
pgvector: postgres $(INSTALL_PREFIX)/lib/vector.so
pgvectorscale: postgres $(INSTALL_PREFIX)/lib/vectorscale.so
pgtextsearch: postgres $(INSTALL_PREFIX)/lib/pg_textsearch.so
pg_search: postgres $(INSTALL_PREFIX)/lib/pg_search.so
pg_duckdb: postgres $(INSTALL_PREFIX)/lib/postgresql/pg_duckdb.$(PG_DLSUFFIX)
vectorchord: postgres $(INSTALL_PREFIX)/lib/vchord.so
graph: postgres $(INSTALL_PREFIX)/lib/graph.so
age: postgres $(INSTALL_PREFIX)/lib/age.so
psql_bm25s: postgres $(INSTALL_PREFIX)/lib/psql_bm25s.so
timescaledb: postgres $(INSTALL_PREFIX)/lib/postgresql/timescaledb.$(PG_DLSUFFIX)

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

### graph
PGGRAPH_TAG := v0.1.3
PGGRAPH_REF := 9fc4fb2121fbaed0352dfddc8ab5b09e153bb30c
PGGRAPH_URL := https://github.com/evokoa/pggraph/archive/$(PGGRAPH_REF).tar.gz
PGGRAPH_DIR := pggraph-$(PGGRAPH_TAG)
PGGRAPH_CRATE_DIR := $(PGGRAPH_DIR)/graph

$(PGGRAPH_DIR).tar.gz:
	curl -L -o $(PGGRAPH_DIR).tar.gz $(PGGRAPH_URL)

$(PGGRAPH_CRATE_DIR)/Cargo.toml: $(PGGRAPH_DIR).tar.gz
	mkdir -p $(PGGRAPH_DIR)
	tar xzf $(PGGRAPH_DIR).tar.gz -C $(PGGRAPH_DIR) --strip-components=1
	touch $(PGGRAPH_CRATE_DIR)/Cargo.toml

$(INSTALL_PREFIX)/lib/graph.so: $(PGGRAPH_CRATE_DIR)/Cargo.toml $(INSTALL_PREFIX)/bin/postgres
ifeq ($(RUST_EXTENSIONS_SUPPORTED),1)
	unset MAKELEVEL && unset MAKEFLAGS && unset MFLAGS \
		&& export PATH="$$HOME/.cargo/bin:$$PATH" \
		&& export RUSTUP_TOOLCHAIN=1.95.0 \
		&& cd $(PGGRAPH_CRATE_DIR) \
		&& cargo pgrx init --pg17 $(INSTALL_PREFIX)/bin/pg_config$(EXE_EXT) \
		&& cargo pgrx install --release --features pg17 --no-default-features --pg-config $(INSTALL_PREFIX)/bin/pg_config$(EXE_EXT)
else ifeq ($(OS),Windows_NT)
	$(error graph build is not supported on Windows due to cargo-pgrx issues)
else
	$(error graph build is not supported on musl libc distributions (e.g., Alpine) due to cargo-pgrx issues)
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

### psql_bm25s (BM25S Full-Text Search)
PSQL_BM25S_TAG := main
PSQL_BM25S_REPO := https://github.com/Intelligent-Internet/psql_bm25s.git
PSQL_BM25S_DIR := psql_bm25s-$(PSQL_BM25S_TAG)

$(PSQL_BM25S_DIR)/Makefile:
	git clone --branch $(PSQL_BM25S_TAG) --single-branch --depth 1 $(PSQL_BM25S_REPO) $(PSQL_BM25S_DIR)
	sed -i '' 's|SHLIB_LINK += $$(ICU_LIBS)|SHLIB_LINK += -L/opt/homebrew/opt/icu4c@78/lib -licui18n -licuuc -licudata|' $(PSQL_BM25S_DIR)/Makefile
	sed -i '' 's|ICU_CPPFLAGS.*|PG_CPPFLAGS = -I/opt/homebrew/opt/icu4c@78/include -I$$(CURDIR)/src|' $(PSQL_BM25S_DIR)/Makefile
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

### pgvectorscale
PGVECTORSCALE_TAG := 0.9.0
PGVECTORSCALE_URL := https://github.com/timescale/pgvectorscale/archive/refs/tags/$(PGVECTORSCALE_TAG).tar.gz
PGVECTORSCALE_DIR := pgvectorscale-$(PGVECTORSCALE_TAG)

$(PGVECTORSCALE_DIR).tar.gz:
	curl -L -o $(PGVECTORSCALE_DIR).tar.gz $(PGVECTORSCALE_URL)

$(PGVECTORSCALE_DIR)/pgvectorscale/Cargo.toml: $(PGVECTORSCALE_DIR).tar.gz
	mkdir -p $(PGVECTORSCALE_DIR)
	tar xzf $(PGVECTORSCALE_DIR).tar.gz -C $(PGVECTORSCALE_DIR) --strip-components=1
	touch $(PGVECTORSCALE_DIR)/pgvectorscale/Cargo.toml

$(INSTALL_PREFIX)/lib/vectorscale.so: $(PGVECTORSCALE_DIR)/pgvectorscale/Cargo.toml $(INSTALL_PREFIX)/bin/postgres
ifeq ($(RUST_EXTENSIONS_SUPPORTED),1)
	unset MAKELEVEL && unset MAKEFLAGS && unset MFLAGS \
		&& export PATH="$$HOME/.cargo-pgrx-0.17.0/bin:$(INSTALL_PREFIX)/bin:$$HOME/.cargo/bin:$$PATH" \
		&& cd $(PGVECTORSCALE_DIR)/pgvectorscale \
		&& sed -i '' 's/pgrx = "=0.16.1"/pgrx = "=0.17.0"/' Cargo.toml \
		&& sed -i '' 's/pgrx-tests = "=0.16.1"/pgrx-tests = "=0.17.0"/' Cargo.toml \
		&& sed -i '' 's/pgrx-pg-config = "=0.16.1"/pgrx-pg-config = "=0.17.0"/' Cargo.toml \
		&& RUSTUP_TOOLCHAIN=nightly cargo pgrx init --pg17 $(INSTALL_PREFIX)/bin/pg_config$(EXE_EXT) \
		&& RUSTUP_TOOLCHAIN=nightly cargo pgrx install --release
else ifeq ($(OS),Windows_NT)
	$(error pgvectorscale build is not supported on Windows due to cargo-pgrx issues)
else
	$(error pgvectorscale build is not supported on musl libc distributions (e.g., Alpine) due to cargo-pgrx issues)
endif

### pgtextsearch
PGTEXTSEARCH_TAG := v1.0.0
PGTEXTSEARCH_URL := https://github.com/timescale/pg_textsearch/archive/refs/tags/$(PGTEXTSEARCH_TAG).tar.gz
PGTEXTSEARCH_DIR := pgtextsearch-$(PGTEXTSEARCH_TAG)

$(PGTEXTSEARCH_DIR).tar.gz:
	curl -L -o $(PGTEXTSEARCH_DIR).tar.gz $(PGTEXTSEARCH_URL)

$(PGTEXTSEARCH_DIR)/Makefile: $(PGTEXTSEARCH_DIR).tar.gz
	mkdir -p $(PGTEXTSEARCH_DIR)
	tar xzf $(PGTEXTSEARCH_DIR).tar.gz -C $(PGTEXTSEARCH_DIR) --strip-components=1
	touch $(PGTEXTSEARCH_DIR)/Makefile

$(INSTALL_PREFIX)/lib/pg_textsearch.so: $(PGTEXTSEARCH_DIR)/Makefile $(INSTALL_PREFIX)/bin/postgres
ifeq ($(RUST_EXTENSIONS_SUPPORTED),1)
	unset MAKELEVEL && unset MAKEFLAGS && unset MFLAGS \
		&& $(MAKE) -C $(PGTEXTSEARCH_DIR) PG_CONFIG=$(INSTALL_PREFIX)/bin/pg_config$(EXE_EXT) -j \
		&& $(MAKE) -C $(PGTEXTSEARCH_DIR) PG_CONFIG=$(INSTALL_PREFIX)/bin/pg_config$(EXE_EXT) install
else ifeq ($(OS),Windows_NT)
	$(error pgtextsearch build is not supported on Windows due to cargo-pgrx issues)
else
	$(error pgtextsearch build is not supported on musl libc distributions (e.g., Alpine) due to cargo-pgrx issues)
endif

### pg_search
PGSEARCH_TAG ?= v0.21.14
PGSEARCH_URL := https://github.com/paradedb/paradedb/archive/refs/tags/$(PGSEARCH_TAG).tar.gz
PGSEARCH_DIR := paradedb-$(PGSEARCH_TAG)
PGSEARCH_SUBDIR := $(PGSEARCH_DIR)/pg_search

$(PGSEARCH_DIR).tar.gz:
	curl -L -o $(PGSEARCH_DIR).tar.gz $(PGSEARCH_URL)

$(PGSEARCH_SUBDIR)/Cargo.toml: $(PGSEARCH_DIR).tar.gz
	mkdir -p $(PGSEARCH_DIR)
	tar xzf $(PGSEARCH_DIR).tar.gz -C $(PGSEARCH_DIR) --strip-components=1
	touch $(PGSEARCH_SUBDIR)/Cargo.toml

$(INSTALL_PREFIX)/lib/pg_search.so: $(PGSEARCH_SUBDIR)/Cargo.toml $(INSTALL_PREFIX)/bin/postgres
ifeq ($(RUST_EXTENSIONS_SUPPORTED),1)
	unset MAKELEVEL && unset MAKEFLAGS && unset MFLAGS \
		&& export PATH="$$HOME/.cargo/bin:$$PATH" \
		&& cd $(PGSEARCH_SUBDIR) \
		&& RUSTUP_TOOLCHAIN=nightly cargo pgrx init --pg17 $(INSTALL_PREFIX)/bin/pg_config$(EXE_EXT) \
		&& RUSTUP_TOOLCHAIN=nightly cargo pgrx install --release
else ifeq ($(OS),Windows_NT)
	$(error pg_search build is not supported on Windows due to cargo-pgrx issues)
else
	$(error pg_search build is not supported on musl libc distributions (e.g., Alpine) due to cargo-pgrx issues)
endif

### other
.PHONY: clean clean-all
clean:
	rm -rf $(INSTALL_PREFIX)
	rm -rf $(POSTGRES_SRC)
	rm -rf $(POSTGRES_BLD)
	rm -rf $(PGVECTOR_DIR)
	rm -rf $(PGVECTORSCALE_DIR)
	rm -rf $(PG_DUCKDB_DIR)
	rm -rf $(PGTEXTSEARCH_DIR)
	rm -rf $(PGSEARCH_DIR)
	rm -rf $(VECTORCHORD_DIR)
	rm -rf $(AGE_DIR)
	rm -rf $(PSQL_BM25S_DIR)
	rm -rf $(TIMESCALEDB_DIR)

clean-all: clean
	rm -rf *.tar.gz
