from __future__ import annotations

import os
import platform
import shutil
import subprocess
import time
from pathlib import Path

from ycbot.config import Settings
from ycbot.utils import log_error, log_event


BROWSER_COMMANDS = ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable")
APT_BROWSER_PACKAGES = (
    "chromium",
    "chromium-driver",
    "ca-certificates",
    "fonts-liberation",
    "libnss3",
    "libxss1",
    "xvfb",
)

_XVFB_PROCESS: subprocess.Popen[bytes] | None = None


def ensure_browser_runtime(settings: Settings, logger) -> None:
    if settings.yc_center_chrome_user_data_dir:
        Path(settings.yc_center_chrome_user_data_dir).mkdir(parents=True, exist_ok=True)

    if settings.yc_center_selenium_remote_url or settings.yc_center_chrome_debugger_address:
        return

    needs_xvfb = _needs_virtual_display(settings)
    browser = _configured_or_system_browser(settings)
    xvfb_missing = needs_xvfb and not shutil.which("Xvfb")
    if browser and not xvfb_missing:
        _ensure_virtual_display(needs_xvfb, logger)
        log_event(logger, "browser.runtime.ready", browser=browser, display=os.environ.get("DISPLAY") or "-")
        return

    if not settings.yc_center_auto_install_browser:
        log_event(
            logger,
            "browser.runtime.missing",
            auto_install=False,
            browser=bool(browser),
            xvfb=not xvfb_missing,
        )
        return

    try:
        _install_browser_with_apt(logger)
    except Exception as exc:  # noqa: BLE001
        log_error(logger, "browser.runtime.install_failed", exc)
        return

    browser = _configured_or_system_browser(settings)
    _ensure_virtual_display(needs_xvfb, logger)
    if browser:
        log_event(logger, "browser.runtime.installed", browser=browser, display=os.environ.get("DISPLAY") or "-")
    else:
        log_event(logger, "browser.runtime.install_missing_browser")


def _needs_virtual_display(settings: Settings) -> bool:
    return (
        platform.system().lower() == "linux"
        and not settings.yc_center_selenium_headless
        and not os.environ.get("DISPLAY")
    )


def _ensure_virtual_display(enabled: bool, logger) -> None:
    global _XVFB_PROCESS
    if not enabled:
        return
    if _XVFB_PROCESS is not None and _XVFB_PROCESS.poll() is None:
        return
    xvfb = shutil.which("Xvfb")
    if not xvfb:
        log_event(logger, "browser.xvfb.missing")
        return

    last_returncode: int | None = None
    for display_number in range(99, 105):
        display = f":{display_number}"
        _XVFB_PROCESS = subprocess.Popen(
            [xvfb, display, "-screen", "0", "1440x1200x24", "-nolisten", "tcp"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.5)
        if _XVFB_PROCESS.poll() is None:
            os.environ["DISPLAY"] = display
            log_event(logger, "browser.xvfb.started", display=display)
            return
        last_returncode = _XVFB_PROCESS.returncode

    log_event(logger, "browser.xvfb.failed", returncode=last_returncode)


def _configured_or_system_browser(settings: Settings) -> str | None:
    if settings.yc_center_chrome_binary and Path(settings.yc_center_chrome_binary).exists():
        return settings.yc_center_chrome_binary
    return _system_browser()


def _system_browser() -> str | None:
    for command in BROWSER_COMMANDS:
        path = shutil.which(command)
        if path:
            return path
    return None


def _install_browser_with_apt(logger) -> None:
    if platform.system().lower() != "linux":
        raise RuntimeError("browser auto-install is supported only on Linux")
    if not shutil.which("apt-get"):
        raise RuntimeError("apt-get not found")
    if os.geteuid() != 0:
        raise RuntimeError("browser auto-install requires root privileges")

    log_event(logger, "browser.runtime.install_start", packages=",".join(APT_BROWSER_PACKAGES))
    subprocess.run(["apt-get", "update"], check=True)
    subprocess.run(
        ["apt-get", "install", "-y", "--no-install-recommends", *APT_BROWSER_PACKAGES],
        check=True,
    )
