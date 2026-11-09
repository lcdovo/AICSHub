"""Outcome-separated latency reporting; reply keywords never prove acceptance."""
from __future__ import annotations
from collections import Counter
import math


def outcome(row: dict) -> str:
    if row.get('http_status') != 200 or row.get('error_type'):
        return 'transport_error'
    # These are whole server notices, not arbitrary words inside policy text.
    text=row.get('reply','')
    if text.startswith('本轮请求理解暂时不可用'):return 'understanding_unavailable'
    if text.startswith('本轮回答生成暂时不可用'):return 'generation_unavailable'
    if text.startswith('本轮回答审查暂时不可用'):return 'review_unknown'
    if text.startswith('本轮回复未通过事实或安全核验'):return 'review_rejected'
    review=row.get('review') or {}
    if review.get('passed') is False:return 'review_unknown' if review.get('status')=='unknown' else 'review_rejected'
    # Historical heuristic review_accepted is deliberately not authoritative.
    if review.get('passed') is True:
        if row.get('answerability') in {'unknown','unanswerable'} or row.get('response_behavior')=='refuse_insufficient':
            return 'accepted_insufficient'
        if row.get('waiting_for')=='human_request':return 'handoff_suggested'
        if row.get('semantic_review_passed') is True:return 'business_verified'
        return 'review_accepted_not_semantically_verified'
    return 'unclassified'


def percentile(values, q=.95):
    values=sorted(values)
    return round(values[math.ceil(len(values)*q)-1],2) if values else None


def summarize(rows):
    result={}
    for kind in dict.fromkeys(r['scenario'] for r in rows):
        group=[r for r in rows if r['scenario']==kind]
        categories=Counter(outcome(r) for r in group)
        verified=[r['elapsed_ms'] for r in group if outcome(r)=='business_verified']
        result[kind]={'n':len(group),'all_response_p95_ms':percentile([r['elapsed_ms'] for r in group]),
            'outcomes':dict(categories),'business_verified_n':len(verified),
            'business_verified_p95_ms':percentile(verified),
            'stable_tail_estimate':len(verified)>=100,
            'by_outcome':{key:{'n':categories[key],'p95_ms':percentile([r['elapsed_ms'] for r in group if outcome(r)==key])} for key in categories}}
    return result
