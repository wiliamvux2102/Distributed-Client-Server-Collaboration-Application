"""Main window, split sign-in view, 4-zone workspace, and smooth animated transitions."""

from __future__ import annotations

from datetime import datetime
import math
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QColor,
    QDesktopServices,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QHideEvent,
    QInputMethodEvent,
    QKeyEvent,
    QKeySequence,
    QPainter,
    QPaintEvent,
    QPen,
    QRadialGradient,
    QShortcut,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from chat.client_chat import MAX_CHAT_MESSAGE_BYTES
from common.app_config import CONFIG, DEFAULT_DOWNLOADS_DIR
from ui.gui.controller import (
    ChatMessageEvent,
    ConnectionState,
    ConnectResultEvent,
    DeliveryStatusEvent,
    ErrorEvent,
    GuiSessionBridge,
    OnlineListEvent,
    SessionController,
    TransferSnapshotEvent,
)
from ui.gui.icons import get_icon
from ui.gui.models import (
    BROADCAST_CONV_ID,
    ChatSessionStore,
    MessageBubbleDelegate,
    MessageListModel,
    TransferRecord,
    format_bytes,
    initials_for,
)
from ui.gui.theme import PALETTE, avatar_color_for, build_stylesheet


class NetworkTopologyWidget(QWidget):
    """Lightweight animated LAN star-topology hero graphic for the Sign-In view."""

    _PEER_POINTS: tuple[tuple[float, float], ...] = (
        (80.0, 50.0),
        (90.0, 200.0),
        (400.0, 40.0),
        (410.0, 210.0),
        (240.0, 22.0),
        (240.0, 238.0),
    )
    _CENTER = (240.0, 130.0)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(210)
        self.setMaximumHeight(250)
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(33)  # ~30 FPS, paused automatically when hidden
        self._timer.timeout.connect(self._tick)

    def _tick(self) -> None:
        self._phase = (self._phase + 0.014) % 1.0
        self.update()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if not self._timer.isActive():
            self._timer.start()

    def hideEvent(self, event: QHideEvent) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: ARG002
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w = float(self.width())
        h = float(self.height())
        scale = min(w / 480.0, h / 260.0)
        ox = (w - 480.0 * scale) / 2.0
        oy = (h - 260.0 * scale) / 2.0

        def pt(x: float, y: float) -> QPointF:
            return QPointF(ox + x * scale, oy + y * scale)

        c_pt = pt(*self._CENTER)

        # Radial links
        line_pen = QPen(QColor(PALETTE["border_strong"]), 1.5)
        painter.setPen(line_pen)
        for px, py in self._PEER_POINTS:
            painter.drawLine(c_pt, pt(px, py))

        # Subtle traveling packet pulses along spokes
        accent = QColor(PALETTE["brand_primary"])
        painter.setPen(Qt.PenStyle.NoPen)
        for idx, (px, py) in enumerate(self._PEER_POINTS):
            t = (self._phase + idx * 0.17) % 1.0
            p_end = pt(px, py)
            # Alternate direction: odd outgoing, even incoming
            if idx % 2 == 0:
                qx = c_pt.x() + (p_end.x() - c_pt.x()) * t
                qy = c_pt.y() + (p_end.y() - c_pt.y()) * t
            else:
                qx = p_end.x() + (c_pt.x() - p_end.x()) * t
                qy = p_end.y() + (c_pt.y() - p_end.y()) * t
            alpha = int(220 * math.sin(t * math.pi))
            pulse_color = QColor(accent)
            pulse_color.setAlpha(max(0, alpha))
            painter.setBrush(pulse_color)
            r = 3.2 * scale
            painter.drawEllipse(QPointF(qx, qy), r, r)

        # Outer peer nodes
        peer_fill = QColor(PALETTE["bg_card"])
        peer_stroke = QPen(accent, 1.5)
        painter.setBrush(peer_fill)
        painter.setPen(peer_stroke)
        peer_r = 13.5 * scale
        for px, py in self._PEER_POINTS:
            painter.drawEllipse(pt(px, py), peer_r, peer_r)

        # Subtle breathing aura on central server node
        aura_r = (34.0 + 5.0 * math.sin(self._phase * 2 * math.pi)) * scale
        grad = QRadialGradient(c_pt, aura_r)
        c0 = QColor(accent)
        c0.setAlpha(55)
        c1 = QColor(accent)
        c1.setAlpha(0)
        grad.setColorAt(0.0, c0)
        grad.setColorAt(1.0, c1)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(grad)
        painter.drawEllipse(c_pt, aura_r, aura_r)

        # Central server hub
        hub_r = 28.0 * scale
        painter.setBrush(QColor(PALETTE["brand_soft"]))
        painter.setPen(QPen(accent, 2.0))
        painter.drawEllipse(c_pt, hub_r, hub_r)

        inner_r = 6.0 * scale
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(accent)
        painter.drawEllipse(c_pt, inner_r, inner_r)


class AvatarCircle(QWidget):
    """Painted circular pastel avatar with initials or '#' glyph and optional status dot."""

    def __init__(
        self,
        label: str = "?",
        bg_color: str = "#7DD3FC",
        text_color: str = "#0B1220",
        size: int = 36,
        status: str | None = None,  # "online" | "offline" | None
        ring_color_key: str = "bg_panel",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._label = label
        self._bg_color = bg_color
        self._text_color = text_color
        self._size = size
        self._status = status
        self._ring_color_key = ring_color_key
        self.setFixedSize(size + 4, size + 4)

    def set_avatar(
        self,
        label: str,
        bg_color: str,
        text_color: str | None = None,
        status: str | None = None,
    ) -> None:
        self._label = label
        self._bg_color = bg_color
        if text_color is not None:
            self._text_color = text_color
        self._status = status
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: ARG002
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        circle_rect = QRectF(1, 1, self._size, self._size)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self._bg_color))
        painter.drawEllipse(circle_rect)

        font = QFont(self.font())
        font.setWeight(QFont.Weight.DemiBold)
        font.setPointSizeF(max(8.5, self._size * 0.34))
        painter.setFont(font)
        painter.setPen(QColor(self._text_color))
        painter.drawText(circle_rect, Qt.AlignmentFlag.AlignCenter, self._label)

        if self._status is not None:
            dot_d = 11
            dot_x = self._size - dot_d + 2
            dot_y = self._size - dot_d + 2
            dot_color = (
                PALETTE["accent_online"] if self._status == "online" else PALETTE["text_muted"]
            )
            painter.setBrush(QColor(dot_color))
            painter.setPen(QPen(QColor(PALETTE[self._ring_color_key]), 2.0))
            painter.drawEllipse(QRectF(dot_x, dot_y, dot_d, dot_d))


class SidebarItemWidget(QWidget):
    """Sidebar conversation row with avatar, presence indicator, and unread badge."""

    def __init__(
        self,
        title: str,
        subtitle: str,
        avatar_text: str,
        avatar_bg: str,
        avatar_fg: str,
        status: str | None = None,
        unread_count: int = 0,
        is_channel: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(11)

        if is_channel:
            ch_circle = QFrame()
            ch_circle.setFixedSize(36, 36)
            ch_circle.setStyleSheet(
                f"background-color: {PALETTE['brand_soft']}; border-radius: 18px;"
            )
            ch_l = QVBoxLayout(ch_circle)
            ch_l.setContentsMargins(0, 0, 0, 0)
            ch_l.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ic = QLabel()
            ic.setPixmap(get_icon("hash", PALETTE["brand_soft_text"], 18).pixmap(18, 18))
            ch_l.addWidget(ic)
            layout.addWidget(ch_circle)
        else:
            avatar = AvatarCircle(
                label=avatar_text,
                bg_color=avatar_bg,
                text_color=avatar_fg,
                size=34,
                status=status,
            )
            layout.addWidget(avatar)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)
        title_lbl = QLabel(title)
        title_lbl.setTextFormat(Qt.TextFormat.PlainText)
        title_lbl.setStyleSheet(
            f"font-weight: 600; font-size: 9.5pt; color: {PALETTE['text_primary']};"
        )
        sub_lbl = QLabel(subtitle)
        sub_lbl.setTextFormat(Qt.TextFormat.PlainText)
        sub_lbl.setStyleSheet(f"font-size: 8.5pt; color: {PALETTE['text_muted']};")
        text_col.addWidget(title_lbl)
        text_col.addWidget(sub_lbl)
        layout.addLayout(text_col, 1)

        if unread_count > 0:
            badge_text = "99+" if unread_count > 99 else str(unread_count)
            badge = QLabel(badge_text)
            badge.setTextFormat(Qt.TextFormat.PlainText)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setMinimumWidth(22)
            badge.setFixedHeight(22)
            badge.setStyleSheet(
                f"background-color: {PALETTE['brand_primary']}; color: {PALETTE['on_primary']}; "
                f"border-radius: 11px; padding: 0 6px; font-size: 8.5pt; font-weight: 700;"
            )
            layout.addWidget(badge)


class ChatComposer(QPlainTextEdit):
    """Multi-line composer supporting Vietnamese IME and Enter-to-send."""

    send_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ComposerInput")
        self._ime_composing = False
        self.setPlaceholderText("Nhắn tin tới mọi người trong Chat chung...")
        self.setFixedHeight(42)
        self.setTabChangesFocus(True)

    def inputMethodEvent(self, event: QInputMethodEvent) -> None:
        self._ime_composing = bool(event.preeditString())
        super().inputMethodEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if (
            event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            and not self._ime_composing
        ):
            event.accept()
            self.send_requested.emit()
            return
        super().keyPressEvent(event)


class TransferItemCard(QFrame):
    """Compact transfer row inside the Right Inspector card with smooth progress interpolation."""

    def __init__(self, rec: TransferRecord, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TransferRow")
        self._record = rec
        self._bar_anim: QPropertyAnimation | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 10, 0, 10)
        layout.setSpacing(6)

        top_row = QHBoxLayout()
        top_row.setSpacing(8)
        self.title_label = QLabel()
        self.title_label.setTextFormat(Qt.TextFormat.PlainText)
        self.title_label.setWordWrap(True)
        self.title_label.setMinimumWidth(0)
        self.title_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

        self.pct_label = QLabel()
        self.pct_label.setTextFormat(Qt.TextFormat.PlainText)
        self.pct_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        top_row.addWidget(self.title_label, 1)
        top_row.addWidget(self.pct_label)
        layout.addLayout(top_row)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 100)
        layout.addWidget(self.progress)

        self.status_label = QLabel()
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.update_record(rec, animate=False)

    def refresh_theme(self) -> None:
        self.update_record(self._record, animate=False)

    def _animate_progress(self, target_pct: int) -> None:
        current_val = self.progress.value()
        if target_pct <= current_val or abs(target_pct - current_val) <= 1:
            if self._bar_anim is not None:
                self._bar_anim.stop()
            self.progress.setValue(target_pct)
            return
        if self._bar_anim is None:
            anim = QPropertyAnimation(self.progress, b"value", self)
            anim.setDuration(105)
            anim.setEasingCurve(QEasingCurve.Type.OutQuad)
            self._bar_anim = anim
        self._bar_anim.stop()
        self._bar_anim.setStartValue(current_val)
        self._bar_anim.setEndValue(target_pct)
        self._bar_anim.start()

    def update_record(self, rec: TransferRecord, animate: bool = True) -> None:
        self._record = rec
        self.setStyleSheet(
            f"QFrame#TransferRow {{ border-bottom: 1px solid {PALETTE['border_card']}; background: transparent; }}"
        )

        self.title_label.setText(rec.filename)
        self.title_label.setToolTip(rec.saved_path or rec.filename)
        self.title_label.setStyleSheet(
            f"font-weight: 500; font-size: 9.5pt; color: {PALETTE['text_primary']};"
        )

        pct = (
            int(min(100, (rec.current_bytes / rec.total_bytes) * 100))
            if rec.total_bytes > 0
            else (100 if rec.state == "COMPLETED" else 0)
        )
        if animate:
            self._animate_progress(pct)
        else:
            self.progress.setValue(pct)

        if rec.state == "COMPLETED":
            self.pct_label.setText("● Xong")
            self.pct_label.setStyleSheet(
                f"color: {PALETTE['brand_soft_text']}; font-size: 9pt; font-weight: 600;"
            )
        elif rec.state == "FAILED":
            self.pct_label.setText("Lỗi")
            self.pct_label.setStyleSheet(
                f"color: {PALETTE['accent_error_text']}; font-size: 9pt; font-weight: 600;"
            )
        else:
            self.pct_label.setText(f"{pct}%")
            self.pct_label.setStyleSheet(
                f"color: {PALETTE['text_secondary']}; font-size: 9pt; font-weight: 600;"
            )

        is_upload = rec.direction == "upload"
        peer_part = f"@{rec.peer}" if rec.peer else ""
        size_info = f"{format_bytes(rec.current_bytes)} / {format_bytes(rec.total_bytes)}"
        if rec.state == "INIT_SENT":
            self.status_label.setStyleSheet(f"color: {PALETTE['text_muted']}; font-size: 8.5pt;")
            self.status_label.setText(f"Chờ {peer_part or 'bên nhận'} chấp nhận  •  {size_info}")
        elif rec.state == "TRANSFERRING":
            self.status_label.setStyleSheet(f"color: {PALETTE['text_muted']}; font-size: 8.5pt;")
            self.status_label.setText(f"Đang gửi tới {peer_part}  •  {size_info}")
        elif rec.state == "AWAITING_VERIFICATION":
            self.status_label.setStyleSheet(f"color: {PALETTE['accent_warning']}; font-size: 8.5pt;")
            self.status_label.setText("Đã gửi 100%  •  Đang kiểm tra SHA-256...")
        elif rec.state == "RECEIVING":
            self.status_label.setStyleSheet(f"color: {PALETTE['text_muted']}; font-size: 8.5pt;")
            self.status_label.setText(f"Đang nhận từ {peer_part}  •  {size_info}")
        elif rec.state == "COMPLETED":
            self.status_label.setStyleSheet(f"color: {PALETTE['text_muted']}; font-size: 8.5pt;")
            verb = "Đã gửi tới" if is_upload else "Đã nhận từ"
            self.status_label.setText(f"{verb} {peer_part}  •  SHA-256 OK")
        elif rec.state == "FAILED":
            self.status_label.setStyleSheet(f"color: {PALETTE['accent_error_text']}; font-size: 8.5pt;")
            self.status_label.setText(rec.error or "Lỗi truyền file")


class MainWindow(QMainWindow):
    """Primary desktop window managing the split sign-in view and 4-zone workspace."""

    def __init__(self, controller: SessionController | None = None) -> None:
        super().__init__()
        self.setWindowTitle("CollabChat — Distributed TLS Chat & File Transfer")
        self.setMinimumSize(940, 620)
        self.resize(1320, 820)
        self.setAcceptDrops(True)

        self.bridge = GuiSessionBridge()
        self.controller = controller or SessionController(bridge=self.bridge)
        if controller is not None:
            self.bridge = controller.bridge
        self.store = ChatSessionStore()
        self._active_gen = 0
        self._current_username = ""
        self._theme_mode = "dark"
        self._transfer_cards: dict[str, TransferItemCard] = {}
        self._fade_anim: QPropertyAnimation | None = None
        self._fade_target: QWidget | None = None
        self._scroll_anim: QPropertyAnimation | None = None
        self._inspector_anim: QPropertyAnimation | None = None
        self._banner_anim: QPropertyAnimation | None = None

        self._status_reset_timer = QTimer(self)
        self._status_reset_timer.setSingleShot(True)
        self._status_reset_timer.timeout.connect(self._update_conv_header_meta)

        self._online_poll_timer = QTimer(self)
        self._online_poll_timer.setInterval(5000)
        self._online_poll_timer.timeout.connect(self.controller.refresh_online_users)

        self.setStyleSheet(build_stylesheet(self._theme_mode))
        self._build_ui()
        self._refresh_theme_icons()
        self._connect_signals()

    def _fade_widget_in(self, widget: QWidget, duration: int = 125) -> None:
        """Apply a snappy opacity fade-in and detach the graphics effect upon completion."""
        if not self.isVisible():
            return
        if self._fade_anim is not None:
            self._fade_anim.stop()
            self._fade_anim.deleteLater()
            if self._fade_target is not None and self._fade_target.graphicsEffect() is not None:
                self._fade_target.setGraphicsEffect(None)
        effect = QGraphicsOpacityEffect(widget)
        effect.setOpacity(0.25)
        widget.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", self)
        anim.setDuration(duration)
        anim.setStartValue(0.25)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        def _cleanup() -> None:
            if widget.graphicsEffect() is effect:
                widget.setGraphicsEffect(None)
            self._fade_target = None

        anim.finished.connect(_cleanup)
        self._fade_target = widget
        self._fade_anim = anim
        anim.start()

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("RootWindow")
        self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.stack = QStackedWidget()
        root_layout.addWidget(self.stack)

        self.login_page = self._build_login_page()
        self.workspace_page = self._build_workspace_page()
        self.stack.addWidget(self.login_page)
        self.stack.addWidget(self.workspace_page)
        self.stack.setCurrentWidget(self.login_page)

    def _build_login_page(self) -> QWidget:
        page = QWidget()
        split = QHBoxLayout(page)
        split.setContentsMargins(0, 0, 0, 0)
        split.setSpacing(0)

        # Left Brand Panel (50%)
        brand_panel = QFrame()
        brand_panel.setObjectName("BrandPanel")
        brand_layout = QVBoxLayout(brand_panel)
        brand_layout.setContentsMargins(52, 46, 52, 42)
        brand_layout.setSpacing(20)

        brand_top = QHBoxLayout()
        brand_top.setSpacing(12)
        logo_box = QFrame()
        logo_box.setObjectName("BrandMark")
        logo_box.setFixedSize(36, 36)
        lb_l = QVBoxLayout(logo_box)
        lb_l.setContentsMargins(0, 0, 0, 0)
        lb_l.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.login_logo_icon = QLabel()
        lb_l.addWidget(self.login_logo_icon)
        brand_title = QLabel("CollabChat")
        brand_title.setStyleSheet("font-size: 15pt; font-weight: 700;")
        brand_top.addWidget(logo_box)
        brand_top.addWidget(brand_title)
        brand_top.addStretch()
        brand_layout.addLayout(brand_top)

        brand_layout.addStretch(1)
        self.topology_canvas = NetworkTopologyWidget()
        brand_layout.addWidget(self.topology_canvas)
        brand_layout.addSpacing(12)

        hero_head = QLabel("Trò chuyện và chia sẻ tệp ngay trên mạng của bạn.")
        hero_head.setObjectName("HeroTitle")
        hero_head.setWordWrap(True)
        brand_layout.addWidget(hero_head)
        brand_layout.addSpacing(12)

        self._feature_icon_labels: list[tuple[QLabel, str]] = []
        for icon_name, desc in (
            ("wifi", "Tự động tìm máy chủ và đồng bộ peer qua UDP"),
            ("file", "Truyền tệp phân mảnh qua TCP & kiểm tra SHA-256"),
            ("lock", "Mã hóa toàn bộ kênh điều khiển bằng TLS"),
        ):
            row = QHBoxLayout()
            row.setSpacing(12)
            box = QFrame()
            box.setObjectName("FeatureIconBox")
            box.setFixedSize(36, 36)
            bl = QVBoxLayout(box)
            bl.setContentsMargins(0, 0, 0, 0)
            bl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ic_lbl = QLabel()
            self._feature_icon_labels.append((ic_lbl, icon_name))
            bl.addWidget(ic_lbl)
            txt = QLabel(desc)
            txt.setObjectName("SecondaryLabel")
            row.addWidget(box)
            row.addWidget(txt, 1)
            brand_layout.addLayout(row)

        brand_layout.addStretch(1)
        brand_foot = QLabel("Self-hosted  •  Mạng LAN nội bộ  •  Không phụ thuộc bên thứ ba")
        brand_foot.setObjectName("MutedLabel")
        brand_layout.addWidget(brand_foot)

        # Right Sign-In Form Panel (50%)
        form_panel = QWidget()
        form_panel.setObjectName("LoginFormPanel")
        fp_outer = QVBoxLayout(form_panel)
        fp_outer.setContentsMargins(32, 24, 32, 32)

        top_right = QHBoxLayout()
        top_right.addStretch()
        self.login_theme_btn = QPushButton("Sáng")
        self.login_theme_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.login_theme_btn.setToolTip("Chuyển giao diện Sáng / Tối")
        top_right.addWidget(self.login_theme_btn)
        fp_outer.addLayout(top_right)

        fp_center = QVBoxLayout()
        fp_center.setAlignment(Qt.AlignmentFlag.AlignCenter)

        fc = QWidget()
        fc.setMaximumWidth(410)
        fc.setMinimumWidth(360)
        fc_layout = QVBoxLayout(fc)
        fc_layout.setContentsMargins(0, 0, 0, 0)
        fc_layout.setSpacing(16)

        sign_title = QLabel("Đăng nhập")
        sign_title.setObjectName("SignInTitle")
        sign_sub = QLabel("Kết nối tới máy chủ CollabChat trong mạng của bạn.")
        sign_sub.setObjectName("MutedLabel")
        sign_sub.setWordWrap(True)
        head_blk = QVBoxLayout()
        head_blk.setSpacing(4)
        head_blk.addWidget(sign_title)
        head_blk.addWidget(sign_sub)
        fc_layout.addLayout(head_blk)

        u_col = QVBoxLayout()
        u_col.setSpacing(7)
        u_lbl = QLabel("Tên người dùng")
        u_lbl.setObjectName("FieldLabel")
        self.username_input = QLineEdit()
        self.username_input.setPlaceholderText("Ví dụ: minh.nguyen hoặc alice")
        self.username_input.setFixedHeight(46)
        u_col.addWidget(u_lbl)
        u_col.addWidget(self.username_input)
        fc_layout.addLayout(u_col)

        # Discovery mode selector
        mode_card = QFrame()
        mode_card.setObjectName("ModeCard")
        mc_layout = QVBoxLayout(mode_card)
        mc_layout.setContentsMargins(14, 12, 14, 14)
        mc_layout.setSpacing(10)

        self.auto_radio = QRadioButton("Auto Discovery (UDP Broadcast)")
        self.auto_radio.setChecked(True)
        self.manual_radio = QRadioButton("Nhập thủ công địa chỉ Server")
        mc_layout.addWidget(self.auto_radio)
        mc_layout.addWidget(self.manual_radio)

        addr_row = QHBoxLayout()
        addr_row.setSpacing(12)

        host_col = QVBoxLayout()
        host_col.setSpacing(6)
        host_lbl = QLabel("Địa chỉ Server")
        host_lbl.setObjectName("FieldLabel")
        self.host_input = QLineEdit()
        self.host_input.setPlaceholderText("127.0.0.1")
        self.host_input.setFixedHeight(44)
        self.host_input.setEnabled(False)
        host_col.addWidget(host_lbl)
        host_col.addWidget(self.host_input)

        port_col = QVBoxLayout()
        port_col.setSpacing(6)
        port_lbl = QLabel("Port")
        port_lbl.setObjectName("FieldLabel")
        self.port_input = QLineEdit()
        self.port_input.setPlaceholderText(str(CONFIG["server"]["tcp_port"]))
        self.port_input.setFixedSize(110, 44)
        self.port_input.setEnabled(False)
        port_col.addWidget(port_lbl)
        port_col.addWidget(self.port_input)

        addr_row.addLayout(host_col, 1)
        addr_row.addLayout(port_col)
        mc_layout.addLayout(addr_row)
        fc_layout.addWidget(mode_card)

        self.login_error_label = QLabel()
        self.login_error_label.setTextFormat(Qt.TextFormat.PlainText)
        self.login_error_label.setWordWrap(True)
        self.login_error_label.hide()
        fc_layout.addWidget(self.login_error_label)

        self.connect_button = QPushButton("Kết nối")
        self.connect_button.setObjectName("PrimaryButton")
        self.connect_button.setFixedHeight(48)
        self.connect_button.setCursor(Qt.CursorShape.PointingHandCursor)
        fc_layout.addWidget(self.connect_button)

        sec_row = QHBoxLayout()
        sec_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sec_row.setSpacing(6)
        self.login_lock_icon = QLabel()
        sec_txt = QLabel("Mã hóa TLS  •  Kiểm tra toàn vẹn SHA-256")
        sec_txt.setObjectName("MutedLabel")
        sec_row.addWidget(self.login_lock_icon)
        sec_row.addWidget(sec_txt)
        fc_layout.addLayout(sec_row)

        fp_center.addWidget(fc)
        fp_outer.addLayout(fp_center, 1)

        split.addWidget(brand_panel, 1)
        split.addWidget(form_panel, 1)
        return page

    def _build_workspace_page(self) -> QWidget:
        page = QWidget()
        outer = QHBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Zone 1: Left Icon Rail (72px)
        rail = QFrame()
        rail.setObjectName("NavRail")
        rail.setFixedWidth(72)
        rail_layout = QVBoxLayout(rail)
        rail_layout.setContentsMargins(0, 16, 0, 16)
        rail_layout.setSpacing(8)
        rail_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        rail_logo = QFrame()
        rail_logo.setObjectName("BrandMark")
        rail_logo.setFixedSize(44, 44)
        rl_l = QVBoxLayout(rail_logo)
        rl_l.setContentsMargins(0, 0, 0, 0)
        rl_l.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.rail_logo_icon = QLabel()
        rl_l.addWidget(self.rail_logo_icon)
        rail_layout.addWidget(rail_logo)
        rail_layout.addSpacing(10)

        self.rail_chats_btn = QPushButton()
        self.rail_chats_btn.setObjectName("RailButton")
        self.rail_chats_btn.setCheckable(True)
        self.rail_chats_btn.setChecked(True)
        self.rail_chats_btn.setFixedSize(44, 44)
        self.rail_chats_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.rail_chats_btn.setToolTip("Cuộc trò chuyện")

        self.rail_peers_btn = QPushButton()
        self.rail_peers_btn.setObjectName("RailButton")
        self.rail_peers_btn.setFixedSize(44, 44)
        self.rail_peers_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.rail_peers_btn.setToolTip("Làm mới & Tìm người dùng online")

        self.rail_files_btn = QPushButton()
        self.rail_files_btn.setObjectName("RailButton")
        self.rail_files_btn.setCheckable(True)
        self.rail_files_btn.setChecked(True)
        self.rail_files_btn.setFixedSize(44, 44)
        self.rail_files_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.rail_files_btn.setToolTip("Ẩn / Hiện bảng Kết nối & Truyền file")

        rail_layout.addWidget(self.rail_chats_btn)
        rail_layout.addWidget(self.rail_peers_btn)
        rail_layout.addWidget(self.rail_files_btn)
        rail_layout.addStretch()

        self.workspace_theme_btn = QPushButton()
        self.workspace_theme_btn.setObjectName("RailButton")
        self.workspace_theme_btn.setFixedSize(44, 44)
        self.workspace_theme_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.workspace_theme_btn.setToolTip("Chuyển giao diện Sáng / Tối")

        self.disconnect_button = QPushButton()
        self.disconnect_button.setObjectName("DangerIconButton")
        self.disconnect_button.setFixedSize(44, 44)
        self.disconnect_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.disconnect_button.setToolTip("Ngắt kết nối & Đăng xuất")

        rail_layout.addWidget(self.workspace_theme_btn)
        rail_layout.addWidget(self.disconnect_button)
        outer.addWidget(rail)

        # Splitter for Zone 2 (Sidebar) | Zone 3 (Main Chat) | Zone 4 (Inspector)
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.setChildrenCollapsible(False)
        outer.addWidget(self.main_splitter, 1)

        # Zone 2: Sidebar (280-320px)
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setMinimumWidth(250)
        sidebar.setMaximumWidth(330)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(0)

        side_head = QLabel("CollabChat")
        side_head.setObjectName("SidebarTitle")
        side_head.setContentsMargins(20, 20, 20, 12)
        side_layout.addWidget(side_head)

        self.search_box = QFrame()
        self.search_box.setObjectName("SearchContainer")
        self.search_box.setFixedHeight(40)
        sb_wrap = QHBoxLayout()
        sb_wrap.setContentsMargins(16, 0, 16, 8)
        sb_layout = QHBoxLayout(self.search_box)
        sb_layout.setContentsMargins(12, 0, 10, 0)
        sb_layout.setSpacing(8)
        self.search_icon_label = QLabel()
        self.peer_search_input = QLineEdit()
        self.peer_search_input.setObjectName("SearchInput")
        self.peer_search_input.setPlaceholderText("Tìm người dùng...")
        self.peer_search_input.setClearButtonEnabled(True)
        sb_layout.addWidget(self.search_icon_label)
        sb_layout.addWidget(self.peer_search_input, 1)
        sb_wrap.addWidget(self.search_box)
        side_layout.addLayout(sb_wrap)

        ch_sec = QLabel("KÊNH CHAT")
        ch_sec.setObjectName("SectionHeader")
        ch_sec.setContentsMargins(20, 10, 20, 6)
        side_layout.addWidget(ch_sec)

        self.channel_list = QListWidget()
        self.channel_list.setObjectName("SidebarList")
        self.channel_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.channel_list.setFixedHeight(60)
        side_layout.addWidget(self.channel_list)

        online_row = QHBoxLayout()
        online_row.setContentsMargins(20, 10, 14, 4)
        self.online_sec_label = QLabel("TRỰC TUYẾN · 0")
        self.online_sec_label.setObjectName("SectionHeader")
        self.refresh_button = QPushButton()
        self.refresh_button.setObjectName("IconButton")
        self.refresh_button.setToolTip("Làm mới danh sách online (LIST_ONLINE)")
        self.refresh_button.setFixedSize(28, 28)
        self.refresh_button.setCursor(Qt.CursorShape.PointingHandCursor)
        online_row.addWidget(self.online_sec_label)
        online_row.addStretch()
        online_row.addWidget(self.refresh_button)
        side_layout.addLayout(online_row)

        self.online_updated_label = QLabel("Cập nhật: Chưa tải")
        self.online_updated_label.setObjectName("MutedLabel")
        self.online_updated_label.setContentsMargins(20, 0, 20, 6)
        side_layout.addWidget(self.online_updated_label)

        self.peer_list = QListWidget()
        self.peer_list.setObjectName("SidebarList")
        self.peer_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        side_layout.addWidget(self.peer_list, 1)

        # Sidebar bottom profile card
        me_footer = QFrame()
        me_footer.setObjectName("SidebarFooter")
        me_layout = QHBoxLayout(me_footer)
        me_layout.setContentsMargins(16, 12, 16, 12)
        me_layout.setSpacing(12)
        self.me_avatar = AvatarCircle(
            label="?", bg_color="#FDE68A", text_color="#0B1220", size=34, status="online"
        )
        me_text = QVBoxLayout()
        me_text.setSpacing(1)
        self.me_name_label = QLabel("@user")
        self.me_name_label.setTextFormat(Qt.TextFormat.PlainText)
        self.me_name_label.setStyleSheet("font-weight: 600; font-size: 9.5pt;")
        self.me_endpoint_label = QLabel("127.0.0.1:5000")
        self.me_endpoint_label.setObjectName("MutedLabel")
        self.me_endpoint_label.setTextFormat(Qt.TextFormat.PlainText)
        me_text.addWidget(self.me_name_label)
        me_text.addWidget(self.me_endpoint_label)
        me_layout.addWidget(self.me_avatar)
        me_layout.addLayout(me_text, 1)
        side_layout.addWidget(me_footer)

        self.main_splitter.addWidget(sidebar)

        # Zone 3: Center Main Chat Area
        chat_panel = QWidget()
        chat_panel.setMinimumWidth(420)
        chat_layout = QVBoxLayout(chat_panel)
        chat_layout.setContentsMargins(0, 0, 0, 0)
        chat_layout.setSpacing(0)

        # Header (72px)
        chat_header = QFrame()
        chat_header.setObjectName("ChatHeader")
        chat_header.setFixedHeight(72)
        ch_layout = QHBoxLayout(chat_header)
        ch_layout.setContentsMargins(28, 0, 24, 0)
        ch_layout.setSpacing(10)

        title_wrap = QWidget()
        title_wrap.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        title_col = QVBoxLayout(title_wrap)
        title_col.setContentsMargins(0, 0, 0, 0)
        title_col.setSpacing(2)
        self.conv_title_label = QLabel("Global chat (#all)")
        self.conv_title_label.setTextFormat(Qt.TextFormat.PlainText)
        self.conv_title_label.setStyleSheet("font-size: 13.5pt; font-weight: 700;")
        self.global_status_label = QLabel("Kênh chung")
        self.global_status_label.setObjectName("MutedLabel")
        self.global_status_label.setTextFormat(Qt.TextFormat.PlainText)
        title_col.addWidget(self.conv_title_label)
        title_col.addWidget(self.global_status_label)

        self.endpoint_chip = QLabel("127.0.0.1:5000")
        self.endpoint_chip.setObjectName("NeutralChip")
        self.endpoint_chip.setTextFormat(Qt.TextFormat.PlainText)

        self.tls_chip = QLabel("TLS Verified")
        self.tls_chip.setObjectName("TlsChip")
        self.tls_chip.setTextFormat(Qt.TextFormat.PlainText)

        self.header_send_file_btn = QPushButton("Gửi file")
        self.header_send_file_btn.setObjectName("SoftActionButton")
        self.header_send_file_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.header_send_file_btn.setToolTip("Gửi file tới cuộc trò chuyện hiện tại")

        self.toggle_inspector_btn = QPushButton()
        self.toggle_inspector_btn.setObjectName("IconButton")
        self.toggle_inspector_btn.setCheckable(True)
        self.toggle_inspector_btn.setChecked(True)
        self.toggle_inspector_btn.setFixedSize(34, 34)
        self.toggle_inspector_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_inspector_btn.setToolTip("Ẩn / Hiện bảng bên phải")

        ch_layout.addWidget(title_wrap, 1, Qt.AlignmentFlag.AlignVCenter)
        ch_layout.addWidget(self.endpoint_chip, 0, Qt.AlignmentFlag.AlignVCenter)
        ch_layout.addWidget(self.tls_chip, 0, Qt.AlignmentFlag.AlignVCenter)
        ch_layout.addWidget(self.header_send_file_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        ch_layout.addWidget(self.toggle_inspector_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        chat_layout.addWidget(chat_header)

        # Smoothly Animated Error Banner
        self.error_banner = QFrame()
        self.error_banner.setObjectName("ErrorBanner")
        eb_layout = QHBoxLayout(self.error_banner)
        eb_layout.setContentsMargins(24, 9, 16, 9)
        eb_layout.setSpacing(10)
        self.error_banner_icon = QLabel()
        self.error_banner_label = QLabel()
        self.error_banner_label.setTextFormat(Qt.TextFormat.PlainText)
        self.error_banner_label.setWordWrap(True)
        self.eb_close = QPushButton()
        self.eb_close.setObjectName("IconButton")
        self.eb_close.setFixedSize(26, 26)
        self.eb_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.eb_close.clicked.connect(self._hide_error_banner_animated)
        eb_layout.addWidget(self.error_banner_icon)
        eb_layout.addWidget(self.error_banner_label, 1)
        eb_layout.addWidget(self.eb_close)
        self.error_banner.hide()
        chat_layout.addWidget(self.error_banner)

        # Message Feed / Empty State
        self.feed_stack = QStackedWidget()
        empty_widget = QWidget()
        empty_layout = QVBoxLayout(empty_widget)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        empty_card = QFrame()
        empty_card.setObjectName("EmptyCard")
        empty_card.setMaximumWidth(380)
        ec_layout = QVBoxLayout(empty_card)
        ec_layout.setContentsMargins(28, 26, 28, 26)
        ec_layout.setSpacing(10)
        ec_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.empty_icon_label = QLabel()
        self.empty_icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_title = QLabel("Chưa có tin nhắn nào")
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_title.setStyleSheet("font-size: 11.5pt; font-weight: 700;")
        self.empty_feed_label = QLabel(
            "Hãy bắt đầu cuộc trò chuyện bằng tin nhắn bên dưới hoặc kéo-thả tệp để chia sẻ qua TLS."
        )
        self.empty_feed_label.setObjectName("MutedLabel")
        self.empty_feed_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_feed_label.setWordWrap(True)
        ec_layout.addWidget(self.empty_icon_label)
        ec_layout.addWidget(empty_title)
        ec_layout.addWidget(self.empty_feed_label)
        empty_layout.addWidget(empty_card)

        self.message_model = MessageListModel(self)
        self.message_delegate = MessageBubbleDelegate(self)
        self.message_view = QListView()
        self.message_view.setObjectName("MessageFeed")
        self.message_view.setModel(self.message_model)
        self.message_view.setItemDelegate(self.message_delegate)
        self.message_view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.message_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.message_view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.message_view.setResizeMode(QListView.ResizeMode.Adjust)
        self.message_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.message_view.setSpacing(2)

        self.feed_stack.addWidget(empty_widget)
        self.feed_stack.addWidget(self.message_view)
        chat_layout.addWidget(self.feed_stack, 1)

        # Composer bar
        composer_wrapper = QFrame()
        composer_wrapper.setObjectName("ComposerWrapper")
        cw_layout = QVBoxLayout(composer_wrapper)
        cw_layout.setContentsMargins(28, 10, 28, 18)
        cw_layout.setSpacing(6)

        self.composer_box = QFrame()
        self.composer_box.setObjectName("ComposerBox")
        comp_layout = QHBoxLayout(self.composer_box)
        comp_layout.setContentsMargins(10, 8, 10, 8)
        comp_layout.setSpacing(8)

        self.attach_button = QPushButton()
        self.attach_button.setObjectName("IconButton")
        self.attach_button.setFixedSize(40, 40)
        self.attach_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.attach_button.setToolTip("Đính kèm file gửi tới cuộc trò chuyện hiện tại")

        self.composer = ChatComposer()

        self.send_button = QPushButton()
        self.send_button.setObjectName("SendButton")
        self.send_button.setFixedSize(40, 40)
        self.send_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_button.setToolTip("Gửi tin nhắn (Enter)")

        comp_layout.addWidget(self.attach_button, 0, Qt.AlignmentFlag.AlignVCenter)
        comp_layout.addWidget(self.composer, 1)
        comp_layout.addWidget(self.send_button, 0, Qt.AlignmentFlag.AlignVCenter)
        cw_layout.addWidget(self.composer_box)

        meta_row = QHBoxLayout()
        meta_row.setContentsMargins(6, 0, 6, 0)
        hint_lbl = QLabel("Enter để gửi  •  Shift+Enter xuống dòng  •  Kéo-thả tệp vào khung chat")
        hint_lbl.setObjectName("MutedLabel")
        self.byte_counter_label = QLabel(f"0 / {MAX_CHAT_MESSAGE_BYTES} B")
        self.byte_counter_label.setObjectName("MutedLabel")
        meta_row.addWidget(hint_lbl)
        meta_row.addStretch()
        meta_row.addWidget(self.byte_counter_label)
        cw_layout.addLayout(meta_row)

        chat_layout.addWidget(composer_wrapper)
        self.main_splitter.addWidget(chat_panel)

        # Zone 4: Right Inspector Panel (Connection + Transfers)
        self.inspector_panel = QFrame()
        self.inspector_panel.setObjectName("InspectorPanel")
        self.inspector_panel.setMinimumWidth(0)
        self.inspector_panel.setMaximumWidth(328)
        self.inspector_panel.resize(328, 700)
        insp_layout = QVBoxLayout(self.inspector_panel)
        insp_layout.setContentsMargins(20, 22, 20, 20)
        insp_layout.setSpacing(20)

        # Card 1: Connection Details
        conn_block = QVBoxLayout()
        conn_block.setSpacing(10)
        conn_header = QLabel("KẾT NỐI")
        conn_header.setObjectName("SectionHeader")
        conn_block.addWidget(conn_header)

        conn_card = QFrame()
        conn_card.setObjectName("InfoCard")
        cc_layout = QVBoxLayout(conn_card)
        cc_layout.setContentsMargins(16, 6, 16, 6)
        cc_layout.setSpacing(0)

        self._conn_kv_rows: list[QFrame] = []
        self.kv_server_val = self._add_kv_row(cc_layout, "Server", "—")
        self.kv_tcp_val = self._add_kv_row(cc_layout, "Phiên TCP", "● Đã kết nối", is_ok=True)
        hb_sec = CONFIG["heartbeat"]["interval_seconds"]
        self.kv_udp_val = self._add_kv_row(cc_layout, "UDP heartbeat", f"● Mỗi {hb_sec} giây", is_ok=True)
        self.kv_tls_val = self._add_kv_row(cc_layout, "Giao thức TLS", "TLS Verified")
        self.kv_cipher_val = self._add_kv_row(cc_layout, "Mã hóa", "AES-256", last=True)

        conn_block.addWidget(conn_card)
        insp_layout.addLayout(conn_block)

        # Card 2: Transfers
        tr_block = QVBoxLayout()
        tr_block.setSpacing(10)
        tr_top = QHBoxLayout()
        tr_header = QLabel("TRUYỀN FILE")
        tr_header.setObjectName("SectionHeader")
        self.transfer_count_lbl = QLabel("0")
        self.transfer_count_lbl.setObjectName("MutedLabel")
        tr_top.addWidget(tr_header)
        tr_top.addStretch()
        tr_top.addWidget(self.transfer_count_lbl)
        tr_block.addLayout(tr_top)

        self.transfers_card = QFrame()
        self.transfers_card.setObjectName("InfoCard")
        tc_layout = QVBoxLayout(self.transfers_card)
        tc_layout.setContentsMargins(14, 10, 14, 10)
        tc_layout.setSpacing(0)

        self.transfer_empty_label = QLabel("Chưa có phiên truyền file nào.")
        self.transfer_empty_label.setObjectName("MutedLabel")
        self.transfer_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.transfer_empty_label.setContentsMargins(0, 12, 0, 12)
        tc_layout.addWidget(self.transfer_empty_label)

        self.transfer_scroll = QScrollArea()
        self.transfer_scroll.setWidgetResizable(True)
        self.transfer_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.transfer_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.transfer_scroll.setStyleSheet("background: transparent;")
        self.transfer_container = QWidget()
        self.transfer_container.setStyleSheet("background: transparent;")
        self.transfer_cards_layout = QVBoxLayout(self.transfer_container)
        self.transfer_cards_layout.setContentsMargins(0, 0, 2, 0)
        self.transfer_cards_layout.setSpacing(0)
        self.transfer_cards_layout.addStretch()
        self.transfer_scroll.setWidget(self.transfer_container)
        self.transfer_scroll.hide()
        tc_layout.addWidget(self.transfer_scroll)

        tr_block.addWidget(self.transfers_card, 1)

        self.open_downloads_btn = QPushButton("Mở thư mục downloads/")
        self.open_downloads_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.open_downloads_btn.setToolTip("Mở thư mục lưu tệp nhận về")
        tr_block.addWidget(self.open_downloads_btn)

        insp_layout.addLayout(tr_block, 1)

        self.main_splitter.addWidget(self.inspector_panel)
        self.main_splitter.setSizes([278, 640, 328])
        return page

    def _add_kv_row(
        self,
        parent_layout: QVBoxLayout,
        key: str,
        val: str,
        is_ok: bool = False,
        last: bool = False,
    ) -> QLabel:
        row_frame = QFrame()
        row_frame.setProperty("kv_last", last)
        self._conn_kv_rows.append(row_frame)
        row_l = QHBoxLayout(row_frame)
        row_l.setContentsMargins(0, 10, 0, 10)
        row_l.setSpacing(8)
        k_lbl = QLabel(key)
        k_lbl.setObjectName("MutedLabel")
        v_lbl = QLabel(val)
        v_lbl.setTextFormat(Qt.TextFormat.PlainText)
        v_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        v_lbl.setProperty("kv_ok", is_ok)
        row_l.addWidget(k_lbl)
        row_l.addStretch()
        row_l.addWidget(v_lbl)
        parent_layout.addWidget(row_frame)
        return v_lbl

    def _connect_signals(self) -> None:
        self.login_theme_btn.clicked.connect(self._toggle_theme)
        self.workspace_theme_btn.clicked.connect(self._toggle_theme)
        self.rail_chats_btn.clicked.connect(self._on_rail_chats)
        self.rail_peers_btn.clicked.connect(self._on_rail_peers)
        self.rail_files_btn.clicked.connect(lambda checked: self._set_inspector_open(checked))
        self.toggle_inspector_btn.clicked.connect(lambda checked: self._set_inspector_open(checked))
        self.open_downloads_btn.clicked.connect(self._on_open_downloads)

        self.auto_radio.toggled.connect(self._on_discovery_mode_changed)
        self.username_input.returnPressed.connect(self._on_connect_clicked)
        self.host_input.returnPressed.connect(self._on_connect_clicked)
        self.port_input.returnPressed.connect(self._on_connect_clicked)
        self.connect_button.clicked.connect(self._on_connect_clicked)

        self.disconnect_button.clicked.connect(lambda: self.controller.disconnect())
        self.refresh_button.clicked.connect(self.controller.refresh_online_users)
        self.peer_search_input.textChanged.connect(lambda _txt: self._refresh_sidebar())
        self.channel_list.itemClicked.connect(self._on_channel_clicked)
        self.peer_list.itemClicked.connect(self._on_peer_clicked)

        self.composer.textChanged.connect(self._update_byte_counter)
        self.composer.send_requested.connect(self._on_send_clicked)
        self.send_button.clicked.connect(self._on_send_clicked)
        self.attach_button.clicked.connect(self._on_attach_clicked)
        self.header_send_file_btn.clicked.connect(self._on_attach_clicked)
        self.message_delegate.open_file_requested.connect(self._on_open_specific_file)

        self.message_view.customContextMenuRequested.connect(self._on_message_context_menu)
        copy_shortcut = QShortcut(QKeySequence.StandardKey.Copy, self.message_view)
        copy_shortcut.activated.connect(self._copy_selected_message)

        # Bridge signals
        self.bridge.state_changed.connect(self._on_state_changed)
        self.bridge.connected.connect(self._on_connected)
        self.bridge.connect_failed.connect(self._on_connect_failed)
        self.bridge.online_list_received.connect(self._on_online_list)
        self.bridge.message_received.connect(self._on_message_received)
        self.bridge.delivery_status_received.connect(self._on_delivery_status)
        self.bridge.transfer_event_received.connect(self._on_transfer_event)
        self.bridge.server_error_received.connect(self._on_server_error)
        self.bridge.disconnected.connect(self._on_disconnected)

        self.peer_search_input.installEventFilter(self)
        self.composer.installEventFilter(self)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        etype = event.type()
        if etype in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            focused = etype == QEvent.Type.FocusIn
            if obj is getattr(self, "composer", None):
                self.composer_box.setProperty("focused", focused)
                self.composer_box.style().unpolish(self.composer_box)
                self.composer_box.style().polish(self.composer_box)
            elif obj is getattr(self, "peer_search_input", None):
                self.search_box.setProperty("focused", focused)
                self.search_box.style().unpolish(self.search_box)
                self.search_box.style().polish(self.search_box)
        return super().eventFilter(obj, event)

    def _on_rail_chats(self) -> None:
        self.rail_chats_btn.setChecked(True)
        self.composer.setFocus()

    def _on_rail_peers(self) -> None:
        self.controller.refresh_online_users()
        self.peer_search_input.setFocus()

    def _set_inspector_open(self, open_panel: bool) -> None:
        self.rail_files_btn.blockSignals(True)
        self.toggle_inspector_btn.blockSignals(True)
        self.rail_files_btn.setChecked(open_panel)
        self.toggle_inspector_btn.setChecked(open_panel)
        self.rail_files_btn.blockSignals(False)
        self.toggle_inspector_btn.blockSignals(False)

        if self._inspector_anim is not None:
            self._inspector_anim.stop()

        if not self.isVisible():
            self.inspector_panel.setMaximumWidth(328 if open_panel else 0)
            self.inspector_panel.setVisible(open_panel)
            return

        if self._inspector_anim is None:
            anim = QPropertyAnimation(self.inspector_panel, b"maximumWidth", self)
            anim.setDuration(150)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)

            def _on_insp_done() -> None:
                if self.inspector_panel.maximumWidth() == 0:
                    self.inspector_panel.setVisible(False)

            anim.finished.connect(_on_insp_done)
            self._inspector_anim = anim

        if open_panel:
            self.inspector_panel.setVisible(True)
            start_w = self.inspector_panel.width()
            end_w = 328
        else:
            start_w = self.inspector_panel.width()
            end_w = 0

        self._inspector_anim.stop()
        self._inspector_anim.setStartValue(start_w)
        self._inspector_anim.setEndValue(end_w)
        self._inspector_anim.start()

    def _toggle_theme(self) -> None:
        self._theme_mode = "light" if self._theme_mode == "dark" else "dark"
        self.setStyleSheet(build_stylesheet(self._theme_mode))
        self._refresh_theme_icons()
        for card in self._transfer_cards.values():
            card.refresh_theme()
        self._refresh_sidebar()
        self._update_byte_counter()
        self.topology_canvas.update()
        self.message_view.viewport().update()

    def _refresh_theme_icons(self) -> None:
        is_dark = self._theme_mode == "dark"
        next_label = "Giao diện Sáng" if is_dark else "Giao diện Tối"
        next_icon = "sun" if is_dark else "moon"

        self.login_theme_btn.setText(next_label)
        self.login_theme_btn.setIcon(get_icon(next_icon, PALETTE["text_secondary"], 16))
        self.workspace_theme_btn.setIcon(get_icon(next_icon, PALETTE["text_muted"], 20))

        self.login_logo_icon.setPixmap(get_icon("chat", PALETTE["on_primary"], 18).pixmap(18, 18))
        self.rail_logo_icon.setPixmap(get_icon("chat", PALETTE["on_primary"], 22).pixmap(22, 22))
        self.login_lock_icon.setPixmap(get_icon("lock", PALETTE["text_muted"], 14).pixmap(14, 14))
        for lbl, icon_name in self._feature_icon_labels:
            lbl.setPixmap(get_icon(icon_name, PALETTE["brand_soft_text"], 18).pixmap(18, 18))

        self.rail_chats_btn.setIcon(get_icon("chat", PALETTE["brand_soft_text"], 20))
        self.rail_peers_btn.setIcon(get_icon("users", PALETTE["text_muted"], 20))
        self.rail_files_btn.setIcon(get_icon("file", PALETTE["brand_soft_text"], 20))
        self.disconnect_button.setIcon(get_icon("logout", PALETTE["accent_error_text"], 20))

        self.search_icon_label.setPixmap(get_icon("search", PALETTE["text_muted"], 16).pixmap(16, 16))
        self.refresh_button.setIcon(get_icon("refresh", PALETTE["text_muted"], 16))
        self.toggle_inspector_btn.setIcon(get_icon("panel-right", PALETTE["text_secondary"], 18))
        self.header_send_file_btn.setIcon(get_icon("paperclip", PALETTE["brand_soft_text"], 15))
        self.attach_button.setIcon(get_icon("paperclip", PALETTE["text_muted"], 20))
        self.send_button.setIcon(get_icon("send", PALETTE["on_primary"], 18))
        self.open_downloads_btn.setIcon(get_icon("folder", PALETTE["text_secondary"], 16))

        self.eb_close.setIcon(get_icon("x", PALETTE["accent_error_text"], 14))
        self.error_banner_icon.setPixmap(
            get_icon("alert", PALETTE["accent_error_text"], 18).pixmap(18, 18)
        )
        self.error_banner_label.setStyleSheet(
            f"color: {PALETTE['accent_error_text']}; font-weight: 500;"
        )
        self.login_error_label.setStyleSheet(
            f"color: {PALETTE['accent_error_text']}; background-color: {PALETTE['accent_error_bg']}; "
            f"border: 1px solid {PALETTE['accent_error_border']}; border-radius: 10px; padding: 10px 12px;"
        )
        self.empty_icon_label.setPixmap(
            get_icon("chat", PALETTE["text_muted"], 40).pixmap(40, 40)
        )

        # Style Connection KV rows
        for row_frame in self._conn_kv_rows:
            is_last = bool(row_frame.property("kv_last"))
            border_css = "none" if is_last else f"1px solid {PALETTE['border_card']}"
            row_frame.setStyleSheet(f"border-bottom: {border_css}; background: transparent;")
        for val_lbl in (
            self.kv_server_val,
            self.kv_tcp_val,
            self.kv_udp_val,
            self.kv_tls_val,
            self.kv_cipher_val,
        ):
            if val_lbl.property("kv_ok"):
                val_lbl.setStyleSheet(
                    f"color: {PALETTE['brand_soft_text']}; font-weight: 600; font-size: 9.5pt;"
                )
            elif val_lbl is self.kv_cipher_val:
                val_lbl.setStyleSheet(
                    f"color: {PALETTE['text_primary']}; font-weight: 500; font-size: 8.5pt;"
                )
            else:
                val_lbl.setStyleSheet(
                    f"color: {PALETTE['text_primary']}; font-weight: 500; font-size: 9.5pt;"
                )

    def _on_open_downloads(self) -> None:
        dl_dir = DEFAULT_DOWNLOADS_DIR.resolve()
        dl_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(dl_dir)))

    def _on_open_specific_file(self, path_str: str) -> None:
        target = Path(path_str).resolve()
        if target.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
        else:
            self._on_open_downloads()

    def _update_byte_counter(self) -> None:
        raw = self.composer.toPlainText()
        byte_len = len(raw.encode("utf-8", errors="replace"))
        self.byte_counter_label.setText(f"{byte_len} / {MAX_CHAT_MESSAGE_BYTES} B")
        if byte_len > MAX_CHAT_MESSAGE_BYTES:
            self.byte_counter_label.setStyleSheet(
                f"color: {PALETTE['accent_error']}; font-size: 8.5pt; font-weight: 700;"
            )
        elif byte_len > int(MAX_CHAT_MESSAGE_BYTES * 0.85):
            self.byte_counter_label.setStyleSheet(
                f"color: {PALETTE['accent_warning']}; font-size: 8.5pt; font-weight: 600;"
            )
        else:
            self.byte_counter_label.setStyleSheet(
                f"color: {PALETTE['text_muted']}; font-size: 8.5pt;"
            )

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if (
            self.stack.currentWidget() is self.workspace_page
            and event.mimeData().hasUrls()
            and any(u.isLocalFile() for u in event.mimeData().urls())
        ):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        if self.stack.currentWidget() is self.workspace_page and event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    local_path = Path(url.toLocalFile())
                    if local_path.is_file():
                        self._send_file_path(local_path)
                        event.acceptProposedAction()
                        return
        super().dropEvent(event)

    def _on_discovery_mode_changed(self, auto_checked: bool) -> None:
        self.host_input.setEnabled(not auto_checked)
        self.port_input.setEnabled(not auto_checked)
        if not auto_checked and self.isVisible():
            self.host_input.setFocus()

    def _on_connect_clicked(self) -> None:
        self.login_error_label.hide()
        username = self.username_input.text().strip()
        host: str | None = None
        port: int | None = None

        if self.manual_radio.isChecked():
            host = self.host_input.text().strip()
            if not host:
                self._show_login_error("Vui lòng nhập IP/Host của server.")
                return
            raw_port = self.port_input.text().strip()
            if raw_port:
                if not raw_port.isdigit() or not (1 <= int(raw_port) <= 65535):
                    self._show_login_error("Port phải là số nguyên từ 1 đến 65535.")
                    return
                port = int(raw_port)

        self.controller.connect(username=username, server_host=host, server_port=port)

    def _show_login_error(self, message: str) -> None:
        self.login_error_label.setText(message)
        self.login_error_label.show()

    def _on_state_changed(self, state_str: str) -> None:
        is_connecting = state_str == ConnectionState.CONNECTING.value
        is_disconnecting = state_str == ConnectionState.DISCONNECTING.value
        self.connect_button.setEnabled(state_str == ConnectionState.DISCONNECTED.value)
        self.connect_button.setText("Đang kết nối..." if is_connecting else "Kết nối")
        self.username_input.setEnabled(not is_connecting)
        self.auto_radio.setEnabled(not is_connecting)
        self.manual_radio.setEnabled(not is_connecting)
        if not is_connecting:
            self._on_discovery_mode_changed(self.auto_radio.isChecked())
        self.disconnect_button.setEnabled(not is_disconnecting)

    def _on_connected(self, event: ConnectResultEvent) -> None:
        if event.gen != self.controller.current_gen:
            return
        self._active_gen = event.gen
        self._current_username = event.username
        self.store = ChatSessionStore()
        self._clear_transfer_cards()

        port_display = event.server_port or CONFIG["server"]["tcp_port"]
        endpoint = f"{event.server_host}:{port_display}"
        self.endpoint_chip.setText(endpoint)
        self.tls_chip.setText(f"{event.tls_version} Verified")
        self.kv_server_val.setText(endpoint)
        self.kv_tls_val.setText(event.tls_version or "TLS")
        cipher_display = event.tls_cipher or "TLS Encrypted"
        if len(cipher_display) > 28:
            self.kv_cipher_val.setText(cipher_display[:26] + "…")
        else:
            self.kv_cipher_val.setText(cipher_display)
        self.kv_cipher_val.setToolTip(cipher_display)

        self.me_avatar.set_avatar(
            initials_for(event.username),
            avatar_color_for(event.username),
            PALETTE["avatar_text"],
            status="online",
        )
        self.me_name_label.setText(f"@{event.username}")
        self.me_endpoint_label.setText(endpoint)

        self.online_updated_label.setText("Cập nhật: Đang tải...")
        self.peer_search_input.clear()
        self.composer.clear()
        self._update_byte_counter()
        self._hide_error_banner_immediate()

        self.stack.setCurrentWidget(self.workspace_page)
        self._fade_widget_in(self.workspace_page, duration=170)
        self._refresh_sidebar()
        self._load_active_conversation()
        self.controller.refresh_online_users()
        self._online_poll_timer.start()

    def _on_connect_failed(self, event: ErrorEvent) -> None:
        if event.gen != self.controller.current_gen:
            return
        self._show_login_error(event.message)

    def _on_disconnected(self, event: ErrorEvent) -> None:
        if event.gen != self._active_gen and event.gen != self.controller.current_gen:
            return
        self._status_reset_timer.stop()
        self._online_poll_timer.stop()
        self._clear_transfer_cards()
        self._hide_error_banner_immediate()
        self.stack.setCurrentWidget(self.login_page)
        self._fade_widget_in(self.login_page, duration=150)
        if event.message:
            self._show_login_error(event.message)

    def _on_online_list(self, event: OnlineListEvent) -> None:
        if event.gen != self._active_gen:
            return
        self.store.online_users = [u for u in event.users if u != self._current_username]
        self.store.online_refreshed_at = datetime.now().strftime("%H:%M:%S")
        self.online_sec_label.setText(f"TRỰC TUYẾN · {len(self.store.online_users)}")
        self.online_updated_label.setText(f"Cập nhật lúc {self.store.online_refreshed_at}")
        self._refresh_sidebar()
        self._update_conv_header_meta()

    def _on_message_received(self, event: ChatMessageEvent) -> None:
        if event.gen != self._active_gen:
            return
        conv_id, record = self.store.append_incoming(
            sender=event.sender,
            text=event.text,
            is_broadcast=event.is_broadcast,
        )
        if conv_id == self.store.active_conv_id:
            self.message_model.append_message(record)
            self._sync_feed_stack()
            self._scroll_to_bottom(animate=True)
        self._refresh_sidebar()

    def _on_delivery_status(self, event: DeliveryStatusEvent) -> None:
        if event.gen != self._active_gen:
            return
        if event.ok:
            self.global_status_label.setText(event.message or "Server đã tiếp nhận tin nhắn.")
            self._status_reset_timer.start(4000)
        else:
            code_part = f"[{event.code}] " if event.code else ""
            self._show_error_banner(f"{code_part}{event.message}")

    def _on_server_error(self, event: ErrorEvent) -> None:
        if event.gen != self._active_gen:
            return
        code_part = f"[{event.code}] " if event.code else ""
        self._show_error_banner(f"{code_part}{event.message}")

    def _on_transfer_event(self, event: TransferSnapshotEvent) -> None:
        if event.gen != self._active_gen:
            return
        rec = self.store.apply_transfer_event(
            event=event.event,
            session_id=event.session_id,
            direction=event.direction,
            peer=event.peer,
            filename=event.filename,
            current=event.current,
            total=event.total,
            saved_path=event.saved_path,
            error=event.error,
        )
        self.transfer_empty_label.hide()
        self.transfer_scroll.show()
        if event.event == "started" and not self.toggle_inspector_btn.isChecked():
            self._set_inspector_open(True)

        card = self._transfer_cards.get(rec.session_id)
        if card is None:
            card = TransferItemCard(rec)
            self._transfer_cards[rec.session_id] = card
            self.transfer_cards_layout.insertWidget(0, card)
        else:
            card.update_record(rec, animate=True)

        self.transfer_count_lbl.setText(str(len(self.store.transfers)))

        # Route broadcast uploads as one aggregated bubble in #all and broadcast downloads into #all;
        # keep point-to-point transfers inside each peer's DM conversation.
        conv_id = ""
        file_msg = None
        inserted = False
        if event.is_broadcast and event.batch_id and event.direction == "upload":
            conv_id = BROADCAST_CONV_ID
            file_msg, inserted = self.store.upsert_broadcast_batch_file(
                username=self._current_username,
                batch_id=event.batch_id,
                batch_total=event.batch_total,
                rec=rec,
            )
        elif event.is_broadcast:
            conv_id = BROADCAST_CONV_ID
            file_msg, inserted = self.store.upsert_file_message(
                conv_id=BROADCAST_CONV_ID,
                username=self._current_username,
                rec=rec,
            )
        elif rec.peer:
            conv_id = rec.peer
            file_msg, inserted = self.store.upsert_file_message(
                conv_id=conv_id,
                username=self._current_username,
                rec=rec,
            )

        if file_msg is not None and conv_id:
            if conv_id == self.store.active_conv_id:
                self.message_model.upsert_file_record(file_msg)
                self._sync_feed_stack()
                if inserted:
                    self._scroll_to_bottom(animate=True)
            elif inserted:
                self._refresh_sidebar()

        if event.event == "completed":
            has_active = any(
                r.state in ("INIT_SENT", "TRANSFERRING", "AWAITING_VERIFICATION", "RECEIVING")
                for r in self.store.transfers.values()
            )
            if not has_active:
                self.global_status_label.setText(f"Đã truyền xong '{rec.filename}'.")
                self._status_reset_timer.start(4000)
        elif event.event == "failed":
            self.global_status_label.setText(f"Truyền file '{rec.filename}' thất bại.")
            self._status_reset_timer.start(4000)
            if event.error:
                self._show_error_banner(f"Lỗi truyền file '{rec.filename}': {event.error}")

    def _ensure_banner_anim(self) -> QPropertyAnimation:
        if self._banner_anim is None:
            anim = QPropertyAnimation(self.error_banner, b"maximumHeight", self)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)

            def _on_done() -> None:
                if self.error_banner.maximumHeight() == 0:
                    self.error_banner.hide()
                self.error_banner.setMaximumHeight(16777215)

            anim.finished.connect(_on_done)
            self._banner_anim = anim
        return self._banner_anim

    def _show_error_banner(self, text: str) -> None:
        self.error_banner_label.setText(text)
        if self._banner_anim is not None:
            self._banner_anim.stop()
        self.error_banner.show()
        if not self.isVisible():
            self.error_banner.setMaximumHeight(16777215)
            return
        target_h = max(40, self.error_banner.sizeHint().height())
        self.error_banner.setMaximumHeight(0)
        anim = self._ensure_banner_anim()
        anim.setDuration(140)
        anim.setStartValue(0)
        anim.setEndValue(target_h)
        anim.start()

    def _hide_error_banner_immediate(self) -> None:
        if self._banner_anim is not None:
            self._banner_anim.stop()
        self.error_banner.hide()
        self.error_banner.setMaximumHeight(16777215)

    def _hide_error_banner_animated(self) -> None:
        if not self.isVisible():
            self._hide_error_banner_immediate()
            return
        anim = self._ensure_banner_anim()
        anim.stop()
        anim.setDuration(120)
        anim.setStartValue(self.error_banner.height())
        anim.setEndValue(0)
        anim.start()

    def _clear_transfer_cards(self) -> None:
        for card in self._transfer_cards.values():
            card.deleteLater()
        self._transfer_cards.clear()
        self.transfer_scroll.hide()
        self.transfer_empty_label.show()
        self.transfer_count_lbl.setText("0")

    def _refresh_sidebar(self) -> None:
        self.channel_list.blockSignals(True)
        self.channel_list.clear()
        bc = self.store.conversations[BROADCAST_CONV_ID]
        bc_item = QListWidgetItem()
        bc_item.setData(Qt.ItemDataRole.UserRole, BROADCAST_CONV_ID)
        bc_item.setSizeHint(QSize(0, 52))
        bc_widget = SidebarItemWidget(
            title="Global chat (#all)",
            subtitle="Gửi tới mọi người",
            avatar_text="#",
            avatar_bg=PALETTE["brand_soft"],
            avatar_fg=PALETTE["brand_soft_text"],
            status=None,
            unread_count=bc.unread_count,
            is_channel=True,
        )
        self.channel_list.addItem(bc_item)
        self.channel_list.setItemWidget(bc_item, bc_widget)
        if self.store.active_conv_id == BROADCAST_CONV_ID:
            self.channel_list.setCurrentItem(bc_item)
        self.channel_list.blockSignals(False)

        query = self.peer_search_input.text().strip().lower()
        ordered_peers: list[str] = list(self.store.online_users)
        for cid in self.store.conversations:
            if cid != BROADCAST_CONV_ID and cid not in ordered_peers:
                ordered_peers.append(cid)

        self.peer_list.blockSignals(True)
        self.peer_list.clear()
        for peer in ordered_peers:
            if query and query not in peer.lower():
                continue
            is_online = peer in self.store.online_users
            conv = self.store.conversations.get(peer)
            unread = conv.unread_count if conv else 0
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, peer)
            item.setSizeHint(QSize(0, 52))
            peer_widget = SidebarItemWidget(
                title=peer,
                subtitle="Đang trực tuyến" if is_online else "Ngoại tuyến",
                avatar_text=initials_for(peer),
                avatar_bg=avatar_color_for(peer),
                avatar_fg=PALETTE["avatar_text"],
                status="online" if is_online else "offline",
                unread_count=unread,
                is_channel=False,
            )
            self.peer_list.addItem(item)
            self.peer_list.setItemWidget(item, peer_widget)
            if self.store.active_conv_id == peer:
                self.peer_list.setCurrentItem(item)
        self.peer_list.blockSignals(False)

    def _on_channel_clicked(self, item: QListWidgetItem) -> None:
        conv_id = item.data(Qt.ItemDataRole.UserRole)
        if conv_id:
            self.store.set_active_conversation(conv_id)
            self._refresh_sidebar()
            self._load_active_conversation()

    def _on_peer_clicked(self, item: QListWidgetItem) -> None:
        peer = item.data(Qt.ItemDataRole.UserRole)
        if peer:
            self.store.set_active_conversation(peer)
            self._refresh_sidebar()
            self._load_active_conversation()

    def _update_conv_header_meta(self) -> None:
        cid = self.store.active_conv_id
        if cid == BROADCAST_CONV_ID:
            self.conv_title_label.setText("Global chat (#all)")
            count = len(self.store.online_users)
            self.global_status_label.setText(
                f"{count} người khác online  •  Kênh chung"
            )
            self.composer.setPlaceholderText("Nhắn tin tới mọi người trong Global chat...")
        else:
            is_online = cid in self.store.online_users
            self.conv_title_label.setText(f"@{cid}")
            state_txt = "Đang trực tuyến" if is_online else "Hiện đang ngoại tuyến"
            self.global_status_label.setText(f"{state_txt}  •  Tin nhắn riêng tư")
            self.composer.setPlaceholderText(f"Nhắn tin riêng cho @{cid}...")

    def _load_active_conversation(self) -> None:
        cid = self.store.active_conv_id
        conv = self.store.conversations[cid]
        is_dm = not conv.is_broadcast
        self._update_conv_header_meta()
        self.attach_button.setEnabled(True)
        self.header_send_file_btn.setEnabled(True)
        tip = (
            f"Gửi file tới @{cid}"
            if is_dm
            else "Gửi file tới tất cả người dùng đang online"
        )
        self.attach_button.setToolTip(tip)
        self.header_send_file_btn.setToolTip(tip)
        self.message_model.set_messages(conv.messages)
        self._sync_feed_stack()
        self._scroll_to_bottom(animate=False)

    def _sync_feed_stack(self) -> None:
        has_rows = self.message_model.rowCount() > 0
        self.feed_stack.setCurrentIndex(1 if has_rows else 0)

    def _scroll_to_bottom(self, animate: bool = False) -> None:
        if self.message_model.rowCount() <= 0:
            return
        if not animate or not self.isVisible():
            if self._scroll_anim is not None:
                self._scroll_anim.stop()
            self.message_view.scrollToBottom()
            return

        # Compute target layout synchronously so scrollbar maximum is up to date
        self.message_view.doItemsLayout()
        bar = self.message_view.verticalScrollBar()
        target = bar.maximum()
        start = bar.value()
        if target <= start:
            return
        if self._scroll_anim is None:
            self._scroll_anim = QPropertyAnimation(bar, b"value", self)
            self._scroll_anim.setDuration(130)
            self._scroll_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._scroll_anim.stop()
        self._scroll_anim.setStartValue(start)
        self._scroll_anim.setEndValue(target)
        self._scroll_anim.start()

    def _on_send_clicked(self) -> None:
        if self.controller.state != ConnectionState.CONNECTED:
            self._show_error_banner("Chưa kết nối tới server.")
            return

        raw_text = self.composer.toPlainText()
        if not raw_text.strip():
            return

        try:
            encoded = raw_text.encode("utf-8")
        except UnicodeEncodeError:
            self._show_error_banner("Tin nhắn chứa ký tự UTF-8 không hợp lệ.")
            return

        if len(encoded) > MAX_CHAT_MESSAGE_BYTES:
            self._show_error_banner(
                f"Tin nhắn vượt quá giới hạn {MAX_CHAT_MESSAGE_BYTES} bytes "
                f"(hiện tại {len(encoded)} bytes)."
            )
            return

        cid = self.store.active_conv_id
        if cid == BROADCAST_CONV_ID:
            self.controller.send_broadcast(raw_text)
        else:
            self.controller.send_direct(cid, raw_text)

        record = self.store.append_outgoing(cid, self._current_username, raw_text)
        self.message_model.append_message(record)
        self._sync_feed_stack()
        self._scroll_to_bottom(animate=True)
        self.composer.clear()

    def _send_file_path(self, local_path: Path) -> None:
        if self.controller.state != ConnectionState.CONNECTED:
            self._show_error_banner("Chưa kết nối tới server.")
            return

        cid = self.store.active_conv_id
        if cid == BROADCAST_CONV_ID:
            peers = list(self.store.online_users)
            if not peers:
                self._show_error_banner(
                    "Chưa có người dùng nào khác đang online. Hãy bấm Refresh để cập nhật danh sách."
                )
                return
            self.controller.send_file_broadcast(peers, local_path)
            self._status_reset_timer.stop()
            self.global_status_label.setText(
                f"Đang gửi '{local_path.name}' tới {len(peers)} người online..."
            )
            return

        self.controller.send_file(cid, local_path)

    def _on_attach_clicked(self) -> None:
        if self.controller.state != ConnectionState.CONNECTED:
            self._show_error_banner("Chưa kết nối tới server.")
            return

        cid = self.store.active_conv_id
        if cid == BROADCAST_CONV_ID:
            peers = list(self.store.online_users)
            if not peers:
                self._show_error_banner(
                    "Chưa có người dùng nào khác đang online. Hãy bấm Refresh để cập nhật danh sách."
                )
                return
            file_path, _ = QFileDialog.getOpenFileName(
                self, f"Chọn file gửi tới tất cả ({len(peers)} người online)"
            )
            if not file_path:
                return
            if self.controller.state != ConnectionState.CONNECTED:
                self._show_error_banner("Kết nối đã bị ngắt trước khi gửi file.")
                return
            peers = list(self.store.online_users)
            if not peers:
                self._show_error_banner(
                    "Không còn người dùng nào khác đang online để gửi file."
                )
                return
            self._send_file_path(Path(file_path))
            return

        file_path, _ = QFileDialog.getOpenFileName(self, f"Chọn file gửi tới @{cid}")
        if not file_path:
            return
        if self.controller.state != ConnectionState.CONNECTED:
            self._show_error_banner("Kết nối đã bị ngắt trước khi gửi file.")
            return
        self._send_file_path(Path(file_path))

    def _on_message_context_menu(self, pos: QPoint) -> None:
        idx = self.message_view.indexAt(pos)
        if not idx.isValid():
            return
        self.message_view.setCurrentIndex(idx)
        menu = QMenu(self)
        copy_action = QAction("Sao chép tin nhắn", self)
        copy_action.triggered.connect(self._copy_selected_message)
        menu.addAction(copy_action)
        menu.exec(self.message_view.viewport().mapToGlobal(pos))

    def _copy_selected_message(self) -> None:
        idx = self.message_view.currentIndex()
        if not idx.isValid():
            return
        text = idx.data(Qt.ItemDataRole.DisplayRole)
        if isinstance(text, str):
            QApplication.clipboard().setText(text)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._status_reset_timer.stop()
        self._online_poll_timer.stop()
        self.topology_canvas._timer.stop()
        for anim in (self._fade_anim, self._scroll_anim, self._inspector_anim, self._banner_anim):
            if anim is not None:
                anim.stop()
        for card in self._transfer_cards.values():
            if card._bar_anim is not None:
                card._bar_anim.stop()
        self.controller.shutdown_sync(timeout=2.5)
        super().closeEvent(event)
