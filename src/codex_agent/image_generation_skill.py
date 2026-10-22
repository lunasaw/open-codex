"""Install the project-bundled image skill with this machine's API binding."""
import json
import re
import shlex
import sys
from pathlib import Path

from .image_generation import MARKER, private_write


def install_skill(source: Path, destination: Path, profile: Path) -> None:
    source, destination = Path(source), Path(destination)
    skill_path = source / 'SKILL.md'
    if (not skill_path.is_file()
            or not re.search(r'^name:\s*["\']?imagegen-api["\']?\s*$',
                             skill_path.read_text(), re.MULTILINE)):
        raise ValueError('项目内缺少完整的 imagegen-api 技能，请更新项目文件。')
    if not (source / 'scripts/_image_gen_original.py').is_file():
        raise ValueError('项目内缺少 imagegen 原始 CLI，请更新项目文件。')
    if (destination / 'SKILL.md').exists() and MARKER not in (destination / 'SKILL.md').read_text():
        raise ValueError('已有同名 imagegen-api 技能，请先处理名称冲突。')

    # This is a complete copy of the versioned project bundle. Only its command
    # text and a private per-instance binding are machine-specific.
    command = shlex.join([sys.executable, str(destination / 'scripts/image_gen.py')])
    for path in sorted(source.rglob('*')):
        relative = path.relative_to(source)
        if '__pycache__' in relative.parts or path.suffix == '.pyc':
            continue
        if path.is_symlink():
            raise ValueError('imagegen 技能资源不能是符号链接。')
        if not path.is_file():
            continue
        data = path.read_bytes()
        if relative == Path('SKILL.md'):
            data = data.replace(b'python scripts/image_gen.py', command.encode())
        private_write(destination / relative, data)
    private_write(destination / 'scripts/_host_binding.json', json.dumps({
        'cli': str(Path(__file__).with_name('image_generation_cli.py')),
        'profile': str(profile),
    }, ensure_ascii=False))
