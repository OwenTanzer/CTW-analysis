# Experimental modifier reference

Migrated from [database PR #12](https://github.com/OwenTanzer/computational-total-war/pull/12),
head `dce21c44dd0d7e6d14a5873ae930092248356a66`.

The database owns extracted game facts, schemas and provenance. Analysis owns
this experimental candidate compiler, recipient/identity interpretation, query
engine and its tests. Candidate relevance is not proof of acquisition, active
scope, stacking or final statistics. This is not yet a build-comparison engine.

## Reproduce

Python 3.11+ and Node 24+. The local rebuild used Python 3.12 / SQLite 3.45.1.
A first attempt with this environment's SQLite 3.53.1 failed with an invalid
rootpage error; use the tested system runtime until that compatibility issue is
investigated. The workflow uses Ubuntu 24.04 system Python. Run from the Analysis repository root. Use a separate,
clean database checkout at the exact commit in this study's `source_lock.json`.
The root-level lock for the race strategy study is deliberately unchanged.

```bash
git clone https://github.com/OwenTanzer/computational-total-war ../ctw-modifier-source
git -C ../ctw-modifier-source checkout f61fd8ab2a65931abb494340a6918faaeedfdd7d
export CTW_ROOT="$(cd ../ctw-modifier-source && pwd)"
python src/ctw_analysis/modifier_reference/build-modifier-reference.py \
  "$CTW_ROOT/data/effect_semantics/source_exports" work/modifier_reference
python src/ctw_analysis/modifier_reference/validate-modifier-reference.py work/modifier_reference
python src/ctw_analysis/modifier_reference/test-modifier-reference.py
python src/ctw_analysis/modifier_reference/query-modifier-reference.py \
  unit wh2_main_hef_inf_lothern_sea_guard_0 --modifiers \
  --owner wh2_main_hef_teclis --rank 7 --limit 10
```

PowerShell: set `$env:CTW_ROOT` to the absolute database checkout path and use
`python` for the same commands. The builder requires a fresh output directory.
All generated indexes, reports and decompression caches stay under Analysis
`work/`. The source checkout is read-only. Evidence paths in query results are
relative to `CTW_ROOT`; generated query commands run from the Analysis root.
Keep `CTW_ROOT` set while following navigation links.

## Preservation and review status

- The seven implementation/test files were ported from the exact PR head. Changes
  separate source, code and output roots and update emitted command paths.
- Raw source exports remain byte-identical in the database's separate source-only
  branch. No raw game tables or generated SQLite archives are copied into Analysis.
- The full former review conversation is retained in `pr12-review-history.json`.
  Original review URLs remain authoritative; historical requests to dispatch
  workers are records, not new instructions.
- `reference.md` and `exercises.md` preserve the former detailed documentation.
  Their old layout/build sections are historical; use the commands above.
- The original PR and branch preserve all seven commits and published artifacts.
  Neither replacement is merged by this migration.

The last review reported two outstanding findings: portable mixed-case ordering
in character reconciliation, and silently ignored unsupported query options
(including numeric zero). This migration carries those findings forward; a fresh
build passing on one platform does not resolve cross-platform ordering. Keep this
PR draft until those findings have explicit fixes and regression evidence.

Further stacking, legal-build construction, comparison assumptions and ranking
belong here. They are not prerequisites for merging the raw database extraction.
