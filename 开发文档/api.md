# HTTP API

所有修改请求携带 `X-CSRF-Token`，登录态使用 HttpOnly、SameSite=Lax Cookie。

- `POST api/auth/register|login|logout|password`
- `GET|POST api/monitors`：每页固定 20 条及添加监控。
- `GET|DELETE api/monitors/<id>`：统一库存详情及取消订阅。
- `GET api/monitors/<id>/snapshots/<snapshot_id>`：快照与新增、移除、数量变化。
- `GET api/monitors/<id>/compare?days=1|3|7`：最新快照与指定天数前基准快照的差异。
- `GET api/jobs/<id>`：后台任务状态。
- `GET api/admin/users|targets|status`：管理员用户、目标与概览数据；分页越界时返回最后一个有效页。
- `PATCH api/admin/users/<id>`：仅重置密码，不再接受账号停用状态。
- `DELETE api/admin/users/<id>`：永久删除账号及订阅，共享目标保留，无主目标及其快照随之删除；当前管理员不能删除自身。
- `DELETE api/admin/targets/<id>`：管理员强制删除平台目标及其快照。
- `POST api/admin/query`：一小时有效的即时查询结果。
- `POST api/admin/targets/<id>/scan`：手动重扫。
- `GET health|ready`：进程与数据库健康检查。

快照公开结构仅包含 `items`、`total_items`、`item_types`、`coverage`、`scanned_at`、`elapsed_ms` 和 `errors`；每个 `items` 元素包含 `name`、`count`、`is_trade_protected`。同名物品会按明确交易保护状态拆分为最多两行，保护行始终置顶。
物品首次发现时间仅用于服务端排序，不通过公开快照结构返回。管理员用户列表额外返回加密副本解密后的当前密码及改密时间，并禁止响应缓存。
# 名称稳定性约定（2026-08-16）

库存接口继续只公开 `name`、`count`、`is_trade_protected`，不公开内部的 `raw_name`、`classid`、`instanceid` 或 `name_localized`。`snapshot_diff` 先按 `asset_key` 对齐，再通过官方名称映射聚合；同一资产仅发生中英文切换时，`added`、`removed`、`changed` 均为空。

管理员状态接口的 `localization` 对象包含 `mappings`、`pending_jobs`、`pending_items` 和 `failed_jobs`，用于观测名称映射和补译队列。

`GET api/monitors` 返回 `platform_targets`、`platform_limit` 和 `platform_limit_enforced=false`；`GET api/admin/status` 返回 `targets`、`target_limit` 和 `target_limit_enforced=false`。其中 35 仅为控制台参考值。`quota.daily_budget_enforced=false` 表示每日额度只统计并允许超过；`quota.billing_budget_enforced=true` 表示账期预算仍由 Worker 执行。

# 账号与分页约定（2026-08-19）

账号管理不提供停用或恢复语义。删除成功返回 `ok`、`deleted_user_id` 和 `deleted_targets`。监控、管理员用户、管理员目标三个列表接口均规范化 `page`，前端通过 `view`、`section`、`page`、`user_page`、`target_page` 保存导航状态。
