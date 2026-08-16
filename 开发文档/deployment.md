# 部署

1. 确认本地测试、编译、密钥扫描及工作树检查通过。
2. 推送 GitHub `main`，以 commit SHA 打包 `git archive`。
3. 执行 `scripts/healthdoc_backup_prune.sh`：创建并验证新冷备份，保留新备份与最近五份历史备份。
4. 将发布包上传服务器，执行 `deploy/release.sh <archive> <commit>`；脚本先执行 Alembic 迁移，再切换发布软链接。
5. 验证 Web、Worker、Timer、IP 路径和 HealthDoc 服务。
6. 对比服务器发布目录、GitHub `main` 和本地 commit SHA。

Apache 保留 `http://111.229.87.94/cs2_inventory/` 入口，代理转发到 `127.0.0.1:5060`。
# 稳定汉化发布步骤（2026-08-16）

1. 备份数据库与当前 release，执行 `alembic upgrade head`。
2. 运行 `python -m cs2_inventory.cli localization-report` 获取只读修复报告。
3. 核对映射、变更快照和未解析数量后，运行 `python -m cs2_inventory.cli repair-localized-names --apply`。
4. 重启 Web、Worker、Timer；统一入队全部监控目标并等待批次完成。
5. 核对数据库版本、提交 SHA、`/health`、`/ready`、服务日志和管理员汉化状态。
6. 若迁移或修复失败，停止新版本，恢复部署前数据库与上一 release；修复命令可重复执行。
