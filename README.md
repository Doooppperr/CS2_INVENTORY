# CS2 Inventory Monitor

CS2 Inventory 是一个按 SteamID64 监控 CS2 库存的 Web 应用。后端通过多来源采样最大化覆盖，并将公开可见、近期观测及交易保护中的资产合并为一个统一库存快照。

正式源码位于 `src/cs2_inventory/`，测试位于 `tests/`，部署文件位于 `deploy/`，开发与运维文档位于 `docs/`。

## 基线测试

```bash
python -m unittest discover -s tests -v
```

> 本仓库不保存 API Key、生产数据库、真实库存响应或发布归档。
