"""System tray icon using pystray with context menu and notifications."""

import threading
import logging
from typing import Optional, Callable

try:
    import pystray
    from PIL import Image, ImageDraw, ImageFont
    TRAY_AVAILABLE = True
except ImportError:
    TRAY_AVAILABLE = False

log = logging.getLogger("tray")


def create_icon_image(size: int = 64) -> "Image.Image":
    """Create a programmatic tray icon: orange circle with white 'C'."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Orange circle
    margin = 2
    draw.ellipse([margin, margin, size - margin, size - margin], fill="#e67e22")
    # White "C" letter
    font_size = size // 2
    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except (OSError, IOError):
        font = ImageFont.load_default()
    text = "C"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (size - tw) // 2
    y = (size - th) // 2 - bbox[1]
    draw.text((x, y), text, fill="white", font=font)
    return img


class TrayManager:
    def __init__(
        self,
        on_show_window: Callable,
        on_quit: Callable,
        on_pause_toggle: Optional[Callable] = None,
        on_run_all: Optional[Callable] = None,
    ):
        self.on_show_window = on_show_window
        self.on_quit = on_quit
        self.on_pause_toggle = on_pause_toggle
        self.on_run_all = on_run_all
        self._icon: Optional[pystray.Icon] = None
        self._thread: Optional[threading.Thread] = None
        self._paused = False
        self._schedule_count = 0
        self._next_run = ""

    @property
    def available(self) -> bool:
        return TRAY_AVAILABLE

    def start(self):
        if not TRAY_AVAILABLE:
            log.warning("pystray not available, tray disabled")
            return
        if self._icon:
            return
        self._icon = pystray.Icon(
            "ClaudeScheduler",
            icon=create_icon_image(),
            title="Claude Code Scheduler",
            menu=self._build_menu(),
        )
        self._thread = threading.Thread(target=self._icon.run, daemon=True)
        self._thread.start()
        log.info("Tray icon started")

    def stop(self):
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass
            self._icon = None

    def update_info(self, schedule_count: int = 0, next_run: str = ""):
        self._schedule_count = schedule_count
        self._next_run = next_run
        if self._icon:
            self._icon.menu = self._build_menu()
            self._icon.update_menu()

    def notify(self, title: str, message: str):
        if self._icon:
            try:
                self._icon.notify(message, title)
            except Exception:
                log.debug("Notification failed (may not be supported)")

    def _build_menu(self) -> pystray.Menu:
        items = [
            pystray.MenuItem("Show Window", self._on_show, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                f"Schedules: {self._schedule_count}", None, enabled=False
            ),
        ]
        if self._next_run:
            items.append(
                pystray.MenuItem(f"Next: {self._next_run}", None, enabled=False)
            )
        items.extend([
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Resume All" if self._paused else "Pause All",
                self._on_pause_toggle,
            ),
            pystray.MenuItem("Run All Now", self._on_run_all),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._on_quit),
        ])
        return pystray.Menu(*items)

    def _on_show(self, icon, item):
        self.on_show_window()

    def _on_pause_toggle(self, icon, item):
        self._paused = not self._paused
        if self.on_pause_toggle:
            self.on_pause_toggle(self._paused)
        if self._icon:
            self._icon.menu = self._build_menu()
            self._icon.update_menu()

    def _on_run_all(self, icon, item):
        if self.on_run_all:
            self.on_run_all()

    def _on_quit(self, icon, item):
        self.on_quit()
