# AICSHub 前端重构

## 基线

2026-11-02，master，HEAD 1de5ac2592953476b2833721399f0e9a1a3b1d6c。已有未跟踪 .workbuddy/ 与 scripts/_dev_run_import.py 保留。React 18 / Vite 6 / TypeScript strict / Tailwind 3 延续。后端源码与契约不改。验收启动器使用新临时 SQLite、合成账户和真实后端，关闭外部模型及可选依赖；不会触及现有数据库。

## 审计

旧结构：App.tsx 管理认证、导航、通知；api.ts 汇集接口；store.ts 保存完整登录响应；ChatPage 同时负责 HTTP、WebSocket、消息、滚动、诊断和人工表单。6 个页面，没有前端测试。

风险：刷新令牌并发轮换；身份未复核；会话切换后旧响应覆盖；token 直接改最后一条消息；断线 busy 无恢复；IME 误发送；反馈使用临时 ID；人工分配共用状态；原生 prompt；部分分析失败清空全部结果；假在线与假时间范围。

## 设计与信息架构

技能 design-taste-frontend，app 路线，framed-sheet，seed 1052807293，无重抽。灰青外框 + 白色工作面，IBM Plex Sans 与中文系统无衬线（后续记录实际字体），数据使用 tabular figures。标志为两条对话线；产品特有元素是咨询、经服务端校验的答复、人工/工单处理的连续记录。状态带文字；不使用装饰图表、渐变、假指标。

| 角色 | 一级入口 | 首屏问题和动作 | 次级区域 |
| --- | --- | --- | --- |
| 用户 | 我的智能客服 | 要咨询什么 / 发送问题 | 桌面会话列、手机历史抽屉 |
| 用户 | 会话历史 | 如何继续咨询 / 打开会话 | URL 定位聊天 |
| 用户 | 我的工单 | 处理到哪里 / 查看或创建 | 详情面板 |
| 用户 | 个人账户 | 身份与状态 / 退出 | 独立页 |
| 管理员 | 会话工作台 | 哪个会话需要查看 / 读取历史 | 诊断抽屉，自有会话测试 |
| 管理员 | 工单管理 | 哪些待处理 / 筛选和处理 | 详情与编辑 |
| 管理员 | 人工协同 | 哪些等待分配 / 分配和完成 | 每项独立弹窗 |
| 管理员 | 知识文档 | 哪些可被检索 / 创建或管理 | 文档表单与确认 |
| 管理员 | 检索测试 | 实际命中什么 / 检索 | 独立证据列表 |
| 管理员 | 运行分析 | 当前累计表现 / 刷新 | 指标、告警、依赖分区 |
| 管理员 | 用户管理 | 哪些账号可访问 / 创建编辑停用 | 确认弹窗 |
| 管理员 | 管理员账户 | 当前身份 / 退出 | 独立页 |

## 模块

app：初始化、认证和 hash 路由。layouts：响应式壳层。api：类型、请求客户端和领域方法。state：唯一 Zustand 本地状态；TanStack Query 管理服务端缓存。features：聊天控制器、协议归约器、消息和业务表单。components：按钮、弹窗、状态、分页。hooks：提交锁、请求和 viewport。styles：token、全局、布局与业务视图。tests：单元、组件和浏览器验收。

布局算式：1440 - 228 导航 - 8 外框 - 48 内边距 = 1156 工作宽度。聊天 256 历史 + 弹性消息列，诊断抽屉。手机一次呈现一个任务区域；表格改为字段列表；导航折叠。

## 接口限制

- 管理员全会话 GET /api/v1/sessions/，为按需快照。他人会话只读。
- 历史消息无 Trace ID；支持手动查询已有 Trace。工具目录与本轮调用分别展示。
- 用户无人工进度查询；只展示提交回执，不能伪造队列位置。
- 指标为进程累计，无时间序列。健康 configured 不代表模型实测可用。
- 创建知识立即发布与索引；明确保存并发布。文档列表不返回完整正文和更新时间，不能冒充可读取原文或准确更新时间。
- 会话历史仅 limit，无游标；扩大读取数量并说明范围。部分列表无总数，分页显示当前页及可能更多。
- 客服目录为登记数据，不宣称真实在线或负载保证。

## 阶段

1. 审计、设计、旧截图。2. 认证与基础架构。3. 用户完整链路。4. 管理员链路。5. 响应式与一致性。6. 测试、生产构建、五档浏览器验收、技能 lint/inspect、截图复查与分主题提交。

基线截图保存在 reports/frontend/before，验收结果见下文。

## 实施结果（2026-11-03）

旧的单文件 App/API/Store/样式与六个耦合页面，改为以下职责：

| 模块 | 新职责 |
| --- | --- |
| frontend/src/app、layouts | 登录验证、受保护的 URL 路由、用户/管理员导航与响应式工作面 |
| frontend/src/api、state | 接口契约、统一错误与并发刷新；唯一 Zustand 本地状态；TanStack Query 服务端缓存 |
| frontend/src/features/chat | 单轮 WebSocket、已校验流式事件归约、消息/引用、诊断与转人工 |
| frontend/src/features/tickets、knowledge、users | 表单、状态转换、幂等与确认操作 |
| frontend/src/components、hooks | 可访问弹窗、加载/错误/空状态、提交锁 |
| frontend/src/pages、styles | 独立页面任务、token/布局/业务样式与响应式规则 |
| frontend/tests | 协议、认证、表单与浏览器验收 |

旧源码 components/common.tsx、styles.css、types.ts、App.tsx、api.ts、store.ts 已在功能替换后移除。生产 CSS/JS 从本次源码构建。由于原 Dockerfile 直接复制仓库中旧 static/，新增 Node 构建阶段并停止跟踪旧打包文件；本地 static/ 仍保存本轮 npm build 结果。

普通用户首屏：可直接发送、选择真实会话、查看来源；切换会话时请求按 session ID 隔离。离线及不确定提交不自动重发。管理员首屏：会话列表、只读他人历史、可展开真实 Trace/工具调用；工单、人工协同、文档、检索、分析与用户管理分别独立。手机为折叠导航和字段式列表。

## 实际验收

- frontend/ 执行 npm ci、npm test：5 个文件、21 项通过；npm run build：通过，输出根目录 static/ 且资源前缀 /static/。
- Python 原有聊天与退款回归：python -m pytest tests/test_chat_turns.py tests/test_refund_flow.py -q，41 通过、51 个现有弃用警告。
- 生产构建由 FastAPI 静态挂载提供；隔离 SQLite + 合成账号真实登录与业务 API，Playwright 18 项主流程通过且无 JS 异常，六项小屏/离线/长内容/竞态/加载/权限压力检查通过；12 个用户/管理员入口各在 1440、1280、768、390、360px 复查，共 60 张截图且无页面级横向溢出。
- 技能 lint.mjs --app：0 FAIL、0 WARN。inspect.mjs --app 对登录、用户聊天、管理员会话、运行分析验收，全部 0 FAIL、0 WARN。仅弹窗和消息悬浮控件保留阴影并在源码注明原因。
- docker compose config --quiet 通过。docker build --target frontend-build 已尝试，但 Docker Desktop 无法连接 Docker Hub 拉取 node:22-alpine；不能宣称镜像构建通过。

## 截图与视觉复查

- 改版前：reports/frontend/before/login-1440.png、user-1440.png、user-390.png、admin-1440.png、admin-390.png。
- 改版后：reports/frontend/after/login-{1440,1280,768,390,360}.png、user-chat-*.png、admin-*.png；reports/frontend/after/width-audit/ 的 60 张入口截图；reports/frontend/after/inspect-*/ 的相应宽度技能截图与 report.json。压力截图在 reports/frontend/after/stress/。截图和合成验收库留在本机忽略目录，不进入 Git。
- 视觉复查：首屏名称和操作清楚；灰青工作框将导航与白色业务面分开；工单手机字段可扫描，长表格在消息内滚动。会话列表在管理员视角仍受后端字段稀疏限制，只能显示用户 ID、意图、时间及短会话 ID。
- 应用屏幕主观评分：设计 7.5、易用 8、识别性 7、内容 8，按技能加权约 7.6。主要剩余视觉弱点是数据稀少时对话主区域偏空；保持真实空态，不填充演示记录。

## 未验证范围与后端缺口

- 隔离验收未使用真实 LLM、真实 Embedding、Redis 或 Milvus；知识检索交互仅使用测试启动器的确定性 Embedding 替身。未部署、未推送、未访问真实业务数据。
- 未在真实手机软键盘上实测；验收覆盖 360px 视口缩小与输入聚焦。
- Docker 镜像拉取受当前网络限制；Dockerfile 的新前端构建阶段需要可访问 node:22-alpine 的环境再次验证。
- 管理员全会话不是实时推送；历史消息不带 Trace ID；用户端没有人工请求进度查询；进程指标无时间序列；文档列表不返回更新时间或正文。以上均在 UI 标记或降级，未改后端 API。

## 主要修改文件

- 基础架构：frontend/src/main.tsx、app/App.tsx、app/router.ts、layouts/AppLayout.tsx、state/auth.ts、state/chat.ts。
- API 与交互：frontend/src/api/client.ts、api/index.ts、api/types.ts、hooks/useTask.ts、components/ui.tsx。
- 用户链路：frontend/src/pages/LoginPage.tsx、ChatPage.tsx、HistoryPage.tsx、TicketsPage.tsx、AccountPage.tsx；features/chat/、features/tickets/。
- 管理员链路：frontend/src/pages/HandoffPage.tsx、KnowledgePage.tsx、SearchPage.tsx、AnalyticsPage.tsx、UsersPage.tsx；features/knowledge/、features/users/。
- 视觉与构建：frontend/src/styles/{tokens,global,layout,workflows,login,chat,responsive}.css、frontend/public/favicon.svg、frontend/index.html、frontend/vite.config.ts、frontend/package.json、frontend/package-lock.json、Dockerfile、.dockerignore、.gitignore、docs/DEPLOYMENT.md。
- 验收：frontend/vitest.config.ts、frontend/tests/*.test.ts(x)、frontend/tests/support/*.mjs 与 serve_backend.py。

Git 分主题提交：78a5b66（审计与设计）、ad4055f（完整前端重构）、440a646（自动化和浏览器验收）、7b9d0bc（镜像内前端构建与静态包去跟踪）。合成验收与本地截图不纳入版本管理。
