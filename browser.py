#!/usr/bin/env python3
"""CyberDeck Browser — a minimal, distraction-free research browser.

Text and images only. No UI clutter. PyQt6 + QWebEngineView.
"""

import json
import os
import re
import sys
from urllib.parse import quote_plus

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QFont, QKeySequence, QShortcut
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
# Solarized palette + monospace typewriter aesthetic
# ---------------------------------------------------------------------------
BG = "#FDF6E3"
FG = "#657B83"
ACCENT = "#B58900"
FONT_FAMILY = "Courier New, Courier, monospace"

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

# Forces the solarized/monospace look on every page. This sets style
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
        }
        if (el.matches(HIDE_SELECTOR)) {
            s.setProperty('display', 'none', 'important');
        }
        if (el.matches(IMAGE_SELECTOR)) {
            // Logos/badges/icons can't be recolored via `color`, so drain
            // them of hue instead — nothing shows a color outside the
            // palette unless images are switched off entirely.
            if (IMAGES_ENABLED) {
                s.setProperty('filter', 'grayscale(1) contrast(1.1)', 'important');
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
})();
"""


def _style_source(config):
    return STYLE_SCRIPT_TEMPLATE % {
        "bg": json.dumps(BG),
        "fg": json.dumps(FG),
        "accent": json.dumps(ACCENT),
        "font": json.dumps(FONT_FAMILY),
        "images_enabled": "true" if config.get("images_enabled", True) else "false",
    }


STYLE_SCRIPT_NAME = "cyberdeck-style"


def install_style_script(profile, config):
    """(Re-)register the solarized/monospace override on the given profile
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


class SettingsDialog(QDialog):
    """Single minimal settings panel: one option, images on/off."""

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("Settings")
        self.setStyleSheet(
            f"background-color: {BG}; color: {FG}; font-family: {FONT_FAMILY};"
        )

        layout = QVBoxLayout(self)
        self.images_checkbox = QCheckBox("Load images")
        self.images_checkbox.setChecked(self.config.get("images_enabled", True))
        self.images_checkbox.setStyleSheet(f"color: {FG}; font-family: {FONT_FAMILY};")
        layout.addWidget(self.images_checkbox)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button)

    def accept(self):
        self.config["images_enabled"] = self.images_checkbox.isChecked()
        save_config(self.config)
        super().accept()


class BrowserTab(QWidget):
    """A single tab: address bar + web view."""

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.address_bar = QLineEdit()
        self.address_bar.setPlaceholderText("Enter address...")
        self.address_bar.setStyleSheet(
            f"background-color: {BG}; color: {FG}; font-family: {FONT_FAMILY};"
            f"border: 1px solid {FG}; padding: 4px;"
        )
        self.address_bar.returnPressed.connect(self.navigate_to_address)
        layout.addWidget(self.address_bar)

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

        self.title = "New Tab"
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
        self.title = title if title else "New Tab"
        if self._on_title_changed_callback:
            self._on_title_changed_callback(self.title)

    def _on_url_changed(self, url):
        self.address_bar.setText(url.toString())


class TabListItemWidget(QWidget):
    """Row shown in the vertical tab stack: title label + close button."""

    def __init__(self, title, on_close, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)

        self.label = QLabel(self._truncate(title))
        self.label.setStyleSheet(f"color: {FG}; font-family: {FONT_FAMILY};")
        layout.addWidget(self.label, stretch=1)

        close_button = QPushButton("x")
        close_button.setFixedSize(18, 18)
        close_button.setStyleSheet(
            f"background-color: {BG}; color: {ACCENT}; font-family: {FONT_FAMILY};"
            "border: none;"
        )
        close_button.clicked.connect(on_close)
        layout.addWidget(close_button)

        # --- Tab drag stub -------------------------------------------------
        # Drag is wired up here as a placeholder only: mousePressEvent below
        # records the press so a future implementation can start a QDrag on
        # sufficient movement. There is intentionally no drop target yet —
        # reordering tabs by drag-and-drop is a future extension point.
        self._drag_start_pos = None

    @staticmethod
    def _truncate(title, max_len=20):
        return title if len(title) <= max_len else title[: max_len - 1] + "…"

    def set_title(self, title):
        self.label.setText(self._truncate(title))

    def mousePressEvent(self, event):
        # Tab drag stub: record the starting position only. No QDrag is
        # started and no drop target is implemented yet.
        self._drag_start_pos = event.position()
        super().mousePressEvent(event)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config = load_config()
        install_style_script(QWebEngineProfile.defaultProfile(), self.config)

        self.setWindowTitle("CyberDeck Browser")
        self.setStyleSheet(f"background-color: {BG};")
        self.resize(1100, 700)

        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # --- Left vertical tab stack --------------------------------------
        left_panel = QWidget()
        left_panel.setFixedWidth(200)
        left_panel.setStyleSheet(f"background-color: {BG}; border-right: 1px solid {FG};")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)

        self.tab_list = QListWidget()
        self.tab_list.setStyleSheet(
            f"background-color: {BG}; border: none; font-family: {FONT_FAMILY};"
        )
        self.tab_list.currentRowChanged.connect(self._on_tab_selected)
        left_layout.addWidget(self.tab_list, stretch=1)

        new_tab_button = QPushButton("+ New Tab")
        new_tab_button.setStyleSheet(
            f"background-color: {BG}; color: {ACCENT}; font-family: {FONT_FAMILY};"
            f"border-top: 1px solid {FG}; padding: 6px;"
        )
        new_tab_button.clicked.connect(self.new_tab)
        left_layout.addWidget(new_tab_button)

        settings_button = QPushButton("Settings")
        settings_button.setStyleSheet(
            f"background-color: {BG}; color: {FG}; font-family: {FONT_FAMILY};"
            "border: none; padding: 6px;"
        )
        settings_button.clicked.connect(self.open_settings)
        left_layout.addWidget(settings_button)

        root_layout.addWidget(left_panel)

        # --- Stacked tab content area --------------------------------------
        self.stack = QStackedWidget()
        root_layout.addWidget(self.stack, stretch=1)

        self.tabs = []  # list of BrowserTab

        # Global monospace font for the UI chrome.
        QApplication.instance().setFont(QFont("Courier New", 10))

        self._setup_shortcuts()
        self.new_tab()

    # -- Tab management -------------------------------------------------
    def new_tab(self):
        tab = BrowserTab(self.config)
        item = QListWidgetItem()
        widget = TabListItemWidget(tab.title, lambda t=tab: self.close_tab(t))
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

    def close_current_tab(self):
        current = self.stack.currentWidget()
        if current is not None:
            self.close_tab(current)

    def _on_tab_selected(self, row):
        if 0 <= row < len(self.tabs):
            self.stack.setCurrentIndex(row)

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
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
