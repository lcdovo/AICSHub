# 验收材料导航

这里保存场景、固定输入、预期结果和阶段验收适配器。材料存在不代表验收已通过；assistant编写的标签不能视为独立人工金标，替身演练不能代替真实模型语义验证。

历史场景、留出标签、计划版本及其hash应保留。修订需新版本和明确差异，不覆盖旧失败记录，也不通过改路径重置预算。真实调用仍以明确授权及对应派发账本为准。

## 分类目录

- [agent-task-loop/](agent-task-loop/)：任务循环场景。
- [business-v1/](business-v1/)：业务验收第一版。
- [business-v2/](business-v2/)：业务验收第二版。
- [m1/](m1/)：第一阶段验收。
- [m2/](m2/)：Run与并发执行验收。
- [m3/](m3/)：知识生命周期验收。
- [m4/](m4/)：请求理解验收。
- [m5/](m5/)：回答质量与Judge材料。
- [model-first/](model-first/)：模型优先理解场景。
- [multi-intent/](multi-intent/)：多意图逐项场景。
- [optimization-gates-v1/](optimization-gates-v1/)：优化门槛材料。
- [refund-language-v1/](refund-language-v1/)：退款语义场景。

开发与验收说明见[文档导航](../docs/README.md)，执行入口见[脚本导航](../scripts/README.md)。
