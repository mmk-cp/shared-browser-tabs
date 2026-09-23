// Files are chosen explicitly on this device and sent only to a short-lived,
// session-bound target. No server file paths or desktop clipboard are used.
export function createTransfers({command, api, notify, screen, closePanels}) {
  const $ = id => document.getElementById(id);
  const panel = $('file-panel'), input = $('local-files'), status = $('file-status');
  let chooser = null, active = null, generation = 0;
  const limit = 20 * 1024 * 1024;
  const cancelToken = token => token && command({type:'cancel_files', token}).catch(() => {});

  function cancel(sendCancellation = true) {
    generation++;
    active?.controller.abort();
    if (sendCancellation) cancelToken(active?.token || chooser?.token);
    active = null; chooser = null; input.value = '';
    panel.classList.remove('open'); $('choose-files').disabled = false;
    screen.focus({preventScroll:true});
  }
  function message(event) {
    if (event.type === 'file_error') { notify(event.message); return true; }
    if (event.type === 'filechooser_closed') {
      if (chooser?.token === event.token || active?.token === event.token) cancel(false);
      return true;
    }
    if (event.type !== 'filechooser') return false;
    cancel(false); closePanels(); chooser = event;
    input.multiple = !!event.multiple; input.accept = event.accept || '';
    status.textContent = event.multiple ? 'فایل‌ها را از دستگاه خود انتخاب کنید.' : 'یک فایل از دستگاه خود انتخاب کنید.';
    panel.classList.add('open'); $('choose-files').focus();
    return true;
  }
  const encode = file => new Promise((resolve,reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve({name:file.name || 'clipboard.png', type:file.type || 'application/octet-stream', data:reader.result.split(',')[1]});
    reader.onerror = () => reject(Error('خواندن فایل انجام نشد.'));
    reader.readAsDataURL(file);
  });
  function validate(files) {
    if (!files.length || files.length > 8) throw Error('در هر مرحله حداکثر ۸ فایل انتخاب کنید.');
    if (files.reduce((sum,file) => sum+file.size, 0) > limit) throw Error('مجموع فایل‌ها باید حداکثر ۲۰ مگابایت باشد.');
  }
  async function upload(files, target) {
    validate(files);
    if (active) throw Error('یک انتقال در حال انجام است؛ صبر کنید یا آن را لغو کنید.');
    const job = ++generation, controller = new AbortController();
    active = {token:target.token, controller};
    $('choose-files').disabled = true;
    status.textContent = 'در حال انتقال فایل به تب شما…';
    notify(target.kind === 'paste' ? 'در حال ارسال عکس…' : 'در حال ارسال فایل…');
    try {
      const encoded = [];
      for (const file of files) {
        encoded.push(await encode(file));
        if (job !== generation) return;
      }
      const result = await api('/api/tabs/me/files', {method:'POST', signal:controller.signal,
        headers:{'X-Upload-Token':target.token}, body:JSON.stringify({files:encoded})});
      if (job !== generation) return;
      chooser = null; input.value = ''; panel.classList.remove('open');
      closePanels(); screen.focus({preventScroll:true});
      notify(result.kind === 'input' ? 'فایل به بخش آپلود سایت تحویل داده شد.' : result.handled ? 'عکس به محل Paste تحویل داده شد.' : 'عکس ارسال شد؛ اگر سایت آن را نپذیرفت، از دکمهٔ آپلود سایت استفاده کنید.');
    } catch (error) {
      if (job !== generation || error.name === 'AbortError') return;
      status.textContent = error.message; notify(error.message);
    } finally {
      if (job === generation) { active = null; $('choose-files').disabled = false; }
    }
  }
  async function pasteImages(files) {
    try {
      validate(files);
      if (active) throw Error('یک انتقال در حال انجام است؛ کمی صبر کنید.');
      const target = await command({type:'prepare_paste'});
      await upload(files, {...target, kind:'paste'});
    } catch (error) { notify(error.message); }
  }
  $('choose-files').onclick = () => { if (chooser) { input.value = ''; input.click(); } };
  input.onchange = () => {
    if (!chooser || !input.files.length) return;
    upload([...input.files], chooser).catch(error => { status.textContent = error.message; notify(error.message); });
  };
  $('cancel-files').onclick = () => cancel();
  $('choose-paste-image').onclick = () => { $('local-paste-image').value = ''; $('local-paste-image').click(); };
  $('local-paste-image').onchange = event => { if (event.target.files.length) pasteImages([...event.target.files]); };
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && panel.classList.contains('open')) cancel(); });
  return {message, pasteImages, cancel};
}
