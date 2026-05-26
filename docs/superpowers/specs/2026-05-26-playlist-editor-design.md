# Playlist Editor — Design

**Date:** 2026-05-26
**Status:** Design approved, pending implementation plan
**Builds on:** the synchronized playback engine (images, video, PAUSE, image/video split, SCRIPT animations) and the existing per-group playlist lifecycle (`SETPLAYLIST` → `RENDER` → `PLAY`/`PAUSE`/`STOP`).

## Context

The playback lifecycle is fully implemented server-side and test-covered, but has **no UI** — playlists have only ever been set via tests/Playwright. This slice adds a **playlist authoring UI** in `admin.html`: a three-pane editor for building **named, reusable playlists**, saving them to a server-side library, and assigning one to a display group (which drives the existing `SETPLAYLIST`/render/transport machinery).

A "playlist" today is the live `mediaElements` list on a display **group**. This slice introduces named playlists as first-class, group-independent objects; assigning one *applies* it to a group's `mediaElements`.

`admin.html` is a desktop control console — **modern JS is allowed** here (unlike the ES5-constrained display client `index.html`).

## Goals

- Author named playlists independent of any group; save/load/delete them from a server-side library.
- Per playlist item: choose media (from a shared library, with upload), set `duration`, `playmode`, `backgroundColor`, and (deferred-impl) start/end effects; reorder and remove items.
- Assign a playlist to a display group; surface whether a render is required (your "notification when assigning if rendering isn't ready").
- Drive Render / Play / Pause / Stop for the assigned group from the editor, reflecting live render status.
- Fully provision the data model for `INDIVIDUAL` playmode and effects **now**, even though their behavior ships in later slices, so the schema never has to change.

## Non-goals (deferred to later slices)

- **`INDIVIDUAL` render path** (per-screen full-media perspective/rotation warp, OpenCV + ffmpeg) — its own next slice. This slice defines the enum value + field and shows it **disabled** in the picker.
- **Start/End effects** (wipes, fades, audio fades) — fields are modeled and round-tripped but inert.
- Assigning one playlist to multiple groups simultaneously (one group at a time for now).
- A media-management UI beyond list + upload (no rename/delete of library files here).

## Data model

### `PlayMode` enum

Gains `INDIVIDUAL`. Full set: `FULL` (identical, no warp), `INDIVIDUAL` (whole media warped to each screen's own perspective/rotation — **render deferred**), `SEGMENT` (mesh: media stretched across all displays, each screen warped to its region), `SCRIPT` (synced JS animation).

### `MediaElement` (server) and playlist item (wire/JSON)

```
id:               str
file:             str        # media URL, or animation name for SCRIPT (e.g. "bouncingBalls")
duration:         number     # seconds
playmode:         FULL | INDIVIDUAL | SEGMENT | SCRIPT
backgroundColor:  str  = "#000000"   # hex; letterbox/pad fill
startEffect:      str | null = null  # effect name — field only, deferred
endEffect:        str | null = null  # effect name — field only, deferred
```

`backgroundColor` is **live this slice**: in `FULL` mode it sets the display container's background so an image that does not fill the screen letterboxes against that color. In `INDIVIDUAL` mode (next slice) it fills the perspective-warp padding. `startEffect`/`endEffect` are stored and round-tripped but otherwise inert until the effects slice.

When older payloads omit the new fields, the server applies defaults (`backgroundColor="#000000"`, effects `null`) — backward compatible with existing `SETPLAYLIST` callers and tests.

### `Playlist` (server) + persistence

```python
class Playlist:
    name: str
    items: list   # list of item dicts as above
    loop: bool
```

Stored in a new `settings.playlists` dict, keyed by `name`. Persisted automatically by `save_settings_incremental()` (jsonpickle encodes the whole `settings` object). `settings.scripts` is **not** reused — it holds shell `Scripts` (`os.system`), unrelated. A load guard backfills `settings.playlists = {}` for an older `settings.dat` that predates the field (alongside `migrate_client_objects`).

## Server API

### New websocket requests (added to the `msg_response` switch)

| Request | Payload | Returns (`PAYLOAD`) |
|---|---|---|
| `LIST_PLAYLISTS` | — | `[{name, itemCount, hasSegment}]` |
| `GET_PLAYLIST` | `{name}` | `{name, items, loop}` or `{error: "not found"}` |
| `SAVE_PLAYLIST` | `{name, items, loop}` | `"SUCCESS"` (upsert by name) |
| `DELETE_PLAYLIST` | `{name}` | `"SUCCESS"` |
| `ASSIGN_PLAYLIST` | `{name, displayID}` | `{status, displayID}` where `status ∈ {ok, RENDER_REQUIRED, NOT_CALIBRATED, error}` |

`ASSIGN_PLAYLIST` looks up the named playlist, copies its items into the target group exactly as `SETPLAYLIST` does (build `MediaElement`s, reset `renderedToken=""`, broadcast `PRELOAD`), then computes the render state for the response:
- `NOT_CALIBRATED` — playlist has a `SEGMENT` (or, later, `INDIVIDUAL`) item but the group has no `boundingBox`.
- `RENDER_REQUIRED` — has a renderable item and `compute_render_token(displayID) != display.renderedToken`.
- `ok` — otherwise (plays immediately).
- `error` — unknown playlist name or unknown display.

The editor also listens for the existing `RENDER_STATUS` broadcasts (`""`/`rendering`/`ready`/`error`) for live progress after pressing Render.

`SETPLAYLIST`, `PLAY`, and the `PRELOAD` payload are extended to **carry the three new per-item fields** (`backgroundColor`, `startEffect`, `endEffect`) so the display client receives them. `RENDER`/`PLAY`/`PAUSE`/`STOP` request handling is otherwise unchanged.

### New REST endpoint

- `GET /api/media` → `{"images": [urls...], "videos": [urls...]}` by scanning `media/server/images` and `media/server/videos` (created on demand; empty lists if absent). URLs are `/media/server/images/<name>` etc.
- `POST /upload/{dest}` gains a `dest="video"` branch that moves the uploaded file into `media/server/videos` (mirrors the existing `image` branch that targets `media/server/images`).

SCRIPT animation names are **not** server-known (they live in `index.html`); the editor hardcodes the list (`["bouncingBalls"]`) and shows them in the library alongside media.

## Client — editor UI (`admin.html`, modern JS)

A new **Playlists** section/tab. Three-pane layout with a header and a bottom transport bar:

- **Header:** playlist selector (`LIST_PLAYLISTS`), `＋New`, `Save` (`SAVE_PLAYLIST`), `Delete` (`DELETE_PLAYLIST`), and a `Loop` checkbox (part of the saved playlist).
- **Left — Media Library:** results of `GET /api/media` (images, videos) plus the hardcoded SCRIPT entries; an `Upload` control (`POST /upload/image|video`, then refresh the list). Clicking an entry appends a new item row to the playlist with sensible defaults (`duration=5`, `playmode=FULL` for media / `SCRIPT` for an animation, `backgroundColor="#000000"`).
- **Center — Playlist:** the ordered item rows. Drag to reorder, `✕` to remove, click to select. Each row shows file · duration · playmode at a glance.
- **Right — Inspector** (edits the selected item): `Duration` (number), `Play mode` (dropdown: FULL / SEGMENT / SCRIPT enabled; **INDIVIDUAL present but disabled**, labeled "render coming soon"), `Background color` (color input), `Start effect` / `End effect` (dropdowns **disabled**, "None (coming soon)"). Edits write straight back to the in-memory item.
- **Bottom — Assign + Transport:** `Assign → [group]` (`ASSIGN_PLAYLIST`; show the returned status as a badge), a `Render` button + status badge driven by the `ASSIGN_PLAYLIST` result and live `RENDER_STATUS` broadcasts (`needs render` → `rendering…` → `ready`/`error`), and `Play` / `Pause` / `Stop` (existing requests against the assigned group).

State is held in a plain in-memory JS object (`currentPlaylist = {name, items, loop}`, `selectedIndex`, `assignedGroup`); Save serializes it to `SAVE_PLAYLIST`. The display client (`index.html`) is unchanged except that it now receives the three new per-item fields and applies `backgroundColor` as the display container's background (letterbox fill) — a small ES5 addition.

## Display client change (`index.html`, ES5)

`showItem` applies `item.backgroundColor` (default `#000000`) to the `#canvas` container's background so a `FULL` image that doesn't fill the viewport letterboxes against the chosen color. `startEffect`/`endEffect` are ignored this slice. ES5 only (`var`/`function`/string concat) per the 1st-gen iPad constraint.

## Error handling

- `SAVE_PLAYLIST` with an empty name → server returns `{error: "name required"}`; editor blocks save and prompts.
- `ASSIGN_PLAYLIST` for an unknown playlist/display → `{status: "error"}`; editor surfaces it.
- Assigning a playlist with a `SEGMENT` item to an uncalibrated group → `NOT_CALIBRATED`; the editor disables `Render`/`Play` and explains calibration is needed.
- `Render` pressed when not assigned, or with no SEGMENT items, or uncalibrated → existing `RENDER` error statuses surfaced as a badge (the server already returns `{status:"ERROR", error:...}`).
- `/api/media` when the folders don't exist → empty lists, no error.

## Testing

### pytest (server)

- `Playlist` + `settings.playlists` round-trip through jsonpickle; load guard tolerates a `settings.dat` with no `playlists` attribute.
- `SAVE_PLAYLIST` upserts (create then overwrite by name); `LIST_PLAYLISTS` returns `{name, itemCount, hasSegment}`; `GET_PLAYLIST` returns items including the three new fields; `DELETE_PLAYLIST` removes; `GET_PLAYLIST`/unknown → `{error}`.
- `SETPLAYLIST` stores `backgroundColor`/`startEffect`/`endEffect` on each `MediaElement`, applying defaults when omitted; `PLAY` and `PRELOAD` payloads include the three fields.
- `ASSIGN_PLAYLIST`: returns `ok` (no renderable items), `RENDER_REQUIRED` (SEGMENT + stale token), `NOT_CALIBRATED` (SEGMENT + no boundingBox), `error` (unknown name); and actually populates the target group's `mediaElements`.
- `/api/media` lists files under `media/server/{images,videos}`; `POST /upload/video` lands a file in `media/server/videos`.

### Playwright (client — light; logic is mostly server-side)

- Clicking library entries appends item rows; selecting a row populates the Inspector; editing duration/playmode/backgroundColor mutates the in-memory item.
- INDIVIDUAL option and both effect dropdowns are present but `disabled`.
- Save → reload the section → the same playlist loads (`LIST_PLAYLISTS`/`GET_PLAYLIST`).
- Assign reflects the returned status badge; a `FULL` item's `backgroundColor` appears as the display container background on the display client.

## ES5 / legacy

Only the display-client touch (`backgroundColor` on the container in `showItem`) must be ES5 — it is trivial `var`/string work. The entire editor lives in `admin.html`, a desktop console exempt from the iPad constraint.
