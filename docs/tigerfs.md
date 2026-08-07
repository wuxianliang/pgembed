# TigerFS with pgembed

[TigerFS](https://github.com/timescale/tigerfs) mounts a PostgreSQL database as a filesystem, so the same data can be accessed through SQL and ordinary file tools such as `ls`, `cat`, and `grep`. With pgembed, TigerFS runs beside the embedded PostgreSQL server and connects to it using the server's normal PostgreSQL connection URI. Examples in this guide are pinned to TigerFS **v0.7.0**.

## 1. Overview

TigerFS supports two complementary workflows:

- **Data-first:** mount an existing database and explore tables as directories and rows as files. TigerFS paths can also express SQL-backed pipelines such as `.by/<column>/<value>`, `.order/<column>`, `.last/<count>`, and `.export/<format>`.
- **File-first:** write files, including Markdown with frontmatter, through TigerFS `.build/` workspaces. The writes use the same transactional database and can be versioned and reversed through features such as `.history/`, `.savepoint/`, and `.undo/`.

> **TigerFS is a new category in pgembed—not a PostgreSQL extension.**
>
> TigerFS is a standalone client daemon. Its single `tigerfs` executable is bundled in `pginstall/bin/` beside `postgres`, `initdb`, and `psql`; it is not installed into PostgreSQL's extension directories and is never activated with `CREATE EXTENSION`. At runtime, TigerFS runs as a separate long-lived process, connects to PostgreSQL as a client, and mounts the database as a filesystem while normal SQL access continues. Do not expect `pgembed.has_extension("tigerfs")`, `CREATE EXTENSION tigerfs`, or an entry in `EXTENSION_NAMES`.

The runtime binary is discovered through:

```python
import pgembed

bin_tigerfs = pgembed.POSTGRES_BIN_PATH / "tigerfs"
```

There is no `pgembed.tigerfs()` callable and no `pgembed.TIGERFS_BIN_PATH` constant. Invoke the bundled executable at `pgembed.POSTGRES_BIN_PATH / "tigerfs"` with `subprocess.Popen`; the lifecycle-aware `mount_filesystem()` convenience API remains deferred.

## 2. Platform support and prerequisites

| Environment | Mount backend | Requirements and limitations |
|---|---|---|
| **macOS arm64** | Userspace NFS server plus `/sbin/mount_nfs` | This is the only macOS release target. The current build uses deployment target **26.0**; no Intel, universal2, or older macOS compatibility is claimed. The macOS NFS client is built in, and TigerFS uses `noresvport` so the mount can be created by a non-root user. |
| **Linux x86_64/aarch64** | Kernel FUSE through `/dev/fuse` | These are the Linux release targets. Root is not required, but the user must be in the `fuse` group and must have access to `/dev/fuse`. The kernel FUSE module and device must be available. TigerFS talks to `/dev/fuse` directly and does not require `libfuse` headers or a dynamically linked `libfuse`. |
| **Containers and sandboxes** | Host-provided mount facility | The core database and bundled-tool checks do not require FUSE. Linux filesystem mounts normally need `--device /dev/fuse --cap-add SYS_ADMIN` (or equivalent host access); locked-down environments may not permit them. |

These limitations apply to the **TigerFS filesystem mount**, not to pgembed as a whole. The core embedded PostgreSQL database continues to work in pgembed's supported environments even when the host does not permit FUSE or TigerFS has no platform binary.

> **File-first history has additional database prerequisites.** TigerFS v0.7.0 history, log, savepoints, and undo require TimescaleDB. The tested pgembed PostgreSQL 18.4 bundle must start with `shared_preload_libraries="timescaledb"` and activate TimescaleDB in the workspace database. TigerFS v0.7.0 calls unqualified `uuidv7()`; on a fresh PG18 cluster this resolves to PostgreSQL's native `pg_catalog.uuidv7()` and no compatibility wrapper is needed. Ordinary TigerFS users should follow the generic setup below.

Quick prerequisite checks:

```bash
case "$(uname -s)" in
  Linux)
    test -e /dev/fuse || { echo "Missing /dev/fuse" >&2; exit 1; }
    test -r /dev/fuse -a -w /dev/fuse || {
      echo "/dev/fuse is not accessible to this user" >&2
      exit 1
    }
    id -nG | tr ' ' '\n' | grep -qx fuse || {
      echo "User is not in the fuse group" >&2
      exit 1
    }
    ;;
  Darwin)
    test -x /sbin/mount_nfs || {
      echo "Missing /sbin/mount_nfs" >&2
      exit 1
    }
    ;;
  *)
    echo "TigerFS mounts are unsupported on this platform" >&2
    exit 1
    ;;
esac
```

Group-membership changes commonly require a new login session before they take effect.

### 2.1 File-first history database setup

Start the pgembed server with TimescaleDB preloaded before creating the workspace database:

```python
from pathlib import Path

import pgembed

server = pgembed.get_server(
    Path("pgdata"),
    shared_preload_libraries="timescaledb",
)
workspace_uri = server.get_uri("tigerfs_workspace")
```

Create `tigerfs_workspace`, connect to it, and activate TimescaleDB:

```sql
CREATE EXTENSION IF NOT EXISTS timescaledb;
```

PostgreSQL 18 provides native UUIDv7 generation in `pg_catalog.uuidv7()`. Fresh PG18 clusters should use that implementation directly and should **not** create a `public.uuidv7()` compatibility wrapper:

```sql
SELECT pg_catalog.uuidv7();
SELECT substr(pg_catalog.uuidv7()::text, 15, 1); -- UUID version nibble: 7
SELECT to_regprocedure('public.uuidv7()');       -- NULL on a fresh cluster
```

For a database migrated from the former PostgreSQL 17 + TimescaleDB setup, inventory historical wrappers before changing anything:

```sql
SELECT
	to_regprocedure('pg_catalog.uuidv7()') AS native_uuidv7,
	to_regprocedure('public.uuidv7()') AS legacy_public_uuidv7,
	to_regprocedure('public.generate_uuidv7()') AS legacy_timescaledb_generator;
```

A `public.uuidv7()` or `public.generate_uuidv7()` object may be application-owned. Never use `CREATE OR REPLACE`, `DROP`, or an automated migration merely because its name matches the old guidance. Record its owner and definition (`pg_get_functiondef(...)`), test callers with the migrated search path, and remove or retain it only as an explicit application migration. TigerFS on the PG18 bundle is tested against the native `pg_catalog.uuidv7()` contract; compatibility shims are migration-history guidance only.

## 3. Build and install order

TigerFS is distributed as a small, prebuilt static Go binary. Installing it does not require a Go toolchain, CGO, `libfuse` development headers, or compilation against PostgreSQL. It is also independent of the PostgreSQL major-version ABI because it connects as a SQL client rather than loading into the PostgreSQL process.

The `tigerfs` target in pgembed's `pgbuild/Makefile` downloads the matching prebuilt binary over HTTPS, verifies it against a **pinned** SHA-256 that lives in the Makefile itself (an independent trust anchor — the mutable GitHub release `.sha256` sidecar is *not* the root of trust), and installs it into `pginstall/bin`. A stable, content-checked `TIGERFS_CONFIG_STAMP` records the recipe revision, tag, platform, archive/base URL, all four pinned digests, and the selected digest. A configuration change removes the installed binary and atomically replaces the stamp; unchanged content preserves the stamp mtime. The installed executable has normal prerequisites on both the PostgreSQL bundle stamp and TigerFS stamp, plus an order-only prerequisite on the installed `postgres` executable; the phony `tigerfs` target also depends on `postgres`.

The excerpt below mirrors the final dependency shape. For readability, the four literal digest assignments and their four repeated stamp lines are represented by comments; they are present in the Makefile and covered by `tests/test_tigerfs_build.py`.

```makefile
# The final Makefile selects TigerFS when it is in EXTENSIONS or is an explicit goal.
TIGERFS_CONFIG_STAMP ?= .tigerfs-config.stamp
TIGERFS_SELECTED := $(filter tigerfs,$(EXTENSIONS) $(MAKECMDGOALS))

ifneq ($(strip $(TIGERFS_SELECTED)),)
TIGERFS_TAG  ?= v0.7.0
TIGERFS_OS   ?= $(HOST_OS)
TIGERFS_ARCH ?= $(if $(filter arm64 aarch64,$(shell uname -m)),arm64,$(if $(filter x86_64 amd64,$(shell uname -m)),x86_64,$(error unsupported TIGERFS_ARCH for tigerfs: $(shell uname -m))))
TIGERFS_ARCHIVE := tigerfs_$(TIGERFS_OS)_$(TIGERFS_ARCH).tar.gz
TIGERFS_BASE := https://github.com/timescale/tigerfs/releases/download/$(TIGERFS_TAG)
# Four pinned archive SHA-256 values are defined in the Makefile.
TIGERFS_EXPECTED_SHA256 := $(TIGERFS_SHA256_$(TIGERFS_OS)_$(TIGERFS_ARCH))
ifeq ($(TIGERFS_EXPECTED_SHA256),)
$(error no pinned SHA-256 for tigerfs $(TIGERFS_OS)/$(TIGERFS_ARCH) (tag $(TIGERFS_TAG)))
endif

.PHONY: FORCE
FORCE:

$(TIGERFS_CONFIG_STAMP): FORCE
	@set -eu; \
	stamp_dir=$$(dirname "$@"); mkdir -p "$$stamp_dir"; \
	tmp="$@.tmp.$$$$"; trap 'rm -f "$$tmp"' EXIT HUP INT TERM; \
	{ printf '%s\n' 'schema=1' 'recipe=tigerfs-config-stamp-v1' \
		'tag=$(TIGERFS_TAG)' 'os=$(TIGERFS_OS)' 'arch=$(TIGERFS_ARCH)' \
		'archive=$(TIGERFS_ARCHIVE)' 'base=$(TIGERFS_BASE)' \
		'expected_sha256=$(TIGERFS_EXPECTED_SHA256)'; } > "$$tmp"; \
	if cmp -s "$$tmp" "$@"; then exit 0; fi; \
	rm -f "$(INSTALL_PREFIX)/bin/tigerfs"; mv -f "$$tmp" "$@"

$(INSTALL_PREFIX)/bin/tigerfs: $(POSTGRES_BUNDLE_CONFIG_STAMP) $(TIGERFS_CONFIG_STAMP) | $(INSTALL_PREFIX)/bin/postgres
	@set -eu; \
	if test -f "$(INSTALL_PREFIX)/bin/tigerfs"; then exit 0; fi; \
	tmp_dir=".tigerfs-install.$$$$"; tmp_archive="$$tmp_dir/$(TIGERFS_ARCHIVE)"; \
	tmp_bin="$(INSTALL_PREFIX)/bin/.tigerfs.$$$$"; \
	trap 'rm -rf "$$tmp_dir" "$$tmp_bin"' EXIT HUP INT TERM; \
	mkdir -p "$$tmp_dir"; \
	curl --fail --location --show-error --proto '=https' --proto-redir '=https' --retry 3 \
		-o "$$tmp_archive" "$(TIGERFS_BASE)/$(TIGERFS_ARCHIVE)"; \
	printf '%s  %s\n' "$(TIGERFS_EXPECTED_SHA256)" "$$tmp_archive" | $(TIGERFS_SHA256_CMD) -c -; \
	tar xzf "$$tmp_archive" -C "$$tmp_dir" tigerfs; \
	install -m 0755 "$$tmp_dir/tigerfs" "$$tmp_bin"; \
	mv -f "$$tmp_bin" "$(INSTALL_PREFIX)/bin/tigerfs"

tigerfs: postgres $(INSTALL_PREFIX)/bin/tigerfs
```

### Platform-to-asset mapping

The target's native `uname` detection maps supported wheel-build hosts to the v0.7.0 release assets as follows:

| Build platform | Release asset |
|---|---|
| manylinux x86_64 | `tigerfs_Linux_x86_64.tar.gz` |
| manylinux aarch64 | `tigerfs_Linux_arm64.tar.gz` |
| macOS arm64 | `tigerfs_Darwin_arm64.tar.gz` |

Verification uses the **pinned** digest selected for the current OS/architecture (`TIGERFS_SHA256_<os>_<arch>` in the snippet above), not the release `.sha256` sidecar. The recipe reconstructs a `<hash>  <file>` line and feeds it to the platform checksum tool via stdin, so the same recipe works on Linux and macOS:

```bash
printf '%s  %s\n' "$EXPECTED" "tigerfs_Linux_x86_64.tar.gz" | sha256sum     -c -   # Linux
printf '%s  %s\n' "$EXPECTED" "tigerfs_Darwin_arm64.tar.gz"  | shasum -a 256 -c -   # macOS
```

If you bump `TIGERFS_TAG`, update the four pinned digests to match the new release. Unknown OS/architecture combinations fail loudly at parse time rather than silently downloading an incompatible binary. The release workflow invokes the target only for the Darwin/Linux matrix above.

### Build choices

From the pgbuild directory, install TigerFS explicitly after the PostgreSQL runtime exists:

```bash
make tigerfs
```

It may also remain opt-in as part of an explicit target set:

```bash
make EXTENSIONS="<existing-targets> tigerfs" all
```

Although the variable is currently named `EXTENSIONS`, `tigerfs` is only a Make target in that list; it does not become a PostgreSQL extension. The current default target set and release workflow select `tigerfs` explicitly on supported Darwin/Linux builds; there is no separate `BUNDLED_TOOLS` Make variable yet.

After installation, verify the result:

```bash
test -x ../src/pgembed/pginstall/bin/tigerfs
../src/pgembed/pginstall/bin/tigerfs version
```

The installed path is:

```text
src/pgembed/pginstall/bin/tigerfs
```

That directory is included in the pgembed package, so the executable ships inside the wheel like the other bundled PostgreSQL tools. Building TigerFS from source is possible upstream, but it is heavier and is not the pgembed packaging path described here.

## 4. Functional tests

This section is a runnable release-validation checklist for supported hosts. It tests the binary, host prerequisites, a real pgembed connection, a mount, concurrent SQL/filesystem visibility, and deliberate cleanup. Run subsections 4.1–4.6 in the same Bash shell, from an environment in which pgembed and `psycopg2` are installed. The cleanup trap installed in 4.3 protects every later failure path.

### 4.1 Check the bundled binary and version

The following binary-presence command is suitable for CI:

```bash
test -x "$(python -c 'import pgembed,pathlib;print(pathlib.Path(pgembed.POSTGRES_BIN_PATH)/"tigerfs")')"
```

Export the path and check the version:

```bash
export TIGERFS_BIN="$(python -c 'import pgembed,pathlib;print(pathlib.Path(pgembed.POSTGRES_BIN_PATH)/"tigerfs")')"
"$TIGERFS_BIN" version
```

**Expected:** the executable exists and reports TigerFS v0.7.0.

### 4.2 Check the platform mount facility

On Linux:

```bash
test -e /dev/fuse
test -r /dev/fuse -a -w /dev/fuse
id -nG | tr ' ' '\n' | grep -qx fuse
```

On macOS:

```bash
test -x /sbin/mount_nfs
```

**Expected:** every command for the current platform exits with status zero. Skip the mount tests—not the pgembed database tests—when a container or sandbox does not expose the required mount facility.

### 4.3 Start pgembed and capture its URI

The following helper keeps the pgembed server context alive until it receives `SIGTERM` or `SIGINT`:

```bash
set -euo pipefail

export TEST_ROOT="$(mktemp -d)"
export PGDATA="$TEST_ROOT/pgdata"
export URI_FILE="$TEST_ROOT/uri"
export MOUNT_DIR="$TEST_ROOT/mnt"
mkdir -p "$MOUNT_DIR"

export PGEMBED_PID=""
export TIGERFS_PID=""

is_mounted() {
  python - "$MOUNT_DIR" <<'PY'
import os
import sys

raise SystemExit(0 if os.path.ismount(sys.argv[1]) else 1)
PY
}

wait_for_pid_exit() {
  local pid="$1"
  for _ in $(seq 1 50); do
    kill -0 "$pid" 2>/dev/null || return 0
    sleep 0.1
  done
  return 1
}

stop_and_wait() {
  local pid="$1"
  kill -0 "$pid" 2>/dev/null || {
    wait "$pid" 2>/dev/null || true
    return
  }
  kill -TERM "$pid" 2>/dev/null || true
  if ! wait_for_pid_exit "$pid"; then
    kill -KILL "$pid" 2>/dev/null || true
  fi
  wait "$pid" 2>/dev/null || true
}

cleanup() {
  local failed=0

  if [ -n "${TIGERFS_PID:-}" ]; then
    if is_mounted; then
      "$TIGERFS_BIN" unmount --timeout 5 "$MOUNT_DIR" \
        >/dev/null 2>&1 || true
      for _ in $(seq 1 50); do
        is_mounted || break
        sleep 0.1
      done
    fi

    if is_mounted; then
      case "$(uname -s)" in
        Darwin) diskutil unmount force "$MOUNT_DIR" >/dev/null 2>&1 || true ;;
        Linux)  fusermount -u "$MOUNT_DIR" >/dev/null 2>&1 || true ;;
      esac
    fi

    if ! wait_for_pid_exit "$TIGERFS_PID"; then
      stop_and_wait "$TIGERFS_PID"
    else
      wait "$TIGERFS_PID" 2>/dev/null || true
    fi
  fi

  if is_mounted; then
    echo "TigerFS mount still present after cleanup: $MOUNT_DIR" >&2
    failed=1
  fi

  if [ -n "${PGEMBED_PID:-}" ]; then
    stop_and_wait "$PGEMBED_PID"
  fi

  return "$failed"
}

trap 'exit 130' INT
trap 'exit 143' TERM
trap cleanup EXIT

python - "$PGDATA" "$URI_FILE" <<'PY' &
import pathlib
import signal
import sys
import threading

import pgembed

stop = threading.Event()
signal.signal(signal.SIGINT, lambda *_: stop.set())
signal.signal(signal.SIGTERM, lambda *_: stop.set())

with pgembed.get_server(sys.argv[1]) as server:
    pathlib.Path(sys.argv[2]).write_text(server.get_uri("postgres"))
    stop.wait()
PY
export PGEMBED_PID=$!

for _ in $(seq 1 100); do
  test -s "$URI_FILE" && break
  sleep 0.1
done

test -s "$URI_FILE"
export PGURI="$(cat "$URI_FILE")"
printf 'pgembed URI: %s\n' "$PGURI"
```

On Unix, `server.get_uri("postgres")` normally returns a local socket URI such as `postgresql://postgres:@/postgres?host=/path/to/socket_dir`. TigerFS accepts this URI directly and treats the local connection as non-SSL.

### 4.4 Create known SQL data, mount it, and read a row file

Create a deterministic row through SQL:

```bash
python - <<'PY'
import os
import psycopg2

with psycopg2.connect(os.environ["PGURI"]) as conn:
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS public.tigerfs_doc_test (
                id bigint PRIMARY KEY,
                body text NOT NULL
            )
        """)
        cur.execute("""
            INSERT INTO public.tigerfs_doc_test (id, body)
            VALUES (1, 'hello-from-sql')
            ON CONFLICT (id) DO UPDATE SET body = EXCLUDED.body
        """)
PY
```

Start the standalone TigerFS daemon in the background. `--foreground` is required here so the captured PID owns the mount lifecycle. Wait for the real mount state—not merely for the pre-created directory to become listable—and fail if the daemon exits or readiness times out:

```bash
"$TIGERFS_BIN" mount --foreground "$PGURI" "$MOUNT_DIR" \
  >"$TEST_ROOT/tigerfs.log" 2>&1 &
export TIGERFS_PID=$!

MOUNT_READY=0
for _ in $(seq 1 100); do
  if is_mounted; then
    MOUNT_READY=1
    break
  fi
  if ! kill -0 "$TIGERFS_PID" 2>/dev/null; then
    cat "$TEST_ROOT/tigerfs.log" >&2
    wait "$TIGERFS_PID" 2>/dev/null || true
    exit 1
  fi
  sleep 0.1
done

if [ "$MOUNT_READY" -ne 1 ]; then
  echo "TigerFS mount did not become ready: $MOUNT_DIR" >&2
  cat "$TEST_ROOT/tigerfs.log" >&2
  exit 1
fi

ls -la "$MOUNT_DIR"
export ROW_DIR="$MOUNT_DIR/tigerfs_doc_test/1"
export ROW_FILE="$ROW_DIR/body.txt"
test -d "$ROW_DIR"
test -f "$ROW_FILE"
grep -Fxq -- 'hello-from-sql' "$ROW_FILE"
printf 'row file: %s\n' "$ROW_FILE"
cat "$ROW_FILE"
```

**Expected:** the mount is listable, the deterministic row directory exists, and `body.txt` contains the unique value inserted through SQL. TigerFS v0.7.0 exposes the public table at `tigerfs_doc_test/1` in this data-first view.

### 4.5 Verify SQL-to-filesystem and filesystem-to-SQL concurrency

The preceding step proves the SQL-to-filesystem direction: the row inserted with `psycopg2` is visible without copying or exporting the database.

For the reverse direction, write through a writable TigerFS file representation and then query the same database. If the data-first row file found above is writable in the mounted schema, update only the unique text value so the row's serialization remains valid:

```bash
python - "$ROW_FILE" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
contents = path.read_text()
assert "hello-from-sql" in contents
path.write_text(contents.replace("hello-from-sql", "hello-from-filesystem", 1))
PY
```

Then verify the change through SQL:

```bash
python - <<'PY'
import os
import psycopg2

with psycopg2.connect(os.environ["PGURI"]) as conn:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT body FROM public.tigerfs_doc_test WHERE id = %s",
            (1,),
        )
        value = cur.fetchone()[0]
        assert value == "hello-from-filesystem", value
        print(value)
PY
```

**Expected:** SQL prints `hello-from-filesystem`. The TigerFS daemon and the SQL client are operating on one transactional PostgreSQL database, not on synchronized copies.

If a particular data-first view is intentionally read-only, perform the write half through the writable `.build/` workspace described in the file-first usage example below, then query the mapped table through SQL. The acceptance criterion is the same: content written through the mounted filesystem must be visible from a concurrent SQL connection.

### 4.6 Unmount and validate lifecycle cleanup

With the current direct-CLI workflow, cleanup order is explicit: unmount TigerFS, wait for its daemon to exit, and only then allow the pgembed server context to close.

```bash
# Disable the automatic traps only after taking responsibility for explicit
# cleanup. The same cleanup function also runs on every earlier error/signal.
trap - EXIT INT TERM
cleanup

if is_mounted; then
  echo "TigerFS mount still present after cleanup: $MOUNT_DIR" >&2
  exit 1
fi

if kill -0 "$TIGERFS_PID" 2>/dev/null; then
  echo "TigerFS daemon still running after cleanup" >&2
  exit 1
fi

if kill -0 "$PGEMBED_PID" 2>/dev/null; then
  echo "pgembed server helper is still running" >&2
  exit 1
fi

rm -rf "$TEST_ROOT"
```

**Expected:** `tigerfs unmount` may report non-zero on macOS yet still succeed, so cleanup keys off the actual mount state. The mount disappears, the TigerFS daemon has no orphaned process, and the pgembed context exits cleanly; a mount still present after daemon and server shutdown is a hard failure.

The planned `server.mount_filesystem()` integration must add a stronger lifecycle acceptance test: create the mount inside `with pgembed.get_server(...)`, exit the server context without a separate user-managed daemon, and assert that the mount manager first unmounts TigerFS, then terminates the daemon, then stops PostgreSQL. The intended acceptance test is concrete even though the API it tests is **planned and not implemented yet**:

```python
import os
import subprocess
import time

import pgembed
import psycopg2


def wait_until(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    assert predicate()


def test_tigerfs_context_cleanup(tmp_path):
    mount_dir = tmp_path / "mnt"
    mount_dir.mkdir()

    with pgembed.get_server(tmp_path / "pgdata") as server:
        uri = server.get_uri("postgres")
        server.mount_filesystem(mount_dir)  # Planned API; not available today.
        wait_until(lambda: os.path.ismount(mount_dir))

    # Exiting the pgembed context must clean up the mount and daemon first.
    wait_until(lambda: not os.path.ismount(mount_dir))
    assert subprocess.run(
        ["pgrep", "-f", f"tigerfs mount.*{mount_dir}"],
        check=False,
        capture_output=True,
    ).returncode != 0

    # The context must also have stopped the embedded PostgreSQL server.
    try:
        psycopg2.connect(uri, connect_timeout=1)
    except psycopg2.OperationalError:
        pass
    else:
        raise AssertionError("PostgreSQL remained reachable after context exit")
```

Enable this test when the wrapper lands. Until then, the runnable direct-CLI test above is the required cleanup path: do not exit the pgembed context while a direct-CLI mount is active.

## 5. Usage

The pgembed server handle and the TigerFS daemon must both remain alive for the entire lifetime of a mount. Always unmount before the server stops. If the surrounding application cannot keep the normal server context open, use a deliberate server lifetime such as `cleanup_mode=None` and arrange an equally deliberate `tigerfs unmount` during application shutdown.

### 5.1 Data-first: mount an existing pgembed database

Start pgembed, obtain its URI, and keep the Python process alive. Use two terminals so the foreground mount process remains supervised.

In **terminal A**, invoke the bundled executable and keep this command running:

```bash
export TIGERFS_BIN="$(python -c 'import pgembed,pathlib;print(pathlib.Path(pgembed.POSTGRES_BIN_PATH)/"tigerfs")')"
mkdir -p /mnt/db
"$TIGERFS_BIN" mount --foreground \
  "postgresql://postgres:@/postgres?host=/path/to/socket_dir" \
  /mnt/db
```

In **terminal B**, first wait for the actual mount state, then use ordinary file tools:

```bash
export TIGERFS_BIN="$(python -c 'import pgembed,pathlib;print(pathlib.Path(pgembed.POSTGRES_BIN_PATH)/"tigerfs")')"
python - /mnt/db <<'PY'
import os
import sys
import time

path = sys.argv[1]
deadline = time.monotonic() + 15
while not os.path.ismount(path):
    if time.monotonic() >= deadline:
        raise TimeoutError(f"TigerFS mount not ready: {path}")
    time.sleep(0.1)
PY

ls /mnt/db
find /mnt/db -maxdepth 3 -type f | head
export TABLE_DIR="/mnt/db/<path-shown-by-TigerFS>/<table>"
export ROW_FILE="$TABLE_DIR/<row-file>"
cat "$ROW_FILE"
grep -R "search text" "$TABLE_DIR"

# Examples of SQL-backed path operations exposed by TigerFS:
ls "$TABLE_DIR/.by/<column>/<value>"
ls "$TABLE_DIR/.order/<column>/.last/10"
cat "$TABLE_DIR/.export/<format>"

# Unmount before stopping pgembed. A non-zero timeout can still mean that the
# macOS unmount completed, so inspect the real mount state before fallback.
"$TIGERFS_BIN" unmount --timeout 5 /mnt/db || true
if python -c 'import os; raise SystemExit(0 if os.path.ismount("/mnt/db") else 1)'; then
  case "$(uname -s)" in
    Darwin) diskutil unmount force /mnt/db || true ;;
    Linux)  fusermount -u /mnt/db || true ;;
  esac
fi
python - /mnt/db <<'PY'
import os
import sys
import time

path = sys.argv[1]
deadline = time.monotonic() + 5
while os.path.ismount(path) and time.monotonic() < deadline:
    time.sleep(0.1)
assert not os.path.ismount(path), f"mount still present: {path}"
PY
```

After terminal B unmounts successfully, the foreground command in terminal A exits; wait for that return before stopping pgembed.

Paths below the mount are determined by the mounted database's schemas and tables; inspect the mounted tree rather than assuming that the placeholders above exist unchanged.

Today, Python can supervise the bundled daemon directly with `subprocess.Popen`. Do not use blocking `subprocess.run` for `tigerfs mount`:

```python
import os
import subprocess
import sys
import time
from pathlib import Path

import pgembed


def wait_for_state(path: Path, mounted: bool, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if os.path.ismount(path) is mounted:
            return True
        time.sleep(0.1)
    return os.path.ismount(path) is mounted


mount_dir = Path("/mnt/db")
mount_dir.mkdir(parents=True, exist_ok=True)

with pgembed.get_server("/path/to/data") as server:
    uri = server.get_uri("postgres")
    bin_tigerfs = pgembed.POSTGRES_BIN_PATH / "tigerfs"
    daemon = subprocess.Popen([str(bin_tigerfs), "mount", "--foreground", uri, str(mount_dir)])
    try:
        try:
            deadline = time.monotonic() + 15
            while not os.path.ismount(mount_dir):
                if daemon.poll() is not None:
                    raise RuntimeError(f"TigerFS exited early: {daemon.returncode}")
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"mount not ready: {mount_dir}")
                time.sleep(0.1)

            subprocess.run(["ls", str(mount_dir)], check=True)
            # Run SQL queries against `uri` while file tools read the same database.
        finally:
            # Unmount. `tigerfs unmount` can exit non-zero or time out on macOS
            # even when it succeeds, so the fallback decision uses the ACTUAL
            # mount state, not the return code.
            try:
                subprocess.run(
                    [str(bin_tigerfs), "unmount", str(mount_dir)],
                    check=False,
                    timeout=10,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                pass
            if os.path.ismount(mount_dir):
                try:
                    fallback = (
                        ["diskutil", "unmount", "force", str(mount_dir)]
                        if sys.platform == "darwin"
                        else ["fusermount", "-u", str(mount_dir)]
                    )
                    subprocess.run(fallback, check=False, timeout=10)
                except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                    pass
                wait_for_state(mount_dir, False, 5)
    finally:
        # Unconditional process reclamation — the outer finally, so any unmount
        # error above cannot skip reclaiming the daemon.
        try:
            daemon.wait(timeout=15)
        except subprocess.TimeoutExpired:
            daemon.terminate()
            try:
                daemon.wait(timeout=5)
            except subprocess.TimeoutExpired:
                daemon.kill()
                daemon.wait(timeout=5)
        # After reclaiming the daemon, a lingering mount is a hard error.
        if os.path.ismount(mount_dir):
            raise RuntimeError(f"TigerFS mount still present after cleanup: {mount_dir}")
```

A lifecycle-aware convenience API is **deferred and unimplemented**; the following is design-only and must not be treated as current pgembed API:

```python
import pgembed

with pgembed.get_server("/path/to/data") as server:
    # Planned API; not available today.
    mount = server.mount_filesystem("/mnt/db")
    try:
        # Use /mnt/db with ls, cat, grep, or normal file APIs.
        # SQL clients can simultaneously use server.get_uri("postgres").
        ...
    finally:
        mount.unmount()
```

### 5.2 File-first: write files backed by PostgreSQL

File-first workflows use a writable `.build/` workspace exposed by TigerFS. First create the same supervised two-terminal mount shown in the data-first example above. With the foreground command still running in terminal A and the mount confirmed ready in terminal B, locate the workspace:

```bash
find /mnt/db -type d -name .build -print
```

Select the `.build/` workspace configured for the target collection, then write a file such as Markdown with frontmatter:

```bash
export BUILD_WORKSPACE="/mnt/db/<path-shown-by-TigerFS>/.build/<workspace>"

test -d "$BUILD_WORKSPACE"
cat >"$BUILD_WORKSPACE/hello.md" <<'EOF'
---
title: Hello from TigerFS
tags:
  - pgembed
  - tigerfs
---

This file is stored transactionally in PostgreSQL.
EOF

cat "$BUILD_WORKSPACE/hello.md"
```

The exact workspace path and resulting table shape come from the TigerFS workspace/schema configuration visible in the mount. After the write, query that mapped table using `server.get_uri("postgres")`; SQL and filesystem clients see the same committed data. TigerFS's file-first features can also expose history, savepoints, and undo operations through `.history/`, `.savepoint/`, and `.undo/`.

Direct CLI cleanup remains mandatory. Reuse the `unmount --timeout 5`, mount-state check, OS fallback, and foreground-process wait from the data-first example before stopping pgembed.

The corresponding planned Python form is:

```python
from pathlib import Path
import pgembed

with pgembed.get_server("/path/to/data") as server:
    # Planned API; not available today.
    mount = server.mount_filesystem("/mnt/db")
    try:
        workspace = Path("/mnt/db/<path-shown-by-TigerFS>/.build/<workspace>")
        (workspace / "hello.md").write_text(
            "---\n"
            "title: Hello from TigerFS\n"
            "---\n\n"
            "This file is stored transactionally in PostgreSQL.\n"
        )

        uri = server.get_uri("postgres")
        # Query the mapped table through psycopg2 using `uri`.
    finally:
        mount.unmount()
```

## 6. Troubleshooting

### `/dev/fuse` is missing or inaccessible on Linux

Check the device and the current user's permissions:

```bash
ls -l /dev/fuse
test -r /dev/fuse -a -w /dev/fuse
id -nG
```

The host must load/provide the kernel FUSE device, and the user must be in the `fuse` group. Adding a user to the group normally requires logging out and back in. Installing a userspace `libfuse` development package does not by itself fix a missing `/dev/fuse`; TigerFS's static Go binary talks to the device directly.

### The mount fails in Docker, Colab, CI, or a sandbox

Default containers commonly hide `/dev/fuse` and deny the mount capability. For Docker, start the container with:

```bash
docker run --device /dev/fuse --cap-add SYS_ADMIN ...
```

Use the equivalent device/capability configuration for a pod or CI runner. If the environment cannot grant it, skip TigerFS mount tests. pgembed's embedded database and SQL access remain usable.

### The user is not in the `fuse` group

Confirm with:

```bash
id -nG | tr ' ' '\n' | grep -x fuse
```

Ask the system administrator to add the user to the group, then start a new login session. Avoid presenting root execution as the normal TigerFS workflow; a correctly configured Linux host permits a non-root mount.

### An orphaned or stale mount remains

First use TigerFS's own unmount command:

```bash
tigerfs unmount /mnt/db
```

If the daemon has crashed or the normal unmount cannot complete, use the platform fallback:

```bash
# Linux
fusermount -u /mnt/db

# macOS
diskutil unmount force /mnt/db
```

Afterward, stop any orphaned TigerFS daemon and confirm the mount is absent with `mount`. Do not remove a mount directory while it is still mounted.

### `tigerfs unmount` reports a deadline error on macOS

On macOS, `tigerfs unmount` can consume most of its internal deadline and then exit non-zero with a message such as `unmount failed: context deadline exceeded`, even though the mount was in fact torn down and the daemon exited. Treat that non-zero (or timed-out) unmount as "verify, then escalate," not as a hard failure: re-check `os.path.ismount(<dir>)`, and if the mount is still present, run the `diskutil unmount force` fallback. The lifecycle examples above wrap the normal unmount so that a timeout or non-zero status still proceeds to the platform fallback and to daemon cleanup.

### PostgreSQL stopped before TigerFS was unmounted

TigerFS is a client of the pgembed PostgreSQL server. If the server context exits first, filesystem operations will fail and the mount may become stale. Restore the cleanup order:

1. Stop new filesystem activity.
2. Run `tigerfs unmount <mount-directory>`.
3. Wait for the TigerFS daemon to exit.
4. Allow the pgembed server context to close.

For long-lived direct mounts, keep the pgembed server alive deliberately; do not rely on default context cleanup while TigerFS is still mounted.

### macOS listings or file contents appear briefly stale

The macOS backend uses NFS, whose attribute caching can delay the visibility of very recent metadata changes. Retry after a short delay and re-read the path before diagnosing a database inconsistency. For testing, wait for the expected content rather than relying on one immediate `ls`; if necessary, unmount and remount after confirming all writers are idle.

### The connection URI fails or TigerFS reports an SSL error

Pass the exact URI returned by:

```python
uri = server.get_uri("postgres")
```

On Unix this is normally a local socket URI, which TigerFS recognizes as local and uses without SSL. Preserve URI escaping and quote the complete URI in shell commands, especially the `?host=...` query component. For a remote PostgreSQL URI, configure the connection's SSL parameters for that server instead of assuming the local-socket behavior applies.

### `CREATE EXTENSION tigerfs` or `has_extension("tigerfs")` fails

That is expected. TigerFS is not a PostgreSQL extension and must not be added to pgembed's extension registry. Verify the standalone binary instead:

```bash
python - <<'PY'
import pgembed

path = pgembed.POSTGRES_BIN_PATH / "tigerfs"
print(path)
print("present:", path.is_file())
PY
```

Then start it as a separate daemon with `tigerfs mount`, keep both processes alive, and unmount before stopping PostgreSQL.
