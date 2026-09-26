"""Build the whole extracted source into an indexed, optional unit reference.

No final-stat arithmetic or campaign availability is inferred. Generated queries
return candidate relationships, with source records and unresolved paths intact.
"""
import argparse
import csv
import gzip
import json
import sqlite3
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from character_reference import build_characters, character_coverage
from modifier_reference import ROOT, ANALYSIS_ROOT, SELECTORS, compact, digest, family, records, selector_match, membership_status, scope_classification


from reference_access import build_form_access, reconcile_character_sources


def build(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.is_relative_to(ROOT):
        raise ValueError('The CTW source checkout is read-only')
    if not output.is_relative_to(ANALYSIS_ROOT / 'work'):
        raise ValueError('Build candidates under ignored work/ before promotion')
    if output.exists() and any(output.iterdir()):
        raise ValueError('Use a fresh candidate directory')
    subprocess.run(['node', str(ROOT / 'scripts/validate-effect-source.mjs'), str(source)],
                   check=True, stdout=subprocess.PIPE, text=True)
    output.mkdir(parents=True, exist_ok=True)
    source_manifest = json.loads((source / 'source_manifest.json').read_text())
    schema = json.loads((source / 'decoded_schema.json').read_text())
    inputs = {}

    def lock(path):
        path = Path(path)
        key = path.relative_to(ROOT).as_posix()
        inputs[key] = digest(path.read_bytes())
        return path

    def external_rows(path):
        return records(lock(path))

    dbpath = output / 'reference.sqlite'
    db = sqlite3.connect(dbpath)
    db.executescript('''
    PRAGMA page_size=4096;
    PRAGMA foreign_keys=ON;
    CREATE TABLE source_tables(table_key TEXT PRIMARY KEY,family TEXT,row_count INTEGER,source_path TEXT,sha256 TEXT,version INTEGER,columns_json TEXT);
    CREATE TABLE source_records(id INTEGER PRIMARY KEY,table_key TEXT REFERENCES source_tables,source_row INTEGER,payload_json TEXT);
    CREATE INDEX records_table ON source_records(table_key);
    CREATE TABLE schema_fields(table_key TEXT,column_key TEXT,field_type TEXT,is_key INTEGER,reference_table TEXT,reference_column TEXT,annotations_json TEXT,PRIMARY KEY(table_key,column_key));
    CREATE TABLE schema_links(table_key TEXT,column_key TEXT,target_table TEXT,target_column TEXT,status TEXT,PRIMARY KEY(table_key,column_key));
    CREATE TABLE effects(effect_key TEXT PRIMARY KEY,category TEXT,label TEXT,record_id INTEGER REFERENCES source_records,status TEXT);
    CREATE TABLE bindings(id INTEGER PRIMARY KEY,effect_key TEXT REFERENCES effects,bonus_key TEXT,target_table TEXT,target_column TEXT,target_key TEXT,route TEXT,status TEXT,record_id INTEGER REFERENCES source_records);
    CREATE INDEX binding_effect ON bindings(effect_key);
    CREATE TABLE binding_targets(binding_id INTEGER REFERENCES bindings,kind TEXT,target_key TEXT,evidence_record INTEGER REFERENCES source_records,PRIMARY KEY(binding_id,kind,target_key));
    CREATE INDEX targets_lookup ON binding_targets(kind,target_key);
    CREATE TABLE target_sets(set_key TEXT PRIMARY KEY,rank_enabled TEXT,min_rank TEXT,max_rank TEXT,special_category TEXT,record_id INTEGER REFERENCES source_records);
    CREATE TABLE target_selectors(record_id INTEGER PRIMARY KEY REFERENCES source_records,set_key TEXT REFERENCES target_sets,exclude TEXT,predicate_count INTEGER,unit_record TEXT,unit_class TEXT,unit_category TEXT,unit_caste TEXT);
    CREATE INDEX selectors_set ON target_selectors(set_key);
    CREATE TABLE units(unit_key TEXT PRIMARY KEY,unit_name TEXT,source_unit_class TEXT,source_category TEXT,source_caste TEXT);
    CREATE TABLE unit_records(unit_key TEXT REFERENCES units,race_slug TEXT,source_path TEXT,source_row INTEGER,PRIMARY KEY(unit_key,race_slug));
    CREATE TABLE selector_matches(record_id INTEGER REFERENCES target_selectors,unit_key TEXT REFERENCES units,status TEXT,PRIMARY KEY(record_id,unit_key));
    CREATE INDEX selector_unit ON selector_matches(unit_key);
    CREATE TABLE unit_targets(kind TEXT,target_key TEXT,unit_key TEXT REFERENCES units,status TEXT,PRIMARY KEY(kind,target_key,unit_key));
    CREATE INDEX unit_target_lookup ON unit_targets(unit_key,kind,target_key);
    CREATE TABLE owners(id INTEGER PRIMARY KEY,kind TEXT,owner_key TEXT,label TEXT,race_slug TEXT,faction_key TEXT,source_path TEXT UNIQUE);
    CREATE TABLE sources(id INTEGER PRIMARY KEY,kind TEXT,source_key TEXT,label TEXT,skill_level TEXT,effect_key TEXT REFERENCES effects,scope_key TEXT,value TEXT,record_type TEXT,conditions_json TEXT,canonical_key TEXT UNIQUE);
    CREATE INDEX sources_effect ON sources(effect_key);
    CREATE TABLE source_occurrences(id INTEGER PRIMARY KEY,source_id INTEGER REFERENCES sources,owner_id INTEGER REFERENCES owners,source_row INTEGER,node_key TEXT,node_set_key TEXT,variant_key TEXT,campaign_key TEXT);
    CREATE INDEX occurrences_source ON source_occurrences(source_id,owner_id);
    CREATE INDEX occurrences_owner ON source_occurrences(owner_id,source_id);
    CREATE TABLE scope_classifications(scope_key TEXT PRIMARY KEY,recipient TEXT,classification TEXT,record_id INTEGER REFERENCES source_records);
    CREATE VIEW classified_sources AS SELECT s.*,COALESCE(sc.classification,'unresolved_scope') scope_classification,sc.recipient,sc.record_id scope_record_id FROM sources s LEFT JOIN scope_classifications sc USING(scope_key);
    CREATE VIEW classified_source_occurrences AS SELECT so.*,s.scope_key,s.scope_classification,s.scope_record_id FROM source_occurrences so JOIN classified_sources s ON s.id=so.source_id;
    CREATE TABLE binding_activation(binding_id INTEGER PRIMARY KEY REFERENCES bindings,status TEXT,rank_status TEXT,evidence_json TEXT);
    CREATE TABLE source_gaps(owner_id INTEGER REFERENCES owners,source_row INTEGER,record_type TEXT,status TEXT,detail_json TEXT);
    CREATE TABLE set_coverage(set_key TEXT PRIMARY KEY REFERENCES target_sets,selector_count INTEGER,candidate_units INTEGER,excluded_units INTEGER,status TEXT);
    CREATE VIEW unit_binding_candidates AS
      SELECT u.unit_key,b.binding_id,u.kind,u.target_key,u.status
      FROM unit_targets u JOIN binding_targets b USING(kind,target_key);
    CREATE VIEW effect_coverage AS
      SELECT e.effect_key,e.status,COUNT(DISTINCT b.id) binding_count,
      (SELECT COUNT(*) FROM sources s WHERE s.effect_key=e.effect_key) source_count,
      COUNT(DISTINCT CASE WHEN b.status='unit_target_indexed' THEN b.id END) indexed_bindings
      FROM effects e LEFT JOIN bindings b ON b.effect_key=e.effect_key GROUP BY e.effect_key;
    ''')
    loaded = defaultdict(list)
    field_defs = {}
    raw_id = 0
    for f in sorted(source_manifest['files'], key=lambda f: f['path']):
        if not f['path'].startswith('db/'):
            continue
        path = source / f['path']
        text = path.read_text(encoding='utf-8-sig')
        table = f['path'].split('/')[1]
        version = int(text.splitlines()[1].split(';')[1])
        definition = next(d for d in schema[table] if d['version'] == version)
        rows = list(records(path, '\t'))
        cols = next(csv.reader([text.splitlines()[0]], delimiter='\t'))
        db.execute('INSERT INTO source_tables VALUES(?,?,?,?,?,?,?)',
                   (table, family(table), len(rows), f['path'], f['sha256'], version, compact(cols)))
        field_defs[table] = definition['fields']
        for field in definition['fields']:
            ref = field.get('is_reference')
            target = ref[0] + ('' if ref[0].endswith('_tables') else '_tables') if ref else ''
            db.execute('INSERT INTO schema_fields VALUES(?,?,?,?,?,?,?)',
                       (table, field['name'], compact(field['field_type']), int(field.get('is_key', False)), target,
                        ref[1] if ref else '', compact({k: v for k, v in field.items() if k not in ('name','field_type','is_key','is_reference')})))
        for line, row in rows:
            raw_id += 1
            db.execute('INSERT INTO source_records VALUES(?,?,?,?)', (raw_id, table, line, compact(row)))
            loaded[table].append((raw_id, row))
    for table, fields in field_defs.items():
        for f in fields:
            ref = f.get('is_reference')
            if not ref:
                continue
            target = ref[0] + ('' if ref[0].endswith('_tables') else '_tables')
            existing = any((ROOT / 'data' / d / 'source_exports/db' / target).exists() for d in ('unit_stats','skill_trees','technology_trees','economy'))
            status = 'in_extraction' if target in loaded else 'existing_dataset' if existing else 'unresolved_reference'
            db.execute('INSERT INTO schema_links VALUES(?,?,?,?,?)', (table, f['name'], target, ref[1], status))
    known_effects = set()

    def ensure_effect(key, label=''):
        if key not in known_effects:
            db.execute('INSERT INTO effects VALUES(?,?,?,?,?)', (key, '', label, None, 'missing_definition'))
            known_effects.add(key)
        elif label:
            db.execute("UPDATE effects SET label=? WHERE effect_key=? AND label=''", (label, key))

    for rid, r in loaded['effects_tables']:
        db.execute('INSERT INTO effects VALUES(?,?,?,?,?)', (r['effect'], r['category'], '', rid, 'source_defined'))
        known_effects.add(r['effect'])
    for rid, r in loaded['campaign_effect_scopes_tables']:
        db.execute('INSERT INTO scope_classifications VALUES(?,?,?,?)',
                   (r['key'],r['target'],scope_classification(r),rid))

    # Exact source classification fields, never the curated tactical category.
    def raw_external(table, key):
        result = {}
        for p in sorted((ROOT / 'data/unit_stats/source_exports/db' / table).glob('*.tsv')):
            for _, row in records(lock(p), '\t'):
                result[row[key]] = row
        return result
    main_units = raw_external('main_units_tables', 'unit')
    land_units = raw_external('land_units_tables', 'key')
    units = {}
    for path in sorted((ROOT / 'data/unit_stats/normalized').glob('*.csv')):
        race = path.name.split('__')[0]
        for line, r in external_rows(path):
            main = main_units[r['source_main_unit_key']]
            land = land_units[r['source_land_unit_key']]
            unit = dict(unit_key=r['unit_key'], unit_name=r['unit_name'], source_unit_class=land['class'],
                        source_category=land['category'], source_caste=main['caste'])
            if r['unit_key'] not in units:
                units[r['unit_key']] = unit
                db.execute('INSERT INTO units VALUES(?,?,?,?,?)', tuple(unit.values()))
            elif units[r['unit_key']] != unit:
                raise ValueError('Conflicting repeated unit: ' + r['unit_key'])
            db.execute('INSERT INTO unit_records VALUES(?,?,?,?)', (r['unit_key'], race, path.relative_to(ROOT).as_posix(), line))
    print(f'Indexed {len(units)} distinct units', flush=True)
    sets = {}
    selectors = defaultdict(list)
    for rid, r in loaded['unit_sets_tables']:
        sets[r['key']] = r
        db.execute('INSERT INTO target_sets VALUES(?,?,?,?,?,?)', (r['key'],r['use_unit_exp_level_range'],r['min_unit_exp_level_inclusive'],r['max_unit_exp_level_inclusive'],r['special_category'],rid))
    for rid, r in loaded['unit_set_to_unit_junctions_tables']:
        selectors[r['unit_set']].append((rid, r))
        db.execute('INSERT INTO target_selectors VALUES(?,?,?,?,?,?,?,?)', (rid,r['unit_set'],r['exclude'],sum(bool(r[k]) for k in SELECTORS),r['unit_record'],r['unit_class'],r['unit_category'],r['unit_caste']))
    # Invert selectors to avoid a table-by-unit Cartesian scan.
    dimensions = {field: defaultdict(set) for field in SELECTORS.values()}
    for unit in units.values():
        for field in dimensions:
            dimensions[field][unit[field]].add(unit['unit_key'])
    for key, s in sorted(sets.items()):
        per_unit = defaultdict(lambda: [[], []])
        for rid, r in selectors[key]:
            possible = set()
            active = [(field, r[src]) for src, field in SELECTORS.items() if r[src]]
            for field, value in active:
                possible.update(dimensions[field].get(value, set()))
            if not active:
                possible.update(units)
            for unit_key in sorted(possible):
                status = selector_match(r, units[unit_key])
                db.execute('INSERT INTO selector_matches VALUES(?,?,?)', (rid,unit_key,status))
                per_unit[unit_key][r['exclude'] == 'true'].append(status)
        included = excluded = 0
        for unit_key, (inc, exc) in sorted(per_unit.items()):
            status = membership_status(inc, exc, s['special_category'])
            if status == 'excluded':
                excluded += 1
            elif status != 'no_match':
                db.execute('INSERT INTO unit_targets VALUES(?,?,?,?)', ('unit_set',key,unit_key,status))
                included += 1
        has_include = any(r['exclude'] != 'true' for _, r in selectors[key])
        status = 'requires_special_category' if s['special_category'] else 'no_positive_selector' if not has_include else 'no_roster_match' if not included else 'selector_candidates_indexed'
        db.execute('INSERT INTO set_coverage VALUES(?,?,?,?,?)', (key,len(selectors[key]),included,excluded,status))
    for key in sorted(units):
        db.execute('INSERT INTO unit_targets VALUES(?,?,?,?)', ('unit',key,key,'explicit_unit'))
    for prefix, field, kind in [('unit_abilities','ability_key','ability'),('unit_attributes','attribute_key','attribute')]:
        for p in sorted((ROOT / 'data/unit_stats/lookups').glob(prefix+'__*.csv')):
            for _, r in external_rows(p):
                db.execute('INSERT OR IGNORE INTO unit_targets VALUES(?,?,?,?)', (kind,r[field],r['unit_key'],'existing_base_'+kind))

    # Only these source relations establish a unit target. Other references remain
    # navigable in full; never traverse an arbitrary graph as if it meant targeting.
    direct = {'unit_sets_tables': ('unit_set', None), 'main_units_tables': ('unit',None),
              'unit_abilities_tables': ('ability',None), 'unit_attributes_tables': ('attribute',None)}
    intermediates = {
        'unit_set_unit_ability_junctions_tables': ('unit_set','unit_set'),
        'unit_set_unit_attribute_junctions_tables': ('unit_set','unit_set'),
        'unit_set_special_ability_phase_junctions_tables': ('unit_set','unit_set'),
        'unit_missile_weapon_junctions_tables': ('unit','unit'),
    }
    binding_id = 0
    # Weapons/projectiles are already retained in unit_stats. Follow those typed
    # relations for evidence, without treating a sibling set's rank as a weapon gate.
    weapon_evidence = {}
    for table in ('missile_weapons_tables','missile_weapons_to_projectiles_tables','projectiles_tables'):
        weapon_evidence[table] = []
        for path in sorted((ROOT/'data/unit_stats/source_exports/db'/table).glob('*.tsv')):
            for line, row in records(lock(path),'\t'):
                weapon_evidence[table].append({'path':path.relative_to(ROOT).as_posix(),'row':line,'fields':row})
    for table in sorted(loaded):
        if family(table) != 'effect_bindings':
            continue
        fields = field_defs[table]
        refs = {f['name']: f['is_reference'] for f in fields if f.get('is_reference')}
        for rid, r in loaded[table]:
            binding_id += 1
            effect = r.get('effect') or r.get('effect_key')
            if not effect:
                raise ValueError('Unrecognized effect binding: '+table)
            ensure_effect(effect)
            bonus = r.get('bonus_value_id') or r.get('bonus_value') or ''
            targets = [(k, v) for k, v in refs.items() if k not in ('effect','effect_key','bonus_value','bonus_value_id')]
            if len(targets) > 1:
                raise ValueError('Unmodeled multi-target binding: '+table)
            col, ref = targets[0] if targets else ('', ['', ''])
            target_table = ref[0] + ('' if not ref[0] or ref[0].endswith('_tables') else '_tables')
            target_key = r.get(col, '')
            target = None
            if target_table in direct:
                target = (direct[target_table][0],target_key,rid)
            elif target_table in intermediates:
                kind, target_col = intermediates[target_table]
                matches = [(i, x) for i, x in loaded[target_table] if x[ref[1]] == target_key]
                if len(matches) == 1:
                    target = (kind,matches[0][1][target_col],matches[0][0])
            status = 'unit_target_indexed' if target else 'requires_context_or_target_resolution'
            route = 'direct_reference' if target_table in direct else 'typed_intermediate' if target else 'retained_reference'
            db.execute('INSERT INTO bindings VALUES(?,?,?,?,?,?,?,?,?)', (binding_id,effect,bonus,target_table,ref[1],target_key,route,status,rid))
            if target:
                db.execute('INSERT INTO binding_targets VALUES(?,?,?,?)', (binding_id,*target))
            if target_table == 'unit_missile_weapon_junctions_tables':
                evidence = [{'record_id':rid,'role':'effect_binding'}]
                for junction_id, junction in loaded[target_table]:
                    if junction[ref[1]] != target_key:
                        continue
                    evidence.append({'record_id':junction_id,'role':'unit_weapon_junction'})
                    weapon = junction['missile_weapon']
                    projectiles = set()
                    for item in weapon_evidence['missile_weapons_tables']:
                        if item['fields']['key'] == weapon:
                            evidence.append(dict(item,role='missile_weapon'))
                            projectiles.add(item['fields']['default_projectile'])
                    for item in weapon_evidence['missile_weapons_to_projectiles_tables']:
                        if item['fields']['missile_weapon'] == weapon:
                            evidence.append(dict(item,role='additional_projectile_relation'))
                            projectiles.add(item['fields']['projectile'])
                    for item in weapon_evidence['projectiles_tables']:
                        if item['fields']['key'] in projectiles:
                            evidence.append(dict(item,role='projectile'))
                db.execute('INSERT INTO binding_activation VALUES(?,?,?,?)',
                           (binding_id,'unresolved_weapon_activation','unresolved_rank_activation',compact(evidence)))
    print(f'Preserved all {binding_id} effect bindings', flush=True)

    # Source definitions are deduplicated; occurrences retain faction/character,
    # variant, node and rank distinctions. They are not simultaneous acquired buffs.
    source_ids = {}
    owner_id = source_id = occurrence_id = 0
    for kind, folder in [('skill','data/skill_trees/characters'),('technology','data/technology_trees/factions')]:
        for path in sorted((ROOT / folder).rglob('*.csv')):
            owner_id += 1
            owner_inserted = False
            for line, r in external_rows(path):
                if not owner_inserted:
                    db.execute('INSERT INTO owners VALUES(?,?,?,?,?,?,?)', (owner_id,kind,r.get('agent_subtype_key') or r.get('faction_key'),r.get('character_name') or r.get('faction_name'),r['race_slug'],r.get('faction_key',''),path.relative_to(ROOT).as_posix()))
                    owner_inserted = True
                effect = r.get('effect_key','')
                if not effect:
                    if r['record_type'] in ('initiative_effect','scripted_reward','trait_unlock','ancillary_unlock'):
                        detail = {k:r[k] for k in ('initiative_key','effect_list_key','technology_key','ancillary_key','character_trait_level_key','mechanic_id') if r.get(k)}
                        db.execute('INSERT INTO source_gaps VALUES(?,?,?,?,?)', (owner_id,line,r['record_type'],'retained_indirect_source_reference',compact(detail)))
                    continue
                ensure_effect(effect,r.get('effect_description',''))
                key = r.get('skill_key') or r.get('technology_key') or r.get('source_key','')
                conditions = {k:r[k] for k in ('classification','initiative_key','effect_list_key','level_campaign_key','level_faction_key','level_subculture_key') if r.get(k)}
                definition = (kind,key,r.get('skill_level',''),effect,r.get('effect_scope',''),r.get('effect_value',''),r['record_type'],compact(conditions))
                canonical = compact(definition)
                if canonical not in source_ids:
                    source_id += 1
                    source_ids[canonical] = source_id
                    db.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?,?,?)', (source_id,kind,key,r.get('skill_name') or r.get('technology_name',''),r.get('skill_level',''),effect,r.get('effect_scope',''),r.get('effect_value',''),r['record_type'],compact(conditions),canonical))
                sid = source_ids[canonical]
                occurrence_id += 1
                db.execute('INSERT INTO source_occurrences VALUES(?,?,?,?,?,?,?,?)', (occurrence_id,sid,owner_id,line,r.get('node_key',''),r.get('node_set_key',''),r.get('variant_key',''),r.get('campaign_key') or r.get('node_set_campaign_key','')))
    build_characters(db, lock)
    build_form_access(db, lock)
    reconciliation=reconcile_character_sources(db)
    (output/'character_source_reconciliation.json').write_text(json.dumps(reconciliation,indent=2)+'\n')
    print(f'Linked {source_id} source definitions / {occurrence_id} owner occurrences', flush=True)
    for dataset in ('unit_stats','skill_trees','technology_trees'):
        lock(ROOT / 'data' / dataset / 'dataset_manifest.json')
    if db.execute('PRAGMA foreign_key_check').fetchall():
        raise ValueError('Foreign-key failure')
    coverage = {
        **character_coverage(db),
        'queryable_forms_outside_normalized_roster': db.execute('SELECT COUNT(*) FROM supplemental_forms').fetchone()[0],
        'character_source_reconciliation': {k:v for k,v in reconciliation.items() if k!='owner_reports'},
        'source_tables': len(loaded), 'source_rows': raw_id, 'units': len(units),
        'effects': len(known_effects), 'bindings': binding_id,
        'source_definitions': source_id, 'source_occurrences': occurrence_id,
        'character_form_relations': db.execute('SELECT COUNT(*) FROM character_forms').fetchone()[0],
        'mount_acquisition_occurrences': db.execute('SELECT COUNT(*) FROM mount_acquisitions').fetchone()[0],
        'mount_rank_statuses': dict(db.execute('SELECT rank_status,COUNT(*) FROM mount_acquisitions GROUP BY rank_status')),
        'source_scope_classifications': dict(db.execute('SELECT scope_classification,COUNT(*) FROM classified_sources GROUP BY 1')),
        'occurrence_scope_classifications': dict(db.execute('SELECT scope_classification,COUNT(*) FROM classified_source_occurrences GROUP BY 1')),
        'binding_activation_statuses': dict(db.execute('SELECT status,COUNT(*) FROM binding_activation GROUP BY 1')),
        'binding_statuses': dict(db.execute('SELECT status,COUNT(*) FROM bindings GROUP BY status')),
        'target_set_statuses': dict(db.execute('SELECT status,COUNT(*) FROM set_coverage GROUP BY status')),
        'schema_reference_statuses': dict(db.execute('SELECT status,COUNT(*) FROM schema_links GROUP BY status')),
        'source_gap_types': dict(db.execute('SELECT record_type,COUNT(*) FROM source_gaps GROUP BY record_type')),
        'source_effect_coverage': dict(db.execute("SELECT CASE WHEN indexed_bindings>0 THEN 'unit_target_indexed' WHEN binding_count>0 THEN 'binding_retained_not_unit_resolved' ELSE 'no_binding_in_extraction' END,COUNT(*) FROM effect_coverage WHERE source_count>0 GROUP BY 1")),
        'missing_effect_definitions': db.execute("SELECT COUNT(*) FROM effects WHERE status='missing_definition'").fetchone()[0],
        'compound_selector_rows': db.execute('SELECT COUNT(*) FROM target_selectors WHERE predicate_count>1').fetchone()[0],
        'unit_targets': db.execute('SELECT COUNT(*) FROM unit_targets').fetchone()[0],
        'bindings_with_roster_candidates': db.execute('SELECT COUNT(DISTINCT binding_id) FROM unit_binding_candidates').fetchone()[0],
        'explicit_unit_targets_outside_roster': db.execute("SELECT COUNT(*) FROM binding_targets t WHERE t.kind='unit' AND NOT EXISTS(SELECT 1 FROM units u WHERE u.unit_key=t.target_key)").fetchone()[0],
        'unindexed_binding_target_types': dict(db.execute("SELECT target_table,COUNT(*) FROM bindings WHERE status!='unit_target_indexed' GROUP BY target_table ORDER BY COUNT(*) DESC,target_table")),
        'limitations': [
            'All indexed relationships are potential relevance, never proof of acquisition or active scope.',
            'Default ordinary-unit queries omit character-only source occurrences using recipient fields; evidence mode retains them. Unknown recipients remain unresolved.',
            'Personal identity exclusions require distinct explicit base anchors and corroborated mounted target paths. Conflicting or incomplete identity remains unresolved; custom-battle paths do not prove campaign acquisition.',
            'Character forms outside normalized coverage support modifier and owner-effect queries through retained raw targeting evidence, but cannot supply normalized base-stat queries.',
            'Weapon routes retain traced weapon/projectile evidence but unresolved activation and rank; missing rank predicates do not establish eligibility.',
            'Special-category predicates, selector combinations and exclusion precedence require engine verification; candidate rules are explicit.',
            'Ability/attribute reverse lookup covers existing base abilities/attributes; grants are indexed by their explicit recipient sets, not recursively propagated.',
            'Bindings without a supported unit target remain in shared queries and coverage; an empty unit result is not proof of no modifiers.',
            'No effect arithmetic is inferred from key spelling; rank-growth parameters and explicit operations remain raw evidence.',
            'Additional source families and prerequisite evaluation are not synthesized; skill and technology references preserve original owner records.',
        ],
    }
    # Compact per-unit directory: no duplicated effects or giant unit dossiers.
    counts = dict(db.execute('SELECT unit_key,COUNT(DISTINCT binding_id) FROM unit_binding_candidates GROUP BY unit_key'))
    with (output / 'unit_index.csv').open('w',encoding='utf-8',newline='') as f:
        writer = csv.writer(f,lineterminator='\n')
        writer.writerow(['unit_key','unit_name','candidate_binding_count','retrieval_status'])
        for key, unit in sorted(units.items()):
            writer.writerow([key,unit['unit_name'],counts.get(key,0),'potential_relationships_only'])
    coverage['units_with_candidates'] = len(counts)
    inventory = [dict(zip(('table_key','family','row_count'),r)) for r in db.execute('SELECT table_key,family,row_count FROM source_tables ORDER BY table_key')]
    (output / 'table_inventory.json').write_text(json.dumps(inventory,indent=2)+'\n')
    (output / 'coverage_report.json').write_text(json.dumps(coverage,indent=2)+'\n')
    schema_inventory = {}
    for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view') ORDER BY name").fetchall():
        schema_inventory[name] = [{'name':r[1],'type':r[2],'primary_key':bool(r[5])} for r in db.execute(f'PRAGMA table_info("{name}")')]
    (output / 'schema_inventory.json').write_text(json.dumps(schema_inventory,indent=2)+'\n')
    db.commit()
    db.execute('VACUUM')
    db.close()
    payload = dbpath.read_bytes()
    with (output / 'reference.sqlite.gz').open('wb') as f:
        with gzip.GzipFile(filename='',mode='wb',fileobj=f,mtime=0,compresslevel=9) as z:
            z.write(payload)
    dbpath.unlink()
    manifest = {
        'schema_version':5,'game':'warhammer_3','patch':'8.1.1','steam_build_id':'24237342',
        'source_manifest_sha256':digest((source/'source_manifest.json').read_bytes()),
        'source_exports':'source_exports','database_sha256':digest(payload),
        'sqlite_version':sqlite3.sqlite_version,'source_input_locks':inputs,
        'scope':'Full extraction preserved; per-unit candidate targeting plus existing skill/technology source links. No acquired-build simulation.',
        'artifacts':{p.name:{'sha256':digest(p.read_bytes()),'bytes':p.stat().st_size} for p in sorted(output.iterdir())},
    }
    (output / 'dataset_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps(coverage,indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source')
    parser.add_argument('output')
    args = parser.parse_args()
    from modifier_reference import verify_source
    verify_source()
    build(args.source,args.output)
