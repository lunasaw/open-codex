"""Portable entrypoint installed with a per-host API binding by the Host."""
import json
from pathlib import Path
import runpy
import sys


def main():
    binding_path = Path(__file__).with_name('_host_binding.json')
    if not binding_path.is_file():
        raise SystemExit('请先在 Host 中启用图像生成，再调用已安装技能目录中的入口。')
    binding = json.loads(binding_path.read_text(encoding='utf-8'))
    cli = binding['cli']
    profile = binding['profile']
    sys.argv = [cli, '--profile', profile, *sys.argv[1:]]
    runpy.run_path(cli, run_name='__main__')


if __name__ == '__main__':
    main()
