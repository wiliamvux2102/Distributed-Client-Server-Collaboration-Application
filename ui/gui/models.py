"""Qt Main-Thread chat/transfer state models and QStyledItemDelegate message feed."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from PySide6.QtCore import (
    QAbstractListModel,
    QEvent,
    QModelIndex,
    QRect,
    QRectF,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPainterPath,
    QPen,
    QTextOption,
)
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from ui.gui.icons import get_icon
from ui.gui.theme import PALETTE, avatar_color_for

BROADCAST_CONV_ID = "#all"
MAX_MESSAGES_PER_CONV = 1000


def format_bytes(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes / (1024 * 1024):.1f} MB"


def initials_for(name: str) -> str:
    """Return 1-2 uppercase initials for an avatar circle."""
    clean = name.strip()
    if not clean:
        return "?"
    parts = [p for p in clean.replace(".", " ").replace("_", " ").replace("-", " ").split() if p]
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    return clean[0].upper()


def _asymmetric_rounded_path(
    rect: QRectF, tl: float, tr: float, br: float, bl: float
) -> QPainterPath:
    """Build a rounded rectangle with independent corner radii (CSS-style)."""
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    max_r = min(w / 2.0, h / 2.0)
    tl = min(tl, max_r)
    tr = min(tr, max_r)
    br = min(br, max_r)
    bl = min(bl, max_r)

    path = QPainterPath()
    path.moveTo(x + tl, y)
    path.lineTo(x + w - tr, y)
    if tr > 0:
        path.arcTo(x + w - 2 * tr, y, 2 * tr, 2 * tr, 90, -90)
    path.lineTo(x + w, y + h - br)
    if br > 0:
        path.arcTo(x + w - 2 * br, y + h - 2 * br, 2 * br, 2 * br, 0, -90)
    path.lineTo(x + bl, y + h)
    if bl > 0:
        path.arcTo(x, y + h - 2 * bl, 2 * bl, 2 * bl, 270, -90)
    path.lineTo(x, y + tl)
    if tl > 0:
        path.arcTo(x, y, 2 * tl, 2 * tl, 180, -90)
    path.closeSubpath()
    return path


@dataclass(frozen=True)
class MessageRecord:
    sender: str
    text: str
    is_outgoing: bool
    is_broadcast: bool
    timestamp_str: str
    kind: str = "text"  # text | file | system
    session_id: str = ""
    filename: str = ""
    total_bytes: int = 0
    current_bytes: int = 0
    file_state: str = ""
    saved_path: str = ""
    file_error: str = ""
    status_note: str = ""


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
                title="Global chat",
                is_broadcast=True,
            )
        }
        self.online_users: list[str] = []
        self.online_refreshed_at: str = "Chưa cập nhật"
        self.active_conv_id: str = BROADCAST_CONV_ID
        self.transfers: dict[str, TransferRecord] = {}
        self._broadcast_batches: dict[str, dict[str, TransferRecord]] = {}
        self._broadcast_meta: dict[str, tuple[str, int, int]] = {}

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

    def load_history(
        self,
        current_username: str,
        messages: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    ) -> None:
        """Populate historical broadcast/DM messages without incrementing unread counters."""
        grouped: dict[str, list[MessageRecord]] = {}
        for item in messages:
            if not isinstance(item, dict):
                continue
            sender = item.get("sender")
            recipient = item.get("recipient", "")
            text = item.get("text")
            is_broadcast = bool(item.get("is_broadcast", False))
            if not isinstance(sender, str) or not isinstance(text, str):
                continue

            created_at = item.get("created_at")
            try:
                ts_str = (
                    datetime.fromtimestamp(float(created_at)).strftime("%H:%M")
                    if created_at is not None
                    else "--:--"
                )
            except (TypeError, ValueError, OSError):
                ts_str = "--:--"

            is_outgoing = sender == current_username
            if is_broadcast:
                conv_id = BROADCAST_CONV_ID
            else:
                peer = recipient if is_outgoing else sender
                if not isinstance(peer, str) or not peer:
                    continue
                conv_id = peer

            grouped.setdefault(conv_id, []).append(
                MessageRecord(
                    sender=sender,
                    text=text,
                    is_outgoing=is_outgoing,
                    is_broadcast=is_broadcast,
                    timestamp_str=ts_str,
                )
            )

        for conv_id, hist_records in grouped.items():
            conv = (
                self.conversations[BROADCAST_CONV_ID]
                if conv_id == BROADCAST_CONV_ID
                else self.ensure_dm_conversation(conv_id)
            )
            existing = list(conv.messages)
            conv.messages.clear()
            conv.messages.extend(hist_records)
            conv.messages.extend(existing)

    def upsert_file_message(
        self,
        conv_id: str,
        username: str,
        rec: TransferRecord,
        status_note: str = "",
    ) -> tuple[MessageRecord, bool]:
        """Insert or update an inline file transfer bubble in a conversation."""
        is_broadcast = conv_id == BROADCAST_CONV_ID
        conv = (
            self.conversations[BROADCAST_CONV_ID]
            if is_broadcast
            else self.ensure_dm_conversation(conv_id)
        )
        is_outgoing = rec.direction == "upload"
        sender = username if is_outgoing else rec.peer

        for idx in range(len(conv.messages) - 1, -1, -1):
            existing = conv.messages[idx]
            if existing.kind == "file" and existing.session_id == rec.session_id:
                updated = replace(
                    existing,
                    current_bytes=rec.current_bytes,
                    total_bytes=rec.total_bytes,
                    file_state=rec.state,
                    saved_path=rec.saved_path,
                    file_error=rec.error,
                    status_note=status_note,
                    text=rec.filename,
                )
                conv.messages[idx] = updated
                return updated, False

        created = MessageRecord(
            sender=sender,
            text=rec.filename,
            is_outgoing=is_outgoing,
            is_broadcast=is_broadcast,
            timestamp_str=datetime.now().strftime("%H:%M"),
            kind="file",
            session_id=rec.session_id,
            filename=rec.filename,
            total_bytes=rec.total_bytes,
            current_bytes=rec.current_bytes,
            file_state=rec.state,
            saved_path=rec.saved_path,
            file_error=rec.error,
            status_note=status_note,
        )
        conv.messages.append(created)
        if not is_outgoing and conv_id != self.active_conv_id:
            conv.unread_count += 1
        return created, True

    def upsert_broadcast_batch_file(
        self,
        username: str,
        batch_id: str,
        batch_total: int,
        rec: TransferRecord,
    ) -> tuple[MessageRecord, bool]:
        """Aggregate fan-out broadcast upload sessions into one bubble inside #all."""
        sessions = self._broadcast_batches.setdefault(batch_id, {})
        sessions[rec.session_id] = rec

        prev_name, prev_size, prev_target = self._broadcast_meta.get(
            batch_id, (rec.filename, rec.total_bytes, batch_total)
        )
        filename = prev_name or rec.filename
        per_file_bytes = max(prev_size, rec.total_bytes)
        target_count = max(prev_target, batch_total, len(sessions))
        self._broadcast_meta[batch_id] = (filename, per_file_bytes, target_count)

        agg_total = per_file_bytes * target_count
        agg_current = sum(min(r.current_bytes, per_file_bytes) for r in sessions.values())
        done_cnt = sum(1 for r in sessions.values() if r.state == "COMPLETED")
        fail_cnt = sum(1 for r in sessions.values() if r.state == "FAILED")
        await_cnt = sum(1 for r in sessions.values() if r.state == "AWAITING_VERIFICATION")

        if done_cnt + fail_cnt >= target_count:
            if fail_cnt == 0:
                agg_state = "COMPLETED"
                agg_current = agg_total
                note = f"{format_bytes(per_file_bytes)}  •  Đã gửi tới {done_cnt}/{target_count} người (SHA-256 OK)"
            elif done_cnt > 0:
                agg_state = "FAILED"
                agg_current = per_file_bytes * done_cnt
                note = f"Hoàn tất {done_cnt}/{target_count} người ({fail_cnt} thất bại)"
            else:
                agg_state = "FAILED"
                note = f"Thất bại tới {fail_cnt}/{target_count} người"
        elif done_cnt + await_cnt + fail_cnt >= target_count and await_cnt > 0:
            agg_state = "AWAITING_VERIFICATION"
            note = f"Đã gửi tới {target_count} người  •  Đang xác minh SHA-256 ({done_cnt}/{target_count})..."
        else:
            agg_state = "TRANSFERRING"
            note = (
                f"Đang gửi tới {target_count} người ({done_cnt}/{target_count} xong)"
                f"  •  {format_bytes(agg_current)} / {format_bytes(agg_total)}"
            )

        agg_rec = TransferRecord(
            session_id=f"batch:{batch_id}",
            direction="upload",
            peer=BROADCAST_CONV_ID,
            filename=filename,
            total_bytes=agg_total,
            current_bytes=agg_current,
            state=agg_state,
        )
        return self.upsert_file_message(
            conv_id=BROADCAST_CONV_ID,
            username=username,
            rec=agg_rec,
            status_note=note,
        )

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

    def upsert_file_record(self, record: MessageRecord) -> bool:
        """Update an existing inline file bubble in-place or append if new."""
        for row in range(len(self._items) - 1, -1, -1):
            item = self._items[row]
            if item.kind == "file" and item.session_id == record.session_id:
                self._items[row] = record
                idx = self.index(row, 0)
                self.dataChanged.emit(idx, idx, [self.RecordRole, Qt.ItemDataRole.DisplayRole])
                return False
        self.append_message(record)
        return True


class MessageBubbleDelegate(QStyledItemDelegate):
    """Custom delegate rendering grouped chat bubbles, avatars, and inline file cards."""

    open_file_requested = Signal(str)

    OUTER_MARGIN_X = 28
    OUTER_MARGIN_Y_FIRST = 10
    OUTER_MARGIN_Y_GROUPED = 3
    AVATAR_SIZE = 36
    AVATAR_GAP = 12
    WHO_GAP = 5
    PAD_X = 14
    PAD_Y = 10

    def _fonts(self, option: QStyleOptionViewItem) -> tuple[QFont, QFont, QFont, QFont]:
        body_font = QFont(option.font)
        who_font = QFont(option.font)
        who_font.setWeight(QFont.Weight.DemiBold)
        pt = body_font.pointSizeF()
        if pt > 0:
            who_font.setPointSizeF(max(8.5, pt - 0.5))

        meta_font = QFont(option.font)
        if pt > 0:
            meta_font.setPointSizeF(max(8.0, pt - 1.2))

        avatar_font = QFont(option.font)
        avatar_font.setWeight(QFont.Weight.DemiBold)
        if pt > 0:
            avatar_font.setPointSizeF(max(8.5, pt - 0.5))
        return body_font, who_font, meta_font, avatar_font

    @staticmethod
    def _is_grouped_with_prev(index: QModelIndex, record: MessageRecord) -> bool:
        model = index.model()
        if model is None or index.row() <= 0 or record.kind == "system":
            return False
        prev_idx = model.index(index.row() - 1, 0)
        prev_rec = prev_idx.data(MessageListModel.RecordRole)
        if not isinstance(prev_rec, MessageRecord) or prev_rec.kind == "system":
            return False
        return (
            prev_rec.is_outgoing == record.is_outgoing
            and prev_rec.sender == record.sender
            and prev_rec.is_broadcast == record.is_broadcast
        )

    def _file_card_dimensions(self, view_width: int) -> tuple[int, int]:
        max_w = max(240, view_width - 2 * self.OUTER_MARGIN_X - self.AVATAR_SIZE - self.AVATAR_GAP)
        card_w = min(390, max_w)
        card_h = 84
        return card_w, card_h

    def _compute_text_layout(
        self,
        option: QStyleOptionViewItem,
        index: QModelIndex,
        record: MessageRecord,
    ) -> tuple[int, int, int, bool]:
        is_grouped = self._is_grouped_with_prev(index, record)
        view_width = max(320, option.rect.width())
        gutter = self.AVATAR_SIZE + self.AVATAR_GAP
        max_bubble_w = max(180, min(620, int((view_width - gutter - 2 * self.OUTER_MARGIN_X) * 0.74)))

        body_font, who_font, meta_font, _ = self._fonts(option)
        body_fm = QFontMetrics(body_font)
        who_fm = QFontMetrics(who_font)

        who_h = 0 if is_grouped else (who_fm.height() + self.WHO_GAP)

        if record.kind == "file":
            bubble_w, bubble_h = self._file_card_dimensions(view_width)
            return bubble_w, bubble_h, who_h, is_grouped

        max_text_w = max(120, max_bubble_w - 2 * self.PAD_X)
        text_bounds = body_fm.boundingRect(
            QRect(0, 0, max_text_w, 10000),
            Qt.TextFlag.TextWordWrap | Qt.TextFlag.TextWrapAnywhere,
            record.text,
        )

        # Ensure bubble is at least wide enough so single-word bubbles still feel balanced
        bubble_w = min(max_bubble_w, max(text_bounds.width(), 24) + 2 * self.PAD_X)
        bubble_h = self.PAD_Y + text_bounds.height() + self.PAD_Y + 2
        return bubble_w, bubble_h, who_h, is_grouped

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        record = index.data(MessageListModel.RecordRole)
        if not isinstance(record, MessageRecord):
            return super().sizeHint(option, index)

        if record.kind == "system":
            return QSize(option.rect.width(), 40)

        _, bubble_h, who_h, is_grouped = self._compute_text_layout(option, index, record)
        top_margin = self.OUTER_MARGIN_Y_GROUPED if is_grouped else self.OUTER_MARGIN_Y_FIRST
        bottom_margin = 3
        content_h = who_h + bubble_h
        if not is_grouped:
            content_h = max(content_h, self.AVATAR_SIZE)
        return QSize(option.rect.width(), top_margin + content_h + bottom_margin)

    def editorEvent(
        self,
        event: QEvent,
        model: Any,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> bool:
        if event.type() == QEvent.Type.MouseButtonRelease:
            record = index.data(MessageListModel.RecordRole)
            if (
                isinstance(record, MessageRecord)
                and record.kind == "file"
                and not record.is_outgoing
                and record.file_state == "COMPLETED"
                and record.saved_path
            ):
                bubble_w, bubble_h, who_h, is_grouped = self._compute_text_layout(option, index, record)
                top_margin = self.OUTER_MARGIN_Y_GROUPED if is_grouped else self.OUTER_MARGIN_Y_FIRST
                y = option.rect.top() + top_margin + who_h
                gutter = self.AVATAR_SIZE + self.AVATAR_GAP
                x = option.rect.left() + self.OUTER_MARGIN_X + gutter
                btn_rect = QRectF(x + bubble_w - 72, y + (bubble_h - 30) / 2, 58, 30)
                if btn_rect.contains(event.position()):
                    self.open_file_requested.emit(record.saved_path)
                    return True
        return super().editorEvent(event, model, option, index)

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

        if record.kind == "system":
            self._paint_system_pill(painter, option, record)
            return

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        bubble_w, bubble_h, who_h, is_grouped = self._compute_text_layout(option, index, record)
        top_margin = self.OUTER_MARGIN_Y_GROUPED if is_grouped else self.OUTER_MARGIN_Y_FIRST
        row_y = option.rect.top() + top_margin
        gutter = self.AVATAR_SIZE + self.AVATAR_GAP

        if record.is_outgoing:
            avatar_x = option.rect.right() - self.OUTER_MARGIN_X - self.AVATAR_SIZE
            bubble_x = avatar_x - self.AVATAR_GAP - bubble_w
        else:
            avatar_x = option.rect.left() + self.OUTER_MARGIN_X
            bubble_x = avatar_x + gutter

        if not is_grouped:
            self._paint_avatar(painter, option, avatar_x, row_y, record.sender)
            self._paint_who_header(
                painter,
                option,
                bubble_x,
                row_y,
                bubble_w,
                who_h - self.WHO_GAP,
                record,
            )

        bubble_y = row_y + who_h
        if record.kind == "file":
            self._paint_file_card(
                painter, option, bubble_x, bubble_y, bubble_w, bubble_h, record
            )
            painter.restore()
            return

        bubble_rect = QRectF(bubble_x, bubble_y, bubble_w, bubble_h)
        bg_hex = PALETTE["bubble_outgoing"] if record.is_outgoing else PALETTE["bubble_incoming"]
        if record.is_outgoing:
            path = _asymmetric_rounded_path(bubble_rect, 16, 4, 16, 16)
        else:
            path = _asymmetric_rounded_path(bubble_rect, 4, 16, 16, 16)

        painter.fillPath(path, QColor(bg_hex))

        if option.state & QStyle.StateFlag.State_Selected:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(PALETTE["border_focus"]), 1.5))
            painter.drawPath(path)
        elif not record.is_outgoing:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(PALETTE["bubble_incoming_border"]), 1.0))
            painter.drawPath(path)

        body_font, _, _, _ = self._fonts(option)
        painter.setFont(body_font)
        body_color = "#FFFFFF" if record.is_outgoing else PALETTE["text_primary"]
        painter.setPen(QColor(body_color))
        text_rect = QRect(
            int(bubble_x) + self.PAD_X,
            int(bubble_y) + self.PAD_Y,
            bubble_w - 2 * self.PAD_X,
            bubble_h - 2 * self.PAD_Y,
        )
        text_opt = QTextOption(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        text_opt.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        painter.drawText(text_rect, record.text, text_opt)

        painter.restore()

    def _paint_who_header(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        x: int,
        y: int,
        bubble_w: int,
        h: int,
        record: MessageRecord,
    ) -> None:
        _, who_font, meta_font, _ = self._fonts(option)
        who_fm = QFontMetrics(who_font)
        meta_fm = QFontMetrics(meta_font)

        name_text = "Bạn" if record.is_outgoing else record.sender
        time_text = record.timestamp_str
        name_w = who_fm.horizontalAdvance(name_text)
        time_w = meta_fm.horizontalAdvance(time_text)
        gap = 8

        if record.is_outgoing:
            # Align right against bubble right edge
            right_edge = x + bubble_w
            time_x = right_edge - time_w
            name_x = time_x - gap - name_w
        else:
            name_x = x
            time_x = x + name_w + gap

        painter.setFont(who_font)
        painter.setPen(QColor(PALETTE["text_primary"]))
        painter.drawText(
            QRect(name_x, y, name_w + 4, h),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            name_text,
        )

        painter.setFont(meta_font)
        painter.setPen(QColor(PALETTE["text_muted"]))
        painter.drawText(
            QRect(time_x, y, time_w + 4, h),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            time_text,
        )

    def _paint_avatar(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        x: int,
        y: int,
        sender: str,
    ) -> None:
        painter.save()
        _, _, _, avatar_font = self._fonts(option)
        rect = QRectF(x, y, self.AVATAR_SIZE, self.AVATAR_SIZE)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(avatar_color_for(sender)))
        painter.drawEllipse(rect)

        painter.setFont(avatar_font)
        painter.setPen(QColor(PALETTE["avatar_text"]))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, initials_for(sender))
        painter.restore()

    def _paint_file_card(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        x: int,
        y: int,
        w: int,
        h: int,
        record: MessageRecord,
    ) -> None:
        painter.save()
        card_rect = QRectF(x, y, w, h)
        card_path = QPainterPath()
        card_path.addRoundedRect(card_rect, 14, 14)
        painter.fillPath(card_path, QColor(PALETTE["bg_card"]))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(PALETTE["border_card"]), 1.0))
        painter.drawPath(card_path)

        # Left 44x44 mint-soft icon box
        icon_rect = QRectF(x + 14, y + (h - 44) / 2, 44, 44)
        icon_path = QPainterPath()
        icon_path.addRoundedRect(icon_rect, 12, 12)
        painter.fillPath(icon_path, QColor(PALETTE["brand_soft"]))

        icon_name = "file" if record.is_outgoing else "download"
        px = get_icon(icon_name, PALETTE["brand_soft_text"], 20).pixmap(20, 20)
        painter.drawPixmap(int(icon_rect.x() + 12), int(icon_rect.y() + 12), px)

        # Text + progress bar region
        has_open_btn = (not record.is_outgoing) and record.file_state == "COMPLETED" and bool(record.saved_path)
        right_reserve = 74 if has_open_btn else 14
        info_x = int(icon_rect.right() + 14)
        info_w = max(80, int(x + w - right_reserve - info_x))

        body_font, who_font, meta_font, _ = self._fonts(option)
        who_fm = QFontMetrics(who_font)
        elided_name = who_fm.elidedText(record.filename or record.text, Qt.TextElideMode.ElideMiddle, info_w)
        painter.setFont(who_font)
        painter.setPen(QColor(PALETTE["text_primary"]))
        painter.drawText(QRect(info_x, y + 13, info_w, 20), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elided_name)

        pct = (
            min(1.0, record.current_bytes / record.total_bytes)
            if record.total_bytes > 0
            else (1.0 if record.file_state == "COMPLETED" else 0.0)
        )
        bar_y = y + 40
        bar_rect = QRectF(info_x, bar_y, info_w, 6)
        bar_path = QPainterPath()
        bar_path.addRoundedRect(bar_rect, 3, 3)
        painter.fillPath(bar_path, QColor(PALETTE["border_strong"]))

        if pct > 0:
            fill_rect = QRectF(info_x, bar_y, max(6.0, info_w * pct), 6)
            fill_path = QPainterPath()
            fill_path.addRoundedRect(fill_rect, 3, 3)
            fill_color = PALETTE["accent_error"] if record.file_state == "FAILED" else PALETTE["brand_primary"]
            painter.fillPath(fill_path, QColor(fill_color))

        size_str = f"{format_bytes(record.current_bytes)} / {format_bytes(record.total_bytes)}"
        if record.file_state == "COMPLETED":
            sub_text = f"{format_bytes(record.total_bytes)}  •  Đã xác minh SHA-256"
            sub_color = PALETTE["brand_soft_text"]
        elif record.file_state == "FAILED":
            sub_text = f"Thất bại: {record.file_error or 'Lỗi truyền file'}"
            sub_color = PALETTE["accent_error_text"]
        elif record.file_state == "AWAITING_VERIFICATION":
            sub_text = "Đã gửi 100%  •  Đang xác minh SHA-256..."
            sub_color = PALETTE["accent_warning"]
        elif record.is_outgoing:
            sub_text = f"Đang gửi  •  {size_str}"
            sub_color = PALETTE["text_muted"]
        else:
            sub_text = f"Đang nhận  •  {size_str}"
            sub_color = PALETTE["text_muted"]
        if record.status_note:
            sub_text = record.status_note

        painter.setFont(meta_font)
        painter.setPen(QColor(sub_color))
        painter.drawText(
            QRect(info_x, y + 52, info_w, 20),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            QFontMetrics(meta_font).elidedText(sub_text, Qt.TextElideMode.ElideRight, info_w),
        )

        if has_open_btn:
            btn_rect = QRectF(x + w - 72, y + (h - 30) / 2, 58, 30)
            btn_path = QPainterPath()
            btn_path.addRoundedRect(btn_rect, 15, 15)
            painter.fillPath(btn_path, QColor(PALETTE["brand_soft"]))
            painter.setFont(who_font)
            painter.setPen(QColor(PALETTE["brand_soft_text"]))
            painter.drawText(btn_rect, Qt.AlignmentFlag.AlignCenter, "Mở")

        painter.restore()

    def _paint_system_pill(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        record: MessageRecord,
    ) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        _, _, meta_font, _ = self._fonts(option)
        fm = QFontMetrics(meta_font)
        text_w = fm.horizontalAdvance(record.text)
        pill_w = min(option.rect.width() - 40, text_w + 38)
        pill_h = 28
        px = option.rect.left() + (option.rect.width() - pill_w) / 2
        py = option.rect.top() + (option.rect.height() - pill_h) / 2

        rect = QRectF(px, py, pill_w, pill_h)
        path = QPainterPath()
        path.addRoundedRect(rect, 14, 14)
        painter.fillPath(path, QColor(PALETTE["bg_card"]))
        painter.setPen(QPen(QColor(PALETTE["border_card"]), 1.0))
        painter.drawPath(path)

        dot_rect = QRectF(px + 12, py + 10, 8, 8)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(PALETTE["accent_warning"]))
        painter.drawEllipse(dot_rect)

        painter.setFont(meta_font)
        painter.setPen(QColor(PALETTE["text_secondary"]))
        painter.drawText(
            QRectF(px + 26, py, pill_w - 36, pill_h),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            fm.elidedText(record.text, Qt.TextElideMode.ElideRight, int(pill_w - 36)),
        )
        painter.restore()
