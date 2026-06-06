/**
 * Wire the + Upload button on the media bin to a hidden file input;
 * uploads each chosen file via api.uploadMedia, then re-fetches
 * store.media so the new files appear in the bin.
 *
 * Files are uploaded sequentially rather than parallel: the server
 * routes the upload + probes video duration synchronously, and a burst
 * of parallel uploads can saturate ffprobe on weak hardware. One at a
 * time keeps the toast counters accurate too.
 */
import { api } from './api.js';

export function attachUpload(store) {
  const btn = document.getElementById('mmUploadBtn');
  const input = document.getElementById('mmUploadInput');
  if (!btn || !input) return;
  btn.addEventListener('click', () => input.click());
  input.addEventListener('change', async () => {
    const files = Array.from(input.files || []);
    if (files.length === 0) return;
    let ok = 0, fail = 0;
    for (const f of files) {
      try { await api.uploadMedia(f); ok += 1; }
      catch (e) { fail += 1; }
    }
    try { store.media = await api.listMedia(); } catch (_) { /* hydrate retry */ }
    if (fail === 0) store.toast(`Uploaded ${ok} file${ok === 1 ? '' : 's'}`, 'info');
    else store.toast(`${ok} uploaded, ${fail} failed`, 'error');
    input.value = '';
  });
}
