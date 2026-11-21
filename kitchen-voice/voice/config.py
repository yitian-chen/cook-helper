import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env', override=False)
RUNTIME = ROOT / 'runtime'

def number(name, default, low=1, high=120):
    value = float(os.getenv(name, str(default)))
    if not low <= value <= high:
        raise ValueError(f'{name} 必须在 {low}～{high} 之间')
    return value

def device(name):
    value = os.getenv(name, '').strip()
    return int(value) if value.isdigit() else value or None

def artifact(name):
    """API only accepts generated filenames, never arbitrary filesystem paths."""
    if Path(name).name != name or '/' in name or '\\' in name or name in ('.', '..'):
        raise ValueError('只接受 runtime 中的文件名')
    path = (RUNTIME / name).resolve()
    if path.parent != RUNTIME.resolve():
        raise ValueError('文件路径超出 runtime')
    return path
