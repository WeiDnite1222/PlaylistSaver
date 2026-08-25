from pathlib import Path
import platform


project_dir = Path(SPEC).resolve().parent


def data_tree(source: Path, destination: str):
    """Return PyInstaller data tuples while retaining the directory tree."""
    return [
        (str(path), str(Path(destination) / path.relative_to(source).parent))
        for path in source.rglob("*")
        if path.is_file()
    ]


def bundled_mpv_dir() -> Path:
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "windows":
        os_dir = "nt"
    elif system in {"darwin", "linux"}:
        os_dir = system
    else:
        raise SystemExit(f"Unsupported build platform: {platform.system()}")

    if machine in {"amd64", "x86_64"}:
        arch_dir = "x64"
    elif machine in {"arm64", "aarch64"}:
        arch_dir = "arm"
    elif machine in {"x86", "i386", "i686"}:
        arch_dir = "x86"
    else:
        raise SystemExit(f"Unsupported build architecture: {platform.machine()}")

    return project_dir / "bin" / "mpv" / os_dir / arch_dir


mpv_dir = bundled_mpv_dir()
if not mpv_dir.is_dir():
    raise SystemExit(f"Bundled mpv directory does not exist: {mpv_dir}")

datas = data_tree(project_dir / "assets", "assets")
datas += data_tree(
    mpv_dir,
    str(Path("bin") / "mpv" / mpv_dir.parent.name / mpv_dir.name),
)

a = Analysis(
    [str(project_dir / "main.py")],
    pathex=[str(project_dir)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["customtkinter", "PIL"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PlaylistSaver",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(project_dir / "assets" / "icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="PlaylistSaver",
)

