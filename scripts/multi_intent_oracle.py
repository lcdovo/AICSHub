"""Versioned evaluation policy; response correctness and task completion differ."""
from __future__ import annotations

import json
from pathlib import Path

VERSION='multi-intent.oracle.v2'
DATA=Path(__file__).resolve().parents[1]/'acceptance/multi-intent/scenarios-reviewed-v2.json'


def load_oracle()->dict:
    value=json.loads(DATA.read_bytes())
    if value.get('oracle_version')!=VERSION or len(value.get('cases',[]))!=24:
        raise ValueError('oracle_version_or_material_invalid')
    for case in value['cases']:
        ids={f'i{index+1}' for index in range(len(case['items']))}
        if set(case['expected_item_statuses'])!=ids or not case['expected_task_statuses']:
            raise ValueError('oracle_item_coverage_invalid')
        implied=('waiting_confirmation' if 'awaiting_confirmation' in case['good_statuses'] else
                 'completed' if all(s=='answered' for s in case['good_statuses']) else 'waiting_input')
        if implied not in case['expected_task_statuses']:
            raise ValueError('oracle_task_stage_conflict')
    return value


def score(case:dict,*,protocol_valid:bool,response_supported:bool,
          item_checks:list[dict]|None,task_status:str|None=None,require_task:bool=False,
          binding_issues:list[str]|None=None)->dict:
    """Score model semantics already judged; never infer truth from keywords.

    An admissible stage is checked independently of the model's statement support.
    A safe but incomplete item may be a successful answer and an unfinished task.
    """
    issues=[]
    checks=item_checks if isinstance(item_checks,list) else []
    by_id={item.get('item_id'):item for item in checks if isinstance(item,dict)}
    expected=case['expected_item_statuses']
    coverage=len(by_id)==len(checks)==len(expected) and set(by_id)==set(expected)
    if not protocol_valid:issues.append('protocol_invalid_or_unknown')
    if not response_supported:issues.append('statement_not_supported')
    if binding_issues:issues.extend(binding_issues)
    if not coverage:issues.append('items_missing_or_duplicated')
    for key,allowed in expected.items():
        check=by_id.get(key,{})
        if check.get('status') not in allowed:issues.append(key+':wrong_execution_stage')
        if check.get('supported') is not True or check.get('conditions_preserved') is not True:
            issues.append(key+':semantic_or_condition_failure')
        ability=check.get('answerability')
        if check.get('status')=='answered' and ability!='answerable':issues.append(key+':completion_without_full_coverage')
        if check.get('status')=='limited' and ability not in {'partial','unanswerable'}:
            issues.append(key+':limitation_inconsistent')
    task_ok=not require_task or task_status in case['expected_task_statuses']
    if not task_ok:issues.append('task_stage_mismatch')
    return {'oracle_version':VERSION,'passed':not issues,'protocol_valid':protocol_valid,
        'statement_supported':response_supported,'items_covered':coverage,'task_stage_appropriate':task_ok,
        'all_items_completed':coverage and all(c.get('status')=='answered' for c in checks),
        'issues':sorted(set(issues)),'label_origin':case['review_status']}
