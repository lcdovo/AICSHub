# M4合成验收集

- development.jsonl：30条开发烟测；holdout.jsonl：最早300条商品/措辞组合矩阵；critical.jsonl：12条边界。矩阵不满足300条独立语义留出要求，不用于最终放行。
- independent.jsonl：300条不同业务信息需求，调用前固定标签。所有文本与标签由助手编写，**未经用户或独立人工标注者复核**。数据行annotation_note中的“人工编排”是不准确的原始描述，保留冻结原文件及SHA256供审计，不据此宣称人工金标。
- 当前独立集已经用于评测及失败分析，不能在依其错题调参后继续当作未见留出。新一轮完整验收应另行冻结独立语义集和标注规则。
- scripts/evaluate_m4_live.py需要显式split；运行会真实调用供应商。授权账本/排他锁在reports/m4-closeout/live，禁止删账本、复制为新预算或并行规避总额度。当前660/660次，累计预留输出328916/330000，禁止自动跑新批。
- tests/test_m4_evaluation.py离线检查计分，不发供应商请求。real_services.py仅由隔离真实依赖执行器运行，不读业务.env。
- 最终门槛未通过；见docs/M4-EVIDENCE-v1.md及M4-GATES-v1.json。不能从某个数值指标或接口契约PASS推导整个M4完成。

proposal-boundary补验只使用原授权最后2次：真实供应商生成不受信任工具/身份字段提议，生产ReadTask拒绝，不执行任何工具。unsafe-proposal-replay.json冻结合成输出和原报告SHA256，test_m4_proposal_probe.py再将它们放入生产RequestUnderstanding/完整图回放，失败澄清且不写入。不是注入prompt抵抗率、整体业务图真实调用或300条质量留出。额度已660/660，禁止自动重跑付费split或删除/迁移账本重置额度；后续仅可离线回放，新增付费批次需新授权。
