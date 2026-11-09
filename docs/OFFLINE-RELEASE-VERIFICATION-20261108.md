# 离线测试隔离与部署版本回归（2026-11-08）

## 使用方式

在项目根目录运行一条命令，同时验证工作区与当前API/Worker部署镜像：

```powershell
python scripts/verify_offline_release.py
```

仅检查当前工作区已启用链路：

```powershell
python scripts/test_offline.py --profile active
```

保留原兼容路径或选择测试文件：

```powershell
python scripts/test_offline.py -q tests/test_model_first_understanding.py
```

直接python -m pytest在tests/conftest.py导入时失败关闭，并给出上述入口。保护发生在业务测试模块导入前，不能仅设置环境变量解除；离线入口安装网络审计后才设置进程内标记。真实供应商评测继续走显式、固定预算和失败停止的既有评测脚本，没有新增自动付费路径。

主机需已安装测试依赖、Docker可用且当前API/Worker运行。镜像验证仅在一次性容器内安装测试用aiosqlite，不修改业务镜像；wheel从reports/offline-wheel-cache读取。缓存缺失时验证失败，不自动联网安装。可在明确准备依赖时执行：

```powershell
python -m pip download --no-deps aiosqlite==0.22.1 -d reports/offline-wheel-cache
```

版本应与主机已安装的aiosqlite一致；本机为0.22.1。本轮使用已存在的本地wheel准备最终缓存。

## 隔离与证据规则

- AST枚举Settings字段，清除继承的业务配置，并清除OpenAI/Azure/Anthropic/LangChain/LangSmith常见SDK凭据及追踪开关。
- 临时工作目录不读取根目录.env；独立SQLite、无真实模型Key、关闭真实Redis/Milvus/外部观测，模型SDK请求使用MockTransport或显式替身。
- active档保持model/100%、任务循环、退款预览、逐项完成、SQL模型网关、Judge v2/json_object启用，重试0、输出2048、重排batch20。负面用例可显式验证关闭/兼容行为，但新增主路径用例逐项断言开关开启。
- 同进程网络审计覆盖线程、异步连接、DNS和sendto，保留Windows事件循环内部socketpair。任何阻断尝试即使被测试捕获，也拒绝通过证据；镜像容器另有network none。
- 记录起止源码指纹、测试配置档、依赖版本、JUnit、pytest.log与阻断次数；源码漂移或缺失拒绝通过。

主路径包括逐项Schema理解成功/超时/空值/非法结构、有限任务决策与本轮退款确认、逐项Judge证据绑定、超时/缺回执、发布前版本重核，以及真实SDK配合MockTransport的合法/非法/截断JSON。过期证据可以生成安全提示，但不得发布旧事实或把任务记为完成。不是依靠关闭任务循环/Judge来使本链通过。

旧test_model_first_understanding.py覆盖旧单项Schema和两次修复请求，不能直接套用逐项单次Schema。其原用例保留为baseline兼容回归，active档由新的ItemizedTaskUnderstanding单次调用用例覆盖；未修改业务实现或削弱原断言。

## 部署镜像对应关系

verify_offline_release读取API/Worker不可变镜像ID及127份app源码hash，要求两服务一致。隔离容器从该镜像复制app与迁移源码，验证app hash完全对应运行服务；使用同一份冻结测试材料，镜像内的依赖仅增加本地aiosqlite测试包。没有临时挂载源码到业务容器，也没有部署或重启服务。

工作区使用Git源码快照；镜像使用显式filesystem快照及image_id，commit记null，不捏造Git提交对应关系。镜像验证后的运行容器ID/镜像/hash与开始时再次对比。最终closeout逐项记录工作区与部署源码差异，不能将两者的测试成绩互相替代。

证据保存在reports/offline-release/<时间>/，包含closeout、workspace/deployed日志、两侧manifest、JUnit、完整源码hash及测试wheel hash；不记录业务环境变量或密钥。

## 验证范围

属于启用链路的合成替身/契约与真实SDK离线回归，不是独立人工语义金标、真实供应商整链、生产容量或全仓全部测试验收。旧失败报告保留。业务镜像与付费评测账本本轮不改。

## 本轮最终成绩

- 单命令最终报告：reports/offline-release/20261108-134818-200521/closeout.json，PASSED。工作区active 261通过；部署镜像d8b78495…active 261通过；两侧零失败/错误/跳过，起止源码稳定完整，blocked_network_operations=0，运行镜像/容器保持不变。
- 旧单项路径加隔离/证据保护：38通过，reports/offline/20261108-134653-865083。此前新主路径与保护定向16通过另保留，不能将重复用例累加成独立覆盖。
- 工作区与部署app有14处源码差异，完整路径和两侧hash见closeout，主要是候选性能/指标、LLM/Embedding池、可配置时限与生成参数、回答缓存绑定等；本轮没有将这些候选部署。
- Windows子进程输出编码导致的一次编排失败、镜像验证包遗漏迁移文件导致的260通过/1失败均保留。最终使用UTF-8子进程输出、镜像自带迁移文件以及离线wheel缓存后复验，不修改业务代码让测试变绿。最初active混入旧单项Schema的5项失败保留，旧单项用例在baseline原样通过，active使用逐项Schema新用例。新过期证据测试原先错误要求审查阶段一律拒绝，已改为检查最终不发布旧事实、不记任务完成，保留安全提示允许通过的业务含义。
- compileall和修改范围diff检查通过；最后九服务healthy。本轮无真实供应商派发，既有额度账本不变。没有运行全仓全量或真实供应商语义验收。
