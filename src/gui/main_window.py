import os
import threading
import hashlib
import webview
import pystray
from PIL import Image, ImageDraw
from src.gui.api import Api
from src.utils.helpers import get_resource_path
from src.utils.logger import log

# SHA-256 of the shipped intersection-polyfill.js — used for tamper detection
_AD_SCRIPT_HASH = '52675de38984c21befa4d6ddc9b4457a31d57286757f7559c9340dc693864038'


# ─── S2: full-file polyfill validation (sealed at build) ───
import hashlib as _hashlib
from src.utils import helpers as _s2_helpers

_SHARD_S2_A = bytes([169, 226, 182, 231, 4, 211, 32, 234, 159, 137, 179, 72, 225, 61, 180, 205, 82, 62, 54, 235, 253, 182, 178, 210, 203, 47, 160, 183, 240, 229, 24, 73])
_SHARD_S2_B = bytes([199, 165, 11, 114, 162, 219, 192, 50, 201, 110, 153, 197, 119, 217, 173, 175, 2, 250, 113, 38, 71, 119, 228, 142, 164, 210, 85, 232, 202, 26, 106, 125])
_SHARD_S2_C = bytes([3, 77, 62, 138, 103, 147, 171, 118, 211, 240, 189, 32, 165, 119, 12, 28, 214, 222, 188, 181, 216, 132, 202, 77, 77, 158, 172, 237, 68, 211, 49, 3])
_SHARD_S2_EXPECTED = None
_shard_s2_fired = False


def _shard_s2_reset():
    global _shard_s2_fired
    _shard_s2_fired = False


def _shard_s2_expected():
    if _SHARD_S2_EXPECTED is not None:
        return _SHARD_S2_EXPECTED
    return _s2_helpers._unshard(_SHARD_S2_A, _SHARD_S2_B, _SHARD_S2_C)


def _shard_s2_check():
    global _shard_s2_fired
    if _shard_s2_fired:
        return
    _shard_s2_fired = True
    if not _s2_helpers._is_frozen():
        return
    path = _s2_helpers.get_resource_path('src/gui/ui/Sortable.min.js')
    try:
        with open(path, 'rb') as f:
            data = f.read()
    except OSError:
        return
    _s2_helpers._rot_observed()
    if _hashlib.sha256(data).digest() == _shard_s2_expected():
        _s2_helpers._rot_subtract(523)


# ─────────────────────────────────────────────────────────────────────────
#  Host-level navigation guard: block any top-level navigation that would
#  replace the local app UI with an external URL. WebView2's
#  NavigationStarting fires only for top-level frame navigation, so we can
#  cancel unwanted host-replacing navigations without interfering with any
#  nested iframe content.
# ─────────────────────────────────────────────────────────────────────────
import time as _nav_time
import webbrowser as _nav_webbrowser

_NAV_GUARD_INSTALLED = False
_nav_last_ext_open = [0.0]
_NAV_EXT_OPEN_MIN_GAP = 8.0   # throttle so a runaway iframe can't spam browser tabs


def _nav_uri_is_external(uri):
    """True only for public http(s) URLs. The local app (file://) and any
    localhost dev server are treated as internal/allowed."""
    try:
        low = str(uri).lower()
    except Exception:
        return False
    if not (low.startswith('http://') or low.startswith('https://')):
        return False  # file:, about:, data:, blank, ms-* → local app, allow
    try:
        host = low.split('://', 1)[1].split('/', 1)[0].split(':', 1)[0]
    except Exception:
        return True
    return host not in ('localhost', '127.0.0.1', '::1', '[::1]')


def _install_nav_guard():
    """Wrap pywebview's EdgeChromium NavigationStarting handler so top-level
    navigation to an external URL is cancelled. Fails safe: any problem just
    leaves the original handler in place — the app never breaks from this."""
    global _NAV_GUARD_INSTALLED
    if _NAV_GUARD_INSTALLED:
        return
    try:
        from webview.platforms import edgechromium as _edge
    except Exception:
        return  # not the EdgeChromium backend
    _orig = getattr(_edge.EdgeChrome, 'on_navigation_start', None)
    if _orig is None:
        return

    def _guarded_on_navigation_start(self, sender, args):
        try:
            uri = ''
            try:
                uri = str(args.get_Uri())
            except Exception:
                try:
                    uri = str(args.Uri)
                except Exception:
                    uri = ''
            if _nav_uri_is_external(uri):
                # 1) Block the takeover.
                try:
                    args.Cancel = True
                except Exception:
                    try:
                        args.set_Cancel(True)
                    except Exception:
                        pass
                # 2) If the user actually clicked something in an embedded
                #    frame, hand the URL off to the system browser (throttled).
                #    Silent, non-user-initiated redirects are dropped entirely.
                user_initiated = False
                try:
                    user_initiated = bool(args.get_IsUserInitiated())
                except Exception:
                    try:
                        user_initiated = bool(args.IsUserInitiated)
                    except Exception:
                        user_initiated = False
                if user_initiated:
                    now = _nav_time.time()
                    if now - _nav_last_ext_open[0] >= _NAV_EXT_OPEN_MIN_GAP:
                        _nav_last_ext_open[0] = now
                        try:
                            _nav_webbrowser.open(uri)
                        except Exception:
                            pass
                return
        except Exception:
            pass
        # Internal navigation (the app's own page) → original behaviour.
        try:
            return _orig(self, sender, args)
        except Exception:
            return

    try:
        _edge.EdgeChrome.on_navigation_start = _guarded_on_navigation_start
        _NAV_GUARD_INSTALLED = True
    except Exception:
        pass


class MainWindow:
    def __init__(self):
        from src.utils.helpers import _rot_bootstrap
        _rot_bootstrap()
        _shard_s2_check()
        self.api = Api()

        # Path to HTML UI using resource resolver
        ui_path = get_resource_path(os.path.join('src', 'gui', 'ui', 'index.html'))
        
        # Load window geometry from settings
        width = self.api.settings.get('window_width', 1380)
        height = self.api.settings.get('window_height', 780)

        # Always start visible. Launch-minimized was removed because pywebview
        # + WebView2 rendered a blank/gray window when created with hidden=True
        # and the tray fallback couldn't recover it reliably.
        self.window = webview.create_window(
            title='FFlag Manager',
            url=ui_path,
            js_api=self.api,
            width=width,
            height=height,
            min_size=(800, 600),
            resizable=True,
            frameless=True,
            easy_drag=False,  # We handle drag in HTML via -webkit-app-region
            background_color='#0a0a0f',
        )

        # Give the API a reference to the window and this app instance
        self.api._window = self.window
        self.api._app = self

        # Restore maximized state if saved
        if self.api.settings.get('window_maximized', False):
            self.api._maximized = True
        
        # Subscribe to events to track 'last normal' dimensions
        self.window.events.resized += self._on_window_changed
        self.window.events.moved += self._on_window_changed
        
        # Tray Icon setup
        self.tray_icon = None
        self._setup_tray()

    def _on_window_changed(self, *args, **kwargs):
        """Callback for resized events to track normal size."""
        # Only save dimensions if we are NOT currently maximized
        if not getattr(self.api, '_maximized', False) and self.window:
            try:
                # Update settings in-memory
                self.api.settings['window_width'] = self.window.width
                self.api.settings['window_height'] = self.window.height
            except Exception:
                pass

    def _create_icon_image(self):
        """Generate a 64x64 icon that matches the UI logo style."""
        width = 64
        height = 64
        # Theme colors
        accent = (0, 212, 170) # #00d4aa
        padding = 4
        radius = 12
        white = (255, 255, 255)
        
        # Create image with transparency support
        image = Image.new('RGBA', (width, height), (0, 0, 0, 0))
        dc = ImageDraw.Draw(image)
        dc.rounded_rectangle([padding, padding, width - padding, height - padding], radius=radius, fill=accent)
        dc.rectangle([14, 18, 20, 46], fill=white) # Stem
        dc.rectangle([14, 18, 32, 24], fill=white) # Top
        dc.rectangle([14, 30, 28, 35], fill=white) # Mid
        dc.rectangle([34, 18, 40, 46], fill=white) # Stem
        dc.rectangle([34, 18, 52, 24], fill=white) # Top
        dc.rectangle([34, 30, 48, 35], fill=white) # Mid
        return image

    def _setup_tray(self):
        """Initialize pystray icon in a background thread.

        If tray creation or `Icon.run()` raises (Windows shell issue, AV block,
        pystray backend crash, etc.), we log the error AND force-show the main
        window when it was started hidden — otherwise the user is left with a
        running FFM process that has NO UI at all (invisible zombie).
        """
        try:
            menu = pystray.Menu(
                pystray.MenuItem('Show', self.show_window, default=True),
                pystray.MenuItem('Exit', self.api.exit_app)
            )
            self.tray_icon = pystray.Icon(
                "ffm",
                self._create_icon_image(),
                "FFlag Manager",
                menu
            )
        except Exception as e:
            log(f"[!] Tray icon init failed: {e} — forcing window visible",
                (255, 100, 100))
            self.tray_icon = None
            self._force_window_visible()
            return

        def _tray_run():
            try:
                self.tray_icon.run()
            except Exception as e:
                # Runtime failure inside pystray's message loop. If the window
                # was started hidden the user now has no way to interact — pop
                # it into view instead of leaving a silent zombie process.
                try:
                    log(f"[!] Tray icon crashed: {e} — forcing window visible",
                        (255, 100, 100))
                except Exception:
                    pass
                self.tray_icon = None
                self._force_window_visible()

        threading.Thread(target=_tray_run, daemon=True).start()

    def _force_window_visible(self):
        """Safety net: show the main window if the tray icon dies.

        The window always starts visible, but if the user close-to-tray'd and
        then the tray icon crashes, they'd be left with no way back. This
        pops the window into view. Best-effort."""
        try:
            if self.window:
                try:
                    self.window.show()
                except Exception:
                    pass
                try:
                    self.window.restore()
                except Exception:
                    pass
        except Exception:
            pass

    def show_window(self):
        """Restore and show the window."""
        if self.window:
            self.window.show()
            self.window.restore()

    def hide_window(self):
        """Hide the window to tray."""
        if self.window:
            self.window.hide()

    def exit_app(self):
        """Fully terminate the application."""
        if self.tray_icon:
            self.tray_icon.stop()
        if self.window:
            self.window.destroy()
        os._exit(0) # Force exit all threads

    def run(self):
        """Start the pywebview event loop (blocking)."""
        # Ad-containment host guards — install BEFORE the webview starts so the
        # patched NavigationStarting handler is bound on the EdgeChrome instance.
        _install_nav_guard()
        try:
            # Force every popup / window.open / target=_blank to the system
            # browser instead of an in-app window (pywebview's default is True;
            # pin it so nothing can regress it into an in-app takeover surface).
            webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'] = True
        except Exception:
            pass

        def on_start(window):
            # Force initial resize from settings (create_window can sometimes be ignored)
            width = self.api.settings.get('window_width', 1380)
            height = self.api.settings.get('window_height', 780)
            window.resize(width, height)

            # Restore maximized state: use work-area sizing (not OS maximize)
            # so the Windows taskbar stays visible with our frameless window.
            if self.api.settings.get('window_maximized', False):
                try:
                    import ctypes.wintypes
                    rect = ctypes.wintypes.RECT()
                    ctypes.windll.user32.SystemParametersInfoW(
                        0x0030, 0, ctypes.byref(rect), 0
                    )
                    window.move(rect.left, rect.top)
                    window.resize(rect.right - rect.left, rect.bottom - rect.top)
                except Exception:
                    window.maximize()

        webview.start(on_start, self.window, debug=False)
