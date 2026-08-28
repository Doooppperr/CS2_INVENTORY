# 数据库

生产环境使用 `/var/lib/cs2-inventory/cs2_inventory.db`，启用 SQLite WAL、外键和 10 秒 busy timeout。

核心表包括用户、体验记录、邀请码、唯一 Steam 目标、订阅、快照、资产行、扫描任务、扫描批次、额度账本和系统状态。不同用户通过订阅共享同一个目标；删除账号时共享目标保留，最后一个订阅删除后目标、快照和待执行任务一并删除。

快照资产按 `(snapshot_id, asset_key)` 唯一。用户接口只读取按名称合并后的统一库存，内部证据字段不对外暴露。

结构变更使用 Alembic；每次生产迁移前停止写入，使用 SQLite backup API 创建并校验一致性备份，保留在对应发布备份目录。

# 权益、邀请码与备注（2026-08-28）

- 迁移 `20260828_07` 为 `users` 增加 `account_kind`、`plan`、激活时间、到期时间和每用户监控限额。
- `trial_experiences` 保存体验注册/结果截止及固定快照；记录随体验用户删除。
- `activation_codes` 只保存 SHA-256 摘要和短前缀，记录套餐、限额、创建者、兑换者及撤销时间。
- `subscriptions.remark` 是用户私有的 50 字备注；同一个 SteamTarget 可被不同用户赋予不同备注。
- `NULL monitor_limit` 只用于管理员和内部人员无限额度；正式客户使用非负整数，邀请码创建时至少为 1。
# 名称汉化数据（2026-08-16）

- `snapshot_items` 增加 `raw_name`、`classid`、`instanceid`、`name_localized`；资产键、数量、首次发现时间和交易保护字段语义不变。
- `item_name_localizations` 以 `(language, source_name)` 唯一保存官方展示名及物品类标识。
- `localization_jobs` 保存快照补译队列、尝试次数、下次执行时间、待处理数量和失败原因。
- 迁移版本为 `20260816_04`；历史行先令 `raw_name=name`，再由可信缓存和同资产历史配对修复。

# 永久删除与一次性初始化（2026-08-19）

- `system_state.bootstrap_seed_version=1` 标记默认账号初始化已完成；服务重启和每日 CLI 不再恢复已删除账号或目标。
- `20260819_05` 永久清理旧停用账号、三个旧预置目标及无主目标。迁移删除的数据只能从部署前 SQLite 备份恢复。
- 新数据库仅创建默认账号，不创建预置监控目标。
