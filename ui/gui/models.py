"""Qt Main-Thread chat/transfer state models and QStyledItemDelegate message feed."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QPoint,
    QRect,
    QSize,
    Qt,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPainterPath,
    QTextOption,
)
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from ui.gui.theme import PALETTE

BROADCAST_CONV_ID = "#all"
MAX_MESSAGES_PER_CONV = 1000


def format_bytes(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes / (1024 * 1024):.1f} MB"


@dataclass(frozen=True)
class MessageRecord:
    sender: str
    text: str
    is_outgoing: bool
    is_broadcast: bool
    timestamp_str: str


@dataclass
class ConversationState:
    conv_id: str
    title: str
    is_broadcast: bool
    unread_count: int = 0
    messages: deque[MessageRecord] = field(
        default_factory=lambda: deque(maxlen=MAX_MESSAGES_PER_CONV)
    )


@dataclass
class TransferRecord:
    session_id: str
    direction: str  # upload | download
    peer: str
    filename: str
    total_bytes: int
    current_bytes: int = 0
    state: str = "INIT_SENT"  # INIT_SENT | TRANSFERRING | AWAITING_VERIFICATION | RECEIVING | COMPLETED | FAILED
    saved_path: str = ""
    error: str = ""


class ChatSessionStore:
    """In-memory chat & transfer state owned strictly by the Qt Main Thread."""

    def __init__(self) -> None:
        self.conversations: dict[str, ConversationState] = {
            BROADCAST_CONV_ID: ConversationState(
                conv_id=BROADCAST_CONV_ID,
                title="#all (Chat chung)",
                is_broadcast=True,
            )
        }
        self.online_users: list[str] = []
        self.online_refreshed_at: str = "Chưa cập nhật"
        self.active_conv_id: str = BROADCAST_CONV_ID
        self.transfers: dict[str, TransferRecord] = {}

    def ensure_dm_conversation(self, peer: str) -> ConversationState:
        conv = self.conversations.get(peer)
        if conv is None:
            conv = ConversationState(
                conv_id=peer,
                title=f"@{peer}",
                is_broadcast=False,
            )
            self.conversations[peer] = conv
        return conv

    def set_active_conversation(self, conv_id: str) -> ConversationState:
        if conv_id != BROADCAST_CONV_ID and conv_id not in self.conversations:
            self.ensure_dm_conversation(conv_id)
        self.active_conv_id = conv_id
        conv = self.conversations[conv_id]
        conv.unread_count = 0
        return conv

    def append_incoming(self, sender: str, text: str, is_broadcast: bool) -> tuple[str, MessageRecord]:
        conv_id = BROADCAST_CONV_ID if is_broadcast else sender
        conv = (
            self.conversations[BROADCAST_CONV_ID]
            if is_broadcast
            else self.ensure_dm_conversation(sender)
        )
        record = MessageRecord(
            sender=sender,
            text=text,
            is_outgoing=False,
            is_broadcast=is_broadcast,
            timestamp_str=datetime.now().strftime("%H:%M"),
        )
        conv.messages.append(record)
        if conv_id != self.active_conv_id:
            conv.unread_count += 1
        return conv_id, record

    def append_outgoing(self, conv_id: str, username: str, text: str) -> MessageRecord:
        is_broadcast = conv_id == BROADCAST_CONV_ID
        conv = (
            self.conversations[BROADCAST_CONV_ID]
            if is_broadcast
            else self.ensure_dm_conversation(conv_id)
        )
        record = MessageRecord(
            sender=username,
            text=text,
            is_outgoing=True,
            is_broadcast=is_broadcast,
            timestamp_str=datetime.now().strftime("%H:%M"),
        )
        conv.messages.append(record)
        return record

    def apply_transfer_event(
        self,
        event: str,
        session_id: str,
        direction: str,
        peer: str,
        filename: str,
        current: int,
        total: int,
        saved_path: str = "",
        error: str = "",
    ) -> TransferRecord:
        rec = self.transfers.get(session_id)
        if rec is None:
            initial_state = "INIT_SENT" if direction == "upload" else "RECEIVING"
            rec = TransferRecord(
                session_id=session_id,
                direction=direction,
                peer=peer,
                filename=filename,
                total_bytes=total,
                current_bytes=current,
                state=initial_state,
            )
            self.transfers[session_id] = rec

        if event == "started":
            rec.state = "INIT_SENT" if direction == "upload" else "RECEIVING"
            rec.current_bytes = current
            rec.total_bytes = total
        elif event == "progress":
            rec.current_bytes = current
            rec.total_bytes = total
            if direction == "upload":
                rec.state = "AWAITING_VERIFICATION" if (total > 0 and current >= total) else "TRANSFERRING"
            else:
                rec.state = "RECEIVING"
        elif event == "completed":
            rec.current_bytes = rec.total_bytes
            rec.state = "COMPLETED"
            rec.saved_path = saved_path
        elif event == "failed":
            rec.state = "FAILED"
            rec.error = error

        return rec


class MessageListModel(QAbstractListModel):
    """QAbstractListModel backed by the active conversation's bounded deque."""

    RecordRole = Qt.ItemDataRole.UserRole + 1

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._items: list[MessageRecord] = []

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._items)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not (0 <= index.row() < len(self._items)):
            return None
        rec = self._items[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return rec.text
        if role == self.RecordRole:
            return rec
        return None

    def set_messages(self, messages: deque[MessageRecord] | list[MessageRecord]) -> None:
        self.beginResetModel()
        self._items = list(messages)
        self.endResetModel()

    def append_message(self, record: MessageRecord) -> None:
        if len(self._items) >= MAX_MESSAGES_PER_CONV:
            self.beginRemoveRows(QModelIndex(), 0, 0)
            self._items.pop(0)
            self.endRemoveRows()
        row = len(self._items)
        self.beginInsertRows(QModelIndex(), row, row)
        self._items.append(record)
        self.endInsertRows()


class MessageBubbleDelegate(QStyledItemDelegate):
    """Paints plain-text chat bubbles with DPI-aware QFontMetrics sizing."""

    OUTER_MARGIN_X = 16
    OUTER_MARGIN_Y = 5
    PAD_X = 14
    PAD_Y = 10
    HEADER_GAP = 4

    def _fonts(self, option: QStyleOptionViewItem) -> tuple[QFont, QFont]:
        body_font = QFont(option.font)
        meta_font = QFont(option.font)
        pt = meta_font.pointSizeF()
        if pt > 0:
            meta_font.setPointSizeF(max(7.5, pt - 1.5))
        return body_font, meta_font

    def _compute_layout(
        self,
        option: QStyleOptionViewItem,
        record: MessageRecord,
    ) -> tuple[int, int, QRect, bool]:
        view_width = max(320, option.rect.width())
        max_bubble_w = int(view_width * 0.70)
        body_font, meta_font = self._fonts(option)
        body_fm = QFontMetrics(body_font)
        meta_fm = QFontMetrics(meta_font)

        show_sender = record.is_broadcast and not record.is_outgoing
        header_text = (
            f"{record.sender}  {record.timestamp_str}"
            if show_sender
            else record.timestamp_str
        )
        header_w = meta_fm.horizontalAdvance(header_text)
        header_h = meta_fm.height()

        max_text_w = max(120, max_bubble_w - 2 * self.PAD_X)
        text_bounds = body_fm.boundingRect(
            QRect(0, 0, max_text_w, 10000),
            Qt.TextFlag.TextWordWrap,
            record.text,
        )

        content_w = max(text_bounds.width(), header_w)
        bubble_w = min(max_bubble_w, content_w + 2 * self.PAD_X)
        bubble_h = self.PAD_Y + header_h + self.HEADER_GAP + text_bounds.height() + self.PAD_Y
        return bubble_w, bubble_h, text_bounds, show_sender

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        record = index.data(MessageListModel.RecordRole)
        if not isinstance(record, MessageRecord):
            return super().sizeHint(option, index)
        _, bubble_h, _, _ = self._compute_layout(option, record)
        return QSize(option.rect.width(), bubble_h + 2 * self.OUTER_MARGIN_Y)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        record = index.data(MessageListModel.RecordRole)
        if not isinstance(record, MessageRecord):
            super().paint(painter, option, index)
            return

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        bubble_w, bubble_h, _, show_sender = self._compute_layout(option, record)
        if record.is_outgoing:
            x = option.rect.right() - self.OUTER_MARGIN_X - bubble_w
        else:
            x = option.rect.left() + self.OUTER_MARGIN_X
        y = option.rect.top() + self.OUTER_MARGIN_Y

        bubble_rect = QRect(x, y, bubble_w, bubble_h)
        bg_hex = PALETTE["bubble_outgoing"] if record.is_outgoing else PALETTE["bubble_incoming"]
        path = QPainterPath()
        path.addRoundedRect(bubble_rect, 12, 12)
        painter.fillPath(path, QColor(bg_hex))

        if option.state & QStyle.StateFlag.State_Selected:
            painter.setPen(QColor(PALETTE["border_focus"]))
            painter.drawPath(path)
        elif not record.is_outgoing:
            painter.setPen(QColor(PALETTE["border_subtle"]))
            painter.drawPath(path)

        body_font, meta_font = self._fonts(option)
        meta_fm = QFontMetrics(meta_font)
        header_h = meta_fm.height()

        # Header line (sender + timestamp)
        painter.setFont(meta_font)
        meta_color = "#DBEAFE" if record.is_outgoing else PALETTE["text_secondary"]
        painter.setPen(QColor(meta_color))
        header_text = (
            f"{record.sender} • {record.timestamp_str}"
            if show_sender
            else record.timestamp_str
        )
        header_rect = QRect(
            x + self.PAD_X,
            y + self.PAD_Y,
            bubble_w - 2 * self.PAD_X,
            header_h,
        )
        painter.drawText(header_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, header_text)

        # Plain-text message body
        painter.setFont(body_font)
        body_color = PALETTE["on_primary"] if record.is_outgoing else PALETTE["text_primary"]
        painter.setPen(QColor(body_color))
        text_rect = QRect(
            x + self.PAD_X,
            y + self.PAD_Y + header_h + self.HEADER_GAP,
            bubble_w - 2 * self.PAD_X,
            bubble_h - 2 * self.PAD_Y - header_h - self.HEADER_GAP,
        )
        text_opt = QTextOption(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        text_opt.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        painter.drawText(text_rect, record.text, text_opt)

        painter.restore()
