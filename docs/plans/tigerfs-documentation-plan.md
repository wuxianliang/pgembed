# TigerFS documentation plan — implemented and superseded

Date: 2026-08-07
Status: **Implemented; this planning brief is superseded by the repository's current code, tests, workflow, and user documentation.**

Do not use earlier revisions of this file as a specification. In particular, the work is no longer documentation-only, TigerFS build integration has landed, and the project does not publish Windows, macOS Intel, or universal2 artifacts.

## Authoritative current state

- User guide: [`docs/tigerfs.md`](../tigerfs.md)
- Project overview and runnable lifecycle example: [`README.md`](../../README.md)
- Build implementation and stable configuration stamp: `pgbuild/Makefile`
- Release matrix: `.github/workflows/build-and-test.yml`
- Installed-wheel checks: `tests/test_bundled_tools.py` and `cibuildwheel_test.bash`
- Build invalidation tests: `tests/test_tigerfs_build.py`
- Command-export behavior: `src/pgembed/_commands.py` and `tests/test_commands.py`

## Implemented decisions

1. TigerFS v0.7.0 is bundled as a standalone executable at `pgembed.POSTGRES_BIN_PATH / "tigerfs"`; it is not a PostgreSQL extension.
2. There is no top-level `pgembed.tigerfs()` wrapper. Long-lived mounts are launched with `subprocess.Popen` and explicitly unmounted before the PostgreSQL server context exits.
3. `PostgresServer.mount_filesystem()` remains deferred until it can own readiness, unmount, timeout, and process-cleanup semantics.
4. The Make target uses a stable, content-checked configuration stamp. Changes to tag, OS, architecture, release URL, or pinned checksums invalidate the installed binary; unchanged configuration preserves the stamp mtime.
5. Release artifacts are Darwin/Linux-only:
   - macOS arm64, deployment target 26.0; no Intel, universal2, or older macOS compatibility claim;
   - Linux x86_64 and aarch64.
6. Installed-wheel tests verify the TigerFS file, executable mode, bounded `version` invocation, and absence of `pgembed.tigerfs`. Linux package tests do not require FUSE.
7. Real mount validation remains host-capability-dependent: macOS uses NFS; Linux requires usable `/dev/fuse` access.

## Deferred work

- A public bundled-tool registry or `TIGERFS_BIN_PATH` convenience constant.
- A lifecycle-aware `mount_filesystem()` API.
- Any additional platform or architecture, including macOS Intel/universal2 or older deployment targets.
- Linux mount tests on CI until runners explicitly provide `/dev/fuse` and the required capability.

Historical orchestration details, proposed output-only Make recipes, and architecture claims from the original planning draft were intentionally removed because the final implementation has replaced them.
