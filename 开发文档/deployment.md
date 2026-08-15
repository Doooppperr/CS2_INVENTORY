# 部署

1. 确认本地测试、编译、密钥扫描及工作树检查通过。
2. 推送 GitHub `main`，以 commit SHA 打包 `git archive`。
3. 执行 `scripts/healthdoc_backup_prune.sh`：创建并验证新冷备份，保留新备份与最近五份历史备份。
4. 将发布包上传服务器，执行 `deploy/release.sh <archive> <commit>`；脚本先执行 Alembic 迁移，再切换发布软链接。
5. 验证 Web、Worker、Timer、IP 路径和 HealthDoc 服务。
6. 对比服务器发布目录、GitHub `main` 和本地 commit SHA。

Apache 保留 `http://111.229.87.94/cs2_inventory/` 入口，代理转发到 `127.0.0.1:5060`。
