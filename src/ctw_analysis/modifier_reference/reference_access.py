"""Supplemental form access and independent character-source reconciliation.

Normalized roster ownership is unchanged. Raw identity/predicate evidence permits
modifier navigation without manufacturing a normalized stat card.
"""
import json
import sqlite3
from collections import Counter
from modifier_reference import ROOT, records, compact, selector_match, membership_status


def build_form_access(db, lock):
    db.commit()
    db.row_factory=sqlite3.Row
    db.executescript('''
    CREATE TABLE supplemental_forms(unit_key TEXT PRIMARY KEY,unit_name TEXT,source_unit_class TEXT,source_category TEXT,source_caste TEXT,evidence_json TEXT);
    CREATE TABLE supplemental_targets(kind TEXT,target_key TEXT,unit_key TEXT REFERENCES supplemental_forms,status TEXT,evidence_json TEXT,PRIMARY KEY(kind,target_key,unit_key));
    CREATE INDEX supplemental_targets_unit ON supplemental_targets(unit_key);
    CREATE VIEW query_units AS SELECT *, 'available' normalized_base_stat_coverage FROM units
      UNION ALL SELECT unit_key,unit_name,source_unit_class,source_category,source_caste,'unavailable' FROM supplemental_forms;
    CREATE VIEW query_binding_candidates AS SELECT * FROM unit_binding_candidates UNION ALL
      SELECT u.unit_key,b.binding_id,u.kind,u.target_key,u.status FROM supplemental_targets u JOIN binding_targets b USING(kind,target_key);
    CREATE VIEW owner_effect_access AS
      SELECT so.*,o.owner_key,o.kind owner_kind,o.source_path,s.effect_key,s.source_key,s.skill_level,s.value,s.scope_key,s.record_type,s.conditions_json,
      CASE WHEN EXISTS(SELECT 1 FROM bindings b WHERE b.effect_key=s.effect_key AND b.status='unit_target_indexed') THEN 'typed_unit_target_available'
           WHEN EXISTS(SELECT 1 FROM bindings b WHERE b.effect_key=s.effect_key) THEN 'binding_retained_target_unresolved'
           ELSE 'no_binding_in_extraction' END target_access_status
      FROM source_occurrences so JOIN owners o ON o.id=so.owner_id JOIN sources s ON s.id=so.source_id;
    ''')
    db.commit()
    def raw(table):
        return [dict(path=p.relative_to(ROOT).as_posix(),row=n,fields=r)
                for p in sorted((ROOT/'data/unit_stats/source_exports/db'/table).glob('*.tsv'))
                for n,r in records(lock(p),'\t')]
    main={e['fields']['unit']:e for e in raw('main_units_tables')}
    land={e['fields']['key']:e for e in raw('land_units_tables')}
    abilities=raw('land_units_to_unit_abilites_junctions_tables')
    attributes=raw('unit_attributes_to_groups_junctions_tables')
    sets=[dict(r) for r in db.execute('SELECT * FROM target_sets')]
    selectors={s['set_key']:[dict(r) for r in db.execute('SELECT * FROM target_selectors WHERE set_key=?',(s['set_key'],))] for s in sets}
    keys=[r[0] for r in db.execute('SELECT DISTINCT cf.unit_key FROM character_forms cf LEFT JOIN units u USING(unit_key) WHERE u.unit_key IS NULL ORDER BY cf.unit_key')]
    for key in keys:
        m=main.get(key);l=land.get(m['fields']['land_unit']) if m else None
        unit=dict(unit_key=key,unit_name='',source_unit_class=l['fields']['class'] or None if l else None,
                  source_category=l['fields']['category'] or None if l else None,source_caste=m['fields']['caste'] or None if m else None)
        db.execute('INSERT INTO supplemental_forms VALUES(?,?,?,?,?,?)',(*unit.values(),compact([e for e in (m,l) if e])))
        db.execute('INSERT INTO supplemental_targets VALUES(?,?,?,?,?)',('unit',key,key,'explicit_unit','[]'))
        for s in sets:
            inc=[];exc=[];evidence=[]
            for r in selectors[s['set_key']]:
                status=selector_match(r,unit)
                if status!='no_match':
                    (exc if r['exclude']=='true' else inc).append(status)
                    evidence.append(dict(record_id=r['record_id'],status=status,exclude=r['exclude']))
            status=membership_status(inc,exc,s['special_category'])
            if status not in ('no_match','excluded'):
                db.execute('INSERT INTO supplemental_targets VALUES(?,?,?,?,?)',('unit_set',s['set_key'],key,status,compact(evidence)))
        if l:
            for kind,items,column,value,target in [('ability',abilities,'land_unit',l['fields']['key'],'ability'),('attribute',attributes,'attribute_group',l['fields']['attribute_group'],'attribute')]:
                for e in items:
                    if value and e['fields'][column]==value:
                        db.execute('INSERT OR IGNORE INTO supplemental_targets VALUES(?,?,?,?,?)',(kind,e['fields'][target],key,'retained_base_'+kind,compact([e])))


def reconcile_character_sources(db):
    """Start from files, not indexed owners; detect missing owners/occurrences.

    Comparison preserves physical row, effect, value, scope, source key, rank and
    node context. Classification is a navigation boundary, never applicability.
    """
    actual={}
    for row in db.execute('SELECT a.* FROM owner_effect_access a JOIN owners o ON o.id=a.owner_id WHERE o.kind=\'skill\''):
        k=(row['source_path'],row['source_row'])
        if k in actual:
            raise ValueError('Duplicate character source occurrence: '+str(k))
        actual[k]=dict(row)
    owner_reports=[];seen=set();counts=Counter()
    for p in sorted((ROOT/'data/skill_trees/characters').rglob('*.csv')):
        path=p.relative_to(ROOT).as_posix();statuses=Counter();owner_key=None
        owner=db.execute("SELECT id,owner_key FROM owners WHERE kind='skill' AND source_path=?",(path,)).fetchone()
        if owner is None:
            raise ValueError('Missing character source owner: '+path)
        for n,r in records(p):
            owner_key=r['agent_subtype_key']
            if owner['owner_key']!=owner_key:
                raise ValueError('Character source owner mismatch: '+path)
            if not r.get('effect_key'):
                continue
            k=(path,n);a=actual.get(k)
            if a is None:
                raise ValueError('Missing character source occurrence: '+path+':'+str(n))
            for field,expected in [('effect_key',r['effect_key']),('source_key',r.get('skill_key') or r.get('source_key','')),('skill_level',r.get('skill_level','')),('scope_key',r.get('effect_scope','')),('value',r.get('effect_value','')),('record_type',r['record_type']),('node_key',r.get('node_key','')),('node_set_key',r.get('node_set_key','')),('campaign_key',r.get('campaign_key') or r.get('node_set_campaign_key',''))]:
                if a[field]!=expected:
                    raise ValueError('Character source field differs: '+path+':'+str(n)+':'+field)
            conditions={k:r[k] for k in ('classification','initiative_key','effect_list_key','level_campaign_key','level_faction_key','level_subculture_key') if r.get(k)}
            if json.loads(a['conditions_json'])!=conditions:
                raise ValueError('Character source conditions differ: '+path+':'+str(n))
            seen.add(k);statuses[a['target_access_status']]+=1
        counts.update(statuses)
        owner_reports.append(dict(owner_key=owner_key,source_path=path,effect_occurrences=sum(statuses.values()),target_access_statuses=dict(sorted(statuses.items())),query='owner '+str(owner_key)+' --source-kind skill'))
    if seen!=set(actual):
        raise ValueError('Extra character source occurrences')
    return dict(status='passed',scope='Every effect-bearing row in every authoritative character CSV, including unbound effects; source ownership does not establish target applicability.',owners=len(owner_reports),effect_occurrences=len(seen),target_access_statuses=dict(sorted(counts.items())),owner_reports=owner_reports)


def validate_form_access(db):
    """Recompute supplemental predicates/routes from retained raw source rows.

    This oracle does not consult generated selectors, supplemental predicates or
    compiler membership helpers. It detects deleted/altered target rows as well
    as dropped binding routes.
    """
    def raw(table):
        return [dict(path=p.relative_to(ROOT).as_posix(),row=n,fields=r)
                for p in sorted((ROOT/'data/unit_stats/source_exports/db'/table).glob('*.tsv')) for n,r in records(p,'\t')]
    main={r['fields']['unit']:r for r in raw('main_units_tables')}
    land={r['fields']['key']:r for r in raw('land_units_tables')}
    abilities=raw('land_units_to_unit_abilites_junctions_tables')
    attributes=raw('unit_attributes_to_groups_junctions_tables')
    raw_sets={json.loads(r[0])['key']:json.loads(r[0]) for r in db.execute("SELECT payload_json FROM source_records WHERE table_key='unit_sets_tables'")}
    raw_selectors={k:[] for k in raw_sets}
    for (payload,) in db.execute("SELECT payload_json FROM source_records WHERE table_key='unit_set_to_unit_junctions_tables'"):
        r=json.loads(payload);raw_selectors[r['unit_set']].append(r)
    expected_keys={r[0] for r in db.execute('SELECT DISTINCT cf.unit_key FROM character_forms cf LEFT JOIN units u USING(unit_key) WHERE u.unit_key IS NULL')}
    rows={r['unit_key']:dict(r) for r in db.execute('SELECT * FROM supplemental_forms')}
    if expected_keys!=set(rows):
        raise ValueError('Supplemental form coverage differs')
    dimensions={'unit_record':'unit_key','unit_class':'source_unit_class','unit_category':'source_category','unit_caste':'source_caste'}
    for key,a in rows.items():
        m=main.get(key);l=land.get(m['fields']['land_unit']) if m else None
        unit={'unit_key':key,'source_unit_class':l['fields']['class'] or None if l else None,'source_category':l['fields']['category'] or None if l else None,'source_caste':m['fields']['caste'] or None if m else None}
        if any(a[k]!=v for k,v in unit.items()) or json.loads(a['evidence_json'])!=[e for e in (m,l) if e]:
            raise ValueError('Supplemental predicate evidence differs: '+key)
        expected={('unit',key):'explicit_unit'}
        for sk,s in raw_sets.items():
            positive=[];negative=[]
            for r in raw_selectors[sk]:
                comparisons=[None if unit[d] in ('',None) else unit[d]==r[c] for c,d in dimensions.items() if r[c]]
                status='unresolved' if not comparisons or None in comparisons else 'match' if all(comparisons) else 'possible' if any(comparisons) else 'no_match'
                (negative if r['exclude']=='true' else positive).append(status)
            if 'match' in negative:
                continue
            if 'match' in positive:
                expected[('unit_set',sk)]='conditional_selector' if s['special_category'] or any(v in ('possible','unresolved') for v in negative) else 'selector_match'
            elif any(v in ('possible','unresolved') for v in positive):
                expected[('unit_set',sk)]='unresolved_selector'
        if l:
            for e in abilities:
                if e['fields']['land_unit']==l['fields']['key']:
                    expected[('ability',e['fields']['ability'])]='retained_base_ability'
            for e in attributes:
                if l['fields']['attribute_group'] and e['fields']['attribute_group']==l['fields']['attribute_group']:
                    expected[('attribute',e['fields']['attribute'])]='retained_base_attribute'
        actual={(r[0],r[1]):r[2] for r in db.execute('SELECT kind,target_key,status FROM supplemental_targets WHERE unit_key=?',(key,))}
        if actual!=expected:
            raise ValueError('Supplemental target reconciliation differs: '+key)
    direct={'unit_sets_tables':'unit_set','main_units_tables':'unit','unit_abilities_tables':'ability','unit_attributes_tables':'attribute'}
    intermediate={'unit_set_unit_ability_junctions_tables':('unit_set','unit_set'), 'unit_set_unit_attribute_junctions_tables':('unit_set','unit_set'), 'unit_set_special_ability_phase_junctions_tables':('unit_set','unit_set'), 'unit_missile_weapon_junctions_tables':('unit','unit')}
    retained={t:[(r[0],json.loads(r[1])) for r in db.execute('SELECT id,payload_json FROM source_records WHERE table_key=?',(t,))] for t in intermediate}
    expected_routes=set()
    for b in db.execute('SELECT * FROM bindings'):
        t=b['target_table']
        if t in direct:
            expected_routes.add((b['id'],direct[t],b['target_key'],b['record_id']))
        elif t in intermediate:
            matched=[(rid,r) for rid,r in retained[t] if r[b['target_column']]==b['target_key']]
            if len(matched)==1:
                rid,r=matched[0];kind,column=intermediate[t]
                expected_routes.add((b['id'],kind,r[column],rid))
    actual_routes={tuple(r) for r in db.execute('SELECT binding_id,kind,target_key,evidence_record FROM binding_targets')}
    if expected_routes!=actual_routes:
        raise ValueError('Typed binding target coverage differs')
    return dict(status='passed',forms=len(rows),target_rows=db.execute('SELECT COUNT(*) FROM supplemental_targets').fetchone()[0])
