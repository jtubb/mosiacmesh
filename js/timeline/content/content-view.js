/**
 * mmContent — the Content tab. Two sub-views: Library (the unified content
 * grid + upload + delete) and Playlists (list/create/delete). Reads
 * store.contentItems; opens the playlist editor.
 */
import { api } from '../api.js';
import { openPlaylistEditor } from '../modals/playlist-editor.js';
import { playlistGroupSummary } from '../util/render-helpers.js';

export function mmContentComponent() {
  return {
    subview: 'library',       // 'library' | 'playlists'
    filter: 'all',            // 'all' | 'image' | 'video' | 'animation'
    get items() {
      const all = this.$store.mm.contentItems;
      return this.filter === 'all' ? all : all.filter((i) => i.kind === this.filter);
    },
    get playlists() {
      return Object.values(this.$store.mm.playlists || {}).sort((a, b) => a.name.localeCompare(b.name));
    },
    iconFor(kind) { return kind === 'image' ? '▦' : kind === 'video' ? '▶' : '✦'; },

    async uploadFiles(ev) {
      const files = Array.from(ev.target.files || []);
      let ok = 0, fail = 0;
      for (const f of files) { try { await api.uploadMedia(f); ok += 1; } catch (_) { fail += 1; } }
      // api.listMedia() returns the media object directly ({images, videos,
      // videoDurations}) — matches the store's hydrate assignment.
      try { this.$store.mm.media = await api.listMedia(); } catch (_) {}
      this.$store.mm.toast(fail ? `${ok} uploaded, ${fail} failed` : `Uploaded ${ok} file${ok === 1 ? '' : 's'}`, fail ? 'error' : 'info');
      ev.target.value = '';
    },
    async removeItem(it) {
      if (it.kind === 'animation') return;
      if (!confirm(`Delete ${it.name}?`)) return;
      try { await this.$store.mm.deleteMedia(it.ref); }
      catch (_) { /* store.deleteMedia toasts 409 refs */ }
    },

    newPlaylist() {
      const name = (prompt('New playlist name:') || '').trim();
      if (!name) return;
      this.$store.mm.createPlaylist(name).catch(() => {});
    },
    async deletePlaylist(name) {
      if (!confirm(`Delete playlist "${name}"?`)) return;
      try { await this.$store.mm.deletePlaylist(name); } catch (_) {}
    },
    edit(name) { openPlaylistEditor(this.$store.mm, name); },

    playlistRenderSummary(name) {
      const pl = this.$store.mm.playlists[name];
      const renderable = !!(pl && (pl.items || []).some(
        (it) => it.playmode === 'SEGMENT' || it.playmode === 'INDIVIDUAL'));
      return playlistGroupSummary(name, this.$store.mm.displayGroups, this.$store.mm.renders, renderable);
    },
    retryRender(name) {
      const summary = this.playlistRenderSummary(name);
      if (typeof window.sock === 'undefined' || typeof window.generateMessage !== 'function') {
        this.$store.mm.toast('SockJS not available; reload the page.', 'error');
        return;
      }
      for (const displayID of summary.failed) {
        window.sock.send(window.generateMessage('SRV', 'RENDER', { displayID, name }));
      }
      this.$store.mm.toast(`Retrying render of "${name}" on ${summary.failed.length} group(s).`, 'info');
    },
  };
}
