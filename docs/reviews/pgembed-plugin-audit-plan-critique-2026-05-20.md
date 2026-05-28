# Critique: pgembed Plugin Installation Audit Plan

**Plan reviewed:** `docs/plans/pgembed-plugin-installation-audit-2026-05-20.md`
**Compared against:** `prompt-exports/oracle-plan-2026-05-20-094544-plugin-audit-a828c0-27fc.md` (context_builder export)

---

## 1. Top 3 Under-Specified Seams

**1a. No actionable work items or acceptance criteria.**
The plan states a goal and lists open questions but never answers them or breaks into discrete tasks. An implementer must guess whether to (a) audit and document current state, (b) fix all broken plugins, or (c) build the new `extensions.py` registry the export proposes. The plan's "Open Questions" section reads like a research brief, not a plan — and the export already resolved most of them.

**1b. `age` preload — unmentioned critical bug.**
The plan's Background omits the single most dangerous finding in the export: `PostgresServer.ensure_pgdata_inited()` unconditionally writes `shared_preload_libraries = 'age'` (`postgres_server.py`). If `age` is not built (and the export's CI audit shows no evidence it is), this breaks default startup. An implementer reading only the plan would not prioritize this fix.

**1c. Extension stub → core registry disconnect.**
The plan references the hardcoded registry in `__init__.py:23-91` but doesn't articulate the gap the export surfaces: the stub packages (`pgembed_pgvector`, `pgembed_pgvectorscale`, `pgembed_pgtextsearch`) expose share-path helpers that the core registry never consults. An implementer would have to discover independently whether to wire them in or replace them.

---

## 2. Specificity Balance

**Plan under-specifies relative to the export.** The export carries concrete design (a `ExtensionMetadata` dataclass with per-extension field values, error message templates, a 9-step implementation order, migration notes for existing `PGDATA`). The plan dropped all of this. The result is a plan that reads like the *input* to the export, not the output of it — as if the export's analysis was never folded back.

**Export over-specifies in two areas:**
- Exact error message text (e.g. the `CREATE EXTENSION` failure templates in §4.3) — an implementer should own wording.
- The `ExtensionMetadata` field list and `library_patterns` tuples per extension are stated as definitive, but some values (e.g., `vectorscale` SQL patterns, `pg_search` preload behavior) are marked "mark `False` unless implementation validates" — placeholders that should have been resolved before the plan was treated as actionable.

**Dropped framing worth keeping:** The export's four-level status taxonomy ("documented," "built in CI," "present in wheel," "actually creatable") is the single most useful analytical frame. The plan doesn't reproduce it.

---

## 3. Contradictions and Missing Dependencies

- **Plan lists `pgbuild/Makefile` as a reference but it's outside the repo tree.** The file map shows no `pgbuild/` directory. The export works around this via the prior investigation doc, but the plan doesn't note this dependency. An implementer can't inspect build recipes without it.
- **Plan's open question "should failures be optional or fixed?" is already answered in the export** — the export treats `age`, `vectorchord`, `psql_bm25s` as registry-only and designs them to not affect startup. The plan should close this question rather than leaving it open.
- **`pg_search` preload status is unknown in both documents.** The export marks it "Build-attempted but unproven" and flags `requires_preload` as unresolved. Neither document proposes how to resolve it before implementation. This is a hidden dependency: if `pg_search` needs preload, it changes the preload support design in §4.2.

---

## 4. Risk of Over-Planning

The export's §4 (Design) and §5 (File-by-file impact) total ~400 lines of specification for what is fundamentally a registry cleanup and a one-line preload removal. Sections that could be cut or simplified:

- **§4.1 full `ExtensionMetadata` / `ExtensionStatus` dataclass definitions** — prescribe internal implementation shape that the implementer should design. Replacing with "centralize metadata, validate .so + .control + SQL, report partial installs" would be enough.
- **§4.4 Extension stub changes** — three subsections for what amounts to "fix the control-name constants to match PostgreSQL names."
- **§7 Implementation order step 9 (README/docs)** — documentation updates should trail working code, not be planned as a step on par with the preload fix.

The one section that *shouldn't* be simplified: **§4.2 preload handling** — the `age` removal, managed config blocks, and existing-PGDATA migration logic are load-bearing and need the detail given.

---

## 5. Questions That Would Change Implementation Order

1. **Is `age` preload currently breaking real user startups?** If yes, step 4 (remove `age` preload) should be step 1, shipped as a hotfix before any registry refactor.
2. **Does `pg_search` require `shared_preload_libraries`?** If yes, the preload support design (§4.2) must be validated against two consumers (`pg_duckdb` + `pg_search`), not one, before building.
3. **Are the stub packages (`pgembed_pgvector`, etc.) actually published to PyPI with user-facing APIs?** If they're internal-only, the stub alignment work (§4.4) can be deferred; if public, it should be coordinated with the registry refactor.
4. **Can CI currently build all listed extensions on all platforms?** The export flags `vectorchord`, `psql_bm25s`, and `age` as having no CI evidence. If the answer is "no, and we don't plan to," those should be removed from the registry entirely rather than carried as dead entries.
