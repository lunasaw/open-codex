"""Discover local Conda environments and capture their activation overrides."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def conda_executable() -> str | None:
    candidates = [os.environ.get('CONDA_EXE'), shutil.which('conda')]
    for root in (Path(sys.prefix), Path.home() / 'miniconda3', Path.home() / 'anaconda3', Path('/opt/conda')):
        candidates.append(str(root / 'bin/conda'))
    return next((path for path in candidates if path and Path(path).is_file() and os.access(path, os.X_OK)), None)


def _run(executable: str, *args: str, marker: str | None = None) -> dict:
    try:
        result = subprocess.run([executable, *args], capture_output=True, text=True, timeout=30, check=True)
        output = result.stdout
        if marker:
            output = next(line[len(marker):] for line in reversed(output.splitlines()) if line.startswith(marker))
        value = json.loads(output)
        if not isinstance(value, dict):
            raise ValueError('invalid conda response')
        return value
    except (OSError, subprocess.SubprocessError, ValueError, StopIteration) as exc:
        raise ValueError('无法读取 Conda 环境，请检查服务器上的 Conda 配置') from exc


def list_environments() -> dict:
    executable = conda_executable()
    if not executable:
        return {'available': False, 'environments': [], 'error': '服务器未找到 Conda，可通过 CONDA_EXE 指定可执行文件。'}
    data = _run(executable, 'info', '--envs', '--json')
    root = data.get('root_prefix', str(Path(executable).resolve().parent.parent))
    environments = []
    for prefix in dict.fromkeys(data.get('envs', [])):
        if isinstance(prefix, str) and Path(prefix).is_absolute():
            environments.append({'name': 'base' if prefix == root else Path(prefix).name, 'prefix': prefix})
    return {'available': True, 'environments': environments}


def activate_environment(prefix: str | None) -> tuple[dict | None, dict[str, str | None]]:
    if prefix is None:
        return None, {}
    if not isinstance(prefix, str) or not prefix:
        raise ValueError('请选择 Conda 环境，或取消绑定')
    inventory = list_environments()
    environment = next((row for row in inventory['environments'] if row['prefix'] == prefix), None)
    if not environment:
        raise ValueError('所选 Conda 环境不存在，请刷新环境列表')
    executable = conda_executable()
    if not executable:
        raise ValueError('服务器未找到 Conda')
    # Use the Host interpreter only to serialize the activated process environment.
    # Conda runs activate.d hooks too; no shell text is constructed from a prefix.
    marker = '__OPEN_CODEX_CONDA_ENV__'
    activated = _run(executable, 'run', '--no-capture-output', '--prefix', prefix,
                     sys.executable, '-c', f'import json, os; print({marker!r} + json.dumps(dict(os.environ)))',
                     marker=marker)
    if not all(isinstance(key, str) and isinstance(value, str) for key, value in activated.items()):
        raise ValueError('invalid conda activation response')
    ignored = {'_', 'PWD', 'OLDPWD', 'SHLVL'}
    variables: dict[str, str | None] = {
        key: value for key, value in activated.items()
        if key not in ignored and os.environ.get(key) != value
    }
    variables.update({key: None for key in os.environ if key not in activated and key not in ignored})
    bins = [prefix, str(Path(prefix) / 'Scripts'), str(Path(prefix) / 'Library/bin')] if os.name == 'nt' else [str(Path(prefix) / 'bin')]
    variables['PATH'] = os.pathsep.join([*bins, *[part for part in activated.get('PATH', '').split(os.pathsep) if part not in bins]])
    return environment, variables


def runtime_overrides(config: dict, variables: dict[str, str | None], *, excludes: list[str]) -> dict:
    """Keep the thread's original overrides and the native global shell policy."""
    result = dict(config)
    result['allow_login_shell'] = False
    for key, value in variables.items():
        if value is not None:
            result[f'shell_environment_policy.set.{key}'] = value
    removed = [key for key, value in variables.items() if value is None]
    if removed:
        configured = config.get('shell_environment_policy.exclude')
        excludes = configured if isinstance(configured, list) else excludes
        result['shell_environment_policy.exclude'] = list(dict.fromkeys([*excludes, *removed]))
    return result
