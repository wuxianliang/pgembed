# Investigation: uv local pgembed installation

## Summary
The locally modified `pgembed` working tree is installed and usable in the likely `rag` virtual environment at `/Users/wxl/Projects/pgembed/.venv/rag`. That environment imports `pgembed` from `/Users/wxl/Projects/pgembed/src/pgembed`, has package metadata version `0.2.0`, and its `direct_url.json` records an editable install from `file:///Users/wxl/Projects/pgembed`.

The embedded PostgreSQL binaries are present in the working tree at `/Users/wxl/Projects/pgembed/src/pgembed/pginstall/bin`; `initdb`, `pg_ctl`, `psql`, and `pg_config` exist and report PostgreSQL `17.10`.

However, `examples/pyproject.toml` currently declares only `pgembed>=0.1.7` and has no `[tool.uv.sources]` local path source, so a fresh uv sync for the examples project would resolve `pgembed` as a registry/version dependency unless changed with `uv add --editable ..` (or equivalent metadata). A nearby `/Users/wxl/Projects/newrag/backend/.venv` does not have `pgembed` installed.

## Symptoms
- User is unsure whether the modified local `pgembed` package has been installed locally.
- A virtual environment named `rag` may already exist.
- User wants uv-managed local projects to use this package.

## Background / Prior Research

### uv local package usage (official docs lookup, 2026-05-27)
- For a uv-managed project, prefer `uv add --editable ../path/to/pgembed`; this records both `[project].dependencies` and `[tool.uv.sources] pgembed = { path = "...", editable = true }` in project metadata/lockfile.
- `uv pip install -e ../path/to/pgembed` is best for ad-hoc pip-compatible workflows or directly mutating an already-selected virtual environment; it does not update uv project metadata.
- For uv projects, the default environment is `.venv`; `UV_PROJECT_ENVIRONMENT=/path/to/venv uv sync` can redirect a project to an existing/specific environment.
- For `uv pip`, uv selects an active `VIRTUAL_ENV`, active Conda env, or a `.venv` in the current/parent directory. You can also use `uv pip install --python /path/to/venv/bin/python -e /path/to/pgembed`.
- To verify the imported package location: `uv run python -c "import pgembed, pathlib; print(pathlib.Path(pgembed.__file__).resolve())"` or `/path/to/venv/bin/python -c "import pgembed, pathlib; print(pathlib.Path(pgembed.__file__).resolve())"`.
- Official docs cited by explore agent: https://docs.astral.sh/uv/concepts/projects/dependencies/#path, https://docs.astral.sh/uv/pip/packages/#editable-packages, https://docs.astral.sh/uv/pip/environments/, https://docs.astral.sh/uv/concepts/projects/config/#project-environment-path, https://docs.astral.sh/uv/concepts/projects/workspaces/

## Investigator Findings

### 2026-05-27 - Local environment discovery

**Hypothesis:** A virtual environment named `rag` exists locally and may already contain the working-tree `pgembed`.

**Commands run:**

```bash
pwd
env | grep -E '^(VIRTUAL_ENV|CONDA_PREFIX|UV_PROJECT_ENVIRONMENT|UV_PYTHON|PYTHONPATH|PATH)=' || true
for c in uv python python3 pip pip3; do command -v "$c" || true; done
uv --version
python3 -V && python3 -c 'import sys; print(sys.executable)'
uv python dir
find /Users/wxl/Projects -maxdepth 4 -type f -name pyvenv.cfg 2>/dev/null | sort
for base in /Users/wxl/Projects /Users/wxl/.venvs /Users/wxl/.virtualenvs /Users/wxl/.local/share/virtualenvs /Users/wxl/.cache/pypoetry/virtualenvs /Users/wxl/.conda/envs /Users/wxl/miniconda3/envs /Users/wxl/anaconda3/envs; do [ -d "$base" ] && find "$base" -maxdepth 3 -type d \( -name 'rag' -o -name '*rag*' \) 2>/dev/null; done | sort -u
uv python list --only-installed
```

**Outputs / evidence:**

- Working directory was `/Users/wxl/Projects/pgembed`.
- No `VIRTUAL_ENV`, `CONDA_PREFIX`, `UV_PROJECT_ENVIRONMENT`, `UV_PYTHON`, or `PYTHONPATH` was set in the command environment; only `PATH` was present. `PATH` included `/Users/wxl/Projects/newrag/.flow/bin`, which is a clue for a nearby `newrag` project but not a Python venv by itself.
- `uv` resolved to `/Users/wxl/.local/bin/uv`; `uv --version` returned `uv 0.8.24 (252f88733 2025-10-07)`.
- System/default `python3` was Python `3.14.5` at `/opt/homebrew/opt/python@3.14/bin/python3.14`; no `python` command was found.
- `uv python dir` returned `/Users/wxl/.local/share/uv/python`.
- `find /Users/wxl/Projects -maxdepth 4 -type f -name pyvenv.cfg` found exactly:
  - `/Users/wxl/Projects/pgembed/.venv/rag/pyvenv.cfg`
  - `/Users/wxl/Projects/newrag/backend/.venv/pyvenv.cfg`
- The `rag`-name directory search found many source/project directories containing `rag`, but the only Python virtualenv-shaped `rag` directory found was `/Users/wxl/Projects/pgembed/.venv/rag`.
- `uv python list --only-installed` showed installed interpreters including uv-managed CPython `3.12.12` at `/Users/wxl/.local/share/uv/python/cpython-3.12.12-macos-aarch64-none/bin/python3.12`; this matches the `rag` venv's `pyvenv.cfg`.
- A sandboxed `uv python list --only-installed` first failed with `error: failed to open file /Users/wxl/.cache/uv/sdists-v9/.git: Operation not permitted`; rerunning as a read-only escalated command succeeded.

**Conclusion:** The most plausible `rag` virtual environment is `/Users/wxl/Projects/pgembed/.venv/rag`. A nearby consumer/project venv also exists at `/Users/wxl/Projects/newrag/backend/.venv`, so it was checked as a secondary target.

### 2026-05-27 - Candidate environment import and metadata checks

**Commands run:**

```bash
cat /Users/wxl/Projects/pgembed/.venv/rag/pyvenv.cfg
/Users/wxl/Projects/pgembed/.venv/rag/bin/python -V
/Users/wxl/Projects/pgembed/.venv/rag/bin/python -c '<probe imports pgembed, prints __file__, importlib.metadata.version, direct_url.json, METADATA, and pgembed._commands.POSTGRES_BIN_PATH>'
uv pip show pgembed --python /Users/wxl/Projects/pgembed/.venv/rag/bin/python
uv pip list --python /Users/wxl/Projects/pgembed/.venv/rag/bin/python | grep -i pgembed || true

cat /Users/wxl/Projects/newrag/backend/.venv/pyvenv.cfg
/Users/wxl/Projects/newrag/backend/.venv/bin/python -V
/Users/wxl/Projects/newrag/backend/.venv/bin/python -c '<same pgembed probe>'
uv pip show pgembed --python /Users/wxl/Projects/newrag/backend/.venv/bin/python
uv pip list --python /Users/wxl/Projects/newrag/backend/.venv/bin/python | grep -i pgembed || true
```

**Outputs / evidence for `/Users/wxl/Projects/pgembed/.venv/rag`:**

- `pyvenv.cfg`:
  - `home = /Users/wxl/.local/share/uv/python/cpython-3.12.12-macos-aarch64-none/bin`
  - `implementation = CPython`
  - `uv = 0.8.24`
  - `version_info = 3.12.12`
  - `include-system-site-packages = false`
- `python -V` returned `Python 3.12.12`.
- Probe output:
  - `sys.executable= /Users/wxl/Projects/pgembed/.venv/rag/bin/python`
  - `sys.prefix= /Users/wxl/Projects/pgembed/.venv/rag`
  - `import pgembed=OK`
  - `pgembed.__file__= /Users/wxl/Projects/pgembed/src/pgembed/__init__.py`
  - `pgembed package dir= /Users/wxl/Projects/pgembed/src/pgembed`
  - `resolves_to_local_src= True`
  - `metadata.version(pgembed)= 0.2.0`
  - `dist._path= /Users/wxl/Projects/pgembed/.venv/rag/lib/python3.12/site-packages/pgembed-0.2.0.dist-info`
  - `dist direct_url.json= {"url":"file:///Users/wxl/Projects/pgembed","dir_info":{"editable":true}}`
  - selected `METADATA` contained `Name: pgembed`, `Version: 0.2.0`, `Requires-Python: >=3.12`, and runtime requirements `fasteners>=0.19`, `platformdirs>=4.0.0`, `psutil>=5.9.0`.
- `python -m pip show -f pgembed` was attempted but failed because the venv has no pip module: `No module named pip`.
- `uv pip show pgembed --python /Users/wxl/Projects/pgembed/.venv/rag/bin/python` returned:
  - `Using Python 3.12.12 environment at: .venv/rag`
  - `Name: pgembed`
  - `Version: 0.2.0`
  - `Location: /Users/wxl/Projects/pgembed/.venv/rag/lib/python3.12/site-packages`
  - `Editable project location: /Users/wxl/Projects/pgembed`
  - `Requires: fasteners, platformdirs, psutil`
- `uv pip list --python /Users/wxl/Projects/pgembed/.venv/rag/bin/python | grep -i pgembed` returned `pgembed           0.2.0   /Users/wxl/Projects/pgembed`.
- Sandboxed `uv pip show/list` first failed with the same `~/.cache/uv/sdists-v9/.git` permission error; rerunning as read-only escalated commands succeeded.

**Outputs / evidence for `/Users/wxl/Projects/newrag/backend/.venv`:**

- `pyvenv.cfg`:
  - `home = /opt/anaconda3/bin`
  - `implementation = CPython`
  - `uv = 0.8.24`
  - `version_info = 3.12.2`
  - `include-system-site-packages = false`
- `python -V` returned `Python 3.12.2`.
- Probe output:
  - `sys.executable= /Users/wxl/Projects/newrag/backend/.venv/bin/python`
  - `sys.prefix= /Users/wxl/Projects/newrag/backend/.venv`
  - `import pgembed=ERROR ModuleNotFoundError No module named 'pgembed'`
  - `metadata pgembed=ERROR PackageNotFoundError No package metadata was found for pgembed`
- `python -m pip show -f pgembed` also failed because this venv has no pip module: `No module named pip`.
- `uv pip show pgembed --python /Users/wxl/Projects/newrag/backend/.venv/bin/python` returned `warning: Package(s) not found for: pgembed`.

**Conclusion:** The local working tree is editably installed and importable in `/Users/wxl/Projects/pgembed/.venv/rag`. The nearby `newrag/backend/.venv` does not currently have `pgembed` installed.

### 2026-05-27 - Embedded PostgreSQL binary availability

**Commands run:**

```bash
/Users/wxl/Projects/pgembed/.venv/rag/bin/python -c '<probe imports pgembed._commands and checks POSTGRES_BIN_PATH plus initdb/pg_ctl/psql/pg_config existence/executable bits>'
ls -la /Users/wxl/Projects/pgembed/src/pgembed/pginstall/bin | sed -n '1,120p'
for exe in initdb pg_ctl psql pg_config; do /Users/wxl/Projects/pgembed/src/pgembed/pginstall/bin/$exe --version; done
/Users/wxl/Projects/pgembed/src/pgembed/pginstall/bin/pg_config --version --bindir --pkglibdir --sharedir
```

**Outputs / evidence:**

- `pgembed._commands.POSTGRES_BIN_PATH= /Users/wxl/Projects/pgembed/src/pgembed/pginstall/bin`.
- `POSTGRES_BIN_PATH.exists= True`.
- Required binary existence/executable checks all passed:
  - `initdb`: exists `True`, executable `True`, path `/Users/wxl/Projects/pgembed/src/pgembed/pginstall/bin/initdb`
  - `pg_ctl`: exists `True`, executable `True`, path `/Users/wxl/Projects/pgembed/src/pgembed/pginstall/bin/pg_ctl`
  - `psql`: exists `True`, executable `True`, path `/Users/wxl/Projects/pgembed/src/pgembed/pginstall/bin/psql`
  - `pg_config`: exists `True`, executable `True`, path `/Users/wxl/Projects/pgembed/src/pgembed/pginstall/bin/pg_config`
- Version checks:
  - `initdb (PostgreSQL) 17.10`
  - `pg_ctl (PostgreSQL) 17.10`
  - `psql (PostgreSQL) 17.10`
  - `PostgreSQL 17.10` from `pg_config --version`
- `pg_config --version --bindir --pkglibdir --sharedir` returned:
  - `PostgreSQL 17.10`
  - `/Users/wxl/Projects/pgembed/src/pgembed/pginstall/bin`
  - `/Users/wxl/Projects/pgembed/src/pgembed/pginstall/lib/postgresql`
  - `/Users/wxl/Projects/pgembed/src/pgembed/pginstall/share/postgresql`
- No long-running PostgreSQL server was started.

**Conclusion:** Binary availability is good for the editable `rag` install because it resolves to the working tree and the working tree has a populated `src/pgembed/pginstall/bin`.

### 2026-05-27 - Consumer uv metadata and local path source checks

**Commands run:**

```bash
find /Users/wxl/Projects/pgembed -maxdepth 3 \( -name uv.lock -o -name pyproject.toml \) -print | sort
grep -Rsn --include='pyproject.toml' --include='uv.lock' -E 'tool\.uv|pgembed|editable|sources' /Users/wxl/Projects/pgembed /Users/wxl/Projects/newrag 2>/dev/null | sed -n '1,220p'
```

**Repo file evidence:**

- Root package metadata declares `name = "pgembed"`, `version = "0.2.0"`, `requires-python = ">=3.12"`, and package discovery from `src` including `pgembed*` / `pgembed_pg*` (`pyproject.toml:2-30`).
- `setup.py` has a dummy CFFI setup hook and does not itself build PostgreSQL binaries (`setup.py:1-7`).
- `MANIFEST.in` includes `graft src/pgembed/pginstall` (`MANIFEST.in:1`).
- Runtime command resolution uses `POSTGRES_BIN_PATH = _pkg_path / "pginstall" / "bin"`; missing binaries only emit a development/editable-install warning and command wrappers are exposed only by iterating the existing bin directory (`src/pgembed/_commands.py:13-29`, `src/pgembed/_commands.py:113-126`).
- Prior build/packaging investigation says artifacts are build-time outputs and editable installs expect `make build` if `pginstall` is absent (`docs/investigations/embedded-postgres-plugin-installation-2026-05-15.md:31-35`).
- README states PostgreSQL binaries are available at `pgembed.POSTGRES_BIN_PATH` for tools including `initdb`, `pg_ctl`, `psql`, and `pg_config` (`README.md:25-48`).

**Consumer metadata evidence:**

- `find` found pyproject files at:
  - `/Users/wxl/Projects/pgembed/pyproject.toml`
  - `/Users/wxl/Projects/pgembed/examples/pyproject.toml`
  - `/Users/wxl/Projects/pgembed/src/pgembed_pgtextsearch/pyproject.toml`
  - `/Users/wxl/Projects/pgembed/src/pgembed_pgvector/pyproject.toml`
  - `/Users/wxl/Projects/pgembed/src/pgembed_pgvectorscale/pyproject.toml`
- No `uv.lock` was found under `/Users/wxl/Projects/pgembed` at depth 3, and a RepoPrompt path search found no `uv.lock` in the loaded workspace.
- `examples/pyproject.toml:6` currently has only `"pgembed>=0.1.7"`; there is no `[tool.uv.sources]` block and no `pgembed = { path = "..", editable = true }` source (`examples/pyproject.toml:1-18`).
- Extension subpackage pyprojects depend on registry-style `pgembed>=0.1.8` (`src/pgembed_pgvector/pyproject.toml:7`, `src/pgembed_pgvectorscale/pyproject.toml:7`, `src/pgembed_pgtextsearch/pyproject.toml:7`).
- An explore probe independently searched `pyproject.toml`/`uv.lock` references and reported no local/path source patterns (`path =`, `editable`, `file://`, or relative `../pgembed`) for `pgembed`; examples currently resolve `pgembed` from a version spec, not the local repo.

**Conclusion:** The named `rag` venv is correct today because it has an editable install, but the examples project metadata does not yet encode that preference. A clean uv sync for `examples` can choose the registry package unless a local editable path source is added.

### 2026-05-27 - Recommended commands

Use one of these flows depending on intent:

**Inspect the existing `rag` env without changing it:**

```bash
/Users/wxl/Projects/pgembed/.venv/rag/bin/python -V
/Users/wxl/Projects/pgembed/.venv/rag/bin/python -c 'import pathlib, pgembed, importlib.metadata as md; print(pathlib.Path(pgembed.__file__).resolve()); print(md.version("pgembed"))'
/Users/wxl/Projects/pgembed/.venv/rag/bin/python -c 'import pathlib, pgembed._commands as c; print(c.POSTGRES_BIN_PATH); print(c.POSTGRES_BIN_PATH.exists())'
```

**Install/update the working tree into an ad-hoc target venv such as `rag`:**

```bash
uv pip install --python /Users/wxl/Projects/pgembed/.venv/rag/bin/python -e /Users/wxl/Projects/pgembed
```

Use this when the target is just a venv and you intentionally want to mutate that environment. It updates installed packages, not a consumer project's `pyproject.toml`.

**Make the examples uv project prefer the local working tree:**

```bash
cd /Users/wxl/Projects/pgembed/examples
uv add --editable ..
uv sync
uv run python -c 'import pathlib, pgembed; print(pathlib.Path(pgembed.__file__).resolve())'
```

Expected metadata shape after `uv add --editable ..` is a dependency on `pgembed` plus a local source similar to:

```toml
[tool.uv.sources]
pgembed = { path = "..", editable = true }
```

**Use an existing/specific environment for a uv project instead of the default `.venv`:**

```bash
cd /path/to/consumer-project
UV_PROJECT_ENVIRONMENT=/Users/wxl/Projects/pgembed/.venv/rag uv sync
UV_PROJECT_ENVIRONMENT=/Users/wxl/Projects/pgembed/.venv/rag uv run python -c 'import pathlib, pgembed; print(pathlib.Path(pgembed.__file__).resolve())'
```

Only do this if reusing the `rag` environment for that project is intentional; uv projects normally use their own `.venv`.

**If binaries are missing in an editable install:**

```bash
cd /Users/wxl/Projects/pgembed
make build
```

The checked `rag` install does not currently need this for the core PostgreSQL tools because `src/pgembed/pginstall/bin` is already populated.

## Investigation Log

### Phase 1 - Initial Assessment
**Hypothesis:** The local repo may not be installed into the intended Python environment, or uv projects may be resolving `pgembed` from PyPI/cache instead of the working tree.
**Findings:** Report created; external uv documentation and local environment/project packaging state needed investigation.
**Evidence:** Report path: `/Users/wxl/Projects/pgembed/docs/investigations/uv-local-pgembed-installation-2026-05-27.md`
**Conclusion:** Superseded by Phase 2 findings.

### Phase 2 - Local Environment and uv Metadata Verification
**Hypothesis:** The likely `rag` venv is `/Users/wxl/Projects/pgembed/.venv/rag`, and consumer uv metadata may not be pinned to the local source.
**Findings:** Pair investigator found `/Users/wxl/Projects/pgembed/.venv/rag` and `/Users/wxl/Projects/newrag/backend/.venv`; verified that only the `rag` venv imports `pgembed` from the local source tree. Verified embedded PostgreSQL binaries are present and executable. Verified `examples/pyproject.toml` has no uv local path source for `pgembed`.
**Evidence:** See `## Investigator Findings`, especially the 2026-05-27 sections for environment discovery, candidate environment checks, binary checks, and consumer uv metadata.
**Conclusion:** Confirmed.

## Root Cause
There are two separate states that can be confused:

1. The ad-hoc/local `rag` virtual environment already has an editable install of the working tree, so local code changes are used there.
2. The examples uv project metadata does not declare a local editable path source, so uv project resolution is not guaranteed to use the working tree in a fresh/default project environment.

The package's binary availability also depends on the build-time `src/pgembed/pginstall` tree. In this checkout it is present, so the editable `rag` install can find the embedded PostgreSQL tools.

## Recommendations
1. Keep using `/Users/wxl/Projects/pgembed/.venv/rag/bin/python` when you specifically want the already-working local editable `pgembed` environment.
2. For uv-managed consumer projects, encode the local source in project metadata with `uv add --editable /Users/wxl/Projects/pgembed` (or `uv add --editable ..` from `examples`) rather than relying on a manually mutated venv.
3. If you want a consumer uv project to reuse the existing `rag` environment, run uv with `UV_PROJECT_ENVIRONMENT=/Users/wxl/Projects/pgembed/.venv/rag`; do this intentionally because it shares one environment across projects.
4. If a future editable install imports the working tree but lacks PostgreSQL binaries, run `make build` from `/Users/wxl/Projects/pgembed` and re-check `pgembed._commands.POSTGRES_BIN_PATH`.

## Preventive Measures
- Add `[tool.uv.sources] pgembed = { path = "..", editable = true }` to local example/consumer projects that must follow this checkout.
- Keep a short verification command in docs or scripts: `python -c 'import pathlib, pgembed; print(pathlib.Path(pgembed.__file__).resolve())'`.
- For binary-dependent checks, verify both import location and `pgembed._commands.POSTGRES_BIN_PATH.exists()`; import success alone does not prove embedded PostgreSQL binaries are available.
- Avoid assuming a venv name implies project metadata: `uv pip install -e` changes the selected environment, while `uv add --editable` changes uv project metadata.
