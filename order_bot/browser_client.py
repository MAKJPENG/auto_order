from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .models import Order, OrderAttemptResult
from .paths import app_data_dir, browser_cache_dir, log_dir


DEFAULT_PAYMENT_METHOD = "bank_transfer"
_OPEN_FAILURE_SESSIONS: list[dict[str, object]] = []

PAYMENT_METHOD_VALUES = {
    "bank_transfer": ["bank_transfer", "bank-transfer", "bankTransfer", "bacs", "manual"],
    "popular_payments": ["popular_payments", "popular-payments", "card", "stripe"],
    "card": ["popular_payments", "popular-payments", "card", "stripe"],
    "cod": ["cod", "cash_on_delivery"],
}

PAYMENT_METHOD_LABELS = {
    "bank_transfer": ["Bank Transfer", "Bank transfer", "Direct bank transfer"],
    "popular_payments": ["Popular payments options", "Popular payments", "Credit card"],
    "card": ["Popular payments options", "Popular payments", "Credit card"],
    "cod": ["Cash on delivery"],
}

ADD_TO_BAG_SELECTORS = [
    ".block-product__button-wrapper button.block-product__button--primary[data-qa='productsection-btn-addtobag']",
    ".block-product__button-wrapper button[data-qa='productsection-btn-addtobag']",
    ".block-product__main-info button[data-qa='productsection-btn-addtobag']",
    "button[data-qa='productsection-btn-addtobag']",
    "button.block-product__button--primary:has-text('Add to bag')",
    "button:has-text('Add to bag')",
    "button.single_add_to_cart_button[name='add-to-cart']",
    "button.single_add_to_cart_button",
    "form.cart button.single_add_to_cart_button",
    "button[name='add-to-cart']",
    "button[type='submit'][name='add-to-cart']",
    "form.cart button[type='submit']",
    ".single_add_to_cart_button.button.alt",
    "button:has-text('Add to cart')",
    "button:has-text('Add to Cart')",
    "button:has-text('ADD TO CART')",
    "text=Add to bag",
    "text=Add to cart",
    "text=ADD TO CART",
]

CART_CHECKOUT_SELECTORS = [
    ".wc-proceed-to-checkout a.checkout-button",
    ".wc-proceed-to-checkout a[href*='checkout']",
    "a.checkout-button.button.alt.wc-forward",
    "p.woocommerce-mini-cart__buttons a.checkout.wc-forward",
    ".woocommerce-mini-cart__buttons a.checkout",
    ".widget_shopping_cart_content a.checkout",
    "a.checkout.wc-forward[href*='checkout']",
    "a.button.checkout.wc-forward",
    "button[data-qa='shoppingcart-btn-checkout']",
    "[data-qa='shoppingcart-btn-checkout']",
    "[data-qa*='shoppingcart'][data-qa*='checkout']",
    "a.checkout-button",
    "a.wc-forward[href*='checkout']",
    "a[href*='checkout']",
    "button[data-qa*='checkout']",
    "[data-qa*='checkout']",
    "button:has-text('Checkout')",
    "button:has-text('Check out')",
    "button:has-text('Proceed to checkout')",
    "a:has-text('Checkout')",
    "text=Checkout",
    "text=Check out",
]

VIEW_CART_SELECTORS = [
    ".woocommerce-message a.wc-forward:has-text('View cart')",
    ".woocommerce-message a.button:has-text('View cart')",
    "a.added_to_cart.wc-forward",
    "a.button.wc-forward:has-text('View cart')",
    "a[href*='cart']:has-text('View cart')",
    "text=View cart",
]

CART_TOGGLE_SELECTORS = [
    ".ast-site-header-cart a",
    ".ast-site-header-cart",
    ".ast-menu-cart-outline",
    ".ast-cart-menu-wrap",
    ".astra-icon.ast-icon-shopping-basket",
    ".ast-icon-shopping-basket",
    ".ast-icon.icon-basket",
    "[data-cart-total]",
    "a.cart-contents",
    ".cart-contents",
    "a[href*='cart'] .astra-icon",
    "a[href*='cart']",
    "button:has-text('Cart')",
    "text=Cart",
]

CART_ITEM_SELECTORS = [
    ".woocommerce-mini-cart li.woocommerce-mini-cart-item",
    ".woocommerce-mini-cart .mini_cart_item",
    ".widget_shopping_cart_content .mini_cart_item",
    ".cart_list .mini_cart_item",
    "[data-qa*='shoppingcart'] [data-qa*='item']",
]

CART_CLOSE_SELECTORS = [
    ".astra-cart-drawer-close",
    ".ast-cart-close",
    ".ast-icon-close",
    ".drawer-close",
    ".cart-drawer-close",
    "button[aria-label*='Close' i]",
    "button:has-text('Close')",
]

ADD_TO_CART_DONE_SELECTORS = [
    "a.added_to_cart",
    "a.added_to_cart.wc-forward",
    ".woocommerce-message a.wc-forward:has-text('View cart')",
    ".woocommerce-message:has-text('added to your cart')",
    ".woocommerce-message:has-text('has been added')",
    ".woocommerce-notices-wrapper:has-text('added')",
    ".woocommerce-mini-cart__buttons a.checkout",
    ".widget_shopping_cart_content a.checkout",
]

PLACE_ORDER_SELECTORS = [
    "#place_order",
    "button[name='woocommerce_checkout_place_order']",
    "button[type='submit']:has-text('Place an order')",
    "button[type='submit']:has-text('Place order')",
    "button[type='submit']:has-text('Complete order')",
    "button[type='submit']:has-text('Pay now')",
    "button:has-text('Place an order')",
    "button:has-text('Place order')",
    "button:has-text('Complete order')",
    "button:has-text('Pay now')",
    "text=Place an order",
    "text=Place order",
    "text=Complete order",
    "text=Pay now",
]


class DryRunOrderClient:
    def place_order(self, order: Order) -> OrderAttemptResult:
        return OrderAttemptResult(
            success=True,
            submitted=False,
            message="dry-run only; no browser action was taken",
            details={"order_id": order.order_id, "product_urls": order.product_urls},
        )


class BrowserOrderClient:
    def __init__(
        self,
        *,
        headless: bool = False,
        slow_mo_ms: int = 0,
        review_seconds: int = 120,
        payment_method: str = DEFAULT_PAYMENT_METHOD,
        keep_open_on_failure: bool = False,
        allow_detected_country_on_mismatch: bool = False,
        log_callback: Callable[[str], None] | None = None,
    ):
        self.headless = headless
        self.slow_mo_ms = slow_mo_ms
        self.review_seconds = review_seconds
        self.payment_method = normalize_payment_method(payment_method)
        self.keep_open_on_failure = keep_open_on_failure
        self.allow_detected_country_on_mismatch = allow_detected_country_on_mismatch
        self.log_callback = log_callback

    def place_order(self, order: Order, *, submit_final: bool = False) -> OrderAttemptResult:
        self._configure_packaged_playwright()
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            return self._exception_failure(
                None,
                order,
                "Playwright 依赖未安装，安装包可能不完整，请重新下载安装包",
                exc,
            )

        playwright = None
        browser = None
        context = None
        page = None
        result: OrderAttemptResult | None = None
        try:
            playwright = sync_playwright().start()
            self._ensure_playwright_browser(playwright)
            browser = playwright.chromium.launch(
                headless=self.headless,
                slow_mo=self.slow_mo_ms,
            )
            context = browser.new_context()
            page = context.new_page()
            product_urls = order.product_urls
            if not product_urls:
                result = self._failure(page, "product_url is empty")
                return result
            for product_index, product_url in enumerate(product_urls, start=1):
                page.goto(product_url, wait_until="domcontentloaded", timeout=60000)
                self._wait_for_product_page(page, PlaywrightTimeoutError)

                self._set_quantity(page, order.quantity)

                is_last_product = product_index == len(product_urls)
                if not self._click_add_to_bag(page, PlaywrightTimeoutError, allow_cart_open=is_last_product):
                    result = self._failure(
                        page,
                        f"product {product_index}/{len(product_urls)} add-to-bag did not add product to shopping bag after retries",
                    )
                    result.details["product_url"] = product_url
                    result.details["product_index"] = product_index
                    return result

            if not self._click_checkout_from_bag(page, PlaywrightTimeoutError):
                result = self._failure(page, "checkout button not found in shopping bag")
                return result

            self._wait_for_checkout_page(page, PlaywrightTimeoutError)
            if not self._fill_checkout(page, order):
                result = self._failure(page, self._country_not_found_message(order))
                return result
            self._choose_payment_method(page, self._payment_method_for(order))

            if submit_final:
                missing_fields = self._missing_required_checkout_fields(page, order)
                if missing_fields:
                    if not self._fill_checkout(page, order):
                        result = self._failure(page, self._country_not_found_message(order))
                        return result
                    page.wait_for_timeout(500)
                    missing_fields = self._missing_required_checkout_fields(page, order)
                if missing_fields:
                    result = self._failure(
                        page,
                        "checkout required fields missing: " + ", ".join(missing_fields),
                    )
                    return result
                self._accept_terms(page)
                if not self._click_place_order(page, PlaywrightTimeoutError):
                    result = self._failure(page, "place-order did not reach confirmation after retries")
                    return result
                result = OrderAttemptResult(
                    True,
                    True,
                    "order submitted",
                    {"final_url": page.url, "product_count": len(product_urls), "product_urls": product_urls},
                )
                return result

            page.wait_for_timeout(max(0, self.review_seconds) * 1000)
            result = OrderAttemptResult(
                True,
                False,
                "checkout filled; final submit intentionally skipped",
                {
                    "final_url": page.url,
                    "review_seconds": self.review_seconds,
                    "product_count": len(product_urls),
                    "product_urls": product_urls,
                },
            )
            return result
        except PlaywrightError as exc:
            if self._is_target_closed_error(exc):
                result = OrderAttemptResult(
                    False,
                    False,
                    "用户手动关闭浏览器",
                    {"order_id": order.order_id, "error": str(exc)},
                )
                return result
            raise
        except Exception as exc:
            result = self._exception_failure(page, order, self._order_exception_message(exc), exc)
            return result
        finally:
            if not self._keep_failure_session_for_review(playwright, browser, context, page, result):
                self._close_browser_session(playwright, browser, context)

    def _keep_failure_session_for_review(self, playwright, browser, context, page, result) -> bool:
        if not self.keep_open_on_failure or result is None or result.success:
            return False
        if browser is None or context is None or page is None:
            return False
        try:
            if page.is_closed():
                return False
        except Exception:
            return False
        try:
            if not browser.is_connected():
                return False
        except Exception:
            return False
        try:
            page.bring_to_front()
        except Exception:
            pass
        result.details["browser_kept_open"] = True
        result.details["debug_note"] = "Failure browser was kept open for manual inspection."
        _OPEN_FAILURE_SESSIONS.append(
            {
                "playwright": playwright,
                "browser": browser,
                "context": context,
                "page": page,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        return True

    def _close_browser_session(self, playwright, browser, context) -> None:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
        try:
            playwright.stop()
        except Exception:
            pass

    def _payment_method_for(self, order: Order) -> str:
        return self.payment_method or normalize_payment_method(order.payment_method)

    def _configure_packaged_playwright(self) -> None:
        if getattr(sys, "frozen", False):
            browser_dir = browser_cache_dir()
            self._migrate_legacy_browser_cache(browser_dir)
            browser_dir.mkdir(parents=True, exist_ok=True)
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browser_dir)

    def _migrate_legacy_browser_cache(self, browser_dir: Path) -> None:
        legacy_dir = app_data_dir() / "playwright-browsers"
        try:
            if legacy_dir.resolve() == browser_dir.resolve() or not legacy_dir.exists():
                return
            browser_dir.mkdir(parents=True, exist_ok=True)
            shutil.copytree(legacy_dir, browser_dir, dirs_exist_ok=True)
        except Exception:
            pass

    def _ensure_playwright_browser(self, playwright) -> None:
        executable_path = Path(playwright.chromium.executable_path)
        if executable_path.exists():
            return
        if not getattr(sys, "frozen", False):
            raise RuntimeError("Chromium 浏览器未安装，请先运行：python -m playwright install chromium")
        self._log(f"未找到 Chromium，开始自动下载到：{os.environ.get('PLAYWRIGHT_BROWSERS_PATH', browser_cache_dir())}")
        try:
            from playwright._impl._driver import compute_driver_executable, get_driver_env

            driver_executable, driver_cli = compute_driver_executable()
            env = get_driver_env()
            env["PLAYWRIGHT_BROWSERS_PATH"] = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", str(browser_cache_dir()))
            process = subprocess.Popen(
                [driver_executable, driver_cli, "install", "chromium"],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdin=subprocess.DEVNULL,
                **self._hidden_subprocess_kwargs(),
            )
            output_lines: list[str] = []
            assert process.stdout is not None
            for raw_line in process.stdout:
                line = raw_line.strip()
                if not line:
                    continue
                output_lines.append(line)
                self._log(f"浏览器下载：{line}")
            return_code = process.wait()
        except Exception as exc:
            raise RuntimeError(f"Chromium 浏览器未安装，自动下载启动失败：{exc}") from exc
        if return_code != 0:
            output = "\n".join(output_lines).strip()
            raise RuntimeError(f"Chromium 浏览器下载失败，请检查网络后重试。\n{output}")
        if not executable_path.exists():
            raise RuntimeError(f"Chromium 下载结束，但没有找到浏览器文件：{executable_path}")
        self._log(f"Chromium 下载完成：{executable_path}")

    def _hidden_subprocess_kwargs(self) -> dict[str, object]:
        if sys.platform != "win32":
            return {}
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}

    def _log(self, message: str) -> None:
        if not self.log_callback:
            return
        try:
            self.log_callback(message)
        except Exception:
            pass

    def _set_quantity(self, page, quantity: int) -> bool:
        quantity = max(1, quantity)
        quantity_selectors = [
            "input[data-qa='productpage-text-qty']",
            "[data-qa='productpage-text-qty']",
            "input.quantity-picker__amount",
            "input.qty",
            "input[name='quantity']",
        ]
        if quantity == 1:
            return True

        current = self._read_quantity(page, quantity_selectors) or 1
        if current < quantity:
            for _ in range(quantity - current):
                if not self._click_quantity_button(
                    page,
                    [
                        "button[data-qa='productpage-btn-increaseq']",
                        "[data-qa='productpage-btn-increaseq']",
                        "button[data-qa='productpage-btn-qty-increase']",
                        "button[data-qa='productpage-btn-qty-plus']",
                        "[data-qa='productpage-btn-qty-increase']",
                        "[data-qa='productpage-btn-qty-plus']",
                        "[data-qa*='qty'][data-qa*='increase']",
                        "[data-qa*='qty'][data-qa*='plus']",
                        ".quantity-picker button:has-text('+')",
                        "button:has-text('+')",
                    ],
                ):
                    self._fill_first(page, quantity_selectors, str(quantity))
                    return True
                page.wait_for_timeout(200)
            return True
        elif current > quantity:
            for _ in range(current - quantity):
                if not self._click_quantity_button(
                    page,
                    [
                        "button[data-qa='productpage-btn-decrease']",
                        "[data-qa='productpage-btn-decrease']",
                        "button[data-qa='productpage-btn-qty-decrease']",
                        "button[data-qa='productpage-btn-qty-minus']",
                        "[data-qa='productpage-btn-qty-decrease']",
                        "[data-qa='productpage-btn-qty-minus']",
                        "[data-qa*='qty'][data-qa*='decrease']",
                        "[data-qa*='qty'][data-qa*='minus']",
                        ".quantity-picker button:has-text('-')",
                        "button:has-text('-')",
                    ],
                ):
                    self._fill_first(page, quantity_selectors, str(quantity))
                    return True
                page.wait_for_timeout(200)
            return True

        if self._fill_first(page, quantity_selectors, str(quantity)):
            page.keyboard.press("Tab")
            page.wait_for_timeout(300)
            return True
        return True

    def _wait_for_product_page(self, page, timeout_error) -> None:
        self._find_visible_enabled_locator(page, ADD_TO_BAG_SELECTORS, timeout_error, timeout_ms=30000)
        page.wait_for_timeout(500)

    def _click_add_to_bag(self, page, timeout_error, *, allow_cart_open: bool = True) -> bool:
        for attempt in range(6):
            candidate = self._find_visible_enabled_locator(
                page,
                ADD_TO_BAG_SELECTORS,
                timeout_error,
                timeout_ms=8000 if attempt == 0 else 2500,
            )
            if candidate is not None and self._try_product_button_click(
                page,
                candidate,
                timeout_error,
                allow_cart_open=allow_cart_open,
            ):
                return True
            page.wait_for_timeout(1000)
        return False

    def _try_product_button_click(self, page, candidate, timeout_error, *, allow_cart_open: bool) -> bool:
        for _ in range(3):
            previous_cart_count = self._cart_total_count(page)
            if self._click_candidate_with_fallbacks(page, candidate, click_timeout_ms=4000):
                if self._wait_for_add_to_cart_completion(
                    page,
                    timeout_error,
                    previous_cart_count,
                    allow_cart_open=allow_cart_open,
                ):
                    return True
            page.wait_for_timeout(700)
        return False

    def _click_checkout_from_bag(self, page, timeout_error) -> bool:
        for attempt in range(5):
            if self._click_first(page, CART_CHECKOUT_SELECTORS, timeout_ms=5000, attempts=1):
                return True

            if self._click_view_cart(page, timeout_error):
                if self._click_first(page, CART_CHECKOUT_SELECTORS, timeout_ms=8000, attempts=3):
                    return True

            if self._click_first(page, CART_TOGGLE_SELECTORS, timeout_ms=4000, attempts=1):
                page.wait_for_timeout(1500)
                if self._click_first(page, CART_CHECKOUT_SELECTORS, timeout_ms=7000, attempts=2):
                    return True
                if self._cart_is_empty(page):
                    self._close_cart_drawer(page)
                    page.wait_for_timeout(2500)
                    continue

            if self._wait_for_cart_checkout(page, timeout_error, timeout_ms=3000):
                if self._click_first(page, CART_CHECKOUT_SELECTORS, timeout_ms=5000, attempts=1):
                    return True

            page.wait_for_timeout(1500 + attempt * 500)
        return False

    def _wait_for_add_to_cart_completion(
        self,
        page,
        timeout_error,
        previous_cart_count: int | None,
        *,
        allow_cart_open: bool,
    ) -> bool:
        for attempt in range(5):
            self._quiet_wait_for_network(page, timeout_error)
            if self._cart_count_increased(previous_cart_count, self._cart_total_count(page)):
                return True
            if self._has_visible_any(page, ADD_TO_CART_DONE_SELECTORS + VIEW_CART_SELECTORS):
                return True
            if self._wait_for_cart_checkout(page, timeout_error, timeout_ms=1200):
                return True
            page.wait_for_timeout(1000 + attempt * 400)

        if not allow_cart_open:
            return False

        for attempt in range(3):
            if self._click_first(page, CART_TOGGLE_SELECTORS, timeout_ms=3000, attempts=1):
                page.wait_for_timeout(1800)
                if self._cart_count_increased(previous_cart_count, self._cart_total_count(page)):
                    return True
                if self._cart_has_items(page) or self._has_visible_any(page, CART_CHECKOUT_SELECTORS):
                    return True
                if self._cart_is_empty(page):
                    self._close_cart_drawer(page)
                    page.wait_for_timeout(2500)
                    continue
            page.wait_for_timeout(1500 + attempt * 500)
        return False

    def _click_view_cart(self, page, timeout_error) -> bool:
        if not self._click_first(page, VIEW_CART_SELECTORS, timeout_ms=5000, attempts=2):
            return False
        try:
            page.wait_for_url(lambda url: "cart" in url.lower(), timeout=15000)
        except timeout_error:
            pass
        self._quiet_wait_for_network(page, timeout_error)
        return self._has_visible_any(page, CART_CHECKOUT_SELECTORS) or "cart" in page.url.lower()

    def _cart_count_increased(self, previous_count: int | None, current_count: int | None) -> bool:
        if current_count is None:
            return False
        if previous_count is None:
            return current_count > 0
        return current_count > previous_count

    def _cart_total_count(self, page) -> int | None:
        for selector in [
            "[data-cart-total]",
            ".ast-site-header-cart .count",
            ".ast-cart-menu-wrap .count",
            ".cart-contents .count",
            ".site-header-cart .count",
        ]:
            locator = page.locator(selector)
            count = self._safe_count(locator)
            for index in range(min(count, 10)):
                candidate = locator.nth(index)
                for attr in ["data-cart-total", "data-count", "aria-label"]:
                    try:
                        value = candidate.get_attribute(attr, timeout=500)
                    except Exception:
                        value = None
                    parsed = parse_cart_count_value(value)
                    if parsed is not None:
                        return parsed
                try:
                    text = candidate.inner_text(timeout=500)
                except Exception:
                    text = ""
                parsed = parse_cart_count_value(text)
                if parsed is not None:
                    return parsed
        return None

    def _cart_has_items(self, page) -> bool:
        cart_count = self._cart_total_count(page)
        if cart_count is not None and cart_count > 0:
            return True
        return self._has_visible_any(page, CART_ITEM_SELECTORS)

    def _cart_is_empty(self, page) -> bool:
        return self._has_visible_text_any(
            page,
            [
                "Shopping bag is empty",
                "Your cart is currently empty",
                "No products in the cart",
                "Cart is empty",
            ],
        )

    def _close_cart_drawer(self, page) -> None:
        if self._click_first(page, CART_CLOSE_SELECTORS, timeout_ms=1500, attempts=1):
            page.wait_for_timeout(500)
            return
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        page.wait_for_timeout(500)

    def _wait_for_cart_checkout(self, page, timeout_error, *, timeout_ms: int = 12000) -> bool:
        for selector in CART_CHECKOUT_SELECTORS:
            try:
                page.wait_for_selector(selector, state="visible", timeout=timeout_ms)
                return True
            except timeout_error:
                if self._cart_is_empty(page):
                    return False
            except Exception:
                pass
        return self._has_visible_any(page, CART_CHECKOUT_SELECTORS)

    def _wait_for_checkout_page(self, page, timeout_error) -> None:
        try:
            page.wait_for_url("**/checkout**", timeout=30000)
        except timeout_error:
            pass
        for selector in [
            "#billing_first_name",
            "input[name='billing_first_name']",
            "#billing_address_1",
            "input[name='billing_address_1']",
            "#place_order",
            "input[placeholder='Email']",
            "input[type='email']",
            "input[placeholder='Full name']",
            "text=Billing details",
            "text=Payment",
        ]:
            try:
                page.wait_for_selector(selector, state="visible", timeout=15000)
                break
            except Exception:
                continue
        page.wait_for_timeout(700)

    def _fill_checkout(self, page, order: Order) -> bool:
        self._fill_checkout_field(
            page,
            order.email,
            ["email", "contact"],
            [
                "[data-qa='checkout-contactinformation-email'] input",
                "div[name='email'] input",
                "input#email",
                "input[placeholder='Email']",
                "input[placeholder*='Email' i]",
                "input[aria-label='Email']",
                "input[type='email']",
                "input[autocomplete='email']",
                "input[autocomplete*='email' i]",
                "input[name='email']",
                "#billing_email",
                "input[name='billing_email']",
            ],
        )
        self._fill_checkout_field(
            page,
            order.full_name,
            ["full name", "fullname", "name", "recipient"],
            [
                "[data-qa='checkout-contactinformation-fullname'] input",
                "[data-qa='checkout-contactinformation-name'] input",
                "div[name='name'] input",
                "input#name",
                "input#fullName",
                "input[placeholder='Full name']",
                "input[placeholder*='Full name' i]",
                "input[aria-label='Full name']",
                "input[autocomplete='name']",
                "input[name='fullName']",
                "input[name='full_name']",
            ],
        )
        self._fill_checkout_field(
            page,
            order.first_name,
            ["first name", "firstname", "given-name"],
            [
                "input[placeholder='First name']",
                "input[placeholder*='First name' i]",
                "input[aria-label='First name']",
                "input[autocomplete='given-name']",
                "input[autocomplete*='given-name' i]",
                "#billing_first_name",
                "input[name='billing_first_name']",
                "input[name='firstName']",
                "input[name='first_name']",
            ],
        )
        self._fill_checkout_field(
            page,
            order.last_name,
            ["last name", "lastname", "family-name", "surname"],
            [
                "input[placeholder='Last name']",
                "input[placeholder*='Last name' i]",
                "input[aria-label='Last name']",
                "input[autocomplete='family-name']",
                "input[autocomplete*='family-name' i]",
                "#billing_last_name",
                "input[name='billing_last_name']",
                "input[name='lastName']",
                "input[name='last_name']",
            ],
        )
        if not self._select_country(page, order):
            if not self.allow_detected_country_on_mismatch:
                return False
            if not self._select_detected_country(page):
                return False
        self._wait_for_hostinger_shipping_fields(page)
        self._fill_hostinger_shipping_fields(page, order)
        self._fill_checkout_field(
            page,
            order.address_line,
            ["address", "address line 1", "address-line1", "street"],
            [
                "[data-qa='checkout-contactinformation-address'] input",
                "div[name='address'] input",
                "input#address",
                "input[placeholder='Address']",
                "input[placeholder*='Address' i]",
                "input[placeholder*='House number' i]",
                "input[placeholder*='street name' i]",
                "input[aria-label='Address']",
                "input[autocomplete='address-line1']",
                "input[autocomplete*='address-line1' i]",
                "#billing_address_1",
                "input[name='billing_address_1']",
                "input[name='address']",
                "input[name='address1']",
                "input[name='addressLine1']",
            ],
        )
        self._fill_checkout_field(
            page,
            order.value("address_line2"),
            ["apartment", "suite", "address line 2", "address-line2"],
            [
                "input[placeholder='Apartment, suite, etc.']",
                "input[placeholder*='Apartment' i]",
                "input[placeholder*='Address line 2' i]",
                "input[autocomplete='address-line2']",
                "input[autocomplete*='address-line2' i]",
                "#billing_address_2",
                "input[name='billing_address_2']",
                "input[name='address2']",
                "input[name='addressLine2']",
            ],
        )
        self._fill_checkout_field(
            page,
            order.city,
            ["city", "town", "address-level2"],
            [
                "[data-qa='checkout-contactinformation-city'] input",
                "div[name='city'] input",
                "input#city",
                "input[placeholder='City']",
                "input[placeholder*='City' i]",
                "input[placeholder*='Town' i]",
                "input[aria-label='City']",
                "input[autocomplete='address-level2']",
                "input[autocomplete*='address-level2' i]",
                "#billing_city",
                "input[name='billing_city']",
                "input[name='city']",
            ],
        )
        self._fill_checkout_field(
            page,
            order.postal_code,
            ["postal", "postcode", "zip", "postal-code"],
            [
                "[data-qa='checkout-contactinformation-postalcode'] input",
                "div[name='postalCode'] input",
                "input#postalCode",
                "input[placeholder='Postal code']",
                "input[placeholder*='Postal code' i]",
                "input[placeholder*='Postcode' i]",
                "input[placeholder*='ZIP' i]",
                "input[aria-label='Postal code']",
                "input[autocomplete='postal-code']",
                "input[autocomplete*='postal-code' i]",
                "#billing_postcode",
                "input[name='billing_postcode']",
                "input[name='postalCode']",
                "input[name='postal_code']",
                "input[name='zip']",
            ],
        )
        self._fill_shipping_fields_by_order(page, order)
        self._fill_hostinger_shipping_fields(page, order)
        self._fill_checkout_field(
            page,
            order.value("state") or order.value("province"),
            ["state", "province", "county", "address-level1"],
            [
                "input[placeholder='State']",
                "input[placeholder='Province']",
                "input[placeholder*='State' i]",
                "input[placeholder*='Province' i]",
                "input[placeholder*='County' i]",
                "input[autocomplete='address-level1']",
                "input[autocomplete*='address-level1' i]",
                "#billing_state",
                "input[name='billing_state']",
                "input[name='state']",
                "input[name='province']",
            ],
        )
        self._fill_checkout_field(
            page,
            order.phone or order.value("phone"),
            ["phone", "telephone", "tel"],
            [
                "input[placeholder='Phone']",
                "input[placeholder*='Phone' i]",
                "input[aria-label='Phone']",
                "input[type='tel']",
                "input[autocomplete='tel']",
                "input[autocomplete*='tel' i]",
                "#billing_phone",
                "input[name='billing_phone']",
                "input[name='phone']",
            ],
        )
        self._fill_checkout_field(
            page,
            order.notes,
            ["notes", "instructions", "comments"],
            [
                "#order_comments",
                "textarea[name='order_comments']",
                "textarea[placeholder*='Notes' i]",
                "textarea[placeholder*='Instructions' i]",
            ],
        )
        return True

    def _fill_checkout_field(
        self,
        page,
        value: str,
        keywords: list[str],
        selectors: list[str],
    ) -> bool:
        if not value:
            return False
        if self._fill_first(page, selectors, value):
            return True
        return self._fill_by_metadata(page, value, keywords)

    def _fill_hostinger_shipping_fields(self, page, order: Order) -> None:
        exact_fields = [
            (
                order.email,
                [
                    "[data-qa='checkout-contactinformation-email'] input",
                    "div[name='email'] input",
                    "input#email",
                ],
            ),
            (
                order.full_name,
                [
                    "[data-qa='checkout-contactinformation-fullname'] input",
                    "[data-qa='checkout-contactinformation-name'] input",
                    "div[name='name'] input",
                    "input#name",
                    "input#fullName",
                ],
            ),
            (
                order.address_line,
                [
                    "[data-qa='checkout-contactinformation-address'] input",
                    "div[name='address'] input",
                    "input#address",
                ],
            ),
            (
                order.city,
                [
                    "[data-qa='checkout-contactinformation-city'] input",
                    "div[name='city'] input",
                    "input#city",
                ],
            ),
            (
                order.postal_code,
                [
                    "[data-qa='checkout-contactinformation-postalcode'] input",
                    "div[name='postalCode'] input",
                    "input#postalCode",
                ],
            ),
        ]
        for value, selectors in exact_fields:
            self._fill_first(page, selectors, value)

    def _wait_for_hostinger_shipping_fields(self, page) -> None:
        field_groups = [
            [
                "[data-qa='checkout-contactinformation-address'] input",
                "div[name='address'] input",
                "input#address",
            ],
            [
                "[data-qa='checkout-contactinformation-city'] input",
                "div[name='city'] input",
                "input#city",
            ],
            [
                "[data-qa='checkout-contactinformation-postalcode'] input",
                "div[name='postalCode'] input",
                "input#postalCode",
            ],
        ]
        try:
            page.wait_for_function(
                """
                (fieldGroups) => fieldGroups.every((selectors) => selectors.some((selector) => {
                    const element = document.querySelector(selector);
                    if (!element) return false;
                    const rect = element.getBoundingClientRect();
                    const style = window.getComputedStyle(element);
                    return rect.width > 0
                        && rect.height > 0
                        && style.display !== 'none'
                        && style.visibility !== 'hidden'
                        && !element.disabled;
                }))
                """,
                field_groups,
                timeout=15000,
            )
        except Exception:
            for selectors in field_groups:
                for selector in selectors:
                    try:
                        page.wait_for_selector(selector, state="visible", timeout=2000)
                        break
                    except Exception:
                        pass

    def _fill_by_metadata(self, page, value: str, keywords: list[str]) -> bool:
        fields = page.locator("input, textarea")
        count = self._safe_count(fields)
        for index in range(min(count, 80)):
            field = fields.nth(index)
            if not self._is_visible(field) or not self._is_enabled(field):
                continue
            metadata = self._field_metadata(field)
            if any(keyword.lower() in metadata for keyword in keywords):
                if self._fill_locator(field, value):
                    return True
        return False

    def _fill_shipping_fields_by_order(self, page, order: Order) -> None:
        targets = [
            ("address", order.address_line, ["address", "street"]),
            ("city", order.city, ["city", "town"]),
            ("postal", order.postal_code, ["postal", "postcode", "zip"]),
        ]
        for _, value, keywords in targets:
            if value:
                self._fill_by_metadata(page, value, keywords)

        remaining_values = [
            value
            for value in [order.address_line, order.city, order.postal_code]
            if value and not self._page_has_input_value(page, value)
        ]
        if not remaining_values:
            return

        candidates = self._visible_blank_shipping_inputs(page)
        for locator, value in zip(candidates, remaining_values):
            self._fill_locator(locator, value)

    def _missing_required_checkout_fields(self, page, order: Order) -> list[str]:
        first_name_selectors = [
            "input[placeholder='First name']",
            "input[placeholder*='First name' i]",
            "input[aria-label='First name']",
            "input[autocomplete='given-name']",
            "input[autocomplete*='given-name' i]",
            "#billing_first_name",
            "input[name='billing_first_name']",
            "input[name='firstName']",
            "input[name='first_name']",
        ]
        last_name_selectors = [
            "input[placeholder='Last name']",
            "input[placeholder*='Last name' i]",
            "input[aria-label='Last name']",
            "input[autocomplete='family-name']",
            "input[autocomplete*='family-name' i]",
            "#billing_last_name",
            "input[name='billing_last_name']",
            "input[name='lastName']",
            "input[name='last_name']",
        ]
        full_name_selectors = [
            "[data-qa='checkout-contactinformation-fullname'] input",
            "[data-qa='checkout-contactinformation-name'] input",
            "div[name='name'] input",
            "input#name",
            "input#fullName",
            "input[placeholder='Full name']",
            "input[autocomplete='name']",
            "input[autocomplete*='name' i]",
        ]
        name_checks = (
            [
                ("first_name", order.first_name, first_name_selectors),
                ("last_name", order.last_name, last_name_selectors),
            ]
            if self._has_visible_any(page, first_name_selectors + last_name_selectors)
            else [("full_name", order.full_name, full_name_selectors)]
        )
        checks = [
            (
                "email",
                order.email,
                [
                    "[data-qa='checkout-contactinformation-email'] input",
                    "div[name='email'] input",
                    "input#email",
                    "input[placeholder='Email']",
                    "input[type='email']",
                    "input[autocomplete='email']",
                    "input[autocomplete*='email' i]",
                    "#billing_email",
                    "input[name='billing_email']",
                ],
            ),
            *name_checks,
            (
                "address",
                order.address_line,
                [
                    "[data-qa='checkout-contactinformation-address'] input",
                    "div[name='address'] input",
                    "input#address",
                    "input[placeholder='Address']",
                    "input[placeholder*='Address' i]",
                    "input[placeholder*='House number' i]",
                    "input[placeholder*='street name' i]",
                    "input[autocomplete='address-line1']",
                    "input[autocomplete*='address-line1' i]",
                    "#billing_address_1",
                    "input[name='billing_address_1']",
                ],
            ),
            (
                "city",
                order.city,
                [
                    "[data-qa='checkout-contactinformation-city'] input",
                    "div[name='city'] input",
                    "input#city",
                    "input[placeholder='City']",
                    "input[placeholder*='City' i]",
                    "input[placeholder*='Town' i]",
                    "input[autocomplete='address-level2']",
                    "input[autocomplete*='address-level2' i]",
                    "#billing_city",
                    "input[name='billing_city']",
                ],
            ),
            (
                "postal_code",
                order.postal_code,
                [
                    "[data-qa='checkout-contactinformation-postalcode'] input",
                    "div[name='postalCode'] input",
                    "input#postalCode",
                    "input[placeholder='Postal code']",
                    "input[placeholder*='Postal code' i]",
                    "input[placeholder*='Postcode' i]",
                    "input[autocomplete='postal-code']",
                    "input[autocomplete*='postal-code' i]",
                    "#billing_postcode",
                    "input[name='billing_postcode']",
                ],
            ),
        ]
        missing = []
        for label, expected, selectors in checks:
            if expected and not self._checkout_value_present(page, expected, selectors):
                missing.append(label)
        return missing

    def _checkout_value_present(self, page, expected: str, selectors: list[str]) -> bool:
        for selector in selectors:
            locator = page.locator(selector)
            count = self._safe_count(locator)
            for index in range(min(count, 10)):
                if self._locator_has_value(locator.nth(index), expected):
                    return True
        return self._page_has_input_value(page, expected)

    def _visible_blank_shipping_inputs(self, page) -> list:
        fields = page.locator("input, textarea")
        candidates = []
        count = self._safe_count(fields)
        for index in range(min(count, 100)):
            field = fields.nth(index)
            if not self._is_visible(field) or not self._is_enabled(field):
                continue
            metadata = self._field_metadata(field)
            if any(skip in metadata for skip in ["email", "discount", "coupon", "search", "phone"]):
                continue
            try:
                current = field.input_value(timeout=500)
            except Exception:
                current = ""
            if current.strip():
                continue
            if any(key in metadata for key in ["address", "city", "postal", "postcode", "zip"]):
                candidates.append(field)
        return candidates

    def _select_country(self, page, order: Order) -> bool:
        country = order.country.strip()
        if not country:
            return True
        country_code = order.value("country_code")
        selectors = [
            "#billing_country",
            "select[name='billing_country']",
            "select[name='country']",
            "select[autocomplete='country']",
        ]
        for selector in selectors:
            locator = page.locator(selector).first
            if self._safe_count(locator) == 0:
                continue
            for option in (
                {"label": country},
                {"value": country_code} if country_code else None,
            ):
                if option is None:
                    continue
                try:
                    locator.select_option(**option, timeout=3000)
                    page.wait_for_timeout(500)
                    return True
                except Exception:
                    pass

        if self._current_country_matches(page, country):
            return True
        return self._choose_country_from_custom_select(page, country)

    def _choose_country_from_custom_select(self, page, country: str) -> bool:
        country_controls = [
            "[aria-controls='destination-dropdown']",
            ".h-select__field[role='combobox']",
            "[data-qa*='country'] [role='combobox']",
            "[data-qa*='destination'] [role='combobox']",
            "button[aria-label*='Country' i]",
            "[role='combobox']",
        ]
        for selector in country_controls:
            locator = page.locator(selector)
            count = self._safe_count(locator)
            for index in range(min(count, 10)):
                candidate = locator.nth(index)
                if not self._is_visible(candidate) or not self._is_enabled(candidate):
                    continue
                if not self._click_candidate_with_fallbacks(page, candidate, click_timeout_ms=3000):
                    continue
                page.wait_for_timeout(300)
                if self._select_visible_country_option(page, country):
                    page.wait_for_timeout(500)
                    return True
                self._close_country_dropdown(page)
        return False

    def _select_visible_country_option(self, page, country: str) -> bool:
        self._fill_country_search_input(page, country)
        page.wait_for_timeout(250)

        option_locators = [
            page.get_by_role("option", name=country, exact=True),
            page.get_by_role("option", name=country, exact=False),
            page.get_by_text(country, exact=True),
        ]
        for locator in option_locators:
            count = self._safe_count(locator)
            for index in range(min(count, 20)):
                candidate = locator.nth(index)
                if not self._is_visible(candidate):
                    continue
                if not self._locator_text_matches_country(candidate, country):
                    continue
                if self._click_candidate_with_fallbacks(page, candidate, click_timeout_ms=3000):
                    return self._current_country_matches(page, country)
        return False

    def _select_detected_country(self, page) -> bool:
        if self._has_selected_country(page):
            self._close_country_dropdown(page)
            return True
        if self._click_visible_detected_country_option(page):
            return True

        for selector in [
            "[aria-controls='destination-dropdown']",
            ".h-select__field[role='combobox']",
            "[data-qa*='country'] [role='combobox']",
            "[data-qa*='destination'] [role='combobox']",
            "[role='combobox']",
        ]:
            locator = page.locator(selector)
            count = self._safe_count(locator)
            for index in range(min(count, 10)):
                candidate = locator.nth(index)
                if not self._is_visible(candidate) or not self._is_enabled(candidate):
                    continue
                if not self._click_candidate_with_fallbacks(page, candidate, click_timeout_ms=3000):
                    continue
                page.wait_for_timeout(300)
                if self._click_visible_detected_country_option(page):
                    return True
        return self._has_selected_country(page)

    def _click_visible_detected_country_option(self, page) -> bool:
        selectors = [
            "#destination-dropdown [role='option'][aria-selected='true']",
            "#destination-dropdown [role='option'].is-selected",
            "#destination-dropdown [role='option']",
            "[role='listbox'] [role='option'][aria-selected='true']",
            "[role='listbox'] [role='option'].is-selected",
            "[role='listbox'] [role='option']",
            ".h-dropdown-list__dropdown [role='option']",
        ]
        for selector in selectors:
            locator = page.locator(selector)
            count = self._safe_count(locator)
            for index in range(min(count, 20)):
                candidate = locator.nth(index)
                if not self._is_visible(candidate):
                    continue
                try:
                    text = candidate.text_content(timeout=500) or ""
                except Exception:
                    text = ""
                if normalize_country_text(text) in {"", "country", "search"}:
                    continue
                if not self._click_candidate_with_fallbacks(page, candidate, click_timeout_ms=3000):
                    continue
                page.wait_for_timeout(500)
                return self._has_selected_country(page)
        return False

    def _has_selected_country(self, page) -> bool:
        selectors = [
            "[aria-controls='destination-dropdown']",
            ".h-select__field[role='combobox']",
            "[data-qa*='country'] [role='combobox']",
            "[data-qa*='destination'] [role='combobox']",
            "[role='combobox']",
        ]
        ignored = {"", "country", "search"}
        for selector in selectors:
            locator = page.locator(selector)
            count = self._safe_count(locator)
            for index in range(min(count, 10)):
                candidate = locator.nth(index)
                if not self._is_visible(candidate):
                    continue
                try:
                    value = candidate.input_value(timeout=500)
                except Exception:
                    try:
                        value = candidate.text_content(timeout=500) or ""
                    except Exception:
                        value = ""
                if normalize_country_text(value) not in ignored:
                    return True
        return False

    def _current_country_matches(self, page, country: str) -> bool:
        expected = normalize_country_text(country)
        if not expected:
            return True
        selectors = [
            "[aria-controls='destination-dropdown']",
            ".h-select__field[role='combobox']",
            "[data-qa*='country'] [role='combobox']",
            "[data-qa*='destination'] [role='combobox']",
            "[role='combobox']",
            "select[name='country']",
            "select[autocomplete='country']",
        ]
        for selector in selectors:
            locator = page.locator(selector)
            count = self._safe_count(locator)
            for index in range(min(count, 10)):
                candidate = locator.nth(index)
                if not self._is_visible(candidate):
                    continue
                try:
                    value = candidate.input_value(timeout=500)
                except Exception:
                    try:
                        value = candidate.text_content(timeout=500) or ""
                    except Exception:
                        value = ""
                if normalize_country_text(value) == expected:
                    return True
        return False

    def _locator_text_matches_country(self, locator, country: str) -> bool:
        try:
            text = locator.text_content(timeout=500) or ""
        except Exception:
            return False
        normalized_text = normalize_country_text(text)
        normalized_country = normalize_country_text(country)
        return normalized_text == normalized_country or normalized_country in normalized_text.splitlines()

    def _close_country_dropdown(self, page) -> None:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass

    def _country_not_found_message(self, order: Order) -> str:
        return (
            "checkout country not found: "
            f"{order.country}. Enable detected-country fallback to use the website-selected country."
        )

    def _fill_country_search_input(self, page, country: str) -> bool:
        selectors = [
            "#destination-dropdown input[role='searchbox']",
            "#destination-dropdown input[type='search']",
            "[role='listbox'] input[role='searchbox']",
            "input[role='searchbox']",
            "input[type='search']",
            "input[placeholder*='Search' i]",
        ]
        for selector in selectors:
            locator = page.locator(selector)
            count = self._safe_count(locator)
            for index in range(min(count, 5)):
                candidate = locator.nth(index)
                if not self._is_visible(candidate) or not self._is_enabled(candidate):
                    continue
                try:
                    candidate.fill(country, timeout=2000)
                    return True
                except Exception:
                    pass
        return False

    def _choose_payment_method(self, page, payment_method: str) -> bool:
        normalized = normalize_payment_method(payment_method)
        if self._page_has_direct_submit_without_payment_choices(page):
            return True
        try:
            page.get_by_text("Payment", exact=False).first.scroll_into_view_if_needed(timeout=2000)
        except Exception:
            page.mouse.wheel(0, 900)
        page.wait_for_timeout(400)

        if self._check_payment_radio_by_value(page, normalized):
            return True

        labels = PAYMENT_METHOD_LABELS.get(normalized, [payment_method])
        for label in labels:
            if self._check_payment_by_label(page, label):
                return True
        return False

    def _page_has_direct_submit_without_payment_choices(self, page) -> bool:
        payment_controls = [
            "input[type='radio'][name*='payment_method']",
            "input[name='payment_method']",
            "[role='radio'][name*='payment']",
            "[data-qa*='payment'] [role='radio']",
        ]
        return self._has_visible_any(page, PLACE_ORDER_SELECTORS) and not self._has_any(page, payment_controls)

    def _check_payment_radio_by_value(self, page, payment_method: str) -> bool:
        values = PAYMENT_METHOD_VALUES.get(payment_method, [payment_method])
        for value in values:
            selector = f"input[type='radio'][value='{value}']"
            locator = page.locator(selector).first
            if self._safe_count(locator) == 0:
                continue
            try:
                locator.check(force=True, timeout=3000)
                return True
            except Exception:
                pass
        return False

    def _check_payment_by_label(self, page, label: str) -> bool:
        try:
            control = page.get_by_label(label, exact=False).first
            if self._safe_count(control) > 0:
                control.check(force=True, timeout=3000)
                return True
        except Exception:
            pass

        for selector in [
            f"label:has-text('{label}')",
            f"[role='radio']:has-text('{label}')",
            f"div:has-text('{label}')",
            f"text={label}",
        ]:
            locator = page.locator(selector)
            count = self._safe_count(locator)
            for index in range(min(count, 10)):
                candidate = locator.nth(index)
                if not self._is_visible(candidate):
                    continue
                if self._click_candidate_with_fallbacks(page, candidate, click_timeout_ms=3000):
                    page.wait_for_timeout(300)
                    return True
        return False

    def _accept_terms(self, page) -> None:
        for selector in ["#terms", "input[name='terms']", "input[type='checkbox'][name*='terms']"]:
            locator = page.locator(selector).first
            if self._safe_count(locator) > 0:
                try:
                    locator.check(force=True, timeout=2000)
                except Exception:
                    pass

    def _click_place_order(self, page, timeout_error) -> bool:
        if self._wait_for_order_confirmation(page, timeout_error, timeout_ms=1000):
            return True
        for attempt in range(5):
            candidate = self._find_visible_enabled_locator(
                page,
                PLACE_ORDER_SELECTORS,
                timeout_error,
                timeout_ms=10000 if attempt == 0 else 4000,
            )
            if candidate is None:
                if self._wait_for_order_confirmation(page, timeout_error, timeout_ms=5000):
                    return True
                page.wait_for_timeout(1000)
                continue
            if self._click_candidate_with_fallbacks(page, candidate, click_timeout_ms=5000):
                self._quiet_wait_for_network(page, timeout_error)
                if self._wait_for_order_confirmation(page, timeout_error, timeout_ms=15000):
                    return True
            page.wait_for_timeout(1500)
        return self._wait_for_order_confirmation(page, timeout_error, timeout_ms=10000)

    def _wait_for_order_confirmation(self, page, timeout_error, *, timeout_ms: int = 20000) -> bool:
        try:
            page.wait_for_url(
                lambda url: "checkout" not in url.lower()
                or "thank" in url.lower()
                or "success" in url.lower()
                or "confirmation" in url.lower()
                or "order-received" in url.lower(),
                timeout=timeout_ms,
            )
            return True
        except timeout_error:
            pass
        confirmation_texts = [
            "Thank you",
            "Order confirmed",
            "Order received",
            "Order number",
            "order has been received",
            "Your order has been placed",
            "Thanks for your order",
            "We've received your order",
            "Payment instructions",
        ]
        per_text_timeout = max(500, timeout_ms // max(1, len(confirmation_texts)))
        for text in confirmation_texts:
            try:
                page.get_by_text(text, exact=False).first.wait_for(state="visible", timeout=per_text_timeout)
                return True
            except Exception:
                pass
        return False

    def _fill_first(self, page, selectors: list[str], value: str) -> bool:
        if not value:
            return False
        for selector in selectors:
            locator = page.locator(selector)
            count = self._safe_count(locator)
            for index in range(min(count, 10)):
                if self._fill_locator(locator.nth(index), value):
                    return True
        return False

    def _fill_locator(self, locator, value: str) -> bool:
        if not self._is_visible(locator) or not self._is_enabled(locator):
            return False
        try:
            locator.fill(value, timeout=3000)
            self._dispatch_input_events(locator)
            if self._locator_has_value(locator, value):
                return True
        except Exception:
            pass
        try:
            locator.click(timeout=3000)
            locator.press("Control+A", timeout=1000)
            locator.type(value, timeout=5000, delay=15)
            self._dispatch_input_events(locator)
            if self._locator_has_value(locator, value):
                return True
        except Exception:
            pass
        return self._set_input_value_with_events(locator, value)

    def _locator_has_value(self, locator, expected: str) -> bool:
        try:
            return locator.input_value(timeout=500).strip() == expected.strip()
        except Exception:
            return False

    def _page_has_input_value(self, page, expected: str) -> bool:
        fields = page.locator("input, textarea")
        count = self._safe_count(fields)
        for index in range(min(count, 100)):
            field = fields.nth(index)
            if self._locator_has_value(field, expected):
                return True
        return False

    def _set_input_value_with_events(self, locator, value: str) -> bool:
        try:
            locator.evaluate(
                """
                (element, value) => {
                    element.focus();
                    const prototype = element.tagName === 'TEXTAREA'
                        ? window.HTMLTextAreaElement.prototype
                        : window.HTMLInputElement.prototype;
                    const setter = Object.getOwnPropertyDescriptor(prototype, 'value')?.set;
                    if (setter) {
                        setter.call(element, value);
                    } else {
                        element.value = value;
                    }
                    try {
                        element.dispatchEvent(new InputEvent('input', {
                            bubbles: true,
                            cancelable: true,
                            inputType: 'insertText',
                            data: value,
                        }));
                    } catch {
                        element.dispatchEvent(new Event('input', { bubbles: true }));
                    }
                    element.dispatchEvent(new Event('change', { bubbles: true }));
                    element.blur();
                    element.dispatchEvent(new Event('blur', { bubbles: true }));
                }
                """,
                value,
            )
            return self._locator_has_value(locator, value)
        except Exception:
            return False

    def _dispatch_input_events(self, locator) -> None:
        try:
            locator.evaluate(
                """
                (element) => {
                    try {
                        element.dispatchEvent(new InputEvent('input', {
                            bubbles: true,
                            cancelable: true,
                            inputType: 'insertReplacementText',
                            data: element.value || '',
                        }));
                    } catch {
                        element.dispatchEvent(new Event('input', { bubbles: true }));
                    }
                    element.dispatchEvent(new Event('change', { bubbles: true }));
                    element.blur();
                    element.dispatchEvent(new Event('blur', { bubbles: true }));
                }
                """
            )
        except Exception:
            pass

    def _click_quantity_button(self, page, selectors: list[str]) -> bool:
        for _ in range(3):
            for selector in selectors:
                locator = page.locator(selector)
                count = self._safe_count(locator)
                for index in range(min(count, 10)):
                    candidate = locator.nth(index)
                    if not self._is_visible(candidate):
                        continue
                    if self._click_candidate_with_fallbacks(page, candidate, click_timeout_ms=2000):
                        return True
            page.wait_for_timeout(300)
        return False

    def _click_first(self, page, selectors: list[str], *, timeout_ms: int = 8000, attempts: int = 3) -> bool:
        for _ in range(attempts):
            for selector in selectors:
                locator = page.locator(selector)
                count = self._safe_count(locator)
                for index in range(min(count, 10)):
                    candidate = locator.nth(index)
                    if not self._is_visible(candidate) or not self._is_enabled(candidate):
                        continue
                    if self._click_candidate_with_fallbacks(page, candidate, click_timeout_ms=timeout_ms):
                        return True
            page.wait_for_timeout(500)
        return False

    def _click_candidate_with_fallbacks(self, page, candidate, *, click_timeout_ms: int, rounds: int = 2) -> bool:
        for round_index in range(rounds):
            click_attempts: list[Callable[[], None]] = [
                lambda: candidate.click(timeout=click_timeout_ms),
                lambda: candidate.click(timeout=click_timeout_ms, force=True),
                lambda: self._click_element_center(page, candidate),
                lambda: self._dispatch_click(candidate),
            ]
            for attempt in click_attempts:
                try:
                    candidate.scroll_into_view_if_needed(timeout=2000)
                except Exception:
                    pass
                try:
                    attempt()
                    return True
                except Exception:
                    pass
            if round_index < rounds - 1:
                page.wait_for_timeout(300)
        return False

    def _find_visible_enabled_locator(self, page, selectors: list[str], timeout_error, *, timeout_ms: int):
        per_selector_timeout = max(250, timeout_ms // max(1, len(selectors)))
        for selector in selectors:
            try:
                page.wait_for_selector(selector, state="visible", timeout=per_selector_timeout)
            except timeout_error:
                continue
            except Exception:
                pass
            locator = page.locator(selector)
            count = self._safe_count(locator)
            for index in range(min(count, 10)):
                candidate = locator.nth(index)
                if self._is_visible(candidate) and self._is_enabled(candidate):
                    return candidate
        return None

    def _click_element_center(self, page, locator) -> None:
        box = locator.bounding_box(timeout=2000)
        if not box:
            raise RuntimeError("element has no visible bounding box")
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.mouse.down()
        page.wait_for_timeout(80)
        page.mouse.up()

    def _dispatch_click(self, locator) -> None:
        locator.evaluate(
            """
            (element) => {
                element.scrollIntoView({ block: 'center', inline: 'center' });
                for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                    element.dispatchEvent(new MouseEvent(type, {
                        bubbles: true,
                        cancelable: true,
                        view: window,
                    }));
                }
            }
            """
        )

    def _read_quantity(self, page, selectors: list[str]) -> int | None:
        for selector in selectors:
            locator = page.locator(selector)
            count = self._safe_count(locator)
            for index in range(min(count, 10)):
                candidate = locator.nth(index)
                try:
                    raw = candidate.input_value(timeout=1000)
                except Exception:
                    try:
                        raw = candidate.text_content(timeout=1000) or ""
                    except Exception:
                        continue
                text = raw.strip()
                if text.isdigit():
                    return int(text)
        return None

    def _field_metadata(self, locator) -> str:
        try:
            return locator.evaluate(
                """
                (element) => {
                    const parts = [
                        element.getAttribute('placeholder'),
                        element.getAttribute('aria-label'),
                        element.getAttribute('name'),
                        element.getAttribute('id'),
                        element.getAttribute('autocomplete'),
                        element.getAttribute('type'),
                    ];
                    if (element.labels) {
                        for (const label of element.labels) {
                            parts.push(label.innerText);
                        }
                    }
                    const wrapper = element.closest('label,[data-qa],.field,.form-field,.input-wrapper');
                    if (wrapper) {
                        parts.push(wrapper.innerText);
                    }
                    return parts.filter(Boolean).join(' ').toLowerCase();
                }
                """
            )
        except Exception:
            return ""

    def _has_any(self, page, selectors: list[str]) -> bool:
        return any(self._safe_count(page.locator(selector)) > 0 for selector in selectors)

    def _has_visible_any(self, page, selectors: list[str]) -> bool:
        for selector in selectors:
            locator = page.locator(selector)
            count = self._safe_count(locator)
            for index in range(min(count, 10)):
                if self._is_visible(locator.nth(index)):
                    return True
        return False

    def _has_text(self, page, text: str) -> bool:
        try:
            return page.get_by_text(text, exact=False).count() > 0
        except Exception:
            return False

    def _has_visible_text_any(self, page, texts: list[str]) -> bool:
        for text in texts:
            try:
                locator = page.get_by_text(text, exact=False)
                count = self._safe_count(locator)
                for index in range(min(count, 10)):
                    if self._is_visible(locator.nth(index)):
                        return True
            except Exception:
                pass
        return False

    def _is_visible(self, locator) -> bool:
        try:
            return locator.is_visible(timeout=1000)
        except Exception:
            return False

    def _is_enabled(self, locator) -> bool:
        try:
            return locator.is_enabled(timeout=1000)
        except Exception:
            return False

    def _safe_count(self, locator) -> int:
        try:
            return locator.count()
        except Exception:
            return 0

    def _quiet_wait_for_network(self, page, timeout_error) -> None:
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except timeout_error:
            pass

    def _is_target_closed_error(self, exc: Exception) -> bool:
        text = f"{type(exc).__name__}: {exc}".casefold()
        return (
            "targetclosederror" in text
            or "target page, context or browser has been closed" in text
            or "browser has been closed" in text
            or "page has been closed" in text
        )

    def _order_exception_message(self, exc: Exception) -> str:
        if self._is_target_closed_error(exc):
            return "浏览器窗口被关闭，当前订单已停止"
        text = str(exc).strip()
        if "net::ERR" in text or "Timeout" in type(exc).__name__:
            return "网页加载或操作超时，请检查网络、商品链接或网站是否响应"
        if "Chromium" in text or "browser" in text.lower():
            return "浏览器启动或下载失败，请检查网络和浏览器缓存目录权限"
        return "下单过程中出现异常，已记录错误信息"

    def _exception_failure(self, page, order: Order, message: str, exc: Exception) -> OrderAttemptResult:
        detail = str(exc).strip()
        if detail:
            message = f"{message}：{detail}"
        result = self._failure(page, message) if page is not None else OrderAttemptResult(False, False, message, {})
        result.details.update(
            {
                "order_id": order.order_id,
                "error_type": type(exc).__name__,
                "error": detail,
            }
        )
        return result

    def _failure(self, page, message: str) -> OrderAttemptResult:
        details = {}
        try:
            details["url"] = page.url
        except Exception:
            pass
        try:
            details["title"] = page.title()
        except Exception:
            pass
        try:
            screenshot_dir = log_dir()
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            screenshot_path = screenshot_dir / f"failure-{timestamp}.png"
            page.screenshot(path=str(screenshot_path), full_page=True)
            details["screenshot"] = str(screenshot_path)
        except Exception:
            pass
        return OrderAttemptResult(False, False, message, details)


def normalize_payment_method(value: str) -> str:
    normalized = (value or DEFAULT_PAYMENT_METHOD).strip().lower().replace(" ", "_").replace("-", "_")
    if normalized in {"bank", "bank_transfer", "banktransfer", "direct_bank_transfer"}:
        return "bank_transfer"
    if normalized in {"popular", "popular_payments", "popular_payment", "card", "credit_card"}:
        return "popular_payments"
    if normalized in {"cash_on_delivery", "cod"}:
        return "cod"
    return normalized or DEFAULT_PAYMENT_METHOD


def normalize_country_text(value: str) -> str:
    return " ".join((value or "").strip().casefold().split())


def parse_cart_count_value(value: object) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    match = re.search(r"\d+", text)
    if not match:
        return None
    return int(match.group(0))


def result_to_dict(result: OrderAttemptResult) -> dict:
    return asdict(result)
