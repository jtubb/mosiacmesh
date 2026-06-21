"""Data classes for the server's in-memory state and the singleton's structure.

The singleton `settings = Settings()` itself stays in `server.py` (see PR-1
plan rationale): the existing test pattern `server.settings = mock_settings`
requires `server` to be the canonical namespace for the instance binding.
This module owns the CLASS definitions and stateless helpers.
"""
from enum import Enum
import time


class Settings():
    def __init__(self):
        self.displays = {}
        self.scripts = {}
        self.clients = {}
        self.playlists = {}
        self.schedules = {}
        self.profiles = {}     # {name: ScriptingProfile} — populated by REST or
                               # PR-3's bootstrap; empty dict on first ever start

class Scripts():
    def __init__(self):
        self.name = ''
        self.description = ''
        self.value = None
        self.status = None

class Display():
    def __init__(self):
        self.boundingBox = None
        self.boundingBoxCenter = None
        # [GW, GH] device-pixel global wall canvas size for mesh animations,
        # computed at calibration (assign_group_bounding_boxes). None until
        # the group is calibrated; a mesh item with no meshGlobal -> black.
        self.meshGlobal = None
        self.mediaElements = []
        self.loop = False
        self.currentFrame = 0
        self.action = PlayState.NOACTION
        self.currentPlaylistName = None   # name of the playlist whose items are currently applied (None = idle)
        self.playStartEpoch = 0   # server-time ms when playback last (re)started
        self.playSeed = 0         # per-run coordinated PRNG seed (minted at _begin_prepare);
                                  # persisted in settings.dat so a late-joiner after a restart
                                  # shares the same seed as the screens already running
        self.pauseOffset = 0      # ms into the playlist when paused
        self.renderedToken = ""   # token of the last successful SEGMENT render
        self.renderStatus = ""    # "" | "rendering" | "ready" | "error"
        # Per-(playlist) render registry for THIS group (PR auto-render).
        # { playlistName: {token, state, updatedAt, error, percent, eta, startedAt} }
        # state ∈ render.RENDER_{QUEUED,RENDERING,READY,STALE,FAILED}.
        # Persists in settings.dat; revalidated against render_token + on-disk
        # assets at boot. renderedToken/renderStatus above are the legacy
        # single-applied-playlist fields, kept for the live playback path.
        self.renders = {}
        self.defaultPlaylistName = None   # fallback playlist when no schedule is active
        self.scheduledEntryId = None      # transient: which schedule/"__default__" currently drives this group
        self.scheduledPlaying = False     # transient: have we issued PLAY for the current effective target
        self.prepareId = None
        self.readyClients = set()
        self.armPending = set()   # clients that sent NEEDS_ARM, awaiting a human tap
        self.prepareDeadline = 0

class PlayState(Enum):
    NOACTION = 0
    STOP = 1
    PLAY = 2
    PAUSE = 3
    PREPARING = 4

class MediaElement():
    def __init__(self):
        self.id = None
        self.file = None
        self.duration = None
        self.playmode = PlayMode.DEFAULT
        self.backgroundColor = "#000000"
        self.startEffect = None
        self.endEffect = None
        # 'mirror' (default, every screen draws the full animation) | 'mesh'
        # (animation spans the calibrated wall; each screen draws its slice).
        self.scriptSpan = 'mirror'


class Playlist():
    def __init__(self):
        self.name = ""
        self.items = []      # list of item dicts: id, file, duration, playmode, backgroundColor, startEffect, endEffect, scriptSpan
        self.loop = False
        # Monotonic version bumped on each PUT via the REST API. 0 = never persisted
        # via the REST surface (e.g. instances created in pre-PR-2 code paths or
        # loaded from older settings.dat). Used for If-Match optimistic
        # concurrency in mosaicmesh/api/playlists.py.
        self._serverVersion = 0

class Schedule():
    def __init__(self):
        self.id = ""
        self.name = ""
        self.playlistName = ""
        self.displayID = ""
        self.priority = 0
        self.enabled = True
        self.freq = "DAILY"          # DAILY | WEEKLY | MONTHLY | YEARLY
        self.interval = 1
        self.byweekday = []          # ints 0=Mon..6=Sun (WEEKLY)
        self.dtstart = ""            # "YYYY-MM-DD"
        self.end = {"type": "never"} # or {"type":"until","untilDate":...} / {"type":"count","count":N}
        self.exdates = []            # ["YYYY-MM-DD", ...]
        self.startTime = "00:00"
        self.endTime = "23:59"
        # Monotonic version bumped on each PUT via the REST API. See Playlist
        # for rationale.
        self._serverVersion = 0


class ScriptingProfile():
    """Per-device-type bundle of lifecycle scripts + launch configuration +
    webclip identity + SSH options. Auto-matched to clients by deviceType
    on REGISTER (matchDeviceType field). Template variables in script
    strings (e.g. {webclipBundleId}, {displayUrl}, {ip}) are substituted
    at run time via SafeDict.

    PR-2 ships the class shape + REST CRUD + Settings.profiles dict.
    PR-3 ships the launch dispatcher (_exec_ssh / _vnc_tap_sequence /
    _ssh_then_vnc in mosaicmesh.device_scripts) + the bootstrap migration
    that seeds the ipad1-ios5 default profile on first startup. The legacy
    hardcoded script constants were removed in PR-3 Task 7.
    """
    def __init__(self):
        self.name = ""                # unique key (e.g. "ipad1-ios5")
        self.label = ""               # human label ("iPad 1 — iOS 5.1.1")
        self.matchDeviceType = ""     # auto-assign on REGISTER (e.g. "Tablet"); "" = manual only
        self.scripts = {              # template-variable shell commands
            "login": "",
            "start": "",
            "stop":  "",
            "test":  "",
            "reboot": "",
        }
        self.launch = {               # how to actually launch the display
            "method": "shell",        # "shell" | "vnc-tap" | "ssh-then-vnc"
            # Method-specific keys (vncPassword, taps, wakeScript, etc.) are
            # only present when the corresponding method is active. Code that
            # reads them must use dict.get() to avoid KeyError on profiles
            # that haven't configured the keys yet (e.g. fresh shells).
        }
        self.webclip = {              # iOS-5 webclip metadata
            "bundleId": "",
            "title":    "",
        }
        self.ssh = {                  # SSH connection options.
            # legacyCrypto enables the SHA-1-era cipher/kex set required for
            # iOS 5.1.1 sshd; defaults to False (modern device); set True
            # when creating a profile for iPad-1-era hardware.
            "legacyCrypto": False,
            "user": "root",
            "keyPath": "",
        }
        # Monotonic version bumped on each PUT via the REST API.
        self._serverVersion = 0


class PlayMode(Enum):
    DEFAULT = 0
    FULL = 1
    SEGMENT = 2
    SCRIPT = 3
    INDIVIDUAL = 4

class Client():
    def __init__(self):
        self.friendlyName = None
        self.clientID = ""
        self.displayID = None
        self.arucoID = None
        self.deviceHeight = 0
        self.deviceWidth = 0
        self.canvasWidth = 0    # rendered viewport (innerWidth) — reflects actual
        self.canvasHeight = 0   # orientation; device* is the raw screen resolution
        self.measuredCenter = None
        self.measuredPerimeter = None
        self.userAgent = None
        self.ip = ""
        self.hostname = ""              # reverse-DNS (PTR) of ip, when resolvable
        self.hostnameResolved = False   # PTR lookup attempted (don't retry per ip)
        self.nameIsCustom = False       # user set friendlyName -> DNS won't override
        self.touch = False              # client reported touch support at REGISTER
        self.osName=""
        self.osVersion=""
        self.engine=""
        self.deviceBrand=""
        self.deviceModel=""
        self.deviceType=""
        # Selected scripting profile (key into Settings.profiles). None means
        # auto-match has not yet found a matching profile for this device's
        # deviceType (or no profiles exist). The dispatcher treats None as
        # "no scripts to run" and logs a warning.
        self.profileName = None
        self.ready = False      # ready to display: media cached & client ready
        self.isOnline = False   # alive: connected / recent heartbeat
        self.synced = False     # SYN/SYNACK handshake (clock/group) complete
        # Enhanced discovery fields
        self.discoveryTime = time.time()
        self.lastSeen = time.time()
        self.connectionCount = 0
        self.capabilities = []
        self.autoConfigured = False
        self.discoverySource = "manual"  # manual, websocket, network
        # Cache-state model (2026-06-03). cacheMode = "none" by default;
        # set to "lighttpd-localhost" by onboarding when the iPad has
        # lighttpd installed and a writable /var/mobile/Media/
        # MosaicMeshCache/ dir. Set to "service-worker" by the client's
        # ANNOUNCE_CACHE_MODE message when SW registration succeeds.
        # See docs/superpowers/specs/2026-06-03-media-cache-design.md.
        self.cacheMode = "none"
        # Hashes of segments currently cached on this device, in the
        # form "<encode_ver_hash>_<segment_index>" (matches the
        # seg_<HASH>_<N>.mp4 filename convention from the render
        # pipeline). Populated by _push_segment_to_cached_clients on
        # successful scp; pruned by _reconcile_ipad_cache.
        self.cachedSegments = set()
        # Wall-clock ms of the last server-side cache-capability SSH probe
        # (None = never probed). Observability only; nothing gates on it.
        self.cacheProbedMs = None
        # In-memory only (does not persist; meaningful only during a
        # push). Set to a dict by _push_segment_to_cached_clients when
        # a push starts; cleared to None when the push ends (success
        # or stall). Shape: {"token", "n", "bytesSent", "totalBytes",
        # "startedMs", "lastChangeMs", "status", "mbps"}.
        # See docs/superpowers/specs/2026-06-03-cache-progress-and-
        # propagation-ui.md.
        self.cachePushProgress = None


def migrate_client_objects():
    """Migrate old client objects to include new discovery fields"""
    import server as _server
    settings = _server.settings
    if not hasattr(settings, 'playlists'):
        settings.playlists = {}
    if not hasattr(settings, 'schedules'):
        settings.schedules = {}
    for _disp in settings.displays.values():
        if not hasattr(_disp, 'defaultPlaylistName'):
            _disp.defaultPlaylistName = None
        _disp.scheduledEntryId = None      # transient — reset on startup
        _disp.scheduledPlaying = False
        # coordinated-start fields: backfill onto older Displays AND reset the
        # transient prepare state (a restart cancels any in-flight prepare).
        _disp.prepareId = None
        _disp.readyClients = set()
        _disp.armPending = set()
        _disp.prepareDeadline = 0
        if not hasattr(_disp, 'renders'):
            _disp.renders = {}
        if not hasattr(_disp, 'playSeed'):
            _disp.playSeed = 0
    current_time = time.time()
    for client_key, client in settings.clients.items():
        if not hasattr(client, 'discoveryTime'):
            client.discoveryTime = current_time
        if not hasattr(client, 'lastSeen'):
            client.lastSeen = current_time
        if not hasattr(client, 'connectionCount'):
            client.connectionCount = 1
        if not hasattr(client, 'capabilities'):
            client.capabilities = []
        if not hasattr(client, 'autoConfigured'):
            client.autoConfigured = False
        if not hasattr(client, 'discoverySource'):
            client.discoverySource = "existing"
        if not hasattr(client, 'isOnline'):
            client.isOnline = False
        if not hasattr(client, 'synced'):
            client.synced = False
        if not hasattr(client, 'hostname'):
            client.hostname = ""
        if not hasattr(client, 'hostnameResolved'):
            client.hostnameResolved = False
        if not hasattr(client, 'touch'):
            client.touch = False
        if not hasattr(client, 'canvasWidth'):
            client.canvasWidth = getattr(client, 'deviceWidth', 0)
        if not hasattr(client, 'canvasHeight'):
            client.canvasHeight = getattr(client, 'deviceHeight', 0)
        if not hasattr(client, 'nameIsCustom'):
            # Protect pre-existing custom names: a name that is NOT the
            # auto-generated '<device>_<key[:8]>' form is treated as user-set,
            # so reverse-DNS won't clobber it.
            fn = client.friendlyName or ""
            client.nameIsCustom = bool(fn) and not fn.endswith('_' + client_key[:8])
        if not hasattr(client, 'cacheMode'):
            client.cacheMode = "none"
        if not hasattr(client, 'cachedSegments'):
            client.cachedSegments = set()
        if not hasattr(client, 'cacheProbedMs'):
            client.cacheProbedMs = None
        # cachePushProgress is transient (a push is meaningful only
        # while the process is live), so unconditionally reset on
        # startup -- any state in settings.dat is stale.
        client.cachePushProgress = None
        if not hasattr(client, 'profileName'):
            client.profileName = None
        # Re-attempt resolution for clients that never got a hostname (e.g.
        # resolved blank before DNS was fixed / before the mDNS fallback). The
        # 60s retry throttle keeps perpetually-nameless devices from churning.
        if not getattr(client, 'hostname', ''):
            client.hostnameResolved = False

    # PR-3 bootstrap: seed the ipad1-ios5 default profile on a settings.dat
    # that pre-dates Settings.profiles, AND migrate every Client object's
    # legacy script fields to the new profileName indirection.
    from mosaicmesh.profile_bootstrap import (
        seed_default_profile_if_empty, migrate_client_script_fields,
    )
    seed_default_profile_if_empty(settings)
    migrate_client_script_fields(settings)
