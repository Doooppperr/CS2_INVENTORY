# 部署（当前规范：2026-09-15，1.3.2）

## 正式入口与约束

- 唯一平台地址为 `https://cs2inventory.cn/`；HTTP 与 www 308 跳转并保留路径、查询参数。旧 `/cs2_inventory` 前缀优先返回 404，不参与跳转。
- HealthDoc 继续使用 healthdoc.cn 和现有 www 别名；IP、未知 Host 的 80/443 默认站点返回 404。IP HTTPS 可能先发生证书名称不匹配，忽略该错误也不提供应用。
- CS2 仅监听 127.0.0.1:5060，HealthDoc 服务保持现有端口；不更改 DNS、SSH、防火墙或扫描时间。
- 不再执行旧步骤中的 healthdoc_backup_prune.sh：它会停止 HealthDoc 服务并裁剪备份，不属于本次域名切换。

## 发布与域名切换

1. 独立测试环境安装 `pip install -r requirements-test.txt` 后执行 `PYTHONPATH=src python -m unittest discover -s tests -v`、Ruff 和 `git diff --check`；对生产数据库运行 `scripts/domain_data_audit.py` 保存只读摘要。requirements-test.txt 包含旧版可选 HTML 解析器夹具的 beautifulsoup4 依赖，不修改生产 Web/API Worker 的依赖环境。
2. 将 deploy 文件上传服务器，以 root 执行 `bash deploy/domain-routing.sh prepare /var/backups/cs2-domain-<唯一批次>`。脚本备份 Apache、环境文件、CS2 单元和状态、旧 release，只启用新域名的 ACME HTTP 验证站点。
3. 使用现有 ACME 账户运行 `certbot certonly --webroot -w /var/www/cs2-acme --cert-name cs2inventory.cn -d cs2inventory.cn -d www.cs2inventory.cn`。不使用 Apache 自动安装器，不重写 HealthDoc 业务代理。
4. root 执行 `bash deploy/test-domain-routing.sh <切换备份目录>`，在独立回环 18080/18443 实例检查路由、恢复哈希/链接及旧入口恢复行为；生产访问不参与回滚演练。
5. 提交代码、测试和文档，推送 GitHub main；以完整提交 SHA 生成 `git archive --format=tar.gz`，上传同一归档。
6. root 执行 `bash deploy/release.sh <归档> <完整SHA> --no-migrations`。脚本停止 CS2 写入服务和定时任务、备份并检查 SQLite，发布后恢复服务。本版本不执行 Alembic 或 init-db。
7. root 执行新 release 中的 `bash deploy/domain-routing.sh activate <切换备份目录>`：安装域名与默认拒绝站点，禁用旧 IP 站点，更新 Cookie/Host 配置，为 HealthDoc 增加旧 CS2 路径拒绝 include；验证配置后重启 CS2 Web 并平滑重载 Apache。
8. 执行下面的验收矩阵、数据摘要对比、新证书续期演练，核对本地 HEAD、GitHub main 与服务器 current 的完整 SHA 一致。

生产环境字段：`CS2_COOKIE_PATH=/`、`CS2_COOKIE_SECURE=1`、`CS2_TRUSTED_HOSTS=cs2inventory.cn,localhost,127.0.0.1`。保留原密钥，不设置 SESSION_COOKIE_DOMAIN。新域名需要重新登录。

## 验收与失败恢复

- HTTPS 首页、/app、两种详情路由、/static/theme.js、/static/site-footer.css 和 /ready 正常；备案号只出现一次且链接精确。
- HTTP/www 正常路径 308；所有域名上的旧前缀以及 IP/未知 Host 404，无旧入口跳转或 API 泄露；正常域名不允许跳过证书验证。
- HealthDoc 首页、/api/health 与现有服务状态正常；记录应用和通知进程 PID 未变。Web、Worker、两个 timer 正常且两个 timer enabled；不人工触发扫描。
- 证书 SAN 包含裸域名与 www，执行 `certbot renew --cert-name cs2inventory.cn --dry-run`；检查 certbot.timer 和 `/etc/letsencrypt/renewal-hooks/deploy/cs2-reload.sh`。
- 切换尚未验收时失败：root 执行 `bash deploy/domain-routing.sh restore-cutover <切换备份目录>`，恢复切换前的 Apache、环境、旧 release、单元及其运行状态，不覆盖实时数据库。这会恢复旧入口，仅用于本次失败恢复。
- 正式验收后代码回退：`bash deploy/rollback.sh <旧release> <pre-deploy备份> --code-only`，保持新域名路由和环境字段。只有确认数据库损坏或需要恢复有迁移版本时才显式传 `--restore-database`，先停写并验证备份。
- 原始代码归档、SHA256、差异、测试与发布证据保存在工作区外的事务目录；生产配置/数据库备份仅存服务器，不进入 Git。

## 历史发布记录（以下旧 IP、前缀和回滚说明已由上述规范取代）

# 每日双扫描与自然日对比发布验收（2026-09-06）

1. 发布前记录用户、目标、快照和额度账本计数，备份数据库及全部 CS2 Inventory systemd 单元。
2. 执行迁移 `20260906_09`，确认批次增加唯一槽键，历史 Inventory 账本按三倍保守口径转换且业务数据计数未减少。
3. 核对 Timer 同时包含北京时间 07:30 与 20:00，保持 enabled/active；不人工启动扫描服务，避免额外消耗额度。
4. 管理员概览确认容量参考值为 80、额度状态明确显示仅预警不停扫，并能显示 30,000/15,000 两级阈值。
5. 验证 `/health`、`/ready`、Web、Worker、Schedule Timer、Cleanup Timer以及本地、GitHub、服务器 commit SHA 一致；下一次自然调度后再核对批次槽键与成功快照。

仓库通过 `.gitattributes` 强制发布脚本与 systemd 单元使用 LF，保证 Windows 工作区生成的 `git archive` 可在 Linux 服务器直接执行。

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
3. 该版本当时保留账期 9,000 的硬停止线；自 1.3.0 起已由 150,000 账期参考值及 30,000/15,000 只读预警取代，Worker 不再按应用额度拒绝任务。
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
