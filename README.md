# CS2 Inventory Monitor

CS2 Inventory 是一个按 SteamID64 监控 CS2 库存的 Web 应用。后端通过多来源采样最大化覆盖，并将公开可见、近期观测及交易保护中的资产合并为一个统一库存快照。

正式源码位于 `src/cs2_inventory/`，测试位于 `tests/`，部署文件位于 `deploy/`，开发与运维文档位于 `开发文档/`。

## 功能

- 开放注册、用户登录和管理员整合视图。
- 全平台 35 个唯一 SteamID，多用户共享同一目标的扫描与快照。
- 首次扫描异步执行，每天北京时间 18:00 自动更新。
- 快照保留八天，支持最新快照与 1、3、7 天前的新增、移除和数量变化比对。
- 所有可靠资产统一展示；内部分类只用于最大化覆盖，不出现在用户界面。
- 监控列表每页 20 条，昵称显示为 `Steam昵称 (SteamID64)`。
- 明确处于交易保护的单件物品在最新库存中独立计数、金色置顶；其余物品按同名组内最新首次发现时间排序，内部时间不对外显示。
- 所有可靠资产统一采用 Steam 官方 `schinese` 名称；中文服务短暂失败时复用持久化映射或同资产历史名称，名称语言切换不计入库存变化。

## 本地运行

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
export PYTHONPATH="$PWD/src"
export CS2_STATE_DIR="$PWD/var"
export CS2_COOKIE_PATH=/
.venv/bin/flask --app 'cs2_inventory.app:create_app()' run
```

Windows PowerShell 将环境变量改为 `$env:PYTHONPATH="$PWD\src"` 等价形式。

## 测试

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

> 本仓库不保存 API Key、生产数据库、真实库存响应或发布归档。

完整设计、接口、额度、部署和安全说明见 [`开发文档/`](开发文档/)。
