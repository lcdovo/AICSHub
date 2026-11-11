# 回答反馈本机部署（2026-11-11）

已部署 `ai-customer:answer-feedback-20261111`，镜像 `sha256:456ab50f8ead07d70d2438afb42353ca324707f6825ff3680e1b5b5658c5795b`。API 与 Worker 同镜像且健康；九个客服服务检查时均 healthy。

基于实际运行镜像 `f31c83cc2bd0…` 制作补丁，后端增加反馈路由与所需 UTC 工具，前端以运行镜像保留的源码构建，仅覆盖反馈 API、导航、页面、消息按钮及必需 Markdown 组件。工作区的记忆优化、删除会话等其他候选未纳入。无数据库迁移；运行环境、命令、数据卷及模型配置保持。旧镜像保留为 `ai-customer:before-answer-feedback-20261111`。

## 验证

- 隔离前端构建通过；候选镜像断网导入反馈与 Worker 模块通过。
- 部署前未发现 accepted/running Run；两条 awaiting_confirmation 保持。
- 实际 HTTP 与 SQL 合成数据验证点赞、改为点踩、评价读取、管理员读取客服原文、普通用户访问后台403、不存在消息404、匿名访问401通过。测试反馈、消息、会话、两组合成账户已删除。
- `/health`、`/docs`、`/openapi.json`、首页及首页引用静态资源200。
- API/Worker镜像与反馈源码hash一致，环境逐键核对。首次环境核对使用列表比较因顺序不同失败，改为键值比较后通过，没有配置差异。
- 付费模型调用0；没有执行真实聊天或浏览器点击验收。此前宿主测试属于工作区测试，不冒称补丁镜像完整回归。

## 维护与回滚

私有精确覆盖包含运行密钥，只保存于忽略的 `.local/feedback-deploy-20261111/`，不得提交或输出。

保持当前部署：

```powershell
docker compose -f docker-compose.yml -f .local/feedback-deploy-20261111/candidate-exact.yml up -d --no-deps --no-build app worker
```

回滚到本次部署前镜像及配置：

```powershell
docker compose -f docker-compose.yml -f .local/feedback-deploy-20261111/rollback-exact.yml up -d --no-deps --no-build app worker
```

未实际执行回滚。默认 `latest` 标签仍为原版本，维护本次发布请使用上述覆盖，避免默认 Compose 将其替换。证据为同目录 `verification.json` 和 `smoke-result.log`；旧验收账本未改。


## 前端体验更新（2026-11-11，覆盖上文运行镜像）

在反馈功能镜像上制作静态资源补丁 `ai-customer:answer-feedback-ui-20261111`，镜像 `sha256:75bf4db7e34a1b8cfca304a3b728baeca16a0d14e09f6a83bbdb6d69339e000b`。后端继承上一部署；同步构建所用前端源码供后续维护。未混入工作区记忆优化等其他候选。

后台新增快捷筛选、评价状态标记、原文展开、列表层级、当前页范围、空列表引导；手机端使用下拉筛选及纵向列表。只有超过20条才显示翻页控件。短回复完整显示，长回复折叠后可展开。聊天按钮增加选中颜色、当前操作加载状态及保存提示，支持键盘与减少动画偏好。

设计沿用 Framed sheet（seed 1323020296），保留原配色和字体，没有重抽。页面问题是“用户如何评价这条客服回复，管理员从哪里回看上下文”；以评价标记和对应原文作为视觉主体。无虚构全站统计。合成数据仅在隔离预览中使用，未打包进入业务镜像。

验证：前端全套36项通过，后续短文折叠边界修正后相关4项复验通过；工作区和隔离部署上下文构建通过。生产合成预览1440/768/390像素检查0 FAIL；1项留白WARN来自全高应用外框内仅有3条样例数据，保留框架高度以延续现有应用布局。预览真实浏览器交互验证筛选、已有点赞恢复、改为点踩及保存提示通过。不是线上真实客户数据浏览器操作验收。

当前维护：

```powershell
docker compose -f docker-compose.yml -f .local/feedback-deploy-20261111/ui-candidate-exact.yml up -d --no-deps --no-build app worker
```

回退到本次界面优化前（保留回答反馈功能）：

```powershell
docker compose -f docker-compose.yml -f .local/feedback-deploy-20261111/ui-rollback-exact.yml up -d --no-deps --no-build app worker
```

该补丁不需要数据库迁移，不新增付费模型调用。部署验证保存在 `.local/feedback-deploy-20261111/ui-verification.json`，合成截图及生产预览检查在忽略的 `.taste-skill/feedback-production-inspect/`。
