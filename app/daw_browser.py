# daw_browser.py
"""
Браузер студии (слева): текущий проект, библиотека треков плеера, звуки кита, инструменты,
эффекты, пресеты голоса, записи и проекты. Щелчок — послушать (звук кита, трек, запись),
двойной щелчок — добавить в проект, перетащить — в плейлист или микшер. Поиск сверху.
"""
from __future__ import annotations

import os

from PyQt6.QtCore import QMimeData, Qt
from PyQt6.QtGui import QColor, QDrag, QPainter, QPixmap
from PyQt6.QtWidgets import QLineEdit, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

import daw_fx as FX
import daw_inst as I
import daw_project as PR
import daw_ui as U

ROLE = Qt.ItemDataRole.UserRole


class Tree(QTreeWidget):
    def __init__(self, br, parent=None):
        super().__init__(parent)
        self.br = br
        self.setHeaderHidden(True)
        self.setDragEnabled(True)
        self.setIndentation(14)
        self.setAnimated(False)
        self.setMouseTracking(True)
        self.setUniformRowHeights(True)

    def mimeData(self, items):
        md = QMimeData()
        if items:
            d = items[0].data(0, ROLE)
            if d:
                md.setText("echoes:" + d[0] + ":" + str(d[1]))
        return md

    def mimeTypes(self):
        return ["text/plain"]

    def startDrag(self, actions):
        it = self.currentItem()
        if it is None or not it.data(0, ROLE):
            return
        drag = QDrag(self)
        drag.setMimeData(self.mimeData([it]))
        pm = QPixmap(180, 22)
        pm.fill(QColor(0, 0, 0, 0))
        p = QPainter(pm)
        p.fillRect(pm.rect(), QColor(240, 163, 58, 200))
        p.setPen(QColor("#111"))
        p.setFont(U.font(11, True))
        p.drawText(pm.rect().adjusted(6, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, it.text(0)[:28])
        p.end()
        drag.setPixmap(pm)
        drag.exec(Qt.DropAction.CopyAction)


class Browser(QWidget):
    def __init__(self, shell, parent=None):
        super().__init__(parent)
        self.sh = shell
        self.setStyleSheet("background: #252a2e;")
        v = QVBoxLayout(self)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(4)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск: треки, звуки, эффекты…")
        self.search.textChanged.connect(self.apply_filter)
        v.addWidget(self.search)
        self.tree = Tree(self)
        self.tree.itemClicked.connect(self._click)
        self.tree.itemDoubleClicked.connect(self._dbl)
        self.tree.itemEntered.connect(self._enter)
        v.addWidget(self.tree, 1)
        self.sections = {}
        self.build()

    def _sec(self, key, title):
        it = QTreeWidgetItem([title])
        f = it.font(0)
        f.setBold(True)
        it.setFont(0, f)
        it.setForeground(0, QColor("#f0a33a"))
        self.tree.addTopLevelItem(it)
        self.sections[key] = it
        return it

    def _leaf(self, parent, text, data, tip=""):
        it = QTreeWidgetItem([text])
        it.setData(0, ROLE, data)
        if tip:
            it.setToolTip(0, tip)
        parent.addChild(it)
        return it

    def build(self):
        self.tree.clear()
        self.sections = {}
        self._sec("project", "Текущий проект")
        lib = self._sec("library", "Библиотека треков")
        lib.setToolTip(0, "Треки плеера: перетащите в плейлист — станет аудиоклипом")
        kit = self._sec("kit", "Звуки (драм-кит)")
        for cat, items in I.KIT:
            c = QTreeWidgetItem([cat])
            kit.addChild(c)
            for nm, _f in items:
                self._leaf(c, nm, ("kit", nm), "Щелчок — послушать, двойной — новый канал")
        ins = self._sec("inst", "Инструменты")
        for kind, cls in I.GENERATORS.items():
            if kind == "sampler":
                continue
            c = QTreeWidgetItem([cls.NAME])
            c.setToolTip(0, cls.INFO)
            ins.addChild(c)
            self._leaf(c, "Чистый", ("inst", f"{kind}|"), cls.INFO)
            for nm in cls.PRESETS:
                if nm != "Init":
                    self._leaf(c, nm, ("inst", f"{kind}|{nm}"), "Двойной щелчок — новый канал с этим звуком")
        fx = self._sec("fx", "Эффекты")
        for grp in FX.FX_GROUPS:
            c = QTreeWidgetItem([grp])
            fx.addChild(c)
            for cls in FX.FX_CLASSES:
                if cls.GROUP == grp:
                    self._leaf(c, cls.NAME, ("fx", cls.TYPE), cls.INFO + " Двойной щелчок — на выбранную дорожку микшера")
        vp = self._sec("voice", "Пресеты голоса")
        for nm, _ch in FX.VOICE_PRESETS:
            self._leaf(vp, nm, ("voice", nm), "Двойной щелчок — цепочка эффектов на выбранную дорожку микшера")
        self._sec("recs", "Записи")
        self._sec("projects", "Проекты")
        self.refresh_project()
        self.refresh_library()
        self.refresh_files()
        self.sections["kit"].setExpanded(True)

    def refresh_project(self):
        sec = self.sections.get("project")
        if sec is None:
            return
        exp = {sec.child(i).text(0): sec.child(i).isExpanded() for i in range(sec.childCount())}
        sec.takeChildren()
        p = self.sh.p
        pats = QTreeWidgetItem([f"Паттерны ({len(p['patterns'])})"])
        sec.addChild(pats)
        for pt in p["patterns"]:
            self._leaf(pats, pt["name"], ("pattern", pt["id"]), "Двойной щелчок — выбрать, тащить — в плейлист")
        chs = QTreeWidgetItem([f"Каналы ({len(p['channels'])})"])
        sec.addChild(chs)
        for c in p["channels"]:
            self._leaf(chs, c["name"], ("channel", c["id"]), "Двойной щелчок — открыть")
        if p["sources"]:
            au = QTreeWidgetItem([f"Аудио ({len(p['sources'])})"])
            sec.addChild(au)
            for s in p["sources"]:
                self._leaf(au, s["name"], ("source", s["id"]), "Тащить — в плейлист")
        for i in range(sec.childCount()):
            ch = sec.child(i)
            key = ch.text(0)
            ch.setExpanded(exp.get(key, True) if key.startswith("Паттерны") else exp.get(key, False))
        sec.setExpanded(True)

    def refresh_library(self):
        sec = self.sections.get("library")
        if sec is None:
            return
        sec.takeChildren()
        import daw_mashup as M
        lib = list(self.sh.library())
        lib.sort(key=lambda t: M.track_title(t).lower())
        for t in lib:
            path = str(t.get("path", ""))
            if not path:
                continue
            self._leaf(sec, M.track_title(t), ("track", path), "Щелчок — послушать, двойной — в плейлист, тащить — куда нужно")
        sec.setText(0, f"Библиотека треков ({sec.childCount()})")
        self.apply_filter(self.search.text())

    def refresh_files(self):
        for key, folder, ext, kind in (("recs", PR.RECS, ".wav", "rec"), ("projects", PR.PROJECTS, ".echoproj", "proj")):
            sec = self.sections.get(key)
            if sec is None:
                continue
            sec.takeChildren()
            try:
                files = sorted(folder.glob("*" + ext), key=lambda f: f.stat().st_mtime, reverse=True) if folder.exists() else []
            except OSError:
                files = []
            for f in files[:200]:
                self._leaf(sec, f.stem, (kind, str(f)), "Двойной щелчок — " + ("в плейлист" if kind == "rec" else "открыть"))
            if not files:
                it = QTreeWidgetItem(["пусто"])
                it.setForeground(0, QColor("#6d777f"))
                sec.addChild(it)

    def apply_filter(self, text):
        q = (text or "").strip().lower().replace("ё", "е")
        try:
            import search_util as SU
            vs = [v.lower() for v in SU.variants(q)] if q else []
        except Exception:                                # noqa: BLE001
            vs = [q] if q else []

        def match(it):
            t = it.text(0).lower().replace("ё", "е")
            return any(v in t for v in vs)

        def walk(it):
            any_child = False
            for i in range(it.childCount()):
                ch = it.child(i)
                if ch.childCount():
                    ok = walk(ch)
                else:
                    ok = not q or match(ch)
                    ch.setHidden(not ok)
                any_child = any_child or ok
            if it.parent() is not None:
                it.setHidden(bool(q) and not any_child and not match(it))
            if q and any_child:
                it.setExpanded(True)
            return any_child
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            ok = walk(top)
            top.setHidden(bool(q) and not ok)

    # ── действия ── #
    def _enter(self, it, _col):
        tip = it.toolTip(0)
        if tip:
            U.hint_to(self, f"{it.text(0)}: {tip}")

    def _click(self, it, _col):
        d = it.data(0, ROLE)
        if not d:
            return
        kind, val = d
        if kind == "kit":
            self.sh.preview_sample(I.kit_sample(val))
        elif kind in ("track", "rec"):
            self.sh.preview_file(val)
        elif kind == "inst":
            g, nm = val.split("|", 1)
            cls = I.GENERATORS.get(g)
            if cls:
                prm = dict(cls.PRESETS.get(nm, {}))
                self.sh.preview_gen(g, prm)

    def _dbl(self, it, _col):
        d = it.data(0, ROLE)
        if not d:
            return
        kind, val = d
        sh = self.sh
        if kind == "kit":
            sh.add_channel("sampler", {"sample": "kit:" + val}, val)
        elif kind == "inst":
            g, nm = val.split("|", 1)
            cls = I.GENERATORS.get(g)
            if cls:
                sh.add_channel(g, dict(cls.PRESETS.get(nm, {})), nm or cls.NAME)
        elif kind == "fx":
            sh.add_fx_to_selected(val)
        elif kind == "voice":
            sh.apply_voice_preset(sh.mixer_view.cur, val)
        elif kind == "pattern":
            sh.select_pattern(val)
        elif kind == "channel":
            sh.select_channel(val)
            sh.open_channel_plugin(val)
        elif kind in ("track", "rec", "source"):
            sh.drop_item(f"echoes:{kind}:{val}", sh.song_beat(), None)
        elif kind == "proj":
            sh.open_project(val)
