# 新业务验收真实阶段v1（2026-11-08）

## 当前结论

本文件为首次9次阶段原记录；用户随后明确授权修复并续验，最新为16/168、BA-04两轮通过、BA-05/t1因退款理解读计划语义组合失败停止。请以BUSINESS-ACCEPTANCE-CONTINUATION-v1.md为最新状态。

用户明确确认新增168供应商HTTP上限。实际派发9/168，剩159；9条全部http_complete且usage已知。按固定方案首个非预期失败停止，BA-04/t1停止后未执行第二轮、BA-05至BA-20或最终6例。整体未通过，不扩大语义质量/生产容量结论。

前三例分别为REST订单+包装例外、WS条件运费咨询、REST缺订单号+独立包装咨询，均经实际路由、Agent图、隔离SQL与真实供应商理解/决策/Judge执行，最终答复由assistant另行逐条复核。BA-03为limited+answered、waiting_input，正确有限回答通过但不表示全部完成。BA-02重复输出完整运费段落，记录为非阻断表达冗余。

前三例最终正文来自服务端证据直出，不是自由生成模型的质量成绩；本阶段实际自由生成供应商调用仍0。知识为epoch25发布片段的冻结回放，Redis为显式替身；不外推真实检索容量、生产Redis并发或独立人工金标。SQL订单与会话均合成，真实业务库未写入。

## 失败与诊断

BA-04单一排障诉求走兼容单项分支，WS生成调用stream=true。评测补充执行器沿用了scripts/m5_live_budget.py中明确拒绝流式的旧派发边界，抛evaluation_stream_not_authorized，应用安全停止为response_unavailable，没有发布未审草稿。属于评测协议覆盖缺口，不作为模型语义错误率样本；也不能算排障业务成功。

真实第3次尝试没有进入provider dispatch ledger或真实传输。断网回放复用该场景已捕获的前2条供应商响应，复现同一边界错误；前3个请求body SHA256与真实尝试逐个完全一致。被拒流请求hash为d4693d78a189db894216297b6fd1cf7927c4b0b0d3930220d2e9b84732440798。离线重放不产生新付费，不把重放响应计作新质量成绩。

供应商账本9条usage全部已知；隔离SQL模型网关另有1条generation_stream保守unknown预留，原样保留。两类账本不能混同：此处有明确前置拒绝及无真实传输证据，不能把该SQL预留冒充第10条供应商HTTP，也不通过修改SQL状态消除原unknown。

## 离线处理

补充执行器与启动器位于reports/business-acceptance-v1/，执行前已将两者hash绑定到本阶段execution-binding.json，业务源码、数据和原方案hash保持。最初两次Mock演练的诉求摘录非原话/额外Schema字段等替身错误及失败记录保留；最后真实SDK+MockTransport的REST/WS预检均3次合成HTTP通过，重复/超限/错端点/超时4个边界检查通过。它们都不消耗168额度，不当成真实模型成绩。

新增stream_guard_candidate.py仅为离线候选：默认仍拒绝未授权stream；明确要求include_usage，真实派发前预留原始body hash，DONE与stop完成后才结算usage；缺usage、截断、超时保持失败或unknown并保守占额，不重试。10项SDK替身检查通过：正常流、字节拆分、缺usage、截断、length、重复usage、超时、无流授权、重复操作、超大请求。候选未部署、未接入真实派发，不能据此宣称供应商SSE兼容性通过。

## 额度、数据与运行状态

实际input reservation为93378 UTF-8字节、output reservation为8656 tokens；已知真实input tokens 25052、output tokens 2567。价格仍未核验，不把应用估算当实际账单。

原33份派发账本hash保持，旧286/286及更早账户/未知记录不动。本阶段只有一份固定live/1534678f1c67f74b780c83ba6233b0bce045d3f00205417b15311f06edfcb042/dispatch.sqlite，未换目录或删账本重置额度；模型Key仅通过临时私有env注入一次性评测容器，运行后删除。业务API/Worker仍d8b78495…/batch20，未改配置、未部署本轮评测候选，无退款申请/支付落入业务库。

完整证据：reports/business-acceptance-v1/live/1534678f1c67f74b780c83ba6233b0bce045d3f00205417b15311f06edfcb042/closeout.json、diagnosis.json、execution-binding.json、cases/*/api-receipts.json、provider-responses.json、separate-review.json及原始dispatch.sqlite。离线诊断见offline-stream-diagnostic/，流式候选结果见stream-guard-offline-results.json。

## 固定续验提案（未确认，未执行）

继续使用同一168账户，原9行逐列hash保护，剩159中最多新增148次；前三个通过场景剩余11个未用slot退休，不作为重跑许可。原始6次/轮、24576字节/次、2048输出tokens/次及首错停止保持，新增最大输入3637248字节、输出303104 tokens。

保留BA-01/02/03通过证据；BA-04仅允许对已成功的理解/任务决策2条响应进行精确请求body hash匹配回放，原计费只记一次，不重新派发，也不重放失败操作。其后的原http-3尚未真实派发，可在新流式契约获得明确续验指令后占用原slot。该场景成绩必须注明分段等价输入复验，不能伪称一次全新完整运行。

流式续验须明确允许native SSE并请求usage footer，固定候选/执行器hash；协议失败或usage未知立即停止，无自动回退/重试。最终6例仍须开发集与单独文本复核全通过后才进入。提案保存在continuation-proposal.json，目前未确认，根STOP.json保留，不能自动继续。
