"""Judge chooses current sentence/source identities; the server owns all text."""
from copy import deepcopy
import re
from typing import Literal,Union,get_args

from pydantic import Field,create_model,field_validator

from app.agent.judge_contracts import JudgeContractError,StrictJudgeModel,response_segments
from app.agent.item_judge_v2 import SentenceClaim,fragments,wire_contract

VERSION='server-selectors.v1'
PROMPT="""你是独立的客服证据审查员，不是生成器。只输出严格schema JSON。
服务端已固定s0/s1等句子槽位；每个槽位恰好审查一次，不抄写、改写或输出原文text、quote、fragment_id。
facts表示本句含业务事实：support必须判断整句全部论断，evidence只选择该项允许的当前K/T/P/E/L/S/U短编号。正确条款后附加无依据对象、条件、办理材料、后果或能力，整句均不能supported。存在引用不等于语义支持。
纯补问、资料不足、权限拒绝、保守建议或寒暄可选相应非事实kind；具体产品能力、政策、办理条件和效果不能藏在general_advice里。“建议”“以免”不能免除业务事实核验。
保留否定、适用对象、必要条件、例外、时间、数值和单位；条件政策不等于用户已满足条件，不得推广适用范围。证据有明确答案却拒答或补问应invalid。
item_checks逐项完整覆盖，answer_segment_id只选择本项服务端句子编号；缺段时只能null。语义完成判断须审查整项全文，不能只看所选句子。
answered只能与answerable一致；limited表示缺资料/权限/参数，不能冒称完成。准确给出条件政策不等于办理完成，也不能因用户未问的情形而无谓拒答。
退款预览只证明awaiting_confirmation且未提交；权限、确认、支付和业务成功不得由政策或历史推定。unsupported只能limited，未执行不能说已完成。
排障步骤须有当前知识支持，等待反馈为waiting_feedback；只有本轮用户明确报告解决并有U1才可answered，不能把建议当作已解决。
用户报告只证明用户说了什么；历史和进度不是事实来源或新授权。句子中的每个事实、所有事项的状态和条件都须通过，才可整体valid。
"""


def strict_segment_id(value):
    # Some Pydantic versions match False to Literal[0] despite strict model
    # configuration. Reject JSON booleans before Literal can coerce them.
    if value is not None and type(value) is not int:
        raise ValueError('segment_id_must_be_integer_or_null')
    return value


class NonfactSelector(StrictJudgeModel):
    kind:Literal['clarification','insufficient_notice','authorization_refusal','general_advice','social']
    support:Literal['unknown']='unknown'
    evidence:list[str]=Field(default_factory=list,max_length=0)
    issues:list[str]=Field(default_factory=list,max_length=0)


def layout(context,response):
    segments=response_segments(response)
    sections=list(re.finditer(r'【(i[1-4])】(.*?)(?=【i[1-4]】|\Z)',response,re.S))
    owners={};cursor=0
    for segment in segments:
        if len(segment['text'])>1200:raise JudgeContractError('candidate_segment_budget_exceeded')
        start=response.find(segment['text'],cursor);end=start+len(segment['text']);cursor=end
        matches=[s.group(1) for s in sections if start>=s.start() and end<=s.end()]
        owners[segment['segment_id']]=matches[0] if len(matches)==1 else None
    return segments,owners


def selector_contract(context,response):
    segments,owners=layout(context,response)
    allowed={item['item_id']:item['evidence_ids'] for item in context['items']}
    slots={};fact_types={}
    for segment in segments:
        index=segment['segment_id'];refs=allowed.get(owners[index],[])
        reference=Literal[tuple(refs)] if refs else Literal['__no_source__']
        owner=owners[index]
        if owner not in fact_types:
            fact_types[owner]=create_model('_WholeSentence_'+str(owner or 'outside'),__base__=StrictJudgeModel,
                kind=(Literal['facts'],...),support=(Literal['supported','unsupported','unknown'],...),
                evidence=(list[reference],Field(max_length=min(6,len(refs)))),
                issues=(SentenceClaim.model_fields['issues'].annotation,deepcopy(SentenceClaim.model_fields['issues'])))
        facts=fact_types[owner]
        slots[f's{index}']=(Union[facts,NonfactSelector],...)
    slot_model=create_model('_SelectorSlots',__base__=StrictJudgeModel,**slots)
    base=wire_contract(context);annotation=base.model_fields['item_checks'].annotation
    entry=get_args(annotation)[0];variants=get_args(entry) or (entry,)
    checks=[]
    for variant in variants:
        item_id=get_args(variant.model_fields['item_id'].annotation)[0]
        choices=[index for index,owner in owners.items() if owner==item_id]
        selector=Literal[tuple(choices)] if choices else Literal[None]
        checks.append(create_model('_SelectedItem_'+item_id,__base__=StrictJudgeModel,
            __validators__={'strict_segment_id':field_validator('answer_segment_id',mode='before')(strict_segment_id)},
            **{name:(field.annotation,deepcopy(field)) for name,field in variant.model_fields.items() if name!='answer_quote'},
            answer_segment_id=(selector,...)))
    check=Union[tuple(checks)] if len(checks)>1 else checks[0]
    return create_model('ItemJudgeVerdictV2',__base__=StrictJudgeModel,
        **{name:(field.annotation,deepcopy(field)) for name,field in base.model_fields.items()
           if name not in {'claims','nonfact_segments','source_contract_version','item_checks'}},
        source_contract_version=(Literal[VERSION],...),segments=(slot_model,...),
        item_checks=(list[check],Field(min_length=len(checks),max_length=len(checks))))


def materialize(value,response,context,tools,knowledge):
    """Deterministic schema decoding, never repair supplied text or references."""
    payload=selector_contract(context,response).model_validate(value).model_dump()
    segments,_=layout(context,response)
    by_short={row['short_id']:row['fragment_id'] for row in fragments(tools,context,knowledge)}
    normalized={**payload,'source_contract_version':'server-sentences.v1','segments':{}}
    for segment in segments:
        key=f's{segment["segment_id"]}';slot=payload['segments'][key]
        normalized['segments'][key]=({'kind':'facts','claims':[{'text':segment['text'],'support':slot['support'],
            'evidence':[{'fragment_id':by_short[short]} for short in slot['evidence']], 'issues':slot['issues']}]} if slot['kind']=='facts' else {'kind':slot['kind']})
    by_id={s['segment_id']:s['text'] for s in segments}
    normalized['item_checks']=[]
    for check in payload['item_checks']:
        row=dict(check);index=row.pop('answer_segment_id')
        row['answer_quote']=by_id[index][:180] if index is not None else ''
        normalized['item_checks'].append(row)
    return normalized


def fixture_selectors(value,response,context,tools,knowledge):
    """Offline SDK fixture adapter only; no live provider verdict uses it."""
    from app.agent.item_judge_v2 import sentence_contract
    old=sentence_contract(context,response).model_validate(value).model_dump()
    result={**old,'source_contract_version':VERSION,'segments':{},'item_checks':[]}
    short={row['fragment_id']:row['short_id'] for row in fragments(tools,context,knowledge)}
    for key,slot in old['segments'].items():
        if slot['kind']!='facts':result['segments'][key]=slot;continue
        claims=slot['claims']
        result['segments'][key]={'kind':'facts','support':'unsupported' if any(c['support']=='unsupported' for c in claims) else
            'unknown' if any(c['support']=='unknown' for c in claims) else 'supported',
            'evidence':list(dict.fromkeys(short[r['fragment_id']] for c in claims for r in c['evidence'])),
            'issues':list(dict.fromkeys(issue for c in claims for issue in c['issues']))}
    segments,owners=layout(context,response)
    for check in old['item_checks']:
        row=dict(check);row.pop('answer_quote')
        row['answer_segment_id']=next((s['segment_id'] for s in segments if owners[s['segment_id']]==row['item_id']),None)
        result['item_checks'].append(row)
    return selector_contract(context,response).model_validate(result).model_dump()
