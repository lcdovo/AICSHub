"""按预算选择互补证据；邻块保留独立ID/偏移，不伪造合并后的引用身份。"""
from __future__ import annotations

import copy
from .retrieval_text import retrieval_text

ASSEMBLY_VERSION = "bounded-structural-evidence-v1"


def assemble_evidence(candidates: list[dict], chunks: list[dict], *, top_k: int,
                      max_chars: int, neighbors: int) -> tuple[list[dict], dict]:
    selected: list[dict] = []
    selected_ids: set[str] = set()
    signatures: set[tuple] = set()
    used = 0
    duplicates, over_budget, expanded = 0, 0, 0
    groups: dict[tuple, list[dict]] = {}
    for item in chunks:
        unit = (item.get("source_context") or {}).get("unit_id")
        if unit and item.get("version_id"):
            groups.setdefault((item.get("document_id"), item["version_id"], unit), []).append(item)
    for group in groups.values():
        group.sort(key=lambda item: item["start_offset"])

    def append(item: dict, origin: str) -> bool:
        nonlocal used, duplicates, over_budget, expanded
        if item["id"] in selected_ids or len(selected) >= top_k:
            return False
        context = item.get("source_context") or {}
        signature = (item.get("document_id"), item.get("version_id"), item["content"],
                     context.get("heading"), context.get("faq_question"), context.get("table_header"))
        # 不对前缀相似或不同来源的片段去重，保留否定和例外差异。
        if signature in signatures:
            duplicates += 1
            return False
        cost = len(retrieval_text(item))
        if used + cost > max_chars:
            over_budget += 1
            return False
        clean = copy.deepcopy({key: value for key, value in item.items() if not key.startswith("_")})
        clean["evidence_text"] = retrieval_text(item)
        clean["evidence_origin"] = origin
        selected.append(clean)
        selected_ids.add(item["id"])
        signatures.add(signature)
        used += cost
        expanded += int(origin == "structural_neighbor")
        return True

    for seed in candidates:
        if not append(seed, "ranked"):
            continue
        unit = (seed.get("source_context") or {}).get("unit_id")
        group = groups.get((seed.get("document_id"), seed.get("version_id"), unit), [])
        if not neighbors or len(group) < 2:
            continue
        index = next((i for i, item in enumerate(group) if item["id"] == seed["id"]), None)
        if index is None:
            continue
        # 先补紧邻下文（条款例外/后续步骤），再补上文；不跨FAQ或标题单元。
        indices = [i for distance in range(1, neighbors + 1) for i in (index + distance, index - distance)
                   if 0 <= i < len(group)]
        count = 0
        for i in indices:
            if count >= neighbors:
                break
            count += int(append(group[i], "structural_neighbor"))
    return selected, {"version": ASSEMBLY_VERSION, "text_chars": used,
                      "max_chars": max_chars, "neighbor_count": expanded,
                      "duplicates_removed": duplicates, "over_budget_count": over_budget}
