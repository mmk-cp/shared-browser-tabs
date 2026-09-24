"""Admin cleanup UI with ALL APIs intercepted: never resets live data."""
import asyncio
from pathlib import Path
from jinja2 import Environment, FileSystemLoader
from playwright.async_api import async_playwright, expect


async def main():
    artifacts = Path('/tmp/sbt-qa')
    artifacts.mkdir(exist_ok=True)
    template = Environment(loader=FileSystemLoader('app/templates'), autoescape=True)
    html = template.get_template('admin.html').render(user={'username': 'QA admin'})
    calls = []
    fail = False

    async def api(route):
        if route.request.url.endswith('/api/browser/clear-data'):
            calls.append(route.request.post_data_json)
            await route.fulfill(status=500 if fail else 200, json={
                'detail': 'خطای آزمایشی؛ دوباره تلاش کنید.'} if fail else {'ok': True})
        elif route.request.url.endswith('/api/users'):
            await route.fulfill(json=[])
        else:
            await route.fulfill(json={'is_admin': True})

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path='/usr/bin/chromium', headless=True,
                                          args=['--no-sandbox', '--disable-dev-shm-usage'])
        try:
            page = await browser.new_page(viewport={'width': 1280, 'height': 900})
            await page.route('**/api/**', api)
            await page.route('**/admin', lambda route: route.fulfill(content_type='text/html', body=html))
            await page.goto('http://127.0.0.1:8000/admin')
            form = page.locator('#clear-browser-form')
            submit = page.locator('#clear-browser-submit')
            await expect(form).to_be_hidden()
            await page.locator('#clear-browser-data').click()
            await expect(page.locator('#clear-confirmation')).to_be_focused()
            await expect(submit).to_be_disabled()
            await page.locator('#clear-confirmation').fill('اشتباه')
            await expect(submit).to_be_disabled()
            await page.locator('#cancel-browser-clear').click()
            await expect(form).to_be_hidden()
            assert calls == []
            await page.locator('#clear-browser-data').click()
            await page.locator('#clear-confirmation').fill('پاک شود')
            await expect(submit).to_be_enabled()
            await page.screenshot(path=str(artifacts / 'cleanup-desktop.png'), full_page=True)
            await page.set_viewport_size({'width': 390, 'height': 844})
            assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            await page.screenshot(path=str(artifacts / 'cleanup-mobile.png'), full_page=True)
            await submit.click()
            await expect(page.locator('#clear-browser-message')).to_have_class('form-message success')
            await expect(form).to_be_hidden()
            assert calls == [{'confirmation': 'DELETE_BROWSER_DATA'}]
            fail = True
            await page.locator('#clear-browser-data').click()
            await expect(submit).to_be_disabled()
            await page.locator('#clear-confirmation').fill('پاک شود')
            await submit.click()
            await expect(page.locator('#clear-browser-message')).to_contain_text('خطای آزمایشی')
            await expect(page.locator('#clear-browser-data')).to_be_enabled()
            assert len(calls) == 2
            print('PASS cleanup UI: confirmation/cancel, mocked success/failure, desktop/mobile', flush=True)
        finally:
            await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
