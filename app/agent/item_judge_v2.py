"""Connect v2 claim proofs to current intent items without creating execution evidence."""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Literal, Union, get_args
from pydantic import BaseModel, Field, create_model

from app.agent.judge_contracts import (
    JudgeContractError, JudgeVerdictV2, assess_v2, evidence_catalog,
    judge_input_fingerprint, response_segments, StrictJudgeModel, ClaimCheck,
)

VERSION = "item-judge.v2-shared-schema-forty-segments.14"
PROMPT = """
另输出item_checks，每个需求i编号恰好一次，段落编号和来源必须一致。
v2逐句claims负责判断证据是否支持论断，item_checks负责本轮每个诉求的实际阶段；两者都必须通过。
用户报告和退款预览以追加的tool来源提供：报告只证明用户说了什么，预览只证明awaiting_confirmation且未提交；不能据此声称支付、提交或故障排除。
每个事实句的claim引用只能来自对应事项允许的来源；不能把别的事项正确答案算作本项完成。
政策陈述保留“质量问题”等适用条件时，只说明该条件下的规则，不等于断言当前用户已符合条件；具体资格认定需当前证据。
按最小可独立判断的事实拆分claims。复述正确政策后附加的新要求、对象或后果必须单独核验，不能用前半句的来源替整句背书；只要其中一个论断无依据，整项supported=false且回复invalid。
“建议”“以免”等措辞不免除事实核验：涉及办理所需材料、退货附件、受理条件或处理后果时仍是业务事实。来源只要求保留原包装，不能支持必须寄回配件或不寄会影响办理；不能归为general_advice绕过核验。纯联系人工的建议与具体政策保证分开判断。
answerability按本项实际问题及证据的适用范围判断。准确说明有条件的政策不等于确认用户满足条件；不得擅自扩展成所有情形的规则，也不能仅因未覆盖用户未问的另一情形就判partial。若本项仍有必要子问题未获回答，标limited并保留partial；answered只允许answerable，不能同时输出answered和partial。
本契约的claims.evidence只填服务器提供的fragment_id，不输出quote/source_id/version_id，不自行抄写、拼接或截断证据。服务器从片段ID还原完整原文及版本并严格核验；选择ID不等于该来源支持论断，仍须判断语义及所有条件。
item_checks不输出evidence_ids；服务器按每个claim所属i段落推导逐项来源。仍须独立判断各项status、supported、conditions_preserved和answerability，不得把可回答性等同操作已完成。
本次输出segments固定句子槽位s0/s1等，每个槽位只能选一种kind：facts时填本句全部claims；clarification/insufficient_notice/authorization_refusal/general_advice/social时不填claims。禁止输出顶层claims或nonfact_segments，不重复、遗漏或新增句子槽位。字段名对应服务器给出的segment_id，不自行编号。把已知订单、政策、功能或诊断事实藏为非事实属于错误判决。
"""


class FragmentReference(StrictJudgeModel):
    fragment_id: str = Field(pattern=r"^r[a-p]{64}$")


class FragmentClaim(ClaimCheck):
    evidence: list[FragmentReference] = Field(max_length=6)


class SentenceClaim(StrictJudgeModel):
    text: str = Field(min_length=1,max_length=1200)
    support: Literal['supported','unsupported','unknown']
    evidence: list[FragmentReference] = Field(max_length=6)
    issues: list[Literal['negation_changed','scope_expanded','condition_missing','exception_missing',
        'unsupported_detail','contradiction','unanswered_known_question','unsafe_behavior',
        'unnecessary_clarification']] = Field(max_length=8)


class FactSentence(StrictJudgeModel):
    kind: Literal['facts']
    claims: list[SentenceClaim] = Field(min_length=1,max_length=20)


class NonfactSentence(StrictJudgeModel):
    kind: Literal['clarification','insufficient_notice','authorization_refusal','general_advice','social']


def sentence_contract(context: dict,response: str) -> type[BaseModel]:
    """Each server-numbered sentence has exactly one fact/nonfact choice."""
    choice=Union[FactSentence,NonfactSentence]
    slots=create_model('_SentenceSlots',__base__=StrictJudgeModel,
        **{f's{entry["segment_id"]}':(choice,...) for entry in response_segments(response)})
    fields=wire_contract(context).model_fields
    return create_model('ItemJudgeVerdictV2',__base__=StrictJudgeModel,
        **{name:(field.annotation,deepcopy(field)) for name,field in fields.items()
           if name not in {'claims','nonfact_segments','source_contract_version'}},
        source_contract_version=(Literal['server-sentences.v1'],...),segments=(slots,...))


def to_sentence_verdict(value: dict) -> dict:
    """Offline fixture adapter; conflicting historical verdicts are never repaired."""
    result=deepcopy(value)
    claims=result.pop('claims');nonfact=result.pop('nonfact_segments')
    fact_ids={claim['segment_id'] for claim in claims}
    other_ids=[entry['segment_id'] for entry in nonfact]
    if fact_ids & set(other_ids) or len(other_ids)!=len(set(other_ids)):
        raise JudgeContractError('candidate_segments_not_exactly_reviewed')
    slots={f's{index}':{'kind':'facts','claims':[{key:item for key,item in claim.items() if key!='segment_id'}
        for claim in claims if claim['segment_id']==index]} for index in fact_ids}
    slots.update({f's{entry["segment_id"]}':{'kind':entry['kind']} for entry in nonfact})
    result.update(source_contract_version='server-sentences.v1',segments=slots)
    return result


def wire_contract(context: dict) -> type[BaseModel]:
    annotation=contract(context).model_fields["item_checks"].annotation
    entry=get_args(annotation)[0]
    variants=get_args(entry) or (entry,)
    checks=[create_model("_DerivedItemCheck_"+str(index),__config__=variant.model_config,
        **{name:(field.annotation,deepcopy(field)) for name,field in variant.model_fields.items()
           if name!="evidence_ids"}) for index,variant in enumerate(variants)]
    check=Union[tuple(checks)] if len(checks)>1 else checks[0]
    return create_model("ItemJudgeVerdictV2",__base__=JudgeVerdictV2,
        source_contract_version=(Literal["server-fragments.v2"],...),
        claims=(list[FragmentClaim],Field(max_length=40)),
        item_checks=(list[check],Field(min_length=len(checks),max_length=len(checks))))


def fragments(tools: str, context: dict, knowledge: list[dict]) -> list[dict]:
    augmented, identities=review_sources(tools,context,knowledge)
    catalog=evidence_catalog(knowledge,augmented)
    result=[]
    for short,key in identities.items():
        body=catalog[key]
        identity=[context_fingerprint(context),*key,body]
        # Bijective base16 alphabet without digits: a digest can contain an
        # eleven-digit phone-looking run, which the privacy layer must redact.
        # Preserve all 256 bits without exempting user text from redaction.
        digest=hashlib.sha256(json.dumps(identity,ensure_ascii=False).encode()).hexdigest()
        fragment_id="r"+digest.translate(str.maketrans('0123456789abcdef','abcdefghijklmnop'))
        result.append({"fragment_id":fragment_id,"short_id":short,"kind":key[0],
            "source_id":key[1],"version_id":key[2],"content":body,
            "allowed_item_ids":[item["item_id"] for item in context["items"] if short in item["evidence_ids"]]})
    return result


def wire_fragments(tools: str, context: dict, knowledge: list[dict]) -> list[dict]:
    """The provider selects opaque IDs; the server retains full source identity.

    Full content and item ownership remain visible. Repeated identity/version
    fields are recovered from the immutable catalog instead of copied by a model.
    """
    return [{key:row[key] for key in ('fragment_id','short_id','kind','content','allowed_item_ids')}
            for row in fragments(tools,context,knowledge)]


def to_fragment_verdict(value: dict, tools: str, context: dict, knowledge: list[dict]) -> dict:
    """Offline fixture conversion only; never repair a supplier response."""
    from copy import deepcopy
    result=deepcopy(value)
    available=fragments(tools,context,knowledge)
    for claim in result["claims"]:
        claim["evidence"]=[{"fragment_id":next(fragment["fragment_id"] for fragment in available
            if (fragment["kind"],fragment["source_id"],fragment["version_id"])==
               (ref["kind"],ref["source_id"],ref["version_id"]))} for ref in claim["evidence"]]
    for check in result["item_checks"]: check.pop("evidence_ids",None)
    result["source_contract_version"]="server-fragments.v2"
    return result


def context_fingerprint(context: dict) -> str:
    return hashlib.sha256(json.dumps(context, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def contract(context: dict) -> type[BaseModel]:
    from app.agent.validation_llm import item_judge_contract
    field = item_judge_contract(context).model_fields["item_checks"]
    return create_model("ItemJudgeVerdictV2", __base__=JudgeVerdictV2,
        item_checks=(field.annotation, Field(min_length=len(context["items"]), max_length=len(context["items"]))))


def review_sources(tools: str, context: dict, knowledge: list[dict]) -> tuple[str, dict]:
    """Map short per-item IDs to v2 immutable source identities; no new business calls."""
    if context.get("scope_valid") is False or not context.get("items"):
        raise JudgeContractError("item_scope_invalid")
    rows = [line for line in tools.splitlines() if line.strip()]
    scope_rows=[]
    for entry in context["catalog"].values():
        if entry.get('review_failure'):
            rows.append(json.dumps(entry['review_failure'],ensure_ascii=False,sort_keys=True))
        elif entry['kind']=='capability_limit':
            rows.append(json.dumps({'source_kind':'current_flow_operation_limit',**entry['operation_limit']},ensure_ascii=False,sort_keys=True))
        elif entry['kind']=='current_read_only_execution':
            rows.append(json.dumps({'source_kind':'current_read_only_execution',**entry['read_only_execution']},ensure_ascii=False,sort_keys=True))
        elif entry['kind']=='current_information_limit':
            rows.append(json.dumps({'source_kind':'current_information_limit',**entry['information_limit']},ensure_ascii=False,sort_keys=True))
        elif entry['kind']=='current_evidence_scope':
            scope_rows.append(json.dumps({'source_kind':'current_evidence_scope',**entry['evidence_scope']},ensure_ascii=False,sort_keys=True))
        elif entry["kind"] == "prepare_refund":
            rows.append(json.dumps({"source_kind":"current_refund_preview", "preview":entry["preview"],
                "grants_authorization":False}, ensure_ascii=False, sort_keys=True))
        elif entry["kind"] == "current_user_report":
            rows.append(json.dumps({"source_kind":"current_user_report", "report":entry["user_report"],
                "independently_verified":False}, ensure_ascii=False, sort_keys=True))
    # Append new attestation sources after old tool/preview/report rows so
    # legacy source identities remain stable and captured proofs stay auditable.
    augmented = "\n".join([*rows,*scope_rows])
    catalog = evidence_catalog(knowledge, augmented)
    identities = {}
    for short, entry in context["catalog"].items():
        if entry["kind"] == "knowledge":
            key = ("knowledge", entry["source_id"], entry["version_id"])
            if key not in catalog:
                raise JudgeContractError("item_knowledge_source_missing")
            identities[short] = key
            continue
        expected = ({'source_kind':'current_evidence_scope',**entry['evidence_scope']} if entry['kind']=='current_evidence_scope' else
            {'source_kind':'current_information_limit',**entry['information_limit']} if entry['kind']=='current_information_limit' else
            {'source_kind':'current_read_only_execution',**entry['read_only_execution']} if entry['kind']=='current_read_only_execution' else
            {'source_kind':'current_flow_operation_limit',**entry['operation_limit']} if entry['kind']=='capability_limit' else
            entry.get('review_failure') or entry.get("review_tool") if entry["kind"] in {"query_order","get_user_history"} else
            {"source_kind":"current_refund_preview", "preview":entry["preview"],"grants_authorization":False}
            if entry["kind"] == "prepare_refund" else
            {"source_kind":"current_user_report", "report":entry["user_report"],"independently_verified":False}
            if entry["kind"] == "current_user_report" else None)
        matches = [key for key, body in catalog.items() if key[0] == "tool" and json.loads(body) == expected]
        if len(matches) != 1:
            raise JudgeContractError("item_tool_source_missing_or_ambiguous")
        identities[short] = matches[0]
    return augmented, identities


def assess(value: dict, response: str, knowledge: list[dict], tools: str, query: str, context: dict) -> dict:
    original=value
    if value.get('source_contract_version')=='server-selectors.v1':
        from app.agent.item_judge_selectors import materialize
        result=assess(materialize(value,response,context,tools,knowledge),response,knowledge,tools,query,context)
        result['_v2_verdict']=original
        return result
    if value.get('source_contract_version')=='server-sentences.v1':
        payload=sentence_contract(context,response).model_validate(value).model_dump()
        slots=payload.pop('segments')
        payload.update(source_contract_version='server-fragments.v2',claims=[],nonfact_segments=[])
        for entry in response_segments(response):
            index=entry['segment_id'];slot=slots[f's{index}']
            if slot['kind']=='facts':
                payload['claims'].extend({'segment_id':index,**claim} for claim in slot['claims'])
            else: payload['nonfact_segments'].append({'segment_id':index,'kind':slot['kind']})
        result=assess(payload,response,knowledge,tools,query,context)
        result['_v2_verdict']=original
        return result
    derived="source_contract_version" in value
    if "source_contract_version" in value:
        parsed_wire=wire_contract(context).model_validate(value)
        value=parsed_wire.model_dump()
        value.pop("source_contract_version")
        for check in value["item_checks"]: check["evidence_ids"]=[]
        catalog={fragment["fragment_id"]:fragment for fragment in fragments(tools,context,knowledge)}
        for claim in value["claims"]:
            resolved=[]
            for reference in claim["evidence"]:
                fragment=catalog.get(reference["fragment_id"])
                if fragment is None: raise JudgeContractError("fragment_identity_or_context_invalid")
                resolved.append({"kind":fragment["kind"],"source_id":fragment["source_id"],
                    "version_id":fragment["version_id"],"quote":fragment["content"]})
            claim["evidence"]=resolved
    parsed = contract(context).model_validate(value)
    full = parsed.model_dump()
    item_checks = full.pop("item_checks")
    augmented, identities = review_sources(tools, context, knowledge)
    result = assess_v2(full, response, knowledge, augmented, query)
    sections = list(re.finditer(r"【(i[1-4])】(.*?)(?=【i[1-4]】|\Z)", response, re.S))
    segments = response_segments(response)
    owners = {}
    factual_segments={claim.segment_id for claim in parsed.claims}
    cursor = 0
    for segment in segments:
        start = response.find(segment["text"], cursor)
        if start < 0:
            raise JudgeContractError("item_segment_missing")
        end = start + len(segment["text"])
        cursor = end
        matches = [section.group(1) for section in sections if start >= section.start() and end <= section.end()]
        if len(matches) != 1:
            if not matches and segment["segment_id"] not in factual_segments:
                # v2 already checks exact claim/nonfact coverage. A nonfact greeting
                # outside item sections cannot supply evidence or complete an item.
                continue
            raise JudgeContractError("item_segment_outside_section")
        owners[segment["segment_id"]] = matches[0]
    by_item = {item["item_id"]:item for item in context["items"]}
    claims_by_item = {key:[] for key in by_item}
    for claim in parsed.claims:
        owner = owners[claim.segment_id]
        if owner not in by_item:
            raise JudgeContractError("item_claim_owner_invalid")
        allowed = {identities[short] for short in by_item[owner]["evidence_ids"]}
        actual = {(ref.kind,ref.source_id,ref.version_id) for ref in claim.evidence}
        if not actual.issubset(allowed):
            raise JudgeContractError("item_claim_foreign_source")
        claims_by_item[owner].append(claim)
    for check in item_checks:
        if derived:
            verified={(ref.kind,ref.source_id,ref.version_id)
                for claim in claims_by_item.get(check["item_id"],[]) for ref in claim.evidence}
            check["evidence_ids"]=[short for short,key in identities.items() if key in verified]
        if result["publish_allowed"] and check["status"] in {"answered","waiting_feedback","awaiting_confirmation"}:
            claims = claims_by_item.get(check["item_id"],[])
            if not claims or any(claim.support != "supported" or claim.issues for claim in claims):
                raise JudgeContractError("item_completion_without_supported_claims")
            verified = {(ref.kind,ref.source_id,ref.version_id) for claim in claims for ref in claim.evidence}
            selected = {identities[short] for short in check["evidence_ids"] if short in identities}
            if not selected or not selected.issubset(verified):
                raise JudgeContractError("item_completion_reference_not_reviewed")
    # Preserve only the original external inputs in the publication binding.
    result.update(item_checks=item_checks, item_context_fingerprint=context_fingerprint(context),
        input_fingerprint=judge_input_fingerprint(response, knowledge, tools, query),
        item_contract_version=VERSION, _v2_verdict=original)
    return result
