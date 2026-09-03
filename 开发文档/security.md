# 安全

- 登录认证使用 Werkzeug scrypt 哈希；同时使用独立 `CS2_PASSWORD_VAULT_KEY` 保存加密副本，供管理员控制台查看用户改密或重置后的当前密码。
- 历史账号只有哈希，在下一次改密或重置前显示为不可读取；密码副本仅由管理员接口返回且响应禁止缓存。
- 公开注册端点固定无副作用返回 403；登录按 IP/账号限流，所有写请求要求 CSRF Token。
- 管理员开户端点只接受用户名、初始密码、套餐和限额，服务端固定普通客户角色与类型；大小写不敏感的用户名唯一性由开户事务校验。
- 用户数据按订阅关系授权，管理员接口使用独立角色检查。
- API Key、会话密钥、SQLite、快照、真实响应和发布包禁止提交 Git。
- systemd 服务使用独立账号、NoNewPrivileges、PrivateTmp、ProtectSystem 和 ProtectHome。
- 当前生产入口按已确认方案继续使用 IP HTTP；Cookie 的 Secure 开关保留为环境配置，迁移 HTTPS 后设为 `1`。
