# PlaylistSaver

A tool to save your YouTube playlist.

## Run from source

Sync project and install dependencies.
```bash
uv sync
```

Run from source:

```bash
python main.py
```

## Build

> [!IMPORTANT]
> This project does not support Nuitka for building. Some packages used in this project, such as mpv and yt-dlp, 
> may not be able to function or may cause crashes in the building process.

Install build tools:
```bash
uv pip install -e ".[build]"
```

Build this project with PyInstaller:

```powershell
.\build.ps1 -Clean
```

The build is written to `dist\PlaylistSaver`. This includes only the
bundled mpv files matching the operating system and CPU architecture on which the
build is performed. Build each target platform on that platform.

## How to use