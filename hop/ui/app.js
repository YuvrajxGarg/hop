// Hop UI. Talks to Python through window.pywebview.api; Python pushes updates back
// by calling App.on(event, payload). Served over http (HOP_UI_DEV) it uses a fetch bridge.
(function () {
  const ICONS = {
    upload: '<path d="M12 15V4"/><path d="m7 9 5-5 5 5"/><path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/>',
    file: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
    folder: '<path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9L9.6 3.6A2 2 0 0 0 8.2 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2z"/>',
    shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/>',
    layers: '<path d="m12 2 10 5-10 5L2 7z"/><path d="m2 17 10 5 10-5"/><path d="m2 12 10 5 10-5"/>',
    check: '<path d="M20 6 9 17l-5-5"/>',
    x: '<path d="M18 6 6 18M6 6l12 12"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    reveal: '<path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
    alert: '<path d="M12 9v4M12 17h.01"/><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>',
    gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
    download: '<path d="M12 4v11"/><path d="m7 10 5 5 5-5"/><path d="M4 20h16"/>',
    refresh: '<path d="M21 12a9 9 0 1 1-2.6-6.4L21 8"/><path d="M21 3v5h-5"/>',
    lock: '<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
    bolt: '<path d="M13 2 3 14h9l-1 8 10-12h-9z"/>',
    send: '<path d="M22 2 11 13"/><path d="M22 2 15 22l-4-9-9-4z"/>',
    laptop: '<rect x="4" y="4" width="16" height="11" rx="2"/><path d="M2 19h20"/>',
    monitor: '<rect x="3" y="3" width="18" height="13" rx="2"/><path d="M8 21h8M12 16v5"/>',
    phone: '<rect x="6" y="2" width="12" height="20" rx="3"/><path d="M11 18h2"/>',
    inbox: '<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.5 5.1 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.5-6.9A2 2 0 0 0 16.8 4H7.2a2 2 0 0 0-1.7 1.1z"/>',
    out: '<path d="M7 17 17 7"/><path d="M8 7h9v9"/>',
    in: '<path d="M17 7 7 17"/><path d="M16 17H7V8"/>',
    stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
    globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3a14 14 0 0 1 0 18a14 14 0 0 1 0-18z"/>',
    copy: '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1"/>',
    wifi: '<path d="M5 12.6a10 10 0 0 1 14 0"/><path d="M8.5 16a5 5 0 0 1 7 0"/><path d="M2 8.8a15 15 0 0 1 20 0"/><path d="M12 20h.01"/>',
  };
  const icon = (n) => `<svg class="i" viewBox="0 0 24 24">${ICONS[n] || ''}</svg>`;
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const human = (n) => { const u = ['B', 'KB', 'MB', 'GB', 'TB']; let i = 0; n = +n || 0; while (n >= 1024 && i < 4) { n /= 1024; i++; } return i ? n.toFixed(n < 10 ? 1 : 0) + ' ' + u[i] : n + ' B'; };
  const dur = (s) => s < 60 ? Math.max(1, Math.round(s)) + 's' : s < 3600 ? Math.round(s / 60) + ' min' : (s / 3600).toFixed(1) + ' h';
  document.querySelectorAll('[data-icon]').forEach((el) => {
    const svg = icon(el.dataset.icon);
    if (el.tagName === 'I') el.outerHTML = svg; else el.insertAdjacentHTML('afterbegin', svg);
  });

  let api = null, settings = {}, upd = null, modal = null, home = '', info = {}, relay = null;
  let peers = [], selection = { count: 0 }, chosen = null, pairing = null;
  const transfers = new Map();
  const tilde = (p) => (home && p && p.startsWith(home) ? '~' + p.slice(home.length) : p);

  // ---------- devices ----------
  const devIcon = (p) => p.id === 'phone' ? 'phone' : p.remote ? 'globe' : p.platform === 'mac' ? 'laptop' : 'monitor';
  function renderDevices() {
    const list = peers.concat([{ id: 'phone', name: 'Phone or tablet', online: true, phone: true }]);
    if (chosen && !list.some((p) => p.id === chosen && p.online)) chosen = null;
    if (!chosen) { const first = list.find((p) => p.online && p.id !== 'phone'); if (first) chosen = first.id; }
    const sub = (p) => p.phone ? 'Opens in its browser, nothing to install' : p.remote ? 'Anywhere · ' + p.code : !p.online ? 'Not on this network' :
      (p.paired ? 'Paired' : 'Pair once with a code') + (p.platform ? ' · ' + ({ mac: 'Mac', windows: 'Windows', linux: 'Linux' }[p.platform] || p.platform) : '');
    $('#devices').innerHTML = (peers.some((p) => p.online) ? '' : `<div class="none">No one here yet. Computers on this Wi-Fi show up by themselves. For anyone else, add their Hop code.</div>`) +
      list.filter((p) => p.online || p.paired).map((p) => `<button class="dev ${p.id === chosen ? 'on' : ''} ${p.online ? '' : 'offline'}" data-id="${esc(p.id)}">
        <div class="di">${icon(devIcon(p))}${p.paired && !p.remote ? `<span class="lock">${icon('lock')}</span>` : ''}</div>
        <div class="t"><b>${esc(p.name || 'Unknown')}</b><span>${sub(p)}</span></div></button>`).join('');
    refreshSend();
  }
  $('#devices').onclick = (e) => {
    const b = e.target.closest('.dev');
    if (!b || b.classList.contains('offline')) return;
    chosen = b.dataset.id;
    renderDevices();
  };

  function renderSelection() {
    const has = selection.count > 0;
    $('#selection').classList.toggle('hidden', !has);
    document.querySelector('.composer').classList.toggle('has-sel', has);
    $('#dropzone b').textContent = has ? 'Drop more' : 'Drop files to send';
    if (has) {
      const n = selection.names || [];
      $('#sel-title').textContent = selection.items === 1 ? n[0] : `${n[0]} and ${selection.items - 1} more`;
      $('#sel-sub').textContent = `${selection.count} file${selection.count === 1 ? '' : 's'} · ${selection.size}`;
    }
    refreshSend();
  }
  function refreshSend() {
    const b = $('#send-btn');
    const p = peers.find((x) => x.id === chosen);
    const name = chosen === 'phone' ? 'phone' : p ? p.name : '';
    b.disabled = !selection.count || !chosen;
    b.querySelector('span').textContent = !selection.count ? 'Send' : !chosen ? 'Pick a device' :
      chosen !== 'phone' && p && !p.paired ? `Pair and send to ${name}` : `Send to ${name}`;
  }

  // ---------- transfers ----------
  function pill(t) {
    const pct = t.total ? Math.floor(100 * t.done / t.total) : 100;
    const m = {
      preparing: ['', 'Preparing', true], asking: t.dir === 'in' ? ['wait', 'Needs your OK'] : ['', 'Waiting for OK', true],
      sending: ['', pct + '%', true], receiving: ['', pct + '%', true], waiting: ['wait', 'Reconnecting', true],
      ready: ['', 'Waiting for phone', true], done: ['done', t.dir === 'in' ? 'Received' : 'Sent', false, 'check'],
      failed: ['failed', 'Failed', false, 'alert'], declined: ['failed', 'Declined'], cancelled: ['', 'Stopped'],
    }[t.status] || ['', t.status];
    return `<span class="status ${m[0]} ${m[2] ? 'busy' : ''}">${m[3] ? icon(m[3]) : ''}${m[1]}</span>`;
  }
  function rowHTML(t) {
    const title = t.label ? `${t.label} folder` : t.names[0] + (t.count > 1 ? ` and ${t.count - 1} more` : '');
    const active = ['sending', 'receiving', 'preparing'].includes(t.status);
    const left = t.speed > 0 ? (t.total - t.done) / t.speed : 0;
    const subBits = [`${t.dir === 'in' ? 'from' : 'to'} <b>${esc(t.peer_name)}</b>`, `${t.count} file${t.count === 1 ? '' : 's'}`, human(t.total)];
    if (active && t.speed > 0) subBits.push(`${human(t.speed)}/s`, `${dur(left)} left`);
    let sub = subBits.join(' · ');
    if (t.error && !['done'].includes(t.status)) sub = `<span class="err">${esc(t.error)}</span>`;
    else if (t.status === 'ready') sub = `Open Hop on your phone to download · ${human(t.total)}`;
    const bar = active || t.status === 'waiting' || (t.status === 'ready' && t.done) ? `<div class="tbar ${t.status === 'preparing' ? 'hash' : ''}"><div style="width:${t.total ? 100 * t.done / t.total : 0}%"></div></div>` : '';
    const acts = [];
    if (t.dir === 'in' && t.status === 'asking') acts.push(`<button class="btn-quiet" data-act="accept">Review</button>`);
    if (t.status === 'done' && t.first) acts.push(`<button class="btn-icon" data-act="reveal" title="Show in folder">${icon('reveal')}</button>`);
    if (['failed', 'cancelled'].includes(t.status) && (t.kind === 'remote' || (t.dir === 'out' && t.kind === 'computer'))) acts.push(`<button class="btn-icon" data-act="retry" title="Try again">${icon('refresh')}</button>`);
    if (['preparing', 'asking', 'sending', 'receiving', 'waiting', 'ready'].includes(t.status)) acts.push(`<button class="btn-icon" data-act="cancel" title="Stop">${icon('stop')}</button>`);
    else acts.push(`<button class="btn-icon" data-act="remove" title="Remove from list">${icon('x')}</button>`);
    return `<div class="ticon ${t.dir}">${icon(t.kind === 'phone' ? 'phone' : t.kind === 'remote' ? 'globe' : t.dir === 'in' ? 'in' : 'out')}</div>
      <div class="meta"><div class="title">${esc(title)}</div>${bar}<div class="sub">${sub}</div></div>
      ${pill(t)}<div class="acts">${acts.join('')}</div>`;
  }
  function upsert(t) {
    transfers.set(t.tid, t);
    let el = document.getElementById('t-' + t.tid);
    if (!el) {
      el = document.createElement('div');
      el.className = 'trow';
      el.id = 't-' + t.tid;
      el.dataset.tid = t.tid;
      $('#list').prepend(el);
    }
    el.innerHTML = rowHTML(t);
    refreshStats();
  }
  function refreshStats() {
    const all = [...transfers.values()];
    $('#empty').classList.toggle('hidden', all.length > 0);
    const active = all.filter((t) => ['sending', 'receiving', 'preparing', 'waiting', 'asking'].includes(t.status)).length;
    const got = all.filter((t) => t.dir === 'in' && t.status === 'done').reduce((a, t) => a + t.count, 0);
    const sent = all.filter((t) => t.dir === 'out' && t.status === 'done').reduce((a, t) => a + t.count, 0);
    $('#stats').innerHTML = all.length ? `
      ${active ? `<span class="stat lime"><b>${active}</b>in progress</span>` : ''}
      ${sent ? `<span class="stat"><b>${sent}</b>sent</span>` : ''}
      ${got ? `<span class="stat"><b>${got}</b>received</span>` : ''}` : '';
  }
  $('#list').onclick = (e) => {
    const row = e.target.closest('.trow');
    const act = e.target.closest('[data-act]');
    if (!row || !act) return;
    const t = transfers.get(row.dataset.tid);
    const a = act.dataset.act;
    if (a === 'reveal') api.reveal(t.first);
    else if (a === 'cancel') api.cancel(t.tid);
    else if (a === 'retry') api.retry(t.tid);
    else if (a === 'accept') showOffer(t);
    else if (a === 'remove') { api.remove(t.tid); transfers.delete(t.tid); row.remove(); refreshStats(); }
  };

  function toast(text, err, action) {
    const t = document.createElement('div');
    t.className = 'toast' + (err ? ' err' : '');
    t.innerHTML = icon(err ? 'alert' : 'check') + `<span>${esc(text)}</span>`;
    if (action) {
      const b = document.createElement('button');
      b.textContent = action.label;
      b.onclick = action.run;
      t.appendChild(b);
    }
    $('#toasts').appendChild(t);
    setTimeout(() => { t.style.transition = 'opacity .3s'; t.style.opacity = 0; setTimeout(() => t.remove(), 300); }, 4200);
  }

  // ---------- modals ----------
  function closeModal() { $('#modal-root').innerHTML = ''; modal = null; }
  function showModal(name, html, onClick, cls) {
    modal = name;
    $('#modal-root').innerHTML = `<div class="modal-back"><div class="modal ${cls || ''}">${html}</div></div>`;
    $('#modal-root .modal-back').onclick = (e) => {
      if (e.target.classList.contains('modal-back')) return onClick('backdrop');
      const el = e.target.closest('[data-m]');
      if (el) onClick(el.dataset.m, el);
    };
  }
  const sw = (on) => `<span class="switch ${on ? 'on' : ''}"></span>`;
  const opt = (key, title, detail) => `<button class="opt" data-m="toggle" data-key="${key}"><div class="t"><b>${title}</b><span>${detail}</span></div>${sw(settings[key])}</button>`;

  // Sending side of pairing: type the code that's showing on the other device.
  async function startPair(peer) {
    pairing = peer;
    const r = await api.pair_begin(peer.id);
    if (!r.ok) { pairing = null; return toast(r.error, true); }
    showModal('pair-enter', `
      <div class="code-hero">
        <div class="logomark lg">${icon('lock')}</div>
        <h2>Pair with ${esc(peer.name)}</h2>
        <p>A 6-digit code is showing on ${esc(peer.name)} now. Type it here. You only do this once per device.</p>
        <div class="codein" id="codein">${[0, 1, 2, 3, 4, 5].map((i) => `${i === 3 ? '<span class="gap"></span>' : ''}<input inputmode="numeric" maxlength="1" data-i="${i}">`).join('')}</div>
        <div class="err hidden" id="pair-err"></div>
      </div>
      <div class="foot"><button class="btn-quiet" data-m="cancel">Cancel</button></div>`,
      (m) => { if (m === 'cancel' || m === 'backdrop') { api.pair_cancel(peer.id); pairing = null; closeModal(); } }, 'narrow');
    const ins = [...document.querySelectorAll('#codein input')];
    ins[0].focus();
    const submit = async () => {
      const code = ins.map((i) => i.value).join('');
      if (code.length < 6) return;
      ins.forEach((i) => i.disabled = true);
      const res = await api.pair_finish(peer.id, code);
      if (res.ok) {
        closeModal();
        pairing = null;
        toast(`Paired with ${peer.name}`);
        doSend(peer.id);
      } else {
        const box = $('#codein');
        if (!box) return;
        box.classList.remove('bad'); void box.offsetWidth; box.classList.add('bad');
        $('#pair-err').classList.remove('hidden');
        $('#pair-err').textContent = `${res.error || "That code didn't match"}. ${esc(peer.name)} is showing a new code.`;
        await api.pair_begin(peer.id);
        ins.forEach((i) => { i.disabled = false; i.value = ''; });
        ins[0].focus();
      }
    };
    ins.forEach((inp, i) => {
      inp.addEventListener('input', () => {
        const d = inp.value.replace(/\D/g, '');
        if (d.length > 1) { d.split('').slice(0, 6 - i).forEach((c, k) => ins[i + k].value = c); }
        else inp.value = d;
        const next = ins.find((x) => !x.value);
        if (next) next.focus(); else submit();
      });
      inp.addEventListener('keydown', (e) => { if (e.key === 'Backspace' && !inp.value && i) { ins[i - 1].value = ''; ins[i - 1].focus(); } });
    });
  }

  // Receiving side of pairing: show the code.
  function showCode(p) {
    showModal('code:' + p.pid, `
      <div class="code-hero">
        <h2>${esc(p.name)} wants to pair</h2>
        <p>Type this code on ${esc(p.name)}. It works once.</p>
        <div class="bigcode">${esc(p.code.slice(0, 3))}<em></em>${esc(p.code.slice(4))}</div>
        <p class="hint">Didn't start this? Close it. Nobody can pair without the code.</p>
      </div>
      <div class="foot"><button class="btn-quiet" data-m="close">Close</button></div>`, (m) => { if (m === 'close' || m === 'backdrop') closeModal(); }, 'narrow');
  }

  function showOffer(t) {
    const files = t.names.map((n) => `<div><span class="k">${esc(n)}</span></div>`).join('') + (t.count > 3 ? `<div><span class="k faint">and ${t.count - 3} more</span></div>` : '');
    showModal('offer:' + t.tid, `
      <div class="code-hero">
        <div class="logomark lg">${icon('in')}</div>
        <h2>${esc(t.peer_name)} wants to send ${t.count === 1 ? 'a file' : t.count + ' files'}</h2>
        <p>${human(t.total)} · saved to ${esc(tilde(settings.save_dir))}</p>
      </div>
      <div class="offer-files">${files}</div>
      <button class="opt" data-m="always"><div class="t"><b>Always accept from paired devices</b><span>${t.kind === 'remote' ? 'And from people you’ve added by Hop code' : 'Skip this question next time'}</span></div>${sw(settings.auto_accept)}</button>
      <div class="foot"><button class="btn-quiet" data-m="decline">Decline</button><button class="btn-primary inline" data-m="accept">${icon('download')}Accept</button></div>`,
      async (m, el) => {
        if (m === 'accept') { api.decide(t.tid, true); closeModal(); }
        else if (m === 'decline') { api.decide(t.tid, false); closeModal(); }
        else if (m === 'backdrop') closeModal();
        else if (m === 'always') { settings = await api.set_setting('auto_accept', !settings.auto_accept); el.querySelector('.switch').classList.toggle('on', !!settings.auto_accept); }
      }, 'narrow');
  }

  async function showPhone(fresh) {
    const ph = await api.phone(!!fresh);
    showModal('phone', `
      <div class="modal-title"><h2>Send to and from a phone</h2><button class="btn-icon" data-m="close">${icon('x')}</button></div>
      <div class="phone-wrap">
        <div class="qr">${ph.qr}</div>
        <ol>
          <li>Join the <b>same Wi-Fi</b> as this computer.</li>
          <li>Point the <b>camera</b> at the code and open the link.</li>
          <li>Pick photos to send here, or download what you sent to the phone. Nothing to install.</li>
        </ol>
      </div>
      <div class="hint">The key is in the code, never sent over the network, so only phones that scan it can read the files. New link makes old scans stop working.</div>
      <div class="url">${esc(ph.url.split('#')[0])}</div>
      <div class="foot"><button class="link" data-m="fresh">${icon('refresh')}New link</button><button class="btn-quiet" data-m="close">Done</button></div>`,
      (m) => { if (m === 'close' || m === 'backdrop') closeModal(); else if (m === 'fresh') showPhone(true); }, 'phone');
  }

  // Add someone by their Hop code (works on any network), or a computer by address.
  function codeBox() {
    const r = relay || {};
    return `<div class="mycode"><div class="t"><span class="k">Your Hop code</span><b>${esc(r.pretty || '…')}</b>
        <span class="${r.online ? 'ok' : 'faint'}">${r.enabled === false ? 'Turned off in Settings' : r.online ? 'Others can add you with it' : esc(r.error || 'Connecting…')}</span></div>
      <button class="btn-quiet" data-m="copy">${icon('copy')}Copy</button></div>`;
  }
  function addPerson() {
    showModal('person', `
      <div class="modal-title"><h2>Add someone</h2><button class="btn-icon" data-m="close">${icon('x')}</button></div>
      <p class="hint">Type their Hop code. They'll find it in Hop under <b>Add someone</b>. Works across any network, end-to-end encrypted.</p>
      <input class="text-in code-in" id="code-in" placeholder="7KQ2-9XMD-4TRA" spellcheck="false" autocomplete="off" maxlength="16">
      <div class="err hidden" id="code-err"></div>
      ${codeBox()}
      <div class="foot"><button class="btn-link" data-m="addr">Same Wi-Fi but not showing up? Add by address</button>
        <span class="grow"></span><button class="btn-quiet" data-m="close">Cancel</button><button class="btn-primary inline" data-m="add">Add</button></div>`,
      async (m) => {
        if (m === 'close' || m === 'backdrop') return closeModal();
        if (m === 'addr') return addAddress();
        if (m === 'copy') { copyText(relay && relay.pretty); return; }
        if (m === 'add') {
          const btn = document.querySelector('.modal [data-m="add"]');
          btn.disabled = true;
          const r = await api.add_code($('#code-in').value.trim());
          btn.disabled = false;
          if (r.ok) { peers = await api.peers(); chosen = r.peer.id; renderDevices(); closeModal(); toast(`Added ${r.peer.name}`); }
          else { $('#code-err').textContent = r.error; $('#code-err').classList.remove('hidden'); }
        }
      }, 'narrow');
    const inp = $('#code-in');
    inp.focus();
    inp.oninput = () => {
      const raw = inp.value.toUpperCase().replace(/[^0-9A-Z]/g, '').slice(0, 12);
      inp.value = raw.match(/.{1,4}/g) ? raw.match(/.{1,4}/g).join('-') : '';
    };
    inp.onkeydown = (e) => { if (e.key === 'Enter') document.querySelector('[data-m="add"]').click(); };
  }
  function copyText(t) {
    if (!t) return;
    const done = () => toast('Copied your Hop code');
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(t).then(done, () => api.copy(t).then(done));
    else api.copy(t).then(done);
  }

  function addAddress() {
    showModal('addr', `
      <div class="modal-title"><h2>Add a computer by address</h2><button class="btn-icon" data-m="close">${icon('x')}</button></div>
      <p class="hint">For networks that block Bonjour (some offices and hotels). The other computer shows its address in Settings.</p>
      <input class="text-in" id="addr-in" placeholder="192.168.1.20 or 192.168.1.20:47470" spellcheck="false">
      <div class="err hidden" id="addr-err"></div>
      <div class="foot"><button class="btn-quiet" data-m="close">Cancel</button><button class="btn-primary inline" data-m="add">Add</button></div>`,
      async (m) => {
        if (m === 'close' || m === 'backdrop') return closeModal();
        if (m === 'add') {
          const r = await api.add_address($('#addr-in').value.trim());
          if (r.ok) { chosen = r.peer.id; peers = await api.peers(); renderDevices(); closeModal(); }
          else { $('#addr-err').textContent = r.error; $('#addr-err').classList.remove('hidden'); }
        }
      }, 'narrow');
    $('#addr-in').focus();
    $('#addr-in').onkeydown = (e) => { if (e.key === 'Enter') document.querySelector('[data-m="add"]').click(); };
  }

  // ---------- settings & updates ----------
  const ago = (iso) => {
    if (!iso) return 'Never checked';
    const s = (Date.now() - new Date(iso).getTime()) / 1000;
    if (s < 60) return 'Checked just now';
    if (s < 3600) return `Checked ${Math.round(s / 60)} min ago`;
    if (s < 86400) return `Checked ${Math.round(s / 3600)} h ago`;
    return 'Checked ' + new Date(iso).toLocaleDateString();
  };
  function updateLine() {
    if (!upd) return ['', ''];
    switch (upd.phase) {
      case 'checking': return ['Checking for updates…', ''];
      case 'uptodate': return ["You're up to date", 'ok'];
      case 'available': return [`Version ${upd.latest} is available`, 'ok'];
      case 'downloading': return [`Downloading ${upd.latest}… ${Math.round(upd.progress * 100)}%`, ''];
      case 'ready': return [`Version ${upd.latest} is ready to install`, 'ok'];
      case 'installing': return ['Installing…', ''];
      case 'failed': return [upd.error || 'Update failed', 'bad'];
      default: return [ago(upd.last_checked), ''];
    }
  }
  function renderUpdateRow() {
    const el = $('#upd-row');
    if (!el) return;
    const [line, cls] = updateLine();
    const has = upd && ['available', 'downloading', 'ready'].includes(upd.phase);
    el.innerHTML = `<div class="t"><b>Hop ${esc(upd ? upd.current : '')}</b><span class="${cls}">${esc(line)}</span></div>
      ${has ? `<button class="btn-quiet" data-m="see-update">${icon('download')}See update</button>` : `<button class="btn-quiet" data-m="check" ${upd && upd.phase === 'checking' ? 'disabled' : ''}>${icon('refresh')}Check now</button>`}`;
  }
  async function openSettings(focusName) {
    peers = await api.peers();
    const paired = peers.filter((p) => p.paired && !p.remote);
    const people = peers.filter((p) => p.remote);
    showModal('settings', `
      <div class="modal-title"><h2>Settings</h2><button class="btn-icon" data-m="close">${icon('x')}</button></div>
      <div class="label">This computer</div>
      <div class="group">
        <div><span class="k">Name other devices see</span></div>
        <div style="padding-top:0"><input class="text-in" id="name-in" value="${esc(settings.device_name)}" maxlength="40" spellcheck="false"></div>
        <div><span class="k">Hop code</span><span class="v url">${esc(relay ? relay.pretty : '')}</span><button class="btn-quiet" data-m="copy">${icon('copy')}Copy</button></div>
        <div><span class="k">Address</span><span class="v url" style="font-size:12px">${esc(info.ip)}:${esc(info.port)}</span></div>
      </div>
      <div class="label">Sending by Hop code</div>
      <div class="group">
        ${opt('relay', 'Send and receive by Hop code', 'For people on other networks. Files are end-to-end encrypted, held on hop.pxlvzn.in for at most 7 days, and deleted once received.')}
        ${people.length ? people.map((p) => `<div><span class="k">${esc(p.name)} <span class="faint">${esc(p.code)}</span></span><button class="btn-quiet" data-m="forget" data-id="${esc(p.id)}">Remove</button></div>`).join('') : '<div class="empty-row">No one added yet. Use Add someone by Hop code.</div>'}
      </div>
      <div class="label">Receiving</div>
      <div class="group">
        <button class="opt" data-m="savedir"><div class="t"><b>Save files to</b><span>${esc(tilde(settings.save_dir))}</span></div><span class="btn-quiet">Change</span></button>
        ${opt('auto_accept', 'Accept from paired devices without asking', 'And from people you’ve added by Hop code. Phones that scanned your code are always accepted')}
        ${opt('open_after', 'Show files when they arrive', 'Opens the folder in Finder or Explorer')}
      </div>
      <div class="label">Paired devices</div>
      <div class="group">${paired.length ? paired.map((p) => `<div><span class="k">${esc(p.name)}</span><button class="btn-quiet" data-m="forget" data-id="${esc(p.id)}">Forget</button></div>`).join('') : '<div class="empty-row">None yet. Devices pair the first time you send.</div>'}</div>
      <div class="label">Updates</div>
      <div class="group">
        <div class="ver-row" id="upd-row"></div>
        ${opt('auto_check', 'Check for updates automatically', 'Looks for a new version at launch and every few hours')}
        ${opt('auto_install', 'Download and install updates automatically', 'Downloads in the background and installs when you quit')}
      </div>
      <div class="foot"><button class="link" data-m="page">Release notes on GitHub</button><button class="link" data-m="welcome" style="margin-right:0">Show welcome screen</button></div>`,
      async (m, el) => {
        if (m === 'close' || m === 'backdrop') { await saveName(); closeModal(); }
        else if (m === 'toggle') {
          const k = el.dataset.key;
          settings = await api.set_setting(k, !settings[k]);
          el.querySelector('.switch').classList.toggle('on', !!settings[k]);
        } else if (m === 'savedir') { settings = await api.choose_save_dir(); el.querySelector('.t span').textContent = tilde(settings.save_dir); renderFooter(); }
        else if (m === 'forget') { peers = await api.forget(el.dataset.id); renderDevices(); el.parentElement.remove(); }
        else if (m === 'copy') copyText(relay && relay.pretty);
        else if (m === 'check') { upd = await api.check_updates(); renderUpdateRow(); renderPill(); }
        else if (m === 'see-update') openUpdate();
        else if (m === 'page') api.open_url(upd ? upd.page : '');
        else if (m === 'welcome') { closeModal(); showWelcome(); }
      }, 'settings');
    renderUpdateRow();
    $('#name-in').onkeydown = (e) => { if (e.key === 'Enter') { saveName(); e.target.blur(); } };
    $('#name-in').onblur = saveName;
    if (focusName) { $('#name-in').focus(); $('#name-in').select(); }
  }
  async function saveName() {
    const el = $('#name-in');
    if (el && el.value.trim() && el.value.trim() !== settings.device_name) { settings = await api.set_setting('device_name', el.value.trim()); renderFooter(); }
  }
  function renderFooter() {
    $('#my-name').textContent = settings.device_name || '';
    $('#save-path').textContent = tilde(settings.save_dir) || '';
  }

  function renderPill() {
    const p = $('#update-pill');
    if (!upd || !['available', 'downloading', 'ready', 'installing'].includes(upd.phase)) { p.classList.add('hidden'); return; }
    p.classList.remove('hidden');
    p.innerHTML = upd.phase === 'ready' ? `${icon('refresh')}Restart to update` :
      upd.phase === 'downloading' ? `${icon('download')}Updating ${Math.round(upd.progress * 100)}%` :
      upd.phase === 'installing' ? `${icon('refresh')}Installing…` : `${icon('download')}Update to ${esc(upd.latest)}`;
  }
  function openUpdate() {
    if (!upd) return;
    const ph = upd.phase;
    const pct = Math.round((upd.progress || 0) * 100);
    const sub = ph === 'ready' ? 'Downloaded and ready. Restart now, or it installs the next time you quit.' :
      ph === 'downloading' ? `Downloading… ${pct}%` : ph === 'installing' ? 'Installing and restarting…' :
      ph === 'failed' ? (upd.error || 'Something went wrong.') : ph === 'uptodate' ? "You're on the newest version." :
      `You have ${esc(upd.current)}.` + (upd.can_install ? '' : ' This copy runs from source, so the download page opens instead.');
    const primary = ph === 'ready' ? `<button class="btn-primary inline" data-m="install">${icon('refresh')}Restart to update</button>` :
      ph === 'available' || ph === 'failed' ? `<button class="btn-primary inline" data-m="${upd.can_install ? 'download' : 'page'}">${icon('download')}${upd.can_install ? 'Download update' : 'Open download page'}</button>` : '';
    showModal('update', `
      <div class="update-hero">
        <div class="logomark lg"><span class="mark" style="--size:30px;--knock:#1d1d1f">hop<i class="go"></i></span></div>
        <div><h2>${upd.latest ? 'Hop ' + esc(upd.latest) : 'Updates'}</h2><p class="${ph === 'failed' ? 'err' : ''}">${sub}</p></div>
      </div>
      ${ph === 'downloading' ? `<div class="progress"><div style="width:${pct}%"></div></div>` : ''}
      ${upd.notes ? `<div class="label">What's new</div><div class="notes">${esc(upd.notes)}</div>` : ''}
      <div class="foot"><button class="link" data-m="page">View on GitHub</button><button class="btn-quiet" data-m="close">Later</button>${primary}</div>`,
      (m) => {
        if (m === 'close' || m === 'backdrop') closeModal();
        else if (m === 'install') api.install_update();
        else if (m === 'download') api.download_update();
        else if (m === 'page') api.open_url(upd.page);
      });
  }

  // ---------- welcome (first launch) ----------
  function showWelcome() {
    const w = $('#welcome');
    let page = 0;
    const pages = [
      () => `<div class="logomark lg" style="width:96px;height:96px;border-radius:26px"><span class="mark" style="--size:38px;--knock:#1d1d1f">hop<i class="go"></i></span></div>
        <h1>Welcome to Hop</h1>
        <p class="lead">Send files between your Mac, your PC and your phone over Wi-Fi.<br>Like AirDrop, for everything you own.</p>
        <div class="facts">
          <div class="card fact">${icon('wifi')}<b>No cloud</b><span>Files go straight from one device to the other. Nothing is uploaded.</span></div>
          <div class="card fact">${icon('lock')}<b>Encrypted</b><span>Devices pair once with a code. Every chunk is encrypted and checked.</span></div>
          <div class="card fact">${icon('bolt')}<b>Any size</b><span>Huge files resume where they stopped if the Wi-Fi drops.</span></div>
        </div>`,
      () => `<h1>What should others see?</h1>
        <p class="lead">This name shows up on your other devices.</p>
        <div class="group"><div><input class="text-in" id="w-name" value="${esc(settings.device_name)}" maxlength="40" spellcheck="false"></div></div>`,
      () => `<h1>Stay up to date</h1>
        <p class="lead">New versions arrive from GitHub.</p>
        <div class="group">
          ${opt('auto_check', 'Check for updates automatically', 'Looks for a new version at launch and every few hours')}
          ${opt('auto_install', 'Download and install updates automatically', 'Downloads in the background and installs when you quit')}
        </div>`,
    ];
    const render = () => {
      w.innerHTML = `<div class="glow"></div><div class="dots">${pages.map((_, i) => `<i class="${i <= page ? 'on' : ''} ${i === page ? 'cur' : ''}"></i>`).join('')}</div>
        <div class="page">${pages[page]()}</div>
        <div class="foot">${page ? '<button class="btn-quiet" data-w="back">Back</button>' : ''}
          <button class="btn-primary" data-w="next">${page === pages.length - 1 ? 'Get started' : 'Continue'}</button></div>`;
    };
    w.onclick = async (e) => {
      const t = e.target.closest('[data-w],[data-m]');
      if (!t) return;
      const nm = $('#w-name');
      if (nm && nm.value.trim() && nm.value.trim() !== settings.device_name) { settings = await api.set_setting('device_name', nm.value.trim()); renderFooter(); }
      if (t.dataset.m === 'toggle') {
        const k = t.dataset.key;
        settings = await api.set_setting(k, !settings[k]);
        t.querySelector('.switch').classList.toggle('on', !!settings[k]);
      } else if (t.dataset.w === 'back') { page--; render(); }
      else if (t.dataset.w === 'next') {
        if (page < pages.length - 1) { page++; render(); }
        else { w.classList.add('hidden'); settings = await api.set_setting('welcomed', true); }
      }
    };
    render();
    w.classList.remove('hidden');
  }

  // ---------- events from Python ----------
  window.App = {
    on(event, p) {
      if (event === 'peers') { peers = p; renderDevices(); }
      else if (event === 'selection') { selection = p; renderSelection(); }
      else if (event === 'transfer') upsert(p);
      else if (event === 'retid') {
        const t = transfers.get(p.old);
        const el = document.getElementById('t-' + p.old);
        transfers.delete(p.old);
        if (t) transfers.set(p.tid, Object.assign(t, { tid: p.tid }));
        if (el) { el.id = 't-' + p.tid; el.dataset.tid = p.tid; }
      }
      else if (event === 'relay') { relay = p; if (modal === 'person') { const b = document.querySelector('.mycode'); if (b) b.outerHTML = codeBox(); } }
      else if (event === 'toast') toast(p.text, p.error);
      else if (event === 'pair_code') showCode(p);
      else if (event === 'pair_end') { if (modal === 'code:' + p.pid) closeModal(); }
      else if (event === 'paired') toast(`Paired with ${p.name}`);
      else if (event === 'pair_failed') toast(`${p.name} typed the wrong code`, true);
      else if (event === 'offer') { upsert(p); if (!modal) showOffer(p); }
      else if (event === 'received') {
        toast(`Received ${p.count} file${p.count === 1 ? '' : 's'} from ${p.peer_name}`, false, p.first ? { label: 'Show', run: () => api.reveal(p.first) } : null);
      }
      else if (event === 'update') {
        const prev = upd && upd.phase;
        upd = p;
        renderPill();
        if (modal === 'update') openUpdate();
        else if (modal === 'settings') renderUpdateRow();
        if (prev !== 'ready' && p.phase === 'ready' && modal !== 'update')
          toast(`Version ${p.latest} is ready`, false, { label: 'Restart', run: () => api.install_update() });
      }
    },
  };

  // ---------- user actions ----------
  async function doSend(id) {
    if (id === 'phone') {
      const r = await api.send('phone');
      if (!r.ok) return toast(r.error, true);
      return showPhone();
    }
    const r = await api.send(id);
    if (r.pair) return startPair(peers.find((p) => p.id === id));
    if (!r.ok) toast(r.error, true);
  }
  $('#send-btn').onclick = () => chosen && doSend(chosen);
  $('#add-files').onclick = () => api.pick_files();
  $('#dropzone').onclick = () => api.pick_files();
  $('#add-folder').onclick = () => api.pick_folder();
  $('#sel-clear').onclick = async () => { selection = await api.clear_selection(); renderSelection(); };
  $('#add-addr').onclick = addPerson;
  $('#open-phone').onclick = () => showPhone();
  $('#open-inbox').onclick = () => api.open_save_dir();
  $('#save-row').onclick = () => api.open_save_dir();
  $('#name-row').onclick = () => openSettings(true);
  $('#open-settings').onclick = () => openSettings();
  $('#update-pill').onclick = () => openUpdate();
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && modal && !modal.startsWith('offer')) { if (pairing) api.pair_cancel(pairing.id); pairing = null; closeModal(); } });

  // Drag overlay (Python receives the actual drop with full file paths).
  let depth = 0;
  const showDrop = (on) => { $('#drop').classList.toggle('hidden', !on); document.body.classList.toggle('dragging', on); };
  document.addEventListener('dragenter', (e) => { e.preventDefault(); depth++; showDrop(true); });
  document.addEventListener('dragover', (e) => e.preventDefault());
  document.addEventListener('dragleave', () => { if (--depth <= 0) { depth = 0; showDrop(false); } });
  document.addEventListener('drop', (e) => { e.preventDefault(); depth = 0; showDrop(false); });

  // ---------- start ----------
  async function start(a) {
    api = a;
    info = await api.info();
    document.body.classList.add(info.platform === 'darwin' ? 'mac' : info.platform.startsWith('win') ? 'win' : 'other');
    $('#version').textContent = 'v' + info.version;
    settings = info.settings || {};
    home = info.home || '';
    upd = info.update;
    peers = info.peers || [];
    relay = info.relay || null;
    selection = info.selection || { count: 0 };
    (info.transfers || []).forEach(upsert);
    renderFooter();
    renderDevices();
    renderSelection();
    renderPill();
    refreshStats();
    const scan = $('#scan');
    if (!info.bonjour) { scan.classList.add('off'); scan.lastChild.textContent = 'Bonjour off · add by address'; }
    if (info.error) toast(info.error, true);
    if (!settings.welcomed) setTimeout(showWelcome, 1500);
    setTimeout(() => { $('#splash').classList.add('out'); $('#app').classList.add('in'); }, 1500);
    setTimeout(() => $('#splash').remove(), 2100);
  }

  // Development bridge: the same UI in a normal browser, talking to `HOP_UI_DEV` over fetch.
  function devApi() {
    let since = 0;
    (async function poll() {
      for (;;) {
        try {
          const r = await (await fetch('/__events?since=' + since)).json();
          since = r.n;
          r.events.forEach(([ev, p]) => App.on(ev, p));
        } catch (e) { await new Promise((ok) => setTimeout(ok, 1000)); }
      }
    })();
    return new Proxy({}, { get: (_, name) => (...args) => fetch('/__api/' + name, { method: 'POST', body: JSON.stringify(args) }).then((r) => r.json()) });
  }

  if (window.pywebview && window.pywebview.api) start(window.pywebview.api);
  else if (location.protocol.startsWith('http')) start(devApi());
  else window.addEventListener('pywebviewready', () => start(window.pywebview.api));
})();
