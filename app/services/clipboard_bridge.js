(() => {
  const binding = __BINDING_NAME__;
  if (globalThis[binding + '_installed']) return;
  Object.defineProperty(globalThis, binding + '_installed', {value: true});
  const relay = globalThis[binding];
  const maxLength = 1000000;
  const selectedText = () => {
    const element = document.activeElement;
    if (element?.type === 'password') return '';
    if (element && typeof element.selectionStart === 'number')
      return element.value.slice(element.selectionStart, element.selectionEnd);
    return window.getSelection()?.toString() || '';
  };
  const send = async text => {
    text = String(text);
    if (!text || text.length > maxLength || !await relay(text))
      throw new DOMException('Copy requires an active shared-browser viewer and recent user input.', 'NotAllowedError');
  };
  const plainText = html => {
    const template = document.createElement('template');
    template.innerHTML = html;
    return template.content.textContent || '';
  };
  // The desktop clipboard is shared by all native windows, so replace writes
  // with a page-specific relay instead of granting global clipboard access.
  if (navigator.clipboard) {
    Object.defineProperty(navigator.clipboard, 'writeText', {configurable:true, value:send});
    Object.defineProperty(navigator.clipboard, 'write', {configurable:true, value:async items => {
      for (const item of items) {
        if (item.types.includes('text/plain')) return send(await (await item.getType('text/plain')).text());
        if (item.types.includes('text/html')) return send(plainText(await (await item.getType('text/html')).text()));
      }
      throw new DOMException('Only text clipboard transfer is supported.', 'NotSupportedError');
    }});
  }
  // Older copy libraries use a temporary textarea and execCommand('copy').
  // Emulate the copy event so custom event.clipboardData handlers still work,
  // without depending on X11's globally focused window or global clipboard.
  const originalExec = document.execCommand.bind(document);
  document.execCommand = function(command, ...args) {
    if (String(command).toLowerCase() !== 'copy') return originalExec(command, ...args);
    const selection = selectedText();
    const data = new DataTransfer();
    const event = new ClipboardEvent('copy', {bubbles:true, cancelable:true, clipboardData:data});
    (document.activeElement || document.body || document).dispatchEvent(event);
    const text = event.defaultPrevented ? (data.getData('text/plain') || plainText(data.getData('text/html'))) : selection;
    if (!text || text.length > maxLength) return false;
    send(text).catch(() => {});
    return true;
  };
  // Native copy/cut events not issued through execCommand still stay scoped
  // to this page. Capture selection now, before a site removes its textarea.
  for (const type of ['copy', 'cut']) document.addEventListener(type, event => {
    if (!event.isTrusted) return;
    const selection = selectedText();
    queueMicrotask(() => {
      const text = event.defaultPrevented ? (event.clipboardData?.getData('text/plain') || '') : selection;
      if (text) send(text).catch(() => {});
    });
  }, true);
})();
