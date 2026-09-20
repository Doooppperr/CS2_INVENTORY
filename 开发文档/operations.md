# 运维

正式地址为 https://cs2inventory.cn/；HTTP/www 跳转至该地址。IP 和旧 /cs2_inventory 前缀已退役。healthdoc.cn 继续提供独立应用。

TLS：新域名使用独立 Certbot webroot 证书，certbot.timer 自动续期；deploy hook 只处理 cs2inventory.cn，先 apache2ctl -t 再平滑重载。主动演练：`sudo certbot renew --cert-name cs2inventory.cn --dry-run`。

发布与切换脚本、验收矩阵和恢复规则见 [deployment.md](deployment.md)。常规代码回滚默认 --code-only，不恢复数据库、不重开 IP 入口；数据库恢复必须显式 --restore-database。

## 服务

- `cs2-inventory-web.service`：Gunicorn Web 服务。
- `cs2-inventory-worker.service`：两个并发槽的扫描任务处理器。
- `cs2-inventory-schedule.timer`：北京时间每天 00/04/08/12/16/20 点建立扫描批次（6 轮）。
- `cs2-inventory-cleanup.timer`：每 15 分钟清理超过正式客户付费宽限期的监控数据。

批次运行期间普通用户只读，管理员仍可管理。批次使用北京时间日期与窗口序号槽键（`{日期}-R0..R5`）防止重复入队；R2 为深度轮，其余 5 轮为 batch 轻量轮；多个批次重叠时，Worker 等全部定时批次完成后再解除维护并清理过期快照。快照分层保留：48 小时内全保留，其后每个（目标, 北京自然日）只留当日最后一张，整体保留 8 天。

## 常用检查

```bash
systemctl status cs2-inventory-web cs2-inventory-worker cs2-inventory-schedule.timer cs2-inventory-cleanup.timer
curl -fsS http://127.0.0.1:5060/ready
sudo -u cs2inventory env PYTHONPATH=/opt/cs2-inventory/current/src CS2_STATE_DIR=/var/lib/cs2-inventory \
  /opt/cs2-inventory/venv/bin/python -m cs2_inventory.cli prune
sudo -u cs2inventory env PYTHONPATH=/opt/cs2-inventory/current/src CS2_STATE_DIR=/var/lib/cs2-inventory \
  /opt/cs2-inventory/venv/bin/python -m cs2_inventory.cli cleanup-accounts
```

发布使用 `deploy/release.sh`，失败时自动恢复旧软链接、部署前数据库和 systemd units；人工代码回滚使用 `deploy/rollback.sh <旧版本目录> <pre-deploy备份目录> --code-only`；需要恢复数据库时显式使用 `--restore-database`。

生命周期截止均按精确时间执行。清理 timer 只负责物理删除，timer 延迟不会让付费权益继续可用。每日队列和 Worker 会排除仅由宽限或已过期账号持有的目标。

账号删除、旧停用账号清理和旧预置目标清理均不可逆。若需恢复，停止 Web 与 Worker，恢复对应 `pre-deploy-<commit>/cs2_inventory.db`，再切回旧 release 并重启服务。`bootstrap_seed_version` 存在时，任何进程启动都不会再次播种账号或监控目标。
# 名称补译运维（2026-08-16）

- Worker 优先处理完整扫描任务，空闲时领取到期的 `localization_jobs`，补译不调用完整库存接口且不计入扫描额度。
- 发布后第 15 分钟首次补译；失败后约第 1 小时和第 6 小时重试，第三次仍失败则记录为 `failed`。
- `localization-report` 永远只读；`repair-localized-names --apply` 在一次数据库事务中更新名称、重算 `item_types` 并重建压缩载荷。
- 历史修复只采用官方缓存或同一 assetid 的可信简中历史，不猜译未知名称。
