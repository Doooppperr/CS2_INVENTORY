# 架构基线

库存引擎从原始单次查询项目迁入 `src/cs2_inventory/inventory_engine.py`。账号、持久化、任务调度和 Web 界面在后续提交中构建，库存引擎的多来源最大覆盖行为由迁移后的回归测试保护。

应用启动只在全新数据库中执行一次默认账号初始化，并用系统状态标记防止 Web、Worker 和定时 CLI 重复播种。管理员控制台按概览、用户管理、全部监控目标三个按需加载的板块组织；用户管理并行载入用户列表和邀请码列表。列表导航状态保存在 URL，详情页不持有易丢失的内存页码。

## 权益与生命周期（2026-08-28）

- RBAC 与套餐正交：`users.role` 仅为 `admin|user`，`account_kind` 表示 `trial|customer|internal`，`plan` 表示 `monthly|annual|permanent`。
- `TrialExperience` 一对一保存体验注册截止、当前首次扫描任务、固定结果快照和结果截止；成功体验只读取固定快照，不读取共享目标的后续更新。
- `ActivationCode` 保存邀请码摘要、套餐、初始限额及兑换审计；完整邀请码不落库。
- 有效客户、内部人员和管理员订阅才使目标进入每日队列。到期宽限用户的所有快照查询统一增加 `scanned_at <= activation_expires_at`。
- 生命周期清理由独立 15 分钟 timer 幂等执行；接口按当前时间即时执行权限判断，因此 timer 延迟不会延长权益。
