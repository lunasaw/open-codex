"""Execute the bundled API image skill with settings selected in the console."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from codex_agent.image_generation import api_base, read_key, request_headers, validate_options


def run(argv=None):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--profile', type=Path, required=True)
    args, arguments = parser.parse_known_args(argv)
    # Keep existing generated skill invocations working while exposing the
    # complete upstream CLI for generate, edit and generate-batch.
    if not arguments or (arguments[0].startswith('-') and arguments[0] not in {'-h', '--help'}):
        editing = any(arg == '--image' or arg.startswith('--image=') for arg in arguments)
        arguments.insert(0, 'edit' if editing else 'generate')
    profile = json.loads(read_key(args.profile))
    script = Path(profile['script'])
    # Web overrides take effect even if refreshing the generated skill fails.
    # In particular, an old enabled runtime file cannot bypass a later disable.
    settings_file = profile.get('settingsFile')
    if settings_file and Path(settings_file).exists():
        profile = json.loads(read_key(settings_file))
    if not profile.get('enabled'):
        raise ValueError('API 绘图尚未启用，请在设置 → 图像生成中启用。')
    validate_options(profile)
    headers = request_headers(profile)
    env = dict(os.environ)
    env['OPENAI_API_KEY'] = read_key(profile['keyFile']) if profile.get('keyFile') else profile['apiKey']
    if not env['OPENAI_API_KEY']:
        raise ValueError('尚未配置绘图 API Key。')
    env['OPENAI_BASE_URL'] = api_base(profile['baseUrl'])
    env['CODEX_IMAGE_HEADERS'] = json.dumps(profile.get('headers', {}))
    runner = Path(__file__).with_name('image_generation_runner.py')
    if arguments and arguments[0] in {'generate', 'edit', 'generate-batch'}:
        arguments[1:1] = ['--model', profile['model'], '--quality', profile['quality'], '--size', profile['size']]
    command = [sys.executable, str(runner), str(script), *arguments]
    result = subprocess.run(command, env=env, check=False, capture_output=True, text=True)
    # Provider error bodies can echo input. Never return the credential to the
    # model or the observation log, even if an upstream error includes it.
    for output, stream in ((result.stdout, sys.stdout), (result.stderr, sys.stderr)):
        for secret in sorted({env['OPENAI_API_KEY'], *headers.values()} - {''}, key=len, reverse=True):
            output = output.replace(secret, '[hidden]')
        print(output, end='', file=stream)
    return result.returncode


if __name__ == '__main__':
    try:
        raise SystemExit(run())
    except (ValueError, KeyError, OSError):
        print('无法使用已保存的图像配置，请检查设置页和服务端凭据文件。', file=sys.stderr)
        raise SystemExit(1)
