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
  };
}

function basename(p) { return p.split('/').pop(); }
