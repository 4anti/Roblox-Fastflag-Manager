import os
import threading
import webview
import pystray
from PIL import Image, ImageDraw
from src.gui.api import Api


class MainWindow:
    def __init__(self):
        self.api = Api()

        # Path to HTML UI
        ui_path = os.path.join(os.path.dirname(__file__), 'ui', 'index.html')
        
        # Create the pywebview window
        # Check launch_minimized setting for initial visibility
        start_hidden = self.api.settings.get('launch_minimized', False)
        
        # Load window geometry from settings
        width = self.api.settings.get('window_width', 1050)
        height = self.api.settings.get('window_height', 780)

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
            hidden=start_hidden
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
            except:
                pass

    def _create_icon_image(self):
        """Generate a 64x64 icon that matches the UI logo style."""
        width = 64
        height = 64
        # Theme colors
        accent = (0, 212, 170) # #00d4aa
        # Create image with transparency support
        image = Image.new('RGBA', (width, height), (0, 0, 0, 0))
        dc = ImageDraw.Draw(image)
        
        # Draw rounded rectangle (matching .logo in CSS)
        padding = 4
        radius = 12
        dc.rounded_rectangle(
            [padding, padding, width - padding, height - padding],
            radius=radius,
            fill=accent
        )
        
        white = (255, 255, 255)
        dc.rectangle([18, 18, 24, 46], fill=white) # Stem
        dc.rectangle([18, 18, 38, 24], fill=white) # Top bar
        dc.rectangle([18, 30, 34, 35], fill=white) # Middle bar
        dc.rectangle([34, 18, 40, 46], fill=white) # Stem (offset slightly)
        dc.rectangle([34, 18, 54, 24], fill=white) # Top bar
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
        """Initialize pystray icon in a background thread."""
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
        # Start tray in separate thread
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

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
        def on_start(window):
            # Force initial resize from settings (create_window can sometimes be ignored)
            width = self.api.settings.get('window_width', 1050)
            height = self.api.settings.get('window_height', 780)
            window.resize(width, height)

            # Restore maximized state if saved
            if self.api.settings.get('window_maximized', False):
                window.maximize()

        webview.start(on_start, self.window, debug=False)
