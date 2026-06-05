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
  };
}

function basename(p) { return p.split('/').pop(); }
