"""Edit a copy of SpringBoard/IconState.plist to ensure a given
bundle id occupies the LEFTMOST dock slot. Used by the onboarding
script's step 5.4h to position the MosaicMesh webclip icon at a
deterministic VNC-tap-able location (framebuffer (945, 671) in
portrait orientation).

Behavior:
  - If the bundle id is already at buttonBar[0], no-op (idempotent).
  - Otherwise: remove from any iconLists page or buttonBar slot it
    currently occupies, then insert at buttonBar[0].
  - iPad dock holds up to 6 icons; if insertion would push past 6,
    the last (rightmost) icon spills to the first iconLists page so
    no app is destroyed.

iOS 5 IconState.plist is a binary plist. plistlib auto-detects on
read; we write back binary to match what SpringBoard wrote.

Usage: python tools/_dock_webapp_icon.py <plist_path> <bundle_id>
"""
import plistlib
import sys

MAX_DOCK_SLOTS = 6  # iPad dock capacity


def remove_from_page(page, bid):
    """Remove bid from a single iconLists page. Pages contain bundle
    id strings, plus possibly folder dicts (with their own 'iconLists').
    Returns the cleaned page (folder structure preserved)."""
    out = []
    for item in page:
        if isinstance(item, str):
            if item != bid:
                out.append(item)
        elif isinstance(item, dict) and 'iconLists' in item:
            # Folder: recurse into its pages
            item['iconLists'] = [remove_from_page(p, bid) for p in item['iconLists']]
            out.append(item)
        else:
            out.append(item)
    return out


def main():
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} <plist_path> <bundle_id>")
        return 1
    path, bid = sys.argv[1], sys.argv[2]
    with open(path, 'rb') as f:
        p = plistlib.load(f)
    dock = list(p.get('buttonBar', []))
    pages = list(p.get('iconLists', []))

    # Idempotent: already at dock[0]?
    if dock and dock[0] == bid:
        print(f"already at dock slot 0: {bid}")
        return 0

    # Remove from existing dock position (if elsewhere)
    dock = [x for x in dock if x != bid]
    # Remove from any iconLists page (including inside folders)
    pages = [remove_from_page(page, bid) for page in pages]

    # Insert at dock leftmost
    dock.insert(0, bid)

    # Cap dock at MAX_DOCK_SLOTS; spill any overflow into page 0
    if len(dock) > MAX_DOCK_SLOTS:
        overflow = dock[MAX_DOCK_SLOTS:]
        dock = dock[:MAX_DOCK_SLOTS]
        if pages:
            pages[0] = list(pages[0]) + overflow
        else:
            pages.append(overflow)
        print(f"dock overflow: spilled {overflow} to page 0")

    p['buttonBar'] = dock
    p['iconLists'] = pages
    with open(path, 'wb') as f:
        plistlib.dump(p, f, fmt=plistlib.FMT_BINARY)
    print(f"moved to dock slot 0: {bid}")
    print(f"  dock now: {dock}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
