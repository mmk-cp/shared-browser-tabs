"""Real Chromium reset on a fresh disposable profile only; no live app reset."""
import asyncio
import json
import tempfile
from pathlib import Path
from playwright.async_api import async_playwright
from app.services.browser_manager import _clear_profile_browsing_data


async def main():
    with tempfile.TemporaryDirectory(prefix='qa-cleanup-',dir='/dev/shm') as root:
        profile=Path(root)/'chromium-profile'
        app_data=Path(root)/'app.db'; app_data.write_text('keep QA application data')
        async with async_playwright() as pw:
            async def launch():
                context=await pw.chromium.launch_persistent_context(str(profile),executable_path='/usr/bin/chromium',
                    headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
                await context.route('http://localhost:18888/**',lambda route:route.fulfill(
                    content_type='text/html',body='<h1>Disposable cleanup fixture</h1>'))
                return context
            context=await launch()
            page=context.pages[0]; await page.goto('http://localhost:18888/')
            await context.add_cookies([{'name':'qa_cookie','value':'qa','url':'http://localhost:18888','expires':2147483647}])
            await page.evaluate('''async () => {
                localStorage.setItem('qa','exists');
                sessionStorage.setItem('qa','exists');
                await new Promise((resolve,reject)=>{const r=indexedDB.open('qa_cleanup');r.onupgradeneeded=()=>r.result.createObjectStore('test');r.onsuccess=()=>{r.result.close();resolve()};r.onerror=reject});
                await (await caches.open('qa_cache')).put('/qa',new Response('cached'));
            }''')
            await context.close()
            assert (profile/'Default'/'History').exists()
            _clear_profile_browsing_data(str(profile))
            assert list(profile.iterdir())==[]
            assert app_data.read_text()=='keep QA application data'
            context=await launch()
            assert not await context.cookies()
            page=context.pages[0]; await page.goto('http://localhost:18888/')
            state=await page.evaluate('''async () => ({local:localStorage.getItem('qa'),session:sessionStorage.getItem('qa'),dbs:await indexedDB.databases(),caches:await caches.keys()})''')
            assert state=={'local':None,'session':None,'dbs':[],'caches':[]},state
            await context.close()
            print('PASS real Chromium profile reset: history/artifacts removed, cookies/local/session/IndexedDB/CacheStorage empty, sibling app data preserved',flush=True)


if __name__=='__main__': asyncio.run(main())
