# HTTP API

所有修改请求携带 `X-CSRF-Token`，登录态使用 HttpOnly、SameSite=Lax Cookie。

- `POST api/auth/register`：公开注册拒绝桩，始终返回 `403 {"error":"账号仅由管理员创建"}` 且不查询或创建用户。
- `POST api/auth/login|logout|password`：登录、退出与当前用户改密。
- `POST api/activation/redeem`：客户原子兑换一次性邀请码以续期、重新激活或升级。
- `GET|POST api/monitors`：每页固定 20 条及添加监控。
- `GET|PATCH|DELETE api/monitors/<id>`：统一库存详情、私有备注及取消订阅。
- `GET api/monitors/<id>/snapshots/<snapshot_id>`：快照与新增、移除、数量变化。
- `GET api/monitors/<id>/compare?days=1|3|7`：最新快照与北京时间目标自然日最后一张快照的差异；目标日缺失时不向更早日期回退。
- `GET api/jobs/<id>`：后台任务状态。
- `GET api/admin/users|targets|status`：管理员用户、目标与概览数据；分页越界时返回最后一个有效页。
- `POST api/admin/users`：仅管理员在 CSRF 保护下创建普通客户；请求字段为 `username`、`password`、`plan`、`monitor_limit`，成功返回 201 并立即可登录。
- `PATCH api/admin/users/<id>`：重置密码或调整正式客户监控限额，不接受账号停用状态。
- `GET|POST api/admin/activation-codes`：分页列出邀请码或生成一次性邀请码；列表不返回摘要和完整码。
- `DELETE api/admin/activation-codes/<id>`：撤销尚未兑换的邀请码。
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

`GET api/bootstrap` 的 `user.entitlement` 与 `GET api/monitors` 的 `entitlement` 返回账号类别、套餐、派生状态、到期/宽限时间、监控用量/限额及是否允许新增。付费宽限截止由所有详情、历史、显式快照与对比接口一致执行。

用户监控元素增加 `remark`，`label` 的有备注格式为 `备注名 -（Steam名）- SteamID64`；管理员全局目标接口继续返回不含用户备注的全局标签。

`GET api/monitors` 返回 `platform_targets`、`platform_limit` 和 `platform_limit_enforced=false`；`GET api/admin/status` 返回 `targets`、`target_limit` 和 `target_limit_enforced=false`。其中 80 仅为控制台参考值。`quota` 增加 `billing_remaining`、`warning_level`、`warning_reserve`、`critical_reserve`、`estimated_credits_per_scan` 和 `scheduled_scans_per_day`；每日及账期 `*_enforced` 均为 `false`，表示额度仅预警、不停止任务。

自然日对比响应增加 `requested_baseline_date` 与固定值 `baseline_selection=beijing_calendar_day_latest`。查询范围按北京时间目标日期的 `[00:00, 次日 00:00)` 转换为 UTC，存在多张快照时按扫描时间和 ID 倒序选择。

# 账号与分页约定（2026-08-19）

账号管理不提供停用或恢复语义。删除成功返回 `ok`、`deleted_user_id` 和 `deleted_targets`。监控、管理员用户、管理员目标三个列表接口均规范化 `page`，前端通过 `view`、`section`、`page`、`user_page`、`target_page` 保存导航状态。

## 扫描预算状态

`quota` 新增 `billing_period_start`（北京时间）、`billing_period_source`（`provider_subscription` 或 `legacy_25th_estimate`）、`scheduled_scan_hours_beijing=[7,14,21]`、`scheduled_scans_per_day=3`。默认每轮 2 次请求、每次估算 3 credits，故 `estimated_scheduled_credits_per_target_day=18`。每日本地用量按北京时间零点统计；账期优先采用有效的供应商订阅区间，缺失/异常/过期时回退原每月 25 日估算，界面明确标记。本地余额始终为保守估算，未改写历史账本。

## 看板 · 本轮交易保护新增

`GET /api/dashboard/protected-additions?date=YYYY-MM-DD&slot=latest&scope=mine`。日期按北京自然日；可选轮次为 `latest`（各账号当日最近有保护结果的一次）、`07`、`14`、`21`。指定时段按定时批次识别，不按快照写入的钟点推断。普通用户仅能查看订阅的目标；全量范围沿用看板权限。

`summary` 包括总账号、已观察和可比较账号、加仓账号、种类及新增件数。`items` 按新增件数降序，含显示名、总件数和各账号的件数、备注、基线及扫描时间。首次或缺少可靠基线时 `added_units=null`；至少有一个可比账号但均无新增时为 0。

当前保护分区与前一次有效保护扫描对齐；基线须包含完整库存。按资产身份计算正向增量，售出不抵消加仓，仅改名、转保护、能识别为历史持有的重新出现均不计入。1 / 7 日监控卡使用同一新增定义，但基线固定为对应自然日。每轮摘要保留本日及此前七日；升级前的 08 / 20 点记录不标作新的 07 / 21 点轮次。

默认入口使用 `date=recent&slot=latest`：从有权查看的目标中选择最近有记录的北京时间日期，并回传所选日期；同一个响应不会混入其他日期的记录。显式日期或时段仍按原选择查询。
`targets` 含可见目标及对应记录/基线时间；`items.market_name`（有独立市场名时）用于饰品搜索。看板汇总按当前筛选计算。
`GET /api/dashboard/scan-revision?scope=mine|all` 返回不透明的 `revision`，权限和保留期同看板。最新模式的可见页面每五分钟比较它与已读结果；有变化才重读结果。上述接口都不触发实际库存扫描。

## 本浏览器的账号切换

- `GET /api/auth/accounts`：返回当前登录账号 ID 及本浏览器仍有效的账号（仅 ID、用户名）。
- `POST /api/auth/switch`：传入 `user_id` 切换到本浏览器已验证且未被撤销的账号。没有固定的登录有效期。首次添加账号仍走 `POST /api/auth/login`。
- `POST /api/auth/accounts/forget`：传入其他账号的 `user_id`，移除其在本浏览器的快捷登录资格，不影响账号和订阅。
- `POST /api/auth/logout`：退出并清除此浏览器记住的全部账号。上述写请求沿用 CSRF 校验。
