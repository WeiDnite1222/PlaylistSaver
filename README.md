# PlaylistSaver

A tool to save your youtube playlist. **VERY UNSTABLE**

## Development

Create a virtual environment and install the project with its build tools:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[build]"
```

Run from source:

```powershell
.\.venv\Scripts\playlist-saver.exe
```

## Build

Build the application with PyInstaller:

```powershell
.\build.ps1 -Clean
```

The application is written to `dist\PlaylistSaver`. The build includes only the
bundled mpv files matching the operating system and CPU architecture on which the
build is performed. Build each target platform on that platform.
