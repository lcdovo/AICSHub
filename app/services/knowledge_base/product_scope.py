"""Explicit device-operation queries require sources mentioning that device.

This conservative lexical admission is not semantic relevance calibration and
does not assert that an empty result proves the knowledge base has no answer.
"""
import re

VERSION='explicit-device-operation.v1'
DEVICES=('打印机','耳机','键盘','显示器','手表','路由器')


def operation_device(query: str) -> str | None:
    devices=[name for name in DEVICES if name in query]
    if len(devices)!=1 or not re.search(r'卡纸|配对|连不上|无法连接|开不了机|重置|校准|换轴|故障|排查|排障',query):
        return None
    return devices[0]


def scope_candidates(query: str,candidates: list[dict]) -> tuple[list[dict],dict]:
    device=operation_device(query)
    if not device:return candidates,{'version':VERSION,'applied':False}
    selected=[item for item in candidates if device in str(item.get('title',''))+'\n'+str(item.get('content',''))]
    return selected,{'version':VERSION,'applied':True,'device':device,
                     'input_count':len(candidates),'retained_count':len(selected),
                     'reason':'device_not_in_retrieved_sources' if not selected else 'explicit_device_matched'}
