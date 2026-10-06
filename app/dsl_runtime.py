# dsl_runtime.py
"""
EchoScript — рантайм: всё, что скрипт может видеть и делать.

  AudioAnalyzer — из PCM, который реально звучит (AudioEngine.get_visual_samples),
                  считает спектр (64 полосы), бас/середину/верх, уровень, пик, «бит»
                  и форму волны; автоусиление (AGC), поэтому значения всегда 0..1.
  PlayerBridge  — состояние плеера для скрипта (трек, позиция, громкость, плейлист)
                  и действия (play/pause/next/seek/…).
  Gfx           — рисование через QPainter (фигуры, текст, картинки, градиенты,
                  трансформации, режимы смешивания).
  Scene         — скомпилированный скрипт: тема, экземпляры виджетов, события,
                  ввод мышью, горячая перезагрузка с сохранением состояния.
  ScriptCanvas  — QWidget, на котором живёт сцена (60 кадров/с).
"""
from __future__ import annotations

import math
import random
import time
from pathlib import Path

import numpy as np
from img_load import load_pixmap
from PyQt6.QtCore import Qt, QLineF, QPointF, QRectF, QTimer, pyqtSignal
from PyQt6.QtGui import (QBrush, QColor, QFont, QFontMetricsF, QLinearGradient, QPainter, QPainterPath,
                         QPen, QPixmap, QRadialGradient, QConicalGradient)
from PyQt6.QtWidgets import QWidget

import dsl_lang as L
from dsl_lang import EchoError

BANDS = 64


class _MediaPool:
    """Анимированные картинки (GIF/WEBP) и видео для image()/video(). Источник живёт, пока его рисуют:
    через несколько секунд без рисования декодер останавливается (видео не тратит CPU в фоне)."""
    ANIM_EXT = (".gif", ".webp", ".apng")

    def __init__(self):
        self.items = {}                                    # (kind, path) -> [источник, когда рисовали, когда создан]

    def get(self, kind, path):
        key = (kind, path)
        now = time.monotonic()
        it = self.items.get(key)
        if it is not None and it[0] is None and now - it[2] > 5.0:
            self.items.pop(key, None)                      # не открылся — через 5 с попробуем ещё раз
            it = None
        if it is None:
            src = None
            try:
                import theme_layers as TL
                if kind == "video":
                    src = TL.VideoSource(path)
                    if not src.ok:
                        src.stop()
                        src = None
                else:
                    src = TL.MediaSource(path)
                    if not src.valid():
                        src = None
            except Exception as e:                         # noqa: BLE001
                print("[echoscript] media:", e)
                src = None
            it = [src, now, now]
            self.items[key] = it
        it[1] = now
        return it[0]

    def sweep(self, idle=4.0):
        now = time.monotonic()
        for k, it in list(self.items.items()):
            if now - it[1] > idle:
                try:
                    if it[0] is not None:
                        it[0].stop()
                except Exception:                          # noqa: BLE001
                    pass
                self.items.pop(k, None)

    def clear(self):
        self.sweep(-1.0)


MEDIA = _MediaPool()

EV_FIELD = {ev: "on_" + ev for ev in ("init", "frame", "draw", "press", "release", "click", "move", "drag", "wheel",
                                       "beat", "track", "key", "enter", "leave", "resize")}


def svg_path(d: str) -> QPainterPath:
    """Разбор SVG path data в QPainterPath (команды M L H V C S Q T A Z, строчные — относительные)."""
    import re as _re
    toks = _re.findall(r"[MmLlHhVvCcSsQqTtAaZz]|-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", d or "")
    pp = QPainterPath()
    i = 0
    cmd = None
    cx = cy = sx = sy = 0.0
    lc = None                                              # последняя контрольная точка (для S/T)

    def num():
        nonlocal i
        v = float(toks[i])
        i += 1
        return v
    try:
        while i < len(toks):
            t = toks[i]
            if t.isalpha():
                cmd = t
                i += 1
                if cmd in "Zz":
                    pp.closeSubpath()
                    cx, cy = sx, sy
                    lc = None
                    continue
            elif cmd is None:
                break
            rel = cmd.islower()
            C = cmd.upper()
            ox, oy = (cx, cy) if rel else (0.0, 0.0)
            if C == "M":
                cx, cy = ox + num(), oy + num()
                pp.moveTo(cx, cy)
                sx, sy = cx, cy
                cmd = "l" if rel else "L"                  # дальше пары — это lineto
                lc = None
            elif C == "L":
                cx, cy = ox + num(), oy + num()
                pp.lineTo(cx, cy)
                lc = None
            elif C == "H":
                cx = (cx if rel else 0.0) + num()
                pp.lineTo(cx, cy)
                lc = None
            elif C == "V":
                cy = (cy if rel else 0.0) + num()
                pp.lineTo(cx, cy)
                lc = None
            elif C == "C":
                x1, y1, x2, y2 = ox + num(), oy + num(), ox + num(), oy + num()
                cx, cy = ox + num(), oy + num()
                pp.cubicTo(x1, y1, x2, y2, cx, cy)
                lc = ("c", x2, y2)
            elif C == "S":
                x1, y1 = (2 * cx - lc[1], 2 * cy - lc[2]) if lc and lc[0] == "c" else (cx, cy)
                x2, y2 = ox + num(), oy + num()
                cx, cy = ox + num(), oy + num()
                pp.cubicTo(x1, y1, x2, y2, cx, cy)
                lc = ("c", x2, y2)
            elif C == "Q":
                x1, y1 = ox + num(), oy + num()
                cx, cy = ox + num(), oy + num()
                pp.quadTo(x1, y1, cx, cy)
                lc = ("q", x1, y1)
            elif C == "T":
                x1, y1 = (2 * cx - lc[1], 2 * cy - lc[2]) if lc and lc[0] == "q" else (cx, cy)
                cx, cy = ox + num(), oy + num()
                pp.quadTo(x1, y1, cx, cy)
                lc = ("q", x1, y1)
            elif C == "A":
                rx, ry, rot, large, sweep = abs(num()), abs(num()), num(), num(), num()
                ex, ey = ox + num(), oy + num()
                _svg_arc(pp, cx, cy, rx, ry, rot, bool(large), bool(sweep), ex, ey)
                cx, cy = ex, ey
                lc = None
            else:
                i += 1
    except (IndexError, ValueError):
        pass                                               # обрывок в конце — рисуем то, что разобрали
    return pp


def _svg_arc(pp, x1, y1, rx, ry, phi, large, sweep, x2, y2):
    """Дуга SVG (endpoint parameterization) → кубические кривые."""
    if rx == 0 or ry == 0:
        pp.lineTo(x2, y2)
        return
    ph = math.radians(phi)
    cp, sp = math.cos(ph), math.sin(ph)
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    x1p, y1p = cp * dx + sp * dy, -sp * dx + cp * dy
    lam = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
    if lam > 1:
        k = math.sqrt(lam)
        rx, ry = rx * k, ry * k
    num_ = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    co = math.sqrt(max(0.0, num_ / den)) if den else 0.0
    if large == sweep:
        co = -co
    cxp, cyp = co * rx * y1p / ry, -co * ry * x1p / rx
    ccx = cp * cxp - sp * cyp + (x1 + x2) / 2
    ccy = sp * cxp + cp * cyp + (y1 + y2) / 2

    def ang(ux, uy, vx, vy):
        a = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
        return a
    t1 = ang(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dt = ang((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sweep and dt > 0:
        dt -= 2 * math.pi
    elif sweep and dt < 0:
        dt += 2 * math.pi
    n = max(1, int(math.ceil(abs(dt) / (math.pi / 2))))
    step = dt / n
    k = 4 / 3 * math.tan(step / 4)
    t = t1
    for _ in range(n):
        c1, s1 = math.cos(t), math.sin(t)
        c2, s2 = math.cos(t + step), math.sin(t + step)
        p1 = (c1 - k * s1, s1 + k * c1)
        p2 = (c2 + k * s2, s2 - k * c2)
        p3 = (c2, s2)

        def tr(px, py):
            return ccx + rx * px * cp - ry * py * sp, ccy + rx * px * sp + ry * py * cp
        a1, b1 = tr(*p1)
        a2, b2 = tr(*p2)
        a3, b3 = tr(*p3)
        pp.cubicTo(a1, b1, a2, b2, a3, b3)
        t += step


# ══════════════════════════════════════════════════════════════════════════ #
#  Звук
# ══════════════════════════════════════════════════════════════════════════ #

class AudioAnalyzer:
    N = 2048

    def __init__(self, engine):
        self.engine = engine
        self.fft = [0.0] * BANDS
        self.wave = [0.0] * 128
        self.bass = self.mid = self.high = self.level = self.peak = 0.0
        self.beat = False
        self.beat_power = 0.0
        self._rmax = 1e-3
        self._bmax = {"bass": 1e-3, "mid": 1e-3, "high": 1e-3}
        self._lvl_max = 1e-3
        self._bass_avg = 0.0
        self._cool = 0.0
        self._edges = None
        self._sr = None
        self._win = np.hanning(self.N).astype(np.float32)

    def _setup(self, sr):
        self._sr = sr
        freqs = np.fft.rfftfreq(self.N, 1.0 / sr)
        edges_hz = np.geomspace(32.0, min(16000.0, sr / 2 - 1), BANDS + 1)
        idx = np.searchsorted(freqs, edges_hz)
        idx = np.maximum.accumulate(np.maximum(idx, 1))
        for i in range(1, len(idx)):                      # каждая полоса — хотя бы 1 бин
            if idx[i] <= idx[i - 1]:
                idx[i] = idx[i - 1] + 1
        self._edges = np.minimum(idx, len(freqs) - 1)
        self._freqs = freqs

        def rng(lo, hi):
            return slice(int(np.searchsorted(freqs, lo)), max(int(np.searchsorted(freqs, hi)), 1))
        self._sl = {"bass": rng(30, 160), "mid": rng(250, 2500), "high": rng(4000, 13000)}

    def update(self, dt: float, playing: bool):
        s = None
        try:
            if self.engine is not None and playing:
                s = self.engine.get_visual_samples(self.N)
        except Exception:                                  # noqa: BLE001
            s = None
        decay = 0.0 if s is not None else min(1.0, dt * 4.0)
        if s is None or len(s) < self.N:
            self.fft = [v * (1 - decay) for v in self.fft]
            self.bass *= 1 - decay
            self.mid *= 1 - decay
            self.high *= 1 - decay
            self.level *= 1 - decay
            self.peak *= 1 - decay
            self.wave = [v * (1 - decay) for v in self.wave]
            self.beat = False
            return
        try:
            sr = int(getattr(self.engine, "visual_sample_rate", 44100) or 44100)
        except Exception:                                  # noqa: BLE001
            sr = 44100
        if sr != self._sr:
            self._setup(sr)
        x = np.asarray(s, dtype=np.float32)
        mag = np.abs(np.fft.rfft(x * self._win)).astype(np.float32)
        csum = np.concatenate([[0.0], np.cumsum(mag)])
        e = self._edges
        band = (csum[e[1:]] - csum[e[:-1]]) / np.maximum(e[1:] - e[:-1], 1)
        band = np.log1p(band * 4.0)
        m = float(band.max()) if band.size else 0.0
        self._rmax = max(self._rmax * (1 - 0.35 * dt), m, 1e-3)       # плавное автоусиление
        tgt = np.clip(band / self._rmax, 0.0, 1.0)
        cur = np.asarray(self.fft, dtype=np.float32)
        up = tgt > cur
        k_up, k_dn = min(1.0, dt * 28.0), min(1.0, dt * 9.0)
        cur = np.where(up, cur + (tgt - cur) * k_up, cur + (tgt - cur) * k_dn)
        self.fft = [float(v) for v in cur]
        vals = {}
        for name, sl in self._sl.items():
            v = float(np.log1p(mag[sl].mean() * 4.0)) if sl.stop > sl.start else 0.0
            self._bmax[name] = max(self._bmax[name] * (1 - 0.3 * dt), v, 1e-3)
            vals[name] = min(1.0, v / self._bmax[name])
        for name in ("bass", "mid", "high"):
            old = getattr(self, name)
            t = vals[name]
            setattr(self, name, old + (t - old) * (k_up if t > old else k_dn))
        rms = float(np.sqrt(np.mean(x * x)))
        self._lvl_max = max(self._lvl_max * (1 - 0.25 * dt), rms, 1e-4)
        lvl = min(1.0, rms / self._lvl_max)
        self.level += (lvl - self.level) * (k_up if lvl > self.level else k_dn)
        pk = min(1.0, float(np.max(np.abs(x))))
        self.peak = max(pk, self.peak - dt * 0.8)
        step = len(x) // 128
        self.wave = [float(v) for v in x[: step * 128: step]]
        # бит: всплеск баса над его средним
        self._bass_avg += (vals["bass"] - self._bass_avg) * min(1.0, dt * 2.5)
        self._cool = max(0.0, self._cool - dt)
        self.beat = False
        if vals["bass"] > 0.35 and vals["bass"] > self._bass_avg * 1.32 and self._cool <= 0:
            self.beat = True
            self.beat_power = min(1.0, (vals["bass"] - self._bass_avg) * 2.5 + 0.35)
            self._cool = 0.22

    def as_dict(self):
        return {"fft": self.fft, "wave": self.wave, "bass": self.bass, "mid": self.mid, "high": self.high,
                "level": self.level, "peak": self.peak, "beat": self.beat, "beat_power": self.beat_power,
                "bands": BANDS}


# ══════════════════════════════════════════════════════════════════════════ #
#  Плеер
# ══════════════════════════════════════════════════════════════════════════ #

class PlayerBridge:
    """Чтение состояния плеера и действия. win — MainWindow (может быть None в тестах)."""

    def __init__(self, win):
        self.win = win
        self._pl_key = None
        self._pl_cache = []

    def _tracks(self):
        w = self.win
        if w is None:
            return []
        if w.queue_context == "library":
            return w.library
        return w.playlists.get(w.queue_context, {}).get("tracks", [])

    def current(self):
        w = self.win
        tr = self._tracks()
        if w is not None and 0 <= w.current_index < len(tr):
            return tr[w.current_index]
        return None

    def track(self):
        t = self.current() or {}
        eng = getattr(self.win, "engine", None)
        pos = dur = 0.0
        try:
            pos = float(eng.get_position() or 0) if eng is not None else 0.0
            dur = float(eng.duration or t.get("duration") or 0) if eng is not None else 0.0
        except Exception:                                  # noqa: BLE001
            pass
        title = t.get("title", "") or ""
        try:
            from lyrics import sanitize_title
            title = sanitize_title(title)
        except Exception:                                  # noqa: BLE001
            pass
        return {"title": title, "artist": t.get("artist", "") or "", "album": t.get("album", "") or "",
                "cover": t.get("cover", "") or "", "path": str(t.get("path", "") or ""),
                "duration": dur, "position": pos, "progress": (pos / dur) if dur > 0 else 0.0,
                "loaded": bool(t)}

    def player(self):
        w = self.win
        if w is None:
            return {"playing": False, "volume": 0.0, "shuffle": False, "repeat": 0}
        eng = w.engine
        try:
            playing = bool(eng.is_playing())
        except Exception:                                  # noqa: BLE001
            playing = False
        return {"playing": playing, "volume": float(w.settings.get("volume", 80)) / 100.0,
                "shuffle": bool(getattr(w, "shuffle", False)), "repeat": int(getattr(w, "repeat_mode", 0) or 0)}

    def playlist(self):
        w = self.win
        tr = self._tracks()
        name = "Библиотека" if (w is None or w.queue_context == "library") else w.queue_context
        key = (name, len(tr), id(tr))
        if key != self._pl_key:
            self._pl_key = key
            try:
                from lyrics import sanitize_title
            except Exception:                              # noqa: BLE001
                def sanitize_title(s):
                    return s
            self._pl_cache = [{"title": sanitize_title(t.get("title", "") or ""), "artist": t.get("artist", "") or "",
                               "duration": float(t.get("duration") or 0), "cover": t.get("cover", "") or ""}
                              for t in tr]
        return {"name": name, "tracks": self._pl_cache, "index": getattr(w, "current_index", -1) if w else -1,
                "count": len(self._pl_cache)}

    def lyrics(self):
        """Текст песни: строки, номер текущей (по таймингам) и сама строка."""
        lv = getattr(self.win, "lyrics_view", None)
        lines = getattr(lv, "_lines", None) or []
        if id(lines) != getattr(self, "_ly_key", None):
            self._ly_key = id(lines)
            self._ly_cache = [str(ln.get("text", "")) if isinstance(ln, dict) else str(ln) for ln in lines]
        idx = -1
        try:
            idx = int(getattr(getattr(lv, "_text_panel", None), "_active_idx", -1))
        except Exception:                                  # noqa: BLE001
            pass
        txt = self._ly_cache[idx] if 0 <= idx < len(self._ly_cache) else ""
        return {"lines": self._ly_cache, "index": idx, "line": txt, "count": len(self._ly_cache),
                "timed": bool(getattr(lv, "_has_timings", False))}

    # действия
    def actions(self):
        w = self.win

        def guard(fn):
            def f(*a):
                if w is None:
                    return None
                try:
                    return fn(*a)
                except Exception as e:                     # noqa: BLE001
                    raise EchoError(f"действие не выполнилось: {e}")
            return f

        def seek(frac):
            dur = float(w.engine.duration or 0)
            if dur > 0:
                w.engine.seek(max(0.0, min(1.0, float(frac))) * dur)

        def set_volume(v):
            w.vol.setValue(int(max(0.0, min(1.0, float(v))) * 100))

        def play_index(i):
            ctx = w.queue_context or "library"
            w._play_index(int(i), ctx)

        def mute():
            v = w.vol.value()
            if v > 0:
                self._unmute = v
                w.vol.setValue(0)
            else:
                w.vol.setValue(getattr(self, "_unmute", 80))

        def export_fx():
            if hasattr(w, "export_effects"):
                t = self.current()
                if t:
                    w.export_effects([t])

        return {
            "toggle": guard(lambda: w.toggle_play()),
            "play": guard(lambda: None if w.engine.is_playing() else w.toggle_play()),
            "pause": guard(lambda: w.toggle_play() if w.engine.is_playing() else None),
            "next": guard(lambda: w.next_track()),
            "prev": guard(lambda: w.prev_track()),
            "seek": guard(seek),
            "set_volume": guard(set_volume),
            "play_index": guard(play_index),
            "toggle_shuffle": guard(lambda: w._toggle_shuffle()),
            "cycle_repeat": guard(lambda: w._cycle_repeat()),
            "export_fx": guard(export_fx),
            # кнопки интерфейса плеера — для своих кнопок в конструкторе тем
            "lyrics_mode": guard(lambda: w._toggle_lyrics_mode()),
            "show_queue": guard(lambda: w._show_queue_popup()),
            "cover_mode": guard(lambda: w._toggle_cover_mode()),
            "clip_mode": guard(lambda: w._toggle_clip_mode()),
            "music_hub": guard(lambda: w._open_hub()),
            "theme_builder": guard(lambda: w._open_theme_studio()),
            "fullscreen": guard(lambda: w._toggle_fullscreen()),
            "theme_menu": guard(lambda: w._ui_layers_menu(__import__("PyQt6.QtGui", fromlist=["QCursor"]).QCursor.pos())),
            "add_files": guard(lambda: w._add_files()),
            "downloader": guard(lambda: w._open_downloader()),
            "profile": guard(lambda: w._open_profile()),
            "mute": guard(mute),
        }


# ══════════════════════════════════════════════════════════════════════════ #
#  Рисование
# ══════════════════════════════════════════════════════════════════════════ #

_COLOR_CACHE: dict = {}


def to_qcolor(v) -> QColor:
    if v.__class__ is QColor:
        return v
    c = _COLOR_CACHE.get(v)
    if c is not None:
        return c
    if isinstance(v, str):
        s = v.strip()
        if s.startswith("#") and len(s) == 9:              # #rrggbbaa (как в CSS)
            c = QColor(int(s[1:3], 16), int(s[3:5], 16), int(s[5:7], 16), int(s[7:9], 16))
        else:
            c = QColor(s)
    elif isinstance(v, (int, float)):
        g = int(max(0, min(255, v)))
        c = QColor(g, g, g)
    else:
        raise EchoError(f"это не цвет: {v!r}")
    if not c.isValid():
        raise EchoError(f"непонятный цвет «{v}»")
    if len(_COLOR_CACHE) > 4096:
        _COLOR_CACHE.clear()
    _COLOR_CACHE[v] = c
    return c


def _hex(c: QColor) -> str:
    return "#%02x%02x%02x%02x" % (c.red(), c.green(), c.blue(), c.alpha())


# Цветовые функции возвращают готовый QColor (а не строку «#…»): цвет, меняющийся каждый кадр,
# не нужно заново разбирать из текста — это заметно ускоряет визуализаторы.

def _cl(x):
    return 0 if x < 0 else 255 if x > 255 else int(x + 0.5)


def rgb(r, g, b, a=1.0):
    return QColor(_cl(r), _cl(g), _cl(b), _cl(a * 255))


def hsv(h, s=1.0, v=1.0, a=1.0):
    return QColor.fromHsvF((float(h) % 360) / 360.0, max(0.0, min(1.0, float(s))), max(0.0, min(1.0, float(v))),
                           max(0.0, min(1.0, float(a))))


def mix(c1, c2, t=0.5):
    a, b = to_qcolor(c1), to_qcolor(c2)
    t = 0.0 if t < 0 else 1.0 if t > 1 else float(t)
    return QColor(_cl(a.red() + (b.red() - a.red()) * t), _cl(a.green() + (b.green() - a.green()) * t),
                  _cl(a.blue() + (b.blue() - a.blue()) * t), _cl(a.alpha() + (b.alpha() - a.alpha()) * t))


def with_alpha(c, a):
    q = to_qcolor(c)
    a = 0.0 if a < 0 else 1.0 if a > 1 else float(a)
    return QColor(q.red(), q.green(), q.blue(), _cl(a * 255))


def color_hex(c):
    return _hex(to_qcolor(c))


_BLEND = {"normal": QPainter.CompositionMode.CompositionMode_SourceOver,
          "add": QPainter.CompositionMode.CompositionMode_Plus,
          "screen": QPainter.CompositionMode.CompositionMode_Screen,
          "multiply": QPainter.CompositionMode.CompositionMode_Multiply,
          "overlay": QPainter.CompositionMode.CompositionMode_Overlay,
          "lighten": QPainter.CompositionMode.CompositionMode_Lighten,
          "darken": QPainter.CompositionMode.CompositionMode_Darken,
          "difference": QPainter.CompositionMode.CompositionMode_Difference}


class Gfx:
    """Состояние кисти/пера и функции рисования для текущего QPainter."""

    def __init__(self, scene):
        self.scene = scene
        self.p: QPainter | None = None
        self._img_cache: dict = {}
        self._font_cache: dict = {}
        self.reset()

    def reset(self):
        self.brush = QBrush(QColor(255, 255, 255))
        self.pen = Qt.PenStyle.NoPen
        self.stack = []

    def need(self):
        if self.p is None:
            raise EchoError("рисовать можно только в обработчике on draw")
        return self.p

    def _apply(self):
        p = self.p
        p.setBrush(self.brush)
        p.setPen(self.pen)

    # ── кисть/перо ──
    def _brush_of(self, v):
        if isinstance(v, dict) and v.get("__grad__"):
            return QBrush(self._make_grad(v))
        return QBrush(to_qcolor(v))

    def _make_grad(self, d):
        k = d["__grad__"]
        if k == "linear":
            g = QLinearGradient(*d["pts"])
        elif k == "radial":
            cx, cy, r = d["pts"]
            g = QRadialGradient(cx, cy, r)
        else:
            cx, cy, ang = d["pts"]
            g = QConicalGradient(cx, cy, ang)
        stops = d["stops"]
        n = len(stops)
        for i, s in enumerate(stops):
            if isinstance(s, list) and len(s) == 2:
                g.setColorAt(max(0.0, min(1.0, float(s[0]))), to_qcolor(s[1]))
            else:
                g.setColorAt(i / max(1, n - 1), to_qcolor(s))
        return g

    def fill(self, c):
        self.brush = self._brush_of(c)

    def nofill(self):
        self.brush = QBrush(Qt.BrushStyle.NoBrush)

    def stroke(self, c, width=1.0, cap="round"):
        pen = QPen(self._brush_of(c), float(width))
        pen.setCapStyle({"flat": Qt.PenCapStyle.FlatCap, "square": Qt.PenCapStyle.SquareCap}
                        .get(cap, Qt.PenCapStyle.RoundCap))
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        self.pen = pen

    def nostroke(self):
        self.pen = Qt.PenStyle.NoPen

    def alpha(self, a):
        self.need().setOpacity(max(0.0, min(1.0, float(a))))

    def blend(self, mode="normal"):
        m = _BLEND.get(str(mode))
        if m is None:
            raise EchoError(f"неизвестный режим смешивания «{mode}» (есть: {', '.join(_BLEND)})")
        self.need().setCompositionMode(m)

    # ── трансформации ──
    def save(self):
        self.need().save()
        self.stack.append((self.brush, self.pen))

    def restore(self):
        p = self.need()
        if not self.stack:
            raise EchoError("restore() без парного save()")
        p.restore()
        self.brush, self.pen = self.stack.pop()

    def translate(self, x, y):
        self.need().translate(float(x), float(y))

    def rotate(self, deg):
        self.need().rotate(float(deg))

    def scale(self, sx, sy=None):
        self.need().scale(float(sx), float(sx if sy is None else sy))

    def clip(self, x, y, w, h, r=0):
        path = QPainterPath()
        path.addRoundedRect(QRectF(float(x), float(y), float(w), float(h)), float(r), float(r))
        self.need().setClipPath(path, Qt.ClipOperation.IntersectClip)

    # ── фигуры ──
    def clear(self, c):
        p = self.need()
        p.save()
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        r = p.clipBoundingRect() if p.hasClipping() else QRectF(-1e5, -1e5, 2e5, 2e5)
        p.fillRect(r, to_qcolor(c))
        p.restore()

    def rect(self, x, y, w, h, r=0):
        p = self.need()
        self._apply()
        if r:
            p.drawRoundedRect(QRectF(float(x), float(y), float(w), float(h)), float(r), float(r))
        else:
            p.drawRect(QRectF(float(x), float(y), float(w), float(h)))

    def circle(self, x, y, r):
        p = self.need()
        self._apply()
        r = float(r)
        p.drawEllipse(QPointF(float(x), float(y)), r, r)

    def ellipse(self, x, y, rx, ry):
        p = self.need()
        self._apply()
        p.drawEllipse(QPointF(float(x), float(y)), float(rx), float(ry))

    def line(self, x1, y1, x2, y2):
        p = self.need()
        p.setPen(self.pen if self.pen != Qt.PenStyle.NoPen else QPen(self.brush, 1.0))
        p.drawLine(QPointF(float(x1), float(y1)), QPointF(float(x2), float(y2)))

    def lines(self, coords):
        """Много отрезков одним вызовом: [x1, y1, x2, y2,  x1, y1, x2, y2, …] — намного быстрее line() в цикле."""
        p = self.need()
        if not isinstance(coords, list):
            raise EchoError("lines(): нужен список [x1, y1, x2, y2, …]")
        n = len(coords) // 4
        if n <= 0:
            return
        p.setPen(self.pen if self.pen != Qt.PenStyle.NoPen else QPen(self.brush, 1.0))
        c = coords
        p.drawLines([QLineF(c[i], c[i + 1], c[i + 2], c[i + 3]) for i in range(0, n * 4, 4)])

    @staticmethod
    def _pts(points):
        if not isinstance(points, list):
            raise EchoError("точки — список [x1, y1, x2, y2, …]")
        if points and isinstance(points[0], list):
            return [QPointF(float(a[0]), float(a[1])) for a in points]
        return [QPointF(float(points[i]), float(points[i + 1])) for i in range(0, len(points) - 1, 2)]

    def poly(self, points, closed=True):
        p = self.need()
        pts = self._pts(points)
        if len(pts) < 2:
            return
        path = QPainterPath(pts[0])
        for q in pts[1:]:
            path.lineTo(q)
        if closed:
            path.closeSubpath()
            self._apply()
        else:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(self.pen if self.pen != Qt.PenStyle.NoPen else QPen(self.brush, 1.0))
        p.drawPath(path)

    def curve(self, points, closed=False, tension=0.5):
        """Плавная кривая через точки (Catmull-Rom → кубические Безье)."""
        p = self.need()
        pts = self._pts(points)
        n = len(pts)
        if n < 2:
            return
        t = float(tension)
        path = QPainterPath(pts[0])
        rng = range(n) if closed else range(n - 1)
        for i in rng:
            p0 = pts[(i - 1) % n] if (closed or i > 0) else pts[i]
            p1 = pts[i]
            p2 = pts[(i + 1) % n]
            p3 = pts[(i + 2) % n] if (closed or i + 2 < n) else p2
            c1 = QPointF(p1.x() + (p2.x() - p0.x()) * t / 3, p1.y() + (p2.y() - p0.y()) * t / 3)
            c2 = QPointF(p2.x() - (p3.x() - p1.x()) * t / 3, p2.y() - (p3.y() - p1.y()) * t / 3)
            path.cubicTo(c1, c2, p2)
        if closed:
            path.closeSubpath()
            self._apply()
        else:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(self.pen if self.pen != Qt.PenStyle.NoPen else QPen(self.brush, 1.0))
        p.drawPath(path)

    def arc(self, x, y, r, a0, a1):
        p = self.need()
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(self.pen if self.pen != Qt.PenStyle.NoPen else QPen(self.brush, 1.0))
        r = float(r)
        p.drawArc(QRectF(float(x) - r, float(y) - r, 2 * r, 2 * r), int(-float(a0) * 16),
                  int(-(float(a1) - float(a0)) * 16))

    def ring(self, x, y, r1, r2):
        p = self.need()
        path = QPainterPath()
        path.addEllipse(QPointF(float(x), float(y)), float(r2), float(r2))
        path.addEllipse(QPointF(float(x), float(y)), float(r1), float(r1))
        path.setFillRule(Qt.FillRule.OddEvenFill)
        self._apply()
        p.drawPath(path)

    def star(self, x, y, r1, r2, n=5, rot=0):
        n = max(2, int(n))
        pts = []
        for i in range(n * 2):
            a = math.radians(float(rot)) - math.pi / 2 + i * math.pi / n
            rr = float(r1) if i % 2 == 0 else float(r2)
            pts += [float(x) + math.cos(a) * rr, float(y) + math.sin(a) * rr]
        self.poly(pts, True)

    def ngon(self, x, y, r, n=6, rot=0):
        n = max(3, int(n))
        pts = []
        for i in range(n):
            a = math.radians(float(rot)) - math.pi / 2 + i * 2 * math.pi / n
            pts += [float(x) + math.cos(a) * float(r), float(y) + math.sin(a) * float(r)]
        self.poly(pts, True)

    _GLOW: dict = {}

    def glow(self, x, y, r, c, strength=1.0):
        """Мягкое свечение. Рисуется готовым спрайтом (кэш по цвету), масштабируется под радиус —
        в десятки раз быстрее, чем радиальный градиент на каждый вызов."""
        p = self.need()
        r = float(r)
        if r < 0.5:
            return
        q = to_qcolor(c)
        a = max(0.0, min(1.0, q.alphaF() * float(strength)))
        if a <= 0.003:
            return
        key = (q.red(), q.green(), q.blue())
        pm = self._GLOW.get(key)
        if pm is None:
            S = 128
            pm = QPixmap(S, S)
            pm.fill(Qt.GlobalColor.transparent)
            gp = QPainter(pm)
            gp.setRenderHint(QPainter.RenderHint.Antialiasing)
            g = QRadialGradient(S / 2, S / 2, S / 2)
            base = QColor(q.red(), q.green(), q.blue(), 255)
            g.setColorAt(0.0, base)
            mid = QColor(base)
            mid.setAlphaF(0.35)
            g.setColorAt(0.35, mid)
            end = QColor(base)
            end.setAlpha(0)
            g.setColorAt(1.0, end)
            gp.setPen(Qt.PenStyle.NoPen)
            gp.setBrush(QBrush(g))
            gp.drawEllipse(0, 0, S, S)
            gp.end()
            if len(self._GLOW) > 64:
                self._GLOW.clear()
            self._GLOW[key] = pm
        op = p.opacity()
        p.setOpacity(op * a)
        p.drawPixmap(QRectF(float(x) - r, float(y) - r, 2 * r, 2 * r), pm, QRectF(0, 0, pm.width(), pm.height()))
        p.setOpacity(op)

    # ── текст ──
    def _font(self, size, weight, family):
        key = (round(float(size), 1), int(weight), family)
        f = self._font_cache.get(key)
        if f is None:
            f = QFont()
            fam = family or self.scene.theme.get("font")
            if fam:
                f.setFamilies([fam, "Segoe UI", "Arial"])
            f.setPixelSize(max(1, int(round(float(size)))))
            f.setWeight(QFont.Weight(max(100, min(900, int(weight)))))
            if len(self._font_cache) > 256:
                self._font_cache.clear()
            self._font_cache[key] = f
        return f

    def text(self, s, x, y, size=14, align="left", weight=500, font=None, maxw=0):
        p = self.need()
        f = self._font(size, weight, font)
        p.setFont(f)
        col = self.brush.color() if self.brush.style() != Qt.BrushStyle.NoBrush else QColor(255, 255, 255)
        p.setPen(col)
        s = L._to_str(s)
        fm = QFontMetricsF(f)
        if maxw and float(maxw) > 0:
            s = fm.elidedText(s, Qt.TextElideMode.ElideRight, float(maxw))
        w = fm.horizontalAdvance(s)
        x = float(x)
        if align == "center":
            x -= w / 2
        elif align == "right":
            x -= w
        p.drawText(QPointF(x, float(y) + fm.ascent() * 0.5 - fm.descent() * 0.5), s)

    def textwidth(self, s, size=14, weight=500, font=None):
        return QFontMetricsF(self._font(size, weight, font)).horizontalAdvance(L._to_str(s))

    # ── картинки ──
    def _pix(self, path):
        pm = self._img_cache.pop(path, None)
        if pm is None:
            pm = load_pixmap(path, 2048) if path and Path(path).exists() else QPixmap()
            # бюджет по памяти, а не по штукам: 48 обложек 1200² — это ~280 МБ
            total = sum(q.width() * q.height() * 4 for q in self._img_cache.values()) + pm.width() * pm.height() * 4
            while self._img_cache and (len(self._img_cache) >= 48 or total > 64 * 2 ** 20):
                old = self._img_cache.pop(next(iter(self._img_cache)))
                total -= old.width() * old.height() * 4
        self._img_cache[path] = pm                         # в конец — недавно нужна
        return pm

    def _blit(self, p, pic, x, y, w, h, r, is_image=False):
        """Нарисовать pic в прямоугольник с обрезкой по центру (без искажений) и скруглением r."""
        target = QRectF(float(x), float(y), float(w), float(h))
        sw, sh = pic.width(), pic.height()
        s = min(sw / max(1e-6, target.width()), sh / max(1e-6, target.height()))
        src = QRectF((sw - target.width() * s) / 2, (sh - target.height() * s) / 2,
                     target.width() * s, target.height() * s)
        draw = p.drawImage if is_image else p.drawPixmap
        if r:
            p.save()
            path = QPainterPath()
            path.addRoundedRect(target, float(r), float(r))
            p.setClipPath(path, Qt.ClipOperation.IntersectClip)
            draw(target, pic, src)
            p.restore()
        else:
            draw(target, pic, src)

    def image(self, path, x, y, w, h, r=0):
        p = self.need()
        path = str(path or "")
        if path.lower().endswith(MEDIA.ANIM_EXT) and Path(path).exists():
            src = MEDIA.get("anim", path)
            pm = src.pix if src is not None else QPixmap()
        else:
            pm = self._pix(path)
        if pm.isNull():
            return False
        self._blit(p, pm, x, y, w, h, r)
        return True

    def video(self, path, x, y, w, h, r=0, rate=1.0):
        """Видео без звука, по кругу. false — файла нет или не открылся (пока грузится — true, но пусто)."""
        p = self.need()
        path = str(path or "")
        if not path or not Path(path).exists():
            return False
        src = MEDIA.get("video", path)
        if src is None:
            return False
        try:
            if getattr(src, "_target", 1) is None and hasattr(src, "set_target"):
                # кадр нужного размера декодирует VLC (а не 4K ради маленького окошка)
                dpr = float(p.device().devicePixelRatioF()) if p.device() is not None else 1.0
                k = math.hypot(p.transform().m11(), p.transform().m12()) or 1.0
                src.set_target(abs(w) * k * dpr, abs(h) * k * dpr)
            if abs(float(rate) - getattr(src, "_echo_rate", 1.0)) > 1e-3:
                src.set_rate(float(rate))
                src._echo_rate = float(rate)
        except Exception:                                  # noqa: BLE001
            pass
        img = src.img
        if img.isNull():
            return True
        self._blit(p, img, x, y, w, h, r, is_image=True)
        return True

    _PATHS: dict = {}

    def path(self, d, x=0, y=0, w=0, h=0):
        """Векторный контур в синтаксисе SVG (M L H V C S Q T A Z). w, h > 0 — вписать в прямоугольник
        с сохранением пропорций (по центру); иначе — как есть, со сдвигом x, y."""
        p = self.need()
        d = L._to_str(d)
        pp = self._PATHS.get(d)
        if pp is None:
            pp = svg_path(d)
            if len(self._PATHS) > 128:
                self._PATHS.clear()
            self._PATHS[d] = pp
        if pp.isEmpty():
            return False
        self._apply()
        w, h = float(w), float(h)
        if w > 0 and h > 0:
            br = pp.boundingRect()
            k = min(w / max(1e-6, br.width()), h / max(1e-6, br.height()))
            p.save()
            p.translate(float(x) + (w - br.width() * k) / 2, float(y) + (h - br.height() * k) / 2)
            p.scale(k, k)
            p.translate(-br.left(), -br.top())
            pen = p.pen()
            if pen.style() != Qt.PenStyle.NoPen:
                pen.setWidthF(pen.widthF() / k)          # толщина контура — в пикселях экрана, не масштабируется
                p.setPen(pen)
            p.drawPath(pp)
            p.restore()
        else:
            p.save()
            p.translate(float(x), float(y))
            p.drawPath(pp)
            p.restore()
        return True

    # ── градиенты (значения для fill/stroke) ──
    @staticmethod
    def linear(x1, y1, x2, y2, stops):
        return {"__grad__": "linear", "pts": (float(x1), float(y1), float(x2), float(y2)), "stops": list(stops)}

    @staticmethod
    def radial(cx, cy, r, stops):
        return {"__grad__": "radial", "pts": (float(cx), float(cy), float(r)), "stops": list(stops)}

    @staticmethod
    def conic(cx, cy, angle, stops):
        return {"__grad__": "conic", "pts": (float(cx), float(cy), float(angle)), "stops": list(stops)}

    def export(self) -> dict:
        names = ("fill nofill stroke nostroke alpha blend save restore translate rotate scale clip clear rect circle "
                 "ellipse line lines poly curve arc ring star ngon glow text textwidth image video path linear "
                 "radial conic").split()
        return {n: getattr(self, n) for n in names}


# ══════════════════════════════════════════════════════════════════════════ #
#  Стандартная библиотека (математика, списки, строки, физика)
# ══════════════════════════════════════════════════════════════════════════ #

def _perm(seed=1337):
    r = random.Random(seed)
    p = list(range(256))
    r.shuffle(p)
    return p + p


_P = _perm()


def _fade(t):
    return t * t * t * (t * (t * 6 - 15) + 10)


def _grad(h, x, y, z):
    h &= 15
    u = x if h < 8 else y
    v = y if h < 4 else (x if h in (12, 14) else z)
    return (u if (h & 1) == 0 else -u) + (v if (h & 2) == 0 else -v)


def noise(x, y=0.0, z=0.0):
    """Плавный шум Перлина, значения примерно -1..1."""
    x, y, z = float(x), float(y), float(z)
    X, Y, Z = int(math.floor(x)) & 255, int(math.floor(y)) & 255, int(math.floor(z)) & 255
    x -= math.floor(x)
    y -= math.floor(y)
    z -= math.floor(z)
    u, v, w = _fade(x), _fade(y), _fade(z)
    P = _P
    A = P[X] + Y
    AA, AB = P[A] + Z, P[A + 1] + Z
    B = P[X + 1] + Y
    BA, BB = P[B] + Z, P[B + 1] + Z

    def lerp(t, a, b):
        return a + t * (b - a)
    return lerp(w, lerp(v, lerp(u, _grad(P[AA], x, y, z), _grad(P[BA], x - 1, y, z)),
                        lerp(u, _grad(P[AB], x, y - 1, z), _grad(P[BB], x - 1, y - 1, z))),
                lerp(v, lerp(u, _grad(P[AA + 1], x, y, z - 1), _grad(P[BA + 1], x - 1, y, z - 1)),
                     lerp(u, _grad(P[AB + 1], x, y - 1, z - 1), _grad(P[BB + 1], x - 1, y - 1, z - 1))))


def _fmt_time(sec):
    sec = max(0, int(float(sec or 0)))
    return f"{sec // 60}:{sec % 60:02d}"


def stdlib(log_fn) -> dict:
    rnd = random.Random()

    def clamp(v, lo=0.0, hi=1.0):
        return lo if v < lo else hi if v > hi else v

    def lerp(a, b, t):
        return a + (b - a) * t

    def remap(v, a, b, c, d):
        return c + (d - c) * ((v - a) / (b - a) if b != a else 0.0)

    def smooth(t):
        t = clamp(t)
        return t * t * (3 - 2 * t)

    def approach(cur, target, rate, dt):
        """Плавное приближение (экспоненциальное сглаживание, не зависит от FPS)."""
        return target + (cur - target) * math.exp(-float(rate) * float(dt))

    def spring(pos, vel, target, k=120.0, damping=12.0, dt=1 / 60):
        """Пружина: возвращает [новая позиция, новая скорость]."""
        a = -float(k) * (pos - target) - float(damping) * vel
        vel = vel + a * dt
        return [pos + vel * dt, vel]

    def push(lst, *v):
        if not isinstance(lst, list):
            raise EchoError("push(): первый аргумент — список")
        lst.extend(v)
        return lst

    def pop(lst, i=-1):
        return lst.pop(int(i)) if lst else None

    def insert(lst, i, v):
        lst.insert(int(i), v)
        return lst

    def remove(lst, i):
        if 0 <= int(i) < len(lst):
            del lst[int(i)]
        return lst

    def slice_(v, a, b=None):
        return v[int(a):] if b is None else v[int(a):int(b)]

    def rng(a, b=None, step=1):
        if b is None:
            a, b = 0, a
        return list(range(int(a), int(b), int(step) or 1))

    def sort(lst, key=None, desc=False):
        if key is None:
            return sorted(lst, reverse=bool(desc))
        return sorted(lst, key=lambda x: key(x), reverse=bool(desc))

    def contains(c, v):
        return v in c

    def keys(d):
        return list(d.keys()) if isinstance(d, dict) else []

    def values(d):
        return list(d.values()) if isinstance(d, dict) else []

    def num(v, default=0):
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    def to_int(v, default=0):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return default

    def log(*a):
        log_fn(" ".join(L._to_str(x) for x in a))

    def sum_(lst, a=0, b=None):
        seg = lst[int(a):] if b is None else lst[int(a):int(b)]
        return float(sum(seg))

    def avg(lst, a=0, b=None):
        seg = lst[int(a):] if b is None else lst[int(a):int(b)]
        return float(sum(seg)) / len(seg) if seg else 0.0

    return {
        "PI": math.pi, "TAU": math.tau, "E": math.e,
        "sin": math.sin, "cos": math.cos, "tan": math.tan, "asin": math.asin, "acos": math.acos,
        "atan": math.atan, "atan2": math.atan2, "sqrt": lambda v: math.sqrt(max(0.0, v)), "abs": abs,
        "min": min, "max": max, "floor": math.floor, "ceil": math.ceil, "round": round, "pow": pow,
        "exp": math.exp, "log10": math.log10, "ln": lambda v: math.log(max(1e-12, v)),
        "sign": lambda v: (v > 0) - (v < 0), "hypot": math.hypot, "deg": math.degrees, "rad": math.radians,
        "clamp": clamp, "lerp": lerp, "remap": remap, "smooth": smooth, "approach": approach, "spring": spring,
        "noise": noise, "rand": lambda a=0.0, b=1.0: rnd.uniform(a, b), "randint": lambda a, b: rnd.randint(int(a), int(b)),
        "seed": lambda s: rnd.seed(s), "choice": lambda lst: rnd.choice(lst) if lst else None,
        "len": lambda v: len(v) if v is not None else 0, "str": L._to_str, "num": num, "int": to_int,
        "push": push, "pop": pop, "insert": insert, "remove": remove, "slice": slice_, "range": rng,
        "sort": sort, "reverse": lambda lst: list(reversed(lst)), "contains": contains, "keys": keys,
        "values": values, "sum": sum_, "avg": avg, "fill_list": lambda n, v=0: [v] * int(n),
        "join": lambda lst, sep="": str(sep).join(L._to_str(x) for x in lst),
        "split": lambda s, sep=" ": str(s).split(sep), "upper": lambda s: str(s).upper(),
        "lower": lambda s: str(s).lower(), "fmt_time": _fmt_time,
        "fmt": lambda v, digits=2: f"{float(v):.{int(digits)}f}",
        "rgb": rgb, "hsv": hsv, "mix": mix, "with_alpha": with_alpha, "hex": color_hex,
        "log": log, "print": log,
    }


# ══════════════════════════════════════════════════════════════════════════ #
#  Сцена
# ══════════════════════════════════════════════════════════════════════════ #

GEOM = ("x", "y", "w", "h")


def snapshot(bridge: PlayerBridge, analyzer: AudioAnalyzer, player: dict | None = None) -> dict:
    """Всё, что скрипт видит о плеере и звуке в этом кадре (считается один раз на кадр)."""
    return {"audio": analyzer.as_dict(), "track": bridge.track(), "player": player or bridge.player(),
            "playlist": bridge.playlist(), "lyrics": bridge.lyrics()}


def handles(inst, ev) -> bool:
    """Есть ли у экземпляра обработчик события (в классе или своим полем on_<событие>)."""
    return ev in inst.cls.handlers or inst.fields.get(EV_FIELD.get(ev, "")).__class__ is L.UserFn


HANDLER_EVENTS = ("init", "frame", "draw", "press", "release", "click", "move", "drag", "wheel", "beat",
                  "track", "key", "enter", "leave", "resize")


class Scene:
    def __init__(self, src: str, bridge: PlayerBridge, analyzer: AudioAnalyzer, log_fn=print):
        self.bridge, self.analyzer, self.log_fn = bridge, analyzer, log_fn
        self.errors: list[str] = []
        self.instances: list[L.Instance] = []
        self.theme = {"name": "Без названия", "bg": "#07070b", "accent": "#ff3d81", "accent2": "#3dd6ff",
                      "text": "#f4f4f8", "muted": "#8a8aa0", "font": "Segoe UI"}
        self.t0 = time.monotonic()
        self.time = 0.0
        self.frame_no = 0
        self.W = self.H = 1
        self._uid = 0
        self._track_key = None
        self.gfx = Gfx(self)
        self.program = L.compile_program(src)               # синтаксические ошибки — сразу исключением
        g = {}
        g.update(stdlib(self._log))
        g.update(self.gfx.export())
        g.update(bridge.actions())
        builtin_names = set(g)
        g.update({"audio": analyzer.as_dict(), "track": bridge.track(), "player": bridge.player(),
                  "playlist": bridge.playlist(), "lyrics": bridge.lyrics(), "time": 0.0, "dt": 0.0, "frame": 0,
                  "W": 1, "H": 1, "mouse": {"x": 0, "y": 0, "down": False}, "theme": self.theme})
        builtin_names |= {"audio", "track", "player", "playlist", "lyrics", "time", "dt", "frame", "W", "H",
                          "mouse", "theme"}
        g["__user__"] = set()
        self.g = g
        for cls in self.program.widgets.values():
            for fn in list(cls.methods.values()) + list(cls.handlers.values()):
                L.bind_globals(fn, g)
            unknown = [ev for ev in cls.handlers if ev not in HANDLER_EVENTS]
            if unknown:
                raise EchoError(f"виджет {cls.name}: неизвестное событие «{unknown[0]}» "
                                f"(есть: {', '.join(HANDLER_EVENTS)})", cls.line)
        L.BUDGET[0] = L.DEFAULT_BUDGET
        top = L.Frame(g, None, None, g)
        self.program.top(top)                               # верхний уровень: let/fn
        g["__user__"] = set(g) - builtin_names - {"__user__"}
        for tn in self.program.theme_nodes:
            L.BUDGET[0] = L.DEFAULT_BUDGET
            d = tn(top)
            if isinstance(d, dict):
                self.theme.update({k: v for k, v in d.items()})
        self._pending = []
        g["__add__"] = self._add
        for sb in self.program.scene_blocks:
            L.BUDGET[0] = L.DEFAULT_BUDGET
            sb(L.Frame({}, top, None, g))
        del g["__add__"]

    # ── журнал ──
    def _log(self, msg):
        try:
            self.log_fn(msg)
        except Exception:                                  # noqa: BLE001
            pass

    # ── создание экземпляров ──
    def _add(self, name, spec, line):
        cls = self.program.widgets.get(name)
        if cls is None:
            raise EchoError(f"нет виджета «{name}» (объявите: widget {name} {{ … }})", line)
        if not isinstance(spec, dict):
            spec = {}
        inst = L.Instance(cls, self._uid, line)
        self._uid += 1
        f = inst.fields
        f.update({"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0, "z": 0, "visible": True, "clip": True,
                  "name": spec.get("name", name), "hover": False, "pressed": False,
                  "cache": False, "cache_key": "", "resolution": 1.0})
        g = self.g
        fr = L.Frame({}, None, inst, g)
        L.BUDGET[0] = L.DEFAULT_BUDGET
        for pn, pe, _ln in cls.props:
            f[pn] = spec[pn] if pn in spec else pe(fr)
        for k, v in spec.items():
            if k not in f or k in GEOM or k in ("z", "visible", "clip", "name", "cache", "cache_key", "resolution"):
                f[k] = v
        f["_geom"] = tuple(float(f[k]) for k in GEOM)
        for sn, se, _ln in cls.states:
            f[sn] = se(fr)
        self.instances.append(inst)

    def adopt_state(self, old: "Scene"):
        """Горячая перезагрузка: состояние (state) переносится по совпадению класса и порядкового номера."""
        if old is None:
            return
        buckets = {}
        for inst in old.instances:
            buckets.setdefault(inst.cls.name, []).append(inst)
        used = {}
        for inst in self.instances:
            lst = buckets.get(inst.cls.name, [])
            k = used.get(inst.cls.name, 0)
            used[inst.cls.name] = k + 1
            if k < len(lst):
                prev = lst[k]
                for sn, _se, _ln in inst.cls.states:
                    if sn in prev.fields:
                        inst.fields[sn] = prev.fields[sn]
        self.t0 = old.t0
        self.frame_no = old.frame_no

    def start(self):
        for inst in self.instances:
            self._layout(inst)
            self._call(inst, "init", ())

    # ── геометрия ──
    def _layout(self, inst):
        gx, gy, gw, gh = inst.fields.get("_geom", (0, 0, 1, 1))

        def px(v, total):
            return v * total if abs(v) <= 1.0 else v
        f = inst.fields
        x = px(gx, self.W)
        y = px(gy, self.H)
        if x < 0:
            x += self.W
        if y < 0:
            y += self.H
        f["x"], f["y"], f["w"], f["h"] = x, y, max(1.0, px(gw, self.W)), max(1.0, px(gh, self.H))

    def resize(self, W, H):
        if (W, H) == (self.W, self.H):
            return
        self.W, self.H = W, H
        self.g["W"], self.g["H"] = W, H
        for inst in self.instances:
            self._layout(inst)
            self._call(inst, "resize", (inst.fields["w"], inst.fields["h"]))

    # ── вызов обработчиков ──
    def _call(self, inst, ev, args):
        if inst.error is not None:
            return None
        fn = inst.cls.handlers.get(ev)
        ex = inst.fields.get(EV_FIELD.get(ev, ""))           # событие экземпляра: add X { on_click: fn() { … } }
        if ex is not None and ex.__class__ is not L.UserFn:
            ex = None
        if fn is None and ex is None:
            return None
        try:
            res = None
            if fn is not None:
                # «on click { }» — можно не объявлять ненужные параметры
                res = L.run_handler(fn, inst, args[:len(fn.params)] if len(args) > len(fn.params) else args)
            if ex is not None:
                L.run_handler(ex, inst, args[:len(ex.params)] if len(args) > len(ex.params) else args)
            return res
        except EchoError as e:
            inst.error = e
            msg = f"{inst.cls.name}, событие {ev}: {e}"
            self.errors.append(msg)
            self._log("ОШИБКА " + msg)
        except Exception as e:                             # noqa: BLE001
            inst.error = EchoError(str(e))
            msg = f"{inst.cls.name}, событие {ev}: {e}"
            self.errors.append(msg)
            self._log("ОШИБКА " + msg)
        return None

    def has(self, inst, ev):
        return ev in inst.cls.handlers

    # ── кадр ──
    def frame(self, dt):
        b = self.bridge
        pl = b.player()
        self.analyzer.update(dt, pl["playing"])
        self.frame_shared(dt, snapshot(b, self.analyzer, pl))

    def frame_shared(self, dt, sh: dict):
        """Кадр по готовому снимку (snapshot): одна сцена или сразу много (слои конструктора тем)
        без повторного анализа звука и опроса плеера."""
        self.time = time.monotonic() - self.t0
        self.frame_no += 1
        g = self.g
        tr = sh["track"]
        g["audio"] = sh["audio"]
        g["track"] = tr
        g["player"] = sh["player"]
        g["playlist"] = sh["playlist"]
        g["lyrics"] = sh.get("lyrics") or g.get("lyrics")
        g["time"], g["dt"], g["frame"] = self.time, dt, self.frame_no
        if "mouse" in sh:
            g["mouse"] = sh["mouse"]
        key = tr["path"]
        changed = key != self._track_key
        self._track_key = key
        beat = sh["audio"]["beat"]
        power = sh["audio"]["beat_power"]
        for inst in self.instances:
            if inst.error is not None:
                continue
            if changed:
                self._call(inst, "track", (tr,))
            if beat:
                self._call(inst, "beat", (power,))
            self._call(inst, "frame", (dt,))

    gpu = False                                            # холст рисует видеокартой (ставит ScriptCanvas)

    def paint(self, p: QPainter):
        gfx = self.gfx
        gfx.p = p
        gpu = self.gpu
        try:
            for inst in sorted(self.instances, key=lambda i: i.fields.get("z", 0)):
                f = inst.fields
                if not f.get("visible", True) or inst.error is not None or "draw" not in inst.cls.handlers:
                    continue
                if f.get("cache"):
                    self._paint_cached(p, inst)
                    continue
                res = f.get("resolution", 1.0)
                if not gpu and isinstance(res, (int, float)) and 0.1 <= res < 0.99:
                    self._paint_lowres(p, inst, float(res))
                    continue
                p.save()
                p.translate(float(f["x"]), float(f["y"]))
                if f.get("clip", True):
                    p.setClipRect(QRectF(0, 0, float(f["w"]), float(f["h"])))
                p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                gfx.reset()
                self._call(inst, "draw", ())
                while gfx.stack:                            # забытые save()
                    p.restore()
                    gfx.stack.pop()
                p.restore()
        finally:
            gfx.p = None

    def _paint_cached(self, p, inst):
        """cache: true — статичный виджет (фон, рамки) запекается в картинку и дальше только копируется.
        Перерисовывается при смене размера или значения поля cache_key."""
        f = inst.fields
        w, h = max(1, int(f["w"])), max(1, int(f["h"]))
        key = (w, h, L._to_str(f.get("cache_key", "")), self.theme.get("bg"))
        cached = getattr(inst, "_cache", None) if hasattr(inst, "_cache") else None
        store = self.__dict__.setdefault("_wcache", {})
        ent = store.get(inst.uid)
        if ent is None or ent[0] != key:
            pm = QPixmap(w, h)
            pm.fill(Qt.GlobalColor.transparent)
            cp = QPainter(pm)
            cp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            gfx = self.gfx
            gfx.p = cp
            gfx.reset()
            self._call(inst, "draw", ())
            while gfx.stack:
                cp.restore()
                gfx.stack.pop()
            cp.end()
            gfx.p = p
            ent = (key, pm)
            store[inst.uid] = ent
        p.drawPixmap(int(f["x"]), int(f["y"]), ent[1])

    def _paint_lowres(self, p, inst, res):
        """resolution < 1: виджет рисуется в уменьшенную картинку и растягивается со сглаживанием —
        для светящихся визуализаторов почти незаметно, а рисование дешевле в 1/res² раз."""
        from PyQt6.QtGui import QImage
        f = inst.fields
        w, h = max(1, int(f["w"])), max(1, int(f["h"]))
        iw, ih = max(1, int(w * res)), max(1, int(h * res))
        store = self.__dict__.setdefault("_lowres", {})
        img = store.get(inst.uid)
        if img is None or img.width() != iw or img.height() != ih:
            img = QImage(iw, ih, QImage.Format.Format_ARGB32_Premultiplied)
            store[inst.uid] = img
        img.fill(0)
        cp = QPainter(img)
        cp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        cp.scale(iw / w, ih / h)
        gfx = self.gfx
        gfx.p = cp
        gfx.reset()
        self._call(inst, "draw", ())
        while gfx.stack:
            cp.restore()
            gfx.stack.pop()
        cp.end()
        gfx.p = p
        p.save()
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.drawImage(QRectF(float(f["x"]), float(f["y"]), float(w), float(h)), img)
        p.restore()

    # ── мышь и клавиатура ──
    def hit(self, x, y, events):
        for inst in sorted(self.instances, key=lambda i: -i.fields.get("z", 0)):
            f = inst.fields
            if not f.get("visible", True) or inst.error is not None:
                continue
            if not any(handles(inst, e) for e in events):
                continue
            if f["x"] <= x < f["x"] + f["w"] and f["y"] <= y < f["y"] + f["h"]:
                return inst
        return None

    def local(self, inst, x, y):
        return x - inst.fields["x"], y - inst.fields["y"]


# ══════════════════════════════════════════════════════════════════════════ #
#  Холст
# ══════════════════════════════════════════════════════════════════════════ #

# ══════════════════════════════════════════════════════════════════════════ #
#  Рисование видеокартой
# ══════════════════════════════════════════════════════════════════════════ #

_GPU = None
_SOFT_GL = ("gdi generic", "basic render", "llvmpipe", "swiftshader", "softpipe", "software")


def gpu_available() -> tuple[bool, str]:
    """(можно ли рисовать сцену через OpenGL, название видеокарты). Проверяется один раз.
    Программный OpenGL (без видеокарты) не считается — на нём CPU-рисование быстрее."""
    global _GPU
    if _GPU is not None:
        return _GPU
    import os
    if os.environ.get("ECHOES_DSL_GPU", "1") == "0" or os.environ.get("QT_QPA_PLATFORM", "") == "offscreen":
        _GPU = (False, "")
        return _GPU
    try:
        from PyQt6.QtGui import QOffscreenSurface, QOpenGLContext, QSurfaceFormat
        from PyQt6.QtOpenGL import QOpenGLVersionFunctionsFactory, QOpenGLVersionProfile
        surf = QOffscreenSurface()
        surf.create()
        ctx = QOpenGLContext()
        if not ctx.create() or not ctx.makeCurrent(surf):
            _GPU = (False, "")
            return _GPU
        vp = QOpenGLVersionProfile()
        vp.setVersion(2, 0)
        f = QOpenGLVersionFunctionsFactory.get(vp, ctx)
        name = str(f.glGetString(0x1F01) or "") if f is not None else ""
        ver = ctx.format().majorVersion()
        ctx.doneCurrent()
        ok = ver >= 2 and bool(name) and not any(k in name.lower() for k in _SOFT_GL)
        _GPU = (ok, name)
    except Exception as e:                                 # noqa: BLE001
        print("[echoscript] OpenGL недоступен:", e)
        _GPU = (False, "")
    return _GPU


class _GLOffscreen:
    """Рисование сцены видеокартой в невидимый буфер (FBO, сглаживание 4×) и копия готовой картинки
    в обычный виджет. В само окно OpenGL не встраивается: иначе Windows переводит всё окно плеера
    на вывод через OpenGL (пересоздание окна, мигание, чёрный экран при разворачивании на весь экран)."""

    def __init__(self):
        from PyQt6.QtGui import QOffscreenSurface, QOpenGLContext
        self.surf = QOffscreenSurface()
        self.surf.create()
        self.ctx = QOpenGLContext()
        self.failed = not self.ctx.create() or not self.ctx.makeCurrent(self.surf)
        self.fbo = None
        self.size = None

    def render(self, canvas):
        """→ QImage кадра или None (тогда холст нарисует сам, процессором)."""
        from PyQt6.QtOpenGL import (QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat,
                                    QOpenGLPaintDevice)
        if self.failed or not self.ctx.makeCurrent(self.surf):
            self.failed = True
            return None
        dpr = max(1.0, float(canvas.devicePixelRatioF()))
        W, H = max(1, int(canvas.width() * dpr + 0.5)), max(1, int(canvas.height() * dpr + 0.5))
        if self.fbo is None or self.size != (W, H):
            ff = QOpenGLFramebufferObjectFormat()
            ff.setSamples(4)
            ff.setAttachment(QOpenGLFramebufferObject.Attachment.CombinedDepthStencil)
            self.fbo = QOpenGLFramebufferObject(W, H, ff)
            self.size = (W, H)
            if not self.fbo.isValid():
                self.failed = True
                return None
        self.fbo.bind()
        dev = QOpenGLPaintDevice(W, H)
        dev.setDevicePixelRatio(dpr)
        p = QPainter(dev)
        try:
            canvas._render(p)
        finally:
            p.end()
            self.fbo.release()
        img = self.fbo.toImage(True)                       # сглаживание + чтение в память (~3–5 мс);
        # True — перевернуть: у OpenGL строки идут снизу вверх, без этого сцена вверх ногами
        img.setDevicePixelRatio(dpr)
        return img

    def close(self):
        try:
            if self.ctx.makeCurrent(self.surf):
                self.fbo = None                            # буфер освобождается при текущем контексте
                self.ctx.doneCurrent()
        except Exception:                                  # noqa: BLE001
            pass


class ScriptCanvas(QWidget):
    """Живая сцена EchoScript (60 кадров/с), ввод мышью передаётся виджетам скрипта."""
    errors_changed = pyqtSignal(list)
    log_line = pyqtSignal(str)
    # режим расстановки: (строка add, класс, {x, y, w, h} в долях окна)
    geometry_committed = pyqtSignal(list)                    # [(строка add, класс, {x, y, w, h})]
    selection_changed = pyqtSignal(int, str)                 # 0, "" — ничего не выбрано
    delete_requested = pyqtSignal(list)                      # [(строка add, класс)]
    undo_requested = pyqtSignal(bool)                        # True — повторить
    block_dropped = pyqtSignal(str, float, float)            # блок перетащили из конструктора: ключ, x, y (пиксели)
    context_requested = pyqtSignal(object)                   # правый клик в расстановке: QPoint на экране

    def __init__(self, win=None, parent=None):
        super().__init__(parent)
        self.win = win
        self.bridge = PlayerBridge(win)
        self.analyzer = AudioAnalyzer(getattr(win, "engine", None))
        self.scene: Scene | None = None
        self.load_error: str | None = None
        self._last = time.monotonic()
        self._press = None
        self._hover = None
        self._drag_last = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        try:
            from frameclock import FrameTimer
            self.timer = FrameTimer(self)
        except Exception:                                  # noqa: BLE001
            self.timer = QTimer(self)
            self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self._tick)
        self.timer.start(16)
        self.edit_mode = False
        self._sel = []                                     # [(класс, номер среди экземпляров класса)]
        self._ed = None                                    # текущее перетаскивание
        self._sweep_n = 0
        self._gl = None                                    # поверхность OpenGL (None — рисуем на CPU)
        self._ghost = None                                 # (ключ блока, x, y) — блок тащат над сценой
        self.setAcceptDrops(True)
        self._frame_ms = 0.0                               # среднее время кадра (логика + рисование), мс
        self._paint_ms = 0.0
        self._fps = 0.0
        self._fps_n = 0
        self._fps_t = time.monotonic()
        want = True
        try:
            want = bool((getattr(win, "settings", None) or {}).get("dsl_gpu", True))
        except Exception:                                  # noqa: BLE001
            pass
        self.set_gpu(want)

    # ── GPU ──
    @property
    def gpu(self) -> bool:
        return self._gl is not None

    def set_gpu(self, on: bool) -> bool:
        """Включить/выключить рисование видеокартой. Возвращает, включено ли оно на самом деле."""
        if on and self._gl is None and gpu_available()[0]:
            try:
                self._gl = _GLOffscreen()
                if self._gl.failed:
                    self._gl = None
            except Exception as e:                         # noqa: BLE001
                print("[echoscript] GPU:", e)
                self._gl = None
        elif not on and self._gl is not None:
            self._gl.close()
            self._gl = None
        if self.scene is not None:
            self.scene.gpu = self._gl is not None
        self.update()
        return self._gl is not None

    def stats(self) -> dict:
        return {"gpu": self.gpu, "fps": self._fps, "frame_ms": self._frame_ms, "paint_ms": self._paint_ms,
                "card": gpu_available()[1] if self.gpu else ""}

    def closeEvent(self, e):
        if self._gl is not None:
            self._gl.close()
        super().closeEvent(e)

    # ── загрузка / горячая перезагрузка ──
    def load_source(self, src: str, keep_state=True) -> str | None:
        """Возвращает текст ошибки или None. При ошибке остаётся работать прежняя сцена."""
        try:
            sc = Scene(src, self.bridge, self.analyzer, log_fn=lambda m: self.log_line.emit(m))
            sc.gpu = self._gl is not None
            sc.resize(max(1, self.width()), max(1, self.height()))
            if keep_state:
                sc.adopt_state(self.scene)
            sc.start()
        except EchoError as e:
            self.load_error = str(e)
            self.errors_changed.emit([self.load_error])
            self.update()
            return self.load_error
        except Exception as e:                             # noqa: BLE001
            self.load_error = f"ошибка: {e}"
            self.errors_changed.emit([self.load_error])
            self.update()
            return self.load_error
        self.scene = sc
        self.load_error = None
        self.errors_changed.emit(list(sc.errors))
        if self._sel:
            keep = [k for k in self._sel if self._by_key(k) is not None]
            if keep != self._sel:
                self._sel = keep
                self._emit_sel()
        self.update()
        return None

    def theme(self):
        return self.scene.theme if self.scene else {}

    # ── цикл ──
    def _tick(self):
        if not self.isVisible():
            return
        now = time.monotonic()
        dt = min(0.1, now - self._last)
        self._last = now
        sc = self.scene
        if sc is None:
            return
        self._sweep_n += 1
        if self._sweep_n % 90 == 0:
            MEDIA.sweep()
        if self._gl is not None and self._gl.failed:
            self.set_gpu(False)                            # видеокарта не дала контекст — на CPU
        n = len(sc.errors)
        t0 = time.perf_counter()
        sc.resize(max(1, self.width()), max(1, self.height()))
        sc.frame(dt)
        logic = (time.perf_counter() - t0) * 1000
        self._frame_ms += (logic + self._paint_ms - self._frame_ms) * 0.1
        self._fps_n += 1
        if now - self._fps_t >= 0.5:
            self._fps = self._fps_n / (now - self._fps_t)
            self._fps_n = 0
            self._fps_t = now
        if len(sc.errors) != n:
            self.errors_changed.emit(list(sc.errors))
        self.update()

    def paintEvent(self, _):
        if self._gl is not None:
            t0 = time.perf_counter()
            img = self._gl.render(self)
            if img is not None:
                p = QPainter(self)
                p.drawImage(0, 0, img)
                p.end()
                self._paint_ms += ((time.perf_counter() - t0) * 1000 - self._paint_ms) * 0.1
                return
            self.set_gpu(False)                            # видеокарта отказала — дальше процессором
        p = QPainter(self)
        try:
            self._render(p)
        finally:
            p.end()

    def _render(self, p):
        t0 = time.perf_counter()
        sc = self.scene
        bg = (sc.theme.get("bg") if sc else None) or "#07070b"
        try:
            p.fillRect(self.rect(), to_qcolor(bg))
        except EchoError:
            p.fillRect(self.rect(), QColor("#07070b"))
        if sc is not None:
            n = len(sc.errors)
            sc.paint(p)
            if len(sc.errors) != n:
                self.errors_changed.emit(list(sc.errors))
            if self.edit_mode:
                self._paint_edit(p)
        msg = self.load_error or (sc.errors[-1] if sc and sc.errors else None)
        if msg:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            f = QFont("Segoe UI")
            f.setPixelSize(12)
            f.setWeight(QFont.Weight.DemiBold)
            p.setFont(f)
            fm = QFontMetricsF(f)
            txt = fm.elidedText("Ошибка: " + msg, Qt.TextElideMode.ElideRight, self.width() - 60)
            r = QRectF(14, self.height() - 40, fm.horizontalAdvance(txt) + 24, 28)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(170, 30, 40, 230))
            p.drawRoundedRect(r, 10, 10)
            p.setPen(QColor("#ffffff"))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, txt)
        if self._gl is None:
            self._paint_ms += ((time.perf_counter() - t0) * 1000 - self._paint_ms) * 0.1

    # ══ режим расстановки ══
    HANDLE = 7

    def set_edit_mode(self, on: bool):
        self.edit_mode = bool(on)
        self._ed = None
        if not on and self._sel:
            self._sel = []
            self.selection_changed.emit(0, "")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus if on else Qt.FocusPolicy.ClickFocus)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()

    def _ordinal(self, inst):
        k = 0
        for i in self.scene.instances:
            if i is inst:
                return k
            if i.cls.name == inst.cls.name:
                k += 1
        return k

    def _by_key(self, key):
        cls, k = key
        n = 0
        for i in self.scene.instances:
            if i.cls.name == cls:
                if n == k:
                    return i
                n += 1
        return None

    def selected_all(self) -> list:
        if not self._sel or self.scene is None:
            return []
        out = [self._by_key(k) for k in self._sel]
        return [i for i in out if i is not None]

    def selected(self):
        """Главный выбранный виджет (последний щёлкнутый)."""
        s = self.selected_all()
        return s[-1] if s else None

    def _emit_sel(self):
        p = self.selected()
        self.selection_changed.emit(p.line if p is not None else 0, p.cls.name if p is not None else "")
        self.update()

    def _group_of(self, inst):
        g = inst.fields.get("group") if inst is not None else None
        if not g:
            return [inst] if inst is not None else []
        return [i for i in self.scene.instances if i.fields.get("group") == g]

    def select(self, inst, add=False):
        """inst=None — снять выделение; add — Ctrl: добавить/убрать из выделения. Группа выделяется целиком."""
        if inst is None:
            if self._sel:
                self._sel = []
                self._emit_sel()
            return
        members = self._group_of(inst)
        keys = [(m.cls.name, self._ordinal(m)) for m in members]
        main = (inst.cls.name, self._ordinal(inst))
        if add:
            if main in self._sel:
                self._sel = [k for k in self._sel if k not in keys]
            else:
                self._sel = [k for k in self._sel if k not in keys] + [k for k in keys if k != main] + [main]
        else:
            self._sel = [k for k in keys if k != main] + [main]
        self._emit_sel()

    def select_many(self, insts):
        self._sel = []
        for inst in insts:
            for m in self._group_of(inst):
                k = (m.cls.name, self._ordinal(m))
                if k not in self._sel:
                    self._sel.append(k)
        self._emit_sel()

    def editable(self, inst) -> bool:
        """Двигать можно экземпляр, созданный отдельной строкой add (не в цикле)."""
        if inst is None or inst.line <= 0:
            return False
        return sum(1 for i in self.scene.instances if i.line == inst.line) == 1

    def _rect(self, inst):
        f = inst.fields
        return QRectF(float(f["x"]), float(f["y"]), float(f["w"]), float(f["h"]))

    def _handles(self, r):
        cx, cy = r.center().x(), r.center().y()
        return {"nw": QPointF(r.left(), r.top()), "n": QPointF(cx, r.top()), "ne": QPointF(r.right(), r.top()),
                "e": QPointF(r.right(), cy), "se": QPointF(r.right(), r.bottom()), "s": QPointF(cx, r.bottom()),
                "sw": QPointF(r.left(), r.bottom()), "w": QPointF(r.left(), cy)}

    def _edit_hit(self, x, y):
        sc = self.scene
        sel = self.selected_all()
        if len(sel) == 1 and self.editable(sel[0]):
            for k, pt in self._handles(self._rect(sel[0])).items():
                if abs(pt.x() - x) <= self.HANDLE + 2 and abs(pt.y() - y) <= self.HANDLE + 2:
                    return sel[0], k
        best = None
        for inst in sorted(sc.instances, key=lambda i: -i.fields.get("z", 0)):
            f = inst.fields
            if not f.get("visible", True):
                continue
            if self._rect(inst).contains(QPointF(x, y)):
                # среди перекрывающихся на одном слое — самый маленький (фон не мешает выбирать)
                if best is None or (inst.fields.get("z", 0) == best.fields.get("z", 0) and
                                    f["w"] * f["h"] < best.fields["w"] * best.fields["h"]):
                    best = inst
                elif inst.fields.get("z", 0) < best.fields.get("z", 0):
                    break
        return best, "move"

    def _paint_edit(self, p):
        sc = self.scene
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        p.setPen(QPen(QColor(255, 255, 255, 14), 1))
        for i in range(1, 20):
            x = self.width() * i / 20
            y = self.height() * i / 20
            p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
            p.drawLine(QPointF(0, y), QPointF(self.width(), y))
        p.setPen(QPen(QColor(255, 255, 255, 40), 1, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(self.width() / 2, 0), QPointF(self.width() / 2, self.height()))
        p.drawLine(QPointF(0, self.height() / 2), QPointF(self.width(), self.height() / 2))
        sel = self.selected_all()
        main = sel[-1] if sel else None
        f = QFont("Segoe UI")
        f.setPixelSize(11)
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        fm = QFontMetricsF(f)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        for inst in sc.instances:
            if not inst.fields.get("visible", True):
                continue
            r = self._rect(inst)
            is_sel = inst in sel
            ed = self.editable(inst)
            col = QColor("#ffb347") if is_sel else (QColor(255, 255, 255, 90) if ed else QColor(255, 255, 255, 45))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(col, 2 if is_sel else 1, Qt.PenStyle.SolidLine if is_sel else Qt.PenStyle.DashLine))
            p.drawRect(r.adjusted(0.5, 0.5, -0.5, -0.5))
            g = inst.fields.get("group")
            label = inst.cls.name + (f"  [{g}]" if g else "") + ("" if ed else "  (в цикле)")
            tw = fm.horizontalAdvance(label) + 12
            lr = QRectF(r.left(), max(0.0, r.top() - 18), tw, 17)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 179, 71, 230) if is_sel else QColor(20, 18, 32, 200))
            p.drawRoundedRect(lr, 4, 4)
            p.setPen(QColor("#1a1408") if is_sel else QColor(255, 255, 255, 200))
            p.drawText(lr, Qt.AlignmentFlag.AlignCenter, label)
        if len(sel) > 1:
            br = self._rect(sel[0])
            for s in sel[1:]:
                br = br.united(self._rect(s))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(255, 179, 71, 160), 1, Qt.PenStyle.DashDotLine))
            p.drawRect(br.adjusted(-4, -4, 4, 4))
        elif main is not None and self.editable(main):
            p.setPen(QPen(QColor("#1a1408"), 1))
            p.setBrush(QColor("#ffb347"))
            for pt in self._handles(self._rect(main)).values():
                p.drawRect(QRectF(pt.x() - self.HANDLE / 2, pt.y() - self.HANDLE / 2, self.HANDLE, self.HANDLE))
        ed = self._ed
        if ed is not None and ed.get("mode") == "band" and ed.get("moved"):
            r = QRectF(QPointF(ed["x0"], ed["y0"]), QPointF(ed["x1"], ed["y1"])).normalized()
            p.setPen(QPen(QColor(255, 179, 71, 200), 1, Qt.PenStyle.DashLine))
            p.setBrush(QColor(255, 179, 71, 30))
            p.drawRect(r)
        if self._ghost is not None:
            try:
                import dsl_layout as _DL
                b = _DL.BLOCKS.get(self._ghost[0])
            except Exception:                              # noqa: BLE001
                b = None
            if b is not None:
                gw, gh = b["geom"][2], b["geom"][3]
                gw = min(gw, 0.5) * self.width()
                gh = min(gh, 0.5) * self.height()
                r = QRectF(self._ghost[1] - gw / 2, self._ghost[2] - gh / 2, gw, gh)
                p.setPen(QPen(QColor("#ffb347"), 2, Qt.PenStyle.DashLine))
                p.setBrush(QColor(255, 179, 71, 40))
                p.drawRoundedRect(r, 8, 8)
                p.setPen(QColor("#ffb347"))
                p.drawText(r, Qt.AlignmentFlag.AlignCenter, "＋ " + b["title"])
        if main is None and self._ghost is None:
            txt = "Перетащите блок из конструктора на сцену  ·  щёлкните виджет, чтобы настроить его  ·  " \
                  "правая кнопка — меню"
            tw = fm.horizontalAdvance(txt) + 28
            r = QRectF((self.width() - tw) / 2, self.height() - 46, tw, 28)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(20, 18, 32, 220))
            p.drawRoundedRect(r, 14, 14)
            p.setPen(QColor("#ece9f6"))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, txt)
        if main is not None:
            if len(sel) > 1:
                txt = f"Выбрано: {len(sel)}  ·  Ctrl+клик — добавить/убрать  ·  стрелки — сдвиг"
            else:
                gg = self._frac(main)
                txt = f"{main.cls.name}   x {gg['x']:.3f}  y {gg['y']:.3f}  w {gg['w']:.3f}  h {gg['h']:.3f}"
                if not self.editable(main):
                    txt += "   — создан в цикле, двигается только в коде"
            tw = fm.horizontalAdvance(txt) + 20
            r = QRectF(14, 12, tw, 24)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(20, 18, 32, 220))
            p.drawRoundedRect(r, 8, 8)
            p.setPen(QColor("#ece9f6"))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, txt)
        p.restore()

    def _is_backdrop(self, inst) -> bool:
        """Виджет почти на весь холст (фон, видеофон)."""
        f = inst.fields
        return f["w"] * f["h"] >= 0.9 * max(1, self.width()) * max(1, self.height())

    def _frac(self, inst):
        W, H = max(1, self.width()), max(1, self.height())
        f = inst.fields
        return {"x": f["x"] / W, "y": f["y"] / H, "w": f["w"] / W, "h": f["h"] / H}

    def _snap(self, moving, r: QRectF, mode, free):
        """Прилипание к сетке 1% и к краям/центрам других виджетов и окна (Alt — без прилипания)."""
        if free:
            return r
        W, H = float(self.width()), float(self.height())
        tol = 6.0
        xs = [0.0, W / 2, W]
        ys = [0.0, H / 2, H]
        for o in self.scene.instances:
            if o in moving or not o.fields.get("visible", True):
                continue
            q = self._rect(o)
            xs += [q.left(), q.center().x(), q.right()]
            ys += [q.top(), q.center().y(), q.bottom()]

        def best(vals, targets):
            bd, bv = tol + 1, None
            for v in vals:
                for t in targets:
                    if abs(v - t) < bd:
                        bd, bv = abs(v - t), t - v
            return bv
        if mode == "move":
            dx = best([r.left(), r.center().x(), r.right()], xs)
            dy = best([r.top(), r.center().y(), r.bottom()], ys)
            dx = dx if dx is not None else round(r.left() / (W / 100)) * (W / 100) - r.left()
            dy = dy if dy is not None else round(r.top() / (H / 100)) * (H / 100) - r.top()
            return r.translated(dx, dy)
        l, t, rr, b = r.left(), r.top(), r.right(), r.bottom()
        if "w" in mode:
            d = best([l], xs)
            l = l + d if d is not None else round(l / (W / 100)) * (W / 100)
        if "e" in mode:
            d = best([rr], xs)
            rr = rr + d if d is not None else round(rr / (W / 100)) * (W / 100)
        if "n" in mode:
            d = best([t], ys)
            t = t + d if d is not None else round(t / (H / 100)) * (H / 100)
        if "s" in mode:
            d = best([b], ys)
            b = b + d if d is not None else round(b / (H / 100)) * (H / 100)
        return QRectF(QPointF(l, t), QPointF(rr, b))

    def _apply_rect(self, inst, r: QRectF):
        W, H = max(1, self.width()), max(1, self.height())
        x = min(max(r.left() / W, 0.0), 0.995)
        y = min(max(r.top() / H, 0.0), 0.995)
        w = min(max(r.width() / W, 0.005), 1.0)
        h = min(max(r.height() / H, 0.005), 1.0)
        inst.fields["_geom"] = (x, y, w, h)
        self.scene._layout(inst)
        self.scene._call(inst, "resize", (inst.fields["w"], inst.fields["h"]))
        return {"x": x, "y": y, "w": w, "h": h}

    def set_rects(self, items):
        """[(inst, QRectF)] → применить и отправить в код одним пакетом (для выравнивания)."""
        out = []
        for inst, r in items:
            if self.editable(inst):
                out.append((inst.line, inst.cls.name, self._apply_rect(inst, r)))
        if out:
            self.geometry_committed.emit(out)
        self.update()

    def _edit_press(self, e):
        x, y = e.position().x(), e.position().y()
        ctrl = bool(e.modifiers() & Qt.KeyboardModifier.ControlModifier)
        inst, mode = self._edit_hit(x, y)
        if e.button() != Qt.MouseButton.LeftButton:
            if inst is not None and inst not in self.selected_all():
                self.select(inst)
            self._ed = None
            if e.button() == Qt.MouseButton.RightButton and inst is not None:
                self.context_requested.emit(e.globalPosition().toPoint())
            return
        sel = self.selected_all()
        if inst is not None and mode == "move" and self._is_backdrop(inst) and inst not in sel and not ctrl:
            # фон на весь экран не мешает: тянуть по нему — рамка выделения, клик без движения — выбрать фон
            self._ed = {"mode": "band", "x0": x, "y0": y, "x1": x, "y1": y, "moved": False, "add": False,
                        "click": inst}
            return
        if inst is None:
            if not ctrl:
                self.select(None)
            self._ed = {"mode": "band", "x0": x, "y0": y, "x1": x, "y1": y, "moved": False, "add": ctrl}
            return
        if ctrl:
            self.select(inst, add=True)
            self._ed = None
            return
        if mode == "move" and inst in sel and len(sel) > 1:
            pass                                            # тянем всё выделение
        elif mode == "move" or inst not in sel:
            self.select(inst)
        movers = [i for i in self.selected_all() if self.editable(i)] if mode == "move" else [inst]
        if not movers or (mode != "move" and not self.editable(inst)):
            self._ed = None
            return
        self._ed = {"mode": mode, "x0": x, "y0": y, "moved": False, "inst": inst,
                    "items": [(i, self._rect(i)) for i in movers]}

    def _edit_move(self, e):
        x, y = e.position().x(), e.position().y()
        ed = self._ed
        if ed is None:
            inst, mode = self._edit_hit(x, y)
            cur = {"nw": Qt.CursorShape.SizeFDiagCursor, "se": Qt.CursorShape.SizeFDiagCursor,
                   "ne": Qt.CursorShape.SizeBDiagCursor, "sw": Qt.CursorShape.SizeBDiagCursor,
                   "n": Qt.CursorShape.SizeVerCursor, "s": Qt.CursorShape.SizeVerCursor,
                   "e": Qt.CursorShape.SizeHorCursor, "w": Qt.CursorShape.SizeHorCursor}.get(mode)
            if cur is None:
                cur = Qt.CursorShape.SizeAllCursor if inst is not None and self.editable(inst) else \
                    Qt.CursorShape.ArrowCursor
            self.setCursor(cur)
            return
        dx, dy = x - ed["x0"], y - ed["y0"]
        if not ed["moved"] and abs(dx) < 3 and abs(dy) < 3:
            return
        ed["moved"] = True
        mode = ed["mode"]
        if mode == "band":
            ed["x1"], ed["y1"] = x, y
            self.update()
            return
        free = bool(e.modifiers() & Qt.KeyboardModifier.AltModifier)
        if mode == "move":
            items = ed["items"]
            br = QRectF(items[0][1])
            for _i, r0 in items[1:]:
                br = br.united(r0)
            moved = br.translated(dx, dy)
            snapped = self._snap([i for i, _ in items], moved, "move", free)
            sx, sy = snapped.left() - br.left(), snapped.top() - br.top()
            ed["geoms"] = [(i.line, i.cls.name, self._apply_rect(i, r0.translated(sx, sy))) for i, r0 in items]
        else:
            inst, r0 = ed["items"][0]
            r = QRectF(r0)
            if "w" in mode:
                r.setLeft(min(r.left() + dx, r.right() - 8))
            if "e" in mode:
                r.setRight(max(r.right() + dx, r.left() + 8))
            if "n" in mode:
                r.setTop(min(r.top() + dy, r.bottom() - 8))
            if "s" in mode:
                r.setBottom(max(r.bottom() + dy, r.top() + 8))
            if e.modifiers() & Qt.KeyboardModifier.ShiftModifier and len(mode) == 2 and r0.height() > 0:
                k = r0.width() / r0.height()                  # Shift — сохранить пропорции
                r.setHeight(r.width() / k) if "s" in mode else r.setTop(r.bottom() - r.width() / k)
            r = self._snap([inst], r, mode, free)
            ed["geoms"] = [(inst.line, inst.cls.name, self._apply_rect(inst, r))]
        self.update()

    def _edit_release(self, e):
        ed, self._ed = self._ed, None
        if ed is None:
            return
        if ed["mode"] == "band":
            if not ed["moved"] and ed.get("click") is not None:
                self.select(ed["click"])
            if ed["moved"]:
                band = QRectF(QPointF(ed["x0"], ed["y0"]), QPointF(ed["x1"], ed["y1"])).normalized()
                hit = [i for i in self.scene.instances
                       if i.fields.get("visible", True) and band.intersects(self._rect(i))
                       and not self._is_backdrop(i) and not self._rect(i).contains(band)]
                if ed["add"]:
                    hit = self.selected_all() + [i for i in hit if i not in self.selected_all()]
                self.select_many(hit)
            self.update()
            return
        if ed["moved"] and ed.get("geoms"):
            self.geometry_committed.emit(ed["geoms"])

    def _edit_key(self, e):
        k = e.key()
        mods = e.modifiers()
        if mods & Qt.KeyboardModifier.ControlModifier:
            if k == Qt.Key.Key_Z:
                self.undo_requested.emit(bool(mods & Qt.KeyboardModifier.ShiftModifier))
                return True
            if k == Qt.Key.Key_Y:
                self.undo_requested.emit(True)
                return True
            if k == Qt.Key.Key_A:
                self.select_many([i for i in self.scene.instances if i.fields.get("visible", True)])
                return True
        if k == Qt.Key.Key_Escape:
            self.select(None)
            return True
        sel = [i for i in self.selected_all() if self.editable(i)]
        if not sel:
            return False
        if k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_requested.emit([(i.line, i.cls.name) for i in sel])
            return True
        step = 0.02 if mods & Qt.KeyboardModifier.ShiftModifier else 0.0025
        d = {Qt.Key.Key_Left: (-step, 0), Qt.Key.Key_Right: (step, 0),
             Qt.Key.Key_Up: (0, -step), Qt.Key.Key_Down: (0, step)}.get(k)
        if d is not None:
            self.set_rects([(i, self._rect(i).translated(d[0] * self.width(), d[1] * self.height())) for i in sel])
            return True
        return False

    # ── ввод ──
    # ── блоки, перетаскиваемые из конструктора ──
    def _drag_key(self, e):
        md = e.mimeData()
        if md is not None and md.hasFormat("application/x-echo-block"):
            return bytes(md.data("application/x-echo-block")).decode("utf-8", "ignore")
        return None

    def dragEnterEvent(self, e):
        key = self._drag_key(e)
        if key:
            e.acceptProposedAction()
            p = e.position()
            self._ghost = (key, p.x(), p.y())
            self.update()

    def dragMoveEvent(self, e):
        key = self._drag_key(e)
        if key:
            e.acceptProposedAction()
            p = e.position()
            self._ghost = (key, p.x(), p.y())
            self.update()

    def dragLeaveEvent(self, e):
        self._ghost = None
        self.update()

    def dropEvent(self, e):
        key = self._drag_key(e)
        self._ghost = None
        if key:
            e.acceptProposedAction()
            p = e.position()
            self.block_dropped.emit(key, p.x(), p.y())
        self.update()

    def mouseDoubleClickEvent(self, e):
        # быстрый второй щелчок в расстановке — обычное нажатие (иначе выделение «теряет» клик)
        self.mousePressEvent(e)

    def mousePressEvent(self, e):
        sc = self.scene
        if sc is None:
            return
        if self.edit_mode:
            self.setFocus()
            self._edit_press(e)
            return
        x, y = e.position().x(), e.position().y()
        sc.g["mouse"] = {"x": x, "y": y, "down": True}
        inst = sc.hit(x, y, ("press", "click", "drag", "release"))
        self._press = (inst, x, y, e.button())
        self._drag_last = (x, y)
        if inst is not None:
            inst.fields["pressed"] = True
            lx, ly = sc.local(inst, x, y)
            btn = {Qt.MouseButton.LeftButton: 1, Qt.MouseButton.RightButton: 2}.get(e.button(), 3)
            sc._call(inst, "press", (lx, ly, btn))

    def mouseMoveEvent(self, e):
        sc = self.scene
        if sc is None:
            return
        if self.edit_mode:
            self._edit_move(e)
            return
        x, y = e.position().x(), e.position().y()
        sc.g["mouse"] = {"x": x, "y": y, "down": bool(e.buttons())}
        if self._press is not None and self._press[0] is not None and e.buttons():
            inst = self._press[0]
            lx, ly = sc.local(inst, x, y)
            px, py = self._drag_last
            sc._call(inst, "drag", (lx, ly, x - px, y - py))
            self._drag_last = (x, y)
            return
        inst = sc.hit(x, y, ("move", "enter", "leave", "click", "press", "wheel", "drag"))
        if inst is not self._hover:
            if self._hover is not None:
                self._hover.fields["hover"] = False
                sc._call(self._hover, "leave", ())
            self._hover = inst
            if inst is not None:
                inst.fields["hover"] = True
                sc._call(inst, "enter", ())
        self.setCursor(Qt.CursorShape.PointingHandCursor if inst is not None and
                       any(handles(inst, ev) for ev in ("click", "press", "drag"))
                       else Qt.CursorShape.ArrowCursor)
        if inst is not None:
            lx, ly = sc.local(inst, x, y)
            sc._call(inst, "move", (lx, ly))

    def mouseReleaseEvent(self, e):
        sc = self.scene
        if sc is not None and self.edit_mode:
            self._edit_release(e)
            return
        if sc is None or self._press is None:
            return
        inst, x0, y0, btn0 = self._press
        self._press = None
        x, y = e.position().x(), e.position().y()
        sc.g["mouse"] = {"x": x, "y": y, "down": False}
        if inst is None:
            return
        inst.fields["pressed"] = False
        lx, ly = sc.local(inst, x, y)
        btn = {Qt.MouseButton.LeftButton: 1, Qt.MouseButton.RightButton: 2}.get(btn0, 3)
        sc._call(inst, "release", (lx, ly, btn))
        if abs(x - x0) < 6 and abs(y - y0) < 6:
            sc._call(inst, "click", (lx, ly, btn))

    def wheelEvent(self, e):
        sc = self.scene
        if sc is None or self.edit_mode:
            return
        x, y = e.position().x(), e.position().y()
        inst = sc.hit(x, y, ("wheel",))
        if inst is not None:
            lx, ly = sc.local(inst, x, y)
            sc._call(inst, "wheel", (e.angleDelta().y() / 120.0, lx, ly))

    def leaveEvent(self, e):
        if self.scene is not None and self._hover is not None:
            self._hover.fields["hover"] = False
            self.scene._call(self._hover, "leave", ())
        self._hover = None

    def keyPressEvent(self, e):
        sc = self.scene
        if sc is None:
            return
        if self.edit_mode:
            if not self._edit_key(e):
                super().keyPressEvent(e)
            return
        name = e.text() or {Qt.Key.Key_Space: "space", Qt.Key.Key_Left: "left", Qt.Key.Key_Right: "right",
                            Qt.Key.Key_Up: "up", Qt.Key.Key_Down: "down", Qt.Key.Key_Escape: "escape",
                            Qt.Key.Key_Return: "enter"}.get(e.key(), "")
        if e.key() == Qt.Key.Key_Space:
            name = "space"
        for inst in sc.instances:
            if "key" in inst.cls.handlers:
                sc._call(inst, "key", (name,))
