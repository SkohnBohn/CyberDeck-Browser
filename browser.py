#!/usr/bin/env python3
"""CyberDeck Browser — a single-purpose retro-futurist terminal browser.

Monochrome phosphor CRT aesthetic. Text and images only. No UI clutter.
This machine does one thing. PyQt6 + QWebEngineView.
"""

import json
import os
import re
import sys
from urllib.parse import quote_plus

from PyQt6.QtCore import QRect, Qt, QTimer, QUrl
from PyQt6.QtGui import QColor, QFont, QKeySequence, QPainter, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtWebEngineCore import QWebEngineProfile, QWebEngineScript, QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView

# ---------------------------------------------------------------------------
# Monochrome phosphor terminal palette — one hue, nothing else. A dedicated
# machine for one task doesn't get a "theme"; it gets the screen it has.
# ---------------------------------------------------------------------------
BG = "#0A0E0A"          # near-black CRT glass
FG = "#33FF33"          # phosphor green — the only "color" in the machine
FG_DIM = "#1B7A1B"      # dim green: structure, inactive, rule lines
FG_BRIGHT = "#8CFF8C"   # bright green: current selection / caret only
FONT_FAMILY = '"Courier New", "DejaVu Sans Mono", "Consolas", monospace'

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
DEFAULT_CONFIG = {"images_enabled": True}

# DuckDuckGo's HTML-only endpoint: no JavaScript required, minimal markup,
# no cookie-consent wall, and it doesn't track/personalize like Google —
# a good match for a text-only, distraction-free browser.
SEARCH_URL = "https://html.duckduckgo.com/html/?q=%s"

# Matches "looks like a domain/URL" (e.g. "example.com", "localhost:8000",
# "192.168.1.1/admin") so bare keyword queries can be told apart from
# addresses without requiring the user to type a scheme.
_URL_LIKE_RE = re.compile(
    r"^(localhost)(:\d+)?(/.*)?$"
    r"|^(\d{1,3}\.){3}\d{1,3}(:\d+)?(/.*)?$"
    r"|^[\w-]+(\.[\w-]+)+(:\d+)?(/.*)?$"
)


def resolve_address(text):
    """Turn address-bar text into a URL: pass through real URLs/domains,
    send anything else to DuckDuckGo as a keyword search."""
    if "://" in text:
        return text
    first_word = text.split()[0] if text.split() else text
    if " " not in text and _URL_LIKE_RE.match(first_word):
        return "https://" + text
    return SEARCH_URL % quote_plus(text)


# Forces the monochrome terminal look on every page. This sets style
# *properties* directly on each element (el.style.setProperty(...)) rather
# than injecting a <style> tag or stylesheet: a page's Content-Security-Policy
# (style-src) blocks stylesheets and <style> tags even when they come from an
# isolated script world, but it does not block direct DOM style-property
# mutation — so this is the one approach that reliably lands on every site.
# A MutationObserver re-applies it to nodes added after the initial pass
# (e.g. content a page renders client-side after load).
STYLE_SCRIPT_TEMPLATE = """
(function() {
    var BG = %(bg)s, FG = %(fg)s, ACCENT = %(accent)s, FONT = %(font)s;
    var IMAGES_ENABLED = %(images_enabled)s;

    var HIDE_SELECTOR = [
        'video', 'iframe', 'audio',
        '[class*="cookie" i]', '[id*="cookie" i]',
        '[class*="consent" i]', '[id*="consent" i]',
        '[class*="banner" i]', '[id*="banner" i]',
        '[class*="advert" i]', '[id*="advert" i]',
        '[class*="ad-" i]', '[id*="ad-" i]',
        '[class*="ads" i]', '[id*="ads" i]',
        'nav', '[role="navigation"]'
    ].join(', ');
    var IMAGE_SELECTOR = 'img, svg, picture, canvas';

    function styleElement(el) {
        var s = el.style;
        s.setProperty('background-color', BG, 'important');
        s.setProperty('background-image', 'none', 'important');
        s.setProperty('color', FG, 'important');
        s.setProperty('font-family', FONT, 'important');
        s.setProperty('border-color', FG, 'important');
        s.setProperty('box-shadow', 'none', 'important');
        s.setProperty('text-shadow', 'none', 'important');

        if (el.tagName === 'A') {
            s.setProperty('color', ACCENT, 'important');
            s.setProperty('text-decoration', 'underline', 'important');
        }
        if (el.matches(HIDE_SELECTOR)) {
            s.setProperty('display', 'none', 'important');
        }
        if (el.matches(IMAGE_SELECTOR)) {
            // Logos/badges/icons can't be recolored via `color`, so drain
            // them of hue instead — nothing shows a color outside the
            // palette unless images are switched off entirely.
            if (IMAGES_ENABLED) {
                s.setProperty('filter', 'grayscale(1) contrast(1.2) brightness(0.9)', 'important');
            } else {
                s.setProperty('display', 'none', 'important');
            }
        }
    }

    function styleTree(root) {
        if (root.nodeType !== 1) return;
        styleElement(root);
        var descendants = root.querySelectorAll('*');
        for (var i = 0; i < descendants.length; i++) styleElement(descendants[i]);
    }

    styleTree(document.documentElement);
    window.addEventListener('load', function() { styleTree(document.documentElement); });

    new MutationObserver(function(mutations) {
        for (var i = 0; i < mutations.length; i++) {
            var added = mutations[i].addedNodes;
            for (var j = 0; j < added.length; j++) styleTree(added[j]);
        }
    }).observe(document.documentElement, {childList: true, subtree: true});

    // Best-effort scrollbar restyle: a <style> tag can be blocked by a
    // page's CSP (unlike the direct style mutation above), so this is
    // allowed to silently fail on strict-CSP sites rather than break
    // anything else.
    try {
        var sb = document.createElement('style');
        sb.textContent =
            '::-webkit-scrollbar { width: 12px; height: 12px; background: ' + BG + '; }' +
            '::-webkit-scrollbar-thumb { background: ' + FG + '; border-radius: 0; }' +
            '::-webkit-scrollbar-corner { background: ' + BG + '; }';
        document.documentElement.appendChild(sb);
    } catch (e) {}
})();
"""


def _style_source(config):
    return STYLE_SCRIPT_TEMPLATE % {
        "bg": json.dumps(BG),
        "fg": json.dumps(FG),
        "accent": json.dumps(FG),
        "font": json.dumps(FONT_FAMILY),
        "images_enabled": "true" if config.get("images_enabled", True) else "false",
    }


STYLE_SCRIPT_NAME = "cyberdeck-style"


def install_style_script(profile, config):
    """(Re-)register the terminal-monochrome override on the given profile
    so it is injected into every page this profile loads, regardless of
    that page's CSP."""
    collection = profile.scripts()
    for existing in collection.find(STYLE_SCRIPT_NAME):
        collection.remove(existing)

    script = QWebEngineScript()
    script.setName(STYLE_SCRIPT_NAME)
    script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
    script.setWorldId(QWebEngineScript.ScriptWorldId.ApplicationWorld)
    script.setRunsOnSubFrames(True)
    script.setSourceCode(_style_source(config))
    collection.insert(script)


def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r") as f:
                data = json.load(f)
                cfg = dict(DEFAULT_CONFIG)
                cfg.update(data)
                return cfg
        except (json.JSONDecodeError, OSError):
            pass
    return dict(DEFAULT_CONFIG)


def save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)


# ---------------------------------------------------------------------------
# One global stylesheet for the whole machine: flat rectangles, no
# border-radius, no gradients, no hover-glow transitions. Inverted-video
# (background/foreground swap) stands in for "hover" and "selected", the
# way an actual terminal indicates focus.
# ---------------------------------------------------------------------------
APP_STYLESHEET = f"""
QWidget {{
    background-color: {BG};
    color: {FG};
    font-family: {FONT_FAMILY};
    font-size: 13px;
}}

QMainWindow {{
    background-color: {BG};
}}

QLineEdit {{
    background-color: {BG};
    color: {FG};
    border: none;
    border-bottom: 2px solid {FG_DIM};
    padding: 6px 4px;
    selection-background-color: {FG};
    selection-color: {BG};
}}
QLineEdit:focus {{
    border-bottom: 2px solid {FG};
}}

QListWidget {{
    background-color: {BG};
    border: none;
    border-right: 2px solid {FG_DIM};
    outline: none;
}}
QListWidget::item {{
    padding: 4px 6px;
    border-bottom: 1px solid {FG_DIM};
}}
QListWidget::item:selected {{
    background-color: {FG};
    color: {BG};
}}

QPushButton {{
    background-color: {BG};
    color: {FG};
    border: 2px solid {FG_DIM};
    padding: 6px;
}}
QPushButton:hover {{
    border: 2px solid {FG};
    color: {FG_BRIGHT};
}}
QPushButton:pressed {{
    background-color: {FG};
    color: {BG};
}}

QLabel {{
    background: transparent;
    color: {FG};
}}

QCheckBox {{
    color: {FG};
    spacing: 8px;
}}
QCheckBox::indicator {{
    width: 14px;
    height: 14px;
    border: 2px solid {FG};
    background: {BG};
}}
QCheckBox::indicator:checked {{
    background: {FG};
}}

QDialog {{
    background-color: {BG};
    border: 2px solid {FG};
}}

QScrollBar:vertical {{
    background: {BG};
    width: 14px;
    border-left: 2px solid {FG_DIM};
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {FG_DIM};
    min-height: 24px;
    border-radius: 0;
}}
QScrollBar::handle:vertical:hover {{
    background: {FG};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: {BG};
}}

QScrollBar:horizontal {{
    background: {BG};
    height: 14px;
    border-top: 2px solid {FG_DIM};
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: {FG_DIM};
    min-width: 24px;
    border-radius: 0;
}}
QScrollBar::handle:horizontal:hover {{
    background: {FG};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    background: {BG};
}}
"""


class ScanlineOverlay(QWidget):
    """Static CRT scanline + vignette painted over the whole window.

    Mouse-transparent so it never intercepts clicks — purely a visual skin
    reinforcing that this is a screen being looked at, not an app window.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        # Horizontal scanlines.
        scanline = QColor(0, 0, 0, 28)
        painter.setPen(scanline)
        for y in range(0, self.height(), 3):
            painter.drawLine(0, y, self.width(), y)

        # Vignette: darken toward the edges without a radial-gradient
        # dependency — four overlapping flat bands is enough to read as one.
        vignette = QColor(0, 0, 0, 90)
        band = max(self.width(), self.height()) // 14 or 1
        painter.fillRect(QRect(0, 0, self.width(), band), vignette)
        painter.fillRect(QRect(0, self.height() - band, self.width(), band), vignette)
        painter.fillRect(QRect(0, 0, band, self.height()), vignette)
        painter.fillRect(QRect(self.width() - band, 0, band, self.height()), vignette)


class SettingsDialog(QDialog):
    """Single minimal settings panel: one option, images on/off."""

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("SYSTEM CONFIG")

        layout = QVBoxLayout(self)
        header = QLabel("[ SYSTEM CONFIG ]")
        header.setStyleSheet(f"color: {FG_BRIGHT}; font-weight: bold;")
        layout.addWidget(header)

        self.images_checkbox = QCheckBox("ENABLE IMAGE RENDERING")
        self.images_checkbox.setChecked(self.config.get("images_enabled", True))
        layout.addWidget(self.images_checkbox)

        close_button = QPushButton("[ CLOSE ]")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button)

    def accept(self):
        self.config["images_enabled"] = self.images_checkbox.isChecked()
        save_config(self.config)
        super().accept()


class BrowserTab(QWidget):
    """A single session: address bar (rendered as a prompt) + web view."""

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        prompt_row = QWidget()
        prompt_layout = QHBoxLayout(prompt_row)
        prompt_layout.setContentsMargins(6, 0, 0, 0)
        prompt_layout.setSpacing(0)

        prompt_glyph = QLabel(">")
        prompt_glyph.setStyleSheet(f"color: {FG_BRIGHT}; font-weight: bold; border: none;")
        prompt_layout.addWidget(prompt_glyph)

        self.address_bar = QLineEdit()
        self.address_bar.setPlaceholderText("ENTER ADDRESS OR QUERY_")
        self.address_bar.returnPressed.connect(self.navigate_to_address)
        prompt_layout.addWidget(self.address_bar)

        layout.addWidget(prompt_row)

        # The default profile carries the style-override script installed
        # in main() via install_style_script(), so no custom page subclass
        # is needed here — every page this view loads gets it automatically.
        self.view = QWebEngineView()

        # Permanently disable audio output at the QWebEngineView level.
        settings = self.view.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, True)
        self.view.page().setAudioMuted(True)

        self.view.titleChanged.connect(self._on_title_changed)
        self.view.urlChanged.connect(self._on_url_changed)

        layout.addWidget(self.view)

        self.title = "NEW SESSION"
        self._on_title_changed_callback = None

    def navigate_to_address(self):
        text = self.address_bar.text().strip()
        if not text:
            return
        self.view.setUrl(QUrl(resolve_address(text)))

    def load_url(self, url):
        self.address_bar.setText(url)
        self.view.setUrl(QUrl(url))

    def _on_title_changed(self, title):
        self.title = title.upper() if title else "NEW SESSION"
        if self._on_title_changed_callback:
            self._on_title_changed_callback(self.title)

    def _on_url_changed(self, url):
        self.address_bar.setText(url.toString())


class TabListItemWidget(QWidget):
    """Row shown in the vertical session list: index, title, close glyph.

    Reads like a terminal process list ("01 > TITLE") rather than a browser
    tab — the selected row inverts instead of taking an accent color.
    """

    def __init__(self, index, title, on_close, parent=None):
        super().__init__(parent)
        self.setAutoFillBackground(True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)

        self.label = QLabel(self._format(index, title))
        layout.addWidget(self.label, stretch=1)

        self.close_button = QPushButton("[X]")
        self.close_button.setFixedSize(28, 20)
        self.close_button.clicked.connect(on_close)
        layout.addWidget(self.close_button)

        self._index = index
        self._title = title
        self.set_selected(False)

        # --- Tab drag stub -------------------------------------------------
        # Drag is wired up here as a placeholder only: mousePressEvent below
        # records the press so a future implementation can start a QDrag on
        # sufficient movement. There is intentionally no drop target yet —
        # reordering tabs by drag-and-drop is a future extension point.
        self._drag_start_pos = None

    @staticmethod
    def _truncate(title, max_len=18):
        return title if len(title) <= max_len else title[: max_len - 1] + "…"

    def _format(self, index, title):
        return f"{index:02d} > {self._truncate(title)}"

    def set_index(self, index):
        self._index = index
        self.label.setText(self._format(index, self._title))

    def set_selected(self, selected):
        # Inverted video, the way a real terminal marks the focused line —
        # a separate widget painted over the list, so it must invert itself
        # rather than rely on QListWidget's own selection color.
        bg, fg = (FG, BG) if selected else (BG, FG)
        self.setStyleSheet(f"background-color: {bg};")
        self.label.setStyleSheet(f"color: {fg}; background: transparent;")
        self.close_button.setStyleSheet(
            f"background-color: {bg}; color: {fg}; border: 1px solid {fg};"
        )

    def set_title(self, title):
        self._title = title
        self.label.setText(self._format(self._index, title))

    def mousePressEvent(self, event):
        # Tab drag stub: record the starting position only. No QDrag is
        # started and no drop target is implemented yet.
        self._drag_start_pos = event.position()
        super().mousePressEvent(event)


class BootScreen(QWidget):
    """Fake POST/boot sequence shown before the terminal comes up."""

    LINES = [
        "CYBERDECK OS v0.9.1",
        "",
        "BOOT SEQUENCE INITIATED...",
        "MOUNTING DISPLAY DRIVER............ OK",
        "MOUNTING NETWORK STACK.............. OK",
        "MOUNTING INPUT DEVICES.............. OK",
        "LOADING TERMINAL SHELL.............. OK",
        "",
        "> READY_",
    ]

    def __init__(self, on_done, parent=None):
        super().__init__(parent)
        self._on_done = on_done
        self.setStyleSheet(f"background-color: {BG};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.addStretch()

        self._label = QLabel("")
        self._label.setStyleSheet(f"color: {FG}; font-size: 14px;")
        layout.addWidget(self._label)
        layout.addStretch()

        self._shown_lines = []
        self._line_index = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)
        self._timer.start(180)

    def _advance(self):
        if self._line_index >= len(self.LINES):
            self._timer.stop()
            QTimer.singleShot(500, self._on_done)
            return
        self._shown_lines.append(self.LINES[self._line_index])
        self._label.setText("\n".join(self._shown_lines))
        self._line_index += 1


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config = load_config()
        install_style_script(QWebEngineProfile.defaultProfile(), self.config)

        self.setWindowTitle("CYBERDECK // TERMINAL")
        self.resize(1100, 700)

        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # --- Left vertical session stack ------------------------------------
        left_panel = QWidget()
        left_panel.setFixedWidth(220)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)

        panel_header = QLabel("[ SESSIONS ]")
        panel_header.setStyleSheet(
            f"color: {FG_BRIGHT}; font-weight: bold; padding: 6px; "
            f"border-right: 2px solid {FG_DIM}; border-bottom: 2px solid {FG_DIM};"
        )
        left_layout.addWidget(panel_header)

        self.tab_list = QListWidget()
        self.tab_list.currentRowChanged.connect(self._on_tab_selected)
        left_layout.addWidget(self.tab_list, stretch=1)

        new_tab_button = QPushButton("[ + NEW SESSION ]")
        new_tab_button.clicked.connect(self.new_tab)
        left_layout.addWidget(new_tab_button)

        settings_button = QPushButton("[ CONFIG ]")
        settings_button.clicked.connect(self.open_settings)
        left_layout.addWidget(settings_button)

        root_layout.addWidget(left_panel)

        # --- Stacked session content area ------------------------------------
        self.stack = QStackedWidget()
        root_layout.addWidget(self.stack, stretch=1)

        self.tabs = []  # list of BrowserTab

        # Global monospace font for the UI chrome.
        QApplication.instance().setFont(QFont("Courier New", 10))

        # The scanline/vignette overlay belongs on the "screen" (the content
        # area) only — not on the sidebar, which reads as the deck's own
        # control panel/bezel rather than something being displayed on glass.
        self._overlay = ScanlineOverlay(self.stack)
        self._overlay.setGeometry(self.stack.rect())
        self._overlay.raise_()

        self._setup_shortcuts()
        self.new_tab()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._overlay.setGeometry(self.stack.rect())
        self._overlay.raise_()

    # -- Tab management -------------------------------------------------
    def new_tab(self):
        tab = BrowserTab(self.config)
        item = QListWidgetItem()
        widget = TabListItemWidget(len(self.tabs) + 1, tab.title, lambda t=tab: self.close_tab(t))
        item.setSizeHint(widget.sizeHint())

        self.tab_list.addItem(item)
        self.tab_list.setItemWidget(item, widget)
        self.stack.addWidget(tab)

        tab._list_item = item
        tab._list_widget = widget
        tab._on_title_changed_callback = widget.set_title

        self.tabs.append(tab)
        self.tab_list.setCurrentRow(self.tab_list.count() - 1)
        tab.address_bar.setFocus()

    def close_tab(self, tab):
        if len(self.tabs) == 1:
            # Never close the last tab; just reset it to blank instead.
            tab.address_bar.clear()
            tab.view.setUrl(QUrl("about:blank"))
            return

        row = self.tab_list.row(tab._list_item)
        self.tab_list.takeItem(row)
        self.stack.removeWidget(tab)
        self.tabs.remove(tab)
        tab.deleteLater()
        self._renumber_tabs()

    def _renumber_tabs(self):
        for i, tab in enumerate(self.tabs, start=1):
            tab._list_widget.set_index(i)

    def close_current_tab(self):
        current = self.stack.currentWidget()
        if current is not None:
            self.close_tab(current)

    def _on_tab_selected(self, row):
        if 0 <= row < len(self.tabs):
            self.stack.setCurrentIndex(row)
        for i, tab in enumerate(self.tabs):
            tab._list_widget.set_selected(i == row)
        self._overlay.raise_()

    def focus_address_bar(self):
        current = self.stack.currentWidget()
        if current is not None:
            current.address_bar.setFocus()
            current.address_bar.selectAll()

    # -- Settings ---------------------------------------------------------
    def open_settings(self):
        dialog = SettingsDialog(self.config, self)
        dialog.exec()
        # Re-install the style script with the new images setting and
        # reload every open tab so it takes effect immediately.
        install_style_script(QWebEngineProfile.defaultProfile(), self.config)
        for tab in self.tabs:
            tab.view.reload()

    # -- Keyboard shortcuts -------------------------------------------------
    def _setup_shortcuts(self):
        QShortcut(QKeySequence("Ctrl+T"), self, activated=self.new_tab)
        QShortcut(QKeySequence("Ctrl+W"), self, activated=self.close_current_tab)
        QShortcut(QKeySequence("Ctrl+L"), self, activated=self.focus_address_bar)


# ---------------------------------------------------------------------------
# Future Obsidian integration would connect here: a hook capturing the
# current tab's URL/title/selection (e.g. from BrowserTab.view.page()) and
# writing it into an Obsidian vault as a note. Nothing is wired up yet.
# ---------------------------------------------------------------------------


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(APP_STYLESHEET)

    window = MainWindow()

    boot = BootScreen(on_done=window.show)
    boot.setWindowTitle("CYBERDECK // TERMINAL")
    boot.resize(600, 400)
    boot.show()

    def finish_boot():
        boot.close()
        window.show()

    boot._on_done = finish_boot

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
