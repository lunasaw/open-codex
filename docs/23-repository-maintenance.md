# 仓库维护

## 来源与边界

Open Codex 独立维护通用 Codex Host、网页工作台、原生协议桥、恢复逻辑与扩展接入。
Galatea 的训练治理、业务 App、数据与运行状态继续由 Galatea 管理。

来源是 `lunasaw/Galatea` 的 `agents/codex-app-server`，提取自提交
`16a4d2918c27ab3df7b55803732af0aeb1abb5b5`。使用 `git subtree split` 保留该目录的
22 个提交及作者信息；路径重写使提交 SHA 改变。提取后的最新提交对应
`b19a9dc409a6d74837612f6ac01e88040ab9f595`，其文件与来源目录一致。
随后独立维护提交补齐 Apache 2.0 许可证、忽略规则、文档和默认工作目录。

保留 Python 包名 `codex-agent-host`、导入名 `codex_agent`、命令 `codex-agent`、
配置版本 `codex-agent/v2`，因此现有调用方无需改名。首次启动的工作目录默认是本项目根目录；
`CODEX_AGENT_WORKSPACE` 可以覆盖，已生成的配置不会被重写工作目录。

## 本地验证

Python 3.11+、Node.js 24：

```sh
python -m pip install -e '.[test]'
python -m unittest discover -s tests -p 'test_*.py'
npm --prefix frontend ci
npm --prefix frontend test
npm --prefix frontend run build
```

CI 在 Linux 上运行上述测试和前端构建。原生运行时及浏览器集成验证见
[测试验收](09-test-acceptance.md)；不把需要额外运行环境的跳过项视为已验证。
不要提交 `.local/`、凭据、运行数据库、Python 缓存或 `node_modules/`。

## Galatea 的固定版本集成

Galatea 使用路径 `agents/codex-app-server`，通过 Git submodule 固定本仓库的提交：

```sh
git clone --recurse-submodules https://github.com/lunasaw/Galatea.git
cd Galatea
# 已有克隆：先切换到包含 submodule 迁移的分支，再执行此命令。
git submodule update --init --recursive
CODEX_AGENT_WORKSPACE="$PWD" sh agents/codex-app-server/start.sh
```

功能修改在本仓库提交和评审。Galatea 升级到选定提交时：

```sh
git -C agents/codex-app-server fetch origin
git -C agents/codex-app-server checkout --detach <verified-commit>
# 运行 Host 和 Galatea App 的集成测试后提交指针。
git add agents/codex-app-server
git commit -m "chore(agent): update open-codex revision"
```

不要把 `git submodule update --remote` 当作部署步骤；常规拉取后执行
`git submodule update --init --recursive` 恢复主仓库固定版本。GitHub 源码 ZIP 和
普通 `git archive` 不包含 submodule 内容，部署和 CI 必须递归克隆或显式初始化。
切换现有工作区前，先单独备份 `.local/` 等被忽略的状态，确认没有未提交组件改动。
