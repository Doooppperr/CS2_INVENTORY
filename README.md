# CS2 Inventory Monitor

CS2 Inventory 是一个按 SteamID64 监控 CS2 库存的 Web 应用。后端仅通过 Steamwebapi 分别查询实时普通库存和交易保护库存；保护物品优先更新，历史记录不补入当前库存。

正式地址：**https://cs2inventory.cn/**。HTTP 与 `www.cs2inventory.cn` 自动跳转至 HTTPS 裸域名；原 IP 和 `/cs2_inventory/` 入口已退役。切换域名后需要重新登录，主题偏好按新域名保存。

网站页脚统一展示 [沪ICP备2026034136号-2](https://beian.miit.gov.cn/)。同服务器的 HealthDoc 继续由 `healthdoc.cn` 及其现有 www 别名提供服务，两个应用均不再通过 IP 提供访问。

正式源码位于 `src/cs2_inventory/`，测试位于 `tests/`，部署文件位于 `deploy/`，开发与运维文档位于 `开发文档/`。

## 功能

- 公开产品落地页与控制台提供跟随系统、浅色、深色三态主题；浅色保留米色/暖橙复古像素风，深色采用紫黑、霓虹紫和青绿色状态色，控制台可随时返回首页。
- 公开落地页在功能与套餐之间展示使用须知，说明首次库存查询通常需要 1 至 3 分钟，并支持继续提交 SteamID64 进入后台队列。
- 公开注册与试用体系已退役；只有管理员可在用户管理中创建客户账户，并在开户时设置初始密码、月度/年度/永久套餐和监控限额。
- 邀请码用于客户续期、到期后重新激活或升级永久；公开套餐价格仅展示月度 `¥3xx / 月`、年度 `¥2xxx / 年`、永久 `¥xxxx / 永久`，准确价格通过联系邮箱获取。
- 管理员控制台拆为概览、用户管理、全部监控目标；开户与续期升级邀请码归入用户管理板块，完整邀请码只在创建时显示一次。
- 全平台唯一 SteamID 数量持续统计，100 作为控制台参考值但允许超过；多用户共享同一目标的扫描与快照。
- 首次扫描异步执行，每天北京时间 08:00、20:00 自动更新（共 2 轮，正常情况下发现延迟约 12 小时加扫描耗时）；两轮与手动、即时查询共用同一个 API 单源流程，先查询交易保护库存，再查询普通库存。
- 快照分层保留：48 小时内全部保留，其后保留自然日最后完整快照及分区展示所需记录，整体保留八天；支持最新快照与北京时间 1、3、7 个自然日前最后一张快照的新增、移除和数量变化比对。
- 保护物品金色置顶，提供只看交易保护筛选；用户界面只展示库存、实际更新时间和已确认变化。
- 监控列表每页 20 条；用户可设置私有备注，显示为 `备注名 -（Steam昵称）- SteamID64`。
- 账号与监控目标采用永久删除；删除后的预置数据不会在服务重启或每日任务中恢复。
- 列表页码写入 URL，从库存详情返回时恢复原列表和原页码。
- 明确处于交易保护的单件物品在最新库存中独立计数、金色置顶；其余物品按同名组内最新首次发现时间排序，内部时间不对外显示。
- 所有可靠资产优先采用 API 返回的 `schinese` 名称；中文服务短暂失败时复用持久化映射或同资产历史名称，名称语言切换不计入库存变化。

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
python -m pip install -r requirements-test.txt
PYTHONPATH=src python -m unittest discover -s tests -v
```

全量测试包含可选历史 HTML 解析器的夹具，额外依赖 beautifulsoup4；生产 Web/API Worker 继续使用 requirements.txt，测试在独立环境中运行。

> 本仓库不保存 API Key、生产数据库、真实库存响应或发布归档。

完整设计、接口、额度、部署和安全说明见 [`开发文档/`](开发文档/)。

## API 单源口径与诊断

- 保护分区使用 `trade_locked=1,parse=1`，普通分区显式使用 `trade_locked=0,parse=0`，两者均为 `state=active,no_cache=1,with_no_tradable=1,group=0`，完整跟随分页；不使用混合缓存回退、直接 Steam 库存或历史资产补齐。
- 保护分区成功即保存；其他分区失败保留其上次 API 成功结果和真实时间。跨轮分区不相加冒充完整库存；只有同轮两分区都成功才生成完整对比快照。普通分区 `411` 不证明包含不可交易资产的整库为空。
- 新快照使用 `source_policy=api_only_v1`，不与旧混合口径生成确定增减。新 `inventory_sections` 提供分区结果及各自 `updated_at`，`last_complete_snapshot_at` 表示最近完整记录的时间。
- `role=admin` 且用户名为 `cs2inventory_admin` 的账号拥有 `can_view_inventory_diagnostics` 权限；其他账号的库存、任务、历史、对比和日报响应均在后端裁掉诊断，仅保留业务结果。
- 每轮通常两次 API 请求，当前配置估算 6 credits；每天两轮约 12 credits/目标，分页与实际重试另计，额度仍只预警不停扫。
- API context16 检测覆盖交易保护皮肤，不包含刚收到的箱子；保护天数上界不等于准确解锁时间。
