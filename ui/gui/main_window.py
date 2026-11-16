"""Main window, login view, sidebar, chat feed, and transfer drawer for PySide6 GUI."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QModelIndex, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QCloseEvent, QInputMethodEvent, QKeyEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QFrame,
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
from common.app_config import CONFIG
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
)
from ui.gui.theme import PALETTE, build_stylesheet


class ChatComposer(QPlainTextEdit):
    """Multi-line composer supporting Vietnamese IME and Enter-to-send."""

    send_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ime_composing = False
        self.setPlaceholderText("Nhập tin nhắn... (Enter để gửi, Shift+Enter xuống dòng)")
        self.setFixedHeight(64)
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
    """Compact progress card inside the sidebar transfer drawer."""

    def __init__(self, rec: TransferRecord, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TransferItemCard")
        self.setStyleSheet(
            f"QFrame#TransferItemCard {{ background-color: {PALETTE['bg_panel']}; "
            f"border: 1px solid {PALETTE['border_subtle']}; border-radius: 8px; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        top_row = QHBoxLayout()
        top_row.setSpacing(6)
        self.title_label = QLabel()
        self.title_label.setTextFormat(Qt.TextFormat.PlainText)
        self.title_label.setWordWrap(True)
        self.title_label.setStyleSheet("font-weight: 600; font-size: 9pt;")
        self.pct_label = QLabel()
        self.pct_label.setObjectName("MonoLabel")
        self.pct_label.setTextFormat(Qt.TextFormat.PlainText)
        top_row.addWidget(self.title_label, 1)
        top_row.addWidget(self.pct_label)
        layout.addLayout(top_row)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 100)
        layout.addWidget(self.progress)

        self.status_label = QLabel()
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setObjectName("MutedLabel")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.update_record(rec)

    def update_record(self, rec: TransferRecord) -> None:
        arrow = "Gửi ->" if rec.direction == "upload" else "Nhận <-"
        peer_text = f" {rec.peer}" if rec.peer else ""
        self.title_label.setText(f"{arrow}{peer_text}: {rec.filename}")

        pct = (
            int(min(100, (rec.current_bytes / rec.total_bytes) * 100))
            if rec.total_bytes > 0
            else (100 if rec.state == "COMPLETED" else 0)
        )
        self.progress.setValue(pct)
        self.pct_label.setText(f"{pct}%")

        size_info = f"{format_bytes(rec.current_bytes)} / {format_bytes(rec.total_bytes)}"
        if rec.state == "INIT_SENT":
            self.status_label.setStyleSheet(f"color: {PALETTE['text_secondary']}; font-size: 8.5pt;")
            self.status_label.setText(f"Đang chờ bên nhận chấp nhận ({size_info})")
        elif rec.state == "TRANSFERRING":
            self.status_label.setStyleSheet(f"color: {PALETTE['text_secondary']}; font-size: 8.5pt;")
            self.status_label.setText(f"Đang gửi... {size_info}")
        elif rec.state == "AWAITING_VERIFICATION":
            self.status_label.setStyleSheet(f"color: {PALETTE['accent_warning']}; font-size: 8.5pt;")
            self.status_label.setText("Đã gửi 100% — đang chờ bên nhận kiểm tra SHA-256...")
        elif rec.state == "RECEIVING":
            self.status_label.setStyleSheet(f"color: {PALETTE['text_secondary']}; font-size: 8.5pt;")
            self.status_label.setText(f"Đang tải xuống... {size_info}")
        elif rec.state == "COMPLETED":
            self.status_label.setStyleSheet(f"color: {PALETTE['accent_online']}; font-size: 8.5pt;")
            if rec.saved_path:
                self.status_label.setText(f"Hoàn tất • Đã lưu: {rec.saved_path}")
            else:
                self.status_label.setText(f"Hoàn tất • Bên nhận đã xác minh ({format_bytes(rec.total_bytes)})")
        elif rec.state == "FAILED":
            self.status_label.setStyleSheet(f"color: {PALETTE['accent_error']}; font-size: 8.5pt;")
            self.status_label.setText(f"Thất bại: {rec.error or 'Lỗi truyền file'}")


class MainWindow(QMainWindow):
    """Primary desktop window switching cleanly between Login and Chat workspace."""

    def __init__(self, controller: SessionController | None = None) -> None:
        super().__init__()
        self.setWindowTitle("CollabChat — Distributed TLS Chat & File Transfer")
        self.setMinimumSize(900, 600)
        self.resize(1140, 740)

        self.bridge = GuiSessionBridge()
        self.controller = controller or SessionController(bridge=self.bridge)
        if controller is not None:
            self.bridge = controller.bridge
        self.store = ChatSessionStore()
        self._active_gen = 0
        self._current_username = ""
        self._transfer_cards: dict[str, TransferItemCard] = {}
        self._status_reset_timer = QTimer(self)
        self._status_reset_timer.setSingleShot(True)
        self._status_reset_timer.timeout.connect(
            lambda: self.global_status_label.setText("Đã kết nối TLS")
        )
        self._online_poll_timer = QTimer(self)
        self._online_poll_timer.setInterval(5000)
        self._online_poll_timer.timeout.connect(self.controller.refresh_online_users)

        self.setStyleSheet(build_stylesheet())
        self._build_ui()
        self._connect_signals()

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
        outer = QVBoxLayout(page)
        outer.setAlignment(Qt.AlignmentFlag.AlignCenter)

        card = QFrame()
        card.setObjectName("LoginCard")
        card.setFixedWidth(420)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(14)

        header_row = QHBoxLayout()
        title = QLabel("CollabChat")
        title.setObjectName("TitleLabel")
        tls_badge = QLabel("TLS Verified")
        tls_badge.setObjectName("TlsBadge")
        header_row.addWidget(title)
        header_row.addStretch()
        header_row.addWidget(tls_badge)
        layout.addLayout(header_row)

        subtitle = QLabel("Đăng nhập vào máy chủ cộng tác qua kết nối TLS.")
        subtitle.setObjectName("MutedLabel")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        user_label = QLabel("Username")
        user_label.setObjectName("SectionHeader")
        self.username_input = QLineEdit()
        self.username_input.setPlaceholderText("Ví dụ: alice")
        layout.addWidget(user_label)
        layout.addWidget(self.username_input)

        mode_label = QLabel("Phương thức tìm Server")
        mode_label.setObjectName("SectionHeader")
        layout.addWidget(mode_label)

        self.auto_radio = QRadioButton("Auto Discovery (UDP Broadcast)")
        self.auto_radio.setChecked(True)
        self.manual_radio = QRadioButton("Nhập thủ công IP / Port")
        layout.addWidget(self.auto_radio)
        layout.addWidget(self.manual_radio)

        manual_row = QHBoxLayout()
        manual_row.setSpacing(8)
        self.host_input = QLineEdit()
        self.host_input.setPlaceholderText("IPv4 (vd: 127.0.0.1)")
        self.port_input = QLineEdit()
        self.port_input.setPlaceholderText(str(CONFIG["server"]["tcp_port"]))
        self.port_input.setFixedWidth(96)
        self.host_input.setEnabled(False)
        self.port_input.setEnabled(False)
        manual_row.addWidget(self.host_input, 1)
        manual_row.addWidget(self.port_input)
        layout.addLayout(manual_row)

        self.login_error_label = QLabel()
        self.login_error_label.setTextFormat(Qt.TextFormat.PlainText)
        self.login_error_label.setWordWrap(True)
        self.login_error_label.setStyleSheet(
            f"color: #FCA5A5; background-color: {PALETTE['accent_error_bg']}; "
            f"border: 1px solid {PALETTE['accent_error_border']}; border-radius: 8px; padding: 8px;"
        )
        self.login_error_label.hide()
        layout.addWidget(self.login_error_label)

        self.connect_button = QPushButton("Kết nối & Đăng nhập")
        self.connect_button.setObjectName("PrimaryButton")
        self.connect_button.setMinimumHeight(38)
        layout.addWidget(self.connect_button)

        outer.addWidget(card)
        return page

    def _build_workspace_page(self) -> QWidget:
        page = QWidget()
        main_layout = QVBoxLayout(page)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Top utility bar
        top_bar = QFrame()
        top_bar.setObjectName("TopBar")
        top_bar.setFixedHeight(52)
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(16, 0, 16, 0)
        top_layout.setSpacing(10)

        app_label = QLabel("CollabChat")
        app_label.setObjectName("TitleLabel")
        self.top_tls_badge = QLabel("TLS")
        self.top_tls_badge.setObjectName("TlsBadge")
        self.endpoint_label = QLabel()
        self.endpoint_label.setObjectName("MonoLabel")
        self.endpoint_label.setTextFormat(Qt.TextFormat.PlainText)

        self.user_badge = QLabel()
        self.user_badge.setObjectName("UserBadge")
        self.user_badge.setTextFormat(Qt.TextFormat.PlainText)

        self.disconnect_button = QPushButton("Ngắt kết nối")
        self.disconnect_button.setObjectName("DangerButton")
        self.disconnect_button.setIcon(get_icon("logout", "#FCA5A5", 16))

        top_layout.addWidget(app_label)
        top_layout.addWidget(self.top_tls_badge)
        top_layout.addWidget(self.endpoint_label)
        top_layout.addStretch()
        top_layout.addWidget(self.user_badge)
        top_layout.addWidget(self.disconnect_button)
        main_layout.addWidget(top_bar)

        # Splitter: Sidebar | Chat Area
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        main_layout.addWidget(splitter, 1)

        # Sidebar
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setMinimumWidth(220)
        sidebar.setMaximumWidth(340)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(10, 12, 10, 10)
        side_layout.setSpacing(8)

        ch_header = QLabel("KÊNH CHAT")
        ch_header.setObjectName("SectionHeader")
        side_layout.addWidget(ch_header)

        self.channel_list = QListWidget()
        self.channel_list.setObjectName("SidebarList")
        self.channel_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.channel_list.setFixedHeight(44)
        side_layout.addWidget(self.channel_list)

        online_row = QHBoxLayout()
        online_header = QLabel("NGƯỜI DÙNG ONLINE")
        online_header.setObjectName("SectionHeader")
        self.refresh_button = QPushButton()
        self.refresh_button.setObjectName("IconButton")
        self.refresh_button.setToolTip("Làm mới danh sách online (LIST_ONLINE)")
        self.refresh_button.setIcon(get_icon("refresh", PALETTE["text_secondary"], 16))
        self.refresh_button.setFixedSize(28, 28)
        online_row.addWidget(online_header)
        online_row.addStretch()
        online_row.addWidget(self.refresh_button)
        side_layout.addLayout(online_row)

        self.online_updated_label = QLabel("Cập nhật: Chưa tải")
        self.online_updated_label.setObjectName("MutedLabel")
        side_layout.addWidget(self.online_updated_label)

        self.peer_list = QListWidget()
        self.peer_list.setObjectName("SidebarList")
        self.peer_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        side_layout.addWidget(self.peer_list, 1)

        # Transfer Drawer
        drawer = QFrame()
        drawer.setObjectName("TransferDrawer")
        drawer.setMaximumHeight(220)
        drawer_layout = QVBoxLayout(drawer)
        drawer_layout.setContentsMargins(8, 8, 8, 8)
        drawer_layout.setSpacing(6)

        tr_header = QLabel("TRUYỀN FILE (downloads/)")
        tr_header.setObjectName("SectionHeader")
        drawer_layout.addWidget(tr_header)

        self.transfer_empty_label = QLabel("Chưa có phiên truyền file nào.")
        self.transfer_empty_label.setObjectName("MutedLabel")
        drawer_layout.addWidget(self.transfer_empty_label)

        self.transfer_scroll = QScrollArea()
        self.transfer_scroll.setWidgetResizable(True)
        self.transfer_scroll.setMinimumHeight(104)
        self.transfer_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.transfer_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.transfer_scroll.setStyleSheet("background: transparent;")
        self.transfer_container = QWidget()
        self.transfer_container.setStyleSheet("background: transparent;")
        self.transfer_cards_layout = QVBoxLayout(self.transfer_container)
        self.transfer_cards_layout.setContentsMargins(0, 0, 0, 0)
        self.transfer_cards_layout.setSpacing(6)
        self.transfer_cards_layout.addStretch()
        self.transfer_scroll.setWidget(self.transfer_container)
        self.transfer_scroll.hide()
        drawer_layout.addWidget(self.transfer_scroll)

        side_layout.addWidget(drawer)
        splitter.addWidget(sidebar)

        # Main Chat Panel
        chat_panel = QWidget()
        chat_panel.setMinimumWidth(460)
        chat_layout = QVBoxLayout(chat_panel)
        chat_layout.setContentsMargins(0, 0, 0, 0)
        chat_layout.setSpacing(0)

        # Chat Header
        chat_header = QFrame()
        chat_header.setObjectName("ChatHeader")
        chat_header.setFixedHeight(56)
        ch_layout = QHBoxLayout(chat_header)
        ch_layout.setContentsMargins(16, 0, 16, 0)
        ch_layout.setSpacing(10)

        title_col = QVBoxLayout()
        title_col.setSpacing(1)
        self.conv_title_label = QLabel("#all — Chat chung")
        self.conv_title_label.setTextFormat(Qt.TextFormat.PlainText)
        self.conv_title_label.setStyleSheet("font-size: 11.5pt; font-weight: 600;")
        self.global_status_label = QLabel("Sẵn sàng")
        self.global_status_label.setObjectName("MutedLabel")
        self.global_status_label.setTextFormat(Qt.TextFormat.PlainText)
        title_col.addWidget(self.conv_title_label)
        title_col.addWidget(self.global_status_label)

        self.header_send_file_btn = QPushButton("Gửi file")
        self.header_send_file_btn.setIcon(get_icon("paperclip", PALETTE["text_primary"], 16))
        self.header_send_file_btn.setEnabled(True)
        self.header_send_file_btn.setToolTip("Gửi file tới cuộc trò chuyện hiện tại")

        ch_layout.addLayout(title_col, 1)
        ch_layout.addWidget(self.header_send_file_btn)
        chat_layout.addWidget(chat_header)

        # Dismissible Error Banner
        self.error_banner = QFrame()
        self.error_banner.setObjectName("ErrorBanner")
        eb_layout = QHBoxLayout(self.error_banner)
        eb_layout.setContentsMargins(16, 8, 12, 8)
        self.error_banner_label = QLabel()
        self.error_banner_label.setTextFormat(Qt.TextFormat.PlainText)
        self.error_banner_label.setWordWrap(True)
        self.error_banner_label.setStyleSheet("color: #FECACA;")
        eb_close = QPushButton()
        eb_close.setObjectName("IconButton")
        eb_close.setIcon(get_icon("x", "#FECACA", 14))
        eb_close.setFixedSize(24, 24)
        eb_close.clicked.connect(self.error_banner.hide)
        eb_layout.addWidget(self.error_banner_label, 1)
        eb_layout.addWidget(eb_close)
        self.error_banner.hide()
        chat_layout.addWidget(self.error_banner)

        # Message Feed / Empty State
        self.feed_stack = QStackedWidget()
        empty_widget = QWidget()
        empty_layout = QVBoxLayout(empty_widget)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_feed_label = QLabel("Chưa có tin nhắn nào trong hội thoại này.\nHãy bắt đầu cuộc trò chuyện!")
        self.empty_feed_label.setObjectName("MutedLabel")
        self.empty_feed_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(self.empty_feed_label)

        self.message_model = MessageListModel(self)
        self.message_view = QListView()
        self.message_view.setObjectName("MessageFeed")
        self.message_view.setModel(self.message_model)
        self.message_view.setItemDelegate(MessageBubbleDelegate(self.message_view))
        self.message_view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.message_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.message_view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.message_view.setResizeMode(QListView.ResizeMode.Adjust)
        self.message_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        self.feed_stack.addWidget(empty_widget)
        self.feed_stack.addWidget(self.message_view)
        chat_layout.addWidget(self.feed_stack, 1)

        # Composer Bar
        composer_bar = QFrame()
        composer_bar.setObjectName("ComposerBar")
        comp_layout = QHBoxLayout(composer_bar)
        comp_layout.setContentsMargins(14, 10, 14, 10)
        comp_layout.setSpacing(10)

        self.attach_button = QPushButton()
        self.attach_button.setObjectName("IconButton")
        self.attach_button.setIcon(get_icon("paperclip", PALETTE["text_primary"], 20))
        self.attach_button.setFixedSize(40, 40)
        self.attach_button.setEnabled(True)
        self.attach_button.setToolTip("Đính kèm file gửi tới cuộc trò chuyện hiện tại")

        self.composer = ChatComposer()
        self.send_button = QPushButton("Gửi")
        self.send_button.setObjectName("PrimaryButton")
        self.send_button.setIcon(get_icon("send", PALETTE["on_primary"], 16))
        self.send_button.setMinimumHeight(40)

        comp_layout.addWidget(self.attach_button, 0, Qt.AlignmentFlag.AlignBottom)
        comp_layout.addWidget(self.composer, 1)
        comp_layout.addWidget(self.send_button, 0, Qt.AlignmentFlag.AlignBottom)
        chat_layout.addWidget(composer_bar)

        splitter.addWidget(chat_panel)
        splitter.setSizes([270, 870])
        return page

    def _connect_signals(self) -> None:
        self.auto_radio.toggled.connect(self._on_discovery_mode_changed)
        self.username_input.returnPressed.connect(self._on_connect_clicked)
        self.host_input.returnPressed.connect(self._on_connect_clicked)
        self.port_input.returnPressed.connect(self._on_connect_clicked)
        self.connect_button.clicked.connect(self._on_connect_clicked)

        self.disconnect_button.clicked.connect(lambda: self.controller.disconnect())
        self.refresh_button.clicked.connect(self.controller.refresh_online_users)
        self.channel_list.itemClicked.connect(self._on_channel_clicked)
        self.peer_list.itemClicked.connect(self._on_peer_clicked)

        self.composer.send_requested.connect(self._on_send_clicked)
        self.send_button.clicked.connect(self._on_send_clicked)
        self.attach_button.clicked.connect(self._on_attach_clicked)
        self.header_send_file_btn.clicked.connect(self._on_attach_clicked)

        self.message_view.customContextMenuRequested.connect(self._on_message_context_menu)
        copy_shortcut = QShortcut(QKeySequence.StandardKey.Copy, self.message_view)
        copy_shortcut.activated.connect(self._copy_selected_message)

        # Bridge signals (auto Qt.QueuedConnection across threads)
        self.bridge.state_changed.connect(self._on_state_changed)
        self.bridge.connected.connect(self._on_connected)
        self.bridge.connect_failed.connect(self._on_connect_failed)
        self.bridge.online_list_received.connect(self._on_online_list)
        self.bridge.message_received.connect(self._on_message_received)
        self.bridge.delivery_status_received.connect(self._on_delivery_status)
        self.bridge.transfer_event_received.connect(self._on_transfer_event)
        self.bridge.server_error_received.connect(self._on_server_error)
        self.bridge.disconnected.connect(self._on_disconnected)

    def _on_discovery_mode_changed(self, auto_checked: bool) -> None:
        self.host_input.setEnabled(not auto_checked)
        self.port_input.setEnabled(not auto_checked)

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
        self.connect_button.setText("Đang kết nối..." if is_connecting else "Kết nối & Đăng nhập")
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
        self.endpoint_label.setText(f"{event.server_host}:{port_display}")
        self.user_badge.setText(f"@{event.username}")
        self.online_updated_label.setText("Cập nhật: Đang tải...")
        self.composer.clear()
        self.error_banner.hide()
        self.global_status_label.setText("Đã kết nối TLS")

        self.stack.setCurrentWidget(self.workspace_page)
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
        self.error_banner.hide()
        self.stack.setCurrentWidget(self.login_page)
        if event.message:
            self._show_login_error(event.message)

    def _on_online_list(self, event: OnlineListEvent) -> None:
        if event.gen != self._active_gen:
            return
        self.store.online_users = [u for u in event.users if u != self._current_username]
        self.store.online_refreshed_at = datetime.now().strftime("%H:%M:%S")
        self.online_updated_label.setText(
            f"Cập nhật lúc {self.store.online_refreshed_at} ({len(self.store.online_users)} người)"
        )
        self._refresh_sidebar()

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
            self._scroll_to_bottom()
        self._refresh_sidebar()

    def _on_delivery_status(self, event: DeliveryStatusEvent) -> None:
        if event.gen != self._active_gen:
            return
        if event.ok:
            # Protocol has no message_id: report globally without attaching to a specific bubble
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

        card = self._transfer_cards.get(rec.session_id)
        if card is None:
            card = TransferItemCard(rec)
            self._transfer_cards[rec.session_id] = card
            self.transfer_cards_layout.insertWidget(0, card)
        else:
            card.update_record(rec)

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

    def _show_error_banner(self, text: str) -> None:
        self.error_banner_label.setText(text)
        self.error_banner.show()

    def _clear_transfer_cards(self) -> None:
        for card in self._transfer_cards.values():
            card.deleteLater()
        self._transfer_cards.clear()
        self.transfer_scroll.hide()
        self.transfer_empty_label.show()

    def _refresh_sidebar(self) -> None:
        # Channel list (#all)
        self.channel_list.blockSignals(True)
        self.channel_list.clear()
        bc = self.store.conversations[BROADCAST_CONV_ID]
        bc_label = f"#all  ({bc.unread_count} mới)" if bc.unread_count > 0 else "#all — Chat chung"
        bc_item = QListWidgetItem(get_icon("users", PALETTE["text_primary"], 16), bc_label)
        bc_item.setData(Qt.ItemDataRole.UserRole, BROADCAST_CONV_ID)
        self.channel_list.addItem(bc_item)
        if self.store.active_conv_id == BROADCAST_CONV_ID:
            self.channel_list.setCurrentItem(bc_item)
        self.channel_list.blockSignals(False)

        # Peer list: union of online users and existing DM conversations
        ordered_peers: list[str] = list(self.store.online_users)
        for cid in self.store.conversations:
            if cid != BROADCAST_CONV_ID and cid not in ordered_peers:
                ordered_peers.append(cid)

        self.peer_list.blockSignals(True)
        self.peer_list.clear()
        for peer in ordered_peers:
            is_online = peer in self.store.online_users
            conv = self.store.conversations.get(peer)
            unread = conv.unread_count if conv else 0
            status_tag = "" if is_online else " (offline)"
            unread_tag = f"  [{unread}]" if unread > 0 else ""
            item = QListWidgetItem(
                get_icon("user", PALETTE["accent_online"] if is_online else PALETTE["text_muted"], 16),
                f"{peer}{status_tag}{unread_tag}",
            )
            item.setData(Qt.ItemDataRole.UserRole, peer)
            self.peer_list.addItem(item)
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

    def _load_active_conversation(self) -> None:
        cid = self.store.active_conv_id
        conv = self.store.conversations[cid]
        is_dm = not conv.is_broadcast
        self.conv_title_label.setText(
            f"@{cid} — Tin nhắn riêng" if is_dm else "#all — Chat chung"
        )
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
        self._scroll_to_bottom()

    def _sync_feed_stack(self) -> None:
        has_rows = self.message_model.rowCount() > 0
        self.feed_stack.setCurrentIndex(1 if has_rows else 0)

    def _scroll_to_bottom(self) -> None:
        if self.message_model.rowCount() > 0:
            self.message_view.scrollToBottom()

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
        self._scroll_to_bottom()
        self.composer.clear()

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
            self.controller.send_file_broadcast(peers, Path(file_path))
            self._status_reset_timer.stop()
            self.global_status_label.setText(
                f"Đang gửi '{Path(file_path).name}' tới {len(peers)} người online..."
            )
            return

        file_path, _ = QFileDialog.getOpenFileName(self, f"Chọn file gửi tới @{cid}")
        if not file_path:
            return
        if self.controller.state != ConnectionState.CONNECTED:
            self._show_error_banner("Kết nối đã bị ngắt trước khi gửi file.")
            return
        self.controller.send_file(cid, Path(file_path))

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
        self.controller.shutdown_sync(timeout=2.5)
        super().closeEvent(event)
