# 运维

## 服务

- `cs2-inventory-web.service`：Gunicorn Web 服务。
- `cs2-inventory-worker.service`：两个并发槽的扫描任务处理器。
- `cs2-inventory-schedule.timer`：北京时间每天 18:00 建立扫描批次。
- `cs2-inventory-cleanup.timer`：每 15 分钟清理超过正式客户付费宽限期的监控数据。

批次运行期间普通用户只读，管理员仍可管理。批次全部完成后 Worker 自动解除维护并清理七天前快照。

## 常用检查

```bash
systemctl status cs2-inventory-web cs2-inventory-worker cs2-inventory-schedule.timer cs2-inventory-cleanup.timer
curl -fsS http://127.0.0.1:5060/ready
sudo -u cs2inventory env PYTHONPATH=/opt/cs2-inventory/current/src CS2_STATE_DIR=/var/lib/cs2-inventory \
  /opt/cs2-inventory/venv/bin/python -m cs2_inventory.cli prune
sudo -u cs2inventory env PYTHONPATH=/opt/cs2-inventory/current/src CS2_STATE_DIR=/var/lib/cs2-inventory \
  /opt/cs2-inventory/venv/bin/python -m cs2_inventory.cli cleanup-accounts
```

发布使用 `deploy/release.sh`，失败时自动恢复旧软链接、部署前数据库和 systemd units；人工完整回滚使用 `deploy/rollback.sh <旧版本目录> <pre-deploy备份目录>`。

生命周期截止均按精确时间执行。清理 timer 只负责物理删除，timer 延迟不会让付费权益继续可用。每日队列和 Worker 会排除仅由宽限或已过期账号持有的目标。

账号删除、旧停用账号清理和旧预置目标清理均不可逆。若需恢复，停止 Web 与 Worker，恢复对应 `pre-deploy-<commit>/cs2_inventory.db`，再切回旧 release 并重启服务。`bootstrap_seed_version` 存在时，任何进程启动都不会再次播种账号或监控目标。
# 名称补译运维（2026-08-16）

- Worker 优先处理完整扫描任务，空闲时领取到期的 `localization_jobs`，补译不调用完整库存接口且不计入扫描额度。
- 发布后第 15 分钟首次补译；失败后约第 1 小时和第 6 小时重试，第三次仍失败则记录为 `failed`。
- `localization-report` 永远只读；`repair-localized-names --apply` 在一次数据库事务中更新名称、重算 `item_types` 并重建压缩载荷。
- 历史修复只采用官方缓存或同一 assetid 的可信简中历史，不猜译未知名称。
