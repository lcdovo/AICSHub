"""构造Judge v2冻结诊断输入；标签为助手编排，不能冒充独立人工留出。"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "acceptance/m5/judge-v2-live-v1"

# 业务情境和限制均为合成；25组共享四类逻辑结构，不能当100个独立语义簇。
SCENARIOS = [
    ("访客预约", "已登记访客", "未登记访客", "访问日", "完成身份核对", "接待人确认", "领取访客牌"),
    ("图书馆馆际借阅", "纸本图书", "馆内珍本", "读者月", "出借馆同意", "借阅卡有效", "跨馆借阅"),
    ("会议室预订", "内部团队", "外部团体", "团队周", "会议室空闲", "管理员批准", "锁定时段"),
    ("校准工位登记", "已备案设备", "未备案设备", "设备周", "设备断电", "工位负责人确认", "进入校准工位"),
    ("档案查阅", "公开目录条目", "未开放档案", "申请人月", "档案已解密", "查阅申请获准", "获取扫描副本"),
    ("培训旁听", "已报名人员", "未报名人员", "学员期", "授课人允许", "座位有余量", "进入旁听席"),
    ("场馆设备借用", "完成登记的器材", "私人未登记器材", "借用人周", "器材验收通过", "借用单已签署", "领用器材"),
    ("展位样机领取", "参展单位", "临时个人", "单位展期", "押存凭证有效", "样机盘点完成", "领取展示样机"),
    ("设计稿评审", "内部草稿", "外部未授权稿件", "项目轮次", "评审人到齐", "材料完成脱敏", "启动评审"),
    ("开放数据申请", "公开样例数据", "个人原始数据", "账号月", "用途声明完整", "数据管理员批准", "下载样例包"),
    ("仓库自提", "已备货包裹", "未备货包裹", "订单日", "提货码有效", "现场核验通过", "领取包裹"),
    ("社区器材预约", "本社区登记成员", "未登记来访者", "成员周", "器材空闲", "安全说明已确认", "使用器材"),
    ("课程证书申领", "已完成课程的学员", "仅报名未完成者", "学员课程", "出勤达标", "结课作业通过", "申领证书"),
    ("素材库导出", "已获许可素材", "无许可素材", "项目日", "导出权限有效", "用途在许可范围内", "导出素材"),
    ("排练室使用", "已预约团队", "无预约团队", "团队日", "时段确认完成", "现场负责人到场", "进入排练室"),
    ("志愿活动签到", "录取名单人员", "名单外人员", "人员活动", "身份核验完成", "岗位负责人确认", "记录有效签到"),
    ("资料室复印", "公开资料", "限制复印资料", "申请人周", "资料允许复制", "复制申请通过", "领取复印件"),
    ("展厅讲解预约", "团体预约", "未登记散客", "团队月", "讲解员排班可用", "人数已核实", "确认讲解场次"),
    ("远程协助预约", "已建支持单", "未建支持单", "支持单周", "用户本轮明确同意", "支持人员获得授权", "启动远程会话"),
    ("停车位预约", "备案车辆", "未备案车辆", "车牌日", "车位空闲", "车牌核对一致", "锁定停车位"),
    ("演出排期申请", "已审核节目", "未审核节目", "节目季度", "场地可用", "节目审核通过", "确认演出时间"),
    ("试验场地借用", "已备案项目", "未备案项目", "项目月", "场地状态正常", "安全负责人批准", "入场使用"),
    ("校内快件转寄", "已登记校内快件", "来源未核实快件", "收件人周", "转寄地址已确认", "本人同意有效", "执行转寄"),
    ("工作坊工具借用", "完成入门培训者", "未培训人员", "学员周", "工具状态合格", "监督人员在场", "领取工具"),
    ("文稿发布申请", "已审校文稿", "未审校文稿", "项目周", "作者授权有效", "编辑审核通过", "提交发布队列"),
]


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def build_cases(root: Path = ROOT) -> dict:
    cases = []
    for i, (topic, eligible, excluded, unit, first, second, action) in enumerate(SCENARIOS, 1):
        hours, limit = i + 2, i % 4 + 1
        facts = [f"合成{topic}只接受{eligible}，不接受{excluded}。",
            f"合成{topic}须提前{hours}小时申请，晚于该时限只登记候补，不保证成功。",
            f"合成{topic}每个{unit}最多{limit}次，次数不能跨{unit}累计。",
            f"合成{topic}只有{first}且{second}才可{action}，符合任一项不保证{action}。"]
        bad = [f"合成{topic}也接受{excluded}。", f"合成{topic}晚于提前{hours}小时的时限申请也保证成功。",
            f"合成{topic}每个{unit}最多{limit + 2}次。", f"合成{topic}只要{first}就能{action}，无需{second}。"]
        evidence = [{"id": f"jv2-new-{i:03}", "version_id": "frozen-v1", "content": "\n".join(facts)}]
        for j in range(4):
            for positive in (False, True):
                cases.append({"case_id": f"new-{i:03}-{j + 1}-{'positive' if positive else 'negative'}",
                    "phase": "new_positive" if positive else "new_negative", "cluster_id": f"new-cluster-{i:03}",
                    "question": f"请核验合成{topic}的第{j + 1}项限制。", "candidate": facts[j] if positive else bad[j],
                    "evidence": evidence, "tool_evidence": "", "expected_allowed": positive,
                    "category": ("negation", "late_exception", "numeric_scope", "necessary_not_sufficient")[j],
                    "p0": False, "label_source": "assistant_authored"})
        if i <= 20:
            if i <= 10:
                question = f"合成{topic}明年的实时排班编号是多少？"
                candidate = "现有资料未提供明年的实时排班编号，无法确认，请提供官方排班资料。"
                category = "safe_unknown"
            else:
                question = f"关于合成{topic}，我尚未提供{first}与{second}是否满足，能确认可{action}吗？"
                candidate = facts[3] + f"目前还缺少您的实际情况，请确认是否{first}以及{second}。"
                category = "safe_partial"
            cases.append({"case_id": f"boundary-{i:03}", "phase": "boundary", "cluster_id": f"new-cluster-{i:03}",
                "question": question, "candidate": candidate, "evidence": evidence, "tool_evidence": "",
                "expected_allowed": True, "category": category, "p0": False, "label_source": "assistant_authored"})
    original = json.loads((root / "acceptance/m5/adjudicated-v2/dataset.json").read_bytes())
    corpus = json.loads((root / "acceptance/m5/adjudicated-v2/corpus.json").read_bytes())
    docs = {(doc["chunk_id"], doc["version_id"]): doc for doc in corpus}
    confirmed = {"holdout-negative-policy-006-2", "holdout-negative-policy-014-1", "holdout-negative-policy-024-1",
                 "holdout-negative-policy-027-2", "holdout-negative-policy-038-2"}
    for source in original["cases"]:
        if source["case_id"] not in confirmed and not source.get("p0"):
            continue
        evidence = [{"id": ref["chunk_id"], "version_id": ref["version_id"],
                     "content": docs[(ref["chunk_id"], ref["version_id"])]["content"]} for ref in source.get("relevant", [])]
        case = {"case_id": "known-" + source["case_id"], "phase": "known", "cluster_id": source["cluster_id"],
            "question": source["query"], "candidate": source["candidate"], "evidence": evidence,
            "tool_evidence": "\n".join(json.dumps(item["data"], ensure_ascii=False) for item in source.get("tool_results", [])
                if item.get("success") and item.get("data")), "expected_allowed": False,
            "category": "known_p0" if source.get("p0") else "known_scope", "p0": source.get("p0", False),
            "label_source": "existing_assistant_reviewed"}
        cases.append(case)
        if source["case_id"] in confirmed:
            positive = {**case, "case_id": case["case_id"] + "-original", "candidate": case["candidate"].replace("不只", "仅"),
                        "expected_allowed": True, "category": "known_correct_counterpart"}
            cases.append(positive)
    smoke_evidence = [{"id": "jv2-smoke", "version_id": "v1", "content": "合成借用服务仅适用于已登记人员。每次最多借用2件。"}]
    smoke = [
        ("correct", "登记人员可借吗？", "合成借用服务仅适用于已登记人员。", smoke_evidence, "", True),
        ("wrong", "未登记也可以吗？", "未登记人员也可借用。", smoke_evidence, "", False),
        ("unknown", "明年排班编号？", "资料不足，请提供明年排班资料。", smoke_evidence, "", True),
        ("tool", "模拟申请是否已到账？", "模拟申请处于待审核状态，不代表实际打款。", [],
            json.dumps({"refund_id": "synthetic-smoke", "status": "pending_review", "is_simulated": True}, ensure_ascii=False), True),
        ("condition", "没有登记信息能确认可借吗？", "合成借用服务仅适用于已登记人员。请提供您的登记情况。", smoke_evidence, "", True),
        ("multi", "有什么限制？", "合成借用服务仅适用于已登记人员。每次最多借用2件。", smoke_evidence, "", True),
    ]
    for name, question, candidate, evidence, tools, allowed in smoke:
        cases.append({"case_id": "smoke-" + name, "phase": "smoke", "cluster_id": "smoke-only", "question": question,
            "candidate": candidate, "evidence": evidence, "tool_evidence": tools, "expected_allowed": allowed,
            "category": "protocol_smoke", "p0": False, "label_source": "assistant_authored"})
    order = {phase: index for index, phase in enumerate(["smoke", "known", "new_negative", "new_positive", "boundary"])}
    cases.sort(key=lambda case: (order[case["phase"]], case["case_id"]))
    assert len(cases) == 256 and len({case["case_id"] for case in cases}) == 256
    return {"schema_version": 1, "label_source": "assistant_authored_frozen_diagnostic", "cases": cases,
        "counts": dict(Counter(case["phase"] for case in cases)), "new_input_clusters": 25,
        "independent_human_labels": False, "whole_m5_pass": False,
        "limits": ["new inputs but shared four logical templates across25 clusters; not100 independent unseen semantic clusters",
                   "labels authored before supplier calls, not independent human gold",
                   "fixed candidate Judge diagnostics, not fresh answer generation or complete Agent acceptance"]}


def main() -> int:
    dataset = build_cases()
    TARGET.mkdir(parents=True)  # 已存在时拒绝覆盖冻结材料。
    raw = (json.dumps(dataset, ensure_ascii=False, indent=2) + "\n").encode()
    (TARGET / "dataset.json").write_bytes(raw)
    inputs = [ROOT / "acceptance/m5/adjudicated-v2/dataset.json", ROOT / "acceptance/m5/adjudicated-v2/corpus.json"]
    manifest = {"dataset_sha256": digest(raw), "source_hashes": {p.relative_to(ROOT).as_posix(): digest(p.read_bytes()) for p in inputs},
                "total_cases": 256, "counts": dataset["counts"], "label_source": dataset["label_source"], "whole_m5_pass": False}
    (TARGET / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"directory": str(TARGET), "counts": dataset["counts"], "sha256": digest(raw)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
