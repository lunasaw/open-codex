# 会话 Conda 环境

打开会话右侧任务面板，选择“运行环境”，从服务器上的 Conda 环境列表中选择环境，再点击“应用到当前会话”。后续 Agent 执行的 Python、pip 和 Shell 命令，以及该会话新建的底部终端，都会使用此环境。可用 `python -m pip install 包名` 安装到所选环境。

绑定按会话保存，刷新页面、恢复会话或取消归档后重新激活；不同会话可选择不同环境。切换需要等待当前回复结束，聊天记录、模型和权限设置会保留。已启动的进程继续使用其启动时的环境。选择“默认环境（不绑定 Conda）”可以解除绑定。

Host 从 `CONDA_EXE`、`PATH` 及常见安装目录查找 Conda。如果服务未找到 Conda，可在启动服务时设置 `CONDA_EXE=/absolute/path/to/conda`。列表通过 `conda info --envs --json` 读取；环境必须已存在，不会自动创建或安装包。

环境激活通过 `conda run --prefix` 捕获变量，包括 `activate.d` 钩子的设置。Agent 命令使用原生线程的 Shell 环境策略；切换时先取消订阅空闲线程，再以环境覆盖重新恢复相同线程，不修改全局 Conda 或用户 Codex 配置。尚未发送消息的空会话会先通过原生命名接口保存，再重新加载环境；不会插入虚构消息。底部终端由 Host 根据会话身份注入环境，浏览器不保存激活后的变量。

验证：`test_conda_environment.py` 覆盖环境发现、持久化、恢复、切换限制和终端请求重放；`test_conda_environment_browser.cjs` 验证右侧配置与终端交互。设置 `CODEX_SETTINGS_TEST_BINARY=/absolute/path/to/codex` 后，`test_conda_runtime.py` 会在隔离配置目录中调用原生运行时，验证切换与解除绑定对 Python 和 Shell 变量的影响，不调用模型。
