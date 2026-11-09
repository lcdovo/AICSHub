# Judge v2 JSON协议与片段脱敏冲突修复

日期：2026-11-08。用户要求找出问题、修复并再次验收。本次完成两项工程修复，真实复验在新的v2句子归类冲突处按约定停止，**未通过完整验收、未部署启用**。当前新80阶段21/80、剩59，旧122/122与其他历史阶段保持。

## 1. 非法函数参数JSON

原始HTTP回执在客户端解析前已出现`"answerability": full`，finish_reason=tool_calls且未截断，排除“客户端删掉引号”及该次截断原因。旧请求仅function_calling，没有response_format JSON对象约束；Pydantic strict只做本地校验，不能约束供应商输出字节。提示中未给完整JSON schema文本且多个answerability字段语义不同，属于潜在影响因素，未通过有因果对照的真实试验证明其独立作用。

新增`LLM_JUDGE_V2_OUTPUT_PROTOCOL=function_calling|json_object`，**默认仍function_calling**，业务.env未改。候选v2评测显式选择json_object，发送`response_format={"type":"json_object"}`，不发送tools/tool_choice，system提供完整schema与双引号字符串要求；理解/生成/v1仍走原协议。没有供应商不支持时的静默协议回退，v2选择此协议时single_attempt=true。

`LLMService._strict_json_output`只接受完成的stop文本回复，用标准json.loads与严格Pydantic验证；拒绝裸字符串枚举、重复键、非有限数字、截断、Markdown围栏、错误类型及工具回包。没有parse_partial_json、拼接引号、Markdown提取或修复重试。SQL网关继续先预留/后结算，错误回包的usage可用时保留；SDK在截断时先抛异常则保守保留unknown预留，不伪造用量。协议配置纳入最终回答策略指纹，防止复用旧协议判决/缓存。

真实JSON协议复验第一轮新增15次：前4例理解及正反审查均通过，第5例停；本轮10个Judge回复全部是合法JSON，未再出现裸full。加后续安全ID复验2个Judge回复，共**12个真实JSON对象回复语法合法**。这只说明本次样本中协议错误未复现，不是供应商结构化输出可靠性的统计保证，也不是语义验收通过。

## 2. 服务端片段ID被本方脱敏器误改

第一轮JSON复验在MI-05的片段ID出现`[PHONE_REDACTED]`。原字段是服务端SHA256十六进制字符串，含有11位手机号形状数字；`run_structured`调用SensitiveDataRedactor处理输入，原手机号regex仅限制数字边界，误把哈希内部子串当成手机号。供应商只是回显它收到的损坏ID，不能把该失败归为模型伪造引用。

使用真实脱敏器回归先复现红灯，再改为无数字字母表：SHA256的16进制每位通过`0123456789abcdef`→`abcdefghijklmnop`一一映射，前缀r，保留完整256位信息，仍绑定轮次、来源、版本和全文。没有截短哈希或豁免用户输入，手机号脱敏regex未改，真实手机号继续脱敏。回归验证含手机号形状数字的摘要编码可逆且不被脱敏。

真实修复后MI-05返回当前完整ID，正确/错误候选的JSON均合法，坏候选取得合法拒绝；来源身份校验不再因脱敏失败。源ID编码变化会改变供应商输入，本轮保留的前4例是用户明确批准的历史分段证据，**不能称为最终源码相同输入的一次完整运行**。

## 3. 已获准的分段复验及新失败

用户明确批准剩62次：16个剩余组件最多48次，四图按4/3/3/4最多14次。原3条非法函数协议阶段记录、JSON阶段15条均逐列绑定保存；独立操作前缀safe-id-v1/，仍用原80账户，没有新增额度或重置创建标记。前4例真实成绩保留，其余场景从MI-05开始。

修复后仅新增3次，MI-05理解2/2，坏候选合法拒绝，但正确候选将segment0“目前没有权限查询该订单，无法确认状态”同时列入claims与nonfact_segments，违反v2句子类别互斥及完整覆盖规则。返回`candidate_segments_not_exactly_reviewed`，unknown并拒绝。原JSON合法、ID完整，没有超时/截断。系统没有自动删除其中一份或根据整体valid覆盖程序判决。

按任务失败停止，剩15例（含全部4个新编合成例）、4个完整图、生成文本审查、部署冒烟未执行。历史分段累计覆盖5/20组件，4个正确候选接纳、0错误候选误放，9/10合法绑定Judge结果；失败unknown必须保留分母，不能算正确语义拒绝。已保存`acceptance/multi-intent/captured-v2-overlap-v1.json`及回归验证不自动消除该冲突。旧MI-15等开发样例不再当作独立未见金标；全部材料仍为assistant合成标签。

## 验证、额度与服务

最终宿主相关425通过（`reports/offline/20261108-002826-982397`），network=none容器同范围425通过（`reports/offline/20261107-162838-961366`），来源完整稳定、审计阻断0；compileall、定向Ruff、diff检查通过。**这是相关回归，不是最终全量。** 捕获新增后app不再变更，失败实测来源与最终捕获测试来源分别在closeout记录。

此前冻结源码SDK完整演练20组件+4完整图74次合成HTTP通过、付费0，修复后的20例另有离线契约回放。替身只证明调用/绑定/评分/预算机制，不证明真实模型质量。

本次共新增18次（15次JSON阶段+3次安全ID阶段），加原3次为**21/80、剩59**；21条http_complete、usage已知，阶段已知输入70678/output7936 tokens，不等于已核对账单。历史原122/122、旧111/120及其余保护账本均保持，旧两条usage未知保留，不挪旧9次。账本仍为`reports/multi-intent/live-budget/f3659b32128b67fca75b24c67567aab6d031caa7ab0fbb16195ad8f91d1728ef/dispatch.sqlite`。

临时评测容器、凭据文件已清理，业务配置/开关和业务服务启停未改。检查时主app/mysql/redis/milvus/etcd/minio停止，Worker重启或starting，Embedding/Reranker健康，不外推九服务健康或生产部署验收。

机器状态：`JSON_PROTOCOL_AND_REDACTION_SAFE_IDS_FIXED_REVALIDATION_STOPPED_FACT_NONFACT_OVERLAP`，见`reports/multi-intent/safe-id-revalidation/closeout.json`。JSON阶段原错误见`json-object-revalidation/failure-fragment-redaction.json`，第一轮非法参数记录未覆盖。继续真实调用须按失败停止规则取得明确续验指令和固定方案；剩59并不是自动续跑授权。

下一项质量工作应针对v2事实/nonfact分类的互斥性及正确资料不足说明，不再把已修复的JSON语法或脱敏ID冲突当作未定位问题，也不能删去句子覆盖约束使测试变绿。
