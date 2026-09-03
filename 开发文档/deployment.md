# 部署

1. 确认本地测试、编译、密钥扫描及工作树检查通过。
2. 推送 GitHub `main`，以 commit SHA 打包 `git archive`。
3. 执行 `scripts/healthdoc_backup_prune.sh`：创建并验证新冷备份，保留新备份与最近五份历史备份。
4. 将发布包上传服务器，执行 `deploy/release.sh <archive> <commit>`；脚本先执行 Alembic 迁移，再切换发布软链接。
5. 验证 Web、Worker、Timer、IP 路径和 HealthDoc 服务。
6. 对比服务器发布目录、GitHub `main` 和本地 commit SHA。

# 管理员独占开户与试用退役发布验收（2026-09-03）

1. 停止 Web、Worker、Schedule Timer 和 Cleanup Timer，使用 SQLite backup API 创建完整备份并执行 `PRAGMA integrity_check` 与 `PRAGMA foreign_key_check`。
2. 发布前执行只读审计，确认 `account_kind=trial` 与 `trial_experiences` 记录均为 0，并记录用户、密码摘要、订阅、目标、快照和邀请码的计数与内容摘要；任一试用断言失败即中止发布并恢复服务。
3. 推送 GitHub `main` 后按完整 commit SHA 生成归档，执行 `deploy/release.sh <archive> <commit>`；迁移 `20260903_08` 会再次执行试用零记录断言后删除试用表。
4. 发布后确认公开注册返回 403 且用户数不变，管理员能创建并登录月度、年度、永久客户，普通用户不能调用管理员开户接口，既有内部账户密码摘要和权益未改变。
5. 访问 `/`，确认没有注册、免费体验和试用文案，只显示 `¥3xx / 月`、`¥2xxx / 年`、`¥xxxx / 永久`，页面源码也不包含完整金额。
6. 验证邀请码续期、重新激活、升级永久和撤销，确认永久客户不显示兑换区域；核对 `/health`、`/ready` 及四个 systemd 单元均为 `active`，两个 Timer 均为 `enabled`。
7. 核对生产数据库不存在 `trial_experiences` 和 `account_kind=trial`，服务器 `current`、GitHub `main` 与本地完整 commit SHA 一致。
8. 验收失败时执行 `deploy/rollback.sh <旧版本目录> <pre-deploy备份目录>`，恢复上一 release 和数据库备份，并额外执行 `systemctl enable --now cs2-inventory-cleanup.timer` 后复核两个 Timer。

# 使用须知发布验收（2026-08-30）

1. 访问 `/` 并确认“使用须知”位于“一键完成库存追踪”和“选择激活方式”之间。
2. 确认两张须知卡片分别说明首次查询通常需要 1 至 3 分钟，以及任务转入后台后可继续提交 SteamID64 并排队等待完成。
3. 在浅色、深色和 375px 移动视口下检查卡片对齐、文字语义分行与横向溢出。

# 公开套餐价格发布验收（2026-08-30）

1. 访问 `/` 并确认月度、年度、永久套餐依次显示 `¥3xx / 月`、`¥2xxx / 年`、`¥xxxx / 永久`。
2. 浅色和深色模式下确认货币符号、金额、周期单位与卡片背景对比清晰，三张卡片在桌面端对齐且移动端不发生横向溢出。
3. 确认价格直接显示在三张套餐卡片中，套餐区说明不再出现币种提示和“具体价格暂不公开”。

# 三态主题发布验收（2026-08-29）

1. 清空 `cs2-inventory-theme` 后分别以浅色、深色系统设置访问 `/`，确认首次渲染直接匹配系统且没有明显闪白。
2. 分别选择浅色、深色并刷新；从 `/` 进入 `/app`、库存详情和管理员三个板块，确认选择保持一致且显式选择不随系统变化；生产环境额外确认共享脚本从 `/cs2_inventory/static/theme.js` 返回 200，而不是请求根路径 `/static/theme.js`。
3. 切换为跟随系统后改变系统主题，确认页面实时更新；同时打开两个同源标签页，确认偏好通过 `storage` 事件同步。
4. 检查落地页、登录弹窗、监控列表、库存详情、用户管理、全部目标、备注/密码弹窗和 Toast 的文字对比度、焦点状态及 680px 移动布局。
5. 发布后核对 `/health`、`/ready`、Web/Worker、每日 timer、清理 timer、外网 `/cs2_inventory/` 和 `/cs2_inventory/app`，并确认本地、GitHub 与 `/opt/cs2-inventory/current` 的 commit SHA 一致。
6. 主题发布不包含数据库迁移；如需人工回滚，仍使用上一 release 与对应 `pre-deploy` 备份，回滚后额外执行并核对 `systemctl enable --now cs2-inventory-cleanup.timer`。

# 权益与邀请码发布（2026-08-28）

1. 上线前只读导出生产用户清单，确认当前既有账号范围；迁移会将执行时已经存在的账号回填为内部永久无限。
2. 停止 Web、Worker、每日 timer 和清理 timer，使用 SQLite backup API 创建备份并执行 `PRAGMA integrity_check`。
3. 执行 `20260828_07` 后核对用户、订阅、目标和快照计数不变；确认既有账号 `account_kind=internal`、`plan=permanent`、`monitor_limit IS NULL`。
4. 安装并启用 `cs2-inventory-cleanup.service/.timer`；验证每日任务只包含有效正式或内部订阅目标。
5. 验证 `/` 暖色复古落地页、`/app` 控制台返回首页入口、三个管理员板块、用户管理内的邀请码创建/兑换、备注、限额拒绝及宽限冻结接口。
6. 回滚必须同时恢复上一 release、`pre-deploy-<commit>/cs2_inventory.db` 和备份的 systemd units；不能只执行 Alembic downgrade 或切换代码。

Apache 保留 `http://111.229.87.94/cs2_inventory/` 入口，代理转发到 `127.0.0.1:5060`。

# 软容量与每日额度发布（2026-08-20）

1. `20260820_06` 删除旧数据库触发器 `trg_steam_target_capacity`，使唯一 SteamID 超过 35 后仍可新增。
2. 发布脚本执行迁移和 `init-db`；初始化过程也会幂等删除旧触发器。
3. Worker 不再按每日 300 的参考值拒绝扫描，但继续记录 `quota_usage`，并保留账期 9,000 的硬停止线。
4. 发布后核对数据库无容量触发器、控制台显示“当前值/参考值（可超）”，并验证目标数和每日使用量字段仍正常增长。
# 稳定汉化发布步骤（2026-08-16）

1. 备份数据库与当前 release，执行 `alembic upgrade head`。
2. 运行 `python -m cs2_inventory.cli localization-report` 获取只读修复报告。
3. 核对映射、变更快照和未解析数量后，运行 `python -m cs2_inventory.cli repair-localized-names --apply`。
4. 重启 Web、Worker、Timer；统一入队全部监控目标并等待批次完成。
5. 核对数据库版本、提交 SHA、`/health`、`/ready`、服务日志和管理员汉化状态。
6. 若迁移或修复失败，停止新版本，恢复部署前数据库与上一 release；修复命令可重复执行。

# 账号与预置目标清理发布（2026-08-19）

1. 发布前记录用户、停用账号、目标、订阅、无主目标和三个旧预置 SteamID 的只读计数。
2. `20260819_05` 会永久删除旧停用非管理员账号、三个旧预置目标和无主目标，并写入初始化标记。
3. 发布后再次运行 `python -m cs2_inventory.cli init-db` 并重启服务，确认旧账号和目标没有恢复。
4. 验证管理员三个子页面、账号永久删除、普通及管理员目标第二页返回行为。
5. 成功发布后的人工回滚必须同时恢复 `pre-deploy-<commit>/cs2_inventory.db` 和上一 release；仅切换代码不能恢复迁移删除的数据。
