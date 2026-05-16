from __future__ import annotations

import json
import time
from dataclasses import dataclass

from ycbot.config import Settings


@dataclass(slots=True)
class CloudCenterOrganizationCreator:
    settings: Settings
    logger: object

    def import_cookies(self, cookie_text: str) -> dict[str, object]:
        cookies = self._parse_cookies(cookie_text)
        if not cookies:
            raise RuntimeError("cookie file has no supported cookies")

        driver = self._build_driver()
        imported = 0
        failed = 0
        domains: set[str] = set()
        try:
            for cookie in cookies:
                domain = str(cookie.get("domain") or "").lstrip(".").strip()
                if not domain:
                    failed += 1
                    continue
                domains.add(domain)
                driver.get(f"https://{domain}/")
                try:
                    driver.add_cookie(cookie)
                    imported += 1
                except Exception:  # noqa: BLE001
                    failed += 1
            driver.get(self.settings.yc_center_url)
            time.sleep(1)
        finally:
            if self._should_quit_driver():
                driver.quit()

        if imported <= 0:
            raise RuntimeError("no cookies were imported into browser profile")
        return {"imported": imported, "failed": failed, "domains": sorted(domains)}

    def create_organization(self, name: str, *, current_organization_name: str | None = None) -> str:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        driver = self._build_driver()
        wait = WebDriverWait(driver, self.settings.yc_center_wait_seconds)
        try:
            driver.get(self.settings.yc_center_url)
            self._close_optional_welcome(driver)
            if not self._open_create_page_from_menu(driver, wait, current_organization_name):
                driver.get(self.settings.yc_center_url.rstrip("/") + "/create")

            name_input = wait.until(
                EC.element_to_be_clickable(
                    (
                        By.XPATH,
                        "//label[contains(normalize-space(), 'Название')]/following::input[1]"
                        " | //input[not(@type) or @type='text']",
                    )
                )
            )
            name_input.clear()
            name_input.send_keys(name)

            create_button = wait.until(
                EC.element_to_be_clickable(
                    (
                        By.XPATH,
                        "//button[contains(normalize-space(), 'Создать новую организацию')]"
                        " | //button[contains(normalize-space(), 'Создать')]",
                    )
                )
            )
            create_button.click()
            wait.until(lambda browser: "/create" not in browser.current_url)
            time.sleep(2)
            return name
        finally:
            if self._should_quit_driver():
                driver.quit()

    def _build_driver(self):
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options

        options = Options()
        if self.settings.yc_center_chrome_binary:
            options.binary_location = self.settings.yc_center_chrome_binary
        if self.settings.yc_center_chrome_debugger_address:
            options.debugger_address = self.settings.yc_center_chrome_debugger_address
        if self.settings.yc_center_chrome_user_data_dir and not self.settings.yc_center_chrome_debugger_address:
            options.add_argument(f"--user-data-dir={self.settings.yc_center_chrome_user_data_dir}")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1440,1200")
        if self.settings.yc_center_selenium_headless and not self.settings.yc_center_chrome_debugger_address:
            options.add_argument("--headless=new")

        if self.settings.yc_center_selenium_remote_url:
            return webdriver.Remote(
                command_executor=self.settings.yc_center_selenium_remote_url,
                options=options,
            )
        return webdriver.Chrome(options=options)

    def _should_quit_driver(self) -> bool:
        if self.settings.yc_center_selenium_quit:
            return True
        return bool(
            self.settings.yc_center_chrome_user_data_dir
            and not self.settings.yc_center_chrome_debugger_address
            and not self.settings.yc_center_selenium_remote_url
        )

    def _open_create_page_from_menu(self, driver, wait, current_organization_name: str | None) -> bool:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC

        menu_xpaths = []
        if current_organization_name:
            menu_xpaths.append(
                "//*[self::button or @role='button']"
                f"[contains(normalize-space(), {self._xpath_literal(current_organization_name)})]"
            )
        menu_xpaths.extend(
            [
                "//*[self::button or @role='button'][contains(normalize-space(), 'Organization')]",
                "//*[self::button or @role='button'][contains(normalize-space(), 'Организация')]",
            ]
        )
        for xpath in menu_xpaths:
            try:
                wait.until(EC.element_to_be_clickable((By.XPATH, xpath))).click()
                break
            except Exception:  # noqa: BLE001
                continue
        else:
            return False

        try:
            wait.until(
                EC.element_to_be_clickable(
                    (
                        By.XPATH,
                        "//*[self::button or @role='button' or self::a]"
                        "[contains(normalize-space(), 'Создать организацию')]",
                    )
                )
            ).click()
            return True
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _close_optional_welcome(driver) -> None:
        from selenium.webdriver.common.by import By

        for xpath in (
            "//button[contains(normalize-space(), 'Закрыть')]",
            "//button[contains(normalize-space(), 'Close')]",
        ):
            try:
                driver.find_element(By.XPATH, xpath).click()
                return
            except Exception:  # noqa: BLE001
                continue

    @staticmethod
    def _xpath_literal(value: str) -> str:
        if "'" not in value:
            return f"'{value}'"
        if '"' not in value:
            return f'"{value}"'
        return "concat(" + ", \"'\", ".join(f"'{part}'" for part in value.split("'")) + ")"

    @classmethod
    def _parse_cookies(cls, cookie_text: str) -> list[dict[str, object]]:
        text = cookie_text.strip()
        if not text:
            return []
        if text.startswith("[") or text.startswith("{"):
            data = json.loads(text)
            if isinstance(data, dict):
                data = data.get("cookies", [])
            if not isinstance(data, list):
                return []
            return [cookie for item in data if (cookie := cls._normalize_json_cookie(item))]
        return [cookie for line in text.splitlines() if (cookie := cls._parse_netscape_cookie_line(line))]

    @staticmethod
    def _normalize_json_cookie(item: object) -> dict[str, object] | None:
        if not isinstance(item, dict):
            return None
        name = item.get("name")
        value = item.get("value")
        domain = item.get("domain")
        if not name or value is None or not domain:
            return None

        cookie: dict[str, object] = {
            "name": str(name),
            "value": str(value),
            "domain": str(domain),
            "path": str(item.get("path") or "/"),
            "secure": bool(item.get("secure", True)),
        }
        if "httpOnly" in item:
            cookie["httpOnly"] = bool(item.get("httpOnly"))
        expires = item.get("expiry", item.get("expirationDate", item.get("expires")))
        if expires not in {None, "", 0, "0"}:
            cookie["expiry"] = int(float(expires))
        same_site = item.get("sameSite")
        if isinstance(same_site, str) and same_site.capitalize() in {"Strict", "Lax", "None"}:
            cookie["sameSite"] = same_site.capitalize()
        return cookie

    @staticmethod
    def _parse_netscape_cookie_line(line: str) -> dict[str, object] | None:
        stripped = line.strip()
        if not stripped:
            return None
        http_only = False
        if stripped.startswith("#HttpOnly_"):
            stripped = stripped[len("#HttpOnly_"):]
            http_only = True
        elif stripped.startswith("#"):
            return None
        parts = stripped.split("\t")
        if len(parts) != 7:
            parts = stripped.split()
        if len(parts) != 7:
            return None
        domain, _include_subdomains, path, secure, expiry, name, value = parts
        if not domain or not name:
            return None
        cookie: dict[str, object] = {
            "name": name,
            "value": value,
            "domain": domain,
            "path": path or "/",
            "secure": secure.upper() == "TRUE",
        }
        if http_only:
            cookie["httpOnly"] = True
        if expiry not in {"0", "-1"}:
            cookie["expiry"] = int(float(expiry))
        return cookie
