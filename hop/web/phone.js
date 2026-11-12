// Hop in a phone's browser. The key comes from the QR code (the part after #), which
// browsers never send over the network. Every chunk is sealed with NaCl secretbox, the same
// format the desktop app uses, so only devices that scanned the code can read the files.
(function () {
  'use strict';
  const CHUNK = 1 << 20;
  const PARALLEL = 3;
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const human = (n) => { const u = ['B', 'KB', 'MB', 'GB']; let i = 0; while (n >= 1024 && i < 3) { n /= 1024; i++; } return i ? n.toFixed(n < 10 ? 1 : 0) + ' ' + u[i] : n + ' B'; };
  const hex = (b) => Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('');
  const enc = new TextEncoder(), dec = new TextDecoder();
  const CHECK = '<svg viewBox="0 0 24 24"><path d="M20 6 9 17l-5-5"/></svg>';

  function b64d(s) {
    s = s.replace(/-/g, '+').replace(/_/g, '/');
    while (s.length % 4) s += '=';
    const bin = atob(s);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }

  const frag = (location.hash || '').slice(1).split('.');
  if (frag.length !== 2) { $('#nokey').classList.remove('hidden'); $('header .conn').classList.add('hidden'); return; }
  const sid = frag[0];
  let key;
  try { key = b64d(frag[1]); } catch (e) { key = null; }
  if (!key || key.length !== 32) { $('#nokey').classList.remove('hidden'); return; }
  const me = 'web:' + sid + ':' + hex(nacl.randomBytes(4));
  const ua = navigator.userAgent;
  const myName = /iPhone/.test(ua) ? 'iPhone' : /iPad/.test(ua) ? 'iPad' : /Android/.test(ua) ? 'Android phone' : 'Phone';

  // nonce(24) || secretbox( u16 len(ad) || ad || data )
  function seal(ad, data) {
    const a = enc.encode(ad);
    const plain = new Uint8Array(2 + a.length + data.length);
    plain[0] = a.length >> 8; plain[1] = a.length & 255;
    plain.set(a, 2); plain.set(data, 2 + a.length);
    const nonce = nacl.randomBytes(24);
    const box = nacl.secretbox(plain, nonce, key);
    const out = new Uint8Array(24 + box.length);
    out.set(nonce); out.set(box, 24);
    return out;
  }
  function open(ad, blob) {
    const plain = nacl.secretbox.open(blob.subarray(24), blob.subarray(0, 24), key);
    if (!plain) throw new Error('This link is out of date. Scan the code again.');
    const n = (plain[0] << 8) | plain[1];
    if (dec.decode(plain.subarray(2, 2 + n)) !== ad) throw new Error('Mixed-up data. Try again.');
    return plain.subarray(2 + n);
  }
  async function call(path, obj) {
    const r = await fetch(path, { method: 'POST', headers: { 'X-Hop-From': me }, body: seal('req:' + path, enc.encode(JSON.stringify(obj))) });
    if (r.status === 401) throw new Error('This link is out of date. Scan the code again.');
    if (!r.ok) throw new Error('Your computer said no (' + r.status + ')');
    return JSON.parse(dec.decode(open('res:' + path, new Uint8Array(await r.arrayBuffer()))));
  }

  function conn(state, text) {
    const c = $('#conn');
    c.className = 'conn ' + state;
    c.querySelector('span').textContent = text;
  }

  // ---------- phone -> computer ----------
  let pcName = 'computer';
  $('#picker').addEventListener('change', (e) => {
    const files = Array.from(e.target.files || []);
    e.target.value = '';
    if (files.length) upload(files);
  });

  async function upload(files) {
    const tid = hex(nacl.randomBytes(16));
    const total = files.reduce((a, f) => a + f.size, 0);
    const el = document.createElement('div');
    el.className = 'item';
    const title = files[0].name + (files.length > 1 ? ` and ${files.length - 1} more` : '');
    el.innerHTML = `<div class="t"><b>${esc(title)}</b><div class="s">Starting…</div><div class="bar"><div></div></div></div>`;
    $('#sent').prepend(el);
    $('#sent-sec').classList.remove('hidden');
    const sub = el.querySelector('.s'), bar = el.querySelector('.bar > div');
    try {
      const res = await call('/hop/offer', { tid, from_name: myName, files: files.map((f) => ({ name: f.name, size: f.size, sha256: null })) });
      if (res.state !== 'accepted') throw new Error('Your computer declined');
      const jobs = [];
      files.forEach((f, fi) => { for (let ci = 0; ci < Math.ceil(f.size / CHUNK); ci++) jobs.push([fi, ci]); });
      let done = 0, t0 = Date.now();
      const worker = async () => {
        while (jobs.length) {
          const [fi, ci] = jobs.shift();
          const f = files[fi];
          const data = new Uint8Array(await f.slice(ci * CHUNK, Math.min(f.size, (ci + 1) * CHUNK)).arrayBuffer());
          const body = seal(`chunk/${tid}/${fi}/${ci}`, data);
          for (let tries = 0; ; tries++) {
            try {
              const r = await fetch(`/hop/chunk/${tid}/${fi}/${ci}`, { method: 'PUT', headers: { 'X-Hop-From': me }, body });
              if (!r.ok) throw new Error('Your computer refused a piece (' + r.status + ')');
              break;
            } catch (err) {
              if (tries >= 4) throw err;
              await new Promise((ok) => setTimeout(ok, 600 * (tries + 1)));
            }
          }
          done += data.length;
          const speed = done / Math.max(0.2, (Date.now() - t0) / 1000);
          bar.style.width = (100 * done / total) + '%';
          sub.textContent = `${human(done)} of ${human(total)} · ${human(speed)}/s`;
        }
      };
      await Promise.all(Array.from({ length: PARALLEL }, worker));
      bar.parentElement.remove();
      sub.textContent = `${files.length} file${files.length === 1 ? '' : 's'} · ${human(total)}`;
      el.insertAdjacentHTML('beforeend', `<span class="ok-badge">${CHECK}Sent</span>`);
    } catch (err) {
      sub.innerHTML = `<span class="err">${esc(err.message)}</span>`;
    }
  }

  // ---------- computer -> phone ----------
  const seen = new Map();
  async function poll() {
    try {
      const res = await call('/hop/inbox', {});
      pcName = res.name || pcName;
      conn('ok', pcName);
      $('#pc-name').textContent = pcName;
      $('#send-title').textContent = 'Send to ' + pcName;
      $('#main').classList.remove('hidden');
      res.items.forEach((it) => it.files.forEach((f, fi) => {
        const k = it.tid + '/' + fi;
        if (seen.has(k)) return;
        const el = document.createElement('div');
        el.className = 'item';
        el.innerHTML = `<div class="t"><b>${esc(f.name)}</b><div class="s">${human(f.size)}</div></div><button class="btn">Save</button>`;
        el.querySelector('.btn').onclick = () => download(it.tid, fi, f, el);
        seen.set(k, el);
        $('#inbox').prepend(el);
        $('#inbox-sec').classList.remove('hidden');
      }));
    } catch (err) {
      conn('bad', /out of date/.test(err.message) ? 'Scan the code again' : 'Not connected');
    }
    setTimeout(poll, 2000);
  }

  async function download(tid, fi, f, el) {
    const btn = el.querySelector('.btn');
    btn.disabled = true;
    const t = el.querySelector('.t');
    t.insertAdjacentHTML('beforeend', '<div class="bar"><div></div></div>');
    const bar = t.querySelector('.bar > div'), sub = t.querySelector('.s');
    try {
      const n = Math.max(1, Math.ceil(f.size / CHUNK));
      const parts = new Array(n);
      let next = 0, got = 0;
      const worker = async () => {
        while (next < n) {
          const ci = next++;
          const r = await fetch(`/hop/pull/${tid}/${fi}/${ci}`, { headers: { 'X-Hop-From': me } });
          if (!r.ok) throw new Error('Download failed (' + r.status + ')');
          parts[ci] = open(`pull/${tid}/${fi}/${ci}`, new Uint8Array(await r.arrayBuffer())).slice();
          got += parts[ci].length;
          bar.style.width = (100 * got / Math.max(1, f.size)) + '%';
          sub.textContent = `${human(got)} of ${human(f.size)}`;
        }
      };
      await Promise.all(Array.from({ length: PARALLEL }, worker));
      const blob = new Blob(parts, { type: guessType(f.name) });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = f.name;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
      bar.parentElement.remove();
      sub.textContent = human(f.size);
      btn.textContent = 'Save again';
      btn.className = 'btn quiet';
      btn.disabled = false;
    } catch (err) {
      sub.innerHTML = `<span class="err">${esc(err.message)}</span>`;
      btn.disabled = false;
    }
  }

  function guessType(name) {
    const ext = (name.split('.').pop() || '').toLowerCase();
    return { jpg: 'image/jpeg', jpeg: 'image/jpeg', png: 'image/png', gif: 'image/gif', webp: 'image/webp', heic: 'image/heic',
      mp4: 'video/mp4', mov: 'video/quicktime', m4v: 'video/mp4', mp3: 'audio/mpeg', m4a: 'audio/mp4', wav: 'audio/wav', pdf: 'application/pdf',
      txt: 'text/plain', zip: 'application/zip' }[ext] || 'application/octet-stream';
  }

  conn('', 'Connecting…');
  poll();
})();
