from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.agent.item_judge_selectors import NonfactSelector
from scripts.run_item_v2_atomic_acceptance import scope_dataset
from scripts.evaluate_multi_intent import fixture_state,candidate
from tests.test_item_judge_v2 import assess,configured as configured


def test_nonfact_empty_shape_is_explicit_and_cannot_carry_factual_support():
    for shape in [{'kind':'general_advice'},{'kind':'general_advice','support':'unknown','evidence':[],'issues':[]}]:
        assert NonfactSelector.model_validate(shape).model_dump()=={'kind':'general_advice','support':'unknown','evidence':[],'issues':[]}
    for field,value in [('support','supported'),('evidence',['K1']),('issues',['unsupported_detail'])]:
        with pytest.raises(ValueError):NonfactSelector.model_validate({'kind':'general_advice',field:value})


@pytest.mark.parametrize('case_id',[case['case_id'] for case in scope_dataset()['cases']])
def test_captured_selector_component_receipts_keep_the_same_results(case_id):
    root=Path(__file__).resolve().parents[1]
    capture=json.loads((root/'acceptance/multi-intent/captured-selector-components-v1.json').read_bytes())
    case=next(c for c in scope_dataset()['cases'] if c['case_id']==case_id)
    state=fixture_state(case)
    for bad in (False,True):
        row=next(r for r in capture if r['operation']==f'selectors-v1/{case_id}/'+('bad' if bad else 'good'))
        value=json.loads(row['response']['choices'][0]['message']['content'])
        body,_=candidate(case,bad=bad)
        result=assess(state,body,deepcopy(value))
        assert result['publish_allowed'] is not bad,result
