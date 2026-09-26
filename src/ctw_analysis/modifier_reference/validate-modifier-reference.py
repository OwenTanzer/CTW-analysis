"""Reconcile the generated reference against every retained source row and owner effect."""
import argparse
import json
import subprocess
from collections import Counter
from pathlib import Path
from character_reference import validate_characters, character_coverage
from modifier_reference import ROOT, digest, records, open_reference, compact, family, scope_classification


from reference_access import reconcile_character_sources, validate_form_access


def check(condition, message):
    if not condition:
        raise ValueError(message)


def validate(data, source):
    data,source = Path(data).resolve(),Path(source).resolve()
    manifest = json.loads((data/'dataset_manifest.json').read_text())
    subprocess.run(['node',str(ROOT/'scripts/validate-effect-source.mjs'),str(source)],check=True,stdout=subprocess.PIPE,text=True)
    check(digest((source/'source_manifest.json').read_bytes())==manifest['source_manifest_sha256'],'Source snapshot differs')
    for name, item in manifest['artifacts'].items():
        payload = (data/name).read_bytes()
        check(digest(payload)==item['sha256'] and len(payload)==item['bytes'],'Artifact integrity mismatch: '+name)
    for name, sha in manifest['source_input_locks'].items():
        check(digest((ROOT/name).read_bytes())==sha,'Existing dataset changed: '+name)
    db,_ = open_reference(data)
    check(db.execute('PRAGMA integrity_check').fetchone()[0]=='ok','SQLite integrity failure')
    check(not db.execute('PRAGMA foreign_key_check').fetchall(),'Broken generated foreign key')
    source_manifest=json.loads((source/'source_manifest.json').read_text())
    total=bindings=0
    expected_tables=set()
    for f in source_manifest['files']:
        if not f['path'].startswith('db/'):
            continue
        table=f['path'].split('/')[1];expected_tables.add(table)
        expected=list(records(source/f['path'],'\t'))
        actual=db.execute('SELECT source_row,payload_json FROM source_records WHERE table_key=? ORDER BY source_row',(table,)).fetchall()
        check(len(expected)==len(actual),'Missing/extra rows: '+table)
        check(all(line==a[0] and row==json.loads(a[1]) for (line,row),a in zip(expected,actual)),'Source values changed: '+table)
        total+=len(expected)
        if family(table)=='effect_bindings':
            bindings+=len(expected)
            for rid,effect,bonus,target_col,target_key in db.execute('SELECT b.record_id,b.effect_key,b.bonus_key,b.target_column,b.target_key FROM bindings b JOIN source_records r ON r.id=b.record_id WHERE r.table_key=?',(table,)):
                raw=json.loads(db.execute('SELECT payload_json FROM source_records WHERE id=?',(rid,)).fetchone()[0])
                check(effect==(raw.get('effect') or raw.get('effect_key')),'Incorrect effect binding')
                check(bonus==(raw.get('bonus_value_id') or raw.get('bonus_value') or ''),'Incorrect bonus binding')
    check(expected_tables=={r[0] for r in db.execute('SELECT table_key FROM source_tables')},'Table coverage mismatch')
    check(bindings==db.execute('SELECT COUNT(*) FROM bindings').fetchone()[0],'Binding rows dropped or duplicated')
    check(db.execute('SELECT COUNT(DISTINCT record_id) FROM bindings').fetchone()[0]==bindings,'Duplicate binding provenance')
    for r in db.execute('SELECT s.*,r.payload_json FROM scope_classifications s JOIN source_records r ON r.id=s.record_id'):
        raw=json.loads(r['payload_json'])
        check(r['scope_key']==raw['key'] and r['recipient']==raw['target'] and r['classification']==scope_classification(raw),'Scope classification lacks recipient evidence')
    check(db.execute('SELECT COUNT(*) FROM classified_source_occurrences').fetchone()[0]==db.execute('SELECT COUNT(*) FROM source_occurrences').fetchone()[0],'Scope occurrence coverage differs')
    evidence_files={}
    for r in db.execute('SELECT a.*,b.target_table FROM binding_activation a JOIN bindings b ON b.id=a.binding_id'):
        check(r['target_table']=='unit_missile_weapon_junctions_tables' and r['status']=='unresolved_weapon_activation' and r['rank_status']=='unresolved_rank_activation','Unsupported activation claim')
        for item in json.loads(r['evidence_json']):
            if 'path' in item:
                if item['path'] not in evidence_files:
                    evidence_files[item['path']]=dict(records(ROOT/item['path'],'\t'))
                check(evidence_files[item['path']].get(item['row'])==item['fields'],'Weapon evidence changed')
            else:
                check(db.execute('SELECT 1 FROM source_records WHERE id=?',(item['record_id'],)).fetchone() is not None,'Missing activation record')
    check(db.execute("SELECT COUNT(*) FROM bindings WHERE target_table='unit_missile_weapon_junctions_tables'").fetchone()[0]==db.execute('SELECT COUNT(*) FROM binding_activation').fetchone()[0],'Unclassified weapon route')
    validate_form_access(db)
    reconciliation=reconcile_character_sources(db)
    check(reconciliation==json.loads((data/'character_source_reconciliation.json').read_text()),'Character source reconciliation report differs')
    missing={r[0] for r in db.execute('SELECT DISTINCT cf.unit_key FROM character_forms cf LEFT JOIN units u USING(unit_key) WHERE u.unit_key IS NULL')}
    check(missing=={r[0] for r in db.execute('SELECT unit_key FROM supplemental_forms')},'Supplemental form coverage differs')
    mount_count=validate_characters(db,check)
    all_units=set()
    for p in sorted((ROOT/'data/unit_stats/normalized').glob('*.csv')):
        for line,r in records(p):
            all_units.add(r['unit_key'])
            check(db.execute('SELECT 1 FROM unit_records WHERE unit_key=? AND source_path=? AND source_row=?',(r['unit_key'],p.relative_to(ROOT).as_posix(),line)).fetchone() is not None,'Unit record missing')
    check(all_units=={r[0] for r in db.execute('SELECT unit_key FROM units')},'Roster unit coverage mismatch')
    # Every external occurrence must point back to the exact owner row/value and
    # preserve variant/node/rank distinctions, independently of source deduplication.
    occurrence_total=0
    for owner in db.execute('SELECT id,kind,source_path FROM owners ORDER BY id').fetchall():
        expected={line:r for line,r in records(ROOT/owner['source_path']) if r.get('effect_key')}
        actual=db.execute('SELECT so.*,s.kind,s.source_key,s.skill_level,s.effect_key,s.scope_key,s.value,s.record_type,s.conditions_json FROM source_occurrences so JOIN sources s ON s.id=so.source_id WHERE so.owner_id=?',(owner['id'],)).fetchall()
        check(len(expected)==len(actual),'Owner effect occurrences differ: '+owner['source_path'])
        check(len({a['source_row'] for a in actual})==len(actual),'Owner row duplicated')
        for a in actual:
            r=expected[a['source_row']]
            for key,value in [('effect_key',r['effect_key']),('scope_key',r.get('effect_scope','')),('value',r.get('effect_value','')),('skill_level',r.get('skill_level','')),('record_type',r['record_type']),('node_key',r.get('node_key','')),('node_set_key',r.get('node_set_key','')),('variant_key',r.get('variant_key',''))]:
                check(a[key]==value,'Source context mismatch: '+owner['source_path']+':'+str(a['source_row'])+':'+key)
            conditions={k:r[k] for k in ('classification','initiative_key','effect_list_key','level_campaign_key','level_faction_key','level_subculture_key') if r.get(k)}
            check(json.loads(a['conditions_json'])==conditions,'Conditional source flattened')
        occurrence_total+=len(actual)
    # Known exclusions must not leak into the compiled set candidates.
    exclusions=db.execute("SELECT COUNT(*) FROM selector_matches m JOIN target_selectors s ON s.record_id=m.record_id JOIN unit_targets u ON u.kind='unit_set' AND u.target_key=s.set_key AND u.unit_key=m.unit_key WHERE s.exclude='true' AND m.status='match'").fetchone()[0]
    check(exclusions==0,'Explicit exclusions leaked into candidate index')
    check(db.execute("SELECT COUNT(*) FROM unit_targets u LEFT JOIN target_sets s ON u.kind='unit_set' AND u.target_key=s.set_key WHERE u.kind='unit_set' AND s.set_key IS NULL").fetchone()[0]==0,'Unknown target set indexed')
    indexed={r['unit_key']:int(r['candidate_binding_count']) for _,r in records(data/'unit_index.csv')}
    counts=dict(db.execute('SELECT unit_key,COUNT(DISTINCT binding_id) FROM unit_binding_candidates GROUP BY unit_key'))
    check(set(indexed)==all_units and all(indexed[k]==counts.get(k,0) for k in all_units),'Compact per-unit index mismatch')
    coverage=json.loads((data/'coverage_report.json').read_text())
    for key,value in [('source_tables',len(expected_tables)),('source_rows',total),('units',len(all_units)),('bindings',bindings),('source_occurrences',occurrence_total),('mount_acquisition_occurrences',mount_count)]:
        check(coverage[key]==value,'Coverage report mismatch: '+key)
    for key,value in character_coverage(db).items():
        check(coverage[key]==value,'Character coverage report mismatch: '+key)
    db.close()
    return dict(status='passed',errors=[],dataset_manifest_sha256=digest((data/'dataset_manifest.json').read_bytes()),source_tables=len(expected_tables),source_rows=total,units=len(all_units),bindings=bindings,source_occurrences=occurrence_total)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('data',type=Path)
    p.add_argument('source',type=Path,nargs='?')
    a=p.parse_args()
    from modifier_reference import verify_source
    verify_source()
    report = validate(a.data,a.source or ROOT/'data/effect_semantics/source_exports')
    serialized = json.dumps(report,indent=2)+'\n'
    (a.data/'validation_report.json').write_bytes(serialized.encode('utf-8'))
    print(serialized,end='')
