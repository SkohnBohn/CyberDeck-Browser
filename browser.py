#!/usr/bin/env python3
"""CyberDeck Browser — a minimal, distraction-free research browser.

Text and images only. No UI clutter. PyQt6 + QWebEngineView.
"""

import json
import os
import sys

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
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
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

# JavaScript/CSS injected into every page: forces the solarized/monospace
# look and permanently hides video, iframe, audio, and common clutter
# elements (ads / cookie banners / nav bars) where selectors can catch them.
INJECTED_CSS_TEMPLATE = """
(function() {
    var style = document.createElement('style');
    style.id = 'cyberdeck-injected-style';
    style.textContent = `
        html, body, p, div, span, li, td, th, a, h1, h2, h3, h4, h5, h6,
        input, textarea, button, article, section {
            background-color: %(bg)s !important;
            color: %(fg)s !important;
            font-family: %(font)s !important;
        }
        a, a:visited {
            color: %(accent)s !important;
        }
        video, iframe, audio {
            display: none !important;
        }
        %(images_rule)s
        [class*="cookie"], [id*="cookie"],
        [class*="consent"], [id*="consent"],
        [class*="banner"], [id*="banner"],
        [class*="advert"], [id*="advert"],
        [class*="ad-"], [id*="ad-"],
        [class*="ads"], [id*="ads"],
        nav, [role="navigation"] {
            display: none !important;
        }
    `;
    document.documentElement.appendChild(style);
})();
"""


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


class ResearchPage(QWebEnginePage):
    """Web page subclass that injects the solarized/monospace/hide-media CSS
    on every load, and reflects the images on/off setting."""

    def __init__(self, config, profile, parent=None):
        super().__init__(profile, parent)
        self.config = config
        self.loadFinished.connect(self._inject_style)

    def _inject_style(self, ok):
        if not ok:
            return
        images_rule = ""
        if not self.config.get("images_enabled", True):
            images_rule = "img, picture, svg { display: none !important; }"
        script = INJECTED_CSS_TEMPLATE % {
            "bg": BG,
            "fg": FG,
            "accent": ACCENT,
            "font": FONT_FAMILY,
            "images_rule": images_rule,
        }
        self.runJavaScript(script)


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

        # Web view with a page that injects our CSS and a profile that
        # permanently disables audio (not just hides it via CSS).
        profile = QWebEngineProfile.defaultProfile()
        self.view = QWebEngineView()
        page = ResearchPage(self.config, profile, self.view)
        self.view.setPage(page)

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
        if "://" not in text:
            # Treat as a URL if it looks like one, otherwise assume https.
            text = "https://" + text
        self.view.setUrl(QUrl(text))

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
        # Reload current tab so the new images setting takes effect.
        current = self.stack.currentWidget()
        if current is not None:
            current.view.reload()

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
