# 运维

## 服务

- `cs2-inventory-web.service`：Gunicorn Web 服务。
- `cs2-inventory-worker.service`：两个并发槽的扫描任务处理器。
- `cs2-inventory-schedule.timer`：北京时间每天 18:00 建立扫描批次。

批次运行期间普通用户只读，管理员仍可管理。批次全部完成后 Worker 自动解除维护并清理七天前快照。

## 常用检查

```bash
systemctl status cs2-inventory-web cs2-inventory-worker cs2-inventory-schedule.timer
curl -fsS http://127.0.0.1:5060/ready
sudo -u cs2inventory env PYTHONPATH=/opt/cs2-inventory/current/src CS2_STATE_DIR=/var/lib/cs2-inventory \
  /opt/cs2-inventory/venv/bin/python -m cs2_inventory.cli prune
```

发布使用 `deploy/release.sh`，失败时自动恢复旧软链接和部署前数据库；人工代码回滚使用 `deploy/rollback.sh <旧版本目录>`。
