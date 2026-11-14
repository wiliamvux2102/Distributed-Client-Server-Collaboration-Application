"""Dark theme design tokens and Qt Style Sheet (QSS) for PySide6."""

from __future__ import annotations

PALETTE = {
    "bg_app": "#0D111A",
    "bg_panel": "#131926",
    "bg_card": "#1A2233",
    "bg_elevated": "#212B40",
    "bg_hover": "#253149",
    "bg_selected": "#1E3A5F",
    "border_subtle": "#263147",
    "border_focus": "#3B82F6",
    "text_primary": "#E2E8F0",
    "text_secondary": "#94A3B8",
    "text_muted": "#64748B",
    "brand_primary": "#1D4ED8",  # >= 6.7:1 contrast with #FFFFFF
    "brand_hover": "#2563EB",
    "brand_pressed": "#1E40AF",
    "on_primary": "#FFFFFF",
    "bubble_incoming": "#1A2233",
    "bubble_outgoing": "#1D4ED8",
    "accent_online": "#10B981",
    "accent_online_bg": "#064E3B",
    "accent_warning": "#F59E0B",
    "accent_error": "#EF4444",
    "accent_error_bg": "#451A1A",
    "accent_error_border": "#991B1B",
}


def build_stylesheet() -> str:
    """Return the complete QSS stylesheet using Qt-supported properties only."""
    p = PALETTE
    return f"""
    QMainWindow, QWidget#RootWindow {{
        background-color: {p["bg_app"]};
        color: {p["text_primary"]};
        font-family: "Inter", "Segoe UI", sans-serif;
        font-size: 10pt;
    }}

    QWidget {{
        color: {p["text_primary"]};
    }}

    /* Top utility bar */
    QFrame#TopBar {{
        background-color: {p["bg_panel"]};
        border-bottom: 1px solid {p["border_subtle"]};
    }}

    /* Sidebar & Main Chat panels */
    QFrame#Sidebar {{
        background-color: {p["bg_panel"]};
        border-right: 1px solid {p["border_subtle"]};
    }}

    QFrame#ChatHeader {{
        background-color: {p["bg_panel"]};
        border-bottom: 1px solid {p["border_subtle"]};
    }}

    QFrame#ComposerBar {{
        background-color: {p["bg_panel"]};
        border-top: 1px solid {p["border_subtle"]};
    }}

    QFrame#LoginCard {{
        background-color: {p["bg_panel"]};
        border: 1px solid {p["border_subtle"]};
        border-radius: 12px;
    }}

    QFrame#TransferDrawer {{
        background-color: {p["bg_card"]};
        border-top: 1px solid {p["border_subtle"]};
    }}

    QFrame#ErrorBanner {{
        background-color: {p["accent_error_bg"]};
        border-bottom: 1px solid {p["accent_error_border"]};
    }}

    /* Labels */
    QLabel#TitleLabel {{
        font-size: 13pt;
        font-weight: 600;
        color: {p["text_primary"]};
    }}

    QLabel#SectionHeader {{
        font-size: 8.5pt;
        font-weight: 600;
        color: {p["text_secondary"]};
        letter-spacing: 0.5px;
    }}

    QLabel#MutedLabel {{
        color: {p["text_secondary"]};
        font-size: 9pt;
    }}

    QLabel#MonoLabel {{
        font-family: "JetBrains Mono", "Consolas", monospace;
        font-size: 8.5pt;
        color: {p["text_secondary"]};
    }}

    QLabel#TlsBadge {{
        background-color: {p["accent_online_bg"]};
        color: {p["accent_online"]};
        border: 1px solid #065F46;
        border-radius: 10px;
        padding: 2px 8px;
        font-size: 8.5pt;
        font-weight: 600;
    }}

    QLabel#UserBadge {{
        background-color: {p["bg_card"]};
        border: 1px solid {p["border_subtle"]};
        border-radius: 10px;
        padding: 3px 10px;
        font-weight: 600;
        font-size: 9pt;
    }}

    /* Inputs */
    QLineEdit, QPlainTextEdit {{
        background-color: {p["bg_card"]};
        color: {p["text_primary"]};
        border: 1px solid {p["border_subtle"]};
        border-radius: 8px;
        padding: 7px 10px;
        selection-background-color: {p["brand_hover"]};
        selection-color: {p["on_primary"]};
    }}

    QLineEdit:focus, QPlainTextEdit:focus {{
        border: 2px solid {p["border_focus"]};
        padding: 6px 9px;
    }}

    QLineEdit:disabled, QPlainTextEdit:disabled {{
        background-color: {p["bg_app"]};
        color: {p["text_muted"]};
    }}

    /* Buttons */
    QPushButton {{
        background-color: {p["bg_card"]};
        color: {p["text_primary"]};
        border: 1px solid {p["border_subtle"]};
        border-radius: 8px;
        padding: 7px 14px;
        font-weight: 500;
    }}

    QPushButton:hover {{
        background-color: {p["bg_hover"]};
    }}

    QPushButton:pressed {{
        background-color: {p["bg_elevated"]};
    }}

    QPushButton:disabled {{
        color: {p["text_muted"]};
        background-color: {p["bg_panel"]};
    }}

    QPushButton#PrimaryButton {{
        background-color: {p["brand_primary"]};
        color: {p["on_primary"]};
        border: 1px solid {p["brand_primary"]};
        font-weight: 600;
    }}

    QPushButton#PrimaryButton:hover {{
        background-color: {p["brand_hover"]};
        border-color: {p["brand_hover"]};
    }}

    QPushButton#PrimaryButton:pressed {{
        background-color: {p["brand_pressed"]};
    }}

    QPushButton#PrimaryButton:disabled {{
        background-color: #1E293B;
        border-color: #1E293B;
        color: {p["text_muted"]};
    }}

    QPushButton#IconButton {{
        background-color: transparent;
        border: 1px solid transparent;
        border-radius: 6px;
        padding: 5px;
    }}

    QPushButton#IconButton:hover {{
        background-color: {p["bg_hover"]};
        border-color: {p["border_subtle"]};
    }}

    QPushButton#DangerButton {{
        background-color: transparent;
        color: #FCA5A5;
        border: 1px solid {p["accent_error_border"]};
        border-radius: 8px;
        padding: 5px 12px;
    }}

    QPushButton#DangerButton:hover {{
        background-color: {p["accent_error_bg"]};
    }}

    /* Radio buttons */
    QRadioButton {{
        spacing: 8px;
        color: {p["text_primary"]};
    }}

    /* Sidebar Lists */
    QListWidget#SidebarList {{
        background-color: transparent;
        border: none;
        outline: none;
    }}

    QListWidget#SidebarList::item {{
        border-radius: 8px;
        padding: 7px 10px;
        margin: 1px 4px;
        color: {p["text_primary"]};
    }}

    QListWidget#SidebarList::item:hover {{
        background-color: {p["bg_hover"]};
    }}

    QListWidget#SidebarList::item:selected {{
        background-color: {p["bg_selected"]};
        color: {p["on_primary"]};
        font-weight: 600;
    }}

    /* Message Feed */
    QListView#MessageFeed {{
        background-color: {p["bg_app"]};
        border: none;
        outline: none;
    }}

    /* Progress Bars */
    QProgressBar {{
        background-color: {p["bg_app"]};
        border: 1px solid {p["border_subtle"]};
        border-radius: 4px;
        height: 8px;
        text-align: center;
    }}

    QProgressBar::chunk {{
        background-color: {p["brand_hover"]};
        border-radius: 3px;
    }}

    /* Scrollbars */
    QScrollBar:vertical {{
        background: transparent;
        width: 8px;
        margin: 2px;
    }}

    QScrollBar::handle:vertical {{
        background: {p["border_subtle"]};
        border-radius: 4px;
        min-height: 24px;
    }}

    QScrollBar::handle:vertical:hover {{
        background: {p["text_muted"]};
    }}

    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0px;
    }}

    /* Splitter */
    QSplitter::handle {{
        background-color: {p["border_subtle"]};
        width: 1px;
    }}
    """
