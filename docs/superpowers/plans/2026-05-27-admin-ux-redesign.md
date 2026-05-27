# Admin Console UX Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganize `admin.html` into a navigable app shell (status bar + sidebar + routed sections) with a CSS-token design system (light/dark), reworked flows, and consistent components — preserving all existing functionality.

**Architecture:** The design tokens + component classes live in an **inline `<style>` block in `admin.html`'s head** (the server has no `/css/` static route and the spec forbids a server change — so, like `index.html`, the CSS is inlined; no build step). `admin.html` gets a status bar, left sidebar nav, and `<section data-route>` panels routed by `location.hash`. Existing widgets are MOVED into sections keeping their element IDs so all JS (`mosiacMeshCallback`, jsTree, `plRenderInspector`, `schRenderForm`, SockJS) is untouched. New chrome (router, theme toggle, status bar, overview, toasts) is additive. No server change; `index.html` untouched.

**Tech Stack:** Vanilla HTML/CSS/JS + jQuery 1.x + jsTree + SockJS (all already loaded). No build step.

---

## Conventions for every task

- **Branch:** stay on `feature/discovery-completion-legacy-compat` (NOT main).
- Commit trailer: `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>`.
- **This is client-only** — there is NO pytest for `admin.html`/`css`. Each task is: implement → self-check structure → commit. The **controller verifies via Playwright** (the implementer should NOT start a server or run Playwright). After the FINAL task, run `python pytest_runner.py --unit` once to prove the server suite is still green (it must be — no server file is touched).
- **Hard rule — preserve element IDs & handlers:** when moving existing markup, keep every `id` and inline `onclick`/event binding intact. The redesign re-homes and re-skins; it does not rewrite behavior. `index.html` and `server.py` are NOT touched.
- **Read `admin.html` first** each task — it is ~600 lines: head with CDN jsTree + jQuery/SockJS/GoTime/mosiacmesh includes + an inline `<style>` (ends ~line 509) + the existing inline `<script>` (`mosiacMeshCallback`, the playlist editor script, the schedule editor script); `<body>` (from ~511) currently has: `.flex-container` → `[#canvas + #calImageForm]` and `[#displays_q + #displays]`; then `#Media` (with `#file`/`#uploadfile`); then `#playlistEditor`; then `#scheduleEditor`; then `#log`; then `#chatform` (`#text` + a Submit Query button).

---

## Task 1: Inline design-system CSS — tokens + components

**Files:**
- Modify: `admin.html` (add the design system into the existing head `<style>` block)

Note: the CSS is **inlined** in `admin.html`'s `<style>` (no `/css/` server route exists; no server change allowed). Append the rules below to the existing `<style>` block (which ends ~line 509). KEEP the existing rules that moved widgets still use (`.upload-area`, `.thumbnail`, `.size`); the `.flex-container`/`.flex-child` rules become dead after Task 2 re-homes those wrappers and may be deleted then.

- [ ] **Step 1: Add the token system + component classes** to the head `<style>`:

```css
/* ===== MosaicMesh admin design system ===== */
:root {
  --accent:#4a90d9; --accent-hover:#3a78ba;
  --ok:#27ae60; --warn:#e6a23c; --danger:#e25555; --playing:#4a90d9;
  --s1:4px; --s2:8px; --s3:12px; --s4:16px; --s5:24px;
  --radius:6px; --font:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;
  --shadow:0 1px 3px rgba(0,0,0,.25);
}
/* dark (default) */
:root, [data-theme="dark"] {
  --bg:#13171d; --surface:#1a1f27; --surface-2:#222831; --border:#2b313c;
  --text:#e6eaf0; --text-muted:#8b94a2;
}
[data-theme="light"] {
  --bg:#f4f6f9; --surface:#ffffff; --surface-2:#eef1f5; --border:#d7dde5;
  --text:#1c2330; --text-muted:#5a6473; --shadow:0 1px 3px rgba(0,0,0,.10);
}
@media (prefers-color-scheme: light) {
  :root:not([data-theme]) {
    --bg:#f4f6f9; --surface:#ffffff; --surface-2:#eef1f5; --border:#d7dde5;
    --text:#1c2330; --text-muted:#5a6473;
  }
}
* { box-sizing:border-box; }
html,body { margin:0; height:100%; }
body { background:var(--bg); color:var(--text); font:14px/1.4 var(--font); }

/* ---- shell ---- */
.app { display:flex; flex-direction:column; height:100vh; }
.statusbar { display:flex; align-items:center; gap:var(--s3); padding:var(--s2) var(--s4);
  background:var(--surface); border-bottom:1px solid var(--border); }
.statusbar .brand { font-weight:600; }
.statusbar .spacer { margin-left:auto; }
.shell { display:flex; flex:1; min-height:0; }
.sidebar { flex:0 0 168px; background:var(--surface); border-right:1px solid var(--border);
  padding:var(--s2) 0; overflow:auto; }
.navitem { display:block; width:100%; text-align:left; background:none; border:0; color:var(--text-muted);
  padding:var(--s2) var(--s4); font:inherit; cursor:pointer; border-left:3px solid transparent; }
.navitem:hover { color:var(--text); }
.navitem.active { color:var(--text); background:var(--surface-2); border-left-color:var(--accent); }
.main { flex:1; overflow:auto; padding:var(--s4); min-width:0; }
.section { display:none; }
.section.active { display:block; }
.section h2 { margin:0 0 var(--s4); font-size:18px; }

/* ---- components ---- */
.panel { background:var(--surface); border:1px solid var(--border); border-radius:var(--radius);
  padding:var(--s3); box-shadow:var(--shadow); }
.panel-title { font-size:11px; text-transform:uppercase; letter-spacing:.05em; color:var(--text-muted);
  margin:0 0 var(--s2); }
.card { background:var(--surface); border:1px solid var(--border); border-radius:var(--radius);
  padding:var(--s3); box-shadow:var(--shadow); }
.toolbar { display:flex; align-items:center; gap:var(--s2); flex-wrap:wrap; }
.btn { font:inherit; border:1px solid var(--border); background:var(--surface-2); color:var(--text);
  border-radius:var(--radius); padding:6px 12px; cursor:pointer; }
.btn:hover { border-color:var(--accent); }
.btn:disabled { opacity:.5; cursor:not-allowed; }
.btn-primary { background:var(--accent); border-color:var(--accent); color:#fff; }
.btn-primary:hover { background:var(--accent-hover); }
.btn-danger { background:var(--danger); border-color:var(--danger); color:#fff; }
.btn-ghost { background:transparent; }
.field { display:block; margin-bottom:var(--s2); }
.field > label, .field-label { display:block; font-size:12px; color:var(--text-muted); margin-bottom:2px; }
input,select,textarea { font:inherit; background:var(--surface-2); color:var(--text);
  border:1px solid var(--border); border-radius:var(--radius); padding:6px 8px; }
input:focus,select:focus,textarea:focus,.btn:focus,.navitem:focus { outline:2px solid var(--accent); outline-offset:1px; }
.badge { display:inline-block; font-size:10px; padding:2px 7px; border-radius:10px;
  border:1px solid var(--border); color:var(--text-muted); }
.badge.online,.badge.ready,.badge.active,.badge.ok { background:color-mix(in srgb,var(--ok) 18%,transparent); border-color:var(--ok); color:var(--ok); }
.badge.playing { background:color-mix(in srgb,var(--playing) 18%,transparent); border-color:var(--playing); color:var(--playing); }
.badge.syncing,.badge.warn { background:color-mix(in srgb,var(--warn) 18%,transparent); border-color:var(--warn); color:var(--warn); }
.badge.offline { background:color-mix(in srgb,var(--danger) 14%,transparent); border-color:var(--danger); color:var(--danger); }
.dot { width:9px; height:9px; border-radius:50%; display:inline-block; background:var(--danger); }
.dot.on { background:var(--ok); }
.row { display:flex; align-items:center; gap:var(--s2); padding:var(--s2); border:1px solid var(--border);
  border-radius:var(--radius); background:var(--surface-2); margin-bottom:var(--s1); }
.empty { color:var(--text-muted); padding:var(--s4); text-align:center; border:1px dashed var(--border);
  border-radius:var(--radius); }
.steps { list-style:none; padding:0; margin:0; }
.steps li { display:flex; align-items:center; gap:var(--s2); padding:var(--s1) 0; }
.steps .num { flex:0 0 20px; width:20px; height:20px; border-radius:50%; background:var(--accent); color:#fff;
  display:flex; align-items:center; justify-content:center; font-size:11px; }
.steps .num.done { background:var(--ok); }
.grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(180px,1fr)); gap:var(--s3); }
.thumbs { display:grid; grid-template-columns:repeat(auto-fill,minmax(120px,1fr)); gap:var(--s2); }
.thumb { border:1px solid var(--border); border-radius:var(--radius); overflow:hidden; background:var(--surface-2);
  font-size:11px; }
.thumb img,.thumb video { width:100%; height:80px; object-fit:cover; display:block; background:#000; }
.thumb .cap { padding:4px 6px; color:var(--text-muted); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.toasts { position:fixed; right:var(--s4); bottom:var(--s4); display:flex; flex-direction:column; gap:var(--s2); z-index:50; }
.toast { background:var(--surface); border:1px solid var(--border); border-left:4px solid var(--accent);
  border-radius:var(--radius); padding:var(--s2) var(--s3); box-shadow:var(--shadow); max-width:320px; }
.toast.ok { border-left-color:var(--ok); }
.toast.warn { border-left-color:var(--warn); }
.toast.danger { border-left-color:var(--danger); }
.mono { font-family:ui-monospace,Menlo,Consolas,monospace; font-size:12px; }

/* ---- jsTree integration ---- */
.jstree-default .jstree-anchor { color:var(--text); }
.jstree-default .jstree-clicked { background:var(--surface-2); box-shadow:none; border-radius:var(--radius); }
.jstree-default .jstree-hovered { background:var(--surface-2); border-radius:var(--radius); }

/* ---- responsive ---- */
@media (max-width:760px) {
  .shell { flex-direction:column; }
  .sidebar { flex:none; display:flex; overflow-x:auto; border-right:0; border-bottom:1px solid var(--border); }
  .navitem { border-left:0; border-bottom:3px solid transparent; white-space:nowrap; }
  .navitem.active { border-left:0; border-bottom-color:var(--accent); }
}
```

- [ ] **Step 2: Commit**
```bash
git add admin.html
git commit -m "feat(admin-ux): inline design-system CSS (tokens, components, light/dark)"
```

**Controller verification (Playwright):** load `/admin.html`; confirm `getComputedStyle(document.body).backgroundColor` reflects the dark `--bg` token, and a probe element with `class="btn btn-primary"` is accent-colored. (No external CSS request — the styles are inline.)

---

## Task 2: App shell + router + theme toggle + re-home widgets

**Files:**
- Modify: `admin.html` (replace the `<body>` layout; add router + theme JS)

This is the structural core: wrap everything in the shell and MOVE each existing widget block into its section, preserving IDs.

- [ ] **Step 1: Replace the body opening** — change `<body>` ... the existing `.flex-container` ... down through `#chatform` into this shell. The pattern: keep every existing inner widget block VERBATIM (ids/handlers intact), just relocate it inside the matching `<section>`.

New `<body>` structure:
```html
<body>
<div class="app">
  <div class="statusbar">
    <span class="brand">● MosaicMesh</span>
    <span class="dot" id="connDot"></span>
    <span id="connText" class="size">connecting…</span>
    <span id="nowPlaying" class="size"></span>
    <span class="spacer"></span>
    <button class="btn btn-ghost" id="themeToggle" type="button">🌙 Theme</button>
  </div>
  <div class="shell">
    <nav class="sidebar" id="sidebar">
      <button class="navitem" data-nav="overview">Overview</button>
      <button class="navitem" data-nav="displays">Displays</button>
      <button class="navitem" data-nav="media">Media</button>
      <button class="navitem" data-nav="playlists">Playlists</button>
      <button class="navitem" data-nav="schedules">Schedules</button>
      <button class="navitem" data-nav="console">Console</button>
    </nav>
    <main class="main">
      <section class="section" data-route="overview"><h2>Overview</h2><div id="overviewCards" class="grid"></div></section>

      <section class="section" data-route="displays"><h2>Displays &amp; Calibration</h2>
        <div style="display:flex; gap:var(--s4); flex-wrap:wrap;">
          <div class="panel" style="flex:0 0 22em;">
            <div class="panel-title">Groups &amp; screens</div>
            <!-- MOVE HERE: the existing #displays_q input and #displays div (verbatim, keep ids) -->
          </div>
          <div class="panel" style="flex:1; min-width:24em;">
            <div class="panel-title">Calibration</div>
            <!-- MOVE HERE: the existing #canvas div and #calImageForm (verbatim, keep ids) -->
          </div>
        </div>
      </section>

      <section class="section" data-route="media"><h2>Media library</h2>
        <!-- MOVE HERE: the existing #Media block (with #file and #uploadfile), verbatim -->
      </section>

      <section class="section" data-route="playlists"><h2>Playlists</h2>
        <!-- MOVE HERE: the existing #playlistEditor block, verbatim -->
      </section>

      <section class="section" data-route="schedules"><h2>Schedules</h2>
        <!-- MOVE HERE: the existing #scheduleEditor block, verbatim -->
      </section>

      <section class="section" data-route="console"><h2>Console</h2>
        <!-- MOVE HERE: the existing #log block and #chatform (verbatim, keep #text + the Submit button) -->
      </section>
    </main>
  </div>
</div>
<div class="toasts" id="toasts"></div>
```
Remove the now-empty `.flex-container` / `<br>` wrappers left behind. Keep every moved block's inner markup and ids EXACTLY as they were.

- [ ] **Step 2: Add the shell script** — a new `<script>` before `</body>` (after the existing editor scripts):
```html
<script>
// --- section router ---
function adminRoute() {
  var r = (location.hash || "#overview").slice(1);
  var found = false;
  $('.section').each(function(){ var on = $(this).data('route') === r; $(this).toggleClass('active', on); if(on) found=true; });
  if (!found) { $('.section[data-route=overview]').addClass('active'); r = "overview"; }
  $('.navitem').each(function(){ $(this).toggleClass('active', $(this).data('nav') === r); });
}
$(function(){
  $('.navitem').on('click', function(){ location.hash = $(this).data('nav'); });
  $(window).on('hashchange', adminRoute);
  adminRoute();
});

// --- theme toggle ---
function adminApplyTheme(t) {
  document.documentElement.setAttribute('data-theme', t);
  $('#themeToggle').text(t === 'light' ? '☀ Light' : '🌙 Dark');
}
$(function(){
  var stored = null;
  try { stored = localStorage.getItem('adminTheme'); } catch(e) {}
  if (!stored) { stored = (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches) ? 'light' : 'dark'; }
  adminApplyTheme(stored);
  $('#themeToggle').on('click', function(){
    var t = document.documentElement.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
    adminApplyTheme(t);
    try { localStorage.setItem('adminTheme', t); } catch(e) {}
  });
});
</script>
```

- [ ] **Step 3: Self-check** — grep that each moved id appears exactly once (`#displays`, `#canvas`, `#calImageForm`, `#Media`, `#uploadfile`, `#file`, `#playlistEditor`, `#scheduleEditor`, `#log`, `#text`); braces balanced; the old `.flex-container` wrapper is gone.

- [ ] **Step 4: Commit**
```bash
git add admin.html
git commit -m "feat(admin-ux): app shell, sidebar router, theme toggle; re-home widgets into sections"
```

**Controller verification (Playwright):** start `python server.py -p 3000`; load `/admin.html`. Assert: clicking each sidebar item shows that one `.section.active` and sets `location.hash`; default is Overview; theme toggle flips `documentElement[data-theme]` and persists across reload (localStorage). **Regression:** the jsTree still renders groups (it loads via the existing DISPLAYS flow); switching to Playlists, `plAddItem('/m/x.jpg',false)` still appends a row and `plRenderInspector` works; switching to Schedules, `schNew()`+`schRenderForm()` builds the form. (Confirms re-homing kept ids/handlers intact.)

---

## Task 3: Status bar wiring + Overview cards

**Files:**
- Modify: `admin.html` (status-bar JS in `mosiacMeshCallback` + a poll; overview rendering)

- [ ] **Step 1: Connection dot** — in the existing socket setup, when the SockJS connection opens/closes, update `#connDot`/`#connText`. Find where `sock` is created / `mosiacMeshConnect` is called; on open set `$('#connDot').addClass('on'); $('#connText').text('connected');` and on close remove `.on` + text `disconnected`. (If there's no explicit open/close hook, set "connected" on the first received message in `mosiacMeshCallback`.)

- [ ] **Step 2: Online count + now-playing** — in `mosiacMeshCallback`, handle the heartbeat and displays data the console already receives:
```javascript
		else if(data_obj.REQUEST == "DISCOVERY_HEARTBEAT")
		{
			var p = data_obj.PAYLOAD || {};
			if (p.onlineClients != null) $('#connText').text('connected · ' + p.onlineClients + ' online');
		}
```
Add a periodic refresh that asks for displays + builds overview/now-playing. After the socket is up (reuse the existing `$(function(){...})` or add one):
```javascript
function adminRefreshStatus() {
  if (typeof sock !== 'undefined' && sock) { sock.send(generateMessage('SRV','DISPLAYS','null')); }
}
$(function(){ setInterval(adminRefreshStatus, 5000); setTimeout(adminRefreshStatus, 800); });
```

- [ ] **Step 3: Render overview + now-playing from DISPLAYS** — the console already handles the `DISPLAYS` reply for the jsTree. Add overview rendering that reads the same payload (a dict of displayID → display with `action`, `mediaElements`). In the EXISTING `DISPLAYS` branch of `mosiacMeshCallback` (where the tree is built), ALSO call `adminRenderOverview(data_obj.PAYLOAD)`:
```javascript
function adminRenderOverview(displays) {
  var $o = $('#overviewCards').empty(); var playing = [];
  $.each(displays || {}, function(id, d){
    if (!d) return;
    var act = d.action && (d.action.name || d.action);  // enum or string
    var isPlaying = (act === 'PLAY' || act === 2);
    var n = (d.mediaElements || []).length;
    if (isPlaying) playing.push(id);
    $('<div class="card">').html('<b>'+id+'</b><br><span class="size" style="color:var(--text-muted)">'
      + n + ' item' + (n===1?'':'s') + ' · ' + (isPlaying ? '▶ playing' : 'idle') + '</span>').appendTo($o);
  });
  if (!Object.keys(displays||{}).length) $o.html('<div class="empty">No display groups yet.</div>');
  $('#nowPlaying').text(playing.length ? ('▶ ' + playing.join(', ')) : '');
}
```
(Field shapes: `DISPLAYS` returns `settings.displays` jsonpickled; `action` may serialize as an enum object with `.name` or a value — the `act === 'PLAY' || act === 2` covers both. If neither matches in practice, the controller will report the actual shape.)

- [ ] **Step 4: Commit**
```bash
git add admin.html
git commit -m "feat(admin-ux): status bar (connection/online/now-playing) + overview cards"
```

**Controller verification (Playwright):** with the server running and a client connected (the Playwright page itself can be a client via `/`), confirm `#connText` shows connected + a count, the Overview section shows a card per group, and a group set to PLAY shows ▶ in its card and in `#nowPlaying`.

---

## Task 4: Section reworks — badges, calibration steps, media grid, console, editor restyle

**Files:**
- Modify: `admin.html`

Apply the design-system classes + the reworked flows. Each is additive/structural; preserve handlers.

- [ ] **Step 1: Calibration steps + per-group context** — in the Displays section's Calibration panel, wrap the existing `#calImageForm` controls in a `.steps` list and add a "Generate ArUco" action for the selected jsTree group. Use the existing tree-selection + `GENERATEARUCO` send (the tree already wires `sock.send(generateMessage('SRV','GENERATEARUCO',{'id': node.id}))` on a context action — reuse it from a button):
```html
<ol class="steps">
  <li><span class="num">1</span> <button class="btn" id="calGenerate" type="button">Generate &amp; show ArUco on selected group</button></li>
  <li><span class="num">2</span> Photograph the wall</li>
  <li><span class="num">3</span> <!-- existing #calImageForm controls here --></li>
</ol>
```
Wire `#calGenerate` to send GENERATEARUCO for the currently-selected display group (read the jsTree selection: `$('#displays').jstree('get_selected')` → the group id). If nothing selected, `toast('Select a group first','warn')`.

- [ ] **Step 2: Status badges in the tree** — the jsTree node text is built in the existing `DISPLAYS` handler. Append a `.badge` to client/group node labels based on their state (client `isOnline`/`synced`/`ready`; group has `boundingBox` → calibrated). Where the tree data is assembled, add the badge HTML to each node's `text` (jsTree allows HTML when its core config has `"html_titles": true` — add that to the jstree init `core` options if not present). Group label gets `<span class="badge ok">calibrated</span>` when `boundingBox` is set else `<span class="badge warn">not calibrated</span>`; client gets `ready`/`syncing`/`online`/`offline` per its flags.

- [ ] **Step 3: Media thumbnail grid** — in the Media section, ADD a `#mediaGrid` div (`class="thumbs"`) above the existing `#Media` dropzone, and a loader that calls `/api/media` and renders thumbnails:
```javascript
function adminLoadMediaGrid() {
  $.getJSON('/api/media', function(d){
    var $g = $('#mediaGrid').empty(); var all = (d.images||[]).concat(d.videos||[]);
    if (!all.length) { $g.html('<div class="empty">No media yet — drop files below to upload.</div>'); return; }
    $.each(d.images||[], function(_,u){ $('<div class="thumb">').html('<img src="'+u+'"><div class="cap">'+u.split('/').pop()+'</div>').appendTo($g); });
    $.each(d.videos||[], function(_,u){ $('<div class="thumb">').html('<video src="'+u+'" muted></video><div class="cap">'+u.split('/').pop()+'</div>').appendTo($g); });
  });
}
$(function(){ setTimeout(adminLoadMediaGrid, 900); });
```
Call `adminLoadMediaGrid()` again after a successful upload (find the existing upload success handler in the `#Media`/`#uploadfile` logic and append the call).

- [ ] **Step 4: Console drawer + restyle** — give `#log` the `.panel .mono` look (wrap or add classes) and style the raw-command `#chatform` row with `.toolbar` + `.btn`. Add `autoscroll`: where log lines are appended, also `el.scrollTop = el.scrollHeight`.

- [ ] **Step 5: Apply component classes to the editors** — add classes to the existing playlist/schedule controls WITHOUT changing their ids/handlers: the playlist `#plSelect`/buttons row → `.toolbar`, `#plNew`/`#plSave`→`.btn`, `#plSave`→add `.btn-primary`, `#plDelete`→`.btn-danger .btn`; the three pled panes → wrap in `.panel`; the transport buttons → `.btn` (Play→`.btn-primary`). Same for the schedule panel (`#schSave`→`.btn-primary`, `#schDelete`→`.btn-danger`). These are class additions on existing elements.

- [ ] **Step 6: Commit**
```bash
git add admin.html
git commit -m "feat(admin-ux): calibration steps, tree badges, media grid, console drawer, editor restyle"
```

**Controller verification (Playwright):** Displays section shows group/client badges + the 3-step calibration list with a Generate button; Media section shows a thumbnail grid (or empty state) above the dropzone; Console shows the styled log + command bar; the playlist/schedule buttons carry the new classes (primary/danger) and still function (add item, save).

---

## Task 5: Cross-cutting UX — toasts, empty states, confirm, polish

**Files:**
- Modify: `admin.html`

- [ ] **Step 1: Toast helper** — add a global `toast()` and use it:
```javascript
function toast(msg, kind) {
  var $t = $('<div class="toast">').addClass(kind||'').text(msg).appendTo('#toasts');
  setTimeout(function(){ $t.fadeOut(250, function(){ $(this).remove(); }); }, 3000);
}
```
Replace the existing blocking `alert(...)` calls in the playlist/schedule editor scripts with `toast(...)` (e.g. the "Name required" / "Save the playlist first" alerts → `toast(msg,'warn')`). Surface SAVE/ASSIGN/error feedback via `toast` too (e.g. on `SAVE_SCHEDULE` error, `toast(data_obj.PAYLOAD.error,'danger')`; on success `toast('Saved','ok')`).

- [ ] **Step 2: Empty states** — where lists render empty, show `.empty` with a CTA: the playlist items list (`#plItems`) when no items → `<div class="empty">No items — click media on the left to add.</div>`; the schedule select when none; the overview (already done in Task 3). (Add the empty-render branch in `plRenderItems` and the schedule list handler.)

- [ ] **Step 3: Confirm on destructive** — wrap `plDelete`/`schDelete` bodies in `if (!confirm('Delete this playlist?')) return;` / `'Delete this schedule?'` at the top.

- [ ] **Step 4: Disabled/loading polish** — when Render is in flight (the existing render badge shows "rendering…"), the Task-8 playlist code already gates Play/Render via `plSetTransportState`; ensure those buttons get `:disabled` (they use `.prop('disabled', …)` already — just confirm the `.btn` styling shows the disabled state). No behavior change.

- [ ] **Step 5: Commit**
```bash
git add admin.html
git commit -m "feat(admin-ux): toasts replace alerts, empty states, confirm-on-delete"
```

**Controller verification (Playwright):** a `toast` appears (not an `alert`) on a name-less save; `#plItems` shows the empty state with no items; `plDelete` triggers a confirm dialog; deleting after confirm removes it. Then run the FULL pytest suite once: `python pytest_runner.py --unit` → still green (proves no server file was touched).

---

## Final verification (after all tasks)

- [ ] Playwright: all six sections route + render in BOTH themes (screenshot each); status bar live; every preserved flow works (jsTree, playlist add/save/assign, schedule save, effect dropdowns); toasts/empty-states/confirm present.
- [ ] `python pytest_runner.py --unit` → green (no server change).
- [ ] Push branch and update PR #1.

## Notes for the implementer

- **Preserve every id and handler** when re-homing. The move is cut-and-paste of existing inner markup into the new section containers — do not rewrite the widgets.
- **No server change.** The CSS is inlined in `admin.html`'s `<style>` (there is no `/css/` static route, and we must not add one). Do NOT edit `server.py`.
- **`index.html` is NOT touched.**
- jsTree HTML-in-labels needs `core.html_titles = true` in its init for badges (Task 4 step 2) — add only that option, don't restructure the tree config.
- If the existing markup/handlers differ materially from the references here (e.g. the DISPLAYS payload shape, the upload success hook), STOP and report NEEDS_CONTEXT rather than guessing.
```
