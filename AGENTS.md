# AGENTS.md

## Project Overview

DNO Tool — a PySide6 (Qt) desktop app for remote SSH administration of MOS/ALT Linux and Windows machines. It sends commands to multiple hosts simultaneously, manages packages/services/users/keys, and provides SFTP, ping, port scanning, and screen recording.

## Tech Stack

- **Python 3** + **PySide6** (Qt for desktop GUI)
- **paramiko** for SSH
- **pyqtgraph** for plots
- **PyInstaller** for building (`dnotool.spec`)
- Dependencies: `requirements.txt` (paramiko, PySide6, pyqtgraph)

## Running

```bash
python __main__.py
```

Entry point is `__main__.py`, which initializes the DI container and launches the Qt app.

## Building

```bash
pyinstaller dnotool.spec
```

Builds a single-file executable. Release script (`scripts/release.sh`) builds Linux + Windows archives and creates GitHub releases. Requires `GITHUB_TOKEN_WRITE` in `.env.tokens`.

## Architecture

- **DI container**: `src/di/container.py` — manual singleton DI with auto-resolve via type hints. Global instance via `get_container()`.
- **Service init**: `src/services/init.py` — `initialize_services()` wires everything: EventBus, DialogManager, WorkerBridge, ViewState, then DeviceService/CommandService/ConfigService.
- **EventBus**: `src/architecture/event_bus.py` — pub/sub for inter-component communication.
- **DataStore**: `src/data/datastore.py` — persistence layer (JSON files for devices, commands, settings).
- **Workers**: `src/workers/` — QThread-based workers for SSH, SFTP, ping, ffmpeg/GStreamer streaming. All inherit `BaseWorker` with abort support.
- **Command execution**: `src/workers/command/` — `executor_base.py` → `ssh.py`, `sftp.py`, `local.py` + `orchestrator.py`.
- **Views**: `src/views/main_window.py` — single main window; `src/ui/` holds all dialogs and widgets.
- **Domain models**: `src/domain/models/` — DeviceModel, CommandModel, Task.
- **Version**: defined in `src/__init__.py` as `__version__`.

## Key Conventions

- Russian-language UI strings and docstrings throughout the codebase.
- `commands.json` is a built-in library of ~150+ predefined SSH/SFTP commands, loaded at runtime. It ships alongside the binary.
- Services extend `BaseService` (`src/services/base.py`) which provides `_execute_safely()` with EventBus error publishing.
- All worker classes inherit `BaseWorker` and use `self._abort_event` for cancellation.
- The app uses PolicyKit (root) for privileged operations on MOS/ALT Linux — see `policykit/` directory.
- No tests exist in the repository.
- No linter/formatter/typecheck config files (no ruff.toml, pyproject.toml, Makefile, etc.).

## File Layout

```
__main__.py              — app entry point
commands.json             — predefined command library (ships with binary)
dnotool.spec              — PyInstaller build spec
requirements.txt          — 3 deps: paramiko, PySide6, pyqtgraph
assets/                   — SVG icons, favicon.ico
policykit/                — Linux PolicyKit files for privilege escalation
scripts/                  — install.sh/ps1, release.sh/ps1, uninstall.sh
src/architecture/         — EventBus, DialogManager, ViewState, WorkerBridge, interfaces
src/config/               — settings.py (SSH/app config, paths), kde_settings.py
src/data/                 — DataStore (JSON persistence)
src/di/                   — DIContainer (manual DI)
src/domain/models/       — DeviceModel, CommandModel, Task
src/domain/utils/        — command_params (parameter substitution)
src/logger/               — logging setup
src/services/             — BaseService, DeviceService, CommandService, ConfigService, Updater
src/ui/dialogs/           — all dialog windows (command, device, tools, kde, editors)
src/ui/widgets/           — reusable Qt widgets
src/views/                — MainWindow
src/workers/              — SSH, SFTP, local, ping, network, ffmpeg/gstreamer, key installer
```

## Environment

- `.env.tokens` holds `GITHUB_TOKEN_WRITE` for releases (gitignored).
- The `.venv/` directory is the Python virtual environment (gitignored).
- `build/`, `dist/`, `versions/` are gitignored build artifacts.