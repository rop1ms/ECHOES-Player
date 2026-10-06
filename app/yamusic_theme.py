# yamusic_theme.py
"""
Тема «Echoes Music» — интерфейс в духе Яндекс Музыки (полная тема, как Winamp).

Силуэт:
  ┌──────────┬──────────────────────────────────────────────┐
  │ ECHOES   │  ┌ контент (скруглённая панель) ────────────┐ │
  │ Поиск    │  │  страницы: Поиск · Моя волна · Для вас · │ │
  │ Моя волна│  │  Коллекция · Плейлист · Исполнитель ·    │ │
  │ Для вас  │  │  Текст · Настройки                       │ │
  │ Коллекция│  └──────────────────────────────────────────┘ │
  │ Настройки│                                               │
  │ плейлисты│                                               │
  │ профиль  │  ┌ плеер: обложка · кнопки · текст/очередь ┐ │
  └──────────┴──────────────────────────────────────────────┘

Оболочка (YaShell) собрана из НОВЫХ виджетов и общается с MainWindow
только через его методы (_play_index, toggle_play, next_track…), поэтому
обычная раскладка плеера при этой теме просто спрятана и возвращается
без изменений. Единственное, что переезжает, — правая панель настроек
(эквалайзер, пресеты, таймеры, темы): она живёт на странице «Настройки».
"""
from __future__ import annotations

import math
import random
import time
from pathlib import Path

from frameclock import FrameTimer
from img_load import load_pixmap, read_image
from PyQt6.QtCore import (Qt, QTimer, QSize, QRectF, QPointF, QRect, QPoint, pyqtSignal,
                          QAbstractListModel, QModelIndex, QEvent, QVariantAnimation, QEasingCurve)
from PyQt6.QtGui import (QColor, QFont, QPainter, QPainterPath, QPixmap, QIcon, QPen,
                         QLinearGradient, QRadialGradient, QImage, QFontMetrics, QBrush, QCursor, QTransform)
from PyQt6.QtWidgets import (QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                             QStackedWidget, QScrollArea, QListView, QStyledItemDelegate, QStyle,
                             QLineEdit, QSlider, QMenu, QListWidget, QListWidgetItem,
                             QSizePolicy, QAbstractItemView, QButtonGroup, QGridLayout, QComboBox,
                             QMessageBox, QCheckBox, QAbstractButton)

try:
    from lyrics import sanitize_title
except Exception:                                  # noqa: BLE001
    def sanitize_title(t):
        return t or ""

YELLOW = "#ffdb1a"
BG = "#000000"
PANEL = "#121212"
PANEL2 = "#1a1a1a"
CARD = "#232323"
TEXT = "#ffffff"
MUTED = "#8a8a8a"
LIKED = "Мне нравится"
WAVE_PL = "Моя волна"
ALBUM_PL = "Альбом"                 # очередь «играет альбом» (скрыта из списка плейлистов)
AUTO_PLAYLISTS = {WAVE_PL, "Настроение дня", ALBUM_PL}
_NO_ALBUM = {"", "unknown", "unknown album", "неизвестный альбом", "<unknown>", "none", "null",
             "youtube", "soundcloud", "single"}
FAMILIES = ["YS Text", "Inter", "Segoe UI Variable Display", "Segoe UI", "Helvetica Neue", "Arial"]


def ya_font(px: int, weight=QFont.Weight.Normal) -> QFont:
    f = QFont()
    f.setFamilies(FAMILIES)
    f.setPixelSize(int(px))
    f.setWeight(weight)
    return f


# ══════════════════════════════════════════════════════════════════════════ #
#  Тема: словарь цветов + QSS
# ══════════════════════════════════════════════════════════════════════════ #

def ya_theme_dict() -> dict:
    return {
        "bg": BG, "panel": PANEL, "panel2": PANEL2,
        "glass": "rgba(255,255,255,0.05)", "glass2": "rgba(255,255,255,0.09)",
        "text": TEXT, "muted": MUTED, "accent": YELLOW, "accent2": "#ff4fa3",
        "border": "rgba(255,255,255,0.08)", "border2": "rgba(255,255,255,0.16)",
        "danger": "#ff5c5c", "glow": "#000000", "yamusic": True,
    }


def _ui_assets() -> dict:
    """PNG для QSS: тумблер (выкл/вкл) вместо квадратной галочки и стрелка выпадающего списка."""
    d = Path.home() / ".neon_player" / "ui_cache"
    out = {k: d / f"{k}.png" for k in ("sw_off", "sw_on", "arrow")}
    if all(p.exists() for p in out.values()) and (d / "v2").exists():
        return {k: p.as_posix() for k, p in out.items()}
    try:
        d.mkdir(parents=True, exist_ok=True)
        W, H, S = 46, 26, 4                                   # рисуем в 4× и сжимаем — гладкие края
        for key, on in (("sw_off", False), ("sw_on", True)):
            pm = QPixmap(W * S, H * S)
            pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            track = QPainterPath()
            track.addRoundedRect(QRectF(0, 0, W * S, H * S), H * S / 2, H * S / 2)
            p.fillPath(track, QColor(YELLOW) if on else QColor(72, 72, 72))
            r = (H - 8) * S
            x = (W - 4) * S - r if on else 4 * S
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0) if on else QColor(236, 236, 236))
            p.drawEllipse(QRectF(x, 4 * S, r, r))
            p.end()
            pm.scaled(W, H, Qt.AspectRatioMode.IgnoreAspectRatio,
                      Qt.TransformationMode.SmoothTransformation).save(str(out[key]))
        pm = QPixmap(12 * S, 8 * S)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor("#bdbdbd"), 1.8 * S, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                      Qt.PenJoinStyle.RoundJoin))
        p.drawLine(QPointF(1.5 * S, 1.8 * S), QPointF(6 * S, 6.2 * S))
        p.drawLine(QPointF(6 * S, 6.2 * S), QPointF(10.5 * S, 1.8 * S))
        p.end()
        pm.scaled(12, 8, Qt.AspectRatioMode.IgnoreAspectRatio,
                  Qt.TransformationMode.SmoothTransformation).save(str(out["arrow"]))
        (d / "v2").write_text("1")
    except Exception as e:                                    # noqa: BLE001
        print("[ya] ui assets:", e)
    return {k: p.as_posix() for k, p in out.items()}


def ya_qss(t: dict) -> str:
    A = _ui_assets()
    return f"""
    QWidget {{ color:{TEXT}; font-family:"YS Text","Inter","Segoe UI"; font-size:13px; }}
    QMainWindow, QWidget#Root {{ background:{BG}; }}
    QDialog, QMenu {{ background:{PANEL2}; color:{TEXT}; }}
    QMenu {{ border:1px solid #2c2c2c; border-radius:10px; padding:6px; }}
    QMenu::item {{ padding:7px 18px; border-radius:6px; }}
    QMenu::item:selected {{ background:#2c2c2c; }}
    QMenu::section {{ color:{MUTED}; font-size:11px; padding:6px 12px; }}
    QToolTip {{ background:#2a2a2a; color:#fff; border:none; padding:5px 8px; border-radius:6px; }}
    QFrame#YaContent {{ background:{PANEL}; border-radius:16px; }}
    QFrame#YaPlayer {{ background:{PANEL2}; border-radius:14px; }}
    QWidget#YaSide, QWidget#YaPage, QWidget#YaPageInner {{ background:transparent; }}
    QLabel {{ background:transparent; }}
    QLabel#YaH1 {{ font-size:28px; font-weight:800; }}
    QLabel#YaH2 {{ font-size:20px; font-weight:800; }}
    QLabel#YaSub, QLabel#Sub {{ color:{MUTED}; font-size:12px; }}
    QLabel#Section {{ color:{MUTED}; font-size:11px; font-weight:700; letter-spacing:1px; }}
    QLabel#Big {{ font-size:15px; font-weight:700; }}
    QPushButton {{ background:#262626; color:{TEXT}; border:none; border-radius:13px;
                   padding:9px 16px; font-weight:600; }}
    QPushButton:hover {{ background:#303030; }}
    QPushButton:pressed {{ background:#3a3a3a; }}
    QPushButton:checked {{ background:#3a3a3a; }}
    QPushButton:disabled {{ color:#555; }}
    QPushButton#AccentBtn, QPushButton#YaYellow {{ background:{YELLOW}; color:#000; }}
    QPushButton#AccentBtn:hover, QPushButton#YaYellow:hover {{ background:#ffe55c; }}
    QPushButton#GhostBtn {{ background:transparent; border:1px solid #333; }}
    QPushButton#GhostBtn:hover {{ border-color:#555; }}
    QPushButton#YaNav {{ background:transparent; text-align:left; padding:9px 12px; border-radius:10px;
                         font-size:13px; font-weight:600; color:#d6d6d6; }}
    QPushButton#YaNav:hover {{ background:#141414; color:#fff; }}
    QPushButton#YaNav:checked {{ background:#1b1b1b; color:#fff; }}
    QPushButton#YaChip {{ background:rgba(255,255,255,0.08); border:none; border-radius:16px;
                          padding:10px 18px; font-size:13px; font-weight:700; color:#d0d0d0; }}
    QPushButton#YaChip:hover {{ background:rgba(255,255,255,0.15); color:#ffffff; }}
    QPushButton#YaChip:checked {{ background:{YELLOW}; color:#000000; }}
    QFrame#YaSegTrack {{ background:rgba(255,255,255,0.07); border:none; border-radius:22px; }}
    QPushButton#YaSeg {{ background:transparent; border:none; border-radius:18px;
                         padding:8px 20px; font-size:13px; font-weight:700; color:#b5b5b5; }}
    QPushButton#YaSeg:hover {{ color:#ffffff; background:rgba(255,255,255,0.08); }}
    QPushButton#YaSeg:checked {{ background:{YELLOW}; color:#000000; }}
    QPushButton#YaIcon {{ background:transparent; border:none; border-radius:18px; padding:0; }}
    QPushButton#YaIcon:hover {{ background:#2a2a2a; }}
    QPushButton#YaIcon:checked {{ background:#2f2f2f; }}
    QLineEdit {{ background:#1f1f1f; border:1px solid #2c2c2c; border-radius:18px; padding:8px 16px;
                 color:{TEXT}; selection-background-color:{YELLOW}; selection-color:#000; }}
    QLineEdit:focus {{ border-color:#4a4a4a; }}
    QComboBox {{ background:rgba(255,255,255,0.07); border:none; border-radius:14px; padding:9px 16px;
                 font-weight:600; }}
    QComboBox:hover {{ background:rgba(255,255,255,0.12); }}
    QComboBox::drop-down {{ border:none; width:34px; }}
    QComboBox::down-arrow {{ image:url({A["arrow"]}); width:12px; height:8px; }}
    QComboBox QAbstractItemView {{ background:{PANEL2}; border:1px solid #2c2c2c; border-radius:10px; padding:4px;
                                   selection-background-color:#2c2c2c; outline:0; }}
    QSpinBox {{ background:rgba(255,255,255,0.07); border:none; border-radius:10px; padding:5px 8px; }}
    QTextEdit, QPlainTextEdit {{ background:rgba(255,255,255,0.05); border:none; border-radius:14px;
                                 padding:10px 12px; selection-background-color:{YELLOW}; selection-color:#000; }}
    QCheckBox {{ spacing:12px; padding:3px 0; }}
    QCheckBox::indicator {{ width:46px; height:26px; border:none; background:transparent; image:url({A["sw_off"]}); }}
    QCheckBox::indicator:checked {{ image:url({A["sw_on"]}); }}
    QWidget#YaSettingsCard {{ background:#171717; border-radius:16px; }}
    QSlider::groove:horizontal {{ height:4px; background:#3a3a3a; border-radius:2px; }}
    QSlider::sub-page:horizontal {{ background:#ffffff; border-radius:2px; }}
    QSlider::handle:horizontal {{ background:#ffffff; width:12px; height:12px; margin:-4px 0; border-radius:6px; }}
    QSlider::groove:vertical {{ width:4px; background:#3a3a3a; border-radius:2px; }}
    QSlider::add-page:vertical {{ background:{YELLOW}; border-radius:2px; }}
    QSlider::handle:vertical {{ background:#ffffff; height:12px; width:12px; margin:0 -4px; border-radius:6px; }}
    QListWidget, QListView {{ background:transparent; border:none; outline:none; }}
    QListWidget::item {{ padding:6px; border-radius:8px; }}
    QListWidget::item:hover {{ background:#1c1c1c; }}
    QListWidget::item:selected {{ background:#262626; color:#fff; }}
    QScrollArea {{ background:transparent; border:none; }}
    QScrollBar:vertical {{ background:transparent; width:8px; margin:4px 2px; }}
    QScrollBar::handle:vertical {{ background:#3a3a3a; border-radius:3px; min-height:30px; }}
    QScrollBar::handle:vertical:hover {{ background:#555; }}
    QScrollBar:horizontal {{ background:transparent; height:8px; margin:2px 4px; }}
    QScrollBar::handle:horizontal {{ background:#3a3a3a; border-radius:3px; min-width:30px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width:0; height:0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background:transparent; }}
    QFrame#Panel {{ background:transparent; border:none; }}
    QTabWidget::pane {{ border:none; }}
    """


# ══════════════════════════════════════════════════════════════════════════ #
#  Иконки (рисуются кодом — без файлов и эмодзи)
# ══════════════════════════════════════════════════════════════════════════ #

_ICON_CACHE: dict = {}


def ya_icon(name: str, size: int = 22, color: str = "#ffffff") -> QIcon:
    key = (name, size, color)
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]
    dpr = 2
    pm = QPixmap(size * dpr, size * dpr)
    pm.fill(Qt.GlobalColor.transparent)
    pm.setDevicePixelRatio(dpr)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / 24.0, size / 24.0)
    col = QColor(color)
    pen = QPen(col, 1.9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    _draw_icon(p, name, col)
    p.end()
    ic = QIcon(pm)
    _ICON_CACHE[key] = ic
    return ic


def _heart_path(cx=12.0, cy=12.5, s=1.0) -> QPainterPath:
    path = QPainterPath()
    path.moveTo(cx, cy + 7 * s)
    path.cubicTo(cx - 9 * s, cy + 1 * s, cx - 8 * s, cy - 8 * s, cx - 4 * s, cy - 7.5 * s)
    path.cubicTo(cx - 2 * s, cy - 7.3 * s, cx - 0.6 * s, cy - 5.8 * s, cx, cy - 4.4 * s)
    path.cubicTo(cx + 0.6 * s, cy - 5.8 * s, cx + 2 * s, cy - 7.3 * s, cx + 4 * s, cy - 7.5 * s)
    path.cubicTo(cx + 8 * s, cy - 8 * s, cx + 9 * s, cy + 1 * s, cx, cy + 7 * s)
    return path


def _draw_icon(p: QPainter, name: str, col: QColor):
    if name == "search":
        p.drawEllipse(QRectF(4, 4, 12, 12))
        p.drawLine(QPointF(14.6, 14.6), QPointF(20, 20))
    elif name == "wave":
        c = QPointF(12, 12)
        for i in range(12):
            a = i * math.pi / 6
            r1, r2 = (3.0, 9.5) if i % 2 == 0 else (3.0, 6.5)
            p.drawLine(QPointF(c.x() + math.cos(a) * r1, c.y() + math.sin(a) * r1),
                       QPointF(c.x() + math.cos(a) * r2, c.y() + math.sin(a) * r2))
    elif name == "note":
        p.drawLine(QPointF(9, 17), QPointF(9, 5))
        p.drawLine(QPointF(9, 5), QPointF(19, 3.5))
        p.drawLine(QPointF(19, 3.5), QPointF(19, 15))
        p.drawEllipse(QRectF(4, 14.5, 5.5, 5))
        p.drawEllipse(QRectF(13.8, 12.5, 5.5, 5))
    elif name in ("heart", "collection"):
        p.drawPath(_heart_path())
    elif name == "heart_fill":
        p.setBrush(col)
        p.drawPath(_heart_path())
    elif name == "play":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        path = QPainterPath()
        path.moveTo(8, 5); path.lineTo(8, 19); path.lineTo(19, 12); path.closeSubpath()
        p.drawPath(path)
    elif name == "pause":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawRoundedRect(QRectF(7, 5, 3.6, 14), 1, 1)
        p.drawRoundedRect(QRectF(13.4, 5, 3.6, 14), 1, 1)
    elif name in ("prev", "next"):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        path = QPainterPath()
        if name == "next":
            path.moveTo(6, 5.5); path.lineTo(6, 18.5); path.lineTo(16, 12); path.closeSubpath()
            p.drawPath(path)
            p.drawRoundedRect(QRectF(16.4, 5.5, 2.4, 13), 1, 1)
        else:
            path.moveTo(18, 5.5); path.lineTo(18, 18.5); path.lineTo(8, 12); path.closeSubpath()
            p.drawPath(path)
            p.drawRoundedRect(QRectF(5.2, 5.5, 2.4, 13), 1, 1)
    elif name == "shuffle":
        p.drawLine(QPointF(4, 7), QPointF(8, 7))
        path = QPainterPath(); path.moveTo(8, 7); path.cubicTo(12, 7, 12, 17, 16, 17); path.lineTo(20, 17)
        p.drawPath(path)
        p.drawLine(QPointF(4, 17), QPointF(8, 17))
        path = QPainterPath(); path.moveTo(8, 17); path.cubicTo(12, 17, 12, 7, 16, 7); path.lineTo(20, 7)
        p.drawPath(path)
        for y in (7, 17):
            p.drawLine(QPointF(17.5, y - 2.5), QPointF(20, y)); p.drawLine(QPointF(17.5, y + 2.5), QPointF(20, y))
    elif name in ("repeat", "repeat_one"):
        path = QPainterPath()
        path.moveTo(6, 11); path.lineTo(6, 9); path.quadTo(6, 7, 8, 7); path.lineTo(18, 7)
        p.drawPath(path)
        p.drawLine(QPointF(15.5, 4.5), QPointF(18, 7)); p.drawLine(QPointF(15.5, 9.5), QPointF(18, 7))
        path = QPainterPath()
        path.moveTo(18, 13); path.lineTo(18, 15); path.quadTo(18, 17, 16, 17); path.lineTo(6, 17)
        p.drawPath(path)
        p.drawLine(QPointF(8.5, 14.5), QPointF(6, 17)); p.drawLine(QPointF(8.5, 19.5), QPointF(6, 17))
        if name == "repeat_one":
            f = ya_font(8, QFont.Weight.Black)
            p.setFont(f)
            p.drawText(QRectF(9, 8, 6, 8), Qt.AlignmentFlag.AlignCenter, "1")
    elif name == "clip":                                  # экран с кнопкой «play» — клип
        p.drawRoundedRect(QRectF(3, 5, 18, 14), 3, 3)
        tri = QPainterPath()
        tri.moveTo(10, 9)
        tri.lineTo(10, 15)
        tri.lineTo(15.5, 12)
        tri.closeSubpath()
        p.setBrush(col)
        p.drawPath(tri)
        p.setBrush(Qt.BrushStyle.NoBrush)
    elif name == "lyrics":
        for y, w in ((6, 14), (10.5, 11), (15, 14), (19.5, 8)):
            p.drawLine(QPointF(5, y), QPointF(5 + w, y))
    elif name == "queue":
        for y in (6, 12, 18):
            p.drawLine(QPointF(9, y), QPointF(20, y))
            p.setBrush(col); p.drawEllipse(QPointF(5, y), 1.2, 1.2); p.setBrush(Qt.BrushStyle.NoBrush)
    elif name == "settings":
        for y, x in ((6, 15), (12, 8), (18, 13)):
            p.drawLine(QPointF(4, y), QPointF(20, y))
            p.setBrush(QColor(0, 0, 0)); p.drawEllipse(QPointF(x, y), 2.4, 2.4); p.setBrush(Qt.BrushStyle.NoBrush)
    elif name in ("volume", "mute"):
        path = QPainterPath()
        path.moveTo(4, 9.5); path.lineTo(7.5, 9.5); path.lineTo(12, 5.5); path.lineTo(12, 18.5)
        path.lineTo(7.5, 14.5); path.lineTo(4, 14.5); path.closeSubpath()
        p.drawPath(path)
        if name == "volume":
            p.drawArc(QRectF(11, 8, 6, 8), -60 * 16, 120 * 16)
            p.drawArc(QRectF(11, 5, 10, 14), -60 * 16, 120 * 16)
        else:
            p.drawLine(QPointF(15, 9), QPointF(20, 15)); p.drawLine(QPointF(20, 9), QPointF(15, 15))
    elif name == "cover":
        p.drawRoundedRect(QRectF(4, 4, 16, 16), 3.5, 3.5)
        path = QPainterPath(); path.moveTo(6, 17); path.lineTo(10.5, 12); path.lineTo(13.5, 15)
        path.lineTo(15.5, 13); path.lineTo(18, 16)
        p.drawPath(path)
        p.drawEllipse(QPointF(15, 8.5), 1.5, 1.5)
    elif name == "plus":
        p.drawLine(QPointF(12, 5), QPointF(12, 19)); p.drawLine(QPointF(5, 12), QPointF(19, 12))
    elif name == "folder":
        path = QPainterPath(); path.moveTo(3.5, 7); path.lineTo(9, 7); path.lineTo(11, 9); path.lineTo(20.5, 9)
        path.lineTo(20.5, 18); path.lineTo(3.5, 18); path.closeSubpath()
        p.drawPath(path)
    elif name == "download":
        p.drawLine(QPointF(12, 4), QPointF(12, 15))
        p.drawLine(QPointF(7.5, 11), QPointF(12, 15.5)); p.drawLine(QPointF(16.5, 11), QPointF(12, 15.5))
        p.drawLine(QPointF(5, 19.5), QPointF(19, 19.5))
    elif name == "chevron_down":
        p.drawLine(QPointF(6, 9.5), QPointF(12, 15.5)); p.drawLine(QPointF(12, 15.5), QPointF(18, 9.5))
    elif name == "back":
        p.drawLine(QPointF(14.5, 6), QPointF(8.5, 12)); p.drawLine(QPointF(8.5, 12), QPointF(14.5, 18))
    elif name == "palette":
        p.drawEllipse(QRectF(4, 4, 16, 16))
        p.setBrush(col)
        for x, y in ((9, 9), (14.5, 8.5), (16, 13), (8.5, 14.5)):
            p.drawEllipse(QPointF(x, y), 1.3, 1.3)
    elif name == "dots":
        p.setBrush(col)
        for x in (6, 12, 18):
            p.drawEllipse(QPointF(x, 12), 1.5, 1.5)
    elif name == "album":
        p.drawEllipse(QRectF(3.5, 3.5, 17, 17))
        p.drawEllipse(QRectF(9.5, 9.5, 5, 5))
        p.drawArc(QRectF(6.5, 6.5, 11, 11), 100 * 16, 70 * 16)
    elif name == "close":
        p.drawLine(QPointF(6.5, 6.5), QPointF(17.5, 17.5))
        p.drawLine(QPointF(17.5, 6.5), QPointF(6.5, 17.5))
    elif name == "user":
        p.drawEllipse(QRectF(8, 4, 8, 8))
        p.drawArc(QRectF(4.5, 14, 15, 12), 20 * 16, 140 * 16)
    elif name == "clock":
        p.drawEllipse(QRectF(3.5, 3.5, 17, 17))
        p.drawLine(QPointF(12, 7.5), QPointF(12, 12))
        p.drawLine(QPointF(12, 12), QPointF(15.5, 14))
    elif name == "edit":
        path = QPainterPath(); path.moveTo(5, 19); path.lineTo(6, 15); path.lineTo(15.5, 5.5); path.lineTo(18.5, 8.5)
        path.lineTo(9, 18); path.closeSubpath()
        p.drawPath(path)


def icon_button(name, tip="", size=36, icon_size=22, color="#ffffff", checkable=False) -> QPushButton:
    b = QPushButton()
    b.setObjectName("YaIcon")
    b.setIcon(ya_icon(name, icon_size, color))
    b.setIconSize(QSize(icon_size, icon_size))
    b.setFixedSize(size, size)
    b.setToolTip(tip)
    b.setCheckable(checkable)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    return b


# ══════════════════════════════════════════════════════════════════════════ #
#  Обложки (кэш скруглённых картинок)
# ══════════════════════════════════════════════════════════════════════════ #

# Скруглённые обложки: кэш с бюджетом по памяти (вытесняются давно не показанные).
# Раньше держалось до 1500 штук в двойном разрешении — до полугигабайта картинок.
_COVER_CACHE: dict = {}
_COVER_BYTES = [0]
_COVER_BUDGET = 40 * 2 ** 20


def _pm_bytes(pm) -> int:
    return pm.width() * pm.height() * 4


def clear_cover_cache():
    _COVER_CACHE.clear()
    _COVER_BYTES[0] = 0
    try:
        _NO_COVER.clear()
    except NameError:
        pass


def _placeholder(size: int, seed: str = "") -> QPixmap:
    rnd = random.Random(seed)
    h1 = rnd.random()
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    g = QLinearGradient(0, 0, size, size)
    g.setColorAt(0, QColor.fromHsvF(h1, 0.55, 0.55))
    g.setColorAt(1, QColor.fromHsvF((h1 + 0.15) % 1, 0.7, 0.22))
    p.fillRect(pm.rect(), g)
    p.setPen(QPen(QColor(255, 255, 255, 90), max(1.5, size / 30)))
    s = size / 24.0
    p.scale(s, s)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawLine(QPointF(10, 16.5), QPointF(10, 7))
    p.drawLine(QPointF(10, 7), QPointF(16, 6))
    p.drawLine(QPointF(16, 6), QPointF(16, 14.5))
    p.drawEllipse(QRectF(6.5, 14.5, 4, 3.6))
    p.drawEllipse(QRectF(12.5, 12.6, 4, 3.6))
    p.end()
    return pm


def _cover_image(path: str, size: int, radius: float, circle: bool) -> QImage:
    """Скруглённая обложка как QImage — можно строить в фоновом потоке (QPixmap — только в GUI)."""
    src = read_image(path, size * 4)
    if src.isNull():
        return QImage()
    s = min(src.width(), src.height())
    src = src.copy((src.width() - s) // 2, (src.height() - s) // 2, s, s)
    n = size * 2
    sc = src.scaled(n, n, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    out = QImage(n, n, QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    clip = QPainterPath()
    if circle:
        clip.addEllipse(QRectF(0, 0, n, n))
    else:
        clip.addRoundedRect(QRectF(0, 0, n, n), radius * 2, radius * 2)
    p.setClipPath(clip)
    p.drawImage(0, 0, sc)
    p.end()
    return out


class _CoverLoader:
    """Обложки для списков и карточек — без рывков прокрутки: за кадр синхронно декодируется
    не больше ~6 мс обложек, остальные — в фоновом потоке (сначала самые свежие запросы), а
    пока на их месте заглушка; готовая обложка перерисовывает свой виджет."""
    BUDGET = 0.006

    def __init__(self):
        import collections
        import threading
        self.q = collections.deque()
        self.waiting = {}                            # ключ → [weakref виджетов]
        self.cv = threading.Condition()
        self.bridge = None
        self.thread = None
        self._frame = (0.0, 0.0)                     # (начало «кадра», потрачено)

    def sync_ok(self) -> bool:
        now = time.perf_counter()
        t0, used = self._frame
        if now - t0 > 0.03:
            self._frame = (now, 0.0)
            return True
        return used < self.BUDGET

    def spent(self, dt):
        t0, used = self._frame
        self._frame = (t0, used + dt)

    def request(self, key, path, size, radius, circle, widget):
        import weakref
        from PyQt6.QtCore import QObject

        if self.bridge is None:
            class _Bridge(QObject):
                ready = pyqtSignal(object, object)
            self.bridge = _Bridge()
            self.bridge.ready.connect(self._ready)
        lst = self.waiting.get(key)
        try:
            ref = weakref.ref(widget) if widget is not None else None
        except TypeError:
            ref = None
        if lst is not None:
            if ref is not None:
                lst.append(ref)
            return
        self.waiting[key] = [ref] if ref is not None else []
        with self.cv:
            self.q.append((key, path, size, radius, circle))
            while len(self.q) > 300:                 # ушли далеко — старые запросы не нужны
                old = self.q.popleft()
                self.waiting.pop(old[0], None)
            self.cv.notify()
        if self.thread is None:
            import threading
            self.thread = threading.Thread(target=self._run, daemon=True, name="ya-covers")
            self.thread.start()

    def _run(self):
        while True:
            with self.cv:
                while not self.q:
                    self.cv.wait()
                key, path, size, radius, circle = self.q.pop()        # сначала свежие — то, что на экране
            try:
                img = _cover_image(path, size, radius, circle)
            except Exception:                        # noqa: BLE001
                img = QImage()
            try:
                self.bridge.ready.emit(key, img)
            except RuntimeError:
                return

    def _ready(self, key, img):
        refs = self.waiting.pop(key, None)
        if img is None or img.isNull():
            _NO_COVER.add(key)
        else:
            out = QPixmap.fromImage(img)
            out.setDevicePixelRatio(2)
            _cache_put(key, out)
        for r in refs or []:
            w = r() if r is not None else None
            if w is not None:
                try:
                    w.update()
                except RuntimeError:
                    pass


_LOADER = _CoverLoader()
_NO_COVER: set = set()                               # файл есть, но не читается — заглушка, без повторов


def _cache_put(key, out):
    old = _COVER_CACHE.pop(key, None)
    if old is not None:
        _COVER_BYTES[0] -= _pm_bytes(old)
    _COVER_CACHE[key] = out
    _COVER_BYTES[0] += _pm_bytes(out)
    while _COVER_BYTES[0] > _COVER_BUDGET and len(_COVER_CACHE) > 1:
        o = _COVER_CACHE.pop(next(iter(_COVER_CACHE)))
        _COVER_BYTES[0] -= _pm_bytes(o)


def cover_pixmap(path: str, size: int, radius: float = 6, seed: str = "", circle=False, lazy=None) -> QPixmap:
    """lazy — виджет, который перерисовать, когда обложка догрузится в фоне (списки, карточки):
    тогда в кадре декодируется не больше нескольких миллисекунд обложек, остальные — заглушка."""
    key = (path or "", size, radius, circle, seed if not path else "")
    pm = _COVER_CACHE.pop(key, None)
    if pm is not None:
        _COVER_CACHE[key] = pm                       # в конец очереди — недавно показана
        return pm
    if path and key not in _NO_COVER:
        if lazy is not None and not _LOADER.sync_ok():
            if Path(path).exists():
                _LOADER.request(key, path, size, radius, circle, lazy)
                return cover_pixmap("", size, radius, seed or path, circle)
        t0 = time.perf_counter()
        img = _cover_image(path, size, radius, circle) if Path(path).exists() else QImage()
        _LOADER.spent(time.perf_counter() - t0)
        if not img.isNull():
            out = QPixmap.fromImage(img)
            out.setDevicePixelRatio(2)
            _cache_put(key, out)
            return out
        _NO_COVER.add(key)
    src = QPixmap()
    if src.isNull():
        src = _placeholder(size * 2, seed or path or "x")
    s = min(src.width(), src.height())
    src = src.copy((src.width() - s) // 2, (src.height() - s) // 2, s, s)
    dpr = 2
    sc = src.scaled(size * dpr, size * dpr, Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
    out = QPixmap(size * dpr, size * dpr)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    clip = QPainterPath()
    if circle:
        clip.addEllipse(QRectF(0, 0, size * dpr, size * dpr))
    else:
        clip.addRoundedRect(QRectF(0, 0, size * dpr, size * dpr), radius * dpr, radius * dpr)
    p.setClipPath(clip)
    p.drawPixmap(0, 0, sc)
    p.end()
    out.setDevicePixelRatio(dpr)
    _COVER_CACHE[key] = out
    _COVER_BYTES[0] += _pm_bytes(out)
    while _COVER_BYTES[0] > _COVER_BUDGET and len(_COVER_CACHE) > 1:
        old = _COVER_CACHE.pop(next(iter(_COVER_CACHE)))
        _COVER_BYTES[0] -= _pm_bytes(old)
    return out


def cover_colors(path: str):
    """Два цвета обложки для фона «Моей волны»: насыщенный и второй."""
    img = read_image(path, 64) if path and Path(path).exists() else QImage()
    if img.isNull():
        return QColor("#ffd60a"), QColor("#e0218a")
    img = img.scaled(12, 12, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    cols = []
    for y in range(img.height()):
        for x in range(img.width()):
            c = QColor(img.pixel(x, y))
            h, s, v, _ = c.getHsvF()
            cols.append((s * v, h, s, v))
    cols.sort(reverse=True)
    best = cols[0]
    h1 = best[1] if best[1] >= 0 else 0.13
    other = next((c for c in cols[1:] if c[1] >= 0 and min(abs(c[1] - h1), 1 - abs(c[1] - h1)) > 0.12), None)
    h2 = other[1] if other else (h1 + 0.8) % 1.0
    return QColor.fromHsvF(h1, min(1.0, best[2] * 1.2 + 0.2), 1.0), QColor.fromHsvF(h2, 0.85, 0.9)


def _fmt(sec) -> str:
    sec = max(0, int(float(sec or 0)))
    return f"{sec // 60:02d}:{sec % 60:02d}"


def _artist_main(a: str) -> str:
    a = (a or "").replace(" - Topic", "").strip()
    for sep in (",", " & ", " feat", " ft.", " x "):
        if sep in a:
            a = a.split(sep)[0]
    return a.strip()


# ══════════════════════════════════════════════════════════════════════════ #
#  Список треков (модель + делегат + вид)
# ══════════════════════════════════════════════════════════════════════════ #

class _RowBox:
    """Обёртка над строкой списка: Qt при передаче dict делает его КОПИЮ, а объект-обёртка проходит как есть."""
    __slots__ = ("d",)

    def __init__(self, d):
        self.d = d


def _row(idx):
    v = idx.data(Qt.ItemDataRole.UserRole)
    return v.d if isinstance(v, _RowBox) else v


class TrackModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[dict] = []        # {"t": track, "ctx": "library"|имя, "i": индекс}

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = list(rows)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def data(self, idx, role=Qt.ItemDataRole.DisplayRole):
        if not idx.isValid() or not (0 <= idx.row() < len(self.rows)):
            return None
        r = self.rows[idx.row()]
        if role == Qt.ItemDataRole.UserRole:
            return _RowBox(r)              # не dict: Qt копирует словари, а нам нужна сама строка (состояние превью/загрузки)
        if role == Qt.ItemDataRole.DisplayRole:
            return r["t"].get("title", "")
        return None


class TrackDelegate(QStyledItemDelegate):
    ROW_H = 56

    def __init__(self, shell, parent=None):
        super().__init__(parent)
        self.shell = shell

    def sizeHint(self, opt, idx):
        return QSize(opt.rect.width(), self.ROW_H)

    @staticmethod
    def zones(r: QRect):
        heart = QRect(r.right() - 120, r.top() + 14, 28, 28)
        cover = QRect(r.left() + 10, r.top() + 8, 40, 40)
        return heart, cover

    @staticmethod
    def dots_zone(r: QRect):
        return QRect(r.right() - 156, r.top() + 14, 28, 28)

    @staticmethod
    def preview_zone(r: QRect):
        """Кнопка «30 с» (послушать до скачивания) — только у треков, которых нет в коллекции."""
        return QRect(r.right() - 270, r.top() + 14, 86, 28)

    def paint(self, p: QPainter, opt, idx):
        row = _row(idx)
        if not row:
            return
        t = row["t"]
        r = opt.rect
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        hover = bool(opt.state & QStyle.StateFlag.State_MouseOver)
        sel = bool(opt.state & QStyle.StateFlag.State_Selected)
        remote = bool(row.get("remote"))                       # трек альбома, которого ещё нет в коллекции
        owned = bool(row.get("owned"))                         # скачанный — подсвечиваем зелёным
        cur = self.shell.is_current(t) and not remote
        if owned:
            path = QPainterPath()
            path.addRoundedRect(QRectF(r.adjusted(2, 2, -2, -2)), 10, 10)
            p.fillPath(path, QColor(61, 220, 132, 26))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(61, 220, 132, 190))
            p.drawRoundedRect(QRectF(r.left() + 2, r.top() + 12, 3, r.height() - 24), 1.5, 1.5)
        if hover or sel:
            path = QPainterPath()
            path.addRoundedRect(QRectF(r.adjusted(2, 2, -2, -2)), 10, 10)
            p.fillPath(path, QColor(255, 255, 255, 22 if sel else 14))
        if remote:
            p.setOpacity(0.5 if not hover else 0.8)
        heart_r, cov_r = self.zones(r)
        dots_r = self.dots_zone(r)
        view = getattr(opt, "widget", None)
        pm = cover_pixmap(t.get("cover", ""), 40, 6, seed=t.get("path", "") or t.get("title", ""),
                          lazy=view.viewport() if hasattr(view, "viewport") else view)
        p.drawPixmap(cov_r, pm)
        if hover or cur:
            p.fillRect(cov_r, QColor(0, 0, 0, 110))
            ic = ya_icon("download" if remote else
                         ("pause" if (cur and self.shell.is_playing()) else "play"), 18, "#ffffff")
            ic.paint(p, cov_r.adjusted(11, 11, -11, -11))
        tx = cov_r.right() + 14
        tw = dots_r.left() - tx - 10
        title = sanitize_title(t.get("title", "") or "Без названия")
        artist = t.get("artist") or "Неизвестный исполнитель"
        f = ya_font(14, QFont.Weight.DemiBold)
        p.setFont(f)
        p.setPen(QColor(YELLOW) if cur else QColor(TEXT))
        fm = QFontMetrics(f)
        p.drawText(QRect(tx, r.top() + 9, tw, 20), Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(title, Qt.TextElideMode.ElideRight, tw))
        f2 = ya_font(12)
        p.setFont(f2)
        p.setPen(QColor(MUTED))
        p.drawText(QRect(tx, r.top() + 29, tw, 18), Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(f2).elidedText(artist, Qt.TextElideMode.ElideRight, tw))
        if hover:
            # «⋯» — все действия с треком (то же, что правая кнопка)
            ya_icon("dots", 20, "#cfcfcf").paint(p, dots_r.adjusted(4, 4, -4, -4))
        if remote and (row.get("dl") or ("",))[0] not in ("queued", "progress", "retry"):
            pz = self.preview_zone(r)
            pv = row.get("pv") or ("idle", 0)
            active = pv[0] in ("playing", "paused", "loading")
            p.setOpacity(1.0)
            pill = QPainterPath()
            pill.addRoundedRect(QRectF(pz), 14, 14)
            if active:
                p.fillPath(pill, QColor(255, 219, 26, 46))
            else:
                p.fillPath(pill, QColor(255, 255, 255, 30 if hover else 18))
            col = YELLOW if active else "#d6d6d6"
            if pv[0] == "playing" and not hover:
                # живой «эквалайзер»: видно, что звучит; при наведении — значок паузы
                import time as _t
                ph = _t.monotonic() * 7.0
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(YELLOW))
                for k in range(3):
                    hb = 4 + 8 * abs(math.sin(ph + k * 1.7))
                    p.drawRoundedRect(QRectF(pz.left() + 11 + k * 5, pz.top() + 14 - hb / 2, 3, hb), 1.5, 1.5)
            else:
                ya_icon("pause" if pv[0] == "playing" else "play", 14, col).paint(
                    p, QRect(pz.left() + 10, pz.top() + 7, 14, 14))
            p.setFont(ya_font(11, QFont.Weight.Bold))
            p.setPen(QColor(col))
            txt = ("…" if pv[0] == "loading" else f"{int(pv[1]) + 1} с" if pv[0] in ("playing", "paused")
                   else "нет" if pv[0] == "error" else "30 с")
            p.drawText(QRect(pz.left() + 28, pz.top(), pz.width() - 32, pz.height()),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, txt)
            p.setOpacity(0.5 if not hover else 0.8)
        if remote:
            st = row.get("dl") or ("", 0)
            p.setFont(ya_font(11, QFont.Weight.DemiBold))
            p.setPen(QColor(YELLOW) if st[0] in ("queued", "progress", "retry") else
                     QColor("#ff6b6b") if st[0] == "error" else QColor("#bdbdbd"))
            label = {"queued": "в очереди", "progress": f"{int(st[1])}%", "retry": "ещё раз…",
                     "error": "ошибка"}.get(st[0], "")
            if label:
                p.drawText(QRect(r.right() - 200, r.top(), 112, r.height()),
                           Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, label)
            else:
                ya_icon("download", 20, "#bdbdbd").paint(p, heart_r.adjusted(4, 4, -4, -4))
        else:
            liked = self.shell.is_liked(t)
            if liked or hover:
                ya_icon("heart_fill" if liked else "heart", 20, "#ffffff" if liked else "#9a9a9a").paint(
                    p, heart_r.adjusted(4, 4, -4, -4))
        p.setFont(ya_font(12))
        p.setPen(QColor(MUTED))
        dur = float(t.get("duration") or 0)
        p.drawText(QRect(r.right() - 82, r.top(), 64, r.height()),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, _fmt(dur))
        p.restore()


class SelectionBar(QFrame):
    """Панель над списком, когда выделено несколько треков: что с ними сделать."""

    def __init__(self, shell, view, parent=None):
        super().__init__(parent)
        self.shell, self.view = shell, view
        self.setObjectName("YaSelBar")
        self.setStyleSheet("QFrame#YaSelBar{background:#2a2a2a;border-radius:12px;}")
        h = QHBoxLayout(self)
        h.setContentsMargins(14, 6, 8, 6)
        h.setSpacing(8)
        self.lbl = QLabel("")
        self.lbl.setStyleSheet("font-weight:700;")
        h.addWidget(self.lbl)
        h.addStretch(1)
        self.b_add = QPushButton("＋ В плейлист")
        self.b_add.clicked.connect(self._add)
        self.b_remove = QPushButton("Убрать из плейлиста")
        self.b_remove.clicked.connect(lambda: self.shell.remove_rows(self.view.selected_rows()))
        self.b_delete = QPushButton("Удалить из плеера…")
        self.b_delete.setObjectName("YaYellow")
        self.b_delete.clicked.connect(lambda: self.shell.win.remove_tracks_everywhere(
            [r["t"] for r in self.view.selected_rows()], parent=self))
        self.b_clear = QPushButton("Снять выделение")
        self.b_clear.clicked.connect(self.view.clearSelection)
        for b in (self.b_add, self.b_remove, self.b_delete, self.b_clear):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            h.addWidget(b)
        self.hide()
        view.selectionModel().selectionChanged.connect(self._update)
        view.model_.modelReset.connect(self._update)

    def _update(self, *_):
        rows = self.view.selected_rows()
        n = len(rows)
        if n < 2:
            self.hide()
            return
        self.lbl.setText(f"Выделено: {n}")
        self.b_remove.setVisible(self.view._editable(rows[0]))
        self.show()

    def _add(self):
        tracks = [r["t"] for r in self.view.selected_rows()]
        m = QMenu(self)
        for name in self.shell.user_playlists():
            m.addAction(name, lambda nm=name: self.shell.win.playlist_add_tracks(nm, tracks))
        m.addSeparator()
        m.addAction("Новый плейлист…", lambda: self.shell.add_many_to_new_playlist(tracks))
        m.exec(self.b_add.mapToGlobal(self.b_add.rect().bottomLeft()))


class TrackListView(QListView):
    """Список треков в стиле Я.Музыки: клик по обложке / двойной клик —
    играть, сердце — «Мне нравится», ПКМ — меню."""

    def __init__(self, shell, parent=None):
        super().__init__(parent)
        self.shell = shell
        self.model_ = TrackModel(self)
        self.setModel(self.model_)
        self.setItemDelegate(TrackDelegate(shell, self))
        self.setMouseTracking(True)
        self.setUniformItemSizes(True)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Ctrl+клик / Shift+клик / Ctrl+A — выделить много треков сразу
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.doubleClicked.connect(lambda idx: self._play(idx))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._auto_height = False
        self._drag_from = None          # (строка, y нажатия) — для перетаскивания в плейлисте
        self._drag_on = False
        self._drop_at = -1
        from PyQt6.QtGui import QShortcut, QKeySequence
        sc = QShortcut(QKeySequence(Qt.Key.Key_Delete), self)
        sc.setContext(Qt.ShortcutContext.WidgetShortcut)
        sc.activated.connect(self._delete_key)

    def set_rows(self, rows):
        self._all_rows = list(rows)
        self._apply_rows()

    def set_filter(self, q: str):
        """Поиск внутри списка: каждое слово запроса должно быть в «исполнитель + название + альбом»."""
        self.filter_q = (q or "").strip()
        self._apply_rows()

    def set_pred(self, fn):
        """Дополнительное условие по треку (фильтры «жанр» / «настроение»); None — без условия."""
        self.pred = fn
        self._apply_rows()

    def _apply_rows(self):
        rows = getattr(self, "_all_rows", [])
        q = getattr(self, "filter_q", "")
        pred = getattr(self, "pred", None)
        if q:
            from search_util import track_matches
            rows = [r for r in rows if track_matches(q, r["t"])]
        if pred is not None:
            rows = [r for r in rows if pred(r["t"])]
        self.model_.set_rows(rows)
        if self._auto_height:
            self.setFixedHeight(max(60, len(rows) * TrackDelegate.ROW_H + 6))

    def set_auto_height(self, on=True):
        """Высота по содержимому — список внутри прокручиваемой страницы."""
        self._auto_height = on
        if on:
            self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def wheelEvent(self, e):
        if self._auto_height:
            e.ignore()                     # колесо — родительской странице
            return
        super().wheelEvent(e)

    def _play(self, idx):
        row = _row(idx)
        if row:
            self.shell.play_row(row)

    def selected_rows(self) -> list:
        """Выделенные строки (dict-ы модели) в порядке списка."""
        idxs = sorted({i.row() for i in self.selectionModel().selectedIndexes()})
        return [self.model_.rows[i] for i in idxs if 0 <= i < len(self.model_.rows)]

    def _editable(self, row) -> bool:
        """Строка из пользовательского плейлиста — её можно двигать/убирать."""
        return bool(row) and not getattr(self, "filter_q", "") and getattr(self, "pred", None) is None and row.get("ctx") not in ("library", None) \
            and row["ctx"] not in AUTO_PLAYLISTS and row["ctx"] in self.shell.win.playlists   # при поиске порядок не двигаем

    def mousePressEvent(self, e):
        idx = self.indexAt(e.position().toPoint())
        self._drag_from, self._drag_on = None, False
        if idx.isValid() and e.button() == Qt.MouseButton.LeftButton:
            heart, cov = TrackDelegate.zones(self.visualRect(idx))
            dots = TrackDelegate.dots_zone(self.visualRect(idx))
            pt = e.position().toPoint()
            row = _row(idx)
            if dots.contains(pt):
                self.setCurrentIndex(idx)
                self._menu(pt)
                return
            if row.get("remote") and TrackDelegate.preview_zone(self.visualRect(idx)).contains(pt):
                self.shell.preview_row(row, self)
                return
            if self._editable(row):
                self._drag_from = (idx.row(), pt.y())
            if heart.contains(pt):
                if row.get("remote"):
                    self.shell.download_remote(row, self)
                    return
                self.shell.toggle_like(row["t"])
                self.viewport().update()
                return
            if cov.contains(pt):
                if self.shell.is_current(row["t"]):
                    self.shell.win.toggle_play()
                else:
                    self.shell.play_row(row)
                self.viewport().update()
                return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag_from is not None and (e.buttons() & Qt.MouseButton.LeftButton):
            y = e.position().toPoint().y()
            if not self._drag_on and abs(y - self._drag_from[1]) > 8:
                self._drag_on = True
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
            if self._drag_on:
                n = self.model_.rowCount()
                tgt = int(round((y + self.verticalScrollBar().value()) / TrackDelegate.ROW_H)) \
                    if not self._auto_height else int(round(y / TrackDelegate.ROW_H))
                self._drop_at = max(0, min(n, tgt))
                if y < 20:
                    self.verticalScrollBar().setValue(self.verticalScrollBar().value() - 12)
                elif y > self.viewport().height() - 20:
                    self.verticalScrollBar().setValue(self.verticalScrollBar().value() + 12)
                self.viewport().update()
                return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._drag_on and self._drag_from is not None:
            src = self._drag_from[0]
            dst = self._drop_at
            self._drag_from, self._drag_on, self._drop_at = None, False, -1
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.viewport().update()
            sel = sorted({i.row() for i in self.selectionModel().selectedIndexes()})
            rows = sel if (src in sel and len(sel) > 1) else [src]
            if dst >= 0 and not (len(rows) == 1 and dst in (src, src + 1)):
                ctx = self.model_.rows[src]["ctx"]
                before = sum(1 for r in rows if r < dst)       # позиция в списке без перемещаемых
                self.shell.move_rows(ctx, [self.model_.rows[r]["i"] for r in rows], dst - before)
            return
        self._drag_from = None
        super().mouseReleaseEvent(e)

    def paintEvent(self, e):
        super().paintEvent(e)
        if self._drag_on and self._drop_at >= 0:
            p = QPainter(self.viewport())
            y = self._drop_at * TrackDelegate.ROW_H - self.verticalScrollBar().value()
            p.setPen(QPen(QColor(YELLOW), 3))
            p.drawLine(8, y, self.viewport().width() - 8, y)
            p.setBrush(QColor(YELLOW))
            p.drawEllipse(QPointF(8, y), 4, 4)
            p.end()

    def _delete_key(self):
        rows = self.selected_rows()
        if not rows:
            idx = self.currentIndex()
            if not idx.isValid():
                return
            rows = [_row(idx)]
        if self._editable(rows[0]):
            self.shell.remove_rows(rows)
        else:
            self.shell.win.remove_tracks_everywhere([r["t"] for r in rows], parent=self)

    def _menu(self, pos):
        idx = self.indexAt(pos)
        if not idx.isValid():
            return
        row = _row(idx)
        if row and row.get("remote"):
            m = QMenu(self)
            m.addAction("Послушать 30 секунд", lambda: self.shell.preview_row(row, self))
            m.addAction("Скачать", lambda: self.shell.download_remote(row, self))
            m.addAction("Скачать и слушать", lambda: self.shell.download_remote(row, self, play=True))
            m.exec(self.viewport().mapToGlobal(pos))
            return
        sel = [r for r in self.selected_rows() if not r.get("remote")]
        if len(sel) > 1 and any(r is row for r in sel):
            self._batch_menu(sel, pos)
            return
        t = row["t"]
        m = QMenu(self)
        m.addAction("Слушать", lambda: self.shell.play_row(row))
        m.addAction("Играть следующим", lambda: self.shell.win.queue_add([t], next_=True))
        m.addAction("＋  Добавить в очередь", lambda: self.shell.win.queue_add([t]))
        m.addAction("Теги, обложка и текст…", lambda: self.shell.win.edit_track_tags(t))
        if hasattr(self.shell.win, "export_effects"):
            m.addAction("Экспорт с эффектами…", lambda: self.shell.win.export_effects([t]))
        m.addAction("Убрать из «Мне нравится»" if self.shell.is_liked(t) else "Мне нравится",
                    lambda: self.shell.toggle_like(t))
        sub = m.addMenu("Добавить в плейлист")
        for name in self.shell.user_playlists():
            sub.addAction(name, lambda n=name: self.shell.add_to_playlist(t, n))
        sub.addSeparator()
        sub.addAction("Новый плейлист…", lambda: self.shell.add_to_new_playlist(t))
        a = _artist_main(t.get("artist", ""))
        if a:
            m.addAction(f"Исполнитель: {a}", lambda: self.shell.open_artist(a))
        alb = " ".join(str(t.get("album") or "").split())
        if alb.lower() not in _NO_ALBUM and row.get("ctx") != ALBUM_PL:
            m.addAction(f"Альбом: {alb}", lambda: self.shell.open_album(alb.lower()))
        if self._editable(row):
            m.addSeparator()
            n = len(self.shell.win.playlists[row["ctx"]].get("tracks", []))
            i = row["i"]
            a = m.addAction("Выше", lambda: self.shell.move_rows(row["ctx"], [i], i - 1))
            a.setEnabled(i > 0)
            a = m.addAction("Ниже", lambda: self.shell.move_rows(row["ctx"], [i], i + 1))
            a.setEnabled(i < n - 1)
            a = m.addAction("В начало", lambda: self.shell.move_rows(row["ctx"], [i], 0))
            a.setEnabled(i > 0)
            a = m.addAction("В конец", lambda: self.shell.move_rows(row["ctx"], [i], n - 1))
            a.setEnabled(i < n - 1)
            m.addSeparator()
            m.addAction("Убрать из этого плейлиста", lambda: self.shell.remove_from_playlist(row))
        else:
            m.addSeparator()
        m.addAction("    Удалить из плеера…", lambda: self.shell.win.remove_tracks_everywhere([t], parent=self))
        m.exec(self.viewport().mapToGlobal(pos))

    def _batch_menu(self, rows, pos):
        n = len(rows)
        tracks = [r["t"] for r in rows]
        m = QMenu(self)
        sec = m.addAction(f"Выделено треков: {n}")
        sec.setEnabled(False)
        m.addAction("Слушать первый", lambda: self.shell.play_row(rows[0]))
        m.addAction(f"Играть следующими ({n})", lambda: self.shell.win.queue_add(tracks, next_=True))
        m.addAction(f"＋  Добавить в очередь ({n})", lambda: self.shell.win.queue_add(tracks))
        if hasattr(self.shell.win, "export_effects"):
            m.addAction(f"Экспорт с эффектами ({n})…", lambda: self.shell.win.export_effects(tracks))
        sub = m.addMenu(f"＋  Добавить в плейлист ({n})")
        for name in self.shell.user_playlists():
            sub.addAction(name, lambda nm=name: self.shell.win.playlist_add_tracks(nm, tracks))
        sub.addSeparator()
        sub.addAction("Новый плейлист…", lambda: self.shell.add_many_to_new_playlist(tracks))
        m.addSeparator()
        if self._editable(rows[0]):
            m.addAction(f"Убрать из этого плейлиста ({n})", lambda: self.shell.remove_rows(rows))
        m.addAction(f"    Удалить из плеера ({n})…",
                    lambda: self.shell.win.remove_tracks_everywhere(tracks, parent=self))
        m.addSeparator()
        m.addAction("Снять выделение", self.clearSelection)
        m.exec(self.viewport().mapToGlobal(pos))


# ══════════════════════════════════════════════════════════════════════════ #
#  Карточки
# ══════════════════════════════════════════════════════════════════════════ #

class Card(QWidget):
    """Квадратная обложка + подпись (или круглая — исполнитель, или «микс»)."""
    clicked = pyqtSignal()

    def __init__(self, title, subtitle="", cover="", size=150, circle=False, mix=None,
                 seed="", parent=None):
        super().__init__(parent)
        self.title, self.subtitle, self.cover = title, subtitle, cover
        self.size_ = size
        self.circle = circle
        self.mix = mix                    # (QColor, QColor) — градиентная «подборка»
        self.seed = seed or title
        self._hover = False
        self.setFixedSize(size, size + (46 if subtitle else 30))
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = self.size_
        r = QRectF(0, 0, s, s)
        if self.mix:
            c1, c2 = self.mix
            path = QPainterPath(); path.addRoundedRect(r, 12, 12)
            p.setClipPath(path)
            p.fillRect(r, QColor(20, 20, 20))
            for (cx, cy, rad, col) in ((0.3, 0.35, 0.8, c1), (0.8, 0.75, 0.7, c2), (0.75, 0.2, 0.45, c1.lighter(130))):
                g = QRadialGradient(QPointF(cx * s, cy * s), rad * s)
                cc = QColor(col); cc.setAlpha(230)
                g.setColorAt(0, cc)
                cc2 = QColor(col); cc2.setAlpha(0)
                g.setColorAt(1, cc2)
                p.fillRect(r, g)
            p.setClipping(False)
            p.setPen(QColor(255, 255, 255))
            p.setFont(ya_font(int(s * 0.12), QFont.Weight.Black))
            p.drawText(r.adjusted(10, 10, -10, -10),
                       Qt.AlignmentFlag.AlignLeft.value | Qt.AlignmentFlag.AlignBottom.value
                       | Qt.TextFlag.TextWordWrap.value, self.title)
        else:
            pm = cover_pixmap(self.cover, s, 10, seed=self.seed, circle=self.circle, lazy=self)
            p.drawPixmap(0, 0, pm)
        if self._hover:
            path = QPainterPath()
            if self.circle:
                path.addEllipse(r)
            else:
                path.addRoundedRect(r, 10, 10)
            p.fillPath(path, QColor(0, 0, 0, 70))
            br = QRectF(s - 52, s - 52, 42, 42) if not self.circle else QRectF(s / 2 - 22, s / 2 - 22, 44, 44)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(YELLOW))
            p.drawEllipse(br)
            ya_icon("play", 20, "#000000").paint(p, br.adjusted(11, 11, -10, -11).toRect())
        if not self.mix:
            p.setPen(QColor(TEXT))
            f = ya_font(13, QFont.Weight.DemiBold)
            p.setFont(f)
            al = Qt.AlignmentFlag.AlignHCenter if self.circle else Qt.AlignmentFlag.AlignLeft
            p.drawText(QRectF(0, s + 6, s, 18), al | Qt.AlignmentFlag.AlignVCenter,
                       QFontMetrics(f).elidedText(self.title, Qt.TextElideMode.ElideRight, s))
            if self.subtitle:
                f2 = ya_font(12)
                p.setFont(f2)
                p.setPen(QColor(MUTED))
                p.drawText(QRectF(0, s + 25, s, 16), al | Qt.AlignmentFlag.AlignVCenter,
                           QFontMetrics(f2).elidedText(self.subtitle, Qt.TextElideMode.ElideRight, s))
        else:
            p.setPen(QColor(TEXT))
            f = ya_font(13, QFont.Weight.DemiBold)
            p.setFont(f)
            p.drawText(QRectF(0, s + 6, s, 18), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                       QFontMetrics(f).elidedText(self.subtitle or "", Qt.TextElideMode.ElideRight, s))
        p.end()


class HRow(QScrollArea):
    """Горизонтальная лента карточек; колесо мыши листает вбок."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.inner = QWidget()
        self.inner.setObjectName("YaPageInner")
        self.lay = QHBoxLayout(self.inner)
        self.lay.setContentsMargins(0, 0, 0, 6)
        self.lay.setSpacing(16)
        self.lay.addStretch(1)
        self.setWidget(self.inner)
        self.setFrameShape(QFrame.Shape.NoFrame)

    def clear(self):
        while self.lay.count() > 1:
            it = self.lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

    def add(self, w):
        self.lay.insertWidget(self.lay.count() - 1, w)
        self.setFixedHeight(w.height() + 16)

    def wheelEvent(self, e):
        sb = self.horizontalScrollBar()
        if sb.maximum() > 0 and abs(e.angleDelta().y()) > abs(e.angleDelta().x()):
            # вертикальное колесо: если ленту есть куда листать — листаем её
            v = sb.value() - e.angleDelta().y()
            if 0 <= v <= sb.maximum() or (0 < sb.value() < sb.maximum()):
                sb.setValue(max(0, min(sb.maximum(), v)))
                e.accept()
                return
        e.ignore()


def section_title(text, on_more=None) -> QWidget:
    w = QWidget()
    w.setObjectName("YaPageInner")
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 18, 0, 8)
    lay.setSpacing(6)
    lbl = QLabel(text)
    lbl.setObjectName("YaH2")
    lay.addWidget(lbl)
    if on_more is not None:
        b = QPushButton("›")
        b.setObjectName("YaIcon")
        b.setFixedSize(26, 26)
        b.setStyleSheet("font-size:20px;color:#8a8a8a;")
        b.clicked.connect(on_more)
        lay.addWidget(b)
    lay.addStretch(1)
    return w


def chip(text, checkable=True) -> QPushButton:
    b = QPushButton(text)
    b.setObjectName("YaChip")
    b.setCheckable(checkable)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    return b


def seg_group(*buttons) -> QFrame:
    """Сегментированный переключатель: тёмная капсула, внутри кнопки; выбранная — жёлтая."""
    f = QFrame()
    f.setObjectName("YaSegTrack")
    f.setFixedHeight(44)
    lay = QHBoxLayout(f)
    lay.setContentsMargins(4, 4, 4, 4)
    lay.setSpacing(2)
    for b in buttons:
        b.setObjectName("YaSeg")
        b.setFixedHeight(36)
        lay.addWidget(b)
    return f


class Switch(QAbstractButton):
    """Современный тумблер с плавным переездом ползунка."""

    def __init__(self, checked=False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setFixedSize(52, 30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._pos = 1.0 if checked else 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(lambda v: (setattr(self, "_pos", float(v)), self.update()))
        self.toggled.connect(self._on_toggled)

    def _on_toggled(self, on):
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        off, on = QColor(70, 70, 70), QColor(YELLOW)
        t = self._pos
        col = QColor(int(off.red() + (on.red() - off.red()) * t), int(off.green() + (on.green() - off.green()) * t),
                     int(off.blue() + (on.blue() - off.blue()) * t))
        track = QPainterPath()
        track.addRoundedRect(QRectF(0, 0, W, H), H / 2, H / 2)
        p.fillPath(track, col)
        r = H - 8
        x = 4 + (W - 8 - r) * t
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0) if t > 0.5 else QColor(235, 235, 235))
        p.drawEllipse(QRectF(x, 4, r, r))
        p.end()


class Page(QScrollArea):
    """Прокручиваемая страница контента."""

    def __init__(self, parent=None, margins=(32, 22, 32, 24)):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.inner = QWidget()
        self.inner.setObjectName("YaPage")
        self.v = QVBoxLayout(self.inner)
        self.v.setContentsMargins(*margins)
        self.v.setSpacing(4)
        self.setWidget(self.inner)

    def clear(self):
        while self.v.count():
            it = self.v.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
            elif it.layout() is not None:
                _clear_layout(it.layout())


def _clear_layout(lay):
    while lay.count():
        it = lay.takeAt(0)
        if it.widget() is not None:
            it.widget().deleteLater()
        elif it.layout() is not None:
            _clear_layout(it.layout())


# ══════════════════════════════════════════════════════════════════════════ #
#  Логотип и мелкие виджеты
# ══════════════════════════════════════════════════════════════════════════ #

class Logo(QWidget):
    """Колючая «звезда» + ECHOES (вместо «Яндекс Музыки»)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(54)
        self.setMinimumWidth(150)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(24, 27)
        path = QPainterPath()
        n = 14
        for i in range(n * 2):
            a = i * math.pi / n - math.pi / 2
            r = 15 if i % 2 == 0 else (6.5 if i % 4 else 8.5)
            pt = QPointF(c.x() + math.cos(a) * r, c.y() + math.sin(a) * r)
            if i == 0:
                path.moveTo(pt)
            else:
                path.lineTo(pt)
        path.closeSubpath()
        g = QRadialGradient(c, 16)
        g.setColorAt(0, QColor("#fff27a"))
        g.setColorAt(0.6, QColor(YELLOW))
        g.setColorAt(1, QColor("#b8e600"))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawPath(path)
        p.setPen(QColor(YELLOW))
        f = ya_font(22, QFont.Weight.Black)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.5)
        p.setFont(f)
        p.drawText(QRectF(46, 6, 200, 42), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, "ECHOES")
        p.end()


class ProgressLine(QWidget):
    """Полоска воспроизведения: скруглённая, при наведении плавно толстеет, под курсором —
    «призрак» перемотки и время в подсказке, ползунок со свечением. Клик/перетаскивание — перемотка."""
    seek_to = pyqtSignal(float)          # доля 0..1
    PAD = 14

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(20)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.frac = 0.0
        self.dur = 0.0
        self._hover = False
        self._drag = None
        self._hx = None
        self._grow = 0.0
        self._bubble = None
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(lambda v: (setattr(self, "_grow", float(v)), self.update()))

    def set_state(self, frac, dur):
        self.frac = max(0.0, min(1.0, frac))
        self.dur = dur
        self.update()

    def _animate(self, to):
        self._anim.stop()
        self._anim.setStartValue(self._grow)
        self._anim.setEndValue(to)
        self._anim.start()

    def _f(self, x):
        return max(0.0, min(1.0, (x - self.PAD) / max(1, self.width() - 2 * self.PAD)))

    def enterEvent(self, e):
        self._hover = True
        self._animate(1.0)

    def leaveEvent(self, e):
        self._hover = False
        self._hx = None
        if self._drag is None:
            self._animate(0.0)
        if self._bubble is not None:
            self._bubble.hide_now()
        self.update()

    def mouseMoveEvent(self, e):
        self._hx = e.position().x()
        if self._drag is not None:
            self._drag = self._f(self._hx)
        if self.dur > 0:
            from seekbar import TimeBubble
            if self._bubble is None:
                self._bubble = TimeBubble()
            self._bubble.show_at(self.mapToGlobal(QPoint(int(self._hx), 0)), _fmt(self._f(self._hx) * self.dur))
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = self._f(e.position().x())
            self._animate(1.0)
            self.update()

    def mouseReleaseEvent(self, e):
        if self._drag is not None:
            self.seek_to.emit(self._drag)
            self.frac = self._drag
            self._drag = None
            if not self._hover:
                self._animate(0.0)
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        g = self._grow
        h = 4 + 4 * g
        y = (H - h) / 2
        x0, x1 = self.PAD, W - self.PAD
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 38))
        p.drawRoundedRect(QRectF(x0, y, x1 - x0, h), h / 2, h / 2)
        frac = self._drag if self._drag is not None else self.frac
        fx = x0 + (x1 - x0) * frac
        if self._hx is not None and self._drag is None:
            hx = max(x0, min(x1, self._hx))
            if hx > fx:
                p.setBrush(QColor(255, 255, 255, 45))
                p.drawRoundedRect(QRectF(x0, y, hx - x0, h), h / 2, h / 2)
        if frac > 0:
            gr = QLinearGradient(x0, 0, max(x0 + 1, fx), 0)
            gr.setColorAt(0.0, QColor(255, 255, 255, 235) if g < 0.05 else QColor(YELLOW))
            gr.setColorAt(1.0, QColor(255, 255, 255, 235) if g < 0.05 else QColor("#ffb000"))
            if g >= 0.05:
                p.setBrush(gr)
            else:
                p.setBrush(QColor(255, 255, 255, 225))
            p.drawRoundedRect(QRectF(x0, y, max(h, fx - x0), h), h / 2, h / 2)
        r = 6.5 * g
        if r > 0.5:
            glow = QRadialGradient(QPointF(fx, H / 2), r * 2.6)
            c = QColor(YELLOW); c.setAlpha(int(120 * g))
            glow.setColorAt(0.0, c)
            c.setAlpha(0)
            glow.setColorAt(1.0, c)
            p.setBrush(glow)
            p.drawEllipse(QPointF(fx, H / 2), r * 2.6, r * 2.6)
            p.setBrush(QColor("#ffffff"))
            p.drawEllipse(QPointF(fx, H / 2), r, r)
        p.end()


class RoundCover(QLabel):
    def __init__(self, size, radius=6, parent=None):
        super().__init__(parent)
        self.size_, self.radius = size, radius
        self.setFixedSize(size, size)
        self._path = None

    def set_cover(self, path, seed=""):
        if path == self._path:
            return
        self._path = path
        self.setPixmap(cover_pixmap(path or "", self.size_, self.radius, seed=seed))


# ══════════════════════════════════════════════════════════════════════════ #
#  «Моя волна»: живой фон-пятно
# ══════════════════════════════════════════════════════════════════════════ #

# ══════════════════════════════════════════════════════════════════════════ #
#  Оболочка
# ══════════════════════════════════════════════════════════════════════════ #

MOODS = [
    ("energy", "Бодрое", lambda pr: pr["energy"], True),
    ("calm", "Спокойное", lambda pr: pr["energy"], False),
    ("happy", "Весёлое", lambda pr: pr["valence"], True),
    ("sad", "Грустное", lambda pr: pr["valence"], False),
]


def _plural(n, one, few, many):
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


class CardGrid(QScrollArea):
    """Сетка карточек, сама подстраивает число колонок под ширину."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        self.inner = QWidget(); self.inner.setObjectName("YaPage")
        self.grid = QGridLayout(self.inner)
        self.grid.setContentsMargins(0, 6, 0, 6)
        self.grid.setHorizontalSpacing(22)
        self.grid.setVerticalSpacing(20)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.setWidget(self.inner)
        self.cards = []
        self._cols = 0

    def set_cards(self, cards):
        for c in self.cards:
            c.setParent(None)
            c.deleteLater()
        self.cards = list(cards)
        self._cols = 0
        self._relayout()
        self.verticalScrollBar().setValue(0)

    def _relayout(self):
        if not self.cards:
            return
        cw = self.cards[0].width() + self.grid.horizontalSpacing()
        cols = max(1, (self.viewport().width() + self.grid.horizontalSpacing()) // max(1, cw))
        if cols == self._cols and all(c.parent() is self.inner for c in self.cards):
            return
        self._cols = cols
        for c in self.cards:
            self.grid.removeWidget(c)
        for i, c in enumerate(self.cards):
            self.grid.addWidget(c, i // cols, i % cols)
            c.show()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._relayout()


class YaShell(QWidget):
    """Весь интерфейс темы. win — MainWindow."""

    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        self.setObjectName("YaShell")
        self._track_key = None
        self._lib_sig = None
        self._pl_sig = None
        self._dirty = set()
        self._history: list[int] = []
        self._profiles = None
        self._settings_host = None
        self._lyr_src = None
        self._wave_kind = None
        self._coll_filter = ("all", None)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 10, 10)
        root.setSpacing(0)

        # ── левая колонка ──
        side = QWidget(); side.setObjectName("YaSide")
        side.setFixedWidth(210)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(14, 12, 10, 0)
        sv.setSpacing(2)
        sv.addWidget(Logo())
        sv.addSpacing(10)
        self.nav = QButtonGroup(self)
        self.nav.setExclusive(True)
        self._nav_btns = {}
        for key, text, ic in (("search", "Поиск", "search"), ("wave", "Моя волна", "wave"),
                              ("foryou", "Для вас и тренды", "note"), ("collection", "Коллекция", "collection"),
                              ("albums", "Альбомы", "album"),
                              ("settings", "Настройки и звук", "settings")):
            b = QPushButton("  " + text)
            b.setObjectName("YaNav")
            b.setIcon(ya_icon(ic, 18, "#e6e6e6"))
            b.setIconSize(QSize(18, 18))
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(lambda _=False, k=key: self.show_page(k))
            self.nav.addButton(b)
            sv.addWidget(b)
            self._nav_btns[key] = b
        sv.addSpacing(14)
        self.pl_side = QListWidget()
        self.pl_side.setIconSize(QSize(36, 36))
        self.pl_side.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.pl_side.itemClicked.connect(lambda it: self.open_playlist(it.data(Qt.ItemDataRole.UserRole)))
        self.pl_side.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.pl_side.customContextMenuRequested.connect(self._side_menu)
        self.pl_side.setToolTip("Клик — открыть плейлист · правая кнопка — переименовать, удалить")
        new_pl = QPushButton("  Создать плейлист")
        new_pl.setObjectName("YaNav")
        new_pl.setIcon(ya_icon("plus", 18, "#e6e6e6"))
        new_pl.setIconSize(QSize(18, 18))
        new_pl.setCursor(Qt.CursorShape.PointingHandCursor)
        new_pl.clicked.connect(self.new_playlist)
        sv.addWidget(new_pl)
        self.pl_side.setStyleSheet("QListWidget::item{padding:5px 4px;font-size:12px;font-weight:600;}")
        sv.addWidget(self.pl_side, 1)
        # профиль
        prof = QWidget(); prof.setObjectName("YaSide")
        pl = QHBoxLayout(prof)
        pl.setContentsMargins(4, 8, 0, 14)
        pl.setSpacing(10)
        self.avatar = QLabel(); self.avatar.setFixedSize(36, 36)
        self.nick = QLabel(""); self.nick.setStyleSheet("font-weight:700;font-size:12px;")
        self.status = QLabel(""); self.status.setObjectName("YaSub")
        col = QVBoxLayout(); col.setSpacing(0); col.addWidget(self.nick); col.addWidget(self.status)
        pl.addWidget(self.avatar); pl.addLayout(col, 1)
        tb = icon_button("palette", "Сменить тему", 30, 18, "#cfcfcf")
        tb.clicked.connect(lambda: win._winamp_theme_menu(tb.mapToGlobal(tb.rect().topLeft())))
        pl.addWidget(tb)
        prof.mousePressEvent = lambda e: win._open_profile()
        sv.addWidget(prof)
        root.addWidget(side)

        # ── правая часть: контент + плеер ──
        right = QWidget(); right.setObjectName("YaSide")
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 10, 0, 0)
        rv.setSpacing(8)
        self.content = QFrame(); self.content.setObjectName("YaContent")
        cl = QVBoxLayout(self.content)
        cl.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        cl.addWidget(self.stack)
        rv.addWidget(self.content, 1)
        rv.addWidget(self._build_player())
        root.addWidget(right, 1)

        # ── страницы ──
        self.pages = {}
        self.pages["search"] = self._build_search()
        self.pages["wave"] = self._build_wave()
        self.pages["foryou"] = Page()
        self.pages["collection"] = self._build_collection()
        self.pages["albums"] = self._build_albums()
        self.pages["playlist"] = self._build_playlist_page()
        self.pages["lyrics"] = self._build_lyrics()
        self.pages["settings"] = self._build_settings()
        self.pages["artist"] = ArtistPage(self)
        from ya_profile import ProfilePage
        self.pages["profile"] = ProfilePage(self)
        from ya_queue import QueuePage
        self.pages["queue"] = QueuePage(self)
        for k, w in self.pages.items():
            self.stack.addWidget(w)
        self._cur_page = None
        self._prev_page = "foryou"

        self._lib_sig = len(win.library)
        self._pl_sig = tuple((n, len(p.get("tracks", []))) for n, p in win.playlists.items())
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self.refresh_sidebar()
        self.show_page(win.settings.get("ya_page", "foryou") if win.settings.get("ya_page") in
                       ("search", "wave", "foryou", "collection", "albums") else "foryou")

    # ── данные / помощники ─────────────────────────────────────────────── #

    def current_track(self):
        w = self.win
        tracks = w.library if w.queue_context == "library" else w.playlists.get(w.queue_context, {}).get("tracks", [])
        if 0 <= w.current_index < len(tracks):
            return tracks[w.current_index]
        return None

    def is_current(self, t) -> bool:
        cur = self.current_track()
        return bool(cur is not None and str(cur.get("path", "")) == str(t.get("path", "")))

    def is_playing(self) -> bool:
        try:
            return bool(self.win.engine.is_playing())
        except Exception:                            # noqa: BLE001
            return False

    def _liked_paths(self):
        return {str(t.get("path", "")) for t in self.win.playlists.get(LIKED, {}).get("tracks", [])}

    def is_liked(self, t) -> bool:
        lp = getattr(self, "_liked_cache", None)
        if lp is None:
            lp = self._liked_cache = self._liked_paths()
        return str(t.get("path", "")) in lp

    def toggle_like(self, t):
        pls = self.win.playlists
        pl = pls.setdefault(LIKED, {"tracks": [], "desc": "Треки, которые вам понравились", "cover": ""})
        path = str(t.get("path", ""))
        if any(str(x.get("path", "")) == path for x in pl["tracks"]):
            pl["tracks"] = [x for x in pl["tracks"] if str(x.get("path", "")) != path]
        else:
            pl["tracks"].insert(0, dict(t))
        self._liked_cache = None
        self.win._ya_save_playlists()
        self._update_like_btn()
        for v in self.findChildren(TrackListView):
            v.viewport().update()
        if self._cur_page == "playlist" and self._pl_name == LIKED:
            self.open_playlist(LIKED, keep_history=True)

    def user_playlists(self):
        return [n for n in self.win.playlists if n not in AUTO_PLAYLISTS]

    def add_to_playlist(self, t, name):
        self.win.playlist_add_tracks(name, [t])
        self.refresh_sidebar()

    def remove_rows(self, rows):
        """Убрать несколько строк из их пользовательского плейлиста."""
        rows = [r for r in rows if r]
        if not rows:
            return
        ctx = rows[0]["ctx"]
        if ctx not in self.win.playlists or ctx in AUTO_PLAYLISTS:
            return
        self.win.playlist_remove_rows(ctx, [r["i"] for r in rows if r["ctx"] == ctx])
        if self._cur_page == "playlist" and self._pl_name == ctx:
            self.open_playlist(ctx, keep_history=True)

    def add_many_to_new_playlist(self, tracks):
        from PyQt6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "Новый плейлист", "Название:")
        name = (name or "").strip()
        if ok and name:
            self.win.playlist_add_tracks(name, tracks)
            self.refresh_sidebar()

    def move_rows(self, name, rows, target):
        self.win.playlist_move(name, rows, target)
        if self._cur_page == "playlist" and self._pl_name == name:
            self.open_playlist(name, keep_history=True)
            self.pp_list.setCurrentIndex(self.pp_list.model_.index(max(0, min(target, self.pp_list.model_.rowCount() - 1))))

    def on_tracks_changed(self):
        """MainWindow: треки/плейлисты изменились (порядок, теги, удаление)."""
        self._liked_cache = None
        self._dirty.update({"foryou", "collection", "search", "albums"})
        self.refresh_sidebar()
        cur = self._cur_page
        if cur == "playlist" and self._pl_kind == "playlist":
            if self._pl_name in self.win.playlists:
                self.open_playlist(self._pl_name, keep_history=True)
            else:
                self.show_page("collection")
        elif cur == "playlist" and self._pl_kind == "album":
            self._albums = self.album_index()
            if self._pl_name in self._albums:
                self.open_album(self._pl_name, keep_history=True)
            else:
                self.show_page("albums")
        elif cur in ("collection", "albums", "search"):
            self._rebuild(cur)
        for v in self.findChildren(TrackListView):
            v.viewport().update()

    def new_playlist(self):
        from PyQt6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "Новый плейлист", "Название плейлиста:")
        name = (name or "").strip()
        if not ok or not name:
            return
        if name in self.win.playlists:
            QMessageBox.information(self, "Такой уже есть", "Плейлист с таким названием уже существует.")
            return
        self.win.playlists[name] = {"tracks": [], "desc": "", "cover": ""}
        self.win._ya_save_playlists()
        self.refresh_sidebar()
        self.open_playlist(name)

    def _side_menu(self, pos):
        it = self.pl_side.itemAt(pos)
        if it is None:
            return
        name = it.data(Qt.ItemDataRole.UserRole)
        m = QMenu(self)
        m.addAction("Открыть", lambda: self.open_playlist(name))
        m.addAction("Слушать", lambda: self.win._play_index(0, name) if self.win.playlists.get(name, {}).get("tracks") else None)
        m.addAction("＋  Добавить треки из коллекции…", lambda: (self.win.pick_tracks_for_playlist(name, self),))
        m.addSeparator()
        m.addAction("Переименовать…", lambda: self.win._pd_rename(name))
        m.addAction("Поделиться файлом…", lambda: getattr(self.win, "share_playlist", lambda *_: None)(name))
        m.addAction("Удалить плейлист", lambda: self._delete_playlist(name))
        m.exec(self.pl_side.viewport().mapToGlobal(pos))

    def _delete_playlist(self, name):
        self.win._pd_delete_playlist(name)
        if name not in self.win.playlists:
            self.refresh_sidebar()
            if self._cur_page == "playlist" and self._pl_name == name:
                self.show_page("collection")

    def add_to_new_playlist(self, t):
        from PyQt6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "Новый плейлист", "Название:")
        name = (name or "").strip()
        if ok and name:
            self.add_to_playlist(t, name)

    def remove_from_playlist(self, row):
        if row["ctx"] not in self.win.playlists:
            return
        self.win.playlist_remove_rows(row["ctx"], [row["i"]])
        if self._cur_page == "playlist" and self._pl_name == row["ctx"]:
            self.open_playlist(row["ctx"], keep_history=True)

    def preview_row(self, row, view=None):
        """30 секунд трека альбома до скачивания (повторное нажатие — пауза / продолжить)."""
        from preview import get_preview
        cb = row.get("_pv_cb")
        if cb is None:
            def cb(state, left, row=row, view=view):
                row["pv"] = (state, left)
                if view is not None:
                    try:
                        view.viewport().update()
                    except RuntimeError:                   # список уже пересобран
                        pass
            row["_pv_cb"] = cb
        get_preview(self.win).toggle(row["url"], row["t"].get("duration", 0), cb)

    def download_remote(self, row, view=None, play=False):
        """Скачать трек альбома, которого нет в коллекции (YouTube Music), прямо из списка альбома."""
        if (row.get("dl") or ("",))[0] in ("queued", "progress", "retry"):
            return
        t = row["t"]
        row["dl"] = ("queued", 0)
        if view is not None:
            view.viewport().update()

        def cb(st, v):
            if st in ("progress", "queued", "retry"):
                row["dl"] = (st, float(v) if st == "progress" else 0)
            elif st == "error":
                row["dl"] = ("error", 0)
            elif st == "done":
                row["dl"] = ("done", 0)
                self._dirty.update({"albums", "foryou", "collection", "search"})
                if self._cur_page == "playlist" and self._pl_kind == "album":
                    QTimer.singleShot(400, lambda: self.open_album(self._pl_name, keep_history=True))
            if view is not None:
                try:
                    view.viewport().update()
                except RuntimeError:
                    pass
        from ya_online import _meta
        self.win._quick_download(row["url"], playlist=None, play=play, cb=cb, meta=_meta(row["remote_t"]))

    def play_row(self, row):
        if row.get("remote"):
            self.download_remote(row, self.pp_list, play=True)
            return
        if row.get("ctx") == ALBUM_PL and row.get("alb_tracks") is not None:
            # альбом играет своей очередью — по порядку треков
            self.win.playlists[ALBUM_PL] = {"tracks": [dict(t) for t in row["alb_tracks"]],
                                            "desc": f"auto: альбом {row.get('alb_name', '')}", "cover": ""}
        self.win._play_index(row["i"], row["ctx"])

    def _lib_rows(self, tracks=None):
        lib = self.win.library
        if tracks is None:
            return [{"t": t, "ctx": "library", "i": i} for i, t in enumerate(lib)]
        idx = {str(t.get("path", "")): i for i, t in enumerate(lib)}
        return [{"t": t, "ctx": "library", "i": idx[str(t.get("path", ""))]}
                for t in tracks if str(t.get("path", "")) in idx]

    def profiles(self):
        """Профили настроения треков (music_intel) или None, пока считаются."""
        if self._profiles is None:
            self._profiles = self.win._ya_profiles(self._on_profiles)
        return self._profiles

    def _on_profiles(self, prof):
        self._profiles = prof or {}
        if self._cur_page == "playlist" and getattr(self, "_pp_tracks", None) is not None:
            self._pp_filters_reset(self._pp_tracks, getattr(self, "_pp_fkey", None))
        self._dirty.update({"foryou", "collection", "wave"})
        if self._cur_page in ("foryou", "collection", "wave"):
            self._rebuild(self._cur_page)

    # ── навигация ─────────────────────────────────────────────────────── #

    def show_page(self, key, remember=True):
        if key not in self.pages:
            return
        if remember and self._cur_page and self._cur_page != key and self._cur_page != "lyrics":
            self._prev_page = self._cur_page
            hist = self.__dict__.setdefault("_page_hist", [])   # «Назад» на несколько шагов (альбом → артист → …)
            hist.append(self._cur_page)
            del hist[:-30]
        self._cur_page = key
        if key in self._nav_btns:
            self._nav_btns[key].setChecked(True)
            if key in ("search", "wave", "foryou", "collection", "albums"):
                self.win.settings["ya_page"] = key
        else:
            b = self.nav.checkedButton()
            if b is not None:
                self.nav.setExclusive(False)
                b.setChecked(False)
                self.nav.setExclusive(True)
        if key == "profile":
            self.pages["profile"].refresh()
        if key == "queue":
            self.pages["queue"].refresh()
        if key in ("foryou", "collection", "wave", "search", "albums") and (key in self._dirty or key == "foryou"
                                                                  and not self.pages["foryou"].v.count()):
            self._rebuild(key)
        self.stack.setCurrentWidget(self.pages[key])
        self.btn_lyrics.setChecked(key == "lyrics")
        self.btn_queue.setStyleSheet("QPushButton{background:rgba(255,219,26,0.22);border-radius:18px;}" if key == "queue" else "")

    def _rebuild(self, key):
        self._dirty.discard(key)
        if key == "foryou":
            self._fill_foryou()
        elif key == "collection":
            self._fill_collection()
        elif key == "albums":
            self._fill_albums()
        elif key == "wave":
            self._fill_wave_list()
        elif key == "search":
            self._search_changed()

    def go_back(self):
        hist = self.__dict__.get("_page_hist") or []
        while hist:
            key = hist.pop()
            if key != self._cur_page and key in self.pages:
                self._prev_page = hist[-1] if hist else "foryou"
                self.show_page(key, remember=False)
                return
        self.show_page(self._prev_page or "foryou", remember=False)

    # ── плеер (низ) ───────────────────────────────────────────────────── #

    def _build_player(self) -> QWidget:
        w = self.win
        bar = QFrame(); bar.setObjectName("YaPlayer")
        bar.setFixedHeight(84)
        v = QVBoxLayout(bar)
        v.setContentsMargins(0, 2, 0, 8)
        v.setSpacing(0)
        self.progress = ProgressLine()
        self.progress.seek_to.connect(self._seek_frac)
        v.addWidget(self.progress)
        h = QHBoxLayout()
        h.setContentsMargins(12, 0, 14, 0)
        h.setSpacing(6)
        # слева: обложка + название
        self.p_cover = RoundCover(52, 6)
        self.p_cover.setCursor(Qt.CursorShape.PointingHandCursor)
        self.p_cover.mousePressEvent = lambda e: self.toggle_lyrics()
        h.addWidget(self.p_cover)
        h.addSpacing(6)
        tcol = QVBoxLayout(); tcol.setSpacing(1)
        self.p_title = QLabel("—"); self.p_title.setStyleSheet("font-size:14px;font-weight:700;")
        self.p_artist = QLabel(""); self.p_artist.setObjectName("YaSub")
        self.p_artist.setCursor(Qt.CursorShape.PointingHandCursor)
        self.p_artist.mousePressEvent = lambda e: self._open_current_artist()
        tcol.addStretch(1); tcol.addWidget(self.p_title); tcol.addWidget(self.p_artist); tcol.addStretch(1)
        left = QWidget(); left.setObjectName("YaSide"); left.setLayout(tcol)
        left.setMinimumWidth(120); left.setMaximumWidth(300)
        h.addWidget(left, 1)
        # центр: управление
        self.b_shuffle = icon_button("shuffle", "Перемешать", 38, 20, "#cfcfcf", True)
        self.b_shuffle.clicked.connect(lambda: w.btn_shuffle.click())
        self.b_prev = icon_button("prev", "Предыдущий", 40, 22)
        self.b_prev.clicked.connect(w.prev_track)
        self.b_play = QPushButton()
        self.b_play.setObjectName("YaYellow")
        self.b_play.setFixedSize(44, 44)
        self.b_play.setStyleSheet(f"QPushButton{{background:{YELLOW};border-radius:22px;padding:0;}}"
                                  "QPushButton:hover{background:#ffe55c;}")
        self.b_play.setIconSize(QSize(22, 22))
        self.b_play.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_play.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.b_play.clicked.connect(w.toggle_play)
        self.b_next = icon_button("next", "Следующий", 40, 22)
        self.b_next.clicked.connect(w.next_track)
        self.b_repeat = icon_button("repeat", "Повтор", 38, 20, "#cfcfcf", True)
        self.b_repeat.clicked.connect(w._cycle_repeat)
        self.b_like = icon_button("heart", "Мне нравится", 38, 20)
        self.b_like.clicked.connect(lambda: self.current_track() and self.toggle_like(self.current_track()))
        center = QHBoxLayout(); center.setSpacing(8)
        for b in (self.b_shuffle, self.b_prev, self.b_play, self.b_next, self.b_repeat, self.b_like):
            center.addWidget(b)
        h.addStretch(1)
        h.addLayout(center)
        h.addStretch(1)
        # справа
        self.btn_lyrics = icon_button("lyrics", "Текст песни", 36, 20, "#cfcfcf", True)
        self.btn_lyrics.clicked.connect(self.toggle_lyrics)
        self.btn_queue = icon_button("queue", "Очередь", 36, 20, "#cfcfcf")
        self.btn_queue.clicked.connect(self.toggle_queue)
        b_cover = icon_button("cover", "Режим обложки", 36, 20, "#cfcfcf")
        b_cover.clicked.connect(w._toggle_cover_mode)
        b_clip = icon_button("clip", "Клип песни (Ctrl+K)", 36, 20, "#cfcfcf")
        b_clip.clicked.connect(w._toggle_clip_mode)
        b_set = icon_button("settings", "Звук и настройки", 36, 20, "#cfcfcf")
        b_set.clicked.connect(lambda: self.show_page("settings"))
        self.b_vol = icon_button("volume", "Громкость", 34, 20, "#cfcfcf")
        self.b_vol.clicked.connect(self._toggle_mute)
        self.vol = QSlider(Qt.Orientation.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setFixedWidth(90)
        self.vol.setValue(w.vol.value())
        self.vol.valueChanged.connect(lambda v: w.vol.setValue(v))
        for b in (self.btn_lyrics, self.btn_queue, b_clip, b_cover, b_set, self.b_vol):
            h.addWidget(b)
        h.addWidget(self.vol)
        v.addLayout(h, 1)
        self._muted_from = None
        return bar

    def _toggle_mute(self):
        if self._muted_from is None:
            self._muted_from = self.win.vol.value()
            self.win.vol.setValue(0)
        else:
            self.win.vol.setValue(self._muted_from or 60)
            self._muted_from = None

    def _seek_frac(self, frac):
        try:
            dur = float(self.win.engine.duration or 0)
            if dur > 0:
                self.win.engine.seek(frac * dur)
        except Exception as e:                       # noqa: BLE001
            print("[ya] seek:", e)

    def _open_current_artist(self):
        t = self.current_track()
        if t:
            self.open_artist(_artist_main(t.get("artist", "")))

    def _update_like_btn(self):
        t = self.current_track()
        liked = bool(t and self.is_liked(t))
        self.b_like.setIcon(ya_icon("heart_fill" if liked else "heart", 20, "#ffffff"))
        if hasattr(self, "wave"):
            self.wave.like_btn.setIcon(ya_icon("heart_fill" if liked else "heart", 20))

    def _tick(self):
        w = self.win
        eng = w.engine
        t = self.current_track()
        key = str(t.get("path", "")) if t else None
        if key != self._track_key:
            self._track_key = key
            self._on_track_changed(t)
        if self._cur_page == "queue":
            qp = self.pages["queue"]
            if qp.signature() != qp.sig:
                qp.refresh()
        playing = self.is_playing()
        if getattr(self, "_playing_ui", None) != playing:
            self._playing_ui = playing
            self.b_play.setIcon(ya_icon("pause" if playing else "play", 22, "#000000"))
            for v in self.findChildren(TrackListView):
                v.viewport().update()
            self.wave_play.setIcon(ya_icon("pause" if playing else "play", 26, "#000000"))
        try:
            dur = float(eng.duration or 0)
            pos = float(eng.get_position() or 0)
        except Exception:                            # noqa: BLE001
            dur = pos = 0.0
        self.progress.set_state(pos / dur if dur > 0 else 0.0, dur)
        if self.b_shuffle.isChecked() != bool(w.shuffle):
            self.b_shuffle.setChecked(bool(w.shuffle))
        rm = int(getattr(w, "repeat_mode", 0))
        if getattr(self, "_rm", None) != rm:
            self._rm = rm
            self.b_repeat.setChecked(rm != 0)
            self.b_repeat.setIcon(ya_icon("repeat_one" if rm == 2 else "repeat", 20,
                                          YELLOW if rm else "#cfcfcf"))
        self.b_shuffle.setIcon(ya_icon("shuffle", 20, YELLOW if w.shuffle else "#cfcfcf"))
        if self.vol.value() != w.vol.value() and not self.vol.isSliderDown():
            self.vol.blockSignals(True); self.vol.setValue(w.vol.value()); self.vol.blockSignals(False)
            self.b_vol.setIcon(ya_icon("mute" if w.vol.value() == 0 else "volume", 20, "#cfcfcf"))
        # библиотека / плейлисты поменялись — обновить страницы
        lib_sig = len(w.library)
        pl_sig = tuple((n, len(p.get("tracks", []))) for n, p in w.playlists.items())
        if lib_sig != self._lib_sig or pl_sig != self._pl_sig:
            lib_changed = lib_sig != self._lib_sig
            self._lib_sig, self._pl_sig = lib_sig, pl_sig
            self._liked_cache = None
            self.refresh_sidebar()
            self._dirty.update({"foryou", "collection", "search", "albums"})
            if lib_changed:
                self._profiles = None
            if self._cur_page in ("collection",) and self._timer.interval():
                self._rebuild(self._cur_page)
        # текст
        if self._cur_page == "lyrics":
            self._lyrics_tick(None, dur)           # позиция — плавная, см. ниже

    def _on_track_changed(self, t):
        if t is None:
            self.p_title.setText("—")
            self.p_artist.setText("")
            self.p_cover.set_cover("", "none")
            return
        title = sanitize_title(t.get("title", "") or "Без названия")
        artist = t.get("artist") or "Неизвестный исполнитель"
        fm = QFontMetrics(self.p_title.font())
        self.p_title.setText(fm.elidedText(title, Qt.TextElideMode.ElideRight, 280))
        self.p_title.setToolTip(title)
        self.p_artist.setText(artist)
        self.p_cover.set_cover(t.get("cover", ""), str(t.get("path", "")))
        self._update_like_btn()
        for v in self.findChildren(TrackListView):
            v.viewport().update()
        # «Моя волна» и текст — под новый трек
        wl = self.wave.track_lbl
        wl.setText(QFontMetrics(wl.font()).elidedText(f"{title}  ·  {artist}", Qt.TextElideMode.ElideRight, 400))
        self.wave.cover.setPixmap(cover_pixmap(t.get("cover", ""), 60, 6, seed=str(t.get("path", ""))))
        liked = self.is_liked(t)
        self.wave.like_btn.setIcon(ya_icon("heart_fill" if liked else "heart", 20))
        if self.win.queue_context == WAVE_PL and hasattr(self, "_disc_pool"):
            self._update_wave_hint()
            self._disc_feed()
        self.l_cover.set_cover(t.get("cover", ""), str(t.get("path", "")))
        self.l_title.setText(title)
        self.l_artist.setText(artist)
        self._lyr_bg = None
        self.pages["lyrics"].update()
        # панель текста непрозрачна и рисует свой кусок фона сама; без таймингов она не перерисовывается
        # (строки не меняются) — иначе остаётся фон прошлого трека прямоугольником
        self.l_panel.update()

    # ── страница «Поиск» ─────────────────────────────────────────────── #

    def _build_search(self):
        page = Page()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Трек, исполнитель, альбом…")
        self.search_edit.addAction(ya_icon("search", 18, "#8a8a8a"), QLineEdit.ActionPosition.LeadingPosition)
        self.search_edit.setMinimumHeight(40)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(180)
        self._search_timer.timeout.connect(lambda: self._search_now() if self._search_online()
                                           else self._search_changed())
        # в интернете — пауза подольше, чтобы не дёргать серверы на каждую букву
        self.search_edit.textChanged.connect(
            lambda _: self._search_timer.start(900 if self._search_online() else 180))
        self.search_edit.returnPressed.connect(self._search_now)
        page.v.addWidget(self.search_edit)
        # где искать: коллекция или интернет (YouTube Music + SoundCloud)
        mrow = QHBoxLayout(); mrow.setSpacing(8); mrow.setContentsMargins(0, 14, 0, 0)
        self.s_local = chip("В коллекции"); self.s_net = chip("В интернете")
        self.s_net.setIcon(ya_icon("wave", 16)); self.s_net.setIconSize(QSize(16, 16))
        gm = QButtonGroup(page); gm.setExclusive(True)
        for b in (self.s_local, self.s_net):
            gm.addButton(b)
            b.clicked.connect(self._search_mode_changed)
        self.s_net.toggled.connect(lambda on: self.s_net.setIcon(ya_icon("wave", 16, "#000000" if on else "#cfcfcf")))
        self.s_net.setIcon(ya_icon("wave", 16, "#cfcfcf"))
        self.s_local.setChecked(True)
        mrow.addWidget(seg_group(self.s_local, self.s_net))
        mrow.addSpacing(14)
        self.s_src = {}
        gs = QButtonGroup(page); gs.setExclusive(True)
        src_btns = []
        for key, text in (("all", "Везде"), ("ytm", "YouTube Music"), ("sc", "SoundCloud")):
            b = chip(text)
            gs.addButton(b)
            b.clicked.connect(self._search_now)
            self.s_src[key] = b
            src_btns.append(b)
        self.s_src["all"].setChecked(True)
        self.s_src_group = seg_group(*src_btns)
        mrow.addWidget(self.s_src_group)
        mrow.addStretch(1)
        self.s_target_lbl = QLabel("Скачивать в:"); self.s_target_lbl.setObjectName("YaSub")
        self.s_target = QComboBox(); self.s_target.setMinimumWidth(170)
        mrow.addWidget(self.s_target_lbl); mrow.addWidget(self.s_target)
        page.v.addLayout(mrow)
        row = QHBoxLayout(); row.setSpacing(8); row.setContentsMargins(0, 10, 0, 0)
        self.s_pop = chip("Популярное"); self.s_hist = chip("История")
        g = QButtonGroup(page); g.setExclusive(True)
        for b in (self.s_pop, self.s_hist):
            g.addButton(b)
            b.clicked.connect(self._search_changed)
        self.s_pop.setChecked(True)
        row.addWidget(seg_group(self.s_pop, self.s_hist))
        row.addStretch(1)
        self.s_local_row = QWidget(); self.s_local_row.setObjectName("YaPage"); self.s_local_row.setLayout(row)
        page.v.addWidget(self.s_local_row)
        self.s_title = section_title("Популярное у вас")
        page.v.addWidget(self.s_title)
        self.s_artists = HRow()
        page.v.addWidget(self.s_artists)
        self.s_list = TrackListView(self)
        self.s_list.set_auto_height(True)
        page.v.addWidget(self.s_list)
        from ya_online import OnlineResults
        self.s_online = OnlineResults(self)
        page.v.addWidget(self.s_online)
        page.v.addStretch(1)
        self._dirty.add("search")
        self._search_mode_changed()
        return page

    # ── онлайн-поиск ── #

    def _search_online(self) -> bool:
        return self.s_net.isChecked()

    def _search_mode_changed(self, *_):
        net = self._search_online()
        self.s_src_group.setVisible(net)
        self.s_target_lbl.setVisible(net)
        self.s_target.setVisible(net)
        self.s_local_row.setVisible(not net)
        self.s_title.setVisible(not net)
        self.s_list.setVisible(not net)
        self.s_artists.setVisible(False)
        self.s_online.setVisible(net)
        self.search_edit.setPlaceholderText("Найти в интернете: трек или исполнитель (Enter — искать)" if net
                                            else "Трек, исполнитель, альбом…")
        if net:
            self._fill_target_combo()
        self._search_now()

    def _fill_target_combo(self):
        cur = self.s_target.currentData() or self.win.settings.get("online_target", "Скачанное")
        self.s_target.blockSignals(True)
        self.s_target.clear()
        self.s_target.addItem("Только в коллекцию", "")
        names = ["Скачанное"] + [n for n in self.user_playlists() if n != "Скачанное"]
        for n in names:
            self.s_target.addItem(n, n)
        i = self.s_target.findData(cur)
        self.s_target.setCurrentIndex(i if i >= 0 else 1)
        self.s_target.blockSignals(False)
        try:
            self.s_target.currentIndexChanged.disconnect()
        except TypeError:
            pass
        self.s_target.currentIndexChanged.connect(
            lambda _: self.win.settings.__setitem__("online_target", self.s_target.currentData() or ""))

    def online_target_playlist(self):
        return self.s_target.currentData() or None

    def online_local_path(self, url):
        p = self.win.settings.get("online_downloaded", {}).get(url)
        if p and any(t.get("path") == p for t in self.win.library):
            return p
        return ""

    def play_path(self, path):
        rows = [r for r in self._lib_rows() if r["t"].get("path") == path]
        if rows:
            self.play_row(rows[0])

    def _search_now(self, *_):
        if not hasattr(self, "s_online"):
            return
        if self._search_online():
            src = next((k for k, b in self.s_src.items() if b.isChecked()), "all")
            self.s_online.search(self.search_edit.text().strip(), src)
        else:
            self._search_changed()

    def online_find_artist(self, name, source=None):
        self.show_page("search")
        self.s_net.setChecked(True)
        if source in ("ytm", "sc"):
            self.s_src[source].setChecked(True)
        self.search_edit.blockSignals(True)
        self.search_edit.setText(name)
        self.search_edit.blockSignals(False)
        self._search_mode_changed()

    def open_release(self, kind, artist, rel=None, browse_id=None):
        """Альбом / сингл исполнителя (треклист с прослушиваниями) или все его треки."""
        page = self.pages.get("release")
        if page is None:
            from ya_online import ReleasePage
            page = ReleasePage(self)
            self.pages["release"] = page
            self.stack.addWidget(page)
        if kind == "all":
            page.open_all(artist, browse_id)
        else:
            page.open_album(artist, rel or {})
        self.show_page("release")

    def open_online_artist(self, artist: dict):
        page = self.pages.get("online_artist")
        if page is None:
            from ya_online import OnlineProfilePage
            page = OnlineProfilePage(self)
            self.pages["online_artist"] = page
            self.stack.addWidget(page)
        page.load(artist)
        self.show_page("online_artist")

    def _search_changed(self):
        if self._search_online():
            return                                   # в интернете ищем по Enter/кнопкам, не на каждый символ
        q = self.search_edit.text().strip().lower()
        lib = self.win.library
        hist = self.win.settings.get("play_history", {})
        lbl = self.s_title.findChild(QLabel)
        self.s_artists.clear()
        if q:
            from search_util import track_matches, tokens
            qt = tokens(q)
            found = [t for t in lib if track_matches(q, t)]
            lbl.setText(f"Найдено: {len(found)}" if found else "Ничего не нашлось")
            arts = {}
            for t in found:
                a = _artist_main(t.get("artist", ""))
                if a and any(len(w) > 1 and w in a.lower() for w in qt):      # исполнитель, чьё имя есть в запросе
                    arts.setdefault(a, t)
            for a, t in list(arts.items())[:12]:
                c = Card(a, "Исполнитель", t.get("cover", ""), 120, circle=True)
                c.clicked.connect(lambda a=a: self.open_artist(a))
                self.s_artists.add(c)
            self.s_artists.setVisible(bool(arts))
            self.s_list.set_rows(self._lib_rows(found[:300]))
            return
        self.s_artists.setVisible(False)
        if self.s_hist.isChecked():
            items = sorted(((h.get("last", ""), int(h.get("count", 0)), p) for p, h in hist.items()), reverse=True)
            lbl.setText("Недавно слушали")
        else:
            items = sorted(((int(h.get("count", 0)), h.get("last", ""), p) for p, h in hist.items()), reverse=True)
            lbl.setText("Популярное у вас")
        bypath = {str(t.get("path", "")): t for t in lib}
        tracks = [bypath[p] for *_, p in items if p in bypath][:60]
        if not tracks:
            tracks = list(reversed(lib[-40:]))
            lbl.setText("Недавно добавленные")
        self.s_list.set_rows(self._lib_rows(tracks))

    # ── страница «Для вас» ───────────────────────────────────────────── #

    def _fill_foryou(self):
        page = self.pages["foryou"]
        page.clear()
        w = self.win
        lib = w.library
        h = QLabel("Для вас"); h.setObjectName("YaH1")
        page.v.addWidget(h)

        # подборки-миксы
        page.v.addWidget(section_title("Миксы и подборки"))
        row = HRow()
        mixes = [("Моя волна", "Всё, что вы любите", ("my", None), QColor("#ffd60a"), QColor("#e0218a")),
                 ("Настроение дня", "Время суток и погода", ("mood_day", None), QColor("#ff8a00"), QColor("#7a2cff")),
                 ("Трек дня", "Давно не слушали", ("track_day", None), QColor("#00d4ff"), QColor("#2b5bff"))]
        for title, sub, kind, c1, c2 in mixes:
            c = Card(title, sub, size=150, mix=(c1, c2))
            c.clicked.connect(lambda k=kind: self.start_wave(*k))
            row.add(c)
        prof = self.profiles()
        if prof:
            fam_count = {}
            for pr in prof.values():
                for f in pr.get("families", [])[:1]:
                    fam_count[f] = fam_count.get(f, 0) + 1
            try:
                from music_intel import GENRE_RU
            except Exception:                        # noqa: BLE001
                GENRE_RU = {}
            for f, n in sorted(fam_count.items(), key=lambda x: -x[1])[:6]:
                if n < 3:
                    continue
                hue = (hash(f) % 360) / 360.0
                c = Card(GENRE_RU.get(f, f).capitalize(), f"{n} треков",
                         size=150, mix=(QColor.fromHsvF(hue, 0.85, 1.0), QColor.fromHsvF((hue + 0.3) % 1, 0.8, 0.8)))
                c.clicked.connect(lambda f=f: self.start_wave("genre", f))
                row.add(c)
            for key, name, _fn, _hi in MOODS:
                hue = {"energy": 0.02, "calm": 0.55, "happy": 0.14, "sad": 0.66}[key]
                c = Card(name, "Под настроение", size=150,
                         mix=(QColor.fromHsvF(hue, 0.85, 1.0), QColor.fromHsvF((hue + 0.12) % 1, 0.9, 0.7)))
                c.clicked.connect(lambda k=key: self.start_wave("mood", k))
                row.add(c)
        page.v.addWidget(row)

        # плейлисты
        pls = [n for n in w.playlists if n not in AUTO_PLAYLISTS or n == "Настроение дня"]
        if pls:
            page.v.addWidget(section_title("Ваши плейлисты"))
            row = HRow()
            for n in pls:
                tr = w.playlists[n].get("tracks", [])
                cov = w.playlists[n].get("cover") or (tr[0].get("cover", "") if tr else "")
                c = Card(n, f"{len(tr)} треков", cov, 150, seed=n)
                c.clicked.connect(lambda n=n: self.open_playlist(n))
                row.add(c)
            page.v.addWidget(row)

        # исполнители
        arts = {}
        for t in lib:
            a = _artist_main(t.get("artist", ""))
            if a and a.lower() not in ("unknown artist", "неизвестный исполнитель"):
                e = arts.setdefault(a, [0, ""])
                e[0] += 1
                if not e[1] and t.get("cover"):
                    e[1] = t.get("cover")
        top = sorted(arts.items(), key=lambda x: -x[1][0])[:16]
        if top:
            page.v.addWidget(section_title("Ваши исполнители"))
            row = HRow()
            for a, (n, cov) in top:
                c = Card(a, f"{n} треков", cov, 130, circle=True)
                c.clicked.connect(lambda a=a: self.open_artist(a))
                row.add(c)
            page.v.addWidget(row)

        # недавно добавленные
        if lib:
            page.v.addWidget(section_title("Недавно добавленные"))
            row = HRow()
            idx_map = {str(t.get("path", "")): i for i, t in enumerate(lib)}
            for t in list(reversed(lib))[:18]:
                title = sanitize_title(t.get("title", ""))
                c = Card(title, t.get("artist", ""), t.get("cover", ""), 150, seed=str(t.get("path", "")))
                c.clicked.connect(lambda i=idx_map[str(t.get("path", ""))]: self.win._play_index(i, "library"))
                row.add(c)
            page.v.addWidget(row)

        # давно не слушали
        hist = w.settings.get("play_history", {})
        old = sorted(lib, key=lambda t: hist.get(str(t.get("path", "")), {}).get("last", "0"))[:30]
        if old:
            page.v.addWidget(section_title("Вы могли забыть"))
            lv = TrackListView(self)
            lv.set_auto_height(True)
            lv.set_rows(self._lib_rows(old[:12]))
            page.v.addWidget(lv)
        page.v.addStretch(1)

    # ── «Моя волна» ──────────────────────────────────────────────────── #

    def _build_wave(self):
        from ya_wave import WavePage
        pg = WavePage(self.win.engine)
        self.wave = pg
        pg.wheel.activated.connect(self._wheel_activated)
        pg.tune_btn.setIcon(ya_icon("settings", 18)); pg.tune_btn.setIconSize(QSize(18, 18))
        pg.tune_btn.clicked.connect(lambda: self._wave_tune_open(toggle=True))
        pg.prev_btn.setIcon(ya_icon("prev", 24)); pg.prev_btn.setIconSize(QSize(24, 24))
        pg.next_btn.setIcon(ya_icon("next", 24)); pg.next_btn.setIconSize(QSize(24, 24))
        pg.prev_btn.clicked.connect(self.win.prev_track)
        pg.next_btn.clicked.connect(self.win.next_track)
        pg.play_btn.setIconSize(QSize(26, 26))
        pg.play_btn.setIcon(ya_icon("play", 26, "#000000"))
        pg.play_btn.clicked.connect(self._wave_play_clicked)
        pg.like_btn.setIcon(ya_icon("heart", 20)); pg.like_btn.setIconSize(QSize(20, 20))
        pg.like_btn.clicked.connect(lambda: self.current_track() and self.toggle_like(self.current_track()))
        pg.more_btn.setIcon(ya_icon("dots", 20)); pg.more_btn.setIconSize(QSize(20, 20))
        pg.more_btn.clicked.connect(self._wave_menu)
        pg.set_mood("Моя волна", "my", None, instant=True)
        pg.hint.setText("Выберите настроение слева — волна подстроится")
        self.wave_play = pg.play_btn
        self._dirty.add("wave")
        return pg

    # ── «Настроить Мою волну» ── #
    def _wave_tune(self) -> dict:
        t = self.win.settings.get("wave_tune")
        return dict(t) if isinstance(t, dict) else {}

    def _wheel_activated(self, kind, arg=None):
        self.start_wave(kind, arg)
        if kind == "my":                       # «Назад к привычному» — и сразу окошко настроек, как в Яндексе
            self._wave_tune_open()

    def _wave_tune_open(self, toggle=False):
        from ya_wave_tune import WaveTunePopup
        pop = getattr(self, "_tune_pop", None)
        if pop is None:
            pop = self._tune_pop = WaveTunePopup(self.wave, self._wave_tune())
            pop.changed.connect(self._wave_tune_changed)
            self._tune_timer = QTimer(self)
            self._tune_timer.setSingleShot(True)
            self._tune_timer.timeout.connect(self._wave_tune_apply)
        if toggle and pop.isVisible():
            pop.hide()
            return
        pop.popup(self.wave.tune_btn)

    def _wave_tune_changed(self, tune):
        self.win.settings["wave_tune"] = dict(tune)
        try:
            self.win.save_settings()
        except Exception:                      # noqa: BLE001
            pass
        self._tune_timer.start(350)            # щёлкают подряд — пересобираем один раз

    def _wave_tune_apply(self):
        """Настройки поменялись: текущий трек доигрывает, дальше — уже новая подборка."""
        w = self.win
        playing_my = w.current_index >= 0 and w.queue_context == WAVE_PL and \
            (self._wave_kind or ("my",))[0] == "my"
        if not playing_my:
            self.start_wave("my")
            return
        tracks = w.playlists.get(WAVE_PL, {}).get("tracks", [])
        cur = tracks[w.current_index] if 0 <= w.current_index < len(tracks) else None
        out = self._wave_my_tracks(random.Random(), exclude=cur)
        w.playlists[WAVE_PL]["tracks"] = ([cur] if cur else []) + out[:59]
        w.current_index = 0 if cur else -1
        if getattr(w, "shuffle", False):
            try:
                w._build_shuffle_queue()
            except Exception:                  # noqa: BLE001
                pass
        w._ya_save_playlists()
        try:
            w._preload_adjacent()
        except Exception:                      # noqa: BLE001
            pass
        self._wave_set_hint("my", None, len(out[:59]) + (1 if cur else 0))

    def _wave_my_tracks(self, rng, exclude=None) -> list:
        """«Моя волна» под настройки окошка: без повторов исполнителя подряд."""
        from ya_wave_tune import build_pool
        w = self.win
        lib = [t for t in w.library if not exclude or t.get("path") != exclude.get("path")]
        pool = build_pool(lib, self.profiles() or {}, w.settings.get("play_history", {}), self._liked_paths(),
                          self._wave_tune(), rng, artist_of=lambda t: _artist_main(t.get("artist", "")))
        out, rest = [], pool[:120]
        while rest and len(out) < 60:
            last = _artist_main(out[-1].get("artist", "")) if out else None
            j = next((k for k, t in enumerate(rest) if _artist_main(t.get("artist", "")) != last), 0)
            out.append(rest.pop(j))
        return out

    def _wave_set_hint(self, kind, arg, n):
        from ya_wave_tune import summary
        hints = {"my": "Всё, что вы любите, и немного нового", "fresh": "Треки, которые вы ещё не слушали",
                 "fav": "То, что вы слушаете чаще всего", "artist": f"Треки исполнителя {arg}",
                 "genre": "Подборка из вашей коллекции", "mood": "Подобрано по звучанию треков"}
        base = hints.get(kind, "")
        if kind == "my" and summary(self._wave_tune()):
            base = summary(self._wave_tune())
        self._wave_base_hint = f"{base}  ·  {n} треков"
        self.wave.hint.setText(self._wave_base_hint)

    def _wave_menu(self):
        m = QMenu(self)
        m.addAction("Настроить Мою волну", lambda: self._wave_tune_open())
        m.addAction("Очередь", self.win._show_queue_popup)
        m.addAction("Текст песни", self.toggle_lyrics)
        m.addAction("Клип песни", self.win._toggle_clip_mode)
        t = self.current_track()
        if t and _artist_main(t.get("artist", "")):
            a = _artist_main(t.get("artist", ""))
            m.addAction(f"Исполнитель: {a}", lambda: self.open_artist(a))
            m.addAction("Волна по исполнителю", lambda: self.start_wave("artist", a))
        m.exec(QCursor.pos())

    @staticmethod
    def _blob_icon(c1: QColor, c2: QColor, shape: int) -> QIcon:
        pm = QPixmap(88, 88)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        g = QLinearGradient(0, 0, 88, 88)
        g.setColorAt(0, c1)
        g.setColorAt(1, c2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        path = QPainterPath()
        if shape == 0:                               # «X»-клякса
            pa = QPainterPath(); pa.addRoundedRect(QRectF(32, 6, 24, 76), 12, 12)
            t = QPainterPath(); t.addRoundedRect(QRectF(6, 32, 76, 24), 12, 12)
            path = pa.united(t)
            p.translate(44, 44); p.rotate(45); p.translate(-44, -44)
        elif shape == 1:                             # молния-«Z»
            path.moveTo(14, 16); path.lineTo(74, 10); path.lineTo(38, 46); path.lineTo(76, 44)
            path.lineTo(14, 80); path.lineTo(46, 44); path.lineTo(18, 46); path.closeSubpath()
        else:                                        # капля
            path.addEllipse(QRectF(10, 14, 68, 62))
        p.drawPath(path)
        p.end()
        return QIcon(pm)

    def _fill_wave_list(self):
        from ya_wave import palette_for
        items = []
        prof = self.profiles()
        try:
            from music_intel import GENRE_RU
        except Exception:                            # noqa: BLE001
            GENRE_RU = {}
        genres = []
        if prof:
            fam_count = {}
            for pr in prof.values():
                for f in pr.get("families", [])[:1]:
                    fam_count[f] = fam_count.get(f, 0) + 1
            genres = [f for f, n in sorted(fam_count.items(), key=lambda x: -x[1]) if n >= 3][:8]

        def genre_item(f):
            core, halo, ray = palette_for("genre", f)
            icon = "m" if f in ("metal", "metalcore", "rock", "punk") else "bolt"
            return {"kind": "genre", "arg": f, "label": GENRE_RU.get(f, f).capitalize(),
                    "icon": icon, "c1": ray, "c2": halo}

        def mood_item(key, name):
            core, halo, ray = palette_for("mood", key)
            return {"kind": "mood", "arg": key, "label": name, "icon": "blob", "c1": ray, "c2": halo}

        for f in genres[:3]:
            items.append(genre_item(f))
        names = {m[0]: m[1] for m in MOODS}
        items.append(mood_item("energy", names["energy"]))
        core, halo, ray = palette_for("mood_day")
        items.append({"kind": "mood_day", "arg": None, "label": "Настроение дня", "icon": "blob",
                      "c1": ray, "c2": halo})
        back_index = len(items)
        items.append({"kind": "my", "arg": None, "label": "Назад к привычному", "icon": "back",
                      "accent": True})
        arts = {}
        for t in self.win.library:
            a = _artist_main(t.get("artist", ""))
            if a:
                arts[a] = arts.get(a, 0) + 1
        top_arts = [a for a, _ in sorted(arts.items(), key=lambda x: -x[1])[:4]]

        def artist_item(a):
            cov = next((t.get("cover", "") for t in self.win.library
                        if _artist_main(t.get("artist", "")) == a and t.get("cover")), "")
            return {"kind": "artist", "arg": a, "label": a, "sub": "Артист", "icon": "artist",
                    "cover": cov or "", "c1": "#444", "c2": "#222"}

        if top_arts:
            items.append(artist_item(top_arts[0]))
        items.append(mood_item("calm", names["calm"]))
        for f in genres[3:5]:
            items.append(genre_item(f))
        if len(top_arts) > 1:
            items.append(artist_item(top_arts[1]))
        items.append(mood_item("happy", names["happy"]))
        items.append(mood_item("sad", names["sad"]))
        core, halo, ray = palette_for("fresh")
        items.append({"kind": "fresh", "arg": None, "label": "Незнакомое", "icon": "blob", "c1": ray, "c2": halo})
        for a in top_arts[2:]:
            items.append(artist_item(a))
        core, halo, ray = palette_for("fav")
        items.append({"kind": "fav", "arg": None, "label": "Любимое", "icon": "blob", "c1": ray, "c2": halo})
        for f in genres[5:]:
            items.append(genre_item(f))
        self.wave.wheel.set_items(items)
        self.wave.wheel.off = self.wave.wheel.target = float(back_index)
        if self._wave_kind:
            self.wave.wheel.set_active(tuple(self._wave_kind))

    def _wave_play_clicked(self):
        w = self.win
        in_wave = w.current_index >= 0 and w.queue_context == WAVE_PL
        if in_wave:
            w.toggle_play()                    # волна уже идёт — обычная пауза / продолжить
        else:
            # волна не играет: каждый раз собираем новую и начинаем со случайного трека
            kind, arg = self._wave_kind if self._wave_kind and self._wave_kind[0] not in ("mood_day", "track_day")                 else ("my", None)
            self.start_wave(kind, arg)

    def start_wave(self, kind, arg=None):
        """Собрать очередь «волны» и запустить."""
        w = self.win
        lib = list(w.library)
        if not lib:
            return
        if kind == "mood_day":
            w._build_smart_playlist()
            self._wave_kind = ("mood_day", None)
            self.wave.set_mood("Настроение дня", "mood_day", None)
            self.wave.wheel.set_active(("mood_day", None))
            self.wave.hint.setText("Время суток, погода и звучание ваших треков")
            self.show_page("wave")
            return
        if kind == "track_day":
            w._play_track_of_the_day()
            return
        prof = self.profiles() or {}
        hist = w.settings.get("play_history", {})
        rng = random.Random()
        name = "Моя волна"
        pool = lib
        if kind == "mood" and prof:
            fn, hi = next((m[2], m[3]) for m in MOODS if m[0] == arg)
            scored = [(fn(prof[str(t.get("path", ""))]), t) for t in lib if str(t.get("path", "")) in prof]
            scored.sort(key=lambda x: -x[0] if hi else x[0])
            pool = [t for _, t in scored[:max(20, len(scored) // 3)]]
            name = next(m[1] for m in MOODS if m[0] == arg)
        elif kind == "genre" and prof:
            pool = [t for t in lib if arg in prof.get(str(t.get("path", "")), {}).get("families", [])[:2]]
            try:
                from music_intel import GENRE_RU
                name = GENRE_RU.get(arg, arg).capitalize()
            except Exception:                        # noqa: BLE001
                name = arg
        elif kind == "fresh":
            pool = [t for t in lib if int(hist.get(str(t.get("path", "")), {}).get("count", 0)) == 0] or lib
            name = "Незнакомое"
        elif kind == "fav":
            pool = sorted(lib, key=lambda t: -int(hist.get(str(t.get("path", "")), {}).get("count", 0)))[:60]
            name = "Любимое"
        elif kind == "artist":
            pool = [t for t in lib if _artist_main(t.get("artist", "")) == arg]
            name = arg
        elif kind == "my" and self._wave_tune():
            name = "Моя волна"                       # с настройками окошка «Настроить Мою волну»
            from ya_wave_tune import build_pool
            pool = build_pool(lib, prof, hist, self._liked_paths(), self._wave_tune(), rng,
                              artist_of=lambda t: _artist_main(t.get("artist", "")))
        elif kind == "my":
            name = "Моя волна"
            # взвешенная случайная выборка: любимое выпадает чаще, но порядок и первый трек — каждый раз новые
            # (ключ Efraimidis–Спиракиса: rand^(1/вес)); без повторов исполнителя подряд — ниже
            pool = sorted(lib, key=lambda t: -(rng.random() ** (1.0 / (1.0 + int(
                hist.get(str(t.get("path", "")), {}).get("count", 0)) ** 0.5))))
        pool = list(pool) or lib
        if kind != "my":
            rng.shuffle(pool)
        # без двух одинаковых исполнителей подряд
        out = []
        rest = pool[:]
        while rest and len(out) < 60:
            last = _artist_main(out[-1].get("artist", "")) if out else None
            j = next((k for k, t in enumerate(rest) if _artist_main(t.get("artist", "")) != last), 0)
            out.append(rest.pop(j))
        # первый трек — случайный из начала подборки и не тот, что только что играл
        cur = w.library[w.current_index] if (w.queue_context == "library" and 0 <= w.current_index < len(w.library))             else None
        if len(out) > 1:
            head = [t for t in out[:min(len(out), 12)] if not cur or t.get("path") != cur.get("path")] or out[:1]
            first = rng.choice(head)
            out.remove(first)
            out.insert(0, first)
        w.playlists[WAVE_PL] = {"tracks": out, "desc": f"auto: {name}", "cover": ""}
        w._ya_save_playlists()
        self._wave_kind = (kind, arg)
        self.wave.set_mood(name, kind, arg)
        self.wave.wheel.set_active((kind, arg))
        hints = {"my": "Всё, что вы любите, и немного нового", "fresh": "Треки, которые вы ещё не слушали",
                 "fav": "То, что вы слушаете чаще всего", "artist": f"Треки исполнителя {arg}",
                 "genre": f"{name}: подборка из вашей коллекции", "mood": "Подобрано по звучанию треков"}
        base = hints.get(kind, "")
        if kind == "my" and self._wave_tune():
            from ya_wave_tune import summary
            base = summary(self._wave_tune()) or base
        self._wave_base_hint = f"{base}  ·  {len(out)} треков"
        self.wave.hint.setText(self._wave_base_hint)
        w._play_index(0, WAVE_PL)
        self.show_page("wave")
        self._wave_discover(out, name)

    # ── новые похожие треки в волне (из интернета) ─────────────────────── #

    NEW_PL = "Новое из волны"

    def _wave_discover(self, pool, name):
        """Ищет в интернете треки, ПОХОЖИЕ на треки волны, которых нет в
        коллекции (радио YouTube Music / «похожие» SoundCloud), скачивает их
        по 2–3 наперёд и вставляет в очередь волны между знакомыми."""
        self._disc_gen = getattr(self, "_disc_gen", 0) + 1
        self._disc_pool, self._disc_pending, self._disc_added = [], 0, 0
        if not self.win.settings.get("wave_new", True) or not pool:
            return
        gen = self._disc_gen
        seeds = [t for t in pool if (t.get("artist") or "").strip()][:5] or pool[:3]
        lib = list(self.win.library)
        done_urls = set(self.win.settings.get("online_downloaded", {}).keys())
        win = self.win

        def work():
            try:
                import online_search
                from track_identity import norm as _n
            except Exception as e:                       # noqa: BLE001
                print("[wave new]", e)
                return
            have = {(_n(_artist_main(x.get("artist", ""))), _n(sanitize_title(x.get("title", "")))) for x in lib}
            have_t = {k[1] for k in have}
            per_seed = []
            for sd in seeds:
                if gen != self._disc_gen:
                    return
                try:
                    rel = online_search.radio(sanitize_title(sd.get("title", "")), _artist_main(sd.get("artist", "")), 20)
                except Exception as e:                   # noqa: BLE001
                    print("[wave new] radio:", e)
                    rel = []
                lst = []
                for t in rel:
                    key = (_n(_artist_main(t.get("artist", ""))), _n(t.get("title", "")))
                    if key in have or key[1] in have_t or t.get("url") in done_urls:
                        continue
                    if (t.get("duration") or 0) > 600:
                        continue                         # миксы/часовые видео — мимо
                    lst.append(t)
                per_seed.append(lst)
            # чередуем «семена», без повторов
            out, seen = [], set()
            for k in range(max([len(x) for x in per_seed] + [0])):
                for lst in per_seed:
                    if k < len(lst):
                        t = lst[k]
                        key = (_n(t.get("artist", "")), _n(t.get("title", "")))
                        if key not in seen:
                            seen.add(key)
                            out.append(t)
            win._ui_bridge.call.emit(lambda: self._disc_ready(gen, out))
        _threading.Thread(target=work, daemon=True).start()

    def _disc_ready(self, gen, cands):
        if gen != self._disc_gen:
            return
        self._disc_pool = cands[:40]
        self._update_wave_hint()
        self._disc_feed()

    def _disc_feed(self):
        """Держим 2 новых трека скачанными/качающимися наперёд."""
        w = self.win
        if w.queue_context != WAVE_PL:
            return
        tracks = w.playlists.get(WAVE_PL, {}).get("tracks", [])
        ahead = sum(1 for t in tracks[w.current_index + 1:] if t.get("wave_new"))
        gen = self._disc_gen
        while self._disc_pool and self._disc_pending + ahead < 2:
            t = self._disc_pool.pop(0)
            self._disc_pending += 1
            w._quick_download(t["url"], playlist=self.NEW_PL,
                              cb=lambda st, v, t=t, gen=gen: self._disc_cb(gen, t, st, v),
                              meta={"title": t.get("title"), "artist": t.get("artist"),
                                    "album": t.get("album") or "", "thumb_url": t.get("thumb_url") or ""})

    def _disc_cb(self, gen, t, st, v):
        if st not in ("done", "error"):
            return
        if gen != self._disc_gen:
            return
        self._disc_pending = max(0, self._disc_pending - 1)
        w = self.win
        if st == "done":
            lt = next((x for x in w.library if x.get("path") == v), None)
            pl = w.playlists.get(WAVE_PL)
            if lt is not None and pl is not None:
                item = dict(lt)
                item["wave_new"] = True
                cur = w.current_index if w.queue_context == WAVE_PL else -1
                pos = min(len(pl["tracks"]), max(cur + 2, 0) + random.randint(0, 2))
                pl["tracks"].insert(pos, item)
                w._ya_save_playlists()
                self._disc_added += 1
                self._update_wave_hint()
        self._disc_feed()

    def _update_wave_hint(self):
        base = getattr(self, "_wave_base_hint", "")
        extra = ""
        if self._disc_added or self._disc_pool:
            extra = f"  ·  + новое для вас: {self._disc_added}"
        t = self.current_track()
        if t is not None and t.get("wave_new"):
            self.wave.hint.setText("Новое для вас · похоже на то, что вы слушаете" + extra)
        else:
            self.wave.hint.setText(base + extra)

    # ── «Коллекция» ──────────────────────────────────────────────────── #

    def _build_collection(self):
        page = QWidget(); page.setObjectName("YaPage")
        v = QVBoxLayout(page)
        v.setContentsMargins(32, 22, 24, 10)
        v.setSpacing(10)
        top = QHBoxLayout()
        self.c_title = QLabel("Коллекция"); self.c_title.setObjectName("YaH1")
        top.addWidget(self.c_title)
        top.addStretch(1)
        self.c_search = QLineEdit()
        self.c_search.setPlaceholderText("Поиск в коллекции")
        self.c_search.setFixedWidth(230)
        self.c_search.addAction(ya_icon("search", 16, "#8a8a8a"), QLineEdit.ActionPosition.LeadingPosition)
        self.c_search.textChanged.connect(lambda _: self._coll_apply())
        top.addWidget(self.c_search)
        for ic, tip, fn in (("plus", "Добавить файлы", self.win._add_files),
                            ("folder", "Добавить папку", self.win._add_folder),
                            ("download", "Скачать по ссылке", self.win._open_downloader)):
            b = icon_button(ic, tip, 38, 20)
            b.clicked.connect(fn)
            top.addWidget(b)
        v.addLayout(top)
        self.c_chips_w = QWidget(); self.c_chips_w.setObjectName("YaPage")
        self.c_chips = QHBoxLayout(self.c_chips_w)
        self.c_chips.setContentsMargins(0, 0, 0, 0)
        self.c_chips.setSpacing(8)
        v.addWidget(self.c_chips_w)
        self.c_list = TrackListView(self)
        self.c_selbar = SelectionBar(self, self.c_list)
        v.addWidget(self.c_selbar)
        v.addWidget(self.c_list, 1)
        self.c_count = QLabel(""); self.c_count.setObjectName("YaSub")
        v.addWidget(self.c_count)
        self._dirty.add("collection")
        return page

    def _fill_collection(self):
        _clear_layout(self.c_chips)
        g = QButtonGroup(self.c_chips_w)
        g.setExclusive(True)
        opts = [("all", None, "Всё"), ("liked", None, "Мне нравится")]
        prof = self.profiles()
        if prof:
            opts += [("mood", m[0], m[1]) for m in MOODS]
            try:
                from music_intel import GENRE_RU
            except Exception:                        # noqa: BLE001
                GENRE_RU = {}
            fam_count = {}
            for pr in prof.values():
                for f in pr.get("families", [])[:1]:
                    fam_count[f] = fam_count.get(f, 0) + 1
            for f, n in sorted(fam_count.items(), key=lambda x: -x[1])[:7]:
                if n >= 3:
                    opts.append(("genre", f, GENRE_RU.get(f, f).capitalize()))
        for kind, arg, text in opts:
            b = chip(text)
            g.addButton(b)
            b.setChecked((kind, arg) == self._coll_filter)
            b.clicked.connect(lambda _=False, k=kind, a=arg: self._set_coll_filter(k, a))
            self.c_chips.addWidget(b)
        self.c_chips.addStretch(1)
        if prof is None:
            note = QLabel("жанры и настроения появятся после анализа библиотеки")
            note.setObjectName("YaSub")
            self.c_chips.addWidget(note)
        self._coll_apply()

    def _set_coll_filter(self, kind, arg):
        self._coll_filter = (kind, arg)
        self._coll_apply()

    def _coll_apply(self):
        kind, arg = self._coll_filter
        lib = self.win.library
        prof = self._profiles or {}
        if kind == "liked":
            tracks = self.win.playlists.get(LIKED, {}).get("tracks", [])
            rows = [{"t": t, "ctx": LIKED, "i": i} for i, t in enumerate(tracks)]
        else:
            if kind == "mood" and prof:
                fn, hi = next((m[2], m[3]) for m in MOODS if m[0] == arg)
                sc = [(fn(prof[str(t.get("path", ""))]), i, t) for i, t in enumerate(lib)
                      if str(t.get("path", "")) in prof]
                sc = [x for x in sc if (x[0] >= 0.58 if hi else x[0] <= 0.42)]
                sc.sort(key=lambda x: -x[0] if hi else x[0])
                rows = [{"t": t, "ctx": "library", "i": i} for _, i, t in sc]
            elif kind == "genre" and prof:
                rows = [{"t": t, "ctx": "library", "i": i} for i, t in enumerate(lib)
                        if arg in prof.get(str(t.get("path", "")), {}).get("families", [])[:2]]
            else:
                rows = [{"t": t, "ctx": "library", "i": i} for i, t in enumerate(lib)]
                rows.reverse()                       # новые сверху
        q = self.c_search.text().strip().lower()
        if q:
            from search_util import track_matches
            rows = [r for r in rows if track_matches(q, r["t"])]
        self.c_list.set_rows(rows)
        self.c_count.setText(f"{len(rows)} треков  ·  Ctrl/Shift + клик — выделить несколько, "
                             f"Ctrl+A — все, Delete — удалить выделенные")

    # ── альбомы (треки с одинаковым тегом «альбом») ──────────────────── #

    def album_index(self):
        """{ключ: {name, artist, tracks, cover}} — группы треков коллекции по тегу «альбом»."""
        groups = {}
        for t in self.win.library:
            a = " ".join(str(t.get("album") or "").split())
            if a.lower() in _NO_ALBUM:
                continue
            groups.setdefault(a.lower(), []).append(t)
        out = {}
        for k, ts in groups.items():
            names = {}
            for t in ts:
                n = " ".join(str(t.get("album")).split())
                names[n] = names.get(n, 0) + 1
            arts = {}
            for t in ts:
                a = _artist_main(t.get("artist", "")) or "Неизвестный исполнитель"
                arts[a] = arts.get(a, 0) + 1
            top_a, n_a = max(arts.items(), key=lambda x: x[1])
            cover = next((t.get("cover") for t in ts if t.get("cover") and Path(t["cover"]).exists()), "")
            out[k] = {"key": k, "name": max(names.items(), key=lambda x: x[1])[0],
                      "artist": top_a if n_a >= len(ts) * 0.6 else "Разные исполнители",
                      "tracks": ts, "cover": cover,
                      "dur": sum(float(t.get("duration") or 0) for t in ts)}
        return out

    def _build_albums(self):
        page = QWidget(); page.setObjectName("YaPage")
        v = QVBoxLayout(page)
        v.setContentsMargins(32, 22, 24, 10)
        v.setSpacing(10)
        top = QHBoxLayout()
        h = QLabel("Альбомы"); h.setObjectName("YaH1")
        top.addWidget(h)
        top.addStretch(1)
        self.al_singles = QCheckBox("показывать синглы")
        self.al_singles.setChecked(bool(self.win.settings.get("ya_album_singles", False)))
        self.al_singles.setToolTip("Альбомы, в которых у вас всего один трек")
        self.al_singles.toggled.connect(self._albums_singles)
        top.addWidget(self.al_singles)
        self.al_sort = QComboBox()
        for txt, key in (("По названию", "name"), ("По исполнителю", "artist"), ("Больше треков", "count"),
                         ("Недавно добавленные", "recent")):
            self.al_sort.addItem(txt, key)
        self.al_sort.setCurrentIndex(max(0, self.al_sort.findData(self.win.settings.get("ya_album_sort", "name"))))
        self.al_sort.currentIndexChanged.connect(lambda _: self._albums_apply())
        top.addWidget(self.al_sort)
        self.al_search = QLineEdit()
        self.al_search.setPlaceholderText("Альбом или исполнитель")
        self.al_search.setFixedWidth(220)
        self.al_search.addAction(ya_icon("search", 16, "#8a8a8a"), QLineEdit.ActionPosition.LeadingPosition)
        self.al_search.textChanged.connect(lambda _: self._albums_apply())
        top.addWidget(self.al_search)
        v.addLayout(top)
        self.al_grid = CardGrid()
        v.addWidget(self.al_grid, 1)
        self.al_count = QLabel(""); self.al_count.setObjectName("YaSub")
        v.addWidget(self.al_count)
        self._albums = {}
        self._dirty.add("albums")
        return page

    def _albums_singles(self, on):
        self.win.settings["ya_album_singles"] = bool(on)
        self._albums_apply()

    def _fill_albums(self):
        self._albums = self.album_index()
        self._albums_apply()

    def _albums_apply(self):
        sort = self.al_sort.currentData() or "name"
        self.win.settings["ya_album_sort"] = sort
        q = self.al_search.text().strip().lower()
        singles = self.al_singles.isChecked()
        items = [a for a in self._albums.values() if singles or len(a["tracks"]) >= 2]
        if q:
            from search_util import matches
            items = [a for a in items if matches(q, a["name"], a["artist"],
                                                 " ".join(t.get("artist") or "" for t in a["tracks"]))]
        if sort == "artist":
            items.sort(key=lambda a: (a["artist"].lower(), a["name"].lower()))
        elif sort == "count":
            items.sort(key=lambda a: (-len(a["tracks"]), a["name"].lower()))
        elif sort == "recent":
            pos = {id(t): i for i, t in enumerate(self.win.library)}
            items.sort(key=lambda a: -max(pos.get(id(t), 0) for t in a["tracks"]))
        else:
            items.sort(key=lambda a: a["name"].lower())
        cards = []
        for a in items:
            n = len(a["tracks"])
            c = Card(a["name"], f"{a['artist']} · {n} {_plural(n, 'трек', 'трека', 'треков')}",
                     a["cover"], 160, seed=a["name"])
            c.clicked.connect(lambda k=a["key"]: self.open_album(k))
            cards.append(c)
        self.al_grid.set_cards(cards)
        total = len(self._albums)
        if not self._albums:
            self.al_count.setText("Альбомы появятся, когда у треков будет заполнен тег «альбом» "
                                  "(у скачанных с YouTube Music он ставится сам)")
        else:
            self.al_count.setText(f"{len(items)} из {total} альбомов" if len(items) != total
                                  else f"{total} альбомов")

    @staticmethod
    def _track_no(path):
        """(диск, номер трека) из тегов — для правильного порядка в альбоме."""
        try:
            import mutagen
            m = mutagen.File(path, easy=True)
            if m is None or not m.tags:
                return (99, 999)

            def num(key):
                v = (m.tags.get(key) or [""])[0]
                v = str(v).split("/")[0].strip()
                return int(v) if v.isdigit() else None
            return (num("discnumber") or 1, num("tracknumber") or 999)
        except Exception:                            # noqa: BLE001
            return (99, 999)

    def open_album(self, key, keep_history=False):
        if not self._albums or key not in self._albums or "albums" in self._dirty:
            self._albums = self.album_index()
        a = self._albums.get(key)
        if not a:
            return
        tracks = list(a["tracks"])
        order = {id(t): (self._track_no(t.get("path", "")), i) for i, t in enumerate(tracks)}
        tracks.sort(key=lambda t: order[id(t)])
        self._pl_name, self._pl_kind = key, "album"
        self.pp_cover.size_ = 180
        self.pp_cover.setFixedSize(180, 180)
        self.pp_cover._path = None
        self.pp_cover.radius = 12
        self.pp_cover.set_cover(a["cover"], a["name"])
        self.pp_kind.setText("Альбом")
        self.pp_name.setText(a["name"])
        total = a["dur"]
        n = len(tracks)
        self.pp_meta.setText(f"{a['artist']} · {n} {_plural(n, 'трек', 'трека', 'треков')} · "
                             f"{int(total // 3600)} ч {int(total % 3600 // 60)} мин" if total >= 3600 else
                             f"{a['artist']} · {n} {_plural(n, 'трек', 'трека', 'треков')} · {int(total // 60)} мин")
        self.pp_wave.hide()
        self.pp_more.show()
        self.pp_add.hide()
        self.pp_hint.hide()
        self.pp_empty.hide()
        self.pp_list.show()
        self._pp_search_reset(True, key)
        self._pp_filters_reset(tracks, key)
        self.pp_list.set_rows([{"t": t, "ctx": ALBUM_PL, "i": i, "alb_tracks": tracks, "alb_name": a["name"],
                                "owned": True} for i, t in enumerate(tracks)])
        self.show_page("playlist", remember=not keep_history)
        for i in range(self.pl_side.count()):
            self.pl_side.item(i).setSelected(False)
        self._album_fetch_full(key, a, tracks)

    def _album_fetch_full(self, key, a, local_tracks):
        """Подгрузить полный список треков альбома (YouTube Music) и показать ещё не скачанные."""
        cache = self.__dict__.setdefault("_alb_cache", {})
        self._alb_req = getattr(self, "_alb_req", 0) + 1
        req = self._alb_req
        if key in cache:
            self._album_merge(req, key, a, local_tracks, cache[key])
            return
        artist = a["artist"] if a["artist"] != "Разные исполнители" else ""

        def work():
            alb = None
            try:
                import online_search
                alb = online_search.album_tracks(artist, a["name"])
            except Exception as e:                   # noqa: BLE001
                print("[album full]", e)
            self.win._ui_bridge.call.emit(lambda: (cache.__setitem__(key, alb) if alb else None,
                                                   self._album_merge(req, key, a, local_tracks, alb)))
        _threading.Thread(target=work, daemon=True).start()

    def _album_merge(self, req, key, a, local_tracks, alb):
        if req != getattr(self, "_alb_req", 0) or self._pl_kind != "album" or self._pl_name != key:
            return
        remote = (alb or {}).get("tracks") or []
        if not remote:
            return
        from track_identity import similarity
        used, matched = set(), {}
        for ri, rt in enumerate(remote):
            best, bs = None, 0.0
            for li, lt in enumerate(local_tracks):
                if li in used:
                    continue
                s = similarity(sanitize_title(rt.get("title", "")), sanitize_title(lt.get("title", "")))
                if s > bs:
                    best, bs = li, s
            if best is not None and bs >= 0.82:
                used.add(best)
                matched[ri] = best
        ordered = [local_tracks[matched[ri]] for ri in range(len(remote)) if ri in matched] + \
                  [lt for li, lt in enumerate(local_tracks) if li not in used]       # лишние локальные — в конец
        pos = {id(t): i for i, t in enumerate(ordered)}
        rows = []
        for ri, rt in enumerate(remote):
            if ri in matched:
                lt = local_tracks[matched[ri]]
                rows.append({"t": lt, "ctx": ALBUM_PL, "i": pos[id(lt)], "alb_tracks": ordered,
                             "alb_name": a["name"], "owned": True})
            else:
                ph = {"title": rt.get("title", ""), "artist": rt.get("artist", "") or a["artist"],
                      "duration": rt.get("duration", 0), "cover": a["cover"], "path": "", "album": a["name"]}
                rows.append({"t": ph, "ctx": ALBUM_PL, "remote": True, "url": rt.get("url", ""),
                             "remote_t": rt, "alb_name": a["name"]})
        for lt in ordered[len(matched):]:
            rows.append({"t": lt, "ctx": ALBUM_PL, "i": pos[id(lt)], "alb_tracks": ordered,
                         "alb_name": a["name"], "owned": True})
        self.pp_list.set_rows(rows)
        total = len(rows)
        have = sum(1 for r in rows if r.get("owned"))
        dur = sum(float(r["t"].get("duration") or 0) for r in rows)
        self.pp_meta.setText(f"{a['artist']} · {total} {_plural(total, 'трек', 'трека', 'треков')} · "
                             f"скачано {have} из {total} · "
                             + (f"{int(dur // 3600)} ч {int(dur % 3600 // 60)} мин" if dur >= 3600 else f"{int(dur // 60)} мин"))

    def _album_menu(self, key):
        a = (self._albums or {}).get(key)
        if not a:
            return
        m = QMenu(self)
        m.addAction("Сохранить как плейлист", lambda: self._album_to_playlist(a))
        if a["artist"] != "Разные исполнители":
            m.addAction(f"Исполнитель «{a['artist']}»", lambda: self.open_artist(a["artist"]))
        m.exec(QCursor.pos())

    def _album_to_playlist(self, a):
        name = a["name"]
        if name in self.win.playlists and name not in AUTO_PLAYLISTS:
            name = f"{name} — {a['artist']}"
        rows = self.pp_list.model_.rows
        tracks = [dict(r["t"]) for r in rows] if self._pl_kind == "album" else [dict(t) for t in a["tracks"]]
        self.win.playlists[name] = {"tracks": tracks, "desc": f"Альбом · {a['artist']}", "cover": a.get("cover", "")}
        self.win._ya_save_playlists()
        self.refresh_sidebar()
        self.open_playlist(name)

    # ── плейлист / исполнитель ───────────────────────────────────────── #

    def _build_playlist_page(self):
        page = QWidget(); page.setObjectName("YaPage")
        v = QVBoxLayout(page)
        v.setContentsMargins(32, 18, 24, 10)
        v.setSpacing(12)
        b = icon_button("back", "Назад", 34, 20)
        b.clicked.connect(self.go_back)
        v.addWidget(b)
        head = QHBoxLayout(); head.setSpacing(24)
        self.pp_cover = RoundCover(180, 12)
        head.addWidget(self.pp_cover)
        col = QVBoxLayout(); col.setSpacing(6)
        col.addStretch(1)
        self.pp_kind = QLabel("Плейлист"); self.pp_kind.setObjectName("YaSub")
        self.pp_name = QLabel(""); self.pp_name.setStyleSheet("font-size:40px;font-weight:900;")
        self.pp_meta = QLabel(""); self.pp_meta.setObjectName("YaSub")
        col.addWidget(self.pp_kind); col.addWidget(self.pp_name); col.addWidget(self.pp_meta)
        btns = QHBoxLayout(); btns.setSpacing(10)
        self.pp_play = QPushButton("  Слушать")
        self.pp_play.setObjectName("YaYellow")
        self.pp_play.setIcon(ya_icon("play", 18, "#000000"))
        self.pp_play.setMinimumHeight(40)
        self.pp_play.clicked.connect(lambda: self._pp_play(False))
        self.pp_shuffle = QPushButton("  Вперемешку")
        self.pp_shuffle.setIcon(ya_icon("shuffle", 18, "#ffffff"))
        self.pp_shuffle.setMinimumHeight(40)
        self.pp_shuffle.clicked.connect(lambda: self._pp_play(True))
        self.pp_wave = QPushButton("  Волна по артисту")
        self.pp_wave.setIcon(ya_icon("wave", 18, "#ffffff"))
        self.pp_wave.setMinimumHeight(40)
        self.pp_wave.clicked.connect(lambda: self.start_wave("artist", self._pl_name))
        self.pp_add = QPushButton("  Добавить треки")
        self.pp_add.setIcon(ya_icon("plus", 18, "#ffffff"))
        self.pp_add.setMinimumHeight(40)
        self.pp_add.setToolTip("Выбрать треки из коллекции и добавить в этот плейлист")
        self.pp_add.clicked.connect(self._pp_add)
        self.pp_more = icon_button("dots", "Ещё: обложка, переименовать, удалить плейлист", 40, 20)
        self.pp_more.clicked.connect(self._pp_menu)
        for b in (self.pp_play, self.pp_shuffle, self.pp_wave, self.pp_add, self.pp_more):
            btns.addWidget(b)
        btns.addStretch(1)
        col.addLayout(btns)
        col.addStretch(1)
        head.addLayout(col, 1)
        v.addLayout(head)
        self.pp_hint = QLabel("Перетаскивайте треки мышкой, чтобы поменять порядок  ·  Ctrl/Shift + клик — "
                              "выделить несколько  ·  «⋯» или правая кнопка — убрать, теги, удалить")
        self.pp_hint.setObjectName("YaSub")
        v.addWidget(self.pp_hint)
        self.pp_empty = QLabel("Здесь пока пусто.\nНажмите «Добавить треки» — или в любом списке треков "
                               "правая кнопка → «Добавить в плейлист».")
        self.pp_empty.setObjectName("YaSub")
        self.pp_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.pp_empty.setStyleSheet("font-size:15px;padding:40px;")
        self.pp_empty.hide()
        v.addWidget(self.pp_empty)
        self.pp_search = QLineEdit()
        self.pp_search.setPlaceholderText("Поиск в плейлисте: исполнитель, название…")
        self.pp_search.addAction(ya_icon("search", 16, "#8a8a8a"), QLineEdit.ActionPosition.LeadingPosition)
        self.pp_search.setClearButtonEnabled(True)
        self.pp_search.setMaximumWidth(420)
        self.pp_search.textChanged.connect(self._pp_filter_changed)
        v.addWidget(self.pp_search)
        self.pp_fbar = QWidget()
        self.pp_fbar.setObjectName("YaPageInner")
        fh = QHBoxLayout(self.pp_fbar)
        fh.setContentsMargins(0, 0, 0, 0)
        fh.setSpacing(12)
        self.pp_genre = QComboBox()
        self.pp_genre.setMinimumWidth(210)
        self.pp_genre.setToolTip("Показать только треки выбранного жанра")
        self.pp_genre.currentIndexChanged.connect(self._pp_apply_filters)
        fh.addWidget(self.pp_genre)
        self.pp_mood_btns = {}
        mood_btns = []
        mg = QButtonGroup(page)
        mg.setExclusive(True)
        for key, text in (("any", "Любое настроение"),) + tuple((m[0], m[1]) for m in MOODS):
            b = chip(text)
            mg.addButton(b)
            b.clicked.connect(self._pp_apply_filters)
            self.pp_mood_btns[key] = b
            mood_btns.append(b)
        self.pp_mood_btns["any"].setChecked(True)
        self.pp_mood_group = seg_group(*mood_btns)
        fh.addWidget(self.pp_mood_group)
        fh.addStretch(1)
        v.addWidget(self.pp_fbar)
        self.pp_nores = QLabel("Ничего не нашлось")
        self.pp_nores.setObjectName("YaSub")
        self.pp_nores.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.pp_nores.setStyleSheet("font-size:15px;padding:30px;")
        self.pp_nores.hide()
        v.addWidget(self.pp_nores)
        self.pp_list = TrackListView(self)
        self.pp_selbar = SelectionBar(self, self.pp_list)
        v.addWidget(self.pp_selbar)
        v.addWidget(self.pp_list, 1)
        self._pl_name = None
        self._pl_kind = "playlist"
        return page

    def _pp_filter_changed(self, text):
        self.pp_list.set_filter(text)
        self._pp_update_nores()

    def _pp_update_nores(self):
        active = bool(self.pp_search.text().strip()) or getattr(self.pp_list, "pred", None) is not None
        self.pp_nores.setVisible(active and self.pp_list.model_.rowCount() == 0 and bool(self.pp_list._all_rows))

    # ── фильтры по жанру и настроению ── #

    def _pp_filters_reset(self, tracks, key):
        """Подготовить фильтры для открытого списка. Тот же список (обновился) — выбор сохраняется."""
        self._pp_tracks = list(tracks)
        same = key is not None and key == getattr(self, "_pp_fkey", None)
        self._pp_fkey = key
        prof = self.profiles() or {}
        counts = {}
        for t in tracks:
            for f in (prof.get(str(t.get("path", "")), {}).get("families") or [])[:2]:
                counts[f] = counts.get(f, 0) + 1
        try:
            from music_intel import GENRE_RU
        except Exception:                            # noqa: BLE001
            GENRE_RU = {}
        keep = self.pp_genre.currentData() if same else None
        self.pp_genre.blockSignals(True)
        self.pp_genre.clear()
        self.pp_genre.addItem("Все жанры", None)
        for fam, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            self.pp_genre.addItem(f"{str(GENRE_RU.get(fam, fam)).capitalize()}  ·  {n}", fam)
        i = self.pp_genre.findData(keep) if keep else 0
        self.pp_genre.setCurrentIndex(max(0, i))
        self.pp_genre.blockSignals(False)
        show_genre = bool(counts)
        show_mood = bool(self.win.settings.get("mood_filter", False)) and bool(prof)
        self._pp_show_mood = show_mood
        self.pp_genre.setVisible(show_genre)
        self.pp_mood_group.setVisible(show_mood)
        self.pp_fbar.setVisible(show_genre or show_mood)
        if not same:
            self.pp_mood_btns["any"].setChecked(True)
        self._pp_apply_filters()

    def _pp_apply_filters(self, *_):
        prof = self.profiles() or {}
        fam = self.pp_genre.currentData() if self.pp_genre.count() > 1 else None
        mood = next((k for k, b in self.pp_mood_btns.items() if b.isChecked()), "any") \
            if getattr(self, "_pp_show_mood", False) else "any"
        if not fam and mood == "any":
            self.pp_list.set_pred(None)
            self._pp_update_nores()
            return
        rule = next((m for m in MOODS if m[0] == mood), None)

        def pred(t):
            pr = prof.get(str(t.get("path", "")))
            if not pr:
                return False
            if fam and fam not in (pr.get("families") or [])[:2]:
                return False
            if rule is not None:
                x = float(rule[2](pr))
                return x >= 0.60 if rule[3] else x <= 0.40
            return True
        self.pp_list.set_pred(pred)
        self._pp_update_nores()

    def _pp_search_reset(self, show: bool, key=None):
        """Другой плейлист/альбом: поиск очищается. Тот же (обновился после правки) — запрос сохраняется."""
        same = key is not None and key == getattr(self, "_pp_key", None)
        self._pp_key = key
        if not same:
            self.pp_search.blockSignals(True)
            self.pp_search.clear()
            self.pp_search.blockSignals(False)
            self.pp_list.set_filter("")
            self.pp_nores.hide()
        else:
            QTimer.singleShot(0, lambda: self._pp_filter_changed(self.pp_search.text()))
        self.pp_search.setVisible(show)

    def open_playlist(self, name, keep_history=False):
        if not name or name not in self.win.playlists:
            return
        self._pl_name, self._pl_kind = name, "playlist"
        pl = self.win.playlists[name]
        tr = pl.get("tracks", [])
        cov = pl.get("cover") or (tr[0].get("cover", "") if tr else "")
        self.pp_cover.size_ = 180
        self.pp_cover.setFixedSize(180, 180)
        self.pp_cover._path = None
        self.pp_cover.radius = 12
        self.pp_cover.set_cover(cov, name)
        self.pp_kind.setText("Плейлист")
        self.pp_name.setText(name)
        total = sum(float(t.get("duration") or 0) for t in tr)
        self.pp_meta.setText(f"{len(tr)} треков · {int(total // 3600)} ч {int(total % 3600 // 60)} мин"
                             + (f"\n{pl.get('desc')}" if pl.get("desc") and not str(pl.get("desc")).startswith("auto") else ""))
        self.pp_wave.hide()
        self.pp_more.show()
        user_pl = name not in AUTO_PLAYLISTS
        self.pp_add.setVisible(user_pl)
        self.pp_hint.setVisible(user_pl and bool(tr))
        self.pp_empty.setVisible(not tr)
        self.pp_list.setVisible(bool(tr))
        self._pp_search_reset(bool(tr), name)
        self._pp_filters_reset(tr, name)
        self.pp_list.set_rows([{"t": t, "ctx": name, "i": i} for i, t in enumerate(tr)])
        self.show_page("playlist", remember=not keep_history)
        for i in range(self.pl_side.count()):
            it = self.pl_side.item(i)
            it.setSelected(it.data(Qt.ItemDataRole.UserRole) == name)

    def open_artist(self, artist):
        """Карточка исполнителя: данные с серверов + треки из коллекции
        (даже если в коллекции его нет — для «Похожих исполнителей»)."""
        if not artist:
            return
        al = artist.lower()
        tracks = [t for t in self.win.library if _artist_main(t.get("artist", "")).lower() == al
                  or al in (t.get("artist") or "").lower()]
        self.pages["artist"].load(artist, tracks)
        self.show_page("artist")

    def _pp_play(self, shuffle):
        rows = self.pp_list.model_.rows
        if not rows:
            return
        if shuffle != bool(self.win.shuffle):
            self.win.btn_shuffle.click()
        r = random.choice(rows) if shuffle else rows[0]
        self.play_row(r)

    def _pp_menu(self):
        name = self._pl_name
        if self._pl_kind == "album" and name:
            self._album_menu(name)
            return
        if self._pl_kind != "playlist" or not name:
            return
        m = QMenu(self)
        m.addAction("＋  Добавить треки из коллекции…", lambda: self._pp_add())
        m.addAction("Сменить обложку…", lambda: (self.win._pd_set_cover(name), self.open_playlist(name, True)))
        m.addAction("Переименовать…", lambda: self.win._pd_rename(name))
        m.addAction("Описание…", lambda: self.win._pd_edit_description(name))
        m.addAction("Поделиться файлом…", lambda: getattr(self.win, "share_playlist", lambda *_: None)(name))
        m.addSeparator()
        m.addAction("Удалить плейлист", lambda: self._delete_playlist(name))
        m.exec(QCursor.pos())

    def _pp_add(self):
        name = self._pl_name
        if self._pl_kind == "playlist" and name in self.win.playlists:
            if self.win.pick_tracks_for_playlist(name, self):
                self.open_playlist(name, keep_history=True)

    def refresh_sidebar(self):
        lst = self.pl_side
        lst.clear()
        w = self.win
        names = list(w.playlists.keys())
        if LIKED in names:
            names.remove(LIKED)
            names.insert(0, LIKED)
        for n in names:
            if n in (WAVE_PL, ALBUM_PL):
                continue
            pl = w.playlists[n]
            tr = pl.get("tracks", [])
            cov = pl.get("cover") or (tr[0].get("cover", "") if tr else "")
            it = QListWidgetItem(QIcon(cover_pixmap(cov, 36, 6, seed=n)), f"{n}\nПлейлист · {len(tr)}")
            it.setData(Qt.ItemDataRole.UserRole, n)
            lst.addItem(it)
        nick = w.profile.get("nickname", "user")
        self.nick.setText(nick)
        self.status.setText(w.profile.get("status", ""))
        av = w.profile.get("avatar", "")
        self.avatar.setPixmap(cover_pixmap(av, 36, 18, seed=nick, circle=True))

    # ── «Текст» (как в Я.Музыке) ─────────────────────────────────────── #

    def _build_lyrics(self):
        from lyrics_view import LyricsScrollPanel

        shell = self

        class _LyricsPage(QWidget):
            def paintEvent(self, _):
                p = QPainter(self)
                bg = getattr(shell, "_lyr_bg", None)
                if bg is None or bg.size() != self.size():
                    bg = shell._make_lyrics_bg(self.size())
                    shell._lyr_bg = bg
                    lp = getattr(shell, "l_panel", None)
                    if lp is not None:                   # новый фон — и кусок под панелью текста
                        lp.update()
                p.drawPixmap(0, 0, bg)
                p.end()

        page = _LyricsPage()
        page.setObjectName("YaLyrics")
        h = QHBoxLayout(page)
        h.setContentsMargins(40, 20, 20, 20)
        h.setSpacing(30)
        left = QVBoxLayout(); left.setSpacing(10)
        left.addStretch(1)
        self.l_cover = RoundCover(300, 10)
        left.addWidget(self.l_cover, 0, Qt.AlignmentFlag.AlignHCenter)
        self.l_title = QLabel(""); self.l_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.l_title.setStyleSheet("font-size:15px;font-weight:800;")
        self.l_artist = QLabel(""); self.l_artist.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.l_artist.setObjectName("YaSub")
        left.addWidget(self.l_title)
        left.addWidget(self.l_artist)
        self.l_prog = ProgressLine()
        self.l_prog.setFixedWidth(300)
        self.l_prog.seek_to.connect(self._seek_frac)
        left.addWidget(self.l_prog, 0, Qt.AlignmentFlag.AlignHCenter)
        left.addStretch(1)
        h.addLayout(left)
        rcol = QVBoxLayout()
        top = QHBoxLayout()
        self.l_ai = QPushButton("  Синхронизировать с ИИ")
        self.l_ai.setObjectName("YaChip")
        self.l_ai.setIcon(ya_icon("wave", 16, YELLOW))
        self.l_ai.setIconSize(QSize(16, 16))
        self.l_ai.setFixedHeight(34)
        self.l_ai.setCursor(Qt.CursorShape.PointingHandCursor)
        self.l_ai.setToolTip("Нейросеть Whisper послушает трек (локально) и расставит таймкоды по строкам текста")
        self.l_ai.clicked.connect(self.win.ai_sync_lyrics)
        self.l_ai.hide()
        self.l_ai_lbl = QLabel("")
        self.l_ai_lbl.setObjectName("YaSub")
        top.addWidget(self.l_ai)
        top.addWidget(self.l_ai_lbl)
        top.addStretch(1)
        b = icon_button("chevron_down", "Свернуть текст", 40, 22)
        b.setStyleSheet("QPushButton{background:rgba(255,255,255,0.12);border-radius:20px;}"
                        "QPushButton:hover{background:rgba(255,255,255,0.22);}")
        b.clicked.connect(self.toggle_lyrics)
        top.addWidget(b)
        rcol.addLayout(top)
        self.l_panel = LyricsScrollPanel()
        self.l_panel.seek_cb = self.win._lyrics_seek

        def backdrop(page=page):
            bg = getattr(shell, "_lyr_bg", None)
            if bg is None or bg.size() != page.size():
                bg = shell._make_lyrics_bg(page.size())
                shell._lyr_bg = bg
            return bg, self.l_panel.mapTo(page, QPoint(0, 0))
        self.l_panel.set_backdrop(backdrop)
        rcol.addWidget(self.l_panel, 1)
        h.addLayout(rcol, 1)
        return page

    def _make_lyrics_bg(self, size) -> QPixmap:
        W, H = max(1, size.width()), max(1, size.height())
        pm = QPixmap(W, H)
        pm.fill(QColor(38, 38, 40))
        t = self.current_track()
        path = t.get("cover", "") if t else ""
        if path and Path(path).exists():
            src = load_pixmap(path, 256)
            if not src.isNull():
                import blur_fx                               # гаусс вместо «10×10 → растянуть»
                big = blur_fx.blurred_cover(src, W, H, max(W, H) / 22.0)
                p = QPainter(pm)
                p.drawImage(0, 0, big)
                p.fillRect(pm.rect(), QColor(20, 20, 22, 185))
                p.end()
        # скруглённые углы — как у контентной панели
        out = QPixmap(W, H)
        out.fill(Qt.GlobalColor.transparent)
        p = QPainter(out)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        clip = QPainterPath(); clip.addRoundedRect(QRectF(0, 0, W, H), 16, 16)
        p.setClipPath(clip)
        p.drawPixmap(0, 0, pm)
        p.end()
        return out

    def toggle_queue(self):
        """Кнопка «Очередь»: страница внутри плеера; повторное нажатие — назад."""
        if self._cur_page == "queue":
            self.go_back()
        else:
            self.show_page("queue")

    def toggle_lyrics(self):
        if self._cur_page == "lyrics":
            self.go_back()
        else:
            self._lyr_src = None
            self.show_page("lyrics")
            self._lyrics_tick(None, None)

    def _lyrics_tick(self, pos, dur):
        lv = self.win.lyrics_view
        lines = getattr(lv, "_lines", None) or []
        if lines is not self._lyr_src:
            self._lyr_src = lines
            self.l_panel.load(lines, bool(getattr(lv, "_has_timings", False)))
        ai = getattr(self.win, "_ai", None)
        running = bool(ai and ai["running"])
        self.l_ai.setVisible(running or (bool(lines) and not getattr(lv, "_has_timings", False)))
        self.l_ai.setText("  Отмена" if running else "  Синхронизировать с ИИ")
        self.l_ai_lbl.setText(ai["status"] if running else "")
        if pos is None:
            try:
                eng = self.win.engine
                pos = float(eng.get_position_smooth() if hasattr(eng, "get_position_smooth")
                            else eng.get_position() or 0)
                dur = float(eng.duration or 0)
            except Exception:                        # noqa: BLE001
                pos = dur = 0.0
        f = float(getattr(self.win.engine, "preset_factor", 1.0) or 1.0)
        if self.is_playing():
            self.l_panel.set_pos_ms(int(pos * 1000 * f))
        self.l_panel.set_time(int(pos * 1000 * f), int(dur * 1000 * f))
        self.l_prog.set_state(pos / dur if dur else 0.0, dur)

    # ── «Настройки» ──────────────────────────────────────────────────── #

    def _build_settings(self):
        page = Page(margins=(32, 22, 32, 24))
        h = QLabel("Настройки и звук"); h.setObjectName("YaH1")
        page.v.addWidget(h)
        row = QHBoxLayout(); row.setSpacing(8); row.setContentsMargins(0, 12, 0, 6)
        for text, fn, ic in (("Темы оформления", None, "palette"),
                             ("Конструктор тем", lambda: self.win._open_theme_studio(), "edit"),
                             ("Импорт из Spotify", self.win._open_spotify_import, "download"),
                             ("Плейлист из файла",
                              lambda: getattr(self.win, "import_playlist_file", lambda *_: None)(), "download"),
                             ("Куда класть файлы тем/плейлистов",
                              lambda: getattr(self.win, "files_help", lambda: None)(), "settings"),
                             ("Режим обложки", self.win._toggle_cover_mode, "cover"),
                             ("Редактор текста", self.win._open_lyrics_editor, "lyrics"),
                             ("Клавиши и окно",
                              lambda: getattr(self.win, "open_player_prefs", lambda: None)(), "settings"),
                             ("Профиль", self.win._open_profile, "user")):
            b = chip(text, checkable=False)
            b.setIcon(ya_icon(ic, 16, "#d0d0d0"))
            b.setIconSize(QSize(16, 16))
            if fn is None:
                b.clicked.connect(lambda _=False, b=b: self.win._winamp_theme_menu(b.mapToGlobal(b.rect().bottomLeft())))
            else:
                b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        page.v.addLayout(row)
        page.v.addWidget(self._build_wave_setting())
        page.v.addSpacing(8)
        page.v.addWidget(self._build_mood_filter_setting())
        page.v.addSpacing(8)
        page.v.addWidget(self._build_boost_setting())
        page.v.addSpacing(10)
        self._settings_slot = QVBoxLayout()
        holder = QWidget(); holder.setObjectName("YaSettingsCard")
        holder.setMaximumWidth(560)
        self._settings_slot.setContentsMargins(10, 8, 10, 12)
        holder.setLayout(self._settings_slot)
        page.v.addWidget(holder)
        page.v.addStretch(1)
        return page

    def _build_wave_setting(self) -> QWidget:
        """Карточка «Моя волна»: тумблер подмешивания новых похожих треков (раньше была кнопкой на странице волны)."""
        card = QFrame()
        card.setObjectName("YaWaveCard")
        card.setStyleSheet("QFrame#YaWaveCard{background:#171717;border-radius:16px;}")
        card.setMaximumWidth(560)
        lay = QHBoxLayout(card)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(16)
        col = QVBoxLayout()
        col.setSpacing(2)
        t = QLabel("Моя волна: новые похожие треки")
        t.setStyleSheet("font-size:14px;font-weight:700;background:transparent;")
        d = QLabel("В волну подмешиваются треки, которых нет в коллекции, но похожие на ваши "
                   "(YouTube Music / SoundCloud). Они скачиваются заранее.")
        d.setObjectName("YaSub")
        d.setWordWrap(True)
        d.setStyleSheet("background:transparent;")
        col.addWidget(t)
        col.addWidget(d)
        lay.addLayout(col, 1)
        self.wave_new_sw = Switch(bool(self.win.settings.get("wave_new", True)))
        self.wave_new_sw.toggled.connect(self._on_wave_new)
        lay.addWidget(self.wave_new_sw, 0, Qt.AlignmentFlag.AlignVCenter)
        return card

    def _build_mood_filter_setting(self) -> QWidget:
        """Карточка-тумблер: фильтр по настроению на страницах плейлистов (по умолчанию выключен)."""
        card = QFrame()
        card.setObjectName("YaWaveCard")
        card.setStyleSheet("QFrame#YaWaveCard{background:#171717;border-radius:16px;}")
        card.setMaximumWidth(560)
        lay = QHBoxLayout(card)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(16)
        col = QVBoxLayout()
        col.setSpacing(2)
        t = QLabel("Фильтр по настроению в плейлистах")
        t.setStyleSheet("font-size:14px;font-weight:700;background:transparent;")
        d = QLabel("Экспериментальная функция: на страницах плейлистов и «Мне нравится» появляются кнопки "
                   "«Бодрое / Спокойное / Весёлое / Грустное». Настроение определяется по звучанию треков.")
        d.setObjectName("YaSub")
        d.setWordWrap(True)
        d.setStyleSheet("background:transparent;")
        col.addWidget(t)
        col.addWidget(d)
        lay.addLayout(col, 1)
        self.mood_filter_sw = Switch(bool(self.win.settings.get("mood_filter", False)))
        self.mood_filter_sw.toggled.connect(self._on_mood_filter)
        lay.addWidget(self.mood_filter_sw, 0, Qt.AlignmentFlag.AlignVCenter)
        return card

    def _build_boost_setting(self) -> QWidget:
        """Карточка «Экстремальная громкость» (100 %…10 000 %) — общий блок плеера."""
        card = QFrame()
        card.setObjectName("YaWaveCard")
        card.setStyleSheet("QFrame#YaWaveCard{background:#171717;border-radius:16px;}")
        card.setMaximumWidth(560)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(6)
        t = QLabel("Экстремальная громкость")
        t.setStyleSheet("font-size:14px;font-weight:700;background:transparent;")
        d = QLabel("Множитель поверх ползунка громкости: 500 % — в 5 раз громче, "
                   "10 000 % — в 100 раз. На больших значениях будет перегруз.")
        d.setObjectName("YaSub")
        d.setWordWrap(True)
        d.setStyleSheet("background:transparent;")
        lay.addWidget(t)
        lay.addWidget(d)
        lay.addWidget(self.win._make_boost_block(compact=True))
        return card

    def _on_mood_filter(self, on):
        self.win.settings["mood_filter"] = bool(on)
        self.win.save_settings()
        if getattr(self, "_pp_tracks", None) is not None:
            self._pp_filters_reset(self._pp_tracks, getattr(self, "_pp_fkey", None))

    def _on_wave_new(self, on):
        self.win.settings["wave_new"] = bool(on)
        self.win.save_settings()

    def adopt_settings(self, panel: QWidget):
        """Правая панель плеера (эквалайзер, пресеты, таймеры…) — сюда."""
        self._settings_host = panel
        self._settings_slot.addWidget(panel)
        panel.show()
        self._restyle_pager(True)

    def _restyle_pager(self, on: bool):
        """«‹ Оформление ›» панели настроек: в этой теме — круглые кнопки и крупный заголовок."""
        w = self.win
        prev, nxt, lbl = (getattr(w, n, None) for n in ("_rp_prev_btn", "_rp_next_btn", "_rp_page_lbl"))
        if prev is None or nxt is None or lbl is None:
            return
        if on:
            self._pager_saved = [(b, b.styleSheet(), b.text(), b.minimumSize(), b.maximumSize()) for b in (prev, nxt)] \
                + [(lbl, lbl.styleSheet(), "", None, None)]
            ic = ya_icon("back", 20, "#e6e6e6")
            for b, ico in ((prev, ic), (nxt, QIcon(ic.pixmap(20, 20).transformed(QTransform().scale(-1, 1))))):
                b.setStyleSheet("")
                b.setObjectName("YaIcon")
                b.setText("")
                b.setIcon(ico)
                b.setIconSize(QSize(20, 20))
                b.setFixedSize(38, 38)
                b.style().unpolish(b); b.style().polish(b)
            lbl.setStyleSheet("color:#ffffff;font-size:14px;font-weight:800;background:transparent;")
        else:
            for wdg, ss, txt, mn, mx in getattr(self, "_pager_saved", []):
                wdg.setStyleSheet(ss)
                if mn is not None:
                    wdg.setObjectName("")
                    wdg.setText(txt)
                    wdg.setIcon(QIcon())
                    wdg.setMinimumSize(mn); wdg.setMaximumSize(mx)
                    wdg.style().unpolish(wdg); wdg.style().polish(wdg)
            self._pager_saved = []

    def release_settings(self):
        self._restyle_pager(False)
        p = self._settings_host
        if p is not None:
            self._settings_slot.removeWidget(p)
        self._settings_host = None
        return p

    def shutdown(self):
        self._timer.stop()
        try:
            self.wave.shutdown()
            self.wave.wheel._timer.stop()
        except RuntimeError:
            pass
        clear_cover_cache()                          # тема закрыта — её обложки в памяти не нужны
        _ICON_CACHE.clear()


# ══════════════════════════════════════════════════════════════════════════ #
#  Онлайн-страница исполнителя (как в Яндекс Музыке)
# ══════════════════════════════════════════════════════════════════════════ #

import re as _re
import threading as _threading


class ArtistHeader(QWidget):
    """Шапка артиста: размытое фото баннером, круглое фото, имя, статистика
    и «серверы» (Deezer / Wikipedia / Last.fm) с индикаторами подключения."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(300)
        self.name = ""
        self.kind = "Исполнитель"
        self.stats = ""
        self.picture = ""
        self.sources = []          # [(имя, состояние: "wait"|"ok"|"off")]
        self._banner = None
        self._banner_key = None
        self._t0 = time.monotonic()
        self._loading = False
        self._timer = FrameTimer(self)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self.update)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(250, 0, 30, 26)
        lay.addStretch(1)
        self.btn_row = QHBoxLayout()
        self.btn_row.setSpacing(10)
        lay.addLayout(self.btn_row)

    def set_loading(self, on):
        self._loading = on
        if on:
            self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def set_picture(self, path):
        self.picture = path or ""
        self._banner = None
        self.update()

    def _make_banner(self, W, H):
        pm = QPixmap(W, H)
        pm.fill(QColor(24, 24, 26))
        src = load_pixmap(self.picture, 256) if self.picture else QPixmap()
        p = QPainter(pm)
        if not src.isNull():
            import blur_fx                                   # гаусс вместо «16×16 → растянуть»
            p.drawImage(0, 0, blur_fx.blurred_cover(src, W, H, max(W, H) / 30.0))
        g = QLinearGradient(0, 0, 0, H)
        g.setColorAt(0, QColor(0, 0, 0, 60))
        g.setColorAt(1, QColor(18, 18, 18, 255))
        p.fillRect(pm.rect(), g)
        p.end()
        return pm

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        W, H = self.width(), self.height()
        key = (W, H, self.picture)
        if self._banner is None or self._banner_key != key:
            self._banner = self._make_banner(W, H)
            self._banner_key = key
        clip = QPainterPath(); clip.addRoundedRect(QRectF(0, 0, W, H + 20), 16, 16)
        p.setClipPath(clip)
        p.drawPixmap(0, 0, self._banner)
        p.setClipping(False)
        # фото
        ph = QRectF(32, H - 222, 190, 190)
        if self.picture:
            p.drawPixmap(ph.toRect(), cover_pixmap(self.picture, 190, 95, circle=True))
        else:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 18 if not self._loading else
                              int(18 + 14 * (0.5 + 0.5 * math.sin((time.monotonic() - self._t0) * 4)))))
            p.drawEllipse(ph)
        x = 250
        p.setPen(QColor(255, 255, 255, 170))
        p.setFont(ya_font(12, QFont.Weight.DemiBold))
        p.drawText(QRectF(x, H - 220, W - x - 20, 18), Qt.AlignmentFlag.AlignLeft, self.kind)
        f = ya_font(int(max(34, min(60, W * 0.045))), QFont.Weight.Black)
        p.setFont(f)
        p.setPen(QColor(255, 255, 255))
        p.drawText(QRectF(x, H - 200, W - x - 20, 76), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(f).elidedText(self.name, Qt.TextElideMode.ElideRight, int(W - x - 20)))
        p.setFont(ya_font(13, QFont.Weight.DemiBold))
        p.setPen(QColor(255, 255, 255, 215))
        stats = self.stats
        if self._loading:
            stats = "Загружаем данные с серверов" + "." * (1 + int((time.monotonic() - self._t0) * 3) % 3)
        p.drawText(QRectF(x, H - 122, W - x - 20, 20), Qt.AlignmentFlag.AlignLeft, stats)
        # индикаторы серверов (справа сверху)
        p.setFont(ya_font(11, QFont.Weight.DemiBold))
        bx = W - 20
        for name, st in reversed(self.sources):
            fm = QFontMetrics(p.font())
            w = fm.horizontalAdvance(name) + 30
            r = QRectF(bx - w, 16, w, 24)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 120))
            p.drawRoundedRect(r, 12, 12)
            if st == "ok":
                dot = QColor("#3ddc84")
            elif st == "wait":
                a = 0.5 + 0.5 * math.sin((time.monotonic() - self._t0) * 6)
                dot = QColor(255, 219, 26, int(120 + 135 * a))
            else:
                dot = QColor(120, 120, 120)
            p.setBrush(dot)
            p.drawEllipse(QPointF(r.left() + 13, r.center().y()), 4, 4)
            p.setPen(QColor(255, 255, 255, 220 if st == "ok" else 140))
            p.drawText(r.adjusted(22, 0, -8, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, name)
            bx -= w + 8
        p.end()


class TopTrackRow(QWidget):
    """Строка «Популярных треков» с сервера: номер, обложка, название,
    прослушивания (Last.fm) или полоса популярности (Deezer), «в коллекции»."""
    clicked = pyqtSignal()

    def __init__(self, n, t, local, max_rank, parent=None):
        super().__init__(parent)
        self.n, self.t, self.local, self.max_rank = n, t, local, max(1, max_rank)
        self._hover = False
        self.state, self.pct, self.path = "", 0.0, ""
        self.setFixedHeight(56)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Играть из вашей коллекции" if local else "Этого трека нет в коллекции — нажмите, чтобы скачать")

    def enterEvent(self, e):
        self._hover = True; self.update()

    def leaveEvent(self, e):
        self._hover = False; self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def _on_state(self, state, value):
        """Колбэк очереди закачки (queued / progress / retry / done / error)."""
        if state == "progress":
            self.state, self.pct = "progress", float(value)
        elif state == "done":
            self.state, self.path, self.local = "done", str(value), True
            self.setToolTip("Скачано — нажмите, чтобы слушать")
        elif state == "error":
            self.state = "error"
            self.setToolTip(f"Не удалось скачать: {value}")
        elif state in ("queued", "retry", "search"):
            self.state = state
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        if self._hover:
            path = QPainterPath(); path.addRoundedRect(QRectF(0, 2, W, H - 4), 10, 10)
            p.fillPath(path, QColor(255, 255, 255, 14))
        dim = 1.0 if (self.local or self.state) else 0.55
        p.setFont(ya_font(14, QFont.Weight.DemiBold))
        p.setPen(QColor(255, 255, 255, int(150 * dim)))
        p.drawText(QRectF(4, 0, 28, H), Qt.AlignmentFlag.AlignCenter, str(self.n))
        cov = QRect(40, 8, 40, 40)
        p.setOpacity(dim)
        p.drawPixmap(cov, cover_pixmap(self.t.get("cover", ""), 40, 6, seed=self.t.get("title", ""), lazy=self))
        p.setOpacity(1.0)
        tx = 94
        tw = int(W * 0.48) - tx
        f = ya_font(14, QFont.Weight.DemiBold)
        p.setFont(f)
        p.setPen(QColor(255, 255, 255, int(255 * dim)))
        p.drawText(QRect(tx, 9, tw, 20), Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(f).elidedText(self.t.get("title", ""), Qt.TextElideMode.ElideRight, tw))
        f2 = ya_font(12)
        p.setFont(f2)
        p.setPen(QColor(140, 140, 140))
        sub = self.t.get("artists", "")
        if self.t.get("album"):
            sub += "  ·  " + self.t["album"]
        p.drawText(QRect(tx, 29, tw, 18), Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(f2).elidedText(sub, Qt.TextElideMode.ElideRight, tw))
        # прослушивания или популярность
        from artist_net import human
        mx = int(W * 0.52)
        if self.t.get("playcount"):
            p.setFont(ya_font(12, QFont.Weight.DemiBold))
            p.setPen(QColor(220, 220, 220))
            p.drawText(QRect(mx, 0, 190, H), Qt.AlignmentFlag.AlignVCenter,
                       f"{human(self.t['playcount'])} прослушиваний")
        else:
            frac = min(1.0, self.t.get("rank", 0) / self.max_rank)
            p.setPen(Qt.PenStyle.NoPen)
            for k in range(10):
                on = k < round(frac * 10)
                p.setBrush(QColor(255, 255, 255, 200 if on else 40))
                p.drawRoundedRect(QRectF(mx + k * 9, H / 2 - 7, 5, 14), 2, 2)
        # в коллекции
        p.setFont(ya_font(11, QFont.Weight.DemiBold))
        st_txt = {"search": ("ищу…", "#ffdb1a"), "queued": ("в очереди", "#ffdb1a"),
                  "retry": ("другой источник…", "#ffb347"), "error": ("ошибка", "#ff6b6b")}
        if self.state == "progress":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 40))
            p.drawRoundedRect(QRectF(W - 150, H / 2 - 2, 80, 4), 2, 2)
            p.setBrush(QColor(YELLOW))
            p.drawRoundedRect(QRectF(W - 150, H / 2 - 2, 80 * min(1.0, self.pct / 100.0), 4), 2, 2)
        elif self.state in st_txt:
            txt, colr = st_txt[self.state]
            p.setPen(QColor(colr))
            p.drawText(QRect(W - 150, 0, 96, H), Qt.AlignmentFlag.AlignVCenter, txt)
        elif self.local:
            p.setBrush(QColor("#3ddc84")); p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(W - 150, H / 2), 3.5, 3.5)
            p.setPen(QColor("#3ddc84"))
            p.drawText(QRect(W - 142, 0, 90, H), Qt.AlignmentFlag.AlignVCenter, "в коллекции")
        else:
            ya_icon("download", 14, "#bdbdbd").paint(p, QRect(W - 150, int(H / 2 - 7), 14, 14))
            p.setPen(QColor(170, 170, 170))
            p.drawText(QRect(W - 132, 0, 80, H), Qt.AlignmentFlag.AlignVCenter, "скачать")
        dur = int(self.t.get("duration") or 0)
        if dur:
            p.setPen(QColor(140, 140, 140))
            p.setFont(ya_font(12))
            p.drawText(QRect(W - 56, 0, 50, H), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, _fmt(dur))
        p.end()


class ArtistPage(Page):
    """Карточка исполнителя: данные с серверов + ваша коллекция."""

    def __init__(self, shell, parent=None):
        super().__init__(parent, margins=(0, 0, 0, 24))
        self.shell = shell
        self.name = ""
        self.local = []
        self.data = None
        self._req = 0
        self.header = None

    # ── загрузка ── #

    def load(self, name, local_tracks, force=False):
        self.name, self.local = name, list(local_tracks)
        self._req += 1
        req = self._req
        from artist_net import cached
        c = cached(name)
        self._render(c, loading=True)
        key = self.shell.win.settings.get("lastfm_key", "")

        def work():
            from artist_net import fetch_artist
            try:
                d = fetch_artist(name, key, force=force)
            except Exception as e:                   # noqa: BLE001
                print("[artist]", e)
                d = {"name": name, "sources": [], "error": str(e)}
            self.shell.win._ui_bridge.call.emit(lambda: req == self._req and self._render(d, loading=False))
        _threading.Thread(target=work, daemon=True).start()

    # ── отрисовка ── #

    def _local_match(self, title):
        from track_identity import similarity as _sim
        best, bs = None, 0.0
        for t in self.local:
            s = _sim(title, sanitize_title(t.get("title", "")))
            if s > bs:
                best, bs = t, s
        return best if bs >= 0.84 else None

    def _render(self, d, loading):
        from artist_net import human
        self.clear()
        self.data = d
        w = self.shell.win
        d = d or {}
        hdr = ArtistHeader()
        self.header = hdr
        hdr.name = d.get("name") or self.name
        hdr.kind = "Исполнитель"
        hdr.set_picture(d.get("picture", ""))
        parts = []
        if d.get("listeners"):
            parts.append(f"{human(d['listeners'])} слушателей")
        if d.get("fans"):
            parts.append(f"{human(d['fans'])} поклонников")
        if d.get("playcount"):
            parts.append(f"{human(d['playcount'])} прослушиваний")
        if d.get("albums_count"):
            parts.append(f"{d['albums_count']} релизов")
        parts.append(f"{len(self.local)} в вашей коллекции")
        hdr.stats = "  ·  ".join(parts)
        if d.get("error") and not loading:
            hdr.stats = d["error"] + ("  ·  показаны сохранённые данные" if d.get("stale") else "")
        srcs = d.get("sources", [])
        names = ["Deezer", "Wikipedia"] + (["Last.fm"] if w.settings.get("lastfm_key") else [])
        hdr.sources = [(n, "ok" if n in srcs else ("wait" if loading else "off")) for n in names]
        hdr.set_loading(loading and not d.get("sources"))
        b = QPushButton("  Слушать")
        b.setObjectName("YaYellow"); b.setIcon(ya_icon("play", 18, "#000000")); b.setMinimumHeight(40)
        b.clicked.connect(self._play_all)
        hdr.btn_row.addWidget(b)
        b = QPushButton("  Моя волна по артисту"); b.setIcon(ya_icon("wave", 18)); b.setMinimumHeight(40)
        b.clicked.connect(lambda: self.shell.start_wave("artist", self.name))
        hdr.btn_row.addWidget(b)
        b = QPushButton("  Все треки"); b.setIcon(ya_icon("note", 18)); b.setMinimumHeight(40)
        b.setToolTip("Вся дискография исполнителя с YouTube Music: каждый трек и число прослушиваний")
        b.clicked.connect(lambda: self.shell.open_release("all", (self.data or {}).get("name") or self.name))
        hdr.btn_row.addWidget(b)
        top_all = d.get("top") or []
        if any(self._local_match(t.get("title", "")) is None for t in top_all[:10]):
            b = QPushButton("  Скачать популярные"); b.setIcon(ya_icon("download", 18)); b.setMinimumHeight(40)
            b.setToolTip("Найти на YouTube Music / SoundCloud и скачать все популярные треки, которых нет в коллекции")
            b.clicked.connect(self._download_all_top)
            hdr.btn_row.addWidget(b)
        b = icon_button("repeat", "Обновить с серверов", 40, 20)
        b.clicked.connect(lambda: self.load(self.name, self.local, force=True))
        hdr.btn_row.addWidget(b)
        hdr.btn_row.addStretch(1)
        back = icon_button("back", "Назад", 36, 20)
        back.setParent(hdr)
        back.move(14, 14)
        back.setStyleSheet("QPushButton{background:rgba(0,0,0,0.45);border-radius:18px;}"
                           "QPushButton:hover{background:rgba(0,0,0,0.65);}")
        back.clicked.connect(self.shell.go_back)
        self.v.addWidget(hdr)

        body = QWidget(); body.setObjectName("YaPage")
        bv = QVBoxLayout(body)
        bv.setContentsMargins(32, 4, 32, 0)
        bv.setSpacing(4)
        self.v.addWidget(body)

        if self.local:
            bv.addWidget(section_title("В вашей коллекции"))
            lv = TrackListView(self.shell)
            lv.set_auto_height(True)
            lv.set_rows(self.shell._lib_rows(self.local))
            bv.addWidget(lv)

        top = d.get("top") or []
        if top:
            bv.addWidget(section_title("Популярные треки"))
            mr = max([t.get("rank", 0) for t in top] + [1])
            self._top_rows = []
            for i, t in enumerate(top[:10]):
                loc = self._local_match(t.get("title", ""))
                r = TopTrackRow(i + 1, t, loc is not None, mr)
                r.clicked.connect(lambda r=r, loc=loc: self._top_click(r, loc))
                bv.addWidget(r)
                if loc is None:
                    self._top_rows.append(r)
        elif loading:
            for i in range(5):
                sk = QFrame(); sk.setFixedHeight(48)
                sk.setStyleSheet("background:rgba(255,255,255,0.05);border-radius:10px;")
                bv.addWidget(sk)

        # вся дискография (YouTube Music): альбомы и синглы открываются — треки и прослушивания,
        # «Все треки исполнителя»; если YouTube Music не ответил — альбомы Deezer
        if not loading or d.get("albums"):
            from ya_online import DiscographyBlock
            bv.addWidget(DiscographyBlock(self.shell, d.get("name") or self.name, fallback=d.get("albums") or []))

        rel = d.get("related") or []
        if rel:
            bv.addWidget(section_title("Похожие исполнители"))
            row = HRow()
            for r in rel:
                c = Card(r.get("name", ""), f"{human(r.get('fans', 0))} поклонников", r.get("picture", ""), 130,
                         circle=True, seed=r.get("name", ""))
                c.clicked.connect(lambda n=r.get("name", ""): self.shell.open_artist(n))
                row.add(c)
            bv.addWidget(row)

        if d.get("bio") or d.get("tags"):
            bv.addWidget(section_title("Об исполнителе"))
            if d.get("tags"):
                tg = QHBoxLayout(); tg.setSpacing(6)
                for t in d["tags"]:
                    c = chip(t, checkable=False)
                    tg.addWidget(c)
                tg.addStretch(1)
                bv.addLayout(tg)
            if d.get("bio"):
                lb = QLabel(d["bio"])
                lb.setWordWrap(True)
                lb.setStyleSheet("font-size:14px;color:#d0d0d0;line-height:150%;")
                bv.addWidget(lb)
                src = QLabel(f"Источник: {d.get('bio_src', '')}")
                src.setObjectName("YaSub")
                bv.addWidget(src)
        self.v.addStretch(1)
        self.verticalScrollBar().setValue(0)

    # ── действия ── #

    def _play_local(self, t):
        if t is None:
            return
        rows = self.shell._lib_rows([t])
        if rows:
            self.shell.play_row(rows[0])

    def _artist_for(self, t):
        return (self.data or {}).get("name") or self.name

    def _top_click(self, row, loc):
        if loc is not None:
            self._play_local(loc)
        elif row.path:
            self.shell.play_path(row.path)
        else:
            self._download_top(row, play=True)

    def _download_all_top(self):
        for r in list(getattr(self, "_top_rows", [])):
            try:
                if not r.local and r.state in ("", "error"):
                    self._download_top(r, play=False)
            except RuntimeError:
                pass

    def _download_top(self, row, play=False):
        """Популярный трек с сервера → найти на YT Music / SoundCloud → скачать."""
        if row.state in ("search", "queued", "progress", "retry"):
            return
        row._on_state("search", 0)
        t = dict(row.t)
        artist = self._artist_for(t)
        w = self.shell.win
        target = self.shell.online_target_playlist()

        def work():
            cands = []
            try:
                import online_search
                cands = online_search.find_candidates(t.get("title", ""), artist, t.get("duration", 0))
            except Exception as e:                   # noqa: BLE001
                print("[artist dl]", e)

            def go():
                try:
                    row.state
                except RuntimeError:
                    return                           # страница уже перерисована
                if cands:
                    res, alts = dict(cands[0]), [c["url"] for c in cands[1:]]
                else:
                    q = f"{artist} - {t.get('title', '')}".replace(":", " ")
                    res, alts = {"url": f"ytsearch1:{q} audio"}, []
                cov = t.get("cover", "") if str(t.get("cover", "")).startswith("http") else ""
                w._quick_download(res["url"], playlist=target, play=play, cb=row._on_state,
                                  meta={"title": t.get("title", ""), "artist": artist,
                                        "album": t.get("album") or res.get("album") or "",
                                        "thumb_url": res.get("thumb_url") or cov,
                                        "duration": t.get("duration") or 0, "alts": alts})
            w._ui_bridge.call.emit(go)
        _threading.Thread(target=work, daemon=True).start()

    def _download_album(self, a, card):
        """Релиз с сервера → альбом на YouTube Music → все треки в плейлист «Название»."""
        if getattr(card, "_busy", False):
            return
        card._busy = True
        old_sub = card.subtitle
        card.subtitle = "ищу на YouTube Music…"
        card.update()
        w = self.shell.win
        artist = (self.data or {}).get("name") or self.name

        def work():
            alb = None
            try:
                import online_search
                alb = online_search.album_tracks(artist, a.get("title", ""))
            except Exception as e:                   # noqa: BLE001
                print("[album dl]", e)

            def go():
                try:
                    card.subtitle
                except RuntimeError:
                    return
                card._busy = False
                if not alb or not alb.get("tracks"):
                    card.subtitle = "не найден на YouTube Music"
                    card.update()
                    return
                tr = alb["tracks"]
                name = alb["title"] or a.get("title", "Альбом")
                ans = QMessageBox.question(self, "Скачать альбом",
                                           f"«{name}» — {alb.get('artist', '')}\n{len(tr)} треков.\n\n"
                                           f"Скачать в плейлист «{name}»?")
                if ans != QMessageBox.StandardButton.Yes:
                    card.subtitle = old_sub
                    card.update()
                    return
                state = {"done": 0, "fail": 0}

                def cb(st, v):
                    if st not in ("done", "error"):
                        return
                    state["done" if st == "done" else "fail"] += 1
                    try:
                        card.subtitle = f"скачано {state['done']}/{len(tr)}" + \
                            (f" · ошибок {state['fail']}" if state["fail"] else "")
                        card.update()
                    except RuntimeError:
                        pass
                card.subtitle = f"скачано 0/{len(tr)}"
                card.update()
                for t in tr:
                    w._quick_download(t["url"], playlist=name, cb=cb,
                                      meta={"title": t.get("title"), "artist": t.get("artist"),
                                            "album": name, "thumb_url": alb.get("thumb_url") or t.get("thumb_url"),
                                            "duration": t.get("duration") or 0})
                pl = w.playlists.get(name)
                if pl is not None and not pl.get("cover"):
                    pl["desc"] = f"Альбом · {alb.get('artist', '')}"
            w._ui_bridge.call.emit(go)
        _threading.Thread(target=work, daemon=True).start()

    def _play_all(self):
        rows = self.shell._lib_rows(self.local)
        if rows:
            self.shell.play_row(rows[0])

    def _fan_wave(self):
        """Волна из всего, что связано с артистом вкладки."""
        w = self.shell.win
        tracks = list(self.local)
        random.shuffle(tracks)
        if not tracks:
            return
        w.playlists[WAVE_PL] = {"tracks": tracks, "desc": f"auto: {self.name}", "cover": ""}
        w._ya_save_playlists()
        self.shell._wave_kind = ("artist", self.name)
        self.shell.wave.set_mood(self.name, "artist", self.name)
        w._play_index(0, WAVE_PL)
