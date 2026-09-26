"""Behavior tests for retrieval boundaries and dangerous targeting edge cases."""
import importlib.util
import json
import shlex
import subprocess
import sys
import unittest
import sqlite3
import tempfile
from concurrent.futures import ThreadPoolExecutor
import modifier_reference
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace
from character_reference import identity_status, identity_context, personal_allowed_sql
from reference_access import reconcile_character_sources, validate_form_access
from modifier_reference import ROOT, ANALYSIS_ROOT, CODE_ROOT, DEFAULT_DATA, selector_match, membership_status, open_reference, scope_classification

DATA=Path(sys.argv.pop(1)).resolve() if len(sys.argv)>1 and not sys.argv[1].startswith('-') else DEFAULT_DATA
spec=importlib.util.spec_from_file_location('query_reference',CODE_ROOT/'query-modifier-reference.py')
query_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(query_module)


class Targeting(unittest.TestCase):
    def test_character_rank_filter_fails_with_clear_message(self):
        result=subprocess.run([sys.executable,str(CODE_ROOT/'query-modifier-reference.py'),'character','wh_main_emp_karl_franz','--rank','6'],capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('--rank filters unit experience, not mount unlocks',result.stderr)
        self.assertNotIn('Traceback',result.stderr)

    def test_scope_is_recipient_based_not_label_based(self):
        self.assertEqual(scope_classification({'key':'army_buff','target':'character'}),'character_only')
        self.assertEqual(scope_classification({'key':'personal','target':'force'}),'force_or_army')
        self.assertEqual(scope_classification({'target':'unrecognized'}),'unresolved_scope')
        self.assertEqual(scope_classification(None),'unresolved_scope')

    def test_compound_selector_keeps_disagreement_unknown(self):
        row=dict(unit_record='a',unit_class='',unit_category='',unit_caste='lord')
        self.assertEqual(selector_match(row,dict(unit_key='a',source_caste='infantry')),'possible')
        self.assertEqual(selector_match(row,dict(unit_key='a',source_caste='lord')),'match')

    def test_missing_selector_fields_and_caste_remain_unknown(self):
        row=dict(unit_record='',unit_class='com',unit_category='',unit_caste='')
        self.assertEqual(selector_match(row,dict(unit_key='unknown',source_unit_class=None)),'unresolved')
        self.assertEqual(membership_status(['match'],['unresolved']),'conditional_selector')
        unit=dict(unit_key='unknown',source_caste=None)
        self.assertEqual(identity_status(None,1,unit),'unresolved_character_identity')
        self.assertEqual(personal_allowed_sql(None,unit),('1',[]))

    def test_exclusion_and_special_category(self):
        self.assertEqual(membership_status(['match'],['match']),'excluded')
        self.assertEqual(membership_status(['match'],['possible']),'conditional_selector')
        self.assertEqual(membership_status(['match'],[],'mistwalker'),'conditional_selector')
        self.assertEqual(membership_status([],[]),'no_match')

    def test_base_query_never_needs_modifier_database(self):
        a=SimpleNamespace(command='unit',modifiers=False,key='wh2_main_hef_inf_lothern_sea_guard_0',data=Path('/does/not/exist'))
        result=query_module.query(a)
        self.assertIn('base_stats',result)
        self.assertNotIn('candidates',result)

    def test_unknown_unit_is_not_empty_success(self):
        with self.assertRaisesRegex(ValueError,'Unknown roster unit'):
            query_module.base_unit('nonexistent_unit')


class FullSnapshot(unittest.TestCase):
    def test_empty_candidates_link_to_counted_owner_evidence(self):
        owner='wh2_dlc11_cst_noctilus'
        for kind,count in [('technology',45),('skill',301),(None,346)]:
            with self.subTest(kind=kind):
                # Technology is naturally empty; other kinds exercise a filtered zero.
                result=self.unit_query('wh2_dlc11_cst_inf_count_noctilus_0',owner=owner,
                    source_kind=kind,bonus=None if kind=='technology' else 'nonexistent_bonus')
                self.assertEqual(result['candidates']['total'],0)
                notice=result['empty_result']
                self.assertEqual(notice['status'],'no_indexed_candidates_under_selected_filters')
                self.assertIn('does not establish',notice['message'])
                evidence=notice['owner_source_evidence']
                self.assertEqual(evidence['effect_occurrences'],count)
                self.assertIn('applicability to this unit is not established',evidence['meaning'])
                self.assertEqual(evidence['query'],result['source_owner_retrieval'])
                command=shlex.split(evidence['query'])
                self.assertEqual('--source-kind' in command,kind is not None)
                actual=subprocess.run([sys.executable,str(CODE_ROOT/'query-modifier-reference.py'),
                    *command,'--data',str(DATA),'--limit','1'],check=True,capture_output=True,text=True)
                self.assertEqual(json.loads(actual.stdout)['effect_occurrences']['total'],count)

    def test_empty_notice_requires_zero_total_and_does_not_invent_owner(self):
        for offset in (0,1000000):
            result=self.unit_query(offset=offset)
            self.assertGreater(result['candidates']['total'],0)
            self.assertNotIn('empty_result',result)
        for owner in (None,'nonexistent_owner'):
            result=self.unit_query(owner=owner,bonus='nonexistent_bonus')
            self.assertEqual(result['candidates']['total'],0)
            self.assertNotIn('owner_source_evidence',result['empty_result'])

    def test_unit_detail_link_preserves_source_filters(self):
        for owner,kind in [('wh_main_emp_karl_franz','skill'),
                           ('wh_main_emp_karl_franz',None),(None,'skill'),(None,None)]:
            with self.subTest(owner=owner,kind=kind):
                result=self.unit_query('wh_main_emp_cha_karl_franz_0',owner=owner,
                                       source_kind=kind,bonus='melee_attack_mod')
                entry=next(e for e in result['candidates']['entries']
                           if e['effect_key']=='wh_main_effect_character_stat_melee_attack')
                command=shlex.split(entry['details_query'])
                self.assertEqual('--owner' in command,owner is not None)
                self.assertEqual('--source-kind' in command,kind is not None)
                detail=subprocess.run([sys.executable,str(CODE_ROOT/'query-modifier-reference.py'),
                                       *command,'--data',str(DATA),'--limit','100'],
                                      check=True,capture_output=True,text=True)
                actual=json.loads(detail.stdout)['sources']
                expected=query_module.query(SimpleNamespace(command='effect',key=entry['effect_key'],
                    data=DATA,owner=owner,source_kind=kind,limit=100,offset=0))['sources']
                self.assertEqual(actual,expected)
                if owner:
                    self.assertEqual(actual['total'],4)

    def unit_query(self,key='wh2_main_hef_inf_lothern_sea_guard_0',**kwargs):
        args=dict(command='unit',key=key,data=DATA,modifiers=True,bonus=None,rank=None,source_kind=None,owner='wh2_main_hef_teclis',limit=100,offset=0,evidence=False)
        args.update(kwargs)
        return query_module.query(SimpleNamespace(**args))

    @classmethod
    def setUpClass(cls):
        cls.db,_=open_reference(DATA)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_seaguard_variants_and_spearmen(self):
        effect='wh2_main_effect_force_stat_ammunition_hef_archer_seaguard_boltthrower_sister_shadow'
        found={r[0] for r in self.db.execute('SELECT DISTINCT c.unit_key FROM unit_binding_candidates c JOIN bindings b ON b.id=c.binding_id WHERE b.effect_key=?',(effect,))}
        self.assertIn('wh2_main_hef_inf_lothern_sea_guard_0',found)
        self.assertIn('wh2_main_hef_inf_lothern_sea_guard_1',found)
        self.assertNotIn('wh2_main_hef_inf_spearmen_0',found)

    def test_rank_filter_does_not_invent_permanence(self):
        base=dict(command='unit',key='wh2_main_hef_inf_lothern_sea_guard_0',data=DATA,modifiers=True,bonus='armour_mod',source_kind='skill',owner='wh2_main_hef_teclis',limit=100,offset=0)
        before=query_module.query(SimpleNamespace(**base,rank=6))
        after=query_module.query(SimpleNamespace(**base,rank=7))
        effect='wh2_main_effect_force_stat_armour_archers_seaguard_boltthrower_sister_shadow_rank7'
        self.assertNotIn(effect,{r['effect_key'] for r in before['candidates']['entries']})
        self.assertIn(effect,{r['effect_key'] for r in after['candidates']['entries']})
        self.assertIn('potential relevance',after['interpretation'])

    def test_skill_levels_remain_alternatives(self):
        effect='wh2_main_effect_force_stat_ammunition_hef_archer_seaguard_boltthrower_sister_shadow'
        rows=self.db.execute('SELECT skill_level,value FROM sources WHERE kind=\'skill\' AND effect_key=? ORDER BY skill_level',(effect,)).fetchall()
        self.assertEqual(len(rows),3)
        self.assertEqual([r['skill_level'] for r in rows],['1','2','3'])

    def test_personal_sources_separated_for_both_variants(self):
        for suffix in ('0','1'):
            key='wh2_main_hef_inf_lothern_sea_guard_'+suffix
            ordinary=self.unit_query(key)['candidates']
            evidence=self.unit_query(key,evidence=True)['candidates']
            self.assertEqual(ordinary['total'],10)
            self.assertEqual(evidence['total'],17)
            self.assertTrue(all('character_only' not in x['source_scope_classifications'] for x in ordinary['entries']))
            self.assertTrue(any('character_only' in x['source_scope_classifications'] for x in evidence['entries']))
            bonuses={x['bonus_key'] for x in ordinary['entries']}
            self.assertTrue({'ammo_mod','melee_attack_mod','melee_defence_mod'} <= bonuses)
            # Favourable Winds must remain genuinely rank-filtered in both variants.
            effect='wh2_main_effect_force_stat_armour_archers_seaguard_boltthrower_sister_shadow_rank7'
            self.assertNotIn(effect,{x['effect_key'] for x in self.unit_query(key,rank=6)['candidates']['entries']})
            self.assertIn(effect,{x['effect_key'] for x in self.unit_query(key,rank=7)['candidates']['entries']})

    def test_unknown_and_mixed_scopes_are_not_discarded_by_effect_key(self):
        # Perturb a snapshot copy: a newly encountered recipient must survive,
        # while mixed personal/army sources must filter only the personal source.
        for classification in ('unresolved_scope','force_or_army'):
            db=sqlite3.connect(':memory:');self.db.backup(db);db.row_factory=sqlite3.Row
            effect='wh2_main_effect_force_stat_ammunition_hef_archer_seaguard_boltthrower_sister_shadow'
            db.execute("UPDATE sources SET scope_key='character_to_character_own' WHERE effect_key=? AND skill_level='1'",(effect,))
            db.execute("UPDATE scope_classifications SET classification=? WHERE scope_key='general_to_force_own'",(classification,))
            with patch.object(query_module,'open_reference',return_value=(db,{'patch':'test'})):
                result=self.unit_query()['candidates']['entries']
            entry=next(x for x in result if x['effect_key']==effect)
            self.assertEqual(entry['source_scope_classifications'],{classification:2})
            self.assertEqual(entry['omitted_character_only_source_count'],1)

    def test_weapon_activation_is_unresolved_independently_of_rank_set(self):
        effect='wh3_dlc27_effect_tech_vs_infantry_bows_hef_inf_r7'
        for suffix in ('0','1'):
            for rank in (6,7):
                entries=self.unit_query('wh2_main_hef_inf_lothern_sea_guard_'+suffix,rank=rank,owner='wh2_main_hef_order_of_loremasters')['candidates']['entries']
                entries=[x for x in entries if x['effect_key']==effect]
                weapon=next(x for x in entries if x['bonus_key']=='enable')
                self.assertEqual(weapon['activation_status'],'unresolved_weapon_activation')
                self.assertTrue(all(x['rank_status']=='unresolved_rank_activation' and x['rank_match'] is None for x in weapon['candidate_paths']))
                self.assertEqual(any(x['bonus_key']=='reload' for x in entries),rank==7)

    def test_actual_variant_specific_bindings_remain_distinct(self):
        sql='SELECT DISTINCT b.effect_key FROM unit_binding_candidates c JOIN bindings b ON b.id=c.binding_id WHERE c.unit_key=?'
        a={r[0] for r in self.db.execute(sql,('wh2_main_hef_inf_lothern_sea_guard_0',))}
        b={r[0] for r in self.db.execute(sql,('wh2_main_hef_inf_lothern_sea_guard_1',))}
        self.assertIn('wh3_dlc27_tech_upgrade_sea_guard',a-b)
        self.assertIn('wh2_dlc15_effect_force_unit_stat_armour_hef_shields',b-a)

    def test_pink_horror_technology_controls(self):
        owner='wh3_main_tze_oracles_of_tzeentch'
        units=['wh3_main_tze_inf_pink_horrors_0','wh3_main_tze_inf_pink_horrors_1','wh3_main_tze_inf_blue_horrors_0']
        effects=[{x['effect_key'] for x in self.unit_query(k,owner=owner)['candidates']['entries']} for k in units]
        grant='wh3_main_effect_ability_enable_arcane_mirth_tze_horrors'
        cost='wh3_main_effect_recruitment_cost_tze_pink_horrors'
        self.assertEqual([grant in x for x in effects],[True,False,True])
        self.assertEqual([cost in x for x in effects],[True,True,False])
        # Exalted does not need that grant: its base ability lookup already has it.
        base={r[0] for r in self.db.execute("SELECT unit_key FROM unit_targets WHERE kind='ability' AND target_key='wh3_main_unit_passive_arcane_mirth'")}
        self.assertNotIn(units[0],base);self.assertIn(units[1],base);self.assertNotIn(units[2],base)

    def test_pink_horror_rank_and_personal_scope(self):
        key='wh3_main_tze_inf_pink_horrors_0';owner='wh3_main_tze_kairos'
        low=self.unit_query(key,owner=owner,rank=6)['candidates']
        high=self.unit_query(key,owner=owner,rank=7)['candidates']
        raw=self.unit_query(key,owner=owner,rank=7,evidence=True)['candidates']
        self.assertEqual((low['total'],high['total']),(9,12))
        self.assertGreater(raw['total'],high['total'])
        self.assertTrue(all('character_only' not in x['source_scope_classifications'] for x in high['entries']))
        difference={x['bonus_key'] for x in high['entries'] if x['binding_id'] not in {x['binding_id'] for x in low['entries']}}
        self.assertEqual(difference,{'morale','unit_damage_resistance_missile_mod','mod_land_movement_battle'})

    def test_franz_identity_across_supported_mounts(self):
        for suffix in ('0','4','2','1'):
            key='wh_main_emp_cha_karl_franz_'+suffix
            own=self.unit_query(key,owner='wh_main_emp_karl_franz')['candidates']['entries']
            self.assertTrue(any(x['bonus_key']=='melee_attack_mod' and 'source_character_identity_match' in x['personal_source_identity_statuses'] for x in own))
            self.assertTrue(any(x['bonus_key']=='melee_defence_mod' and 'source_character_identity_match' in x['personal_source_identity_statuses'] for x in own))
            other=self.unit_query(key,owner='wh2_main_hef_teclis')['candidates']['entries']
            raw=self.unit_query(key,owner='wh2_main_hef_teclis',evidence=True)['candidates']['entries']
            wrong={x['binding_id'] for x in raw if 'source_character_identity_mismatch' in x['personal_source_identity_statuses']}
            self.assertTrue(wrong)
            self.assertFalse(wrong & {x['binding_id'] for x in other})

    def test_franz_mount_acquisition_is_independent_of_effect_binding(self):
        args=SimpleNamespace(command='character',key='wh_main_emp_karl_franz',data=DATA,limit=100,offset=0)
        result=query_module.query(args)
        self.assertEqual({x['unit_key'] for x in result['forms']['entries']},{'wh_main_emp_cha_karl_franz_'+x for x in ('0','1','2','4')})
        mounts=result['mount_acquisitions']['entries']
        self.assertEqual(len(mounts),3)
        horse=next(x for x in mounts if x['unit_key'].endswith('_4'))
        self.assertEqual(horse['node_rank'],'6')
        self.assertEqual({x['fields']['level_unlocked_at_rank'] for x in horse['level_evidence']},{'3'})
        self.assertEqual(horse['rank_status'],'rank_fields_differ_effective_rank_unresolved')
        self.assertIsNone(horse['effective_unlock_rank'])
        for mount in mounts:
            self.assertTrue(mount['related_effect_keys'])
            for effect in mount['related_effect_keys']:
                self.assertEqual(self.db.execute('SELECT COUNT(*) FROM bindings WHERE effect_key=?',(effect,)).fetchone()[0],0)
        unsupported=self.unit_query('wh_main_emp_cha_karl_franz_3',owner='wh_main_emp_karl_franz')
        self.assertEqual(unsupported['character_identities'],[])
        self.assertEqual({r['acquisition_status'] for r in unsupported['mount_records']['entries']},{'unconfirmed_acquisition'})
        self.assertTrue(any('unresolved_character_identity' in x['personal_source_identity_statuses'] for x in unsupported['candidates']['entries']))

    def test_character_forms_do_not_copy_base_abilities(self):
        foot={r[0] for r in self.db.execute("SELECT target_key FROM unit_targets WHERE unit_key='wh_main_emp_cha_karl_franz_0' AND kind='attribute'")}
        deathclaw={r[0] for r in self.db.execute("SELECT target_key FROM unit_targets WHERE unit_key='wh_main_emp_cha_karl_franz_1' AND kind='attribute'")}
        self.assertNotEqual(foot,deathclaw)
        # Technology/area-wide character recipients must not be equated with
        # their source owner. Only source=self, location=character is personal.
        self.assertEqual(self.db.execute("SELECT identity_policy FROM character_scope_policies WHERE scope_key='character_to_character_own'").fetchone()[0],'same_source_character')
        self.assertEqual(self.db.execute("SELECT identity_policy FROM character_scope_policies WHERE scope_key='faction_to_character_own'").fetchone()[0],'recipient_context_unresolved')

    def test_concurrent_first_reads_publish_independent_cache_files(self):
        # Isolate the cache so both readers exercise first-open decompression.
        with tempfile.TemporaryDirectory() as folder, patch.object(modifier_reference,'ANALYSIS_ROOT',Path(folder)):
            def read():
                db,_=open_reference(DATA)
                result=db.execute('PRAGMA integrity_check').fetchone()[0]
                db.close()
                return result
            with ThreadPoolExecutor(max_workers=2) as pool:
                self.assertEqual(list(pool.map(lambda _:read(),range(2))),['ok','ok'])
            self.assertFalse(list(Path(folder).rglob('*.tmp')))

    def test_prophetess_conflicts_retain_personal_candidates(self):
        for owner in ('wh_dlc07_brt_prophetess_life','wh_dlc07_brt_prophetess_heavens'):
            for key in ('wh_dlc07_brt_cha_prophetess_2','wh_dlc07_brt_cha_prophetess_3','wh_dlc07_brt_cha_prophetess_heavens_2','wh_dlc07_brt_cha_prophetess_heavens_3'):
                result=self.unit_query(key,owner=owner)
                ctx=result['character_identity_resolution']
                self.assertEqual(ctx['status'],'conflicting_character_identity')
                self.assertTrue(ctx['form_paths']);self.assertTrue(ctx['custom_battle_paths'])
                personal=[x for x in result['candidates']['entries'] if 'character_only' in x['source_scope_classifications']]
                self.assertTrue(personal)
                self.assertTrue(all('conflicting_character_identity' in x['personal_source_identity_statuses'] for x in personal))
                self.assertEqual(result['candidates']['total'],self.unit_query(key,owner=owner,evidence=True)['candidates']['total'])

    def test_removing_shared_base_anchor_cannot_create_negative_identity(self):
        db=sqlite3.connect(':memory:');self.db.backup(db);db.row_factory=sqlite3.Row
        owner=db.execute("SELECT id FROM owners WHERE owner_key='wh2_dlc09_tmb_necrotect'").fetchone()[0]
        unit=db.execute("SELECT * FROM units WHERE unit_key='wh2_dlc09_tmb_cha_necrotect_0'").fetchone()
        self.assertEqual(identity_status(db,owner,unit),'source_character_identity_match')
        db.execute('DELETE FROM character_forms WHERE owner_id=? AND unit_key=?',(owner,unit['unit_key']))
        self.assertEqual(identity_status(db,owner,unit),'unresolved_character_identity')
        db.close()

    def test_removing_conflicting_path_cannot_create_positive_identity(self):
        db=sqlite3.connect(':memory:');self.db.backup(db);db.row_factory=sqlite3.Row
        owner=db.execute("SELECT id FROM owners WHERE owner_key='wh_dlc07_brt_prophetess_heavens'").fetchone()[0]
        unit=db.execute("SELECT * FROM units WHERE unit_key='wh_dlc07_brt_cha_prophetess_2'").fetchone()
        self.assertEqual(identity_status(db,owner,unit),'conflicting_character_identity')
        db.execute('DELETE FROM custom_battle_mounts WHERE mounted_unit=?',(unit['unit_key'],))
        self.assertEqual(identity_status(db,owner,unit),'unresolved_character_identity')
        db.close()

    @patch.object(query_module,'open_reference')
    def test_character_navigation_matches_entire_normalized_roster(self,open_db):
        dbpath=self.db.execute('PRAGMA database_list').fetchone()[2]
        manifest=json.loads((DATA/'dataset_manifest.json').read_text())
        def connection(_):
            db=sqlite3.connect(Path(dbpath).as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
            return db,manifest
        open_db.side_effect=connection
        actual={r['unit_key'] for p in (ROOT/'data/unit_stats/normalized').glob('*.csv') for _,r in query_module.records(p)}
        missing=set();owners=set();relations=0
        for (owner,) in self.db.execute("SELECT owner_key FROM owners WHERE kind='skill'"):
            offset=0
            while offset is not None:
                result=query_module.query(SimpleNamespace(command='character',key=owner,data=DATA,limit=100,offset=offset))
                for form in result['forms']['entries']:
                    self.assertEqual(form['modifier_query'],'unit '+form['unit_key']+' --modifiers --owner '+owner)
                    self.assertEqual(form['owner_effects_query'],'owner '+owner+' --source-kind skill')
                    if form['unit_key'] in actual:
                        self.assertEqual(form['base_query'],'unit '+form['unit_key'])
                        self.assertEqual(form['normalized_base_stat_coverage'],'available')
                    else:
                        missing.add(form['unit_key']);owners.add(owner);relations+=1
                        self.assertNotIn('base_query',form)
                        self.assertEqual(form['normalized_base_stat_coverage'],'unavailable')
                        refs=form['evidence'];refs=[refs] if 'path' in refs else list(refs.values())
                        self.assertTrue(all((ROOT/e['path']).is_file() and e['row']>0 for e in refs))
                offset=result['forms']['next_offset']
        coverage=json.loads((DATA/'coverage_report.json').read_text())
        self.assertEqual((len(missing),relations,len(owners)),(461,534,229))
        self.assertEqual(len(missing),coverage['character_forms_without_normalized_stats'])
        self.assertIn('wh2_dlc17_bst_cha_beastlord_2',missing)

    @patch.object(query_module,'open_reference')
    def test_every_supplemental_form_has_working_modifier_access(self,open_db):
        dbpath=self.db.execute('PRAGMA database_list').fetchone()[2]
        manifest=json.loads((DATA/'dataset_manifest.json').read_text())
        def connection(_):
            db=sqlite3.connect(Path(dbpath).as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
            return db,manifest
        open_db.side_effect=connection
        forms=self.db.execute('SELECT DISTINCT cf.unit_key,o.owner_key FROM character_forms cf JOIN owners o ON o.id=cf.owner_id JOIN supplemental_forms u USING(unit_key)').fetchall()
        for key,owner in forms:
            result=self.unit_query(key,owner=owner,limit=1)
            self.assertEqual(result['unit']['normalized_base_stat_coverage'],'unavailable')
            self.assertIn('candidates',result)
            self.assertEqual(result['source_owner_retrieval'],'owner '+owner)
        self.assertEqual(len({r[0] for r in forms}),461)

    def test_independent_reconciliation_catches_missing_occurrence_and_owner(self):
        report=reconcile_character_sources(self.db)
        self.assertEqual(report['owners'],500)
        self.assertEqual(report,json.loads((DATA/'character_source_reconciliation.json').read_text()))
        db=sqlite3.connect(':memory:');self.db.backup(db);db.row_factory=sqlite3.Row
        oid=db.execute("SELECT id FROM owners WHERE kind='skill' ORDER BY id LIMIT 1").fetchone()[0]
        db.execute('SAVEPOINT mutate')
        db.execute('DELETE FROM source_occurrences WHERE id=(SELECT MIN(id) FROM source_occurrences WHERE owner_id=?)',(oid,))
        with self.assertRaisesRegex(ValueError,'Missing character source occurrence'):
            reconcile_character_sources(db)
        db.execute('ROLLBACK TO mutate')
        db.execute('DELETE FROM owners WHERE id=?',(oid,))
        with self.assertRaisesRegex(ValueError,'Missing character source owner'):
            reconcile_character_sources(db)
        db.close()

    def test_source_target_oracle_detects_deleted_targets_and_routes(self):
        self.assertEqual(validate_form_access(self.db)['forms'],461)
        db=sqlite3.connect(':memory:');self.db.backup(db);db.row_factory=sqlite3.Row
        db.execute('SAVEPOINT mutate')
        db.execute('DELETE FROM supplemental_targets WHERE rowid=(SELECT MIN(rowid) FROM supplemental_targets)')
        with self.assertRaisesRegex(ValueError,'Supplemental target reconciliation differs'):
            validate_form_access(db)
        db.execute('ROLLBACK TO mutate')
        db.execute("UPDATE supplemental_targets SET status='selector_match' WHERE rowid=(SELECT MIN(rowid) FROM supplemental_targets)")
        with self.assertRaisesRegex(ValueError,'Supplemental target reconciliation differs'):
            validate_form_access(db)
        db.execute('ROLLBACK TO mutate')
        db.execute('DELETE FROM binding_targets WHERE rowid=(SELECT MIN(rowid) FROM binding_targets)')
        with self.assertRaisesRegex(ValueError,'Typed binding target coverage differs'):
            validate_form_access(db)
        db.close()

    @patch.object(query_module,'open_reference')
    def test_all_owner_effects_paginate_including_unbound(self,open_db):
        dbpath=self.db.execute('PRAGMA database_list').fetchone()[2]
        manifest=json.loads((DATA/'dataset_manifest.json').read_text())
        def connection(_):
            db=sqlite3.connect(Path(dbpath).as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
            return db,manifest
        open_db.side_effect=connection
        unbound=0
        for owner in self.db.execute("SELECT owner_key,source_path FROM owners WHERE kind='skill'"):
            expected={n:r['effect_key'] for n,r in query_module.records(ROOT/owner['source_path']) if r.get('effect_key')}
            actual={};offset=0
            while offset is not None:
                result=query_module.query(SimpleNamespace(command='owner',key=owner['owner_key'],data=DATA,source_kind='skill',limit=100,offset=offset))
                for entry in result['effect_occurrences']['entries']:
                    self.assertNotIn(entry['source_row'],actual)
                    actual[entry['source_row']]=entry['effect_key']
                    self.assertTrue(entry['effect_query'].startswith('effect '))
                    self.assertIn('--source-kind skill',entry['effect_query'])
                    self.assertEqual(entry['owner_kind'],'skill')
                    if entry['target_access_status']=='no_binding_in_extraction': unbound+=1
                offset=result['effect_occurrences']['next_offset']
            self.assertEqual(actual,expected)
        self.assertGreater(unbound,0)
        mixed=query_module.query(SimpleNamespace(command='owner',key='wh2_dlc17_bst_taurox',data=DATA,limit=1,offset=0))
        self.assertEqual({o['kind'] for o in mixed['owners']},{'skill','technology'})

    def test_all_identity_conflicts_remain_visible(self):
        expected=set()
        for (unit,) in self.db.execute('SELECT DISTINCT mounted_unit FROM custom_battle_mounts'):
            grants={r[0] for r in self.db.execute("SELECT a.unit_key FROM character_forms m JOIN character_forms a ON a.owner_id=m.owner_id AND a.relation='associated_unit_override' WHERE m.relation='skill_granted_mount' AND m.unit_key=?",(unit,))}
            customs={r[0] for r in self.db.execute('SELECT base_unit FROM custom_battle_mounts WHERE mounted_unit=?',(unit,))}
            if grants and customs and grants!=customs:
                expected.add(unit)
                self.assertEqual(identity_context(self.db,unit)['status'],'conflicting_character_identity')
        self.assertEqual(len(expected),9)

    def test_coverage_includes_entire_extraction(self):
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM source_tables').fetchone()[0],220)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM source_records').fetchone()[0],109408)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM bindings').fetchone()[0],23110)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM units').fetchone()[0],1669)

    def test_pagination_is_complete_without_duplicates(self):
        base=dict(command='unit',key='wh2_main_hef_inf_lothern_sea_guard_0',data=DATA,modifiers=True,bonus=None,rank=None,source_kind='skill',owner='wh2_main_hef_teclis',limit=5)
        ids=[];offset=0;total=None
        while offset is not None:
            result=query_module.query(SimpleNamespace(**base,offset=offset))['candidates']
            total=result['total'];ids.extend(r['binding_id'] for r in result['entries']);offset=result['next_offset']
        self.assertEqual(len(ids),len(set(ids)))
        self.assertEqual(len(ids),total)


if __name__=='__main__':
    unittest.main()
