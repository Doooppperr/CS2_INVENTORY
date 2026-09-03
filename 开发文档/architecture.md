# 架构基线

库存引擎从原始单次查询项目迁入 `src/cs2_inventory/inventory_engine.py`。账号、持久化、任务调度和 Web 界面在后续提交中构建，库存引擎的多来源最大覆盖行为由迁移后的回归测试保护。

应用启动只在全新数据库中执行一次默认账号初始化，并用系统状态标记防止 Web、Worker 和定时 CLI 重复播种。管理员控制台按概览、用户管理、全部监控目标三个按需加载的板块组织；用户管理提供管理员开户、用户列表及续期升级邀请码。列表导航状态保存在 URL，详情页不持有易丢失的内存页码。

## 权益与生命周期（2026-09-03）

- RBAC 与套餐正交：`users.role` 仅为 `admin|user`，`account_kind` 仅表示 `customer|internal`，`plan` 表示 `monthly|annual|permanent`；`account_kind` 没有隐式默认值。
- 公开注册是无副作用 403 拒绝桩；管理员开户通过 `POST /api/admin/users` 固定创建有效普通客户，并从北京时间创建时刻计算自然月或自然年到期时间。
- `ActivationCode` 保存续期升级邀请码摘要、套餐、限额及兑换审计；完整邀请码不落库。客户开户无需首次兑换邀请码。
- 有效客户、内部人员和管理员订阅才使目标进入每日队列。到期宽限用户的所有快照查询统一增加 `scanned_at <= activation_expires_at`。
- 生命周期清理由独立 15 分钟 timer 幂等执行，只清理超过正式客户七天宽限期的监控数据。
- 迁移 `20260903_08` 先断言试用用户与试用记录均为零，再删除 `trial_experiences`；断言失败会中止发布，避免静默删除数据。

## 客户端三态主题（2026-08-29）

- 主题偏好是纯客户端状态，保存键为 `localStorage["cs2-inventory-theme"]`，合法值为 `system|light|dark`；不进入用户表、会话或业务 API。
- 两个页面模板在主 CSS 解析前同步读取偏好，并写入 `html[data-theme-preference]` 与解析后的 `html[data-theme]`，防止首屏主题闪烁；存储不可用或值非法时回退为 `system`。
- `system` 通过 `matchMedia("(prefers-color-scheme: dark)")` 解析，并只在该偏好下响应系统变化；显式浅色或深色不被系统事件覆盖。`storage` 事件负责同源标签页同步。
- 共享 `static/theme.js` 绑定落地页导航、未登录控制台和登录后控制台的选择器。模板根据当前页面路径解析应用前缀后加载脚本，使根路径开发环境与 `/cs2_inventory/` 反向代理部署共用同一逻辑。两页结构样式分别保留，颜色统一使用语义变量；浅色保持暖色复古基线，深色使用紫黑、紫色强调和青绿状态色。
