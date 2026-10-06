# layer_fx.py
"""
Единый движок конструктора тем: LOOM (EchoScript) живёт внутри слоёв.

Раньше было два мира: конструктор тем (картинки, текст, фигуры поверх плеера) и
отдельный режим LOOM (скрипты EchoScript со своим холстом и студией). Теперь это
один конструктор, а у слоя есть всё, что умел LOOM:

  * слой-виджет (kind "script") — любой виджет EchoScript: кнопки, перемотка,
    громкость, пластинка, обложка, текст песни, список треков, спектры, свои
    шейдер-подобные эффекты… Слой можно двигать, тянуть, крутить и наклонять в 3D,
    как картинку, а код виджета править прямо в конструкторе;
  * поведение (поле "behavior") — обработчики EchoScript у ЛЮБОГО слоя (картинка,
    видео, GIF, текст, фигура, эффект, виджет): on frame(dt) { rot += … },
    on beat(p) { … }, on click { … } — своя логика, физика и реакция на звук;
  * эффекты из встроенных тем (kind "effect") — настоящие MilkDrop из Winamp,
    жидкость из Fluid, живые обои — тоже слоями, их можно складывать в стопку;
  * предустановки (PRESETS) — одним нажатием скопировать эффекты Fluid, Winamp,
    Echoes Music, LOOM и любых своих тем/скриптов в текущую тему;
  * перевод старых тем LOOM (*.echo) в темы конструктора (script_theme).

  ScriptHost  — один анализ звука и один снимок плеера на кадр для всех скриптов
                темы; компиляция по требованию, кэш по исходнику.
  LayerInput  — мышь для слоёв: клики, перетаскивание, колесо, наведение.
"""
from __future__ import annotations

import copy
import json
import math
import random
import re
import time

import numpy as np
from PyQt6.QtCore import QEvent, QObject, QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PyQt6.QtWidgets import QApplication, QWidget

import dsl_layout as DL
import theme_kit as tk

REF_W, REF_H = 1280.0, 760.0                      # окно, под которое заданы доли в каталоге виджетов
MOUSE_EVENTS = ("press", "release", "click", "drag", "move", "wheel", "enter", "leave")
_INTERACTIVE_RE = re.compile(r"\bon\s+(press|release|click|drag|move|wheel|enter|leave)\b")


# ══════════════════════════════════════════════════════════════════════════ #
#  Каталог виджетов (бывшие блоки LOOM + новые)
# ══════════════════════════════════════════════════════════════════════════ #

LIB: dict[str, dict] = {}
GROUP_ORDER = ["Плеер", "Текст", "Визуализаторы", "Из тем", "Фигуры", "Фон"]


def _lib(key, title, group, cls, code, props=None, geom=(0.4, 0.4, 0.2, 0.2), hint="", z="front", clip=True,
         hidden=False):
    gx, gy, gw, gh = geom
    LIB[key] = {"key": key, "title": title, "group": group, "cls": cls, "code": code.strip("\n"),
                "props": dict(props or {}), "geom": (gx, gy, gw, gh), "hint": hint, "z": z, "clip": clip,
                "hidden": hidden}


for _b in DL.BLOCKS.values():
    _spec = dict(_b.get("spec") or {})
    _z = "back" if str(_spec.get("z", "0")).strip().startswith("-") else "front"
    _clip = str(_spec.get("clip", "true")).strip() != "false"
    _lib(_b["key"], _b["title"], _b["group"], _b["cls"], _b["code"],
         {k: v for k, v in _spec.items() if k not in ("z", "clip", "cache")}, _b["geom"], _b["hint"], _z, _clip)

_lib("vinyl", "Пластинка с тонармом", "Плеер", "Vinyl", """
widget Vinyl {          // блок: vinyl
  prop label = true     // обложка трека в центре
  prop arm = true       // тонарм
  prop rpm = 33         // оборотов в минуту
  prop color = "#0c0c10"
  prop shine = theme.accent
  state angle = 0
  state speed = 0
  state arm_a = 0
  on frame(dt) {
    speed = approach(speed, player.playing ? rpm * 6 : 0, 2.5, dt)
    angle += speed * dt
    arm_a = approach(arm_a, track.loaded and player.playing ? 20 + track.progress * 16 : 0, 3, dt)
  }
  on click { toggle() }
  on draw {
    let s = min(w, h)
    let cx = w / 2 - (arm ? s * 0.07 : 0)
    let cy = h / 2
    let R = s * (arm ? 0.43 : 0.48)
    nostroke()
    blend("add")
    glow(cx, cy, R * 1.3, with_alpha(shine, 0.16 + audio.bass * 0.3))
    blend("normal")
    fill(color)
    circle(cx, cy, R)
    nofill()
    for i in 0..14 {
      stroke(with_alpha("#ffffff", i % 3 == 0 ? 0.07 : 0.035), 1)
      circle(cx, cy, R * (0.42 + i * 0.04))
    }
    save()
    translate(cx, cy)
    rotate(angle)
    for k in 0..4 {
      stroke(with_alpha("#ffffff", 0.05 - k * 0.01), 1.5)
      arc(0, 0, R * (0.6 + k * 0.08), -24 + k * 3, 24 - k * 3)
      arc(0, 0, R * (0.6 + k * 0.08), 156 + k * 3, 204 - k * 3)
    }
    nostroke()
    let lr = R * 0.36
    if label {
      clip(-lr, -lr, lr * 2, lr * 2, lr)
      if not image(track.cover, -lr, -lr, lr * 2, lr * 2) {
        fill(radial(0, 0, lr, [theme.accent, theme.accent2]))
        circle(0, 0, lr)
      }
    }
    restore()
    fill(theme.bg)
    circle(cx, cy, max(2, R * 0.035))
    if arm {
      let px = cx + R * 1.04
      let py = cy - R * 0.9
      save()
      translate(px, py)
      rotate(arm_a)
      stroke("#c9ccd6", max(2, s * 0.012))
      line(0, 0, -R * 0.1, R * 1.12)
      nostroke()
      fill("#d9dce6")
      rect(-R * 0.1 - s * 0.022, R * 1.1, s * 0.045, s * 0.075, 3)
      restore()
      fill("#2a2c36")
      circle(px, py, s * 0.035)
      fill("#5b5f70")
      circle(px, py, s * 0.015)
    }
  }
}""", geom=(0.3, 0.12, 0.36, 0.6), hint="Вращающаяся пластинка с обложкой и тонармом; клик — пауза/воспроизведение",
     hidden=True)   # в библиотеке вместо неё — настоящий винил плеера (vinyl.py); код остаётся для старых тем

_lib("lyrics", "Текст песни", "Текст", "Lyrics", """
widget Lyrics {         // блок: lyrics
  prop size = 26
  prop align = "center" // left | center | right
  prop around = 1       // сколько строк показывать до и после текущей
  prop color = theme.text
  prop dim = theme.muted
  state shown = -1
  state fade = 1
  on frame(dt) {
    if lyrics.index != shown {
      shown = lyrics.index
      fade = 0
    }
    fade = approach(fade, 1, 7, dt)
  }
  on draw {
    let tx = align == "center" ? w / 2 : (align == "right" ? w : 0)
    if lyrics.count == 0 {
      fill(dim)
      text(track.loaded ? "текста нет" : "", tx, h / 2, size * 0.6, align: align, maxw: w)
      return
    }
    let i = max(0, lyrics.index)
    for k in (0 - around)..(around + 1) {
      let j = i + k
      if j >= 0 and j < lyrics.count {
        let cur = k == 0
        fill(cur ? with_alpha(color, 0.35 + fade * 0.65) : with_alpha(dim, 0.75))
        text(lyrics.lines[j], tx, h / 2 + k * size * 1.45 + (cur ? (1 - fade) * 10 : 0), cur ? size : size * 0.6,
             align: align, weight: cur ? 800 : 500, maxw: w)
      }
    }
  }
}""", geom=(0.15, 0.4, 0.7, 0.22), hint="Текущая строка текста (с таймингами) и соседние")

_lib("winamp_spectrum", "Спектр Winamp", "Из тем", "WinampSpectrum", """
widget WinampSpectrum { // блок: winamp_spectrum
  prop bars = 19
  prop segs = 16
  prop peaks = true
  prop bg = "#000000"
  state lv = []
  state pk = []
  state pv = []
  on frame(dt) {
    if len(lv) != bars {
      lv = fill_list(bars, 0)
      pk = fill_list(bars, 0)
      pv = fill_list(bars, 0)
    }
    for i in 0..bars {
      let v = audio.fft[floor(i * 60 / bars)]
      lv[i] = approach(lv[i], v, v > lv[i] ? 40 : 6, dt)
      if lv[i] >= pk[i] {
        pk[i] = lv[i]
        pv[i] = 0
      } else {
        pv[i] += dt * 1.8
        pk[i] = max(0, pk[i] - pv[i] * dt)
      }
    }
  }
  on draw {
    nostroke()
    fill(bg)
    rect(0, 0, w, h)
    if len(lv) == bars {
      let bw = w / bars
      let sh = h / segs
      for i in 0..bars {
        let n = floor(lv[i] * segs)
        for s in 0..n {
          let t = s / max(1, segs - 1)
          fill(t < 0.5 ? mix("#00c000", "#d8d800", t * 2) : mix("#d8d800", "#e00000", (t - 0.5) * 2))
          rect(i * bw + 1, h - (s + 1) * sh + 1, bw - 2, sh - 2)
        }
        if peaks {
          fill("#c8c8c8")
          rect(i * bw + 1, h - pk[i] * h - 2, bw - 2, 2)
        }
      }
    }
  }
}""", geom=(0.05, 0.05, 0.25, 0.14), hint="Сегментный спектр с пиками — как в Winamp 2")

_lib("echoes_wave", "«Моя волна»", "Из тем", "EchoesWave", """
widget EchoesWave {     // блок: echoes_wave
  prop color = theme.accent
  prop color2 = theme.accent2
  prop rings = 5
  state t = 0
  state lv = 0
  on frame(dt) {
    lv = approach(lv, audio.level, 6, dt)
    t += dt * (0.35 + lv * 1.6)
  }
  on click { toggle() }
  on draw {
    let cx = w / 2
    let cy = h / 2
    let R = min(w, h) * 0.3
    blend("add")
    glow(cx, cy, R * (1.7 + lv * 0.6), with_alpha(color, 0.22 + lv * 0.3))
    nofill()
    for k in 0..rings {
      let pts = []
      let rr = R * (0.55 + k * 0.13) * (1 + lv * 0.25)
      for i in 0..64 {
        let a = i * TAU / 64
        let n = noise(cos(a) * 1.2 + t + k, sin(a) * 1.2 + t * 0.7, k * 0.3)
        let r = rr * (1 + n * (0.08 + lv * 0.22))
        push(pts, cx + cos(a) * r, cy + sin(a) * r)
      }
      stroke(with_alpha(mix(color, color2, k / max(1, rings - 1)), 0.6 - k * 0.08), 2 + lv * 2)
      curve(pts, true)
    }
    blend("normal")
  }
}""", geom=(0.3, 0.15, 0.4, 0.62), hint="Дышащие кольца «Моей волны» из Echoes Music; клик — пауза")

_lib("action_button", "Кнопка (любое действие)", "Плеер", "ActionButton", """
widget ActionButton {   // блок: action_button
  prop action = "play"  // play | prev | next | shuffle | repeat | lyrics | queue | cover | clip | music | builder | themes | fullscreen | export | files | download | profile | mute
  prop style = "round"  // round | winamp | glass | flat
  prop label = ""       // свой текст вместо значка
  prop color = theme.accent
  state flash = 0
  on click {
    if action == "play" { toggle() } elif action == "prev" { prev() } elif action == "next" { next() }
    elif action == "shuffle" { toggle_shuffle() } elif action == "repeat" { cycle_repeat() }
    elif action == "lyrics" { lyrics_mode() } elif action == "queue" { show_queue() }
    elif action == "cover" { cover_mode() } elif action == "clip" { clip_mode() } elif action == "music" { music_hub() }
    elif action == "builder" { theme_builder() } elif action == "themes" { theme_menu() }
    elif action == "fullscreen" { fullscreen() } elif action == "export" { export_fx() }
    elif action == "files" { add_files() } elif action == "download" { downloader() }
    elif action == "profile" { profile() } else { mute() }
    flash = 1
  }
  on frame(dt) { flash = approach(flash, 0, 6, dt) }
  on draw {
    let on_ = (action == "shuffle" and player.shuffle) or (action == "repeat" and player.repeat > 0)
    let t = label
    if t == "" {
      if action == "play" { t = player.playing ? "❚❚" : "▶" } elif action == "prev" { t = "◀◀" }
      elif action == "next" { t = "▶▶" } elif action == "shuffle" { t = "⤮" }
      elif action == "repeat" { t = player.repeat == 2 ? "↻1" : "↻" } elif action == "lyrics" { t = "Текст" }
      elif action == "queue" { t = "Очередь" } elif action == "cover" { t = "Обложка" } elif action == "clip" { t = "Клип" }
      elif action == "music" { t = "Музыка" } elif action == "builder" { t = "✦" } elif action == "themes" { t = "Темы" }
      elif action == "fullscreen" { t = "⛶" } elif action == "export" { t = "FX" } elif action == "files" { t = "+ Файлы" }
      elif action == "download" { t = "Скачать" } elif action == "profile" { t = "Профиль" }
      else { t = player.volume > 0 ? "Звук" : "Тихо" }
    }
    let ts = min(h * 0.42, w / max(2, len(t)) * 1.35)
    if style == "winamp" {
      let dn = pressed or on_
      nostroke()
      let top = dn ? "#8f8fa3" : (hover ? "#ececf4" : "#dcdce6")
      let bot = dn ? "#c4c4d2" : (hover ? "#a0a0b4" : "#8f8fa3")
      fill(linear(0, 0, 0, h, [top, bot]))
      rect(0, 0, w, h)
      stroke(dn ? "#3c3c4c" : "#f6f6fc", 1)
      line(0.5, 0.5, w - 0.5, 0.5)
      line(0.5, 0.5, 0.5, h - 0.5)
      stroke(dn ? "#f6f6fc" : "#3c3c4c", 1)
      line(0.5, h - 0.5, w - 0.5, h - 0.5)
      line(w - 0.5, 0.5, w - 0.5, h - 0.5)
      nostroke()
      fill(on_ ? "#007a00" : "#10101a")
      text(t, w / 2 + (dn ? 1 : 0), h / 2 + (dn ? 1 : 0), ts, align: "center", weight: 800)
    } elif style == "glass" {
      fill(with_alpha("#ffffff", pressed ? 0.24 : (hover ? 0.16 : 0.08)))
      stroke(with_alpha("#ffffff", 0.2), 1)
      rect(0.5, 0.5, w - 1, h - 1, min(w, h) * 0.3)
      nostroke()
      fill(on_ ? color : theme.text)
      text(t, w / 2, h / 2, ts, align: "center", weight: 700)
    } elif style == "flat" {
      nostroke()
      fill(on_ or hover ? color : theme.text)
      text(t, w / 2, h / 2, ts * (1 + flash * 0.1), align: "center", weight: 700)
    } else {
      let big = action == "play"
      let r = min(w, h) / 2 * (pressed ? 0.92 : 1) * (1 + flash * 0.05)
      nostroke()
      fill(big ? (hover ? mix(color, "#ffffff", 0.15) : color) : (hover ? "#ffffff24" : "#ffffff10"))
      circle(w / 2, h / 2, r)
      fill(big ? theme.bg : (on_ ? color : theme.text))
      text(t, w / 2, h / 2, min(ts, r * 0.9), align: "center", weight: 700)
    }
  }
}""", {"action": '"play"', "style": '"round"'}, geom=(0.45, 0.8, 0.07, 0.1),
     hint="Любая кнопка плеера (play, перемотка, повтор, текст, очередь, обложка, темы, конструктор, "
          "полный экран, экспорт…) в стиле: круглая, Winamp, стекло или плоская")

_lib("glass", "Стеклянная плашка", "Фигуры", "GlassCard", """
widget GlassCard {      // блок: glass
  prop radius = 22
  prop tint = "#ffffff"
  prop opacity = 0.08
  prop border = true
  on draw {
    nostroke()
    fill(with_alpha(tint, opacity))
    rect(0, 0, w, h, radius)
    if border {
      nofill()
      stroke(with_alpha("#ffffff", 0.12), 1)
      rect(0.5, 0.5, w - 1, h - 1, radius)
    }
  }
}""", geom=(0.1, 0.1, 0.35, 0.4), hint="Полупрозрачная подложка для группы виджетов", z="back")


def lib_groups() -> list[tuple[str, list[dict]]]:
    out = {}
    for b in LIB.values():
        if b.get("hidden"):
            continue
        out.setdefault(b["group"], []).append(b)
    order = GROUP_ORDER + [g for g in out if g not in GROUP_ORDER]
    return [(g, out[g]) for g in order if g in out]


def _geom_layer(gx, gy, gw, gh) -> dict:
    """Доли окна (как в LOOM) → положение/размер слоя, который тянется вместе с окном."""
    gw, gh = max(0.005, gw), max(0.005, gh)
    return {"x": round(gx + gw / 2, 4), "y": round(gy + gh / 2, 4), "size": round(gw, 4),
            "aspect": round(gw / gh, 4), "box": "window", "stretch_x": 1.0, "stretch_y": 1.0}


def widget_layer(key: str, pos=None, **over) -> dict:
    """Новый слой-виджет из каталога."""
    b = LIB[key]
    g = _geom_layer(*b["geom"])
    if pos is not None:
        g["x"], g["y"] = pos
    L = tk.new_layer("script", name=b["title"], script=b["code"], widget=b["cls"], props=dict(b["props"]),
                     z=b["z"], clip=b["clip"], **g)
    L.update(over)
    return L


def effect_layer(effect: str, **over) -> dict:
    title = dict(tk.EFFECTS).get(effect, effect)
    L = tk.new_layer("effect", name=title, effect=effect, z="back", x=0.5, y=0.5, size=1.0, aspect=1.0,
                     box="window", opaque=effect in ("milkdrop", "fluid"))
    L.update(over)
    return L


# ══════════════════════════════════════════════════════════════════════════ #
#  Скрипты слоёв
# ══════════════════════════════════════════════════════════════════════════ #

BEHAVIOR_FIELDS = {"dx": 0.0, "dy": 0.0, "rot": 0.0, "scale": 1.0, "sx": 1.0, "sy": 1.0, "opacity": 1.0,
                   "tilt_x": 0.0, "tilt_y": 0.0, "hidden": False, "tint": "", "label": ""}

BG_FIELDS = {"speed": 1.0, "zoom": 1.0, "dx": 0.0, "dy": 0.0, "dim": -1.0}

BG_TEMPLATE = """// Скрипт фона темы на EchoScript. Можно менять:
//   speed — скорость видео / GIF / живых обоев (1 — как в теме, 2 — вдвое быстрее, 0.5 — медленнее)
//   zoom  — приближение (1 — как в теме), dx, dy — сдвиг в пикселях,
//   dim   — затемнение 0…1 (-1 — как в теме).
// Видно: audio.bass / mid / high / level / fft / beat, track, player, time, mouse.
state kick = 0
on beat(p) { kick = 1 }
on frame(dt) {
  kick = approach(kick, 0, 4, dt)
  speed = 1 + kick * 1.5
  zoom = 1 + audio.bass * 0.05
}
"""

BG_SNIPPETS = [
    ("Видео быстрее на бит", "state kick = 0\non beat(p) { kick = 1 }\non frame(dt) {\n"
                             "  kick = approach(kick, 0, 4, dt)\n  speed = 1 + kick * 1.5\n}"),
    ("Скорость от громкости", "on frame(dt) { speed = 0.4 + audio.level * 1.8 }"),
    ("Замедление на паузе", "on frame(dt) { speed = player.playing ? 1 : 0.25 }"),
    ("Зум от баса", "on frame(dt) { zoom = 1 + audio.bass * 0.08 }"),
    ("Тряска на бит", "state k = 0\non beat(p) { k = p }\non frame(dt) {\n  k = approach(k, 0, 8, dt)\n"
                      "  dx = (rand() - 0.5) * 30 * k\n  dy = (rand() - 0.5) * 30 * k\n}"),
    ("Медленное покачивание", "on frame(dt) {\n  dx = sin(time * 0.3) * 20\n  dy = cos(time * 0.23) * 12\n"
                              "  zoom = 1.05\n}"),
    ("Темнее на паузе", "on frame(dt) { dim = player.playing ? -1 : 0.7 }"),
    ("Вспышка на бит (светлее)", "state f = 0\non beat(p) { f = p }\non frame(dt) {\n"
                                 "  f = approach(f, 0, 6, dt)\n  dim = max(0, 0.3 - f * 0.3)\n}"),
]

BEHAVIOR_TEMPLATE = """// Поведение слоя на EchoScript. Можно менять: dx, dy (сдвиг, px), rot (поворот, °),
// scale / sx / sy (масштаб), opacity, tilt_x / tilt_y (3D-наклон, °), hidden, tint (цвет), label (текст).
// Видно: w, h (размер слоя), audio.bass / mid / high / level / fft[0..63] / beat, track, player, time.
state vy = 0
on frame(dt) {
  scale = 1 + audio.bass * 0.25
  rot += dt * (10 + audio.mid * 90)
}
on beat(p) { vy = -260 * p }
on click { toggle() }
"""

BEHAVIOR_SNIPPETS = [
    ("Пульс от баса", "on frame(dt) { scale = 1 + audio.bass * 0.3 }"),
    ("Вращение под середину", "on frame(dt) { rot += dt * (15 + audio.mid * 120) }"),
    ("Прыжок на бит (физика)", "state vy = 0\non beat(p) { vy -= 420 * p }\n"
                              "on frame(dt) {\n  vy += 1400 * dt\n  dy += vy * dt\n"
                              "  if dy > 0 { dy = 0\n    vy = -vy * 0.35 }\n}"),
    ("Пружина к мыши", "state vx = 0\nstate vy = 0\non frame(dt) {\n  let s = spring(dx, vx, (mouse.x - W / 2) * 0.05, 90, 10, dt)\n"
                       "  dx = s[0]\n  vx = s[1]\n  let t = spring(dy, vy, (mouse.y - H / 2) * 0.05, 90, 10, dt)\n"
                       "  dy = t[0]\n  vy = t[1]\n}"),
    ("3D-покачивание", "on frame(dt) {\n  tilt_y = sin(time * 1.4) * 25\n  tilt_x = cos(time * 1.1) * 12\n}"),
    ("Мигает на бит", "state f = 0\non beat(p) { f = 1 }\non frame(dt) {\n  f = approach(f, 0, 5, dt)\n"
                      "  opacity = 0.45 + f * 0.55\n}"),
    ("Цвет по громкости", "on frame(dt) { tint = hex(hsv(200 + audio.level * 160, 0.8, 1)) }"),
    ("Название трека", "on frame(dt) { label = track.loaded ? track.title : \"ECHOES\" }"),
    ("Клик — пауза", "on click { toggle() }"),
    ("Колесо — громкость", "on wheel(d) { set_volume(clamp(player.volume + d * 0.05)) }"),
]


def interactive_src(src: str) -> bool:
    return bool(src) and _INTERACTIVE_RE.search(DL.mask(src)) is not None


def _lit_str(s: str) -> str:
    return '"' + str(s).replace("\\", "/").replace('"', '\\"') + '"'


def palette_block(t: dict) -> str:
    """Палитра темы конструктора → блок theme { } для скриптов (theme.accent и т.д.)."""
    pal = tk.resolved_palette(t) if t else {}
    keys = {"bg": "bg", "accent": "accent", "accent2": "accent2", "accent3": "glow", "text": "text",
            "muted": "muted", "panel": "panel", "danger": "danger"}
    parts = [f'name: {_lit_str(t.get("name", "") if t else "")}']
    for k, src in keys.items():
        v = pal.get(src) if isinstance(pal, dict) else None
        if isinstance(v, str) and v.startswith("#"):
            parts.append(f"{k}: {_lit_str(v[:7])}")
    fam = (t.get("font") or {}).get("family") if t else ""
    if fam:
        parts.append(f"font: {_lit_str(fam)}")
    return "theme { " + ", ".join(parts) + " }\n"


def _strip_scene(code: str) -> str:
    for _ in range(8):
        sp = DL._block_span(code, "scene")
        if sp is None:
            break
        code = code[:sp[0]] + code[sp[2] + 1:]
    return code


class LayerScript:
    """Скомпилированный скрипт одного слоя: виджет (role="widget") или поведение (role="behavior")."""

    def __init__(self, host: "ScriptHost", L: dict, role: str, key):
        from dsl_runtime import Scene
        from dsl_lang import EchoError
        self.host, self.role, self.key = host, role, key
        self.lid = L["id"]
        self.error = None
        self.scene = None
        self.inst = None
        self.started = False
        self.interactive = False
        prefix = palette_block(host.rt.theme)
        if role == "widget":
            code = _strip_scene(L.get("script") or "")
            names = DL.widget_names(code)
            cls = L.get("widget") if L.get("widget") in names else (names[0] if names else "")
            if not cls:
                self.error = "в скрипте нет ни одного widget { … }"
                return
            spec = [f"{k}: {self._resolve(v)}" for k, v in (L.get("props") or {}).items()
                    if k not in ("x", "y", "w", "h")]
            spec.append(f"clip: {'true' if L.get('clip', True) else 'false'}")
            body = code
            scene = f"\nscene {{\n  add {cls} {{ x: 0, y: 0, w: 1, h: 1, {', '.join(spec)} }}\n}}\n"
        else:
            fields = BG_FIELDS if role == "bg" else BEHAVIOR_FIELDS
            cls = "__Background" if role == "bg" else "__Behavior"
            body = f"widget {cls} {{\n" + (L.get("behavior") or "") + "\n}\n"
            init = ", ".join(f"{k}: {self._val(v)}" for k, v in fields.items())
            scene = f"\nscene {{\n  add {cls} {{ x: 0, y: 0, w: 1, h: 1, {init} }}\n}}\n"
        # номера строк в ошибках — как в редакторе (без служебного theme { } и строки «widget …»)
        self.offset = prefix.count("\n") + (0 if role == "widget" else 1)
        try:
            sc = Scene(prefix + body + scene, host.bridge, host.analyzer, log_fn=host.log_line)
        except EchoError as e:
            self.error = self._fix(e)
            return
        except Exception as e:                             # noqa: BLE001
            self.error = f"ошибка: {e}"
            return
        sc.gpu = False
        self.scene = sc
        self.inst = next((i for i in sc.instances if i.cls.name == cls), None)
        if self.inst is None:
            self.error = f"виджет «{cls}» не создан"
            self.scene = None
            return
        from dsl_runtime import handles
        self.interactive = any(handles(self.inst, ev) for ev in MOUSE_EVENTS)
        self.drawable = "draw" in self.inst.cls.handlers

    def _fix(self, e) -> str:
        line = getattr(e, "line", 0) or 0
        if line > self.offset:
            return f"строка {line - self.offset}: {e.msg}"
        return getattr(e, "msg", str(e))

    @staticmethod
    def _val(v):
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, str):
            return _lit_str(v)
        return DL.fmt_num(float(v))

    def _resolve(self, v: str) -> str:
        """Свойство-строка «assets/…» → полный путь файла темы."""
        s = str(v).strip()
        if len(s) >= 2 and s[0] in "\"'" and s[-1] == s[0] and s[1:-1].startswith("assets/"):
            p = self.host.rt.asset(s[1:-1])
            return _lit_str(p) if p else s
        return s or "nil"

    def frame(self, sh, dt, w, h):
        sc = self.scene
        if sc is None or self.inst is None:
            return
        w, h = max(1, int(round(w))), max(1, int(round(h)))
        if not self.started:
            sc.W = sc.H = -1
            sc.resize(w, h)
            sc.start()
            self.started = True
        else:
            sc.resize(w, h)
        sc.frame_shared(dt, sh)
        if self.inst.error is not None and self.error is None:
            self.error = self._fix(self.inst.error)

    def paint(self, p: QPainter):
        sc = self.scene
        if sc is not None and self.inst is not None and self.inst.error is None and self.started:
            n = len(sc.errors)
            sc.paint(p)
            if len(sc.errors) != n and self.error is None:
                self.error = self._fix(self.inst.error) if self.inst.error is not None else sc.errors[-1]

    def motion(self) -> dict | None:
        if self.inst is None or self.inst.error is not None or not self.started:
            return None
        f = self.inst.fields

        def num(k, d):
            v = f.get(k, d)
            return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else d
        if self.role == "bg":
            m = {k: num(k, d) for k, d in BG_FIELDS.items()}
            m["speed"] = max(0.05, min(8.0, m["speed"]))
            m["zoom"] = max(0.2, min(5.0, m["zoom"]))
            m["dim"] = min(1.0, m["dim"])
            return m
        out = {k: num(k, d) for k, d in BEHAVIOR_FIELDS.items() if isinstance(d, float)}
        out["hidden"] = bool(f.get("hidden"))
        tint = f.get("tint")
        out["tint"] = ""
        if tint not in ("", None):
            try:
                from dsl_runtime import to_qcolor
                c = to_qcolor(tint)
                # QColor понимает 8 цифр как #AARRGGBB — отдаём именно так (у EchoScript — #RRGGBBAA)
                out["tint"] = c.name(QColor.NameFormat.HexArgb if c.alpha() < 255 else QColor.NameFormat.HexRgb)
            except Exception:                              # noqa: BLE001
                pass
        lab = f.get("label")
        out["label"] = lab if isinstance(lab, str) else ("" if lab is None else str(lab))
        return out


# ══════════════════════════════════════════════════════════════════════════ #
#  Эффекты из встроенных тем
# ══════════════════════════════════════════════════════════════════════════ #

class _Effect:
    img = None
    gen = 0

    def step(self, dt, w, h, L):
        pass

    def stop(self):
        pass


class _LiveEffect(_Effect):
    """Живые обои конструктора (сияние, ретровейв, плазма, звёзды, дождь) — слоем."""

    def __init__(self, rt, L):
        from theme_layers import LiveWallpaper
        t = rt.theme
        cols = L.get("colors") or t["background"].get("colors")
        self.lw = LiveWallpaper(L["effect"], t["palette"]["bg"], cols, L.get("speed", 1.0), L.get("react", 0.6), rt)
        self.size = None
        self.acc = 1.0
        self.cfg = None

    def step(self, dt, w, h, L):
        sz = (max(8, int(w)), max(8, int(h)))
        if sz != self.size:
            self.size = sz
            self.lw.resize(*sz)
            self.lw.prewarm()
        self.acc += dt
        if self.acc >= 1 / 45.0:                           # обои считаются в низком разрешении, 45 кадров хватает
            self.lw.step(min(self.acc, 0.1))
            self.acc = 0.0
            self.img = self.lw.img
            self.gen += 1


class _MilkEffect(_Effect):
    """MilkDrop из темы Winamp: кадры считает фоновый поток, интерфейс не ждёт."""

    def __init__(self, rt, L):
        from winamp_theme import _MilkWorker
        self.rt = rt
        self.w = _MilkWorker()
        self.w.start()
        self._arr = None
        self._last = time.monotonic()
        self.cost = 8.0
        self.k = 1.0

    def step(self, dt, w, h, L):
        res = self.w.take()
        if res is not None:
            arr = res["frame"]
            hh, ww = arr.shape
            self._arr = arr.view(np.uint8)
            self.img = QImage(self._arr.data, ww, hh, 4 * ww, QImage.Format.Format_RGB32)
            self.gen += 1
            self.cost = self.cost * 0.85 + res["cost"] * 0.15
            if self.cost > 11.5:
                self.k = max(0.6, self.k - 0.05)
            elif self.cost < 6.0:
                self.k = min(1.0, self.k + 0.02)
        eng = self.rt.engine
        samples, rate = None, 44100
        try:
            if eng is not None and eng.is_playing():
                s = eng.get_visual_samples(1024)
                rate = int(getattr(eng, "visual_sample_rate", 44100) or 44100)
                if s is not None:
                    samples = np.array(s, dtype=np.float32, copy=True)
        except Exception:                                  # noqa: BLE001
            samples = None
        now = time.monotonic()
        gw = int(max(200, min(520, w * float(L.get("res", 0.5)) * self.k)))   # меньше 200 — «лесенка»
        gh = max(24, int(gw * max(1.0, h) / max(1.0, w)))
        if self.w.submit(samples, rate, min(0.1, now - self._last), gw, gh):
            self._last = now

    def command(self, *cmd):
        self.w.command(*cmd)

    def stop(self):
        self.w.stop()


class _FluidEffect(_Effect):
    """Жидкость из темы Fluid в цветах обложки: всплески на бит, фоновый поток."""

    def __init__(self, rt, L):
        import fluid_core as fc
        from fluid_theme import _FluidWorker
        self.rt, self.fc = rt, fc
        self.wk = _FluidWorker()
        self.wk.start()
        self.dark = not tk.is_light(rt.theme["palette"]["bg"])
        self.dom = fc.DEFAULT_DOMINANT_DARK if self.dark else fc.DEFAULT_DOMINANT
        self.grid = None
        self.cover = None
        self.cool = 0.0
        self.acc = 0.0
        self.scale = 0.7                                   # доля размера слоя, в которой красится кадр
        self.cost = 8.0

    def _cover_palette(self, path):
        fc = self.fc
        dom, mono = (fc.DEFAULT_DOMINANT_DARK if self.dark else fc.DEFAULT_DOMINANT), False
        try:
            from fluid_theme import _qimage_to_rgb_array
            from img_load import read_image
            img = read_image(path, 128) if path else QImage()
            if not img.isNull():
                small = img.scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation)
                dom, mono = fc.dominant_rgb(_qimage_to_rgb_array(small))
        except Exception:                                  # noqa: BLE001
            pass
        return fc.make_palette(dom, mono, dark=self.dark)

    def step(self, dt, w, h, L):
        r = self.wk.take()
        if r is not None:
            self.img = r[0]
            self.gen += 1
            # качество как в теме Fluid: кадр укладывается в ~11 мс фонового времени
            self.cost = self.cost * 0.85 + r[1] * 0.15
            if self.cost > 11.5 and self.scale > 0.45:
                self.scale -= 0.03
            elif self.cost < 6.5 and self.scale < 0.85:
                self.scale += 0.02
        res = float(L.get("res", 0.5))
        gh = int(max(32, min(160, 190 * res)))
        gw = int(max(24, min(240, gh * max(1.0, w) / max(1.0, h))))
        cover = getattr(self.rt.backdrop, "cover_path", "") if self.rt.backdrop is not None else ""
        if self.grid is None:
            self.grid = (gw, gh)
            self.cover = cover
            self.wk.cmd("create", gw, gh, self.fc.seed_from_key(cover or "echoes"), self._cover_palette(cover), 0.0)
        elif (gw, gh) != self.grid:
            self.grid = (gw, gh)
            self.wk.cmd("regrid", gw, gh)
        if cover != self.cover:
            self.cover = cover
            self.wk.cmd("palette", self._cover_palette(cover))
            self.wk.cmd("seed", self.fc.seed_from_key(cover or "echoes"))
        pulse = self.rt.pulse
        self.cool = max(0.0, self.cool - dt)
        if pulse.beat > 0.55 and self.cool <= 0:
            self.cool = 0.25
            self.wk.cmd("splash", random.uniform(0.2, 0.8) * gw, random.uniform(0.2, 0.8) * gh,
                        0.6 + pulse.beat * float(L.get("react", 0.6)))
        self.acc += dt
        if self.acc >= 1 / 40.0:
            # краска растягивается до почти полного размера слоя и только потом раскрашивается
            # (в фоновом потоке): края зон — гладкие кривые. Раньше кадр был сетка ×3 и потом
            # ещё растягивался на окно — края шли «лесенкой»
            iw, ih = max(16, min(2560, int(w))), max(16, min(1600, int(h)))
            ow, oh = max(16, int(iw * self.scale)), max(16, int(ih * self.scale))
            if self.wk.submit(min(0.1, self.acc), pulse.level, iw, ih, ow, oh):
                self.acc = 0.0

    def splash(self, fx, fy, power=1.0):
        if self.grid is not None:
            self.wk.cmd("splash", fx * self.grid[0], fy * self.grid[1], power)

    def stop(self):
        self.wk.stop()


def make_effect(rt, L):
    kind = L.get("effect")
    try:
        if kind == "milkdrop":
            return _MilkEffect(rt, L)
        if kind == "fluid":
            return _FluidEffect(rt, L)
        if kind in tk.LIVE_BACKGROUNDS:
            return _LiveEffect(rt, L)
    except Exception as e:                                 # noqa: BLE001
        print("[layers] effect:", kind, e)
    return _Effect()


# ══════════════════════════════════════════════════════════════════════════ #
#  Хозяин скриптов и эффектов темы
# ══════════════════════════════════════════════════════════════════════════ #

class ScriptHost:
    """Все скрипты и эффекты слоёв одной темы: один анализ звука и один снимок плеера на кадр."""

    def __init__(self, rt):
        from dsl_runtime import AudioAnalyzer, PlayerBridge
        self.rt = rt
        win = rt.host if hasattr(rt.host, "queue_context") else None
        self.bridge = PlayerBridge(win)
        self.analyzer = AudioAnalyzer(rt.engine)
        self.items: dict = {}                              # (id слоя, роль) → LayerScript
        self.effects: dict = {}                            # id слоя → (ключ, эффект)
        self.motion: dict = {}                             # id слоя → поля поведения
        self.frames: dict = {}                             # id слоя → готовая картинка виджета
        self.bg_motion = None                              # поля скрипта фона (speed, zoom, dx, dy, dim)
        self.shared = None
        self.mouse = {"x": 0.0, "y": 0.0, "down": False}
        self.log: list[str] = []
        self.listeners = []                                # редактор: обновить ошибки скриптов
        self._pal_key = None

    def log_line(self, msg):
        self.log.append(str(msg))
        del self.log[:-80]

    # ── скрипты ──
    def _key(self, L, role):
        src = L.get("script") if role == "widget" else L.get("behavior")
        extra = (L.get("widget"), json.dumps(L.get("props") or {}, sort_keys=True), L.get("clip", True)) \
            if role == "widget" else ()
        return (src, extra, self._pal_key)

    def get(self, L, role) -> LayerScript | None:
        if role == "widget" and L.get("kind") != "script":
            return None
        if role in ("behavior", "bg") and not (L.get("behavior") or "").strip():
            return None
        k = (L["id"], role)
        key = self._key(L, role)
        ls = self.items.get(k)
        if ls is None or ls.key != key:
            old = ls
            ls = LayerScript(self, L, role, key)
            if old is not None and old.scene is not None and ls.scene is not None:
                try:
                    ls.scene.adopt_state(old.scene)              # правка кода — состояние не сбрасывается
                except Exception:                          # noqa: BLE001
                    pass
            self.items[k] = ls
            for fn in list(self.listeners):
                try:
                    fn()
                except Exception:                          # noqa: BLE001
                    pass
        return ls

    def error(self, L) -> str:
        out = []
        for role in ("widget", "behavior", "bg"):
            ls = self.items.get((L["id"], role))
            if ls is not None and ls.error:
                out.append(("Поведение: " if role == "behavior" else "") + ls.error)
        return "\n".join(out)

    def effect(self, L):
        key = (L.get("effect"), json.dumps(L.get("colors") or []), round(L.get("speed", 1.0), 3),
               round(L.get("react", 0.6), 3))
        ent = self.effects.get(L["id"])
        if ent is not None and ent[0] == key:
            return ent[1]
        if ent is not None and ent[0][0] == key[0] and key[0] in ("milkdrop", "fluid"):
            self.effects[L["id"]] = (key, ent[1])        # тяжёлые эффекты не пересоздаём из-за цвета
            return ent[1]
        if ent is not None:
            ent[1].stop()
        eff = make_effect(self.rt, L)
        self.effects[L["id"]] = (key, eff)
        return eff

    def sync(self, theme):
        """Слои удалили — их скрипты и эффекты тоже."""
        pk = json.dumps([theme.get("palette"), (theme.get("font") or {}).get("family")], sort_keys=True) \
            if theme else None
        self._pal_key = pk
        ids = {L["id"] for L in (theme or {}).get("layers", [])} | {"__bg__"}
        for k in [k for k in self.items if k[0] not in ids]:
            del self.items[k]
        for lid in [lid for lid in self.effects if lid not in ids]:
            self.effects.pop(lid)[1].stop()
        for lid in [lid for lid in self.motion if lid not in ids]:
            del self.motion[lid]
        for lid in [lid for lid in self.frames if lid not in ids]:
            del self.frames[lid]

    @staticmethod
    def needed(theme) -> bool:
        if ((theme or {}).get("background") or {}).get("script", "").strip():
            return True
        return any(L.get("visible", True) and (L["kind"] in ("script", "effect") or (L.get("behavior") or "").strip())
                   for L in (theme or {}).get("layers", []))

    @staticmethod
    def interactive(theme) -> bool:
        for L in (theme or {}).get("layers", []):
            if not L.get("visible", True):
                continue
            if L["kind"] == "effect" and L.get("effect") in ("milkdrop", "fluid"):
                return True                                # клик — пресет MilkDrop, мышь мешает жидкость
            if L["kind"] == "script" and interactive_src(L.get("script")):
                return True
            if interactive_src(L.get("behavior")):
                return True
        return False

    def step(self, dt, theme, W, H):
        from theme_layers import layer_base_size
        if not theme:
            return
        layers = [L for L in theme["layers"] if L.get("visible", True)]
        scripted = [L for L in layers if L["kind"] == "script" or (L.get("behavior") or "").strip()]
        bg_src = (theme["background"].get("script") or "").strip()
        if scripted or bg_src:
            pl = self.bridge.player()
            self.analyzer.update(dt, pl["playing"])
            from dsl_runtime import snapshot
            sh = snapshot(self.bridge, self.analyzer, pl)
            sh["mouse"] = self.mouse
            self.shared = sh
        self.bg_motion = None
        if bg_src:                                         # скрипт фона: скорость видео, зум, сдвиг, затемнение
            ls = self.get({"id": "__bg__", "behavior": bg_src, "kind": "bg"}, "bg")
            if ls is not None:
                ls.frame(self.shared, dt, W, H)
                self.bg_motion = ls.motion()
        for L in layers:
            k = L["kind"]
            if k in ("script", "effect") or (L.get("behavior") or "").strip():
                w, h = layer_base_size(L, W, H, None)
            else:
                continue
            if (L.get("behavior") or "").strip():
                ls = self.get(L, "behavior")
                if ls is not None:
                    ls.frame(self.shared, dt, w, h)
                    m = ls.motion()
                    if m is not None:
                        self.motion[L["id"]] = m
                    else:
                        self.motion.pop(L["id"], None)
            else:
                self.motion.pop(L["id"], None)
            if k == "script":
                ls = self.get(L, "widget")
                if ls is not None:
                    self._widget_frame(L, ls, dt, w, h)
            elif k == "effect":
                self.effect(L).step(dt, w, h, L)

    def _widget_frame(self, L, ls, dt, w, h):
        """Логика и рисование виджета — не чаще его частоты кадров (fps слоя; 0 — каждый кадр экрана).
        Рисуется ОДИН раз в картинку; перерисовки окна дальше только копируют её (с поворотом,
        растяжением, 3D-наклоном), а не выполняют draw заново на каждую перерисовку области."""
        ent = self.frames.get(L["id"])
        if ent is None:
            ent = self.frames[L["id"]] = {"img": None, "gen": 0, "acc": 0.0, "m": 0.0, "size": None}
        ent["acc"] += dt
        fps = float(L.get("fps", 0) or 0)
        size = (int(round(w)), int(round(h)), bool(L.get("clip", True)), self.rt.dpr)
        if fps > 0 and ent["img"] is not None and ent["size"] == size and ent["acc"] < 1.0 / fps - 0.002:
            return
        step, ent["acc"] = min(0.1, ent["acc"]), 0.0
        ls.frame(self.shared, step, w, h)
        if ls.scene is None or not ls.started or not getattr(ls, "drawable", True):
            ent["img"] = None
            return
        dpr = self.rt.dpr
        m = 0.0 if L.get("clip", True) else max(w, h) * 0.35 + 8
        iw, ih = max(1, int(math.ceil((w + 2 * m) * dpr))), max(1, int(math.ceil((h + 2 * m) * dpr)))
        if iw * ih > 40_000_000:                           # гигантский слой — без картинки, сразу в окно
            ent["img"] = None
            return
        img = ent["img"]
        if img is None or img.width() != iw or img.height() != ih:
            img = QImage(iw, ih, QImage.Format.Format_ARGB32_Premultiplied)
            img.setDevicePixelRatio(dpr)
        img.fill(0)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.translate(m, m)
        try:
            ls.paint(p)
        finally:
            p.end()
        ent.update(img=img, m=m, size=size)
        ent["gen"] += 1

    def widget_gen(self, L):
        ent = self.frames.get(L["id"])
        return ent["gen"] if ent is not None else None

    def paint_widget(self, p, L, w, h) -> bool:
        ent = self.frames.get(L["id"])
        if ent is not None and ent["img"] is not None:
            m = ent["m"]
            p.drawImage(QRectF(-w / 2 - m, -h / 2 - m, w + 2 * m, h + 2 * m), ent["img"])
            return True
        ls = self.items.get((L["id"], "widget"))
        if ls is None or ls.scene is None or not ls.started:
            return False
        p.translate(-w / 2, -h / 2)                        # без картинки (огромный слой) — прямо в окно
        ls.paint(p)
        return True

    def clear(self):
        for _key, eff in self.effects.values():
            try:
                eff.stop()
            except Exception:                              # noqa: BLE001
                pass
        self.effects.clear()
        self.items.clear()
        self.motion.clear()
        self.frames.clear()


# ══════════════════════════════════════════════════════════════════════════ #
#  Мышь для слоёв
# ══════════════════════════════════════════════════════════════════════════ #

_PASSIVE = {"QWidget", "QFrame", "QLabel", "FluidPanel", "QStackedWidget", "Backdrop", "Overlay"}


class LayerInput(QObject):
    """Фильтр событий приложения: клики/перетаскивание/колесо/наведение уходят виджетам и
    поведению слоёв. Слои поверх интерфейса перехватывают мышь всегда, слои на фоне — только
    там, где под курсором пустое место (не кнопка, не список, не поле ввода)."""

    def __init__(self, rt):
        super().__init__(rt)
        self.rt = rt
        self.on = False
        self.press = None                                  # (LayerScript, inst, L, x0, y0, кнопка)
        self.hover = None                                  # (LayerScript, inst)
        self._cursor = False
        self._seen = None

    def enable(self, on: bool):
        app = QApplication.instance()
        if app is None:
            return
        if on and not self.on:
            app.installEventFilter(self)
            self.on = True
        elif not on and self.on:
            app.removeEventFilter(self)
            self.on = False
            self._leave()
            self.press = None

    # ── попадание ──
    def _local(self, geo, pos: QPointF):
        dx, dy = pos.x() - geo["cx"], pos.y() - geo["cy"]
        a = -math.radians(geo["rot"])
        x = dx * math.cos(a) - dy * math.sin(a)
        y = dx * math.sin(a) + dy * math.cos(a)
        sx = geo["sx"] or 1e-6
        sy = geo["sy"] or 1e-6
        return x / sx + geo["w"] / 2, y / sy + geo["h"] / 2

    def _hit(self, pos, passive):
        rt = self.rt
        t = rt.theme
        host = self.rt.scripts
        if t is None or host is None:
            return None
        W, H = rt.size()
        ui_layers = t["components"].get("ui") == "layers"
        idx = list(enumerate(t["layers"]))
        order = [x for x in idx if x[1]["z"] == "front"][::-1]
        if passive or ui_layers:
            order += [x for x in idx if x[1]["z"] == "back"][::-1]
        for i, L in order:
            if not L.get("visible", True):
                continue
            cands = [host.items.get((L["id"], "widget")), host.items.get((L["id"], "behavior"))]
            cands = [c for c in cands if c is not None and c.scene is not None and c.interactive]
            if not cands:
                continue
            geo = rt.painter.geometry(L, W, H, rt.pulse.t, rt.pulse, i)
            if geo.get("hidden"):
                continue
            lx, ly = self._local(geo, pos)
            if not (0 <= lx <= geo["w"] and 0 <= ly <= geo["h"]):
                continue
            for ls in cands:
                inst = ls.scene.hit(lx, ly, MOUSE_EVENTS)
                if inst is not None:
                    return ls, inst, L, i
        return None

    def _hit_effect(self, pos, passive):
        """Слой-эффект MilkDrop/Fluid под курсором → (эффект, тип, доля x, доля y) или None."""
        rt = self.rt
        t = rt.theme
        if t is None or rt.scripts is None:
            return None
        W, H = rt.size()
        idx = list(enumerate(t["layers"]))
        order = [x for x in idx if x[1]["z"] == "front"][::-1]
        if passive or t["components"].get("ui") == "layers":
            order += [x for x in idx if x[1]["z"] == "back"][::-1]
        for i, L in order:
            if L["kind"] != "effect" or L.get("effect") not in ("milkdrop", "fluid") or not L.get("visible", True):
                continue
            ent = rt.scripts.effects.get(L["id"])
            if ent is None:
                continue
            geo = rt.painter.geometry(L, W, H, rt.pulse.t, rt.pulse, i)
            lx, ly = self._local(geo, pos)
            if 0 <= lx <= geo["w"] and 0 <= ly <= geo["h"]:
                return ent[1], L["effect"], lx / max(1.0, geo["w"]), ly / max(1.0, geo["h"])
        return None

    def _local_of(self, L, i, pos):
        rt = self.rt
        W, H = rt.size()
        geo = rt.painter.geometry(L, W, H, rt.pulse.t, rt.pulse, i)
        return self._local(geo, pos)

    def _leave(self):
        if self.hover is not None:
            ls, inst = self.hover
            inst.fields["hover"] = False
            if ls.scene is not None:
                ls.scene._call(inst, "leave", ())
            self.hover = None
        if self._cursor:
            QApplication.restoreOverrideCursor()
            self._cursor = False

    def _set_hover(self, hit):
        cur = (hit[0], hit[1]) if hit is not None else None
        if cur is not None and self.hover is not None and cur[1] is self.hover[1]:
            return
        self._leave()
        if cur is not None:
            self.hover = cur
            cur[1].fields["hover"] = True
            cur[0].scene._call(cur[1], "enter", ())
            from dsl_runtime import handles
            if any(handles(cur[1], ev) for ev in ("click", "press", "drag")):
                QApplication.setOverrideCursor(Qt.CursorShape.PointingHandCursor)
                self._cursor = True

    def _editing(self) -> bool:
        st = getattr(self.rt.host, "_studio", None)
        return st is not None and getattr(st, "canvas", None) is not None

    def eventFilter(self, obj, ev):
        t = ev.type()
        if t not in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease, QEvent.Type.MouseMove,
                     QEvent.Type.Wheel, QEvent.Type.MouseButtonDblClick):
            return False
        if not isinstance(obj, QWidget) or self.rt.theme is None or self.rt.scripts is None:
            return False
        host = self.rt.host
        try:
            if obj.window() is not host or self._editing():
                return False
        except RuntimeError:
            return False
        # одно событие приходит в фильтр по разу для виджета и каждого родителя — берём первый
        stamp = (int(t.value), ev.timestamp() if hasattr(ev, "timestamp") else 0)
        if stamp == self._seen:
            return False
        self._seen = stamp
        try:
            gp = ev.globalPosition()
        except AttributeError:
            return False
        area = self.rt.backdrop if self.rt.backdrop is not None else host
        pos = QPointF(area.mapFromGlobal(gp.toPoint()))
        self.rt.scripts.mouse = {"x": pos.x(), "y": pos.y(), "down": bool(QApplication.mouseButtons())}
        passive = type(obj).__name__ in _PASSIVE
        btn = {Qt.MouseButton.LeftButton: 1, Qt.MouseButton.RightButton: 2}

        if self.press is not None and t in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonRelease):
            ls, inst, L, i, x0, y0, b0, last = self.press
            if ls.scene is None:
                self.press = None
                return False
            lx, ly = self._local_of(L, i, pos)
            if t == QEvent.Type.MouseMove:
                ls.scene._call(inst, "drag", (lx, ly, pos.x() - last[0], pos.y() - last[1]))
                self.press = (ls, inst, L, i, x0, y0, b0, (pos.x(), pos.y()))
                return True
            self.press = None
            inst.fields["pressed"] = False
            ls.scene._call(inst, "release", (lx, ly, btn.get(b0, 3)))
            if abs(pos.x() - x0) < 6 and abs(pos.y() - y0) < 6:
                ls.scene._call(inst, "click", (lx, ly, btn.get(b0, 3)))
            return True

        if t == QEvent.Type.MouseMove:
            if self.press is None and getattr(self, "_stir", None) is not None:
                if not QApplication.mouseButtons():
                    self._stir = None
                else:                                      # ведут по жидкости — всплески по следу
                    eh = self._hit_effect(pos, True)
                    if eh is not None and eh[1] == "fluid":
                        eh[0].splash(eh[2], eh[3], 0.5)
                    return True
            hit = self._hit(pos, passive) if not QApplication.mouseButtons() else None
            self._set_hover(hit)
            if hit is not None:
                ls, inst, L, i = hit
                lx, ly = self._local_of(L, i, pos)
                ls.scene._call(inst, "move", (lx, ly))
            return False
        if t == QEvent.Type.MouseButtonRelease:
            self._stir = None

        hit = self._hit(pos, passive)
        if hit is None:
            if t in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick) and \
                    ev.button() == Qt.MouseButton.LeftButton:
                eh = self._hit_effect(pos, passive)
                if eh is not None:
                    eff, kind, fx, fy = eh
                    if kind == "milkdrop":
                        eff.command("next")                # следующий пресет MilkDrop
                    else:
                        eff.splash(fx, fy, 1.2)            # всплеск жидкости, дальше — вести мышью
                        self._stir = eff
                    return True
            return False
        ls, inst, L, i = hit
        lx, ly = self._local_of(L, i, pos)
        if t == QEvent.Type.Wheel:
            d = ev.angleDelta().y() / 120.0
            ls.scene._call(inst, "wheel", (d, lx, ly))
            return True
        if t in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick):
            b = ev.button()
            inst.fields["pressed"] = True
            ls.scene._call(inst, "press", (lx, ly, btn.get(b, 3)))
            self.press = (ls, inst, L, i, pos.x(), pos.y(), b, (pos.x(), pos.y()))
            return True
        return False


# ══════════════════════════════════════════════════════════════════════════ #
#  LOOM (*.echo) → тема конструктора
# ══════════════════════════════════════════════════════════════════════════ #

def _num_lit(s, default):
    s = str(s or "").strip()
    return float(s) if DL.NUM_RE.match(s) else default


def script_layers(src: str) -> list[dict]:
    """Каждое «add Виджет { … }» сцены скрипта → слой-виджет со своим кодом (класс + нужные ему функции)."""
    sp = DL._block_span(src, "scene")
    if sp is None:
        return []
    m = DL.mask(src)
    adds = []
    for mt in re.finditer(r"\badd\s+([A-Za-z_]\w*)", m[sp[1]:sp[2]]):
        cls = mt.group(1)
        pos = sp[1] + mt.start()
        line = src.count("\n", 0, pos) + 1
        adds.append((cls, DL.get_props(src, line, cls)))
    code_of = {}
    layers = []
    for n, (cls, props) in enumerate(adds):
        if cls not in code_of:
            try:
                code_of[cls] = DL.import_widgets("", src, [cls], with_instances=False)[0].strip() + "\n"
            except Exception:                              # noqa: BLE001
                code_of[cls] = _strip_scene(src)
        g = {}
        for k, d in (("x", 0.0), ("y", 0.0), ("w", 1.0), ("h", 1.0)):
            v = _num_lit(props.get(k), d)
            if abs(v) > 1.0:                               # пиксели → доли окна
                v /= REF_W if k in ("x", "w") else REF_H
            if v < 0 and k in ("x", "y"):
                v += 1.0
            g[k] = v
        z = _num_lit(props.get("z"), 0)
        clip = str(props.get("clip", "true")).strip() != "false"
        rest = {k: v for k, v in props.items() if k not in ("x", "y", "w", "h", "z", "clip", "name", "group")}
        L = tk.new_layer("script", name=str(props.get("name", "")).strip('"') or cls, script=code_of[cls], widget=cls,
                         props=rest, clip=clip, z="back" if z < 0 else "front", fps=60,   # как было в LOOM
                         **_geom_layer(g["x"], g["y"], g["w"], g["h"]))
        L["_order"] = (z, n)
        layers.append(L)
    layers.sort(key=lambda L: L.pop("_order"))
    return layers


def script_theme(name: str, src: str) -> dict:
    """Целая тема LOOM → тема конструктора: палитра из theme { }, интерфейс — слоями."""
    th = DL.theme_get(src)
    t = tk.blank_canvas()
    t["name"] = name
    t["description"] = tk.TR("Перенесено из LOOM: виджеты EchoScript стали слоями")
    pal = {}
    for k in ("bg", "accent", "accent2", "text", "muted", "panel"):
        v = th.get(k)
        if isinstance(v, str) and re.match(r"^#[0-9a-fA-F]{6}", v):
            pal[k] = v[:7]
    if isinstance(th.get("accent3"), str) and th["accent3"].startswith("#"):
        pal["glow"] = th["accent3"][:7]
    t["palette"].update(pal)
    t["background"]["color"] = pal.get("bg", t["background"]["color"])
    if isinstance(th.get("font"), str):
        t["font"]["family"] = th["font"]
    t["layers"] = script_layers(src)
    return tk.normalize(t)


# ══════════════════════════════════════════════════════════════════════════ #
#  Предустановки: эффекты из знаменитых тем
# ══════════════════════════════════════════════════════════════════════════ #

def _loom_source():
    try:
        import dsl_studio
        return dsl_studio.read_script("Loom") or ""
    except Exception:                                      # noqa: BLE001
        return ""


def native_free(x, y, w, h) -> dict:
    """Родной элемент плеера (настоящая пластинка, список, кнопки…) — вынут из раскладки и стоит в долях окна."""
    return {"free": True, "hidden": False, "x": x, "y": y, "w": w, "h": h}


def _p_player_ui():
    out = []
    for key, geom, props in (
            ("glass", (0.03, 0.05, 0.46, 0.9), None),
            ("title", (0.06, 0.6, 0.4, 0.1), None),
            ("seek", (0.06, 0.71, 0.4, 0.06), None),
            ("buttons", (0.15, 0.8, 0.06, 0.1), {"action": '"prev"'}),
            ("buttons", (0.23, 0.79, 0.08, 0.12), {"action": '"play"'}),
            ("buttons", (0.33, 0.8, 0.06, 0.1), {"action": '"next"'}),
            ("volume", (0.06, 0.92, 0.18, 0.04), None),
            ("spectrum", (0.52, 0.06, 0.45, 0.18), {"mirror": "true", "bars": "40"}),
            ("lyrics", (0.52, 0.26, 0.45, 0.2), None),
            ("tracks", (0.52, 0.48, 0.45, 0.47), None)):
        L = widget_layer(key, **_geom_layer(*geom))
        if props:
            L["props"].update(props)
            if key == "buttons":
                L["name"] = {"prev": "Назад", "play": "Пауза / играть", "next": "Вперёд"}[props["action"].strip('"')]
        L["group"] = "ui" if key != "glass" else ""
        out.append(L)
    # пластинка — настоящий винил плеера (vinyl.py), а не нарисованная скриптом копия
    return {"layers": out, "native": {"vinyl": native_free(0.06, 0.08, 0.4, 0.5)}}


PRESETS = [
    # key, тема-источник, название, подсказка, функция → {"layers": [...], "background": {...}?, "ui": ...?}
    ("fluid", "Fluid", "Жидкость в цветах обложки", "Живая жидкость из Fluid: цвета — из обложки, всплески — на бит",
     lambda: {"layers": [effect_layer("fluid", react=0.7)]}),
    ("milkdrop", "Winamp", "MilkDrop", "Настоящие пресеты MilkDrop из темы Winamp (клик по слою — следующий пресет)",
     lambda: {"layers": [effect_layer("milkdrop")]}),
    ("winamp_spectrum", "Winamp", "Сегментный спектр", "Зелёно-красный спектр с пиками, как в Winamp 2",
     lambda: {"layers": [widget_layer("winamp_spectrum")]}),
    ("winamp_full", "Winamp", "MilkDrop + спектр + кнопки", "Комбо из Winamp: визуализация на фоне, спектр и кнопки",
     lambda: {"layers": [effect_layer("milkdrop"), widget_layer("winamp_spectrum"),
                         widget_layer("buttons", **_geom_layer(0.07, 0.22, 0.05, 0.08), props={"action": '"prev"'}),
                         widget_layer("buttons", **_geom_layer(0.13, 0.21, 0.06, 0.1), props={"action": '"play"'}),
                         widget_layer("buttons", **_geom_layer(0.2, 0.22, 0.05, 0.08), props={"action": '"next"'})]}),
    ("echoes_wave", "Echoes Music", "«Моя волна»", "Дышащие кольца из Echoes Music",
     lambda: {"layers": [widget_layer("echoes_wave")]}),
    ("echoes_vinyl", "Echoes Music", "Винил", "Настоящая пластинка плеера: крутится, пока играет музыка; "
                                             "обложка — в центре",
     lambda: {"layers": [], "native": {"vinyl": native_free(0.3, 0.12, 0.36, 0.6)}}),
    ("echoes_lyrics", "Echoes Music", "Текст песни", "Строки текста с таймингами, плавная смена",
     lambda: {"layers": [widget_layer("lyrics")]}),
    ("loom", "LOOM", "Ткацкий станок целиком", "Вся тема LOOM: станок, веретено, нить-прогресс, кнопки, список",
     lambda: {"layers": script_layers(_loom_source())}),
    ("player_ui", "Интерфейс", "Плеер из слоёв", "Пластинка, название, перемотка, кнопки, громкость, спектр, текст, "
                                                "список — каждый элемент отдельный слой",
     lambda: dict(_p_player_ui(), ui="layers")),
] + [(k, "Живые обои", t, "Живые обои слоем: можно уменьшить, повернуть, наложить несколько",
      (lambda k=k: {"layers": [effect_layer(k)]})) for k, t in tk.EFFECTS if k in tk.LIVE_BACKGROUNDS]


def preset_groups():
    out = {}
    for p in PRESETS:
        out.setdefault(p[1], []).append(p)
    return list(out.items())


def apply_preset(theme: dict, key: str) -> list[dict]:
    """Добавить слои предустановки в тему (поверх уже имеющихся — эффекты складываются). → новые слои."""
    for k, _grp, _t, _h, fn in PRESETS:
        if k == key:
            data = fn()
            new = [copy.deepcopy(L) for L in data.get("layers", [])]
            for L in new:
                L["id"] = tk.new_layer(L["kind"])["id"]
            theme["layers"].extend(new)
            if data.get("ui"):
                theme["components"]["ui"] = data["ui"]
            for nk, spec in (data.get("native") or {}).items():   # родные элементы (настоящий винил и т.п.)
                theme.setdefault("native", {})[nk] = dict(spec)
            return new
    return []


def copy_layers_from(theme: dict, other: dict, store=None) -> list[dict]:
    """Слои другой своей темы (с их картинками/видео/шрифтами) → в текущую."""
    new = []
    for L in other.get("layers", []):
        D = copy.deepcopy(L)
        D["id"] = tk.new_layer(D["kind"])["id"]
        if store is not None:
            for key in ("src", "font"):
                rel = D.get(key)
                if rel and str(rel).startswith("assets/"):
                    path = store.asset(other, rel)
                    if path:
                        D[key] = store.import_asset(theme, path) or rel
            for pk, pv in list((D.get("props") or {}).items()):
                s = str(pv).strip().strip('"')
                if s.startswith("assets/"):
                    path = store.asset(other, s)
                    if path:
                        D["props"][pk] = _lit_str(store.import_asset(theme, path) or s)
        new.append(D)
    theme["layers"].extend(new)
    return new


# ══════════════════════════════════════════════════════════════════════════ #
#  Перенос старых тем LOOM в конструктор (один раз при запуске)
# ══════════════════════════════════════════════════════════════════════════ #

def migrate_loom_scripts(store, settings: dict) -> dict:
    """Темы LOOM (встроенная «Loom» и скрипты пользователя ~/.neon_player/echoscript/*.echo) → темы
    конструктора. Каждый скрипт переносится один раз (settings["loom_migrated"]).
    → {имя скрипта: имя новой темы}."""
    try:
        import dsl_studio
    except Exception:                                      # noqa: BLE001
        return {}
    done = set(settings.get("loom_migrated") or [])
    made = {}
    for s in dsl_studio.list_scripts():
        if s["path"] in done:
            continue
        try:
            with open(s["path"], encoding="utf-8") as f:
                src = f.read()
            t = script_theme(s["name"], src)
            if not t["layers"]:
                done.add(s["path"])
                continue
            t["name"] = tk.suggest_name({x["name"] for x in store.list()}, s["name"])
            store.save(t)
            made[s["name"]] = t["name"]
        except Exception as e:                             # noqa: BLE001
            print("[layers] перенос LOOM:", s.get("name"), e)
        done.add(s["path"])
    settings["loom_migrated"] = sorted(done)
    return made


def placeholder(p: QPainter, w, h, text, error=False):
    """Рамка-заглушка (нет кода, ошибка, эффект в миниатюре)."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r = QRectF(-w / 2, -h / 2, w, h)
    p.setPen(QPen(QColor(255, 90, 110, 200) if error else QColor(255, 255, 255, 120), 1.5, Qt.PenStyle.DashLine))
    p.setBrush(QColor(170, 30, 45, 70) if error else QColor(0, 0, 0, 50))
    p.drawRoundedRect(r, 8, 8)
    if w > 40 and h > 18:
        f = QFont("Segoe UI")
        f.setPixelSize(int(max(9, min(13, h * 0.12))))
        p.setFont(f)
        p.setPen(QColor(255, 220, 225) if error else QColor(235, 235, 245, 200))
        p.drawText(r.adjusted(6, 4, -6, -4), int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap), text)
    p.restore()
