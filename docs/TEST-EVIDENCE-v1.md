# 测试源码与执行证据 v1

2026-11-04，OPT-18，关联AC-18-05/07/09的证据可追溯部分；本实现不代表全部CI、全部门禁或独立重现实验已完成。

`python scripts/test_offline.py -q tests`保持T0隔离：空临时工作目录、独立SQLite、配置清理、网络审计封锁。执行器现在另外写source-start.json、source-end.json，按允许的源码/配置路径计算逐文件SHA256和整体指纹；不输出源码内容。

范围包括app/scripts/tests/acceptance/alembic及frontend/src/tests中允许的文本源码/fixture，根pytest/依赖锁/迁移/Docker与前端配置；Git已跟踪与未忽略的未跟踪源码均纳入。业务.env及.env.*、报告和数据目录不在范围。原有scripts/_dev_run_import.py仅记录路径与摘要，保持未跟踪；不执行。链接到工作区外的文件不读取，已跟踪源码缺失或不支持链接标记快照不完整。

manifest字段：commit是启动时HEAD，commit_at_end是结束时HEAD；它们不等于测试了对应干净提交。dirty_input_paths_at_start列出启动时变动的允许输入。start_source_fingerprint/end_source_fingerprint与逐文件清单关联实际工作区源码；changed_input_paths描述新增/删除/修改。

- pytest_exit_code始终保留pytest结果；失败/无测试等非0不改成通过。
- 用例通过但开始/结束源码不同，或快照不完整时，exit_code=2并提示证据拒绝。JUnit中的绿色用例仍保留，不能以其替代源码有效性门禁。
- 测试期间仅Git提交指针变化、实际源码字节相同，不视为源码漂移；指纹与dirty记录分开，便于把已验证内容对应到后续提交。
- 运行时不应编辑允许范围内源码。若发生变化，停止引用该结果为最终通过，稳定后重跑；不要删除失败报告、改白名单排除问题源码或回写历史指纹。

验证：临时Git仓库核验dirty/新文件纳入、中文路径、新增/删除检测、.env/报告排除、提交后同内容指纹不变。另用真实执行器子进程运行一个会修改源码的通过用例，确认pytest_exit_code=0但exit_code=2、差异精确列app/main.py。项目自身18项流程+来源检查通过，源码开始/结束稳定，详见M4-EVIDENCE-v1.md对应记录。

局限：这是允许范围内开始/结束快照，不是不可变checkout或持续监控；中途改后又恢复相同字节可能无法检测，也不证明未纳入的系统库/硬件/外部依赖未变。软件关键版本另记manifest（含模型SDK/httpx/Pydantic），锁文件有摘要；T1真实SQL/HTTP、T2供应商、UI/部署需要各自证据，不能拿T0指纹证明其完整。旧历史报告没有这些字段，保留当时范围，不补造历史源码。
