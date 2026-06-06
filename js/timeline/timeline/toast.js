/**
 * Bottom-right toast stack. Reads from $store.mm.toasts (added in
 * Task 3). Error toasts stay until clicked; info auto-dismisses
 * after 4s via store.toast()'s internal setTimeout.
 *
 * Click anywhere on a toast to dismiss it manually.
 */
export function mmToastComponent() {
  return {
    get items() { return this.$store.mm.toasts; },
    dismiss(id) { this.$store.mm.dismissToast(id); },
  };
}
