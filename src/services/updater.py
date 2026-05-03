"""
Updater module for dnotool - checks and downloads updates from GitHub Releases.

Uses a GitHub personal access token for private repo access.
The token is stored as a constant and also in the application config directory.
"""

import os
import sys
import json
import shutil
import platform
from pathlib import Path

try:
    from src.logger import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

REPO = "xp9k/dno-tool"
GITHUB_API = "https://api.github.com"

GITHUB_TOKEN_READ = "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"

try:
    from src import __version__
    CURRENT_VERSION = __version__
except ImportError:
    CURRENT_VERSION = "0.0.0"


def _get_config_dir():
    config_dir_name = ".dnotool"
    if platform.system() == "Windows":
        return Path(os.path.expanduser("~")) / config_dir_name
    return Path(os.path.expanduser("~")) / config_dir_name


def _get_token():
    config_dir = _get_config_dir()
    token_file = config_dir / ".github_token"
    if token_file.exists():
        token = token_file.read_text(encoding="utf-8").strip()
        if token:
            return token
    return GITHUB_TOKEN_READ


def _save_token_to_config(token: str):
    config_dir = _get_config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)
    token_file = config_dir / ".github_token"
    token_file.write_text(token, encoding="utf-8")
    if platform.system() != "Windows":
        os.chmod(str(token_file), 0o600)


def set_github_token(token: str):
    _save_token_to_config(token)


def _get_headers(token: str = None):
    if token is None:
        token = _get_token()
    return {
        "User-Agent": "dnotool-updater",
        "Accept": "application/vnd.github.v3+json",
        "Authorization": f"token {token}",
    }


def _fetch_json(url: str, token: str = None):
    import urllib.request
    import urllib.error

    headers = _get_headers(token)
    req = urllib.request.Request(url, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise PermissionError("Invalid GitHub token.")
        if e.code == 404:
            raise FileNotFoundError("Release not found.")
        raise
    except urllib.error.URLError as e:
        raise ConnectionError(f"Network error: {e.reason}")


def _download_file(url: str, dest: str, token: str = None):
    import urllib.request

    headers = _get_headers(token)
    headers["Accept"] = "application/octet-stream"
    req = urllib.request.Request(url, headers=headers)

    with urllib.request.urlopen(req, timeout=120) as resp:
        with open(dest, "wb") as f:
            shutil.copyfileobj(resp, f)


def download_commands_json(token: str = None) -> dict:
    """Download commands.json from the GitHub repository.

    Returns dict:
        - success: bool
        - message: str
        - data: list or None — parsed JSON data
        - error: str or None
    """
    try:
        url = f"{GITHUB_API}/repos/{REPO}/contents/commands.json"
        headers = _get_headers(token)
        headers["Accept"] = "application/vnd.github.v3.raw"

        import urllib.request
        import tempfile

        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")

        data = json.loads(raw)

        return {
            "success": True,
            "message": "Команды загружены с сервера",
            "data": data,
            "error": None,
        }

    except PermissionError as e:
        return {"success": False, "message": f"Ошибка авторизации: {e}", "data": None, "error": str(e)}
    except ConnectionError as e:
        return {"success": False, "message": f"Сетевая ошибка: {e}", "data": None, "error": str(e)}
    except Exception as e:
        return {"success": False, "message": f"Ошибка загрузки команд: {e}", "data": None, "error": str(e)}


def _is_version_tag(tag: str) -> bool:
    s = tag.lstrip("v")
    parts = s.split(".")
    return len(parts) >= 2 and all(p.isdigit() for p in parts)


def get_version_releases(token: str = None) -> list:
    releases = get_all_releases(token)
    return [r for r in releases if _is_version_tag(r.get("tag_name", ""))]


def get_latest_release(token: str = None) -> dict:
    url = f"{GITHUB_API}/repos/{REPO}/releases/latest"
    release = _fetch_json(url, token)
    tag = release.get("tag_name", "")
    if not _is_version_tag(tag):
        version_releases = get_version_releases(token)
        if not version_releases:
            raise FileNotFoundError("No versioned releases found.")
        version_releases.sort(key=lambda r: [int(x) for x in r["tag_name"].lstrip("v").split(".")])
        release = version_releases[-1]
    return release


def get_all_releases(token: str = None) -> list:
    url = f"{GITHUB_API}/repos/{REPO}/releases"
    return _fetch_json(url, token)


def check_for_update(token: str = None) -> dict:
    """
    Check if a newer version is available.

    Returns dict:
        - update_available: bool
        - current_version: str
        - latest_version: str
        - latest_tag: str
        - release_notes: str
        - error: str or None
    """
    result = {
        "update_available": False,
        "current_version": CURRENT_VERSION,
        "latest_version": CURRENT_VERSION,
        "latest_tag": "",
        "release_notes": "",
        "error": None,
    }

    try:
        release = get_latest_release(token)
        tag = release.get("tag_name", "")
        latest_version = tag.lstrip("v")
        result["latest_tag"] = tag
        result["latest_version"] = latest_version
        result["release_notes"] = release.get("body", "")

        current_parts = [int(x) for x in CURRENT_VERSION.split(".")]
        latest_parts = [int(x) for x in latest_version.split(".")]

        if latest_parts > current_parts:
            result["update_available"] = True

    except PermissionError as e:
        result["error"] = str(e)
    except FileNotFoundError as e:
        result["error"] = str(e)
    except ConnectionError as e:
        result["error"] = str(e)
    except Exception as e:
        result["error"] = f"Unexpected error: {e}"

    return result


def _get_downloads_dir():
    if platform.system() == "Windows":
        downloads = Path(os.environ.get("USERPROFILE", "")) / "Downloads"
    else:
        xdg = os.environ.get("XDG_DOWNLOAD_DIR", "")
        if xdg and Path(xdg).is_dir():
            downloads = Path(xdg)
        else:
            downloads = Path.home() / "Downloads"
    if not downloads.is_dir():
        downloads.mkdir(parents=True, exist_ok=True)
    return downloads


def update(token: str = None) -> dict:
    """Download the latest release archive to the Downloads folder.

    Returns dict:
        - success: bool
        - message: str
        - download_dir: str — path to the folder containing the archive
        - new_version: str
        - error: str or None
    """
    system = platform.system()

    if system == "Linux":
        os_name = "mos"
    elif system == "Windows":
        os_name = "windows"
    else:
        return {
            "success": False,
            "message": f"Unsupported OS: {system}",
            "download_dir": "",
            "new_version": CURRENT_VERSION,
            "error": f"Unsupported OS: {system}",
        }

    try:
        release = get_latest_release(token)
        tag = release.get("tag_name", "")
        latest_version = tag.lstrip("v")
        archive_name = f"dnotool-{latest_version}-{os_name}.zip"

        asset = None
        for a in release.get("assets", []):
            if a.get("name") == archive_name:
                asset = a
                break

        if not asset:
            return {
                "success": False,
                "message": f"Archive {archive_name} not found in release.",
                "download_dir": "",
                "new_version": latest_version,
                "error": f"Archive {archive_name} not found.",
            }

        download_url = asset.get("url") or asset.get("browser_download_url")
        dest_dir = _get_downloads_dir()
        dest_path = dest_dir / archive_name

        logger.info(f"Downloading {archive_name} to {dest_path}...")
        _download_file(download_url, str(dest_path), token)

        return {
            "success": True,
            "message": f"Archive saved to {dest_path}",
            "download_dir": str(dest_dir),
            "new_version": latest_version,
            "error": None,
        }

    except PermissionError as e:
        return {"success": False, "message": f"Auth error: {e}", "download_dir": "", "new_version": CURRENT_VERSION, "error": str(e)}
    except ConnectionError as e:
        return {"success": False, "message": f"Network error: {e}", "download_dir": "", "new_version": CURRENT_VERSION, "error": str(e)}
    except Exception as e:
        return {"success": False, "message": f"Download failed: {e}", "download_dir": "", "new_version": CURRENT_VERSION, "error": str(e)}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="dnotool updater")
    subparsers = parser.add_subparsers(dest="command")

    check_p = subparsers.add_parser("check", help="Check for updates")
    update_p = subparsers.add_parser("update", help="Install latest version")
    token_p = subparsers.add_parser("set-token", help="Save GitHub token")
    token_p.add_argument("token", help="GitHub personal access token")

    args = parser.parse_args()

    if args.command == "check":
        result = check_for_update()
        if result["error"]:
            print(f"Error: {result['error']}")
            sys.exit(1)
        if result["update_available"]:
            print(f"Update available: {result['current_version']} -> {result['latest_version']}")
            if result["release_notes"]:
                print(f"\nRelease notes:\n{result['release_notes']}")
        else:
            print(f"You are up to date ({result['current_version']})")

    elif args.command == "update":
        result = update()
        print(result["message"])
        if not result["success"]:
            sys.exit(1)

    elif args.command == "set-token":
        set_github_token(args.token)
        print(f"Token saved to {_get_config_dir() / '.github_token'}")

    else:
        parser.print_help()