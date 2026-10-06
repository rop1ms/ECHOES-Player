# ya_wave.py
"""
«Моя волна» для темы Echoes Music — как в Яндекс Музыке:

  • слева — список волн по ДУГЕ (без фона): к краям пункты уходят вправо
    и тают; у настроений — «кляксы»-иконки, у жанров — «молнии»,
    у исполнителей — круглые фото с подписью «Артист», «Назад к привычному» —
    жёлтая стрелка;
  • справа — живое сияние в цветах ТЕКУЩЕГО НАСТРОЕНИЯ (а не обложки):
    мягкое ядро, ореол и лучи, считается numpy-полем и растягивается со
    сглаживанием — получается настоящая «засветка», а не векторная клякса;
    дышит в такт басу;
  • поверх сияния — НАЗВАНИЕ НАСТРОЕНИЯ, под ним маленькая обложка,
    «пилюля» с названием трека, кнопки и подсказка.
"""
from __future__ import annotations

import colorsys
import threading
import math
import random
import time

import numpy as np
from img_load import load_pixmap
from PyQt6.QtCore import Qt, QTimer, QRectF, QPointF, QSize, QRect, pyqtSignal
from frameclock import FrameTimer
from PyQt6.QtGui import (QRegion, QColor, QFont, QPainter, QPainterPath, QPixmap, QImage, QPen,
                         QLinearGradient, QRadialGradient, QFontMetrics)
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QFrame

YELLOW = "#ffdb1a"
FAM = ["YS Text", "Inter", "Segoe UI Variable Display", "Segoe UI", "Arial"]


def _font(px, w=QFont.Weight.Normal):
    f = QFont()
    f.setFamilies(FAM)
    f.setPixelSize(int(px))
    f.setWeight(w)
    return f


# ── палитры настроений: (ядро, ореол, лучи) ─────────────────────────────── #

def _hx(c):
    c = QColor(c)
    return np.array([c.redF(), c.greenF(), c.blueF()], dtype=np.float32)


MOOD_COLORS = {
    "my":       ("#fff04a", "#e5127d", "#ff4fb8"),
    "mood_day": ("#ffc56b", "#7a2cff", "#ff7a3d"),
    "energy":   ("#ffd23a", "#ff1d2d", "#ff6a00"),
    "calm":     ("#9ffcff", "#1f5bff", "#40c8ff"),
    "happy":    ("#f6ff57", "#18c95a", "#ffe23a"),
    "sad":      ("#b9c2ff", "#3a1aa8", "#6f5cff"),
    "fresh":    ("#a6fff4", "#a020ff", "#3fd8ff"),
    "fav":      ("#ffe0ef", "#ff1f6d", "#ffb02e"),
}


def palette_for(kind, arg=None):
    if kind in MOOD_COLORS:
        return MOOD_COLORS[kind]
    if kind == "mood" and arg in MOOD_COLORS:
        return MOOD_COLORS[arg]
    key = str(arg or kind)
    h = (sum(ord(c) * (i + 7) for i, c in enumerate(key)) % 360) / 360.0
    core = QColor.fromHsvF((h + 0.10) % 1, 0.55, 1.0).name()
    halo = QColor.fromHsvF(h, 0.95, 0.95).name()
    ray = QColor.fromHsvF((h + 0.05) % 1, 0.8, 1.0).name()
    return core, halo, ray


# ══════════════════════════════════════════════════════════════════════════ #
#  Сияние: поле из гауссовых пятен + ореол с лучами
# ══════════════════════════════════════════════════════════════════════════ #

class GlowField:
    GW = 176
    MARGIN = 0.04                 # запас по краям под «плавание» центра (сдвиг картинки при рисовании)

    def __init__(self):
        self.size = (0, 0)
        self.rng = np.random.default_rng(7)
        # параметры «пятен» ядра: смещение по орбите, частоты, радиус
        self.blobs = [(0.16 * self.rng.uniform(0.5, 1.0), self.rng.uniform(0.15, 0.45),
                       self.rng.uniform(0.15, 0.45), self.rng.uniform(0, 6.28),
                       self.rng.uniform(0.26, 0.40)) for _ in range(6)]
        self.core = _hx("#fff04a")
        self.halo = _hx("#e5127d")
        self.ray = _hx("#ff4fb8")
        self._to = None
        self._mix = 1.0
        self.tt = 0.0              # «музыкальное» время: бежит быстрее, когда музыка громче
        self.kick = 0.0            # импульс удара баса (0..1, затухает)
        self.rings: list = []      # ударные волны: [радиус, сила]
        self.energy = 0.0

    def hit(self, strength=1.0):
        """Удар баса: вспышка ядра и расходящееся кольцо света."""
        self.kick = min(1.0, self.kick + 0.7 * strength)
        self.rings.append([0.25, 0.55 * strength])
        if len(self.rings) > 5:
            self.rings.pop(0)

    def set_palette(self, core, halo, ray, instant=False):
        to = (_hx(core), _hx(halo), _hx(ray))
        if instant:
            self.core, self.halo, self.ray = to
            self._to = None
            return
        self._from = (self.core.copy(), self.halo.copy(), self.ray.copy())
        self._to = to
        self._mix = 0.0

    def _grid(self, W, H, cx=0.5, cy=0.5):
        """Сетка и всё, что зависит только от геометрии (радиус, угол, гармоники угла) — один раз на
        размер окна: раньше sqrt/arctan2 и десяток sin по всем точкам считались каждый кадр."""
        gw = self.GW
        gh = max(40, int(gw * H / max(1, W)))
        aspect = W / max(1, H)
        key = (gw, gh, round(aspect, 4), round(cx, 4), round(cy, 4))
        if getattr(self, "_gkey", None) != key:
            self._gkey = key
            self.size = (gw, gh)
            ys, xs = np.mgrid[0:gh, 0:gw].astype(np.float32)
            m = self.MARGIN                               # картинка шире окна на m с каждой стороны
            self.X = ((-m + (1 + 2 * m) * xs / (gw - 1)) * 2 - 1) * aspect
            self.Y = (-m + (1 + 2 * m) * ys / (gh - 1)) * 2 - 1
            X = self.X - np.float32((cx * 2 - 1) * aspect)
            Y = self.Y - np.float32(cy * 2 - 1)
            R2 = X * X + Y * Y
            R0 = np.sqrt(R2)
            ang = np.arctan2(Y, X)
            g = {"X": X, "Y": Y, "R2": R2, "R0": R0, "clipR": np.clip(R0 / 0.35, 0, 1).astype(np.float32),
                 "sinR": np.sin(R0 * 2.2).astype(np.float32), "cosR": np.cos(R0 * 2.2).astype(np.float32),
                 "xs": X[0, :].copy(), "ys": Y[:, 0].copy(), "a4": (4 * ang).astype(np.float32)}
            for k in (2, 3, 4, 5, 6, 7, 9):
                g[f"s{k}"] = np.sin(k * ang).astype(np.float32)
                g[f"c{k}"] = np.cos(k * ang).astype(np.float32)
            self.g = g
        return self.size

    def render(self, W, H, cx, cy, t, beat, dt) -> QImage:
        """cx, cy — центр сияния в долях окна. «Плавание» центра не пересчитывает сетку: оно отдаётся
        сдвигом картинки (self.offset, доли окна) — сияние мягкое, разницы не видно."""
        gw, gh = self._grid(W, H, cx, cy)
        g = self.g
        if self._to is not None:
            self._mix = min(1.0, self._mix + dt / 1.4)
            k = self._mix * self._mix * (3 - 2 * self._mix)
            self.core = self._from[0] * (1 - k) + self._to[0] * k
            self.halo = self._from[1] * (1 - k) + self._to[1] * k
            self.ray = self._from[2] * (1 - k) + self._to[2] * k
            if self._mix >= 1.0:
                self._to = None
        self.energy += (beat - self.energy) * min(1.0, dt * 3)
        self.tt += dt * (1.0 + 2.2 * self.energy + 1.5 * self.kick)
        T = self.tt
        self.kick *= math.exp(-dt * 3.2)
        for r in self.rings:
            r[0] += dt * (1.6 + 0.8 * self.energy)
            r[1] *= math.exp(-dt * 1.4)
        self.rings = [r for r in self.rings if r[1] > 0.02 and r[0] < 3.5]
        self.offset = (0.018 * math.sin(T * 0.7) + 0.008 * math.sin(T * 1.9),
                       0.022 * math.cos(T * 0.55) + 0.008 * math.sin(T * 2.3))
        X, Y, R0, R2 = g["X"], g["Y"], g["R0"], g["R2"]
        f32 = np.float32

        def sn(k, ph):                                    # sin(k·угол + ph) из готовых гармоник
            return g[f"s{k}"] * f32(math.cos(ph)) + g[f"c{k}"] * f32(math.sin(ph))
        pulse = 1.0 + 0.10 * beat + 0.20 * self.kick + 0.03 * math.sin(T * 2.1)
        lobes = (0.17 + 0.07 * self.energy) * sn(5, T * 0.65)
        lobes += f32(0.12) * sn(3, -T * 0.95 + 1.0)
        lobes += f32(0.07) * sn(7, T * 1.5)
        lobes += f32(0.05) * sn(2, -T * 0.4)
        lobes *= g["clipR"]
        lobes += f32(1.0)
        inv = f32(1.0) / lobes
        Xw, Yw = X * inv, Y * inv
        f = np.zeros_like(X)
        for (orb, fx, fy, ph, rad) in self.blobs:
            bx = orb * 1.9 * math.sin(T * fx * 2.4 + ph)
            by = orb * 1.5 * math.cos(T * fy * 2.4 + ph * 1.3)
            r2 = (rad * 1.7 * pulse) ** 2
            dx = Xw - f32(bx)
            dy = Yw - f32(by)
            e = dx * dx
            e += f32(1.1) * dy * dy
            e *= f32(-1.0 / r2)
            f += np.exp(e)
        f *= f32(1.0 / (len(self.blobs) * 0.42))
        inner_r = 1.4 * (g["sinR"] * f32(math.cos(T * 1.3)) - g["cosR"] * f32(math.sin(T * 1.3)))
        rays = f32(0.6) + f32(0.4) * np.sin(g["a4"] + f32(T * 0.45) + inner_r)
        rays *= rays
        rays *= f32(0.85) + f32(0.15) * sn(9, -T * 2.0)
        rh = 1.15 + 0.10 * beat + 0.25 * self.kick
        halo = np.exp(R2 * f32(-1.2 / (rh * rh)))
        halo *= f32(0.75) + f32(0.35) * rays
        rl = 1.6 + 0.3 * self.kick
        s6 = f32(0.5) + f32(0.5) * sn(6, -T * 0.5)
        s6 *= s6
        s6 *= s6
        ray_l = np.exp(R2 * f32(-1.0 / (rl * rl))) * s6 * f32(0.35 + 0.35 * self.energy)
        core = np.clip((f - f32(0.36)) * f32(1 / 0.85), 0, 1)
        core = core * core * (f32(3) - f32(2) * core)
        inner = np.clip((f - f32(0.95)), 0, 1) * f32(0.35 + 0.5 * self.kick)
        # ядро переливается к цвету лучей (переливы разделимы по x и y — внешнее произведение)
        shimmer = f32(0.5) + f32(0.5) * np.outer(np.cos(g["ys"] * f32(1.8) - f32(T * 0.7)),
                                                 np.sin(g["xs"] * f32(2.2) + f32(T * 0.9))).astype(np.float32)
        core_col = self.core.astype(np.float32)[None, None, :] * (f32(1) - f32(0.25) * shimmer[..., None]) + \
            self.ray.astype(np.float32)[None, None, :] * (f32(0.25) * shimmer[..., None])
        img = halo[..., None] * (self.halo * (1.2 + 0.4 * self.kick)).astype(np.float32) + \
            ray_l[..., None] * self.ray.astype(np.float32)
        for rr, a in self.rings:
            d = (R0 - f32(rr)) * f32(1 / 0.16)
            ring = np.exp(-(d * d)) * f32(a * 0.7)
            img += ring[..., None] * (self.ray * 0.7 + self.halo * 0.5).astype(np.float32)
        img = img * (f32(1) - core[..., None] * f32(0.92)) + core[..., None] * core_col
        img += inner[..., None] * ((1.0 - self.core) * 0.6).astype(np.float32)
        np.clip(img, 0, 1, out=img)
        img *= f32(0.95) + f32(0.05) * img                 # ≈ x^1.05 без степени
        u8 = np.ascontiguousarray((img * 255).astype(np.uint8))
        self._buf = u8                                    # держим память под QImage
        return QImage(u8.data, gw, gh, gw * 3, QImage.Format.Format_RGB888)


# ══════════════════════════════════════════════════════════════════════════ #
#  Иконки пунктов
# ══════════════════════════════════════════════════════════════════════════ #

_ICONS: dict = {}


def wave_icon(kind: str, c1: str, c2: str, size: int = 56, seed: int = 0, cover: str = "") -> QPixmap:
    key = (kind, c1, c2, size, seed, cover)
    if key in _ICONS:
        return _ICONS[key]
    dpr = 2
    pm = QPixmap(size * dpr, size * dpr)
    pm.fill(Qt.GlobalColor.transparent)
    pm.setDevicePixelRatio(dpr)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size
    rng = random.Random(seed)
    if kind == "artist":
        path = QPainterPath()
        path.addEllipse(QRectF(2, 2, s - 4, s - 4))
        p.setClipPath(path)
        src = load_pixmap(cover, 512) if cover else QPixmap()
        if src.isNull():
            g = QLinearGradient(0, 0, s, s)
            g.setColorAt(0, QColor(c1)); g.setColorAt(1, QColor(c2))
            p.fillRect(pm.rect(), g)
        else:
            m = min(src.width(), src.height())
            p.drawPixmap(QRectF(2, 2, s - 4, s - 4), src,
                         QRectF((src.width() - m) / 2, (src.height() - m) / 2, m, m))
        p.setClipping(False)
        p.setPen(QPen(QColor(255, 255, 255, 40), 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(2, 2, s - 4, s - 4))
    elif kind == "back":
        pen = QPen(QColor(YELLOW), s * 0.075, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                   Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        path = QPainterPath()
        path.moveTo(s * 0.26, s * 0.42)
        path.lineTo(s * 0.62, s * 0.42)
        path.cubicTo(s * 0.86, s * 0.42, s * 0.86, s * 0.76, s * 0.62, s * 0.76)
        path.lineTo(s * 0.22, s * 0.76)
        p.drawPath(path)
        p.drawLine(QPointF(s * 0.26, s * 0.42), QPointF(s * 0.40, s * 0.28))
        p.drawLine(QPointF(s * 0.26, s * 0.42), QPointF(s * 0.40, s * 0.56))
    else:
        cx, cy = s / 2, s / 2
        path = QPainterPath()
        if kind == "blob":                    # четырёхлепестковая «клякса»-X
            n = 220
            rot = math.radians(45 + rng.uniform(-14, 14))
            for i in range(n + 1):
                a = i / n * 2 * math.pi
                r = s * 0.46 * (0.52 + 0.48 * abs(math.cos(2 * a)) ** 0.55)
                r *= 1 + 0.05 * math.sin(3 * a + seed)
                x = cx + math.cos(a + rot) * r
                y = cy + math.sin(a + rot) * r
                if i == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
            path.closeSubpath()
        elif kind == "bolt":                  # «Z»-молния
            k = s / 56.0
            pts = [(9, 12), (47, 6), (30, 26), (49, 27), (9, 51), (25, 30), (7, 31)]
            path.moveTo(pts[0][0] * k, pts[0][1] * k)
            for x, y in pts[1:]:
                path.lineTo(x * k, y * k)
            path.closeSubpath()
            p.translate(cx, cy); p.rotate(rng.uniform(-10, 10)); p.translate(-cx, -cy)
        elif kind == "m":                      # «М»-форма (метал/рок)
            k = s / 56.0
            pts = [(8, 48), (14, 8), (28, 24), (44, 6), (50, 46), (40, 46), (38, 24), (28, 36),
                   (18, 22), (17, 48)]
            path.moveTo(pts[0][0] * k, pts[0][1] * k)
            for x, y in pts[1:]:
                path.lineTo(x * k, y * k)
            path.closeSubpath()
        else:                                  # «капля»
            path.addEllipse(QRectF(s * 0.12, s * 0.12, s * 0.76, s * 0.76))
        g = QLinearGradient(0, 0, s, s)
        g.setColorAt(0.0, QColor(c1))
        g.setColorAt(1.0, QColor(c2))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawPath(path)
        # объём: блик сверху-слева и тень снизу
        hl = QRadialGradient(QPointF(s * 0.32, s * 0.28), s * 0.55)
        hl.setColorAt(0, QColor(255, 255, 255, 110))
        hl.setColorAt(1, QColor(255, 255, 255, 0))
        p.setBrush(hl)
        p.drawPath(path)
        sh = QLinearGradient(0, s * 0.4, 0, s)
        sh.setColorAt(0, QColor(0, 0, 0, 0))
        sh.setColorAt(1, QColor(0, 0, 0, 80))
        p.setBrush(sh)
        p.drawPath(path)
    p.end()
    _ICONS[key] = pm
    return pm


# ══════════════════════════════════════════════════════════════════════════ #
#  Список волн по дуге
# ══════════════════════════════════════════════════════════════════════════ #

class WaveWheel(QWidget):
    """items: dict(kind, arg, label, icon, c1, c2, sub, cover, accent)."""
    activated = pyqtSignal(object, object)

    ITEM_H = 68

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.items: list[dict] = []
        self.off = 0.0               # индекс пункта в центре (дробный)
        self.target = 0.0
        self.active = None           # (kind, arg) текущей волны
        self._hover = -1
        self._timer = FrameTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    def set_items(self, items):
        self.items = items
        mid = (len(items) - 1) / 2.0
        self.off = self.target = min(mid, 4.0)
        self.update()

    def set_active(self, key):
        self.active = key
        self.update()

    def _tick(self):
        n = len(self.items)
        if n and abs(self.off) > n * 4:               # держим числа маленькими
            shift = round(self.off / n) * n
            self.off -= shift
            self.target -= shift
        d = self.target - self.off
        now = time.monotonic()
        dt = min(0.05, max(0.001, now - getattr(self, "_last_t", now - 1 / 60)))
        self._last_t = now
        if abs(d) < 0.002:
            self.off = self.target
            self._timer.stop()
        else:
            self.off += d * (1.0 - 0.82 ** (dt * 60.0))        # 0.18 «за кадр 60 Гц» при любой частоте
        self.update()

    def wheelEvent(self, e):
        self.target = self.target - e.angleDelta().y() / 120.0    # бесконечная лента — без упора
        if not self._timer.isActive():
            self._last_t = time.monotonic()
        self._timer.start()
        e.accept()

    def _pos(self, i, copy=0):
        """Позиция пункта относительно центра (в пунктах) — с заворотом по кругу."""
        n = max(1, len(self.items))
        p = (i - self.off + n / 2) % n - n / 2
        return p + copy * n

    def _geom(self, i, copy=0):
        H, W = self.height(), self.width()
        cy = H / 2
        p = self._pos(i, copy)
        y = cy + p * self.ITEM_H
        d = (y - cy) / max(1.0, H / 2)               # -1..1
        x = W * 0.20 + (d * d) * W * 0.24            # дуга: середина левее, края правее
        alpha = max(0.0, 1.0 - abs(d) ** 2.2 * 0.85)
        sc = 1.0 - 0.10 * min(1.0, abs(d))
        return x, y, alpha, sc

    def _copies(self):
        n = max(1, len(self.items))
        k = int(math.ceil(self.height() / max(1, n * self.ITEM_H))) + 1
        return range(-k, k + 1)

    def _hit(self, pos):
        for i in range(len(self.items)):
            for c in self._copies():
                x, y, a, sc = self._geom(i, c)
                if a > 0.1 and abs(pos.y() - y) < self.ITEM_H / 2 and x - 10 <= pos.x() <= self.width() - 10:
                    return (i, c)
        return None

    def mouseMoveEvent(self, e):
        h = self._hit(e.position())
        hi = h[0] if h else -1
        if hi != self._hover:
            self._hover = hi
            self.setCursor(Qt.CursorShape.PointingHandCursor if hi >= 0 else Qt.CursorShape.ArrowCursor)
            self.update()

    def leaveEvent(self, e):
        self._hover = -1
        self.update()

    def mousePressEvent(self, e):
        h = self._hit(e.position())
        if h and e.button() == Qt.MouseButton.LeftButton:
            i, c = h
            it = self.items[i]
            self.target = self.off + self._pos(i, c)      # докрутить по кратчайшему пути
            self._timer.start()
            self.activated.emit(it["kind"], it.get("arg"))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        W, H = self.width(), self.height()
        draws = []
        for c in self._copies():
            for i, it in enumerate(self.items):
                x, y, alpha, sc = self._geom(i, c)
                if -self.ITEM_H <= y <= H + self.ITEM_H and alpha > 0.02:
                    draws.append((i, it, x, y, alpha, sc))
        for i, it, x, y, alpha, sc in draws:
            hover = i == self._hover
            act = self.active == (it["kind"], it.get("arg"))
            p.save()
            p.setOpacity(min(1.0, alpha * (1.0 if (hover or act) else 0.92)))
            p.translate(x, y)
            s = sc * (1.06 if hover else 1.0)
            p.scale(s, s)
            isz = 56
            pm = wave_icon(it["icon"], it.get("c1", "#fff"), it.get("c2", "#888"), isz,
                           seed=i * 13 + 5, cover=it.get("cover", ""))
            p.drawPixmap(QRectF(0, -isz / 2, isz, isz), pm, QRectF(0, 0, pm.width(), pm.height()))
            tx = isz + 14
            col = QColor(YELLOW) if (it.get("accent") or act) else QColor(255, 255, 255)
            if it.get("sub"):
                p.setFont(_font(11, QFont.Weight.DemiBold))
                p.setPen(QColor(150, 150, 150))
                p.drawText(QRectF(tx, -19, W, 16), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                           it["sub"])
                p.setFont(_font(15, QFont.Weight.Bold))
                p.setPen(col)
                p.drawText(QRectF(tx, -3, W - x - tx, 22), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                           it["label"])
            else:
                p.setFont(_font(15, QFont.Weight.Bold))
                p.setPen(col)
                p.drawText(QRectF(tx, -12, W - x - tx, 24), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                           it["label"])
            p.restore()
        p.end()


# ══════════════════════════════════════════════════════════════════════════ #
#  Страница «Моя волна»
# ══════════════════════════════════════════════════════════════════════════ #

class _GlowWorker(threading.Thread):
    """Считает кадры сияния в фоне (numpy отпускает GIL) — GUI только рисует."""

    def __init__(self, glow: GlowField):
        super().__init__(daemon=True, name="wave-glow")
        self.glow = glow
        self._cond = threading.Condition()
        self._job = None
        self._alive = True
        self.result = None
        self.lock = threading.Lock()          # палитра/удары меняются из GUI

    def submit(self, *job) -> bool:
        with self._cond:
            if self._job is not None:
                return False
            self._job = job
            self._cond.notify()
            return True

    def stop(self):
        with self._cond:
            self._alive = False
            self._cond.notify()

    def take(self):
        r, self.result = self.result, None
        return r

    def run(self):
        while True:
            with self._cond:
                while self._job is None and self._alive:
                    self._cond.wait()
                if not self._alive:
                    return
                job = self._job
            try:
                with self.lock:
                    img = self.glow.render(*job)
                    off = getattr(self.glow, "offset", (0.0, 0.0))
                self.result = (img.copy(), off)    # своя память — буфер numpy можно переиспользовать
            except Exception as e:                 # noqa: BLE001
                print("[wave] glow:", e)
            finally:
                with self._cond:
                    self._job = None


class WavePage(QWidget):
    """Чёрная страница: дуга-список слева, сияние и плеер справа."""

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.glow = GlowField()
        self._worker = _GlowWorker(self.glow)
        self._worker.start()
        self._title_pm = None
        self.title = "Моя волна"
        self._img = None
        self._beat = 0.0
        self._t0 = time.monotonic()
        self._last = time.monotonic()
        self.wheel = WaveWheel(self)

        # блок управления под сиянием
        self.ctrl = QWidget(self)
        self.ctrl.setObjectName("YaPage")
        v = QVBoxLayout(self.ctrl)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(14)
        self.cover = QLabel(); self.cover.setFixedSize(60, 60)
        v.addWidget(self.cover, 0, Qt.AlignmentFlag.AlignHCenter)
        pill = QFrame(); pill.setObjectName("WavePill")
        pill.setStyleSheet("QFrame#WavePill{background:rgba(20,20,20,0.55);border-radius:21px;}")
        pill.setFixedHeight(42)                   # на весь экран плашка не должна раздуваться в высоту
        pl = QHBoxLayout(pill)
        pl.setContentsMargins(16, 4, 6, 4)
        pl.setSpacing(6)
        self.track_lbl = QLabel("Ничего не играет")
        self.track_lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self.track_lbl.setStyleSheet("font-size:14px;font-weight:700;color:rgba(255,255,255,0.92);background:transparent;")
        self.track_lbl.setMinimumWidth(240)
        self.track_lbl.setMaximumWidth(420)
        pl.addWidget(self.track_lbl, 1)
        self.like_btn = QPushButton(); self.like_btn.setObjectName("YaIcon"); self.like_btn.setFixedSize(34, 34)
        self.more_btn = QPushButton(); self.more_btn.setObjectName("YaIcon"); self.more_btn.setFixedSize(34, 34)
        pl.addWidget(self.like_btn)
        pl.addWidget(self.more_btn)
        v.addWidget(pill, 0, Qt.AlignmentFlag.AlignHCenter)
        row = QHBoxLayout(); row.setSpacing(26)
        row.addStretch(1)
        self.prev_btn = QPushButton(); self.prev_btn.setObjectName("YaIcon"); self.prev_btn.setFixedSize(40, 40)
        self.play_btn = QPushButton()
        self.play_btn.setFixedSize(56, 56)
        self.play_btn.setStyleSheet(f"QPushButton{{background:{YELLOW};border-radius:28px;padding:0;}}"
                                    "QPushButton:hover{background:#ffe55c;}")
        self.next_btn = QPushButton(); self.next_btn.setObjectName("YaIcon"); self.next_btn.setFixedSize(40, 40)
        for b in (self.prev_btn, self.play_btn, self.next_btn):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)
        self.hint = QLabel("")
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint.setStyleSheet("color:rgba(255,255,255,0.75);font-size:12px;font-weight:600;background:transparent;")
        v.addWidget(self.hint)
        v.addStretch(1)                            # лишняя высота (большое окно) — под блоком, а не в плашке

        # «Настроить» — окошко настроек волны (ya_wave_tune), как в Яндекс Музыке
        self.tune_btn = QPushButton("  Настроить", self)
        self.tune_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.tune_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.tune_btn.setFixedHeight(36)
        self.tune_btn.setStyleSheet("QPushButton{background:rgba(255,255,255,0.10);color:#ffffff;border:none;"
                                    "border-radius:18px;padding:0 16px 0 12px;font-size:13px;font-weight:700;}"
                                    "QPushButton:hover{background:rgba(255,255,255,0.18);}")

        self._timer = FrameTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    # ── API ── #

    def set_mood(self, title, kind, arg, instant=False):
        self.title = title
        self._title_pm = None
        core, halo, ray = palette_for(kind, arg)
        with self._worker.lock:
            self.glow.set_palette(core, halo, ray, instant)
        self.update()

    # ── цикл ── #

    def showEvent(self, e):
        super().showEvent(e)
        self._last = time.monotonic()
        self._timer.start()

    def hideEvent(self, e):
        super().hideEvent(e)
        self._timer.stop()

    def shutdown(self):
        self._timer.stop()
        self._worker.stop()

    def _center(self):
        return 0.655, 0.40

    def resizeEvent(self, e):
        super().resizeEvent(e)
        W, H = self.width(), self.height()
        self.wheel.setGeometry(0, 0, int(W * 0.34), H)
        cx, cy = self._center()
        cw = 520
        self.ctrl.setGeometry(int(cx * W - cw / 2), int(H * 0.60), cw, int(H * 0.40) - 10)
        self.tune_btn.adjustSize()
        self.tune_btn.move(W - self.tune_btn.width() - 24, 20)

    def _tick(self):
        now = time.monotonic()
        dt = min(0.1, now - self._last)
        self._last = now
        lvl = 0.0
        try:
            if self.engine.is_playing():
                s = self.engine.get_visual_samples(512)
                if s is not None and len(s):
                    a = np.asarray(s, dtype=np.float32)
                    lvl = float(np.sqrt(np.mean(a * a)))
        except Exception:                            # noqa: BLE001
            lvl = 0.0
        target = min(1.0, lvl * 4.5)
        k = (1.0 - math.exp(-dt * 26.0)) if target > self._beat else (1.0 - math.exp(-dt * 5.0))
        self._beat += (target - self._beat) * k
        # удар: резкий скачок громкости над её средним (сглаживание по времени, не по кадрам)
        avg = getattr(self, "_avg", 0.0)
        a = 1.0 - math.exp(-dt * 1.9)
        self._avg = avg * (1.0 - a) + lvl * a
        self._cool = max(0.0, getattr(self, "_cool", 0.0) - dt)
        if lvl > 0.04 and lvl > avg * 1.32 and self._cool <= 0:
            with self._worker.lock:
                self.glow.hit(min(1.0, (lvl / max(avg, 1e-3) - 1.0) * 1.5 + 0.3))
            self._cool = 0.28
        W, H = max(1, self.width()), max(1, self.height())
        cx, cy = self._center()
        res = self._worker.take()
        if res is not None:
            self._img, self._off = res
            # перерисовываем только область сияния — дуга-список слева не трогается
            x0 = int(W * 0.34)
            self.update(QRect(x0, 0, W - x0, H))
        acc = getattr(self, "_acc_dt", 0.0) + dt
        # сияние мягкое — 30 кадров в секунду хватает; 60 вдвое грузили и поток, и перерисовку
        # кнопок поверх него
        if acc >= 1 / 31 and self._worker.submit(W, H, cx, cy, now - self._t0, self._beat, acc):
            self._acc_dt = 0.0
        else:
            self._acc_dt = acc

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        W, H = self.width(), self.height()
        # скругление — готовой областью: сглаженный clip-путь на всё окно стоил
        # миллисекунды каждый кадр, а на чёрном фоне разницы в углах не видно
        if getattr(self, "_clip_key", None) != (W, H):
            path = QPainterPath()
            path.addRoundedRect(QRectF(0, 0, W, H), 16, 16)
            self._clip_rgn = QRegion(path.toFillPolygon().toPolygon())
            self._clip_key = (W, H)
        p.setClipRegion(self._clip_rgn)
        p.fillRect(self.rect(), QColor(0, 0, 0))
        if self._img is not None:
            ox, oy = getattr(self, "_off", (0.0, 0.0))     # «плавание» центра — сдвигом, с запасом по краям
            m = GlowField.MARGIN
            p.drawImage(QRectF((ox - m) * W, (oy - m) * H, W * (1 + 2 * m), H * (1 + 2 * m)), self._img)
        # левая часть — чистый чёрный под списком (сияние туда не заходит).
        # Слева от x0 всегда ровно чёрный: эта зона не перерисовывается каждый
        # кадр (частичный update), и любое «остаточное» сияние в ней давало
        # видимую чёрную полосу-стык. Плавный переход — целиком правее x0.
        x0 = int(W * 0.34)
        p.fillRect(QRectF(0, 0, x0 + 1, H), QColor(0, 0, 0))
        g = QLinearGradient(x0, 0, W * 0.50, 0)
        g.setColorAt(0.0, QColor(0, 0, 0, 255))
        g.setColorAt(0.35, QColor(0, 0, 0, 200))
        g.setColorAt(0.7, QColor(0, 0, 0, 80))
        g.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(QRectF(x0, 0, W * 0.50 - x0, H), g)
        # название настроения (готовая картинка — текст не растеризуется каждый кадр)
        cx, cy = self._center()
        tw, th = int(W * 0.6), 130
        if self._title_pm is None or self._title_pm.width() != tw:
            pm = QPixmap(tw, th)
            pm.fill(Qt.GlobalColor.transparent)
            q = QPainter(pm)
            q.setRenderHint(QPainter.RenderHint.Antialiasing)
            f = _font(max(40, min(76, W * 0.045)), QFont.Weight.Black)
            f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 96)
            q.setFont(f)
            r = QRectF(0, 0, tw, th - 6)
            q.setPen(QColor(0, 0, 0, 60))
            q.drawText(r.translated(0, 3), Qt.AlignmentFlag.AlignCenter, self.title)
            q.setPen(QColor(255, 255, 255))
            q.drawText(r, Qt.AlignmentFlag.AlignCenter, self.title)
            q.end()
            self._title_pm = pm
        p.drawPixmap(int(cx * W - tw / 2), int(cy * H - 62), self._title_pm)
        p.end()
