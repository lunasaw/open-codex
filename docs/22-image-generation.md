# 图像生成设置

## 配置文件与生效方式

`agent.json` 支持顶层 `image_generation`，模板见
[agent.example.json](../config/agent.example.json)。内置默认值为不启用、复用聊天连接、
`gpt-image-2`、中等质量、`1024x1024`。
下面是独立 Images API 的配置片段，合并到现有 `agent.json`，保留其他配置：

```json
{
  "image_generation": {
    "enabled": true,
    "connection": "custom",
    "base_url": "https://images.example.com/v1",
    "api_key_file": "image-api-key",
    "model": "gpt-image-2",
    "headers": {},
    "quality": "medium",
    "size": "1024x1024"
  }
}
```

`api_key_file` 只写密钥文件路径，不在 JSON 中写 Key；文件内容是 API Key 本身，
权限设为 `0600`。相对路径以 `agent.json` 所在目录为基准。
`connection: "current"` 使用聊天的默认服务，省略 `base_url` 和 `api_key_file`；
聊天网关必须支持 Images API 才能出图。

开发实例配置通常位于 `.local/agent.json`，修改配置文件后重启 `sh start.sh`。
启动时会生成或恢复 `imagegen-api` Skill 并刷新原生 Skill 列表；正式实例也可使用配置文件默认值，
不需要先打开网页保存。正式配置变更仍须按发布流程重新接纳。

优先级为：网页已保存设置 > `agent.json` 默认值 > 内置默认值。网页覆盖保存在
`<paths.state_dir>/console/image-generation.json`；需要完全恢复配置文件默认值时，
停止服务、将该覆盖文件移到备份位置后重启。启动产生的
`console/image-generation-runtime.json` 是 Skill 实际读取的配置，不能作为配置入口编辑。
网页保存立即生效，重启保留；无需修改聊天模型。

## 网页设置

设置中的「图像生成」用于配置会话可以显式调用的 API 绘图入口。它采用与「配置」相同的逐行设置布局，保存以下内容：

- 是否启用 API 绘图；
- 复用当前聊天连接，或填写独立的 Images API 地址和 Key；
- GPT Image 模型、质量和尺寸；
- 可选的额外请求头（JSON 对象），用于模型列表、连接检查、生成和编辑请求；
- 连接检查返回的模型列表及服务端权限结果。

选择「复用默认聊天连接」时，Host 只读取当前 OpenAI provider 的地址，并引用已有的仅所有者可读密钥文件。浏览器不会收到 Key，生成的 Skill 也不会包含 Key。选择独立连接时，Key 仅写入 Host 状态目录的 0600 配置文件；更换地址后必须重新输入对应的 Key。

独立连接支持 HTTP / HTTPS 网关，填写以 `/v1` 结尾的基础地址；生成图片使用 `/images/generations`，不使用 `/chat/completions`。未提供 `/models` 的网关仍可以手动指定模型，连接检查会继续检查图像接口。

Higress 等网关可能需要通过请求头选择模型，尤其是使用 multipart 上传的
`/images/edits`。在 `image_generation` 或网页「额外请求头」中配置：

```json
{
  "x-higress-llm-model": "{model}"
}
```

此对象放入配置文件的 `headers` 字段。`{model}` 在每次请求时替换为当前绘图模型，
例如 `gpt-image-2.5-flare`；也可填写固定字符串。未配置时默认为 `{}`，兼容现有连接。
该字段只用于绘图，不会更改聊天连接。生成与编辑在独立适配进程中通过 SDK 的
`extra_headers` 传递；按每次请求的实际模型解析 `{model}`，包括显式模型参数和批量任务中的模型覆盖。
原生 multipart 编码仍由 SDK 处理，无需修改原版 imagegen 脚本或 SDK。

请求头名称不能重复（不区分大小写），值必须是可打印 ASCII 字符串；最多 32 项、
单值最多 4096 字符、总长最多 16384 字符。不能覆盖 `Authorization`、`Content-Type`、
`Host` 等认证或 HTTP 传输头。额外请求头会在设置页显示，只填写非敏感网关选项；
凭据继续使用 API Key 或 `api_key_file`。

完整的 `imagegen-api` 保存在项目源码
[`src/codex_agent/skills/imagegen-api`](../src/codex_agent/skills/imagegen-api)，并随 wheel 和源码包分发。
它包含完整工作流、提示词规范、输入图角色、约束、检查与迭代、全部 references、assets、界面元数据、许可证及未修改的原 CLI。
API 适配只改变执行入口和连接方式，不改变图像工作流。每个实例启用时，Host 将项目内技能复制到自己的 Codex Home，并生成指向本机配置的私有绑定；运行副本位于 `.local` 或对应实例的 Codex Home，重启时可从项目安装包重新生成。
部署机器不需要访问开发机的 `/root/.codex` 技能目录。

聊天中可引用 `$imagegen-api`。启用此设置代表已选择配置的 API 执行方式；技能仍按原版流程处理用户授权的图像请求。
副本中的 `scripts/image_gen.py` 自动读取受保护的模型、地址、凭据和请求头，完整透传
`generate`、`edit`、`generate-batch` 的参数，包括提示词文件、多参考图、蒙版、格式和批量选项。
网页保存的模型、尺寸和质量作为默认值，显式 CLI 参数优先。不要自行另写 SDK 调用脚本。

例如使用技能正文给出的 Python 解释器执行以下命令（路径以当前实例为准）：

```bash
python <codex_home>/skills/imagegen-api/scripts/image_gen.py generate --prompt-file prompt.txt --out output/imagegen/cat.png
python <codex_home>/skills/imagegen-api/scripts/image_gen.py edit --image cat.png --mask mask.png --prompt "Change only the background" --out output/imagegen/cat-v2.png
python <codex_home>/skills/imagegen-api/scripts/image_gen.py generate-batch --input jobs.jsonl --out-dir output/imagegen
```

保存设置不会自动生成图片；实际调用仍受原生沙盒、网络批准、服务商额度和分组权限约束。
连接检查只读取模型并发送空提示词的参数校验请求，因此不会把检查结果误报为已经成功出图。

运行 `python -m unittest discover -s tests -p test_image_generation.py` 会验证项目内完整技能副本及原 CLI。相关测试只调用本地模拟网关，覆盖生成、编辑、批量、模型路由、原版参数和资产保护；不会请求真实绘图服务。

当前网关如果能列出 `gpt-image-*` 模型，但在 Images API 返回“分组未开启图像生成”，仍需在网关为该 Key 所属分组开通绘图权限；能读取模型列表并不代表能出图。页面会保留这个明确错误，便于区分配置问题与服务端授权问题。

正式实例的发布配置为只读；开发模式保存后会刷新原生 Skill 列表。更换聊天模型或 provider 后，需要回到该页保存一次，让「复用默认聊天连接」同步新的地址和凭据来源。
