# -*- mode: python ; coding: utf-8 -*-
"""文序 Windows 打包 spec（onedir，控制台窗口显示运行状态，关闭即退出）。"""
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = [
    ('web/index.html', 'web'),
    ('web/style.css', 'web'),
    ('web/js', 'web/js'),
    ('app/review/packs', 'app/review/packs'),
    ('app/review/sensitive_words.txt', 'app/review'),
]
binaries = []
hiddenimports = collect_submodules('uvicorn')
hiddenimports += collect_submodules('app.review')
hiddenimports += collect_submodules('app.domains')
for pkg in ('docx',):
    r = collect_all(pkg)
    datas += r[0]
    binaries += r[1]
    hiddenimports += r[2]

a = Analysis(
    ['app/cli.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'numpy', 'pandas', 'tkinter', 'IPython'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Wensu',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Wensu',
)
