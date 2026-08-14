# 架构基线

库存引擎从原始单次查询项目迁入 `src/cs2_inventory/inventory_engine.py`。账号、持久化、任务调度和 Web 界面在后续提交中构建，库存引擎的多来源最大覆盖行为由迁移后的回归测试保护。
