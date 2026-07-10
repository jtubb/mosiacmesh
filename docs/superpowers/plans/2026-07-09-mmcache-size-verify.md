# mmcache download size-verification — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the mmcache client-pull verify a download is byte-complete before writing/acking, so a truncated pull becomes a clean `CACHE_FAILED` (central fallback) instead of a poisoned local copy that decode-errors (`verr=3`) on iPad-1.

**Architecture:** Rewrite `mm_download()` in `tweak/mmcache/Tweak.x` to fetch via `NSURLConnection sendSynchronousRequest:returningResponse:error:` (which exposes the response's `Content-Length`) instead of the completeness-blind `dataWithContentsOfURL:`, verify `statusCode==200` + `data.length==expectedContentLength` BEFORE writing, then rebuild the dylib via the WSL theos toolchain. The build (compile + `nm` load-safety checks) is the test — native code has no unit harness.

**Tech Stack:** iOS-5.1 MobileSubstrate tweak, theos (`iphone:clang:9.3:5.1`, `armv7`), Foundation via `objc_msgSend` (no ARC, no Blocks, no ObjC classes), built in WSL Ubuntu.

## Global Constraints

- **DO NOT stage or commit `index.html`.** It has a temporary uncommitted `MMFORCE_TDBG_TEMP` edit. Every `git add` MUST name exact files — never `git add -A`/`.`.
- Tweak style: plain `.x`, **NO ObjC classes**, **NO Blocks**, **NO C++/float** — runtime messaging via `objc_msgSend` only, Foundation only. A static ObjC class or C++ unwind symbol SIGKILLs the tweak at load on iOS-5.
- **Strict verification:** cache a file ONLY if `statusCode == 200` AND `expectedContentLength > 0` AND `data.length == expectedContentLength`. Any other outcome → `dispatch_fail` (CACHE_FAILED), write nothing.
- Verify BEFORE `writeToFile:` — never write a partial file (no cleanup path needed).
- Do NOT change the `dlctx` struct, the `mmcache://` parsing, `dispatch_done`/`dispatch_fail`, the `evict` path, or the `didClearWindowObject`/`__mmCacheReady` logic.
- `NSURLConnection`/`NSURLRequest`/`NSHTTPURLResponse` are Foundation — the Makefile's `mmcache_FRAMEWORKS = Foundation` already covers them; no Makefile change.
- Build only. The paced fleet redeploy (scp + respring) is a SEPARATE operation, out of scope.

---

### Task 1: verify download completeness in `mm_download` + rebuild dylib

**Files:**
- Modify: `tweak/mmcache/Tweak.x` — replace the body of `mm_download()` (currently ~lines 97-119).
- Modify: `tweak/mmcache/mmcache.dylib` — the rebuilt binary (tracked in the repo).

**Interfaces:**
- Consumes: existing helpers `nsstr(const char*)`, `dispatch_done(token, bytes)`, `dispatch_fail(token, reason)`, `mmclog(...)`, `MM_CACHE_DIR`, and the `dlctx { char token[128]; char url[600]; }` ctx.
- Produces: no new symbols; same `mm_download(void*)` dispatch target.

- [ ] **Step 1: Replace `mm_download`** — in `tweak/mmcache/Tweak.x`, replace the ENTIRE current function (from `static void mm_download(void *p) {` through its closing `}`) with:

```c
static void mm_download(void *p) {
    dlctx *c = (dlctx *)p;
    id url = ((id (*)(id, SEL, id))objc_msgSend)(
        (id)objc_getClass("NSURL"), sel_registerName("URLWithString:"), nsstr(c->url));
    if (!url) { mmclog("[mmcache] bad url token=%s\n", c->token);
                dispatch_fail(c->token, "url"); free(c); return; }
    /* iOS-5 has NO NSURLSession. dataWithContentsOfURL: hides the response, so a
       truncated transfer returns partial data (non-nil, no error) that we used to cache
       + ack CACHED -> verr=3 on playback. sendSynchronousRequest gives us the response
       (Content-Length) so we can verify completeness BEFORE writing. */
    id req = ((id (*)(id, SEL, id))objc_msgSend)(
        (id)objc_getClass("NSURLRequest"), sel_registerName("requestWithURL:"), url);
    id resp = (id)0, err = (id)0;
    id data = req ? ((id (*)(id, SEL, id, id *, id *))objc_msgSend)(
        (id)objc_getClass("NSURLConnection"),
        sel_registerName("sendSynchronousRequest:returningResponse:error:"),
        req, &resp, &err) : (id)0;
    if (!data) { mmclog("[mmcache] download FAILED token=%s\n", c->token);
                 dispatch_fail(c->token, "net"); free(c); return; }
    /* HTTP status must be 200 (reject 206 partial / 4xx / 5xx). */
    int status = 0;
    if (resp && ((int (*)(id, SEL, id))objc_msgSend)(
            resp, sel_registerName("isKindOfClass:"), (id)objc_getClass("NSHTTPURLResponse")))
        status = ((int (*)(id, SEL))objc_msgSend)(resp, sel_registerName("statusCode"));
    if (status != 200) { mmclog("[mmcache] bad status=%d token=%s\n", status, c->token);
                         dispatch_fail(c->token, "http"); free(c); return; }
    /* Completeness: downloaded length must equal the response's Content-Length. */
    long long expect = ((long long (*)(id, SEL))objc_msgSend)(
        resp, sel_registerName("expectedContentLength"));
    unsigned long got = ((unsigned long (*)(id, SEL))objc_msgSend)(
        data, sel_registerName("length"));
    if (expect <= 0 || (long long)got != expect) {
        mmclog("[mmcache] TRUNCATED token=%s got=%lu expect=%lld\n", c->token, got, expect);
        dispatch_fail(c->token, "len"); free(c); return;
    }
    /* Verified complete -> write + ack CACHED. */
    id fm = ((id (*)(id, SEL))objc_msgSend)(
        (id)objc_getClass("NSFileManager"), sel_registerName("defaultManager"));
    ((int (*)(id, SEL, id, int, id, id))objc_msgSend)(
        fm, sel_registerName("createDirectoryAtPath:withIntermediateDirectories:attributes:error:"),
        nsstr(MM_CACHE_DIR), 1, (id)0, (id)0);
    char pathc[320];
    snprintf(pathc, sizeof pathc, "%s/%s.mp4", MM_CACHE_DIR, c->token);
    int ok = ((int (*)(id, SEL, id, int))objc_msgSend)(
        data, sel_registerName("writeToFile:atomically:"), nsstr(pathc), 1);
    mmclog("[mmcache] %s token=%s bytes=%lu -> %s\n", ok ? "OK" : "WRITEFAIL", c->token, got, pathc);
    if (ok) dispatch_done(c->token, (long)got); else dispatch_fail(c->token, "write");
    free(c);
}
```

- [ ] **Step 2: Build the dylib via the WSL theos toolchain**

Run (from the repo root, Git Bash):
```bash
wsl.exe -d Ubuntu -e bash -lc 'cd "/mnt/c/Users/jtubb.SOLUTIONS/Documents/mosiacmesh/tweak/mmcache" && bash build.sh' 2>&1 | tail -40
```
Expected: `make` compiles `Tweak.x` with no errors; the tail shows the build.sh `nm` report and a final `DYLIB=... (NNNNN bytes)` line.

- [ ] **Step 3: Verify the load-safety invariants (from the build.sh output)**

The build.sh output MUST show:
- `== C++ unwind symbols? (want NONE) ==` → `CLEAN (plain ObjC)`
- `== ObjC classes defined? (want NONE ...) ==` → `NONE`

If either fails, the change introduced a static class or C++/unwind — STOP and report (do not ship a dylib that SIGKILLs at load).

- [ ] **Step 4: Copy the freshly built dylib into the repo**

build.sh builds under `~/mmcache` in WSL and prints the built path as `DYLIB=<path>`. Copy that dylib over the tracked repo binary:
```bash
wsl.exe -d Ubuntu -e bash -lc 'DY=$(find ~/mmcache -name "*.dylib" -not -path "*dSYM*" | head -1); cp "$DY" "/mnt/c/Users/jtubb.SOLUTIONS/Documents/mosiacmesh/tweak/mmcache/mmcache.dylib" && echo "copied $DY -> repo ($(stat -c%s "$DY") bytes)"'
```
Expected: `copied ... -> repo (NNNNN bytes)`.

- [ ] **Step 5: Confirm the repo dylib changed + is a valid Mach-O**

```bash
git status --porcelain tweak/mmcache/mmcache.dylib   # expect ' M'
file tweak/mmcache/mmcache.dylib 2>/dev/null || head -c4 tweak/mmcache/mmcache.dylib | xxd
```
Expected: the dylib shows as modified; it is a Mach-O dylib (magic `cafebabe`/`feedface` — a fat or arm Mach-O).

- [ ] **Step 6: Commit (exact files — NOT index.html)**

```bash
git add tweak/mmcache/Tweak.x tweak/mmcache/mmcache.dylib
git commit -m "fix(mmcache): verify download Content-Length before caching (reject truncated pulls)"
```
Then confirm: `git status --porcelain index.html` still shows ` M`.

---

## Final verification (after the task)

- [ ] `git show --stat HEAD` shows only `tweak/mmcache/Tweak.x` + `tweak/mmcache/mmcache.dylib`.
- [ ] The build.sh `nm` checks reported CLEAN + NONE.
- [ ] `index.html` still uncommitted (` M`).

## Self-Review (plan author)

- **Spec coverage:** the `sendSynchronousRequest` rewrite + strict guards (status 200, known+matching Content-Length, else fail-before-write) = spec "The change"; the WSL theos build gated on the `nm` invariants = spec "Build"; the build-only scope (deploy separate) = spec "Out of scope".
- **Placeholder scan:** none — the full replacement function is given verbatim; build/verify commands are exact.
- **Type consistency:** the `objc_msgSend` casts match the existing tweak's style (BOOL-returning selectors cast as `int`, as `writeToFile:atomically:` already does at the current line 112); `expectedContentLength` cast as `long long` (its real 64-bit return); the out-params `&resp,&err` typed `id *`.
- **Load-safety:** the added calls are all `objc_msgSend` to Foundation classes — no new ObjC class, no C++, no Blocks — preserving the no-unwind / no-`OBJC_CLASS_$` invariants the build gate checks.

## Out of scope (post-plan, operator-driven)

- Paced fleet redeploy of `mmcache.dylib` (+ plist) to `/Library/MobileSubstrate/DynamicLibraries` per device → respring, sequential + paced per the no-burst-SSH rule.
- On-device smoke (normal pull → CACHED+plays; truncated pull → CACHE_FAILED+central, no `verr=3`).
- Re-enabling the reconcile (`MM_CACHE_RECONCILE=1`) only after on-device validation.
