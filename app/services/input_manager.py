"""Tab-scoped input; the VNC server is deliberately display-only.

X11 has one desktop-wide keyboard focus and clipboard. Sending input to it
would let simultaneous users type into each other's windows. CDP targets the
authenticated Page instead, including Unicode and mobile composition text.
"""
from playwright.async_api import Page


async def selection_text(page: Page) -> str:
    for frame in page.frames:
        try:
            result = await frame.evaluate("""() => {
                const el = document.activeElement;
                if (el?.type === 'password') return '';
                if (el && typeof el.selectionStart === 'number')
                    return el.value.substring(el.selectionStart, el.selectionEnd);
                return window.getSelection()?.toString() || '';
            }""")
            if result:
                return result
        except Exception:
            continue
    return ""


async def context_info(page: Page, x: float, y: float) -> dict:
    info = await page.evaluate("""({x,y}) => {
        const el = document.elementFromPoint(x,y);
        const link = el?.closest('a[href]');
        const editable = el?.closest('input,textarea,[contenteditable=true]');
        if (editable && document.activeElement !== editable) editable.focus({preventScroll:true});
        return {href: link && /^https?:/.test(link.href) ? link.href : '',
                editable: !!editable};
    }""", {"x": x, "y": y})
    info["text"] = await selection_text(page)
    return info


async def handle_input(page: Page, event: dict) -> dict | None:
    kind = event.get("type")
    if kind in {"move", "down", "up"}:
        x = max(0, min(1800, float(event.get("x", 0))))
        y = max(0, min(1300, float(event.get("y", 0))))
        await page.mouse.move(x, y)
        if kind != "move":
            button = event.get("button", "left")
            if button not in {"left", "middle"}:
                return None  # Native right-click popups are not in the framebuffer.
            method = page.mouse.down if kind == "down" else page.mouse.up
            await method(button=button, click_count=min(3, max(1, int(event.get("count", 1)))))
    elif kind == "wheel":
        if "x" in event and "y" in event:
            await page.mouse.move(max(0, min(1800, float(event["x"]))), max(0, min(1300, float(event["y"]))))
        await page.mouse.wheel(max(-2000, min(2000, float(event.get("dx", 0)))),
                               max(-2000, min(2000, float(event.get("dy", 0)))))
    elif kind == "text":
        text = str(event.get("text", ""))
        if len(text) > 1_000_000:
            raise ValueError("Text is too large")
        await page.keyboard.insert_text(text)
    elif kind in {"key_down", "key_up"}:
        key = str(event.get("key", ""))
        if key and len(key) < 40:
            await (page.keyboard.down(key) if kind == "key_down" else page.keyboard.up(key))
    elif kind == "context":
        return await context_info(page, float(event.get("x", 0)), float(event.get("y", 0)))
    elif kind == "copy":
        return {"text": await selection_text(page)}
    elif kind == "select_all":
        await page.keyboard.press("Control+a")
    elif kind == "release":
        for key in ("Control", "Shift", "Alt", "Meta"):
            await page.keyboard.up(key)
        await page.mouse.up()
    return None
