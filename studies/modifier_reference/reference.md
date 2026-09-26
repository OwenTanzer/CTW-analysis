> Migrated from database PR #12. Paths describing the former in-repository layout below are historical; use README.md here for current setup, build, and validation.

# Optional per-unit modifier reference

Patch **8.1.1**, Steam build **24237342**. This first pass organizes the **entire
220-table extraction**, preserves all 109,408 decoded rows, and applies one
retrieval model across all 1,669 distinct units in the existing normalized roster,
plus modifier access for all 461 known character forms outside that roster.
It is a source-backed reference, not an active campaign-state or final-stat calculator.

## Retrieve only the layer needed

1. **Base unit:** keep using `data/unit_stats/normalized/`. The `unit` command below
   reads that dataset directly and never opens the modifier database.
2. **Modifier index:** use `unit_index.csv`, then the paginated `--modifiers`
   query. It returns a compact bonus-type summary, potential relationships,
   source counts, rank bounds, scope keys and links to shared details.
3. **Shared details:** follow `effect`, `source`, or `record`. These expose exact
   bindings, source values, owner/variant/node references, scope fields and raw
   schema references. No full skill or technology tree is copied into each unit.

Python 3.10+ (standard library only) and Node 24+ are required for build/validation.
On Windows, use the installed Python executable in place of `python3` if needed.

```bash
# Base comparison: no campaign-reference load.
python3 src/ctw_analysis/modifier_reference/query-modifier-reference.py unit wh2_main_hef_inf_lothern_sea_guard_0

# Candidate modifiers, filtered to sources owned by Teclis, at unit rank 7.
python3 src/ctw_analysis/modifier_reference/query-modifier-reference.py unit wh2_main_hef_inf_lothern_sea_guard_0 --modifiers --owner wh2_main_hef_teclis --rank 7 --limit 10

# Narrow further with --bonus ammo_mod or --source-kind technology.
python3 src/ctw_analysis/modifier_reference/query-modifier-reference.py effect wh2_main_effect_force_stat_ammunition_hef_archer_seaguard_boltthrower_sister_shadow --owner wh2_main_hef_teclis

# Substitute returned IDs; IDs are local to the manifest's snapshot.
python3 src/ctw_analysis/modifier_reference/query-modifier-reference.py source 1 --limit 10
python3 src/ctw_analysis/modifier_reference/query-modifier-reference.py record 1 --limit 5

# Browse the full extraction or the observed gaps without expanding a unit dossier.
python3 src/ctw_analysis/modifier_reference/query-modifier-reference.py inventory --limit 20
python3 src/ctw_analysis/modifier_reference/query-modifier-reference.py table unit_experience_bonuses_tables
python3 src/ctw_analysis/modifier_reference/query-modifier-reference.py gaps --kind effects --limit 10
python3 src/ctw_analysis/modifier_reference/query-modifier-reference.py gaps --kind sets --limit 10
```

Every potentially long result is paginated (`--limit`, `--offset`, `next_offset`).
The default is 10; the hard limit is 100. Keys are exact game keys, not guessed
labels. `--owner` selects the character subtype or faction **owning the source**;
it does not establish recruitment access, current ownership or effect scope.
An effect page retains all of that effect's target bindings even when source
ownership is filtered. Scope may restrict a nominal unit-set match to the lord,
an army or another recipient: inspect it before treating a candidate as a buff.

The default unit view now omits **character-only source occurrences** for units
whose source caste is neither lord nor hero. Classification uses the retained
scope's `target` field, not its key or English label. An effect with both personal
and army sources keeps its army sources. `--evidence` restores personal sources;
`effect` and `source` always preserve them. Unknown/conditional recipient enums
remain visible as `unresolved_scope`. Force/army and faction classifications
describe recipients, not proof of active applicability. For lord/hero units,
self-character scopes additionally match the source owner's supported body forms.
A known different character is omitted by default and retained by `--evidence`.
Unmapped bodies/owners remain explicitly `unresolved_character_identity`.
Character-targeted faction/area scopes remain `character_recipient_context_unresolved`;
they are not mistaken for self-character effects. Matching occurs per source-owner
occurrence, so shared skills retain legitimate sources without admitting another
owner's personal effects.
The compact CSV index counts unfiltered evidence, not this filtered query view.

## Character forms and mount acquisition

```bash
python3 src/ctw_analysis/modifier_reference/query-modifier-reference.py character wh_main_emp_karl_franz
python3 src/ctw_analysis/modifier_reference/query-modifier-reference.py unit wh_main_emp_cha_karl_franz_1 --modifiers --owner wh_main_emp_karl_franz
```

`character` queries return paginated forms and independent mount-grant records,
even where the mount-unlock effect has no effect binding. Identity follows
`agent_subtypes.associated_unit_override` and actual owner-tree `ancillary_grant`
rows to `ancillaries.provided_bodyguard_unit`. Neither matching names nor unit-key
prefixes establish identity or availability. `character_forms`, `mount_records`
and `mount_acquisitions` preserve the evidence; `character_scope_policies` derives
self-recipient scope only from source, target, location and ownership fields.

Identity comparison uses explicit base-body anchors. A mounted target requires
agreement between skill-grant base identities and retained custom-battle base-to-
mount relations. Distinct source and target base anchors support a negative
identity conclusion only when target evidence is complete and consistent.
Missing source anchors, shared anchors without an owner pair, or incomplete target
paths remain `unresolved_character_identity`. Disagreeing path sets remain
`conflicting_character_identity`, including positive grant pairs; no names or
key suffixes resolve them. Custom-battle evidence corroborates identity only and
never establishes campaign acquisition. Both paths and their original row evidence
are returned in `character_identity_resolution` / form `identity_resolution`.
Path-set disagreement can reflect partial coverage or shared bodies, not necessarily
a game-data error. Nine bodies have disagreements in this snapshot; 655 mounted
bodies have corroborated identity and 444 have supported base identity.

Every form has `normalized_base_stat_coverage`. Only `available` forms emit
`base_query`; unavailable forms retain their usable source path/row evidence and
identity paths. Every form emits a working `modifier_query` and `owner_effects_query`,
including forms without normalized stats. Supplemental queries use exact retained
main/land-unit classification, abilities and attribute-group relations. All 461
forms have those main/land rows in this snapshot. Unknown predicate fields remain
unresolved; an unknown caste never silently excludes personal sources.
There are 461 distinct forms outside normalized coverage, spanning
534 relations and 229 owners. No statistics are fabricated for them.

```bash
# A form without a normalized base-stat card still has modifier retrieval.
python3 src/ctw_analysis/modifier_reference/query-modifier-reference.py unit wh2_dlc17_bst_cha_beastlord_2 --modifiers --owner wh_dlc03_bst_beastlord
# Browse EVERY effect-bearing owner occurrence, including effects without bindings.
python3 src/ctw_analysis/modifier_reference/query-modifier-reference.py owner wh_dlc03_bst_beastlord --source-kind skill --limit 10
```

`owner --source-kind skill` selects character records. Some game keys are shared
by a character and a faction; without this filter both kinds are retained and
explicitly labelled by `owner_kind` and source path.

`owner` queries paginate exact effect, value, scope, skill level, node context and
source path/row. Each entry links to shared effect/source detail and states whether
its target route is typed, unresolved, or absent from this extraction. Separate
paginated `indirect_references` retain contextual source gaps. These lists include
army and contextual effects as well as personal effects; presence does not establish
applicability to a particular form. Query access does not require a decoded target.

`character_source_reconciliation.json` independently starts from all 500 character
files and reconciles all **140,413 effect-bearing occurrences**: 52,723 have a typed
unit-target route, 86,619 retain bindings whose targets are not unit-resolved, and
1,071 have no binding in the extraction. These are occurrence counts, with rank/node
alternatives retained. The report does not claim every campaign modifier is decoded;
indirect source references and runtime behavior remain separate boundaries.

The nine identity disagreements were checked against underlying skill-node,
node-set/subtype, enabled set-item, and skill-level ancillary-grant rows. All 15
owner/form paths match those raw relations: no normalized-join error was found.
Five bodies have overlapping path sets (grant anchors strictly contain custom-battle
anchors: Overseer Taurus/Lammasu, Magistrate horse, two Fleet Admiral forms). Four
Prophetess bodies have disjoint Life/Heavens path sets. `path_comparison` exposes
shared, grant-only and custom-battle-only bases. Shared bodies/partial custom-battle
coverage can explain overlap; the disjoint sets remain unresolved. No name-based
remapping or campaign acquisition claim is warranted. Affected personal modifier
candidates retain the conflict status and source-owner detail links.

Forms within normalized coverage keep their own base stats, attributes and abilities.
A body with an ancillary but no indexed grant remains `unconfirmed_acquisition`
in its unit view; it is not added to a character's supported forms. A grant route
still does not prove current acquisition, faction access or prerequisite satisfaction.

Mount records retain `node_rank`, the exact skill-level records (including
`level_unlocked_at_rank` and their campaign/faction conditions), and a rank status.
`effective_unlock_rank` remains null: the reference does not select one field,
take their maximum or add a rank offset. Franz's Barded Warhorse retains **6 vs 3**;
Pegasus retains **11 vs 11**, Deathclaw **15 vs 15**. Agreement is reported without
certifying engine unlock semantics. `--rank` remains unit experience rank and
does not filter character acquisition; use the `character` query for its evidence.

## Files and ownership

| File | Role |
|---|---|
| `unit_index.csv` | Small directory covering every normalized roster unit, with candidate counts |
| `reference.sqlite.gz` | Compressed generated query database; shared relations are stored once |
| `table_inventory.json` | All 220 source tables, classification and row totals |
| `schema_inventory.json` | Generated tables/views and their columns |
| `coverage_report.json` | Coverage, unresolved paths, counts and interpretation boundaries |
| `character_source_reconciliation.json` | Independent all-character occurrence counts and per-owner retrieval links |
| `validation_report.json` | Latest successful reconciliation, locked to the dataset manifest |
| `dataset_manifest.json` | Hashes, input locks, snapshot and generator environment |
| `source_exports/` | Complete unchanged validated extraction, its schema and source manifest |

The query helper verifies hashes and decompresses the database into ignored
`work/modifier-reference-cache/` on first detail retrieval. Do not commit that
cache or load the whole database into an agent context. SQLite is the generated
query form; source exports and existing datasets retain their source authority.
The archive is about 16 MiB. Base records retain their existing schema and size.

`source_records` preserves every decoded field, including blanks, using JSON
objects; `schema_fields` retains types and references. `bindings`, `target_sets`,
`target_selectors`, `unit_targets`, `sources` and `source_occurrences` provide the
typed retrieval layer. `source_tables` assigns every extracted table a family.
Supporting visual/audio records remain retained and discoverable; they are not
forced into the per-unit modifier list merely because extraction followed a
schema dependency to them.

Skill/technology source definitions are deduplicated while their owner, node,
variant, rank and conditional occurrences remain separate. Existing source CSV
paths and physical row references lead back to the authoritative prerequisites
and locks. This is a derived join index, not a replacement skill/technology tree.
Native progression and ability tables are exposed as source evidence; future
normalized progression/ability datasets remain owned by `data/unit_stats/`.

## Consistent candidate rules

- Unit keys, source caste, source class and source category are taken from the
  unit dataset and its retained game tables. The curated `tactical_category`
  is not substituted into game targeting predicates.
- Selector rows and exclusions are retained verbatim. Within the candidate
  compiler, a definite exclusion overrides an inclusion. This explicit retrieval
  policy is not a claim to have traced engine precedence.
- A multi-field selector is a definite match only when all specified predicates
  match, a non-match when none match, and otherwise remains possible. Unknown
  AND/OR grouping is not silently invented. Special categories remain conditional.
- Sets without positive selectors are unresolved, never universal or proven empty.
  Sets with no matches in the normalized roster are distinguished from missing
  selector evidence. Explicit targets outside that roster remain in shared bindings.
- Rank bounds are preserved and applied only when `--rank` is supplied. They are
  unit experience levels, not lord skill levels.
- Direct unit/set references and the extracted set-to-ability/attribute/phase and
  missile-weapon-to-unit junctions support typed target paths. Existing base
  abilities and attributes support reverse candidate lookup. Arbitrary schema
  references are never treated as proof of who receives an effect.
- Ability grants are indexed by explicit recipient sets. Newly granted abilities
  are not recursively propagated into all later ability-modifier possibilities.
- Scope recipient classification separates demonstrably personal sources; active
  scope and acquisition remain unevaluated. A `selector_match` is a static source
  match, not a statement that the effect is active. Skill levels are alternatives;
  their values must not be summed. Faction variants are not simultaneous bonuses.
- Identifiers such as `*_mod` and `*_mult` are preserved as bonus keys; their
  spelling is not used to infer engine arithmetic, rounding, stacking or caps.

### Weapon activation and rank are separate questions

Every missile-weapon junction binding has `unresolved_weapon_activation` and
`unresolved_rank_activation`, with independently traced junction, weapon and
projectile evidence available on its effect page. These retained records establish
which weapon belongs to which unit; they do not establish the engine's activation
predicate. Such candidates remain visible at both queried ranks with null
`rank_match` and explicit unresolved eligibility. A route without a modeled rank
predicate is never labelled rank-eligible. Explicit unit-set rank predicates are
still applied, independently, and only certify that predicate rather than a buff.

For Seasoned Militia, both Sea Guard junctions lead to
`wh3_dlc27_hef_sea_guard_bow_anti_infantry`, whose default projectile is
`wh3_dlc27_hef_sea_guard_arrow_anti_infantry`. The retained weapon fields, projectile
fields and additional-projectile relation provide no rank activation predicate.
The reload binding independently targets `wh3_dlc27_hef_all_bow_inf_r7` with an
explicit 7–9 range. We do **not** transfer that range to the weapon-enable binding
or derive it from `_r7`/localized text. The unresolved question is the engine rule
governing the enabled weapon's rank-dependent selection, not a demonstrated game bug.
No additional extraction was performed or prescribed by this audit.

## First full coverage result

- 220 source tables / 109,408 rows preserved.
- 23,110 bindings retained; 14,850 have a supported typed unit-reference route.
  Of those, 10,411 have at least one candidate in the current normalized roster.
- All 1,669 roster units receive an index entry; the index is deliberately not a
  completeness certificate for all campaign modifiers.
- 19,241 distinct skill/technology effect-source definitions link to 153,930
  owner occurrences without flattening rank, node or faction-variant context.
- Scope recipients classify 10,182 definitions as character-only, 6,255 as
  force/army, 1,176 as faction context and 1,628 as unresolved recipient semantics.
  All 254 missile-weapon-junction bindings retain unresolved activation evidence.
- Among effects used by those sources, 4,908 have a supported unit-reference
  binding, 1,526 have retained bindings without that route, and 400 have no binding
  in this extraction. These are unique effect counts, not occurrence counts.
- 44 sets require special-category interpretation; 80 lack positive selectors;
  250 have no current roster match. One selector combines multiple fields.
- 89 explicit unit-target bindings refer outside the normalized roster.
- 115 schema-reference fields point to tables absent from both this extraction
  and the checked existing source snapshots. They may include engine enums;
  absence does not itself establish a missing export worth collecting.

The 8,260 bindings not indexed by unit include economic and other contextual
references as well as unresolved unit-related routes. Their target-type breakdown
is in the coverage report. `source_gaps` preserves indirect source references
such as initiative effects and unlocks; conditional effect rows already present
are indexed independently. These references are not all failed extractions.

The unbound-source-effect examples with the most source definitions are mount
unlocks (Chaos Steed, Barded Warhorse, Elven Steed, Great Eagle, Warshrine).
That is a concrete lead for subsequent investigation, not authorization to infer
their missing bindings. Use the gap queries to decide what merits more work.

## Reproduction and acceptance

```bash
python3 scripts/build-modifier-reference.py data/effect_semantics/source_exports work/modifier-candidate-new
python3 scripts/validate-modifier-reference.py work/modifier-candidate-new data/effect_semantics/source_exports
python3 scripts/test-modifier-reference.py work/modifier-candidate-new
```

The builder requires a fresh ignored output directory and first runs the existing
source-integrity validator. Promote the candidate artifacts only after validation
passes; preserve source bytes unchanged. Update catalog, manifest, schema inventory,
README and coverage together. Build twice in the same Python/SQLite environment
to check byte reproducibility; cross-version SQLite serialization can differ even
when logical data is identical. Gzip timestamps and filenames are fixed.

Validation independently recomputes all supplemental targets from raw selectors,
classification fields, abilities and attribute groups; tests deliberately delete
a target and a typed binding route to ensure omissions fail. All form links and
all owner effect pages are exercised, including unbound effects. Separate mutation
tests remove an entire owner or one source occurrence.

Validation reconciles every source row, every unit record and every owner effect
occurrence with the source snapshots, checks hashes and foreign keys, verifies the
compact index counts, and prevents explicit exclusions from leaking into candidates.
Focused tests cover Sea Guard variants vs spearmen, rank filtering, compound
selectors, exclusions, source-level alternatives, pagination and base-query isolation.
They also cover personal/army separation, mixed and unknown scopes, independently
unresolved weapon activation at ranks 6/7, and actual variant-specific bindings.
Franz regressions verify supported forms, personal-source owner isolation,
unconfirmed Warhorse status and conflicting mount-rank evidence.

This fulfills the first organization pass under issue #7. It does not close that
issue: complete acquisition/compatibility, bespoke campaign systems, native rank
formulas and legal maximum-build comparisons remain open.
