"""打包給別人用：產生 dist/ddr-step-analyzer_v<版本>.zip（版本在 ddrpose/__init__.py）。

只放程式與說明（約數百 KB）；不含 data/（分析結果）、.venv/（對方安裝時自己建）、模型（安裝時自動下載）。
用法：python tools/make_package.py（或雙擊 make_package.bat）
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INCLUDE = ['server.py', 'requirements.txt', 'setup.bat', 'start.bat', 'README.md', 'README.zh-TW.md', 'README.en.md', 'LICENSE',
           'はじめにお読みください.txt', 'README_FIRST.txt', 'ddrpose', 'web', 'tools/setup.ps1']


def main():
    sys.stdout.reconfigure(errors='replace')
    sys.path.insert(0, str(ROOT))
    from ddrpose import __version__
    out = ROOT / 'dist' / f'ddr-step-analyzer_v{__version__}.zip'
    out.parent.mkdir(exist_ok=True)
    n = 0
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for item in INCLUDE:
            p = ROOT / item
            if not p.exists():
                raise SystemExit(f'缺少檔案：{p}')
            files = [p] if p.is_file() else sorted(f for f in p.rglob('*') if f.is_file() and '__pycache__' not in f.parts)
            for f in files:
                z.write(f, Path('ddr-step-analyzer') / f.relative_to(ROOT))
                n += 1
    print(f'{out}（{n} 個檔案，{out.stat().st_size / 1024:.0f} KB）')


if __name__ == '__main__':
    main()
