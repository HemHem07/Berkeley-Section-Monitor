"""Real Chrome regression tests with synthetic pages; no Berkeley traffic or credentials."""
import os

import pytest

import monitor

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_BROWSER_TESTS") != "1", reason="Set RUN_BROWSER_TESTS=1 to test installed Chrome"
)


def test_automatic_sso_redirect_is_not_a_login_failure():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        try:
            context = browser.new_context()
            context.route("https://auth.berkeley.edu/**", lambda route: route.fulfill(
                content_type="text/html", body='''<p>Signing in...</p><script>
                setTimeout(() => location.href = 'https://bcsweb.is.berkeley.edu/test', 1500);
                </script>'''))
            context.route("https://bcsweb.is.berkeley.edu/**", lambda route: route.fulfill(
                content_type="text/html", body="<h1>Enrollment Center</h1>"))
            page = context.new_page()
            page.goto("https://auth.berkeley.edu/test")
            monitor.wait_for_enrollment_entry(page, timeout=5, auth_grace=3)
            assert page.get_by_role("heading", name="Enrollment Center").is_visible()
            context.close()
            monitor.close_browser_context(context)  # Already closed must be harmless.
        finally:
            browser.close()


def test_real_login_page_still_requires_signin():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        try:
            context = browser.new_context()
            context.route("https://auth.berkeley.edu/**", lambda route: route.fulfill(
                content_type="text/html", body='<label>CalNet ID<input></label><input type="password">'))
            page = context.new_page()
            page.goto("https://auth.berkeley.edu/test")
            with pytest.raises(monitor.SignInRequired):
                monitor.wait_for_enrollment_entry(page, timeout=3, auth_grace=0.5)
        finally:
            browser.close()


def test_academics_login_waits_for_separate_enrollment_duo():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        try:
            context = browser.new_context()
            context.route("https://calcentral.berkeley.edu/**", lambda route: route.fulfill(
                content_type="text/html", body='<h1>My Academics</h1><a href="https://api-test.duosecurity.com/test">Enrollment Center</a>'))
            context.route("https://api-test.duosecurity.com/**", lambda route: route.fulfill(
                content_type="text/html", body='''<h1>Verify with Duo</h1><script>
                setTimeout(() => location.href = 'https://bcsweb.is.berkeley.edu/test', 1000);
                </script>'''))
            context.route("https://bcsweb.is.berkeley.edu/**", lambda route: route.fulfill(
                content_type="text/html", body="<h1>Enrollment Center</h1>"))
            page = context.new_page()
            page.goto("https://calcentral.berkeley.edu/academics")
            monitor.complete_calcentral_login(page, timeout=5)
            assert page.url == "https://bcsweb.is.berkeley.edu/test"
        finally:
            browser.close()
