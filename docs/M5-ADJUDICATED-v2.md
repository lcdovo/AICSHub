# M5助手最终裁决与v2合成评测包

2026-11-05。用户明确要求“全部都由你判定”。本轮由助手直接完成所有材料判定、48条问题修订和独立v2包生成，不再逐题或逐组请求用户确认。原v1及四组24条人工批准历史保持原样；新包统一标记assistant_reviewed，不把委托判定记作新增人工金标。

## 已完成

- v1原595题全部已判定：24条历史人工裁决保留，另外571条助手复核及48条修订依据保留。
- 48项问题全部在v2落地，原ID不删除，逐项old/new hash及resolved_review_items见包manifest。
- v2共有616题：351可回答、60无答案/部分答案/业务边界、100审查反例、105正确候选。原21条有依据可回答题移入answerable后，另新增21条真正缺型号参数、外部状态或测试资料的题，原300/60/100/100数量门槛未降低。
- 55份合成文档保持独立于业务知识库；标签机文档只在v2改为051-v2，明确仅保证一维条码打印、不保证二维码能力，避免12毫米介质与15毫米见方二维码的隐含冲突。其余54份原文不变。

包目录：`acceptance/m5/adjudicated-v2/`，dataset.json、corpus.json、manifest.json。`scripts/build_m5_adjudicated_v2.py`按原v1及助手记录可重建内容，默认拒绝覆盖已有目录。原四份材料hash及新包hash已记录；构建时有意创建新源码输入，不谎称构建期间无变更，后续测试及基线才验证稳定快照。

## 明确的裁决

1. 21条有明确条款的否定/风险问题标为可回答，支持度描述实际正确回答，而不是用户命题为真；查询命中证据不允许自动触发业务动作。
2. 原partial题补chunk/version、已知条件及具体待补信息；正确有限回答/补问被审查接受应得分，缺条件或未补问不能得分。
3. 两条无授权访问题按refuse_action且执行器观察业务动作数0计分；模拟退款题补明确合成pending_review/is_simulated回执，实际到账绝不能由此推定。
4. 安全建议/受控补问单列response_behavior维度，不再冒充产品事实支持率。
5. 4条子区间反例改为明确越界能力保证；显示器240Hz未知题保留未知但明确分辨率/接口条件未提供；窗帘题与承重保护证据对齐。

## 评分器变化

`scripts/evaluate_m5.py`兼容v1与v2。v2新增acceptable_behaviors、expected_answer_accepted、evaluation_axis、missing_information、requires_no_business_action；观察中独立记录response_behavior、missing_information_requested、business_actions_executed。

“回答被接受”与“执行业务动作”分开：正确拒答、补问、否定回答可以被审查接受；缺少执行器0动作观察或实际发生动作都不能用refuse_action文本掩盖。可回答题的必要条件覆盖同样受声明的行为和0动作约束。v1历史拒绝计分口径可读，不覆盖历史报告。

assistant_reviewed被显式支持，但不会获得independent_holdout_review_missing或independent_output_claim_review_missing的豁免，也不会自动取得claim_support。助手判定与独立人工/真实模型验收分别记录。

## 验证与实际基线

142项相关T0通过、0失败/错误/跳过、4.08秒、39条既有弃用警告，报告`reports/offline/20261105-012001-897749`；新增v2专项17项。覆盖正确拒答被接受、错误事实伪装、权限拒绝后的0/1/缺失动作、partial缺条件/缺补问/错误支持度、v1兼容、助手标签不能冒充人工、48项全部落地、旧ID保留、原24条不变、616题数量与包hash/重建一致。compileall app scripts、切片Ruff F401/F821和git diff --check通过；本片仅评测与合成材料，无生产行为变化，未重跑全项目。

实际词法基线报告`reports/m5/adjudicated-v2-offline-20261105-012047/report.json`：在55份文档上评估351条可回答题，Recall@20=1.0、MRR=0.984330、nDCG@5=0.988366，首位341/351。没有为提高分数剔除10条非首位样本，也不把召回高分当生成/Judge质量。

T0与基线源码指纹同为`89b7d8b04c40678a8762c17c3c60dd5265f143d935f22bfa5edd9fb4ab7cb7cd`，起止稳定完整，T0 blocked_network_operations=0。基线校验原材料未变、包hash、开发集ID/显式簇/问题字符串不交叉；只是词法组件检查，不证明一般语义独立性、完整检索/HTTP/REST/WS或真实模型质量。

## 当前边界

616题均为助手判定的合成评测资料，仍不是独立真实业务金标。没有执行新真实生成/Judge评测，没有业务动作或付费供应商调用，原660/660额度保持。性能按用户指令暂缓且未通过；M4实体95.67%质量失败及rules/灰度0不变。M5整体未完成，不因材料裁决完成而跳成部署/产品验收通过。
