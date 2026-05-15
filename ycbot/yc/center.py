from __future__ import annotations

import time
from dataclasses import dataclass

from ycbot.config import Settings


@dataclass(slots=True)
class CloudCenterOrganizationCreator:
    settings: Settings
    logger: object

    def create_organization(self, name: str, *, current_organization_name: str | None = None) -> str:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        options = Options()
        if self.settings.yc_center_chrome_binary:
            options.binary_location = self.settings.yc_center_chrome_binary
        if self.settings.yc_center_chrome_debugger_address:
            options.debugger_address = self.settings.yc_center_chrome_debugger_address
        if self.settings.yc_center_chrome_user_data_dir:
            options.add_argument(f"--user-data-dir={self.settings.yc_center_chrome_user_data_dir}")
        if self.settings.yc_center_selenium_headless:
            options.add_argument("--headless=new")

        if self.settings.yc_center_selenium_remote_url:
            driver = webdriver.Remote(
                command_executor=self.settings.yc_center_selenium_remote_url,
                options=options,
            )
        else:
            driver = webdriver.Chrome(options=options)

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
            if self.settings.yc_center_selenium_quit:
                driver.quit()

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
