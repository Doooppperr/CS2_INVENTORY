# 安全

- 密码仅使用 Werkzeug scrypt 哈希保存；API、数据库和日志不返回密码哈希。
- 注册和登录按 IP/账号限流；写请求要求 CSRF Token。
- 用户数据按订阅关系授权，管理员接口使用独立角色检查。
- API Key、会话密钥、SQLite、快照、真实响应和发布包禁止提交 Git。
- systemd 服务使用独立账号、NoNewPrivileges、PrivateTmp、ProtectSystem 和 ProtectHome。
- 当前生产入口按已确认方案继续使用 IP HTTP；Cookie 的 Secure 开关保留为环境配置，迁移 HTTPS 后设为 `1`。
