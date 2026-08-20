# 部署

1. 确认本地测试、编译、密钥扫描及工作树检查通过。
2. 推送 GitHub `main`，以 commit SHA 打包 `git archive`。
3. 执行 `scripts/healthdoc_backup_prune.sh`：创建并验证新冷备份，保留新备份与最近五份历史备份。
4. 将发布包上传服务器，执行 `deploy/release.sh <archive> <commit>`；脚本先执行 Alembic 迁移，再切换发布软链接。
5. 验证 Web、Worker、Timer、IP 路径和 HealthDoc 服务。
6. 对比服务器发布目录、GitHub `main` 和本地 commit SHA。

Apache 保留 `http://111.229.87.94/cs2_inventory/` 入口，代理转发到 `127.0.0.1:5060`。

# 软容量与每日额度发布（2026-08-20）

1. `20260820_06` 删除旧数据库触发器 `trg_steam_target_capacity`，使唯一 SteamID 超过 35 后仍可新增。
2. 发布脚本执行迁移和 `init-db`；初始化过程也会幂等删除旧触发器。
3. Worker 不再按每日 300 的参考值拒绝扫描，但继续记录 `quota_usage`，并保留账期 9,000 的硬停止线。
4. 发布后核对数据库无容量触发器、控制台显示“当前值/参考值（可超）”，并验证目标数和每日使用量字段仍正常增长。
# 稳定汉化发布步骤（2026-08-16）

1. 备份数据库与当前 release，执行 `alembic upgrade head`。
2. 运行 `python -m cs2_inventory.cli localization-report` 获取只读修复报告。
3. 核对映射、变更快照和未解析数量后，运行 `python -m cs2_inventory.cli repair-localized-names --apply`。
4. 重启 Web、Worker、Timer；统一入队全部监控目标并等待批次完成。
5. 核对数据库版本、提交 SHA、`/health`、`/ready`、服务日志和管理员汉化状态。
6. 若迁移或修复失败，停止新版本，恢复部署前数据库与上一 release；修复命令可重复执行。

# 账号与预置目标清理发布（2026-08-19）

1. 发布前记录用户、停用账号、目标、订阅、无主目标和三个旧预置 SteamID 的只读计数。
2. `20260819_05` 会永久删除旧停用非管理员账号、三个旧预置目标和无主目标，并写入初始化标记。
3. 发布后再次运行 `python -m cs2_inventory.cli init-db` 并重启服务，确认旧账号和目标没有恢复。
4. 验证管理员三个子页面、账号永久删除、普通及管理员目标第二页返回行为。
5. 成功发布后的人工回滚必须同时恢复 `pre-deploy-<commit>/cs2_inventory.db` 和上一 release；仅切换代码不能恢复迁移删除的数据。
