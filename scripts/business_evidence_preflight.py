"""Require content-bound scope review before spending on positive refund scenarios.

This is evaluation material admission, not an application eligibility engine.
Scopes are explicit reviewed declarations; titles/keywords cannot prove eligibility.
"""
from __future__ import annotations
import hashlib

REFUND_SCOPES = {'refund_applicability', 'quality_definition', 'quality_window', 'manual_review'}


def audit_refund_sources(cases, sources, review):
    catalog={item['id']:item for item in sources['items']}
    declared={item['id']:item for item in review['items']}
    findings=[]
    for case in cases:
        if not any('waiting_confirmation' in turn['expected_task_states'] for turn in case['turns']):
            continue
        scopes=set()
        for source_id in case['source_ids']:
            item=catalog[source_id];entry=declared.get(source_id)
            actual=hashlib.sha256(item['content'].encode()).hexdigest()
            if actual!=item['sha256']:raise ValueError('source_content_changed')
            if entry is None or entry['sha256']!=actual:raise ValueError('source_scope_review_missing_or_stale')
            scopes.update(entry['scopes'])
        missing=sorted(REFUND_SCOPES-scopes)
        findings.append({'case_id':case['case_id'],'passed':not missing,'missing_scopes':missing,
                         'scope':'material_adequacy_only_not_provider_quality_or_refund_approval'})
    return findings


def require_refund_sources(cases, sources, review):
    findings=audit_refund_sources(cases,sources,review)
    if any(not row['passed'] for row in findings):raise ValueError('refund_acceptance_evidence_incomplete')
    return findings
