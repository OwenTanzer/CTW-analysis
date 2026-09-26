> Migrated from database PR #12. Paths describing the former in-repository layout below are historical; use README.md here for current setup, build, and validation.

# Modifier retrieval exercises

Snapshot: WARHAMMER III 8.1.1 / Steam build 24237342. These are source-backed
retrieval checks, not live campaign tests or a combined acquired build.

## Sea Guard regression

Both `wh2_main_hef_inf_lothern_sea_guard_0` and `_1`, source owner
`wh2_main_hef_teclis`, return 10 default bindings versus 17 in `--evidence`.
Seven personal-source bindings are excluded from ordinary-unit results. Army
sources for Bow Master and Militia Master remain; Favourable Winds' rank-7 armour
binding is absent at rank 6 and present at rank 7. Ordinary spearmen do not enter
Bow Master's ammunition target set. Distinct shielded-armour and unshielded
Sea Guard morale relationships remain variant-specific.

Seasoned Militia's weapon-enable binding remains **unresolved activation/rank**
at both queried ranks. Existing junction → weapon → projectile evidence contains
no independently established rank predicate. Its reload binding separately has
an explicit unit-set gate of ranks 7–9. No condition is transferred between them.

## Pink Horrors

Exact owners: skill subtype `wh3_main_tze_kairos`; technology faction
`wh3_main_tze_oracles_of_tzeentch` (Oracles of Tzeentch). Compare ordinary Pink
`wh3_main_tze_inf_pink_horrors_0`, Exalted Pink `_1`, and Blue
`wh3_main_tze_inf_blue_horrors_0`.

```bash
python3 src/ctw_analysis/modifier_reference/query-modifier-reference.py unit wh3_main_tze_inf_pink_horrors_0
python3 src/ctw_analysis/modifier_reference/query-modifier-reference.py unit wh3_main_tze_inf_pink_horrors_0 --modifiers --owner wh3_main_tze_kairos --rank 7 --limit 100
python3 src/ctw_analysis/modifier_reference/query-modifier-reference.py unit wh3_main_tze_inf_pink_horrors_0 --modifiers --owner wh3_main_tze_oracles_of_tzeentch --limit 100
python3 src/ctw_analysis/modifier_reference/query-modifier-reference.py effect wh3_main_effect_ability_enable_arcane_mirth_tze_horrors --owner wh3_main_tze_oracles_of_tzeentch
```

The base path remains independent of the modifier database. Ordinary Pink has
80 entities, 6,400 health, 25 armour, 20 ammunition and 130 range in the normalized
rank-0/ultra snapshot. Exalted Pink has 80/7,200/35/20/130; Blue has
160/7,680/5/4/90 for those same fields. These are not campaign-adjusted values.

At rank 7, all three have 12 Kairos-linked default binding results; at rank 6,
ordinary Pink has nine. The three added bindings are independently rank-gated
Horrific Advancement effects. Character-only Kairos sources remain in evidence
mode but do not mix into these ordinary-unit results.

| Source | Retained values and target |
|---|---|
| Accursed Horrors, level 3 | Missile damage source value 16 (separate base/AP bindings); barrier health source value 10; all three Horror types |
| Horrific Advancement | Leadership 6, missile resistance 15, battle speed 10; explicit unit ranks 7–9; all three |
| Oracle | Barrier replenishment delay -20; own force while commanding |
| Thaumaturgic Rejuvenation | Barrier replenishment delay -25 and replenishment rate 25; own force while commanding |
| Bewitching Potency | Barrier health 20; own force while commanding |
| Exalted Locus of Conjuration | Ammunition 25 and melee weapon strength 25 (base/AP bindings); all three; faction's own forces |
| Greater Locus of Change | Arcane Mirth enable value 1: ordinary Pink and Blue, not Exalted; recruitment cost -5: both Pink types, not Blue |

Values are the original source numbers. Their arithmetic/stacking is not
evaluated. Skill levels are alternatives, not additive repetitions. Relevant
canonical skill keys are `wh3_main_skill_tze_lord_army_buff_1_1`,
`wh3_main_skill_tze_lord_army_buff_3_1`, `wh3_main_skill_innate_tze_kairos`,
`wh3_main_skill_tze_kairos_self_6` and `_8`; technology keys are
`wh3_main_tech_tze_2_8` and `wh3_main_tech_tze_2_6`. Source pages retain their
exact owner CSV rows, node sets and prerequisites.

The technology view returns eight bindings for ordinary Pink, seven for Exalted,
seven for Blue. Equal totals for Exalted and Blue conceal different membership:
Exalted already has Arcane Mirth in the base ability lookup, while Blue does not
receive the Pink-specific recruitment reduction. Regression tests check identities,
not just counts.

### What this exercise does not establish

The Arcane Mirth grant is linked to its actual unit-set/ability junction, and the
retained special-ability record marks it passive. The base ability record has
`requires_effect_enabling=false`; that does not grant it to units lacking the
base ability or technology grant. Runtime trigger/phase transitions are not
evaluated by this reference. The retained phase with the same key contains
`mana_regen_mod=0.2000`, but matching names alone do not establish an activation
or phase-transition relationship. Do not promote that into an unconditional
mana contribution. A complete ability-to-phase/usage-condition trace remains a
separate unresolved question; this exercise did not extract more tables.

## Karl Franz: identity and mount acquisition

`character wh_main_emp_karl_franz` returns the subtype's foot body (`_0`) and
three owner-tree mount grants: Barded Warhorse (`_4`), Imperial Pegasus (`_2`)
and Deathclaw (`_1`). Each follows a real ancillary-grant record to the ancillary's
provided bodyguard unit. All three related mount-unlock effects have zero effect
bindings, so this retrieval explicitly operates independently of those bindings.
The ordinary Warhorse body (`_3`) retains its mount ancillary but no Franz tree
grant; its acquisition and character identity remain unconfirmed. No label/key
similarity is promoted into a selectable form.

For all four supported forms, Franz's own personal attack and defence sources
remain in default queries. Teclis's self-character sources are omitted there and
restored with explicit identity mismatch in `--evidence`. Shared generic skills
are checked per owner occurrence. Other character-recipient scopes (factionwide,
area effects, and unknown context) are not assumed to target their source owner.

Barded Warhorse preserves node rank 6 and level rank 3 as distinct evidence.
Pegasus preserves 11/11 and Deathclaw 15/15. Effective unlock ranks remain null
in every case; prerequisites and engine rank interpretation are not evaluated.
The forms retain distinct base stats and attribute/ability memberships: no foot
record or mount trait is copied across bodies. All lists remain candidates.

## Identity conflicts and missing normalized forms

The generalization review and adversarial pass exposed two blockers; the schema-4
candidate addresses both across the dataset. Life and Heavens Prophetess queries
now retain all ten candidate bindings, including the six personal bindings that
were formerly suppressed. Both the campaign skill-grant path and custom-battle
base-to-mount path remain visible; disagreement is not repaired by changing keys.

Negative personal identity now requires a retained explicit source base-body
anchor and a distinct target base identity. Mounted targets require agreement
between retained grant-derived and custom-battle base identities; unanchored grant
owners prevent negative proof. Shared base identities, missing evidence and
conflicting paths remain unresolved. A grant pair does not override a conflicting
or incomplete global target identity. Removing the Necrotect shared-body anchor
therefore changes match to unresolved; deleting a Prophetess conflicting
custom-battle path also remains unresolved rather than becoming a positive match.
Franz's four supported forms retain his personal bonuses and omit Teclis's.

The full raw custom-battle table has 681 paths. Nine form bodies have differing
path sets: the four Prophetess bodies, two Vampire Fleet Admiral bodies, the
Overseer's Great Taurus and Lammasu, and the Lord Magistrate mounted body.
These are evidence disagreements, including potentially partial coverage or
shared-body usage, not nine asserted gameplay defects. Another 655 mounted bodies
have corroborated identity and 444 have supported base identities. Custom-battle
relations remain identity evidence only, never proof of campaign acquisition.

All 500 character-owner outputs are checked against normalized unit CSV keys.
The 461 forms outside that roster (534 relations across 229 owners) now say
`normalized_base_stat_coverage: unavailable`, retain their source path/row evidence,
and offer no failing base query. The Beastlord's `wh2_dlc17_bst_cha_beastlord_2`
provides a non-Franz regression. No statistics or further extraction were added.

The expanded 26-test suite includes the original 20 controls, eight Prophetess
owner/form combinations, both evidence-removal cases, the full conflict and
navigation audits, and concurrent first-open cache reads. The latter reproduced
a shared-temporary-file race during validation; each reader now publishes its
verified database through a unique temporary file and atomic replacement.
