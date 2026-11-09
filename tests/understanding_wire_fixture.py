"""Explicitly author new-schema fixtures; never used by production decoding."""
from copy import deepcopy


def item_wire(value):
    result=deepcopy(value)
    result.pop('read_tasks',None)
    result.pop('task_kind',None)
    return result
