"""Progressive retrieval: base unit -> candidate index -> shared evidence."""
import argparse
import json
from pathlib import Path
from character_reference import personal_allowed_sql, identity_status, identity_context
from modifier_reference import ROOT, DEFAULT_DATA, open_reference, records


def dicts(cursor):
    return [dict(r) for r in cursor.fetchall()]


def page(db, sql, params, limit, offset):
    total = db.execute('SELECT COUNT(*) FROM (' + sql + ')', params).fetchone()[0]
    entries = dicts(db.execute(sql + ' LIMIT ? OFFSET ?', [*params,limit,offset]))
    return {'total':total,'offset':offset,'limit':limit,'next_offset':offset+limit if offset+limit<total else None,'entries':entries}


def identity_evidence(context):
    def reference(e):
        if 'path' not in e:
            return {k:reference(v) for k,v in e.items()}
        keys=('key','agent_subtype_key','ancillary_key','associated_unit_override',
              'provided_bodyguard_unit','base_unit','mounted_unit')
        return dict(path=e['path'],row=e['row'],fields={k:e['fields'][k] for k in keys if e['fields'].get(k)})
    result={k:context[k] for k in ('status','base_units','grant_base_units','custom_battle_base_units')}
    result['form_paths']=[dict(owner_key=r['owner_key'],relation=r['relation'],evidence=reference(json.loads(r['evidence_json'])),base_anchor_evidence=[reference(json.loads(a['evidence_json'])) for a in context['anchors'].get(r['owner_id'],[])]) for r in context['forms']]
    result['custom_battle_paths']=[reference(json.loads(r['evidence_json'])) for r in context['custom_battle_paths']]
    grants=set(context['grant_base_units']);custom=set(context['custom_battle_base_units'])
    result['path_comparison']={'classification':'equal' if grants==custom else 'overlapping_nonidentical' if grants&custom else 'disjoint' if grants and custom else 'one_path_missing', 'shared_base_units':sorted(grants&custom),'grant_only_base_units':sorted(grants-custom),'custom_battle_only_base_units':sorted(custom-grants)}
    result['custom_battle_meaning']='identity corroboration/conflict only; not campaign acquisition'
    return result


def base_unit(key):
    found = []
    for p in sorted((ROOT / 'data/unit_stats/normalized').glob('*.csv')):
        for line, row in records(p):
            if row['unit_key'] == key:
                found.append((p,line,row))
    if not found:
        raise ValueError('Unknown roster unit key: '+key)
    path,line,r = found[0]
    fields = ['unit_key','unit_name','tactical_category','tier','entity_count','total_hp','armour',
              'melee_attack','melee_defence','leadership','speed','weapon_base_damage','weapon_ap_damage',
              'charge_bonus','ammunition','range','reload_time','missile_base_damage','missile_ap_damage',
              'campaign_recruit_cost','campaign_upkeep','multiplayer_cost','data_quality_status']
    return {'base_stats':{k:r[k] for k in fields},'context':'unmodified rank-0 base; original unit dataset remains authoritative',
            'base_record_references':[{'path':p.relative_to(ROOT).as_posix(),'row':n} for p,n,_ in found],
            'modifier_query':f'python3 src/ctw_analysis/modifier_reference/query-modifier-reference.py unit {key} --modifiers'}


def query(args):
    if args.command == 'character' and (getattr(args,'rank',None) is not None or any(getattr(args,k,None) for k in ('bonus','modifiers','evidence','owner','source_kind'))):
        raise ValueError('Character queries take an owner key and pagination only; --rank filters unit experience, not mount unlocks')
    if args.command == 'owner' and any(getattr(args,k,None) for k in ('rank','bonus','modifiers','evidence','owner')):
        raise ValueError('Owner queries take an owner key, optional --source-kind, and pagination only')
    if args.command == 'unit' and not args.modifiers:
        return base_unit(args.key)  # No modifier database is opened for base queries.
    db, manifest = open_reference(args.data)
    envelope = {'patch':manifest['patch'],'interpretation':'potential relevance only; acquisition, active scope and stacking are not evaluated'}
    if args.command == 'unit':
        unit = db.execute('SELECT * FROM query_units WHERE unit_key=?',(args.key,)).fetchone()
        if unit is None:
            raise ValueError('Unknown roster unit key: '+args.key)
        where,params = ['c.unit_key=?'],[args.key]
        if args.bonus:
            where.append('b.bonus_key=?');params.append(args.bonus)
        if args.rank is not None:
            where.append("(c.kind!='unit_set' OR ts.rank_enabled!='true' OR (? BETWEEN CAST(ts.min_rank AS INTEGER) AND CAST(ts.max_rank AS INTEGER)))")
            params.append(args.rank)
        sw,sp = [],[]
        evidence = getattr(args,'evidence',False)
        if args.source_kind:
            sw.append('s.kind=?');sp.append(args.source_kind)
        if args.owner:
            sw.append('o.owner_key=?');sp.append(args.owner)
        identity_context_data=identity_context(db,args.key)
        identity_sql,identity_params=personal_allowed_sql(db,unit,identity_context_data)
        occurrence_filter=' AND '.join(sw) if sw else '1'
        allowed_occurrence=occurrence_filter + (" AND (s.scope_classification!='character_only' OR "+identity_sql+")" if not evidence else '')
        allowed_params=sp + (identity_params if not evidence else [])
        source_allowed='EXISTS(SELECT 1 FROM source_occurrences so JOIN owners o ON o.id=so.owner_id WHERE so.source_id=s.id AND '+allowed_occurrence+')'
        if sw or not evidence:
            candidate_allowed='EXISTS(SELECT 1 FROM classified_sources s WHERE s.effect_key=b.effect_key AND '+source_allowed+')'
            # Binding-only evidence must survive without an owner filter.
            if not sw:
                candidate_allowed="(NOT EXISTS(SELECT 1 FROM sources s WHERE s.effect_key=b.effect_key) OR "+candidate_allowed+")"
            where.append(candidate_allowed);params.extend(allowed_params)
        common = ' FROM query_binding_candidates c JOIN bindings b ON b.id=c.binding_id JOIN effects e ON e.effect_key=b.effect_key LEFT JOIN target_sets ts ON c.kind=\'unit_set\' AND ts.set_key=c.target_key WHERE '+' AND '.join(where)
        sql = 'SELECT DISTINCT b.id binding_id,b.effect_key,e.label,b.bonus_key,b.route,b.record_id'+common+' ORDER BY b.effect_key,b.bonus_key,b.id'
        result = page(db,sql,params,args.limit,args.offset)
        for entry in result['entries']:
            entry['candidate_paths'] = dicts(db.execute('SELECT c.kind,c.target_key,c.status,ts.rank_enabled,ts.min_rank,ts.max_rank,ts.special_category FROM query_binding_candidates c LEFT JOIN target_sets ts ON c.kind=\'unit_set\' AND ts.set_key=c.target_key WHERE c.unit_key=? AND c.binding_id=? ORDER BY c.kind,c.target_key',(args.key,entry['binding_id'])))
            source_filter = 's.effect_key=? AND '+source_allowed
            source_params = [entry['effect_key'],*allowed_params]
            # Count omitted definitions only if NONE of their selected owner
            # occurrences passes; shared generic skills can have several owners.
            entry['omitted_character_only_source_count'] = db.execute("SELECT COUNT(*) FROM classified_sources s WHERE s.effect_key=? AND s.scope_classification='character_only' AND EXISTS(SELECT 1 FROM source_occurrences so JOIN owners o ON o.id=so.owner_id WHERE so.source_id=s.id AND "+occurrence_filter+") AND NOT ("+source_allowed+")",[entry['effect_key'],*sp,*allowed_params]).fetchone()[0] if not evidence else 0
            personal_owners=db.execute("SELECT DISTINCT o.id,s.personal_identity_policy FROM classified_sources s JOIN source_occurrences so ON so.source_id=s.id JOIN owners o ON o.id=so.owner_id WHERE s.effect_key=? AND s.scope_classification='character_only' AND "+allowed_occurrence,[entry['effect_key'],*allowed_params]).fetchall()
            entry['personal_source_identity_statuses']=sorted({identity_status(db,o['id'],unit,identity_context_data) if o['personal_identity_policy']=='same_source_character' else 'character_recipient_context_unresolved' for o in personal_owners})
            entry['matching_source_counts'] = dict(db.execute('SELECT s.kind,COUNT(*) FROM classified_sources s WHERE '+source_filter+' GROUP BY s.kind',source_params))
            entry['scope_keys_to_check'] = [r[0] for r in db.execute('SELECT DISTINCT s.scope_key FROM classified_sources s WHERE '+source_filter+' ORDER BY s.scope_key',source_params)]
            entry['source_scope_classifications'] = dict(db.execute('SELECT s.scope_classification,COUNT(*) FROM classified_sources s WHERE '+source_filter+' GROUP BY s.scope_classification',source_params)) or {'unresolved_source':0}
            activation = db.execute('SELECT status,rank_status,evidence_json FROM binding_activation WHERE binding_id=?',(entry['binding_id'],)).fetchone()
            entry['activation_status'] = activation['status'] if activation else 'not_evaluated'
            for candidate_path in entry['candidate_paths']:
                candidate_path['rank_match'] = None if args.rank is None or candidate_path['rank_enabled'] != 'true' else int(candidate_path['min_rank']) <= args.rank <= int(candidate_path['max_rank'])
                candidate_path['rank_status'] = activation['rank_status'] if activation else 'rank_not_queried' if args.rank is None else 'rank_predicate_match' if candidate_path['rank_match'] is True else 'rank_predicate_mismatch' if candidate_path['rank_match'] is False else 'no_rank_predicate_in_target_path'
                candidate_path['eligibility'] = 'unresolved_activation' if activation else 'candidate_only'
            entry['details_query'] = 'effect '+entry['effect_key']
            if args.owner:
                entry['details_query'] += ' --owner '+args.owner
            if args.source_kind:
                entry['details_query'] += ' --source-kind '+args.source_kind
        envelope.update(unit=dict(unit),filters={'bonus':args.bonus,'unit_rank':args.rank,'source_kind':args.source_kind,'source_owner':args.owner},
                        view='evidence' if evidence else 'ordinary_unit_candidates',
                        scope_policy='known personal-source identity mismatches omitted; unresolved identities/scopes explicitly retained; --evidence retains mismatches' if not evidence else 'all source scopes and identity mismatches retained as evidence',
                        character_identity_resolution=identity_evidence(identity_context_data),
                        character_identities=dicts(db.execute('SELECT DISTINCT o.owner_key,cf.relation FROM character_forms cf JOIN owners o ON o.id=cf.owner_id WHERE cf.unit_key=? ORDER BY o.owner_key,cf.relation',(args.key,))),
                        mount_records=page(db,"SELECT mr.ancillary_key,CASE WHEN EXISTS(SELECT 1 FROM mount_acquisitions ma WHERE ma.ancillary_key=mr.ancillary_key) THEN 'skill_grant_route_present' ELSE 'unconfirmed_acquisition' END acquisition_status,mr.evidence_json FROM mount_records mr WHERE mr.unit_key=? ORDER BY mr.ancillary_key",[args.key],args.limit,args.offset),
                        character_query='character <owner_key> follows supported forms and independent mount acquisition evidence',
                        filter_meaning='owner selects source ownership, not proof that this owner can recruit or buff this unit',
                        bonus_groups=page(db,'SELECT b.bonus_key,COUNT(DISTINCT b.id) candidate_bindings'+common+' GROUP BY b.bonus_key ORDER BY candidate_bindings DESC,b.bonus_key',params,min(args.limit,10),args.offset),
                        candidates=result,
                        source_owner_retrieval=('owner '+args.owner+(' --source-kind '+args.source_kind if args.source_kind else '')) if args.owner else 'owner <owner_key> includes unbound and unresolved source effects',
                        target_predicate_evidence=[dict(path=e['path'],row=e['row'],fields={k:v for k,v in e['fields'].items() if k in ('unit','land_unit','key','class','category','caste','attribute_group')}) for row in db.execute('SELECT evidence_json FROM supplemental_forms WHERE unit_key=?',(args.key,)) for e in json.loads(row[0])],
                        supplemental_base_relation_coverage='exact retained land-unit abilities and attribute-group relations; no normalized stat card' if unit['normalized_base_stat_coverage']=='unavailable' else 'normalized roster lookups',
                        shared_bindings_not_indexed_per_unit=db.execute("SELECT COUNT(*) FROM bindings WHERE status!='unit_target_indexed'").fetchone()[0],
                        gaps_query='gaps --kind bindings')
        if result['total']==0:
            envelope['empty_result']={
                'status':'no_indexed_candidates_under_selected_filters',
                'message':'No indexed modifier candidates under the selected filters. This does not establish the absence of army-wide or other campaign effects.',
            }
            if args.owner and db.execute('SELECT 1 FROM owners WHERE owner_key=?',(args.owner,)).fetchone():
                owner_where='owner_key=?'+(' AND owner_kind=?' if args.source_kind else '')
                owner_params=[args.owner]+([args.source_kind] if args.source_kind else [])
                envelope['empty_result']['owner_source_evidence']={
                    'query':envelope['source_owner_retrieval'],
                    'effect_occurrences':db.execute('SELECT COUNT(*) FROM owner_effect_access WHERE '+owner_where,owner_params).fetchone()[0],
                    'meaning':'All effect-bearing rows for the selected owner and source kind, including unbound and unresolved effects. Unit, bonus and rank filters do not apply to this count; applicability to this unit is not established.',
                }
    elif args.command == 'character':
        owners=dicts(db.execute("SELECT * FROM owners WHERE kind='skill' AND owner_key=? ORDER BY id",(args.key,)))
        if not owners:
            raise ValueError('Unknown character owner: '+args.key)
        forms=page(db,'SELECT cf.unit_key,u.unit_name,u.unit_key normalized_unit_key,cf.relation,cf.evidence_json FROM character_forms cf JOIN owners o ON o.id=cf.owner_id LEFT JOIN units u ON u.unit_key=cf.unit_key WHERE o.owner_key=? ORDER BY cf.unit_key,cf.relation',[args.key],args.limit,args.offset)
        for form in forms['entries']:
            form['evidence']=json.loads(form.pop('evidence_json'))
            form['normalized_base_stat_coverage']='available' if form.pop('normalized_unit_key') is not None else 'unavailable'
            if form['normalized_base_stat_coverage']=='available':
                form['base_query']='unit '+form['unit_key']
            form['modifier_query']='unit '+form['unit_key']+' --modifiers --owner '+args.key
            form['owner_effects_query']='owner '+args.key+' --source-kind skill'
            form['identity_resolution']=identity_evidence(identity_context(db,form['unit_key']))
        acquisitions=page(db,'SELECT m.*,o.source_path FROM mount_acquisitions m JOIN owners o ON o.id=m.owner_id WHERE o.owner_key=? ORDER BY m.unit_key,m.node_set_key,m.node_key,m.id',[args.key],args.limit,args.offset)
        for mount in acquisitions['entries']:
            mount['level_evidence']=json.loads(mount.pop('level_evidence_json'))
            mount['related_effect_keys']=json.loads(mount.pop('effect_keys_json'))
            mount['effective_unlock_rank']=None
            mount['availability']='skill_grant_route_present; acquisition/prerequisites not evaluated'
        envelope.update(character_owners=owners,forms=forms,mount_acquisitions=acquisitions,
                        form_policy='Subtype body overrides and owner skill-tree mount grants establish form candidates; custom-battle paths corroborate or conflict with identity only, never establish campaign acquisition. Missing/conflicting identity is retained. Every form has a modifier query plus owner-level source retrieval. Base queries exist only for normalized roster coverage; evidence fields retain usable source path/row references.',
                        rank_policy='node_rank and level_unlocked_at_rank retain distinct source fields; even agreement does not establish effective unlock rank. --rank is unit experience, not character rank.')
    elif args.command == 'owner':
        owners=dicts(db.execute('SELECT * FROM owners WHERE owner_key=? ORDER BY id',(args.key,)))
        if not owners:
            raise ValueError('Unknown source owner: '+args.key)
        selected_kind=getattr(args,'source_kind',None)
        owner_where='owner_key=?'+(' AND owner_kind=?' if selected_kind else '')
        owner_params=[args.key]+([selected_kind] if selected_kind else [])
        owners=[o for o in owners if not selected_kind or o['kind']==selected_kind]
        result=page(db,'SELECT * FROM owner_effect_access WHERE '+owner_where+' ORDER BY source_path,source_row',owner_params,args.limit,args.offset)
        for entry in result['entries']:
            entry['conditions']=json.loads(entry.pop('conditions_json'))
            entry['effect_query']='effect '+entry['effect_key']+' --owner '+args.key+' --source-kind '+entry['owner_kind']
            entry['source_query']='source '+str(entry['source_id'])+' --owner '+args.key
        envelope.update(owners=owners,effect_occurrences=result,
                        target_access_summary=dict(db.execute('SELECT target_access_status,COUNT(*) FROM owner_effect_access WHERE '+owner_where+' GROUP BY 1',owner_params)),
                        indirect_references=page(db,'SELECT o.source_path,g.* FROM source_gaps g JOIN owners o ON o.id=g.owner_id WHERE o.owner_key=?'+(' AND o.kind=?' if selected_kind else '')+' ORDER BY source_path,source_row',owner_params,args.limit,args.offset),
                        character_query='character '+args.key,
                        completeness='Every effect-bearing owner CSV row is independently reconciled; unbound/unresolved effects remain retrievable. This list includes personal, army and contextual effects, not proof of target applicability.')
    elif args.command == 'effect':
        effect = db.execute('SELECT * FROM effects WHERE effect_key=?',(args.key,)).fetchone()
        if effect is None:
            raise ValueError('Unknown effect: '+args.key)
        source_filter, params = 's.effect_key=?',[args.key]
        if args.source_kind:
            source_filter += ' AND s.kind=?';params.append(args.source_kind)
        if args.owner:
            source_filter += ' AND EXISTS(SELECT 1 FROM source_occurrences so JOIN owners o ON o.id=so.owner_id WHERE so.source_id=s.id AND o.owner_key=?)';params.append(args.owner)
        envelope.update(effect=dict(effect),bindings=page(db,'SELECT * FROM bindings WHERE effect_key=? ORDER BY id',[args.key],args.limit,args.offset),
                        sources=page(db,'SELECT s.id,s.kind,s.source_key,s.label,s.skill_level,s.scope_key,s.scope_classification,s.scope_record_id,s.value,s.record_type,s.conditions_json FROM classified_sources s WHERE '+source_filter+' ORDER BY s.kind,s.source_key,s.skill_level,s.id',params,args.limit,args.offset),
                        source_details='source <id> returns owner/variant/node references; levels are alternatives, not independent bonuses')
        for binding in envelope['bindings']['entries']:
            activation = db.execute('SELECT * FROM binding_activation WHERE binding_id=?',(binding['id'],)).fetchone()
            if activation:
                activation=dict(activation);activation['evidence']=json.loads(activation.pop('evidence_json'))
                binding['activation']=activation
    elif args.command == 'source':
        row = db.execute('SELECT * FROM classified_sources WHERE id=?',(args.key,)).fetchone()
        if row is None:
            raise ValueError('Unknown source id')
        source = dict(row);source.pop('canonical_key')
        source['conditions'] = json.loads(source.pop('conditions_json'))
        scope = db.execute("SELECT id,payload_json FROM source_records WHERE table_key='campaign_effect_scopes_tables' AND json_extract(payload_json,'$.key')=?",(source['scope_key'],)).fetchone()
        where,params = ['so.source_id=?'],[args.key]
        if args.owner:
            where.append('o.owner_key=?');params.append(args.owner)
        sql = 'SELECT o.kind,o.owner_key,o.label,o.race_slug,o.faction_key,o.source_path,so.source_row,so.node_key,so.node_set_key,so.variant_key,so.campaign_key FROM source_occurrences so JOIN owners o ON o.id=so.owner_id WHERE '+' AND '.join(where)+' ORDER BY o.owner_key,so.node_set_key,so.node_key,so.source_row'
        envelope.update(source=source,scope={'record_id':scope['id'],'fields':json.loads(scope['payload_json'])} if scope else {'status':'unresolved_scope'},occurrences=page(db,sql,params,args.limit,args.offset),
                        prerequisites='Follow each owner source_path and node_key to the existing skill/technology dataset; they remain authoritative for prerequisites and locks.')
    elif args.command == 'record':
        row = db.execute('SELECT r.*,t.source_path FROM source_records r JOIN source_tables t USING(table_key) WHERE r.id=?',(args.key,)).fetchone()
        if row is None:
            raise ValueError('Unknown source-record id')
        row = dict(row);row['fields'] = json.loads(row.pop('payload_json'))
        links = dicts(db.execute('SELECT * FROM schema_links WHERE table_key=? ORDER BY column_key',(row['table_key'],)))
        for link in links:
            link['value'] = row['fields'].get(link['column_key'])
            if link['status']=='in_extraction' and link['value']:
                sql = 'SELECT id,source_row,payload_json FROM source_records WHERE table_key=? AND json_extract(payload_json,?)=? ORDER BY id'
                link['matching_records'] = page(db,sql,[link['target_table'],'$.'+link['target_column'],link['value']],args.limit,args.offset)
        envelope.update(record=row,references=links)
    elif args.command == 'table':
        row = db.execute('SELECT * FROM source_tables WHERE table_key=?',(args.key,)).fetchone()
        if row is None:
            raise ValueError('Unknown table')
        envelope.update(table=dict(row),schema=dicts(db.execute('SELECT * FROM schema_fields WHERE table_key=? ORDER BY column_key',(args.key,))),
                        records=page(db,'SELECT id,source_row,payload_json FROM source_records WHERE table_key=? ORDER BY id',[args.key],args.limit,args.offset))
    elif args.command == 'gaps':
        options = {
            'bindings':("SELECT * FROM bindings WHERE status!='unit_target_indexed' ORDER BY target_table,id",[]),
            'sets':("SELECT * FROM set_coverage WHERE status!='selector_candidates_indexed' ORDER BY status,set_key",[]),
            'sources':('SELECT o.source_path,g.* FROM source_gaps g JOIN owners o ON o.id=g.owner_id ORDER BY owner_id,source_row',[]),
            'schema':("SELECT * FROM schema_links WHERE status='unresolved_reference' ORDER BY table_key,column_key",[]),
            'effects':('SELECT * FROM effect_coverage WHERE source_count>0 AND binding_count=0 ORDER BY effect_key',[]),
        }
        sql,params = options[args.kind]
        envelope.update(kind=args.kind,meaning='Unindexed/unresolved does not mean absent in the game or necessarily worth further extraction.',results=page(db,sql,params,args.limit,args.offset))
    elif args.command == 'inventory':
        envelope.update(tables=page(db,'SELECT table_key,family,row_count FROM source_tables ORDER BY family,table_key',[],args.limit,args.offset))
    db.close()
    return envelope


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['unit','effect','source','record','table','gaps','inventory','character','owner'])
    p.add_argument('key',nargs='?')
    p.add_argument('--data',type=Path,default=DEFAULT_DATA)
    p.add_argument('--modifiers',action='store_true')
    p.add_argument('--evidence',action='store_true',help='Unit modifier view including character-only sources; does not disable an explicit rank filter')
    p.add_argument('--bonus')
    p.add_argument('--rank',type=int)
    p.add_argument('--source-kind',choices=['skill','technology'])
    p.add_argument('--owner',help='Exact character subtype or technology-owning faction key; a source-ownership filter only')
    p.add_argument('--kind',choices=['bindings','sets','sources','schema','effects'],default='bindings')
    p.add_argument('--limit',type=int,default=10)
    p.add_argument('--offset',type=int,default=0)
    args = p.parse_args()
    if args.command not in ('gaps','inventory') and not args.key:
        p.error('A key is required')
    if not 1 <= args.limit <= 100 or args.offset < 0 or (args.rank is not None and args.rank < 0):
        p.error('Use limit 1–100, nonnegative offset and rank')
    if args.command == 'unit' and not args.modifiers and (args.bonus or args.rank is not None or args.source_kind or args.owner or args.evidence):
        p.error('Add --modifiers when filtering modifier relationships')
    try:
        from modifier_reference import verify_source
        verify_source()
        print(json.dumps(query(args),ensure_ascii=False,indent=2))
    except (ValueError,FileNotFoundError) as e:
        p.exit(1,str(e)+'\n')


if __name__ == '__main__':
    main()
