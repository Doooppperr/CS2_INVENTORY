# HTTP API

所有修改请求携带 `X-CSRF-Token`，登录态使用 HttpOnly、SameSite=Lax Cookie。

- `POST api/auth/register|login|logout|password`
- `GET|POST api/monitors`：每页固定 20 条及添加监控。
- `GET|DELETE api/monitors/<id>`：统一库存详情及取消订阅。
- `GET api/monitors/<id>/snapshots/<snapshot_id>`：快照与新增、移除、数量变化。
- `GET api/monitors/<id>/compare?days=1|3|7`：最新快照与指定天数前基准快照的差异。
- `GET api/jobs/<id>`：后台任务状态。
- `GET api/admin/users|targets|status`：管理员整合视图。
- `PATCH api/admin/users/<id>`：停用/启用账号或重置密码。
- `DELETE api/admin/targets/<id>`：管理员强制删除平台目标及其快照。
- `POST api/admin/query`：一小时有效的即时查询结果。
- `POST api/admin/targets/<id>/scan`：手动重扫。
- `GET health|ready`：进程与数据库健康检查。

快照公开结构仅包含 `items`、`total_items`、`item_types`、`coverage`、`scanned_at`、`elapsed_ms` 和 `errors`。
物品首次发现时间仅用于服务端排序，不通过公开快照结构返回。管理员用户列表额外返回加密副本解密后的当前密码及改密时间，并禁止响应缓存。
