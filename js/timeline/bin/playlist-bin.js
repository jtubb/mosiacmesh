export function mmPlaylistBinComponent() {
  return {
    get list() {
      return Object.values(this.$store.mm.playlists || {})
        .sort((a, b) => a.name.localeCompare(b.name));
    },
  };
}
