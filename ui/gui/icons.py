"""Vector SVG icons for the PySide6 desktop interface."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_SVG_TEMPLATES: dict[str, str] = {
    "chat": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path fill="none" stroke="{color}" stroke-linecap="round" stroke-linejoin="round" '
        'stroke-width="1.8" d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/>'
        "</svg>"
    ),
    "send": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path fill="none" stroke="{color}" stroke-linecap="round" stroke-linejoin="round" '
        'stroke-width="1.9" d="M21 3L10 14M21 3l-7 18-4-7-7-4z"/>'
        "</svg>"
    ),
    "paperclip": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path fill="none" stroke="{color}" stroke-linecap="round" stroke-linejoin="round" '
        'stroke-width="1.8" d="M20 11l-8.5 8.5a5 5 0 0 1-7-7L13 4a3.3 3.3 0 0 1 4.7 4.7l-8.6 8.6a1.7 1.7 0 0 1-2.4-2.4L14 7"/>'
        "</svg>"
    ),
    "refresh": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path fill="none" stroke="{color}" stroke-linecap="round" stroke-linejoin="round" '
        'stroke-width="1.8" d="M20 11a8 8 0 1 0-2.3 5.7M20 5v6h-6"/>'
        "</svg>"
    ),
    "lock": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<rect x="5" y="11" width="14" height="9" rx="2" fill="none" stroke="{color}" '
        'stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8"/>'
        '<path fill="none" stroke="{color}" stroke-linecap="round" stroke-linejoin="round" '
        'stroke-width="1.8" d="M8 11V8a4 4 0 0 1 8 0v3"/>'
        "</svg>"
    ),
    "wifi": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path fill="none" stroke="{color}" stroke-linecap="round" stroke-linejoin="round" '
        'stroke-width="1.8" d="M3 9a14 14 0 0 1 18 0M6 12.5a9.5 9.5 0 0 1 12 0M9 16a5 5 0 0 1 6 0"/>'
        '<circle cx="12" cy="19" r="1" fill="{color}"/>'
        "</svg>"
    ),
    "users": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<circle cx="9" cy="8" r="3.5" fill="none" stroke="{color}" stroke-width="1.8"/>'
        '<path fill="none" stroke="{color}" stroke-linecap="round" stroke-width="1.8" '
        'd="M2.5 20a6.5 6.5 0 0 1 13 0M16 4.5a3.5 3.5 0 0 1 0 7M18 14a6.5 6.5 0 0 1 3.5 6"/>'
        "</svg>"
    ),
    "user": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<circle cx="12" cy="8" r="4" fill="none" stroke="{color}" stroke-width="1.8"/>'
        '<path fill="none" stroke="{color}" stroke-linecap="round" stroke-width="1.8" '
        'd="M4 20a8 8 0 0 1 16 0"/>'
        "</svg>"
    ),
    "file": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path fill="none" stroke="{color}" stroke-linecap="round" stroke-linejoin="round" '
        'stroke-width="1.8" d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/>'
        '<path fill="none" stroke="{color}" stroke-linecap="round" stroke-linejoin="round" '
        'stroke-width="1.8" d="M14 3v5h5"/>'
        "</svg>"
    ),
    "download": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path fill="none" stroke="{color}" stroke-linecap="round" stroke-linejoin="round" '
        'stroke-width="1.8" d="M12 4v12M7 11l5 5 5-5M5 20h14"/>'
        "</svg>"
    ),
    "hash": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path fill="none" stroke="{color}" stroke-linecap="round" stroke-linejoin="round" '
        'stroke-width="1.8" d="M5 9h15M4 15h15M10 3L8 21M16 3l-2 18"/>'
        "</svg>"
    ),
    "search": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<circle cx="11" cy="11" r="6.5" fill="none" stroke="{color}" stroke-width="1.8"/>'
        '<path fill="none" stroke="{color}" stroke-linecap="round" stroke-width="1.8" d="M16 16l4.5 4.5"/>'
        "</svg>"
    ),
    "sliders": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path fill="none" stroke="{color}" stroke-linecap="round" stroke-width="1.8" '
        'd="M4 7h10M18 7h2M4 17h2M10 17h10"/>'
        '<circle cx="16" cy="7" r="2" fill="none" stroke="{color}" stroke-width="1.8"/>'
        '<circle cx="8" cy="17" r="2" fill="none" stroke="{color}" stroke-width="1.8"/>'
        "</svg>"
    ),
    "logout": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path fill="none" stroke="{color}" stroke-linecap="round" stroke-linejoin="round" '
        'stroke-width="1.8" d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/>'
        "</svg>"
    ),
    "x": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path fill="none" stroke="{color}" stroke-linecap="round" stroke-width="1.9" d="M6 6l12 12M18 6L6 18"/>'
        "</svg>"
    ),
    "sun": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<circle cx="12" cy="12" r="4" fill="none" stroke="{color}" stroke-width="1.8"/>'
        '<path fill="none" stroke="{color}" stroke-linecap="round" stroke-width="1.8" '
        'd="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>'
        "</svg>"
    ),
    "moon": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path fill="none" stroke="{color}" stroke-linecap="round" stroke-linejoin="round" '
        'stroke-width="1.8" d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>'
        "</svg>"
    ),
    "folder": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path fill="none" stroke="{color}" stroke-linecap="round" stroke-linejoin="round" '
        'stroke-width="1.8" d="M3 6a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>'
        "</svg>"
    ),
    "alert": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<circle cx="12" cy="12" r="9" fill="none" stroke="{color}" stroke-width="1.8"/>'
        '<path fill="none" stroke="{color}" stroke-linecap="round" stroke-width="1.8" d="M12 8v5"/>'
        '<circle cx="12" cy="16.5" r="1" fill="{color}"/>'
        "</svg>"
    ),
    "panel-right": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<rect x="3" y="4" width="18" height="16" rx="2" fill="none" stroke="{color}" stroke-width="1.8"/>'
        '<path fill="none" stroke="{color}" stroke-width="1.8" d="M15 4v16"/>'
        "</svg>"
    ),
    "shield-check": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path fill="none" stroke="{color}" stroke-linecap="round" stroke-linejoin="round" '
        'stroke-width="1.8" d="M12 3l7 3v5c0 5-3.5 8.5-7 10-3.5-1.5-7-5-7-10V6z"/>'
        '<path fill="none" stroke="{color}" stroke-linecap="round" stroke-linejoin="round" '
        'stroke-width="1.8" d="M9 12l2 2 4-4"/>'
        "</svg>"
    ),
}


def get_icon(name: str, color: str = "#E6EAF0", size: int = 20) -> QIcon:
    """Render an inline SVG icon into a crisp HiDPI QIcon."""
    template = _SVG_TEMPLATES.get(name)
    if not template:
        return QIcon()

    svg_data = template.format(color=color).encode("utf-8")
    renderer = QSvgRenderer(QByteArray(svg_data))
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.setDevicePixelRatio(2.0)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return QIcon(pixmap)
