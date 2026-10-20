# 运维

正式地址为 https://cs2inventory.cn/；HTTP/www 跳转至该地址。IP 和旧 /cs2_inventory 前缀已退役。healthdoc.cn 继续提供独立应用。

TLS：新域名使用独立 Certbot webroot 证书，certbot.timer 自动续期；deploy hook 只处理 cs2inventory.cn，先 apache2ctl -t 再平滑重载。主动演练：`sudo certbot renew --cert-name cs2inventory.cn --dry-run`。

发布与切换脚本、验收矩阵和恢复规则见 [deployment.md](deployment.md)。常规代码回滚默认 --code-only，不恢复数据库、不重开 IP 入口；数据库恢复必须显式 --restore-database。

## 服务

- `cs2-inventory-web.service`：Gunicorn Web 服务。
- `cs2-inventory-worker.service`：两个并发槽的扫描任务处理器。
- `cs2-inventory-schedule.timer`：北京时间每天 08:00、20:00 建立扫描批次（2 轮）。
- `cs2-inventory-cleanup.timer`：每 15 分钟清理超过正式客户付费宽限期的监控数据。

批次运行期间普通用户只读，管理员仍可管理。批次沿用 `{日期}-R2`（08:00 深度）与 `{日期}-R5`（20:00 轻量）槽键防止重复入队；08:00 前补触发直接跳过；R2 为深度轮，R5 为单库存轻量轮（每目标一次 `parse=1` + `try_first_seven_days_blocked_items=2` + `trade_locked=1` + `no_cache=1` 请求，严格读取保护物品，不静默回退历史保护快照；公开物品仍由 Steam 官方接口获取）；多个批次重叠时，Worker 等全部定时批次完成后再解除维护并清理过期快照。快照分层保留：48 小时内全保留，其后每个（目标, 北京自然日）只留当日最后一张，整体保留 8 天。

## 扫描状态与完整性诊断（2026-10-20）

- 管理员目标列表和个人监控列表按证据显示“校验通过 / 实时性待核验 / 含历史记录 / 数量待核对 / 部分来源异常”，管理员列表直接展示原因，鼠标悬停可查看说明。
- API 增加 `scan_label`、`scan_detail` 展示字段，原 `scan_status` 和完整性门槛不变；`partial` 不代表整份库存都未获取，也不升级为可确认移除的 `ready`。
- `steamwebapi-cache=no` 只在明确的非回退上下文请求中作为实时证据；缺少响应证据、缓存、缺件、分页失败、私密、HTTP 451/429 继续保留相应诊断。
- 深度轮的普通来源显式使用 `trade_locked=0`，避免把 context 16 当作公开 context 2；保护来源独立采集。
- 补扫使用现有 Worker 的轻量任务，每个目标通常一次付费请求（本地保守记账 3 credits，分页/重试按实际请求次数增加）。补扫只更新当前快照，不重写历史早间基线，不改定时频率。
- 上游上下文及回退语义参见 [Steamwebapi 官方文档](https://www.steamwebapi.com/api/steam/documentation)。

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

## 额度排查

先对照供应商 `/account/me` 的 `package`、`subscriptionstart`、`subscriptionuntil` 和最近扣费，再检查 `quota_usage` 按 `source` 的平均 credits。单页无重试时，`daily_light` 应为 3，而非约 21。供应商 `month` 是自然月统计，不是订阅账期余额；本地保守记账包含失败尝试，不等于真实扣款。后台展示实际采用的账期起点及回退来源。

## 持续未见与历史观察收敛（2026-10-20）

- 观测缓存仅在具有实时证据时更新 `last_verified_at`、`last_seen` 和有效期；未知/缓存响应不再把旧物品续期十天。旧条目没有该字段时，以既有 `first_seen` 限定最长十天，避免旧错误续期继续生效。过期只代表历史观察结束，不直接确认资产卖出。
- 对比接口逐件核验：本次完整实时库存可以核验旧基线中确曾实时命中的资产，即使旧基线整体不完整；旧缓存/未知观察不会因此被算作确定移除，而进入 `historical_closed`。本次仍不完整时，不以持续天数替代实时证据。
- `pending`、`unconfirmed` 增加 `pending_since`、`pending_days`、`last_confirmed_at`；界面显示持续天数和日期。未知响应不刷新资产的实时历史；对比操作只读，不触发上游请求或重新续期。
- 旧日报基线不重写；日报总量的可比性仍要求两日完整。快照对比新增 `removal_comparable` 和 `comparison_note`，明确区分整体可比与历史实时单件已核验。

### 数量缺口的按需补查

若官方公开 context 2 与严格实时 context 16 的资产并集仍少于已取得的声明总数，轻量轮追加一次交易路径 context 2（`parse=0`、`try_first_seven_days_blocked_items=2`、显式 `trade_locked=0`）。该路径可取回已结束保护但尚未公开的物品；普通无缺口目标仍只调用一次付费请求。显式 `0` 必须发送，不能因默认值逻辑被省略为隐含混合模式。补查分页、缓存证据与请求次数同样校验和记账；通常总计 6 credits，有真实缺口或上游错误时仍保留待核验。
