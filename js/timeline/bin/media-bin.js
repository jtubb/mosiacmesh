import { setDrag, clearDrag } from '../drag/dragstate.js';

export function mmMediaBinComponent() {
  return {
    get items() {
      const media = this.$store.mm.media || {};
      const images = (media.images || []).map(url => ({ kind: 'image', url, name: basename(url) }));
      const videos = (media.videos || []).map(url => ({ kind: 'video', url, name: basename(url),
        duration: media.videoDurations?.[url] }));
      return [...images, ...videos];
    },
    search: '',
    filtered() {
      const q = this.search.trim().toLowerCase();
      if (!q) return this.items;
      return this.items.filter(it => it.name.toLowerCase().includes(q));
    },
    dragStart(item, ev) {
      ev.dataTransfer.setData('application/x-mm-media', item.url);
      ev.dataTransfer.effectAllowed = 'copy';
      setDrag({ kind: 'media', file: item.url, duration: item.duration ?? null });
      document.body.classList.add('mm-dragging');
    },
    dragEnd() {
      clearDrag();
      document.body.classList.remove('mm-dragging');
    },
    /**
     * PR-16: delete a media file. Confirms first; on 409+refs surfaces
     * the blocking playlist names via toast so the operator knows
     * which playlists to clean up before retrying.
     */
    async remove(item, ev) {
      if (ev) { ev.stopPropagation(); ev.preventDefault(); }
      if (!window.confirm(`Delete "${item.name}"? This cannot be undone.`)) return;
      try {
        await this.$store.mm.deleteMedia(item.url);
        this.$store.mm.toast(`Deleted "${item.name}".`, 'info');
      } catch (e) {
        const refs = e?.body?.refs;
        if (Array.isArray(refs) && refs.length) {
          this.$store.mm.toast(
            `Can't delete "${item.name}" — used by ${refs.length} playlist${refs.length === 1 ? '' : 's'}: ${refs.join(', ')}.`,
            'error');
        }
        // For non-409 paths, withRollback already toasted.
      }
    },
  };
}

function basename(p) { return p.split('/').pop(); }
