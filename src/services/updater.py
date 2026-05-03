"""
Updater module for dnotool - checks and downloads updates from GitHub Releases.

Uses a GitHub personal access token for private repo access.
The token is stored as a constant and also in the application config directory.
"""

import os
import sys
import json
import tempfile
import zipfile
import shutil
import platform
import subprocess
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


def update(token: str = None) -> dict:
    """
    Download and install the latest version.

    Returns dict:
        - success: bool
        - message: str
        - new_version: str
        - error: str or None
    """
    system = platform.system()

    if system == "Linux":
        os_name = "mos"
        binary_name = "dnotool"
    elif system == "Windows":
        os_name = "windows"
        binary_name = "dnotool.exe"
    else:
        return {
            "success": False,
            "message": f"Unsupported OS: {system}",
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
                "new_version": latest_version,
                "error": f"Archive {archive_name} not found.",
            }

        download_url = asset.get("url") or asset.get("browser_download_url")

        tmpdir = tempfile.mkdtemp(prefix="dnotool-update-")
        archive_path = os.path.join(tmpdir, archive_name)

        logger.info(f"Downloading {archive_name}...")
        _download_file(download_url, archive_path, token)

        with zipfile.ZipFile(archive_path, "r") as zf:
            zf.extractall(os.path.join(tmpdir, "extracted"))

        config_dir = _get_config_dir()
        config_dir.mkdir(parents=True, exist_ok=True)

        commands_src = os.path.join(tmpdir, "extracted", "commands.json")
        commands_dst = config_dir / "commands.json"
        if os.path.exists(commands_src) and not commands_dst.exists():
            shutil.copy2(commands_src, str(commands_dst))
            logger.info("Installed default commands.json")

        if system == "Windows":
            install_dir = Path(os.environ.get("ProgramFiles", "C:\\Program Files")) / "dnotool"
            install_dir.mkdir(parents=True, exist_ok=True)

            src_binary = os.path.join(tmpdir, "extracted", binary_name)
            dst_binary = install_dir / binary_name
            shutil.copy2(src_binary, str(dst_binary))

            shutil.rmtree(tmpdir, ignore_errors=True)

            return {
                "success": True,
                "message": f"dnotool {latest_version} installed to {dst_binary}. Restart the application.",
                "new_version": latest_version,
                "error": None,
            }
        else:
            install_dir = Path("/usr/local/bin")
            src_binary = os.path.join(tmpdir, "extracted", binary_name)

            try:
                dst_binary = install_dir / binary_name
                shutil.copy2(src_binary, str(dst_binary))
                os.chmod(str(dst_binary), 0o755)
            except PermissionError:
                subprocess.run(["sudo", "cp", src_binary, str(dst_binary)], check=True)
                subprocess.run(["sudo", "chmod", "+x", str(dst_binary)], check=True)

            extracted_dir = os.path.join(tmpdir, "extracted")
            policykit_dir = os.path.join(extracted_dir, "policykit")
            if os.path.isdir(policykit_dir):
                polkit_actions = Path("/usr/share/polkit-1/actions")
                applications = Path("/usr/share/applications")
                admin_src = os.path.join(policykit_dir, "dnotool-admin")

                try:
                    shutil.copy2(
                        os.path.join(policykit_dir, "com.dnotool.policy"),
                        str(polkit_actions),
                    )
                    shutil.copy2(
                        os.path.join(policykit_dir, "com.dnotool.pkexec.desktop"),
                        str(applications),
                    )
                    shutil.copy2(
                        os.path.join(policykit_dir, "com.dnotool.desktop"),
                        str(applications),
                    )
                    shutil.copy2(admin_src, "/usr/bin/")
                    os.chmod("/usr/bin/dnotool-admin", 0o755)
                except PermissionError:
                    subprocess.run(["sudo", "cp", os.path.join(policykit_dir, "com.dnotool.policy"), str(polkit_actions)], check=True)
                    subprocess.run(["sudo", "cp", os.path.join(policykit_dir, "com.dnotool.pkexec.desktop"), str(applications)], check=True)
                    subprocess.run(["sudo", "cp", os.path.join(policykit_dir, "com.dnotool.desktop"), str(applications)], check=True)
                    subprocess.run(["sudo", "cp", admin_src, "/usr/bin/"], check=True)
                    subprocess.run(["sudo", "chmod", "+x", "/usr/bin/dnotool-admin"], check=True)

            uninstall_src = os.path.join(extracted_dir, "uninstall.sh")
            if os.path.exists(uninstall_src):
                uninstall_dst = "/usr/local/bin/dnotool-uninstall.sh"
                try:
                    shutil.copy2(uninstall_src, uninstall_dst)
                    os.chmod(uninstall_dst, 0o755)
                except PermissionError:
                    subprocess.run(["sudo", "cp", uninstall_src, uninstall_dst], check=True)
                    subprocess.run(["sudo", "chmod", "+x", uninstall_dst], check=True)

            shutil.rmtree(tmpdir, ignore_errors=True)

            return {
                "success": True,
                "message": f"dnotool {latest_version} installed to {dst_binary}. Restart the application.",
                "new_version": latest_version,
                "error": None,
            }

    except PermissionError as e:
        return {"success": False, "message": f"Auth error: {e}", "new_version": CURRENT_VERSION, "error": str(e)}
    except ConnectionError as e:
        return {"success": False, "message": f"Network error: {e}", "new_version": CURRENT_VERSION, "error": str(e)}
    except Exception as e:
        return {"success": False, "message": f"Update failed: {e}", "new_version": CURRENT_VERSION, "error": str(e)}


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