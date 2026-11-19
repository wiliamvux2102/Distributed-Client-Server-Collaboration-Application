"""Dual-mode (Dark Obsidian+Mint / Light Daylight) design tokens and QSS for PySide6."""

from __future__ import annotations

from pathlib import Path

_ICON_DIR = Path(__file__).with_name("assets").as_posix()

DARK_PALETTE: dict[str, str] = {
    "mode": "dark",
    "bg_rail": "#0A0D11",
    "bg_app": "#0E1217",
    "bg_panel": "#12171E",
    "bg_card": "#171E27",
    "bg_elevated": "#1B2430",
    "bg_hover": "#1A222D",
    "bg_selected": "#1B2430",
    "border_subtle": "#1C232D",
    "border_card": "#232B36",
    "border_strong": "#2A3441",
    "border_focus": "#5EEAD4",
    "text_primary": "#E6EAF0",
    "text_secondary": "#C3CCD8",
    "text_muted": "#9AA6B5",
    "brand_primary": "#5EEAD4",
    "brand_hover": "#7EF3DF",
    "brand_pressed": "#2DD4BF",
    "brand_soft": "#16322F",
    "brand_soft_text": "#5EEAD4",
    "on_primary": "#062A26",
    "bubble_incoming": "#1B2430",
    "bubble_incoming_border": "#232B36",
    "bubble_outgoing": "#2563EB",
    "bubble_outgoing_meta": "#9AA6B5",
    "accent_online": "#34D399",
    "accent_online_bg": "#16322F",
    "accent_online_border": "#1F4D47",
    "accent_warning": "#FBBF24",
    "accent_warning_bg": "#3A280B",
    "accent_error": "#F87171",
    "accent_error_bg": "#2A1215",
    "accent_error_border": "#5C2B2E",
    "accent_error_text": "#FCA5A5",
    "avatar_text": "#0B1220",
}

LIGHT_PALETTE: dict[str, str] = {
    "mode": "light",
    "bg_rail": "#E2E8F0",
    "bg_app": "#F8FAFC",
    "bg_panel": "#FFFFFF",
    "bg_card": "#F1F5F9",
    "bg_elevated": "#E2E8F0",
    "bg_hover": "#F1F5F9",
    "bg_selected": "#CCFBF1",
    "border_subtle": "#E2E8F0",
    "border_card": "#CBD5E1",
    "border_strong": "#94A3B8",
    "border_focus": "#0D9488",
    "text_primary": "#0F172A",
    "text_secondary": "#334155",
    "text_muted": "#64748B",
    "brand_primary": "#0D9488",
    "brand_hover": "#0F766E",
    "brand_pressed": "#115E59",
    "brand_soft": "#CCFBF1",
    "brand_soft_text": "#0F766E",
    "on_primary": "#FFFFFF",
    "bubble_incoming": "#FFFFFF",
    "bubble_incoming_border": "#CBD5E1",
    "bubble_outgoing": "#2563EB",
    "bubble_outgoing_meta": "#64748B",
    "accent_online": "#059669",
    "accent_online_bg": "#ECFDF5",
    "accent_online_border": "#A7F3D0",
    "accent_warning": "#D97706",
    "accent_warning_bg": "#FFFBEB",
    "accent_error": "#DC2626",
    "accent_error_bg": "#FEF2F2",
    "accent_error_border": "#FECACA",
    "accent_error_text": "#B91C1C",
    "avatar_text": "#0B1220",
}

PALETTE: dict[str, str] = dict(DARK_PALETTE)

# Pastel avatar backgrounds (high contrast with dark #0B1220 initials).
AVATAR_COLORS: tuple[str, ...] = (
    "#7DD3FC",
    "#FDBA74",
    "#C4B5FD",
    "#86EFAC",
    "#F9A8D4",
    "#FDE68A",
    "#93C5FD",
    "#5EEAD4",
)


def avatar_color_for(name: str) -> str:
    """Return a deterministic pastel color for a username."""
    if not name:
        return AVATAR_COLORS[0]
    idx = sum(ord(ch) for ch in name) % len(AVATAR_COLORS)
    return AVATAR_COLORS[idx]


def get_palette(mode: str = "dark") -> dict[str, str]:
    """Return palette tokens for 'dark' or 'light' mode and sync global PALETTE."""
    source = LIGHT_PALETTE if mode == "light" else DARK_PALETTE
    PALETTE.clear()
    PALETTE.update(source)
    return PALETTE


def build_stylesheet(mode: str = "dark") -> str:
    """Return the complete QSS stylesheet using Qt-supported properties only."""
    p = get_palette(mode)
    return f"""
    QMainWindow, QWidget#RootWindow {{
        background-color: {p["bg_app"]};
        color: {p["text_primary"]};
        font-family: "Inter", "Segoe UI", system-ui, sans-serif;
        font-size: 10pt;
    }}

    QWidget {{
        color: {p["text_primary"]};
    }}

    /* Sign-In Split Panels */
    QFrame#BrandPanel {{
        background-color: {p["bg_rail"]};
        border-right: 1px solid {p["border_subtle"]};
    }}

    QWidget#LoginFormPanel {{
        background-color: {p["bg_app"]};
    }}

    /* Workspace 4-Zone Panels */
    QFrame#NavRail {{
        background-color: {p["bg_rail"]};
        border-right: 1px solid {p["border_subtle"]};
    }}

    QFrame#Sidebar {{
        background-color: {p["bg_panel"]};
        border-right: 1px solid {p["border_subtle"]};
    }}

    QFrame#InspectorPanel {{
        background-color: {p["bg_panel"]};
        border-left: 1px solid {p["border_subtle"]};
    }}

    QFrame#ChatHeader {{
        background-color: {p["bg_app"]};
        border-bottom: 1px solid {p["border_subtle"]};
    }}

    QFrame#ComposerWrapper {{
        background-color: {p["bg_app"]};
    }}

    QFrame#ComposerBox {{
        background-color: {p["bg_panel"]};
        border: 1px solid {p["border_strong"]};
        border-radius: 16px;
    }}

    QFrame#ComposerBox[focused="true"] {{
        border: 1.5px solid {p["border_focus"]};
    }}

    QFrame#InfoCard {{
        background-color: {p["bg_card"]};
        border: 1px solid {p["border_card"]};
        border-radius: 14px;
    }}

    QFrame#ModeCard {{
        background-color: {p["bg_panel"]};
        border: 1px solid {p["border_card"]};
        border-radius: 12px;
    }}

    QFrame#SidebarFooter {{
        background-color: {p["bg_panel"]};
        border-top: 1px solid {p["border_subtle"]};
    }}

    QFrame#SearchContainer {{
        background-color: {p["bg_app"]};
        border: 1px solid {p["border_card"]};
        border-radius: 10px;
    }}

    QFrame#SearchContainer[focused="true"] {{
        border: 1px solid {p["border_focus"]};
    }}

    QFrame#ErrorBanner {{
        background-color: {p["accent_error_bg"]};
        border-bottom: 1px solid {p["accent_error_border"]};
    }}

    QFrame#EmptyCard {{
        background-color: {p["bg_panel"]};
        border: 1px solid {p["border_card"]};
        border-radius: 16px;
    }}

    QFrame#BrandMark {{
        background-color: {p["brand_primary"]};
        border-radius: 12px;
    }}

    QFrame#FeatureIconBox {{
        background-color: {p["brand_soft"]};
        border-radius: 10px;
    }}

    /* Labels */
    QLabel#SidebarTitle {{
        font-size: 13.5pt;
        font-weight: 700;
        color: {p["text_primary"]};
    }}

    QLabel#HeroTitle {{
        font-size: 23pt;
        font-weight: 700;
        color: {p["text_primary"]};
    }}

    QLabel#SignInTitle {{
        font-size: 20pt;
        font-weight: 700;
        color: {p["text_primary"]};
    }}

    QLabel#SectionHeader {{
        font-size: 8.5pt;
        font-weight: 600;
        color: {p["text_muted"]};
        letter-spacing: 0.7px;
    }}

    QLabel#FieldLabel {{
        font-size: 9.5pt;
        font-weight: 500;
        color: {p["text_secondary"]};
    }}

    QLabel#MutedLabel {{
        color: {p["text_muted"]};
        font-size: 9pt;
    }}

    QLabel#SecondaryLabel {{
        color: {p["text_secondary"]};
        font-size: 9.5pt;
    }}

    QLabel#NeutralChip {{
        background-color: {p["bg_elevated"]};
        color: {p["text_secondary"]};
        border-radius: 14px;
        padding: 4px 12px;
        font-size: 9pt;
        font-weight: 600;
    }}

    QLabel#TlsChip {{
        background-color: {p["brand_soft"]};
        color: {p["brand_soft_text"]};
        border-radius: 14px;
        padding: 4px 12px;
        font-size: 9pt;
        font-weight: 600;
    }}

    /* Inputs */
    QLineEdit {{
        background-color: {p["bg_panel"]};
        color: {p["text_primary"]};
        border: 1px solid {p["border_strong"]};
        border-radius: 10px;
        padding: 8px 14px;
        selection-background-color: {p["brand_primary"]};
        selection-color: {p["on_primary"]};
    }}

    QLineEdit:focus {{
        border: 1.5px solid {p["border_focus"]};
    }}

    QLineEdit:disabled {{
        background-color: {p["bg_rail"]};
        color: {p["text_muted"]};
        border-color: {p["border_subtle"]};
    }}

    QLineEdit#SearchInput {{
        background-color: transparent;
        border: none;
        padding: 5px 4px;
    }}

    QLineEdit#SearchInput:focus {{
        border: none;
        padding: 5px 4px;
    }}

    QPlainTextEdit#ComposerInput {{
        background-color: transparent;
        color: {p["text_primary"]};
        border: none;
        padding: 6px 6px;
        selection-background-color: {p["brand_primary"]};
        selection-color: {p["on_primary"]};
    }}

    QPlainTextEdit#ComposerInput:focus {{
        border: none;
        padding: 6px 6px;
    }}

    /* Buttons */
    QPushButton {{
        background-color: {p["bg_card"]};
        color: {p["text_primary"]};
        border: 1px solid {p["border_card"]};
        border-radius: 10px;
        padding: 7px 14px;
        font-weight: 600;
    }}

    QPushButton:hover {{
        background-color: {p["bg_elevated"]};
        border-color: {p["border_strong"]};
    }}

    QPushButton:pressed {{
        background-color: {p["bg_hover"]};
    }}

    QPushButton:disabled {{
        color: {p["text_muted"]};
        background-color: {p["bg_panel"]};
        border-color: {p["border_subtle"]};
    }}

    QPushButton#PrimaryButton {{
        background-color: {p["brand_primary"]};
        color: {p["on_primary"]};
        border: none;
        border-radius: 12px;
        font-weight: 700;
    }}

    QPushButton#PrimaryButton:hover {{
        background-color: {p["brand_hover"]};
    }}

    QPushButton#PrimaryButton:pressed {{
        background-color: {p["brand_pressed"]};
    }}

    QPushButton#PrimaryButton:disabled {{
        background-color: {p["bg_elevated"]};
        color: {p["text_muted"]};
    }}

    QPushButton#SendButton {{
        background-color: {p["brand_primary"]};
        color: {p["on_primary"]};
        border: none;
        border-radius: 12px;
    }}

    QPushButton#SendButton:hover {{
        background-color: {p["brand_hover"]};
    }}

    QPushButton#SendButton:pressed {{
        background-color: {p["brand_pressed"]};
    }}

    QPushButton#RailButton {{
        background-color: transparent;
        border: none;
        border-radius: 12px;
    }}

    QPushButton#RailButton:hover {{
        background-color: {p["bg_hover"]};
    }}

    QPushButton#RailButton:checked {{
        background-color: {p["brand_soft"]};
    }}

    QPushButton#IconButton {{
        background-color: transparent;
        border: 1px solid transparent;
        border-radius: 10px;
        padding: 5px;
    }}

    QPushButton#IconButton:hover {{
        background-color: {p["bg_elevated"]};
    }}

    QPushButton#ModePill {{
        background-color: transparent;
        color: {p["text_muted"]};
        border: 1px solid transparent;
        border-radius: 8px;
        padding: 6px 12px;
        font-weight: 600;
        font-size: 9pt;
    }}

    QPushButton#ModePill:checked {{
        background-color: {p["brand_soft"]};
        color: {p["brand_soft_text"]};
        border: 1px solid {p["accent_online_border"]};
    }}

    QPushButton#SoftActionButton {{
        background-color: {p["brand_soft"]};
        color: {p["brand_soft_text"]};
        border: none;
        border-radius: 14px;
        padding: 5px 12px;
        font-size: 9pt;
        font-weight: 600;
    }}

    QPushButton#SoftActionButton:hover {{
        background-color: {p["brand_primary"]};
        color: {p["on_primary"]};
    }}

    QPushButton#DangerIconButton {{
        background-color: transparent;
        border: none;
        border-radius: 10px;
        padding: 6px;
    }}

    QPushButton#DangerIconButton:hover {{
        background-color: {p["accent_error_bg"]};
    }}

    /* Radio buttons */
    QRadioButton {{
        spacing: 8px;
        color: {p["text_secondary"]};
        font-weight: 500;
    }}

    /* Sidebar Lists */
    QListWidget#SidebarList {{
        background-color: transparent;
        border: none;
        outline: none;
    }}

    QListWidget#SidebarList::item {{
        border-radius: 10px;
        padding: 2px 2px;
        margin: 1px 6px;
        color: {p["text_primary"]};
    }}

    QListWidget#SidebarList::item:hover {{
        background-color: {p["bg_hover"]};
    }}

    QListWidget#SidebarList::item:selected {{
        background-color: {p["bg_selected"]};
        color: {p["text_primary"]};
    }}

    /* Message Feed */
    QListView#MessageFeed {{
        background-color: {p["bg_app"]};
        border: none;
        outline: none;
    }}

    /* Progress Bars */
    QProgressBar {{
        background-color: {p["border_strong"]};
        border: none;
        border-radius: 3px;
        height: 6px;
        text-align: center;
    }}

    QProgressBar::chunk {{
        background-color: {p["brand_primary"]};
        border-radius: 3px;
    }}

    /* Scrollbars */
    QScrollBar:vertical {{
        background: transparent;
        width: 7px;
        margin: 4px 2px;
    }}

    QScrollBar::handle:vertical {{
        background: {p["border_strong"]};
        border-radius: 3px;
        min-height: 28px;
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

    /* Context Menu */
    QMenu {{
        background-color: {p["bg_card"]};
        color: {p["text_primary"]};
        border: 1px solid {p["border_strong"]};
        border-radius: 10px;
        padding: 4px;
    }}

    QMenu::item {{
        padding: 7px 14px;
        border-radius: 6px;
    }}

    QMenu::item:selected {{
        background-color: {p["bg_elevated"]};
    }}

    /* ComboBox & Dropdown Popup */
    QComboBox {{
        combobox-popup: 0;
        background-color: {p["bg_panel"]};
        color: {p["text_primary"]};
        border: 1px solid {p["border_strong"]};
        border-radius: 8px;
        padding: 7px 30px 7px 10px;
        min-height: 20px;
        font-size: 9pt;
    }}

    QComboBox:hover {{
        border-color: {p["text_muted"]};
    }}

    QComboBox:focus, QComboBox:on {{
        border-color: {p["border_focus"]};
    }}

    QComboBox:disabled {{
        color: {p["text_muted"]};
        border-color: {p["border_subtle"]};
    }}

    QComboBox::drop-down {{
        subcontrol-origin: padding;
        subcontrol-position: top right;
        width: 28px;
        border: none;
    }}

    QComboBox::down-arrow {{
        image: url("{_ICON_DIR}/chevron-down-{p["mode"]}.svg");
        width: 12px;
        height: 12px;
    }}

    QFrame#TransportModePopup {{
        background-color: {p["bg_card"]};
        border: 1px solid {p["border_strong"]};
        border-radius: 8px;
    }}

    QComboBox QAbstractItemView {{
        background-color: {p["bg_card"]};
        color: {p["text_primary"]};
        border: none;
        padding: 0px;
        font-size: 9pt;
        selection-background-color: {p["brand_soft"]};
        selection-color: {p["brand_soft_text"]};
        outline: 0;
    }}

    QComboBox QAbstractItemView::item {{
        padding: 6px 10px;
        border-radius: 6px;
        min-height: 24px;
    }}

    QComboBox QAbstractItemView::item:hover {{
        background-color: {p["bg_hover"]};
    }}

    QComboBox QAbstractItemView::item:selected {{
        background-color: {p["brand_soft"]};
        color: {p["brand_soft_text"]};
    }}
    """
