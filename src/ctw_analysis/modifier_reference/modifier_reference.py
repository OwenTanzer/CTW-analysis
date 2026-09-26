"""Shared source-faithful helpers for the optional per-unit modifier reference."""
import os
import subprocess
import csv
import gzip
import hashlib
import json
import sqlite3
import tempfile
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parent
ANALYSIS_ROOT = CODE_ROOT.parents[2]
ROOT = Path(os.environ.get('CTW_ROOT', ANALYSIS_ROOT.parent / 'computational-total-war')).resolve()
DEFAULT_DATA = ANALYSIS_ROOT / 'work/modifier_reference'


def verify_source():
    lock = json.loads((ANALYSIS_ROOT / 'studies/modifier_reference/source_lock.json').read_text())
    actual = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != lock['git_commit']:
        raise ValueError('CTW source commit does not match studies/modifier_reference/source_lock.json')
    dirty = subprocess.check_output(['git', '-C', str(ROOT), 'status', '--porcelain', '--untracked-files=no'], text=True)
    if dirty.strip():
        raise ValueError('CTW source checkout has tracked modifications')

SELECTORS = {'unit_record': 'unit_key', 'unit_class': 'source_unit_class',
             'unit_category': 'source_category', 'unit_caste': 'source_caste'}


def scope_classification(fields):
    """Classify the retained recipient field, never the scope key or label.

    Conditional character/force enums are deliberately unresolved until their
    propagation semantics are established. This is not an applicability engine.
    """
    target = (fields or {}).get('target')
    return {'character': 'character_only', 'force': 'force_or_army',
            'army': 'force_or_army', 'faction': 'faction_context'}.get(target, 'unresolved_scope')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def compact(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def records(path, delimiter=','):
    """Yield physical start line and dictionary; retain empty strings and exact values."""
    with Path(path).open(encoding='utf-8-sig', newline='') as f:
        reader = csv.reader(f, delimiter=delimiter)
        columns = next(reader)
        if len(set(columns)) != len(columns):
            raise ValueError(f'Duplicate columns: {path}')
        previous = reader.line_num
        for row in reader:
            start = previous + 1
            previous = reader.line_num
            if not row or not any(row) or (delimiter == '\t' and row[0].startswith('#')):
                continue
            if len(row) != len(columns):
                raise ValueError(f'Row width mismatch: {path}:{start}')
            yield start, dict(zip(columns, row))


def selector_match(selector, unit):
    """Three-valued match; never invent AND/OR for multi-field selectors."""
    predicates = [None if unit.get(dest) in (None, '') else unit.get(dest) == selector[src] for src, dest in SELECTORS.items() if selector[src]]
    if not predicates:
        return 'unresolved'
    if all(p is True for p in predicates):
        return 'match'
    if None in predicates:
        return 'unresolved'
    if any(predicates):
        return 'possible'
    return 'no_match'


def membership_status(includes, excludes, special_category=''):
    # A matching exclude makes a statically selected unit excluded; an uncertain
    # exclude never silently removes it from the candidate index.
    if 'match' in excludes:
        return 'excluded'
    if 'match' in includes:
        return 'conditional_selector' if special_category or any(x in excludes for x in ('possible', 'unresolved')) else 'selector_match'
    if any(x in includes for x in ('possible', 'unresolved')):
        return 'unresolved_selector'
    return 'no_match'


def family(table):
    if table.startswith('effect_bonus_value_'):
        return 'effect_bindings'
    if table.startswith('campaign_bonus_value_battle_context_'):
        return 'battle_conditions'
    if table.startswith('campaign_effect_scope'):
        return 'scope_definitions'
    if table in ('unit_sets_tables', 'unit_set_to_unit_junctions_tables',
                 'unit_set_unit_ability_junctions_tables', 'unit_set_unit_attribute_junctions_tables',
                 'unit_set_special_ability_phase_junctions_tables'):
        return 'unit_targets'
    if table == '_kv_experience_bonuses_tables' or table.startswith(('unit_experience_', 'unit_stats_land_experience_bonuses')):
        return 'experience'
    if table in ('campaign_unit_stat_bonuses_tables', 'modifiable_unit_stats_tables',
                 'unit_stat_modifiers_tables', 'scripted_bonus_value_ids_tables'):
        return 'stat_rule_definitions'
    if table == 'effects_tables':
        return 'effect_definitions'
    return 'supporting_relation'


def open_reference(data=DEFAULT_DATA):
    data = Path(data)
    manifest = json.loads((data / 'dataset_manifest.json').read_text())
    packed = (data / 'reference.sqlite.gz').read_bytes()
    if digest(packed) != manifest['artifacts']['reference.sqlite.gz']['sha256']:
        raise ValueError('Modifier reference archive hash mismatch')
    cache = ANALYSIS_ROOT / 'work/modifier-reference-cache'
    cache.mkdir(parents=True, exist_ok=True)
    key = manifest['database_sha256']
    dbfile = cache / (key + '.sqlite')
    if not dbfile.exists() or digest(dbfile.read_bytes()) != key:
        payload = gzip.decompress(packed)
        if digest(payload) != key:
            raise ValueError('Modifier reference database hash mismatch')
        # Concurrent first readers must not rename or truncate one another's
        # temporary file. Each verified payload replaces the cache atomically.
        with tempfile.NamedTemporaryFile(dir=cache,suffix='.tmp',delete=False) as f:
            temp=Path(f.name)
            f.write(payload)
        try:
            try:
                temp.replace(dbfile)
            except PermissionError:
                # Windows may deny replacing a verified cache already opened
                # by another reader. Accept only that exact completed payload.
                if not dbfile.is_file() or digest(dbfile.read_bytes()) != key:
                    raise
        finally:
            temp.unlink(missing_ok=True)
    db = sqlite3.connect(dbfile.as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    return db, manifest
