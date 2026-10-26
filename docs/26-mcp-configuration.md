# MCP 配置页

设置中的 MCP 页面提供原生 Codex MCP 配置的结构化编辑器。它覆盖截图中的主要字段：

- STDIO：启动命令、参数、工作目录、环境变量和环境变量透传。
- Streamable HTTP：服务 URL、Token 环境变量、直接 Token、OAuth、HTTP 标头和 HTTP 标头环境变量。
- 已有服务：编辑、启用/停用、删除、重新读取和重新加载。

保存使用原生 `config/batchWrite`，携带用户配置层版本号。发生并发修改时会拒绝写入并保留当前草稿，用户可以重新读取后再保存。未编辑的高级字段和隐藏凭证不会被表单重写；删除服务使用原生的命名条目删除语义。

原生 Codex 支持无显式凭证的 MCP 配置，但是否能连接由 MCP 服务端决定。当前 Galatea MCP 服务启用了 Bearer 认证，因此它不能在没有凭证的情况下工作。推荐在表单中填写 Token 环境变量名称；如果运行中的 Host 没有继承该变量，可以使用直接 Token，或先在 Host 启动环境中提供该变量。直接 Token 会保存为原生 HTTP `Authorization` header，列表、配置读取和观察日志都会遮罩其值。

`env_vars` 只把 Host 环境中的变量透传给 STDIO 子进程，不会自动把变量转换成 HTTP Authorization。HTTP 服务应使用 Token 环境变量、HTTP 标头环境变量、直接 Token 或 OAuth。原生接口没有单独的“试连接”方法，页面保存后请求 MCP 重载，并通过原生 MCP 状态列表显示真实结果。

相关验证：

```sh
npm --prefix frontend run typecheck
npm --prefix frontend test
npm --prefix frontend run build
python -m unittest discover -s tests -p 'test_*.py'
```

固定 Codex 二进制的配置契约测试需要显式提供 `CODEX_SETTINGS_TEST_BINARY`，不会默认启动或修改用户配置。
