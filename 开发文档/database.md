# 数据库

生产环境使用 `/var/lib/cs2-inventory/cs2_inventory.db`，启用 SQLite WAL、外键和 10 秒 busy timeout。

核心表包括用户、唯一 Steam 目标、订阅、快照、资产行、扫描任务、扫描批次、额度账本和系统状态。不同用户通过订阅共享同一个目标；最后一个订阅删除时数据库级联删除目标、快照和待执行任务。

快照资产按 `(snapshot_id, asset_key)` 唯一。用户接口只读取按名称合并后的统一库存，内部证据字段不对外暴露。

结构变更使用 Alembic；每次生产迁移前复制 SQLite 数据库并保留在对应发布备份目录。
# 名称汉化数据（2026-08-16）

- `snapshot_items` 增加 `raw_name`、`classid`、`instanceid`、`name_localized`；资产键、数量、首次发现时间和交易保护字段语义不变。
- `item_name_localizations` 以 `(language, source_name)` 唯一保存官方展示名及物品类标识。
- `localization_jobs` 保存快照补译队列、尝试次数、下次执行时间、待处理数量和失败原因。
- 迁移版本为 `20260816_04`；历史行先令 `raw_name=name`，再由可信缓存和同资产历史配对修复。
