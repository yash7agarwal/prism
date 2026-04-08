"""
tools/apk_manager.py — APK installation and management

Handles installing, launching, and extracting metadata from APKs.
Requires ADB to be in PATH and a connected device.
"""
from __future__ import annotations
import os
import re
import subprocess
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class ApkError(Exception):
    pass


def _run_adb(args: list[str], serial: str | None = None, timeout: int = 120) -> str:
    """Run an adb command and return stdout. Raises ApkError on failure."""
    cmd = ["adb"]
    if serial:
        cmd += ["-s", serial]
    cmd += args
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            raise ApkError(f"adb {' '.join(args)} failed: {result.stderr.strip()}")
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        raise ApkError(f"adb command timed out: {' '.join(args)}")
    except FileNotFoundError:
        raise ApkError("adb not found in PATH. Install Android SDK Platform Tools.")


def get_package_name(apk_path: str) -> str:
    """Extract package name from APK using aapt or aapt2."""
    path = Path(apk_path)
    if not path.exists():
        raise ApkError(f"APK not found: {apk_path}")
    for tool in ["aapt2", "aapt"]:
        try:
            if tool == "aapt2":
                result = subprocess.run(
                    [tool, "dump", "badging", str(path)],
                    capture_output=True, text=True, timeout=30
                )
            else:
                result = subprocess.run(
                    [tool, "dump", "badging", str(path)],
                    capture_output=True, text=True, timeout=30
                )
            match = re.search(r"package: name='([^']+)'", result.stdout)
            if match:
                return match.group(1)
        except FileNotFoundError:
            continue
    raise ApkError("Could not extract package name. Install aapt or aapt2 (Android SDK Build Tools).")


def get_apk_version(apk_path: str) -> dict:
    """Extract version name and code from APK."""
    path = Path(apk_path)
    if not path.exists():
        raise ApkError(f"APK not found: {apk_path}")
    for tool in ["aapt2", "aapt"]:
        try:
            result = subprocess.run(
                [tool, "dump", "badging", str(path)],
                capture_output=True, text=True, timeout=30
            )
            version_name = re.search(r"versionName='([^']+)'", result.stdout)
            version_code = re.search(r"versionCode='([^']+)'", result.stdout)
            return {
                "version_name": version_name.group(1) if version_name else "unknown",
                "version_code": version_code.group(1) if version_code else "unknown",
            }
        except FileNotFoundError:
            continue
    return {"version_name": "unknown", "version_code": "unknown"}


def install_apk(apk_path: str, serial: str | None = None, reinstall: bool = True) -> str:
    """
    Install APK on device. Returns the package name.
    reinstall=True replaces existing app (keeps data).
    """
    path = Path(apk_path)
    if not path.exists():
        raise ApkError(f"APK not found: {apk_path}")
    package_name = get_package_name(apk_path)
    args = ["install"]
    if reinstall:
        args.append("-r")
    args.append(str(path.resolve()))
    logger.info(f"Installing {path.name} ({package_name})...")
    _run_adb(args, serial=serial, timeout=180)
    logger.info(f"Installed: {package_name}")
    return package_name


def uninstall_apk(package_name: str, serial: str | None = None) -> None:
    """Uninstall an app by package name."""
    _run_adb(["uninstall", package_name], serial=serial)


def launch_app(package_name: str, activity: str | None = None, serial: str | None = None) -> None:
    """Launch an app. If activity not provided, uses the main launcher activity."""
    if activity:
        _run_adb(["shell", "am", "start", "-n", f"{package_name}/{activity}"], serial=serial)
    else:
        _run_adb(["shell", "monkey", "-p", package_name, "-c",
                  "android.intent.category.LAUNCHER", "1"], serial=serial)


def force_stop_app(package_name: str, serial: str | None = None) -> None:
    """Force stop an app."""
    _run_adb(["shell", "am", "force-stop", package_name], serial=serial)


def clear_app_data(package_name: str, serial: str | None = None) -> None:
    """Clear app data (useful for fresh-state testing)."""
    _run_adb(["shell", "pm", "clear", package_name], serial=serial)


def get_installed_version(package_name: str, serial: str | None = None) -> str:
    """Get installed version of a package."""
    output = _run_adb(["shell", "dumpsys", "package", package_name], serial=serial)
    match = re.search(r"versionName=([^\s]+)", output)
    return match.group(1) if match else "unknown"


def list_connected_devices() -> list[str]:
    """List all connected ADB device serials."""
    result = subprocess.run(["adb", "devices"], capture_output=True, text=True)
    lines = result.stdout.strip().split("\n")[1:]  # Skip header
    return [line.split("\t")[0] for line in lines if "\tdevice" in line]
