"""Vector SVG icons (Phosphor-style 20px outline) for PySide6."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_SVG_TEMPLATES: dict[str, str] = {
    "send": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">'
        '<path fill="none" stroke="{color}" stroke-linecap="round" stroke-linejoin="round" '
        'stroke-width="18" d="M227.32,28.68a16,16,0,0,0-15.66-4.08l-186.1,56.39a16,16,0,0,0-2.06,30l75.47,33.54,'
        '33.54,75.47a16,16,0,0,0,14.62,9.5h.51a16,16,0,0,0,14.87-11.56l56.39-186.1A16,16,0,0,0,227.32,28.68Z"/>'
        '<line x1="99" y1="157" x2="156" y2="100" stroke="{color}" stroke-linecap="round" '
        'stroke-linejoin="round" stroke-width="18"/>'
        "</svg>"
    ),
    "paperclip": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">'
        '<path fill="none" stroke="{color}" stroke-linecap="round" stroke-linejoin="round" '
        'stroke-width="18" d="M160,80l-76.69,76.69a16,16,0,0,0,22.63,22.62L192,93.25a32,32,0,0,0-45.25-45.25'
        'L58.34,136.4a48,48,0,0,0,67.88,67.88L204,126.5"/>'
        "</svg>"
    ),
    "refresh": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">'
        '<polyline fill="none" stroke="{color}" stroke-linecap="round" stroke-linejoin="round" '
        'stroke-width="18" points="176 104 224 104 224 56"/>'
        '<path fill="none" stroke="{color}" stroke-linecap="round" stroke-linejoin="round" '
        'stroke-width="18" d="M190.22,190.22a88,88,0,1,1,0-124.44L224,104"/>'
        "</svg>"
    ),
    "lock": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">'
        '<rect x="40" y="88" width="176" height="128" rx="12" fill="none" stroke="{color}" '
        'stroke-linecap="round" stroke-linejoin="round" stroke-width="18"/>'
        '<path fill="none" stroke="{color}" stroke-linecap="round" stroke-linejoin="round" '
        'stroke-width="18" d="M88,88V56a40,40,0,0,1,80,0V88"/>'
        "</svg>"
    ),
    "users": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">'
        '<circle cx="88" cy="108" r="36" fill="none" stroke="{color}" stroke-width="18"/>'
        '<path fill="none" stroke="{color}" stroke-linecap="round" stroke-width="18" '
        'd="M24,200c12-30,36-44,64-44s52,14,64,44"/>'
        '<path fill="none" stroke="{color}" stroke-linecap="round" stroke-width="18" '
        'd="M168,72a32,32,0,1,1,0,64"/>'
        '<path fill="none" stroke="{color}" stroke-linecap="round" stroke-width="18" '
        'd="M176,158c24,4,44,18,54,42"/>'
        "</svg>"
    ),
    "user": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">'
        '<circle cx="128" cy="96" r="44" fill="none" stroke="{color}" stroke-width="18"/>'
        '<path fill="none" stroke="{color}" stroke-linecap="round" stroke-width="18" '
        'd="M40,216c18-36,48-52,88-52s70,16,88,52"/>'
        "</svg>"
    ),
    "logout": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">'
        '<path fill="none" stroke="{color}" stroke-linecap="round" stroke-linejoin="round" '
        'stroke-width="18" d="M112,216H48a8,8,0,0,1-8-8V48a8,8,0,0,1,8-8h64"/>'
        '<polyline fill="none" stroke="{color}" stroke-linecap="round" stroke-linejoin="round" '
        'stroke-width="18" points="168 88 208 128 168 168"/>'
        '<line x1="104" y1="128" x2="208" y2="128" stroke="{color}" stroke-linecap="round" '
        'stroke-linejoin="round" stroke-width="18"/>'
        "</svg>"
    ),
    "x": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">'
        '<line x1="200" y1="56" x2="56" y2="200" stroke="{color}" stroke-linecap="round" stroke-width="20"/>'
        '<line x1="200" y1="200" x2="56" y2="56" stroke="{color}" stroke-linecap="round" stroke-width="20"/>'
        "</svg>"
    ),
}


def get_icon(name: str, color: str = "#E2E8F0", size: int = 20) -> QIcon:
    """Render an inline SVG icon into a crisp QIcon."""
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
