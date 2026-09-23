import RFB from '/novnc/core/rfb.js';

// Keep noVNC 1.6's encoding negotiation compatible with x11vnc's -id mode.
// DesktopSize is required when the browser is resized, including on phones.
class WindowRFB extends RFB {
  _sendEncodings() {
    // Avoid the CPU-heavy maximum compression setting. Quality 8 permits
    // high-quality JPEG for photographic regions within the Tight stream.
    const encodings = [7, 0, -223, -254, -24]; // Tight, raw, resize, compression 2, quality 8
    this._sock.sQpush8(2);
    this._sock.sQpush8(0);
    this._sock.sQpush16(encodings.length);
    encodings.forEach(value => this._sock.sQpush32(value));
    this._sock.flush();
  }
}

const $ = id => document.getElementById(id);
const screen = $('vnc-screen'), workspace = $('workspace'), state = $('connection-state');
const urlPanel = $('url-panel'), clipboardPanel = $('clipboard-panel'), menu = $('context-menu');
const mobileInput = $('mobile-input');
let rfb, input, reconnectTimer, resizeTimer, toastTimer, requestId = 0;
let remoteSize = {width:1600, height:900}, context = {}, lastUrl = '';
let stopped = false, connected = false, composing = false;
const pending = new Map();
const csrf = () => decodeURIComponent(document.cookie.split('; ').find(x => x.startsWith('shared_browser_csrf='))?.split('=')[1] || '');

function sessionEnded() {
  stopped = true; connected = false;
  clearTimeout(reconnectTimer);
  if (input) { input.onclose = null; input.close(); }
  if (rfb) rfb.disconnect();
  screen.replaceChildren();
  location.replace('/login?reason=session-ended');
}

function notify(message) {
  $('toast').textContent = message;
  $('toast').classList.add('visible');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $('toast').classList.remove('visible'), 4500);
}
async function api(path, options = {}) {
  const response = await fetch(path, {...options, headers:{'Content-Type':'application/json', 'X-CSRF-Token':csrf(), ...options.headers}});
  if (response.status === 401) { sessionEnded(); throw Error('دوباره وارد شوید'); }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw Error(data.detail || 'درخواست انجام نشد');
  return data;
}
function send(event) {
  // A staged hover must never be sent after a newer click/key action.
  if (event.type !== 'move') flushPointerMove();
  if (input?.readyState === WebSocket.OPEN) input.send(JSON.stringify(event));
}
function command(event) {
  if (input?.readyState !== WebSocket.OPEN) return Promise.reject(Error('ارتباط مرورگر قطع است'));
  const id = ++requestId;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { pending.delete(id); reject(Error('پاسخ مرورگر طول کشید')); }, 10000);
    pending.set(id, {resolve, reject, timer});
    send({...event, id});
  });
}
function showConnection(message, ready = false) {
  state.querySelector('span:last-child').textContent = message;
  state.classList.toggle('hidden', ready);
  $('online-dot').classList.toggle('connected', ready);
}
function closePanels() {
  urlPanel.classList.remove('open'); clipboardPanel.classList.remove('open');
  $('url-toggle').classList.remove('active'); $('clipboard-toggle').classList.remove('active');
  menu.hidden = true;
}
function togglePanel(panel, button) {
  const open = !panel.classList.contains('open'); closePanels();
  panel.classList.toggle('open', open); button.classList.toggle('active', open);
  if (open) panel.querySelector('input,textarea').focus();
  else screen.focus();
}
async function resizeRemote() {
  if (input?.readyState !== WebSocket.OPEN) return;
  const box = screen.getBoundingClientRect();
  const ratio = Math.min(1, 1600 / box.width, 900 / box.height);
  const size = {width:Math.max(280, Math.round(box.width*ratio)), height:Math.max(200, Math.round(box.height*ratio))};
  if (size.width === remoteSize.width && size.height === remoteSize.height) return;
  try { remoteSize = await command({type:'resize', ...size}); }
  catch (error) { notify(error.message); }
}
function scheduleResize() { clearTimeout(resizeTimer); resizeTimer = setTimeout(resizeRemote, 200); }

function reconnect() {
  if (stopped) return;
  connected = false;
  showConnection('ارتباط قطع شد؛ در حال اتصال مجدد…');
  clearTimeout(reconnectTimer);
  reconnectTimer = setTimeout(connect, 1800);
}
async function connect() {
  clearTimeout(reconnectTimer);
  if (input) { input.onclose = null; input.close(); }
  if (rfb) { rfb.removeEventListener('disconnect', reconnect); rfb.disconnect(); }
  for (const job of pending.values()) { clearTimeout(job.timer); job.reject(Error('ارتباط قطع شد')); }
  pending.clear();
  showConnection('در حال اتصال به مرورگر…');
  try {
    const tab = await api('/api/tabs/me');
    lastUrl = /^https?:/.test(tab.url) ? tab.url : '';
    $('url-input').value = lastUrl;
    const base = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}`;
    input = new WebSocket(`${base}/ws/input`);
    input.onmessage = event => {
      const message = JSON.parse(event.data), job = pending.get(message.id);
      if (job) { clearTimeout(job.timer); pending.delete(message.id); message.error ? job.reject(Error(message.error)) : job.resolve(message.result); }
    };
    await new Promise((resolve,reject) => { input.onopen = resolve; input.onerror = () => reject(Error('اتصال ورودی برقرار نشد')); });
    input.onclose = event => event.code === 4401 ? sessionEnded() : reconnect();
    // A prior client may have left this shared page at a different size.
    // Always negotiate dimensions on connection, even at the default size.
    remoteSize = {width:0, height:0};
    await resizeRemote();
    rfb = new WindowRFB(screen, `${base}/ws/vnc`, {shared:true});
    rfb.viewOnly = true; // Server also enforces this; X11 focus is desktop-wide.
    rfb.scaleViewport = true; rfb.resizeSession = false; rfb.clipViewport = false;
    rfb.background = '#101418';
    rfb.addEventListener('connect', () => {
      connected = true; showConnection('', true);
      if (!lastUrl) togglePanel(urlPanel, $('url-toggle'));
      else screen.focus({preventScroll:true});
    });
    rfb.addEventListener('disconnect', reconnect);
    rfb.addEventListener('securityfailure', () => notify('اتصال برقرار نشد'));
  } catch (error) { notify(error.message); reconnect(); }
}

async function navigate(url) {
  if (!/^https?:\/\//i.test(url)) url = `https://${url}`;
  $('url-panel').classList.add('loading');
  try {
    const tab = await api('/api/tabs/me/navigate', {method:'POST', body:JSON.stringify({url})});
    lastUrl = tab.url; $('url-input').value = lastUrl; closePanels(); screen.focus();
  } catch (error) { notify(error.message); }
  finally { $('url-panel').classList.remove('loading'); }
}
async function pageAction(action) {
  try { await api(`/api/tabs/me/${action}`, {method:'POST'}); screen.focus(); }
  catch (error) { notify(error.message); }
}
$('url-toggle').onclick = () => togglePanel(urlPanel, $('url-toggle'));
$('clipboard-toggle').onclick = () => togglePanel(clipboardPanel, $('clipboard-toggle'));
$('reload').onclick = () => pageAction('reload');
urlPanel.onsubmit = event => { event.preventDefault(); if ($('url-input').value.trim()) navigate($('url-input').value.trim()); };
$('maximize').onclick = () => { closePanels(); workspace.classList.add('focus-mode'); scheduleResize(); };
$('restore-tools').onclick = () => { workspace.classList.remove('focus-mode'); scheduleResize(); };
$('keyboard-toggle').onclick = () => {
  const open = !workspace.classList.contains('keyboard-open');
  workspace.classList.toggle('keyboard-open', open);
  if (open) mobileInput.focus(); else { mobileInput.blur(); screen.focus(); }
};

async function insertText(text) {
  if (text) await command({type:'text', text});
}
async function copyRemote() {
  const result = await command({type:'copy'});
  return result?.text || '';
}
async function writeLocal(text) {
  if (!text) { notify('ابتدا متن را انتخاب کنید'); return; }
  $('clipboard-text').value = text;
  try { await navigator.clipboard.writeText(text); notify('متن کپی شد'); }
  catch {
    clipboardPanel.classList.add('open'); $('clipboard-text').focus(); $('clipboard-text').select();
    notify('متن آماده است؛ از این کادر کپی کنید');
  }
}
async function pasteLocal() {
  try { await insertText(await navigator.clipboard.readText()); }
  catch { togglePanel(clipboardPanel, $('clipboard-toggle')); notify('متن را در کادر بچسبانید و «ارسال» را بزنید'); }
}
$('send-clipboard').onclick = async () => {
  try { await insertText($('clipboard-text').value); closePanels(); screen.focus(); }
  catch (error) { notify(error.message); }
};
$('copy-clipboard').onclick = async () => { try { await writeLocal(await copyRemote()); } catch(error) { notify(error.message); } };

// Client-side context menu: native Chromium popup windows are not part of
// the exported X11 window. Selection and clipboard stay scoped to this tab.
async function showContext(x, y, point) {
  closePanels();
  try { context = await command({type:'context', ...point}); }
  catch (error) { notify(error.message); return; }
  menu.querySelector('[data-command=copy]').disabled = !context.text;
  menu.querySelector('[data-command=open_link]').hidden = !context.href;
  menu.querySelector('[data-command=copy_link]').hidden = !context.href;
  menu.hidden = false;
  menu.style.left = `${Math.max(4, Math.min(x, innerWidth-menu.offsetWidth-4))}px`;
  menu.style.top = `${Math.max(4, Math.min(y, innerHeight-menu.offsetHeight-4))}px`;
}
menu.onclick = async event => {
  const action = event.target.closest('[data-command]')?.dataset.command;
  if (!action) return;
  menu.hidden = true;
  try {
    if (action === 'copy') await writeLocal(context.text);
    else if (action === 'paste') await pasteLocal();
    else if (action === 'select_all') await command({type:'select_all'});
    else if (action === 'open_link') await navigate(context.href);
    else if (action === 'copy_link') await writeLocal(context.href);
    else await pageAction(action);
  } catch (error) { notify(error.message); }
};
function coords(event) {
  const canvas = screen.querySelector('canvas'), box = canvas?.getBoundingClientRect();
  if (!box || !box.width) return {x:0, y:0};
  return {x:Math.max(0,Math.min(remoteSize.width-1,(event.clientX-box.left)*remoteSize.width/box.width)),
          y:Math.max(0,Math.min(remoteSize.height-1,(event.clientY-box.top)*remoteSize.height/box.height))};
}
screen.addEventListener('contextmenu', event => { event.preventDefault(); event.stopImmediatePropagation(); }, true);
let touch = null, holdTimer, moved = false, lastTap = 0, clicks = 1, lastMove, moveFrame;
function flushPointerMove() {
  if (moveFrame) cancelAnimationFrame(moveFrame);
  moveFrame = null;
  if (lastMove) { const point = lastMove; lastMove = null; send({type:'move', ...point}); }
}
screen.addEventListener('pointerdown', event => {
  if (!connected) return;
  event.preventDefault(); event.stopImmediatePropagation(); closePanels();
  if (!workspace.classList.contains('keyboard-open')) screen.focus({preventScroll:true});
  const point = coords(event);
  screen.setPointerCapture(event.pointerId);
  if (event.pointerType === 'touch') {
    moved = false; touch = {point,x:event.clientX,y:event.clientY,lastX:event.clientX,lastY:event.clientY,held:false};
    holdTimer = setTimeout(() => { if (touch && !moved) { touch.held = true; showContext(event.clientX,event.clientY,point); } }, 600);
  } else if (event.button === 2) showContext(event.clientX,event.clientY,point);
  else { clicks = performance.now()-lastTap < 350 ? Math.min(3,clicks+1) : 1; lastTap=performance.now(); send({type:'down', ...point, count:clicks,button:event.button===1?'middle':'left'}); }
}, true);
screen.addEventListener('pointermove', event => {
  event.stopImmediatePropagation();
  if (touch) {
    if (Math.hypot(event.clientX-touch.x,event.clientY-touch.y)>7) { moved=true; clearTimeout(holdTimer); }
    if (moved && !touch.held) send({type:'wheel',...coords(event),dx:(touch.lastX-event.clientX),dy:(touch.lastY-event.clientY)});
    touch.lastX=event.clientX; touch.lastY=event.clientY;
  } else {
    lastMove={...coords(event),buttons:event.buttons};
    if (!moveFrame) moveFrame=requestAnimationFrame(flushPointerMove);
  }
}, true);
screen.addEventListener('pointerup', event => {
  event.preventDefault(); event.stopImmediatePropagation(); clearTimeout(holdTimer);
  if (touch) {
    if (!moved && !touch.held) { send({type:'down',...touch.point}); send({type:'up',...touch.point}); }
    touch=null;
  } else if (event.button !== 2) send({type:'up',...coords(event),count:clicks,button:event.button===1?'middle':'left'});
}, true);
screen.addEventListener('pointercancel', () => { clearTimeout(holdTimer); touch=null; send({type:'release'}); });
screen.addEventListener('wheel', event => {
  event.preventDefault(); event.stopImmediatePropagation();
  const scale=event.deltaMode===1?16:event.deltaMode===2?screen.clientHeight:1;
  send({type:'wheel', ...coords(event), dx:event.deltaX*scale, dy:event.deltaY*scale});
}, {capture:true, passive:false});

// All text and key events share one ordered WebSocket, avoiding the earlier
// race between Persian HTTP text insertion and VNC space/backspace events.
screen.addEventListener('keydown', event => {
  event.preventDefault(); event.stopImmediatePropagation();
  if (event.isComposing || event.key === 'Process' || event.key === 'Dead') return;
  const shortcut=event.ctrlKey||event.metaKey, key=event.key.toLowerCase();
  if (shortcut && ['v','c','x'].includes(key)) {
    send({type:'release'});
    if(key==='v') pasteLocal();
    else copyRemote().then(async text => { await writeLocal(text); if(key==='x' && text) { send({type:'key_down',key:'Backspace'}); send({type:'key_up',key:'Backspace'}); } }).catch(e=>notify(e.message));
  } else if (shortcut && key==='l') { send({type:'release'}); togglePanel(urlPanel,$('url-toggle')); }
  else if (shortcut && key==='r') { send({type:'release'}); pageAction('reload'); }
  else if (shortcut && ['t','n','w','u'].includes(key)) send({type:'release'});
  else if (!shortcut && !event.altKey && [...event.key].length===1) send({type:'text',text:event.key});
  else send({type:'key_down',key:event.key});
},true);
screen.addEventListener('keyup', event => { event.preventDefault(); event.stopImmediatePropagation(); if(event.key.length>1||event.ctrlKey||event.metaKey||event.altKey) send({type:'key_up',key:event.key}); },true);
screen.addEventListener('compositionend', event => { if(event.data) send({type:'text',text:event.data}); },true);
screen.addEventListener('paste', event => { event.preventDefault(); event.stopImmediatePropagation(); send({type:'text',text:event.clipboardData.getData('text/plain')}); },true);
screen.addEventListener('blur', () => send({type:'release'}));
mobileInput.addEventListener('compositionstart', () => { composing=true; });
function flushMobile() { if(mobileInput.value) send({type:'text',text:mobileInput.value}); mobileInput.value=''; }
mobileInput.addEventListener('compositionend', () => { composing=false; flushMobile(); });
mobileInput.addEventListener('input', event => { if(!composing&&!event.isComposing) flushMobile(); });
mobileInput.addEventListener('keydown', event => {
  if (['Backspace','Delete','Enter','Tab','ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key) && !event.isComposing) {
    event.preventDefault(); send({type:'key_down',key:event.key}); send({type:'key_up',key:event.key});
  }
});
mobileInput.addEventListener('beforeinput', event => {
  const key = {deleteContentBackward:'Backspace', deleteContentForward:'Delete', insertLineBreak:'Enter', insertParagraph:'Enter'}[event.inputType];
  if (key && !event.isComposing) {
    event.preventDefault(); send({type:'key_down',key}); send({type:'key_up',key});
  }
});
document.addEventListener('pointerdown', event => { if(!event.target.closest('.floating-panel,.side-rail,.context-menu,#mobile-input')) closePanels(); });
document.addEventListener('keydown', event => { if(event.key==='Escape') closePanels(); });
new ResizeObserver(scheduleResize).observe(screen);
function viewportHeight() { workspace.style.height = `${window.visualViewport?.height || innerHeight}px`; scheduleResize(); }
window.visualViewport?.addEventListener('resize',viewportHeight);
window.addEventListener('resize',viewportHeight);
$('logout').onclick = async () => { stopped=true; await api('/api/auth/logout',{method:'POST'}); location.href='/login'; };
viewportHeight(); connect();
