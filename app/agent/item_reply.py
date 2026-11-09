"""Server-owned per-item reply slots; providers write content, never section identity."""
from contextvars import ContextVar
from pydantic import BaseModel,ConfigDict,Field,create_model

ITEM_REPLY_IDS: ContextVar[tuple[str,...]]=ContextVar('item_reply_ids',default=())
VERSION='item-reply.fixed-slots.v2-clause-lines'


def reply_contract(base:type[BaseModel],ids:tuple[str,...])->type[BaseModel]:
    if ids!=tuple(f'i{index+1}' for index in range(len(ids))) or not 2<=len(ids)<=4:
        raise ValueError('invalid_reply_item_ids')
    slots=create_model('_ReplyItemSlots',__config__=ConfigDict(extra='forbid',strict=True),
        **{key:(str,Field(min_length=1,max_length=4000)) for key in ids})
    def schema(cls,core,handler):
        result=handler.resolve_ref_schema(handler(core))
        result['properties'].pop('reply',None)
        result['required']=[key for key in result.get('required',[]) if key!='reply']
        return result
    parent=type('_FixedReplyBase',(base,),{'__get_pydantic_json_schema__':classmethod(schema),
        'model_config':ConfigDict(extra='forbid',strict=True)})
    return create_model('LLMResponse',__base__=parent,item_replies=(slots,...))


def render_reply(value,ids:tuple[str,...])->str:
    import re
    rows=value.item_replies.model_dump()
    if tuple(rows)!=ids: raise ValueError('reply_items_changed')
    if any(not text.strip() or re.search(r'【i\d+】',text) for text in rows.values()):
        raise ValueError('reply_item_body_contains_foreign_marker')
    # Keep every clause and delimiter. Separate semicolon clauses visually so
    # the independent reviewer can classify facts and limitation notices in
    # separate server-owned slots without dropping either part of the answer.
    bodies={key:rows[key].strip().replace('；','；\n') for key in ids}
    return '\n\n'.join(f'【{key}】{bodies[key]}' for key in ids)
