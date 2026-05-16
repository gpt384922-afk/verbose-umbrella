from __future__ import annotations

import json
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

from ycbot.config import Settings
from ycbot.utils import log_event


@dataclass(slots=True)
class CloudCenterOrganizationCreator:
    settings: Settings
    logger: object

    def import_cookies(self, cookie_text: str, *, proxy_url: str | None = None) -> dict[str, object]:
        parsed_cookies = self._parse_cookies(cookie_text)
        cookies = [
            cookie
            for cookie in parsed_cookies
            if self._is_supported_cookie_domain(str(cookie.get("domain") or ""))
        ]
        if not cookies:
            raise RuntimeError("cookie file has no supported cookies")

        driver = self._build_driver(proxy_url=proxy_url)
        self._configure_driver_timeouts(driver)
        imported = 0
        failed = 0
        cookies_by_domain: dict[str, list[dict[str, object]]] = defaultdict(list)
        for cookie in cookies:
            domain = str(cookie.get("domain") or "").lstrip(".").strip()
            if domain:
                cookies_by_domain[domain].append(cookie)
        try:
            log_event(
                self.logger,
                "center.cookies.import_start",
                parsed=len(parsed_cookies),
                supported=len(cookies),
                domains=len(cookies_by_domain),
            )
            for domain, domain_cookies in sorted(cookies_by_domain.items()):
                try:
                    driver.get(f"https://{domain}/")
                except Exception:  # noqa: BLE001
                    # We only need the browser context to be on the right domain;
                    # many Yandex endpoints block direct loads in headless mode.
                    pass
                domain_imported = 0
                domain_failed = 0
                for cookie in domain_cookies:
                    try:
                        safe_cookie = dict(cookie)
                        safe_cookie["domain"] = str(safe_cookie["domain"])
                        driver.add_cookie(safe_cookie)
                        domain_imported += 1
                    except Exception:  # noqa: BLE001
                        domain_failed += 1
                imported += domain_imported
                failed += domain_failed
                log_event(
                    self.logger,
                    "center.cookies.domain_imported",
                    domain=domain,
                    imported=domain_imported,
                    failed=domain_failed,
                )
            driver.get(self.settings.yc_center_url)
            time.sleep(1)
        finally:
            if self._should_quit_driver():
                driver.quit()

        if imported <= 0:
            raise RuntimeError("no cookies were imported into browser profile")
        log_event(self.logger, "center.cookies.import_done", imported=imported, failed=failed)
        return {"imported": imported, "failed": failed, "domains": sorted(cookies_by_domain)}

    def create_organization(
        self,
        name: str,
        *,
        current_organization_name: str | None = None,
        proxy_url: str | None = None,
    ) -> str:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        driver = self._build_driver(proxy_url=proxy_url)
        wait = WebDriverWait(driver, self.settings.yc_center_wait_seconds)
        try:
            log_event(self.logger, "center.organization.create_start", name=name)
            driver.get(self.settings.yc_center_url)
            log_event(
                self.logger,
                "center.organization.opened",
                url=self._short_url(driver.current_url),
                title=(driver.title or "-")[:120],
            )
            self._raise_if_login_required(driver, "open Cloud Center")
            self._close_optional_welcome(driver)
            if not self._open_create_page_from_menu(driver, wait, current_organization_name):
                log_event(self.logger, "center.organization.menu_fallback")
                driver.get(self.settings.yc_center_url.rstrip("/") + "/create")
            log_event(
                self.logger,
                "center.organization.create_page",
                url=self._short_url(driver.current_url),
                title=(driver.title or "-")[:120],
            )
            self._raise_if_login_required(driver, "open organization create page")

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
            log_event(self.logger, "center.organization.name_filled", name=name)

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
            log_event(self.logger, "center.organization.submit_clicked", name=name)
            wait.until(lambda browser: "/create" not in browser.current_url)
            self._raise_if_login_required(driver, "submit organization create form")
            time.sleep(2)
            log_event(
                self.logger,
                "center.organization.create_done",
                name=name,
                url=self._short_url(driver.current_url),
            )
            return name
        finally:
            if self._should_quit_driver():
                driver.quit()

    def _build_driver(self, *, proxy_url: str | None = None):
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options

        self._cleanup_stale_profile_locks()
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
        options.add_argument("--disable-software-rasterizer")
        options.add_argument("--disable-background-networking")
        options.add_argument("--no-first-run")
        options.add_argument("--no-default-browser-check")
        options.add_argument("--password-store=basic")
        options.add_argument("--use-mock-keychain")
        options.add_argument("--remote-debugging-port=0")
        options.add_argument("--window-size=1440,1200")
        if self.settings.yc_center_selenium_headless and not self.settings.yc_center_chrome_debugger_address:
            options.add_argument("--headless=new")
        self._apply_proxy_options(options, proxy_url)

        log_event(
            self.logger,
            "center.browser.start",
            binary=self.settings.yc_center_chrome_binary or "auto",
            profile=self.settings.yc_center_chrome_user_data_dir or "-",
            headless=self.settings.yc_center_selenium_headless,
            remote=bool(self.settings.yc_center_selenium_remote_url),
            debugger=bool(self.settings.yc_center_chrome_debugger_address),
            proxy=bool(proxy_url),
        )
        if self.settings.yc_center_selenium_remote_url:
            return webdriver.Remote(
                command_executor=self.settings.yc_center_selenium_remote_url,
                options=options,
            )
        return webdriver.Chrome(options=options)

    def _apply_proxy_options(self, options, proxy_url: str | None) -> None:
        if not proxy_url:
            return
        parsed = urlparse(proxy_url if "://" in proxy_url else f"http://{proxy_url}")
        if not parsed.hostname or not parsed.port:
            raise RuntimeError("proxy URL must include host and port")
        scheme = parsed.scheme or "http"
        proxy_server = f"{scheme}://{parsed.hostname}:{parsed.port}"
        options.add_argument(f"--proxy-server={proxy_server}")
        if parsed.username or parsed.password:
            extension_dir = self._write_proxy_auth_extension(parsed)
            options.add_argument(f"--load-extension={extension_dir}")

    def _write_proxy_auth_extension(self, parsed) -> str:
        import tempfile

        extension_dir = tempfile.mkdtemp(prefix="ycbot-proxy-auth-")
        manifest = {
            "version": "1.0.0",
            "manifest_version": 2,
            "name": "YCBot proxy auth",
            "permissions": ["proxy", "tabs", "unlimitedStorage", "storage", "<all_urls>", "webRequest", "webRequestBlocking"],
            "background": {"scripts": ["background.js"]},
            "minimum_chrome_version": "22.0.0",
        }
        username = unquote(parsed.username or "")
        password = unquote(parsed.password or "")
        background = (
            "chrome.webRequest.onAuthRequired.addListener("
            "function(details) { return {authCredentials: {"
            f"username: {json.dumps(username)}, password: {json.dumps(password)}"
            "}}; },"
            "{urls: ['<all_urls>']}, ['blocking']);"
        )
        Path(extension_dir, "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        Path(extension_dir, "background.js").write_text(background, encoding="utf-8")
        return extension_dir

    def _cleanup_stale_profile_locks(self) -> None:
        if (
            not self.settings.yc_center_chrome_user_data_dir
            or self.settings.yc_center_chrome_debugger_address
            or self.settings.yc_center_selenium_remote_url
        ):
            return
        profile_dir = Path(self.settings.yc_center_chrome_user_data_dir)
        profile_dir.mkdir(parents=True, exist_ok=True)
        for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
            path = profile_dir / name
            try:
                if path.exists() or path.is_symlink():
                    path.unlink()
            except OSError:
                continue

    def _configure_driver_timeouts(self, driver) -> None:
        timeout = max(10, min(int(self.settings.yc_center_wait_seconds), 30))
        driver.set_page_load_timeout(timeout)
        driver.set_script_timeout(timeout)

    @staticmethod
    def _raise_if_login_required(driver, action: str) -> None:
        current_url = (driver.current_url or "").lower()
        title = (driver.title or "").lower()
        if "passport.yandex" in current_url or "passport.yandex" in title or "auth" in current_url:
            raise RuntimeError(
                f"Yandex login required while trying to {action}; imported cookies are missing or expired"
            )

    @staticmethod
    def _short_url(url: str) -> str:
        if len(url) <= 180:
            return url
        return url[:177] + "..."

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

    @staticmethod
    def _is_supported_cookie_domain(domain: str) -> bool:
        cleaned = domain.lstrip(".").lower()
        return (
            cleaned == "yandex.ru"
            or cleaned.endswith(".yandex.ru")
            or cleaned == "yandex.com"
            or cleaned.endswith(".yandex.com")
            or cleaned == "yandex.cloud"
            or cleaned.endswith(".yandex.cloud")
        )
