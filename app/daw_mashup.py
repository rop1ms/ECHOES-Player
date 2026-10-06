# daw_mashup.py
"""
Мэшап-бот студии: из двух треков — вокал одного на бите другого, сведено и разложено по
плейлисту, чтобы дальше можно было править руками.

Как он думает:
1. Слух. Темп, сетка долей и сильные доли, части песни и самые мощные места — тот же
   анализатор, что строит карты osu! (поток атак, складывание темпа, трекер долей, новизна
   по тактам). Тональность — по хроме отдельно вокала и отдельно бита (профили Крумхансла и
   Темперли).
2. Разделение. Нейросеть MDX-Net отделяет вокал от бита (daw_sep). По вокалу — где поют:
   громкость голоса по тактам. Припевы — части, где повторяется одна и та же мелодия
   (хрома вокала по тактам, сравнение окнами по 4 такта) и голос громче.
3. Сведение темпа. Доли вокала ложатся на доли бита: растяжение фазовым вокодером по карте
   «доля → доля» (живой темп не уплывает). Половинный и двойной темп учитываются. Если темпы
   далеко — встречаются посередине: бит меняет скорость как у диджея (передискретизация,
   барабаны остаются чёткими), вокал подстраивается к новому строю бита.
4. Тональность. Перебор сдвига вокала −6…+6 полутонов: совпадение хромы, совместимость по
   кругу Camelot и штраф за большой сдвиг; тембр голоса сохраняется (форманты).
5. Аранжировка — две схемы:
   * по вокалу: песня идёт как в оригинале (куплет, припев, куплет…) одним непрерывным
     голосом, под каждую часть подкладывается подходящая часть бита (под припев — припев/дроп
     бита, под куплет — куплет), короткая часть бита повторяется по 4 такта;
   * по биту: части бита по порядку, на них — фразы вокала той же роли подряд.
6. Сведение. Громкость вокала подгоняется к биту (−3 дБ к громкости бита), в бите вырезается
   место под голос (EQ 1–3 кГц), на вокал — EQ присутствия, де-эссер, компрессор, пластина,
   дилей; переходы — райзер и удар перед припевами; на мастере — склейка и лимитер.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
import re

import numpy as np

import daw_dsp as D
import daw_fx as FX
import daw_project as PR
from daw_dsp import SR

ANALYSIS = PR.STUDIO / "analysis"
AN_VERSION = 7


def _log(msg):
    try:
        print("[studio mashup]", msg)
    except Exception:                                    # noqa: BLE001
        pass


def track_title(t: dict) -> str:
    a = (t.get("artist") or "").strip()
    ti = (t.get("title") or "").strip() or os.path.splitext(os.path.basename(t.get("path", "")))[0]
    for junk in (" - SoundLoadMate.com", "SoundLoadMate.com", "(Official Video)", "(Official Audio)"):
        ti = ti.replace(junk, "")
    ti = re.sub(r"\s*\[[^\]]{6,}\]\s*$", "", ti).strip(" -")
    return f"{a} — {ti}" if a and a.lower() not in ti.lower() else ti


# ------------------------------------------------------------------ #
#  Анализ                                                             #
# ------------------------------------------------------------------ #

def _key(path) -> str:
    try:
        st = os.stat(path)
        sig = f"{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}|{AN_VERSION}"
    except OSError:
        sig = str(path)
    return hashlib.sha1(sig.encode("utf-8", "ignore")).hexdigest()[:20]


def analyze(path, progress=None) -> dict:
    """Темп, доли, такты, части, тональность (кэш в studio/analysis)."""
    ANALYSIS.mkdir(parents=True, exist_ok=True)
    f = ANALYSIS / f"{_key(path)}.json"
    try:
        d = json.loads(f.read_text("utf-8"))
        if d.get("v") == AN_VERSION:
            return d
    except Exception:                                    # noqa: BLE001
        pass
    import osu_mapgen as G

    def prog(x, s):
        if progress:
            progress(x, s)
    an = G.analyze(str(path), lambda x, s: prog(x * 0.8, "Слушаю трек: " + s.rstrip("…").lower() + "…"))
    prog(0.83, "Уточняю темп по ударам…")
    x = G.decode(str(path))
    bpm, steady, beats, phase = _refine_grid(x, an)
    prog(0.9, "Определяю тональность…")
    ch = D.chroma(x, G.SR)
    key = D.detect_key(ch)
    d = {"v": AN_VERSION, "dur": an["dur"] / 1000.0, "bpm": float(bpm), "steady": bool(steady),
         "beats": [round(float(b), 5) for b in beats], "phase": int(phase),
         "sections": [{"s": s["s"] / 1000.0, "e": s["e"] / 1000.0, "int": float(s.get("int", 0.5)),
                       "kiai": bool(s.get("kiai")), "bars": int(s.get("bars", 0))} for s in an["sections"]],
         "key": key, "chroma": [round(float(v), 5) for v in ch]}
    try:
        f.write_text(json.dumps(d), "utf-8")
    except Exception:                                    # noqa: BLE001
        pass
    prog(1.0, "Готово")
    return d


def _refine_grid(x, an):
    """Доводка темпа для мэшапа: ошибка в 1 % за три минуты — почти две секунды расхождения голоса.
    Перебор темпа ±3 % вокруг найденного (шаг 0.001 BPM) по «резкости» сложенной огибающей атак
    (все удары ложатся в одну фазу только при верном темпе); затем ровная сетка сравнивается с долями
    трекера — если она ложится на удары не хуже, темп ровный и доли берутся с неё.
    → (bpm, ровный ли, доли в секундах, индекс сильной доли)."""
    import osu_mapgen as G
    beats0 = np.asarray(an["beats"], float) / 1000.0
    bpm0 = float(an["bpm"])
    try:
        fe, fr = G._fine_env(x)
    except Exception:                                    # noqa: BLE001
        return bpm0, bool(an.get("steady")), beats0, int(an.get("phase", 0))
    fe = np.maximum(0.0, fe - G._smooth(fe, int(fr * 0.3)))
    dur = len(x) / G.SR
    idx = np.arange(len(fe), dtype=np.float64)
    nb = 96

    def fold(bpm):
        P = 60.0 * fr / bpm
        ph = (np.mod(idx, P) * (nb / P)).astype(np.int64)
        np.minimum(ph, nb - 1, out=ph)
        h = np.bincount(ph, weights=fe, minlength=nb)
        h = h + 0.5 * np.roll(h, 1) + 0.5 * np.roll(h, -1)
        return float(h.max() / (h.mean() + 1e-12)), h
    c1 = np.arange(bpm0 * 0.97, bpm0 * 1.03, 0.02)
    s1 = [fold(b)[0] for b in c1]
    b1 = float(c1[int(np.argmax(s1))])
    c2 = np.arange(b1 - 0.03, b1 + 0.03, 0.001)
    s2 = [fold(b)[0] for b in c2]
    bpm = float(c2[int(np.argmax(s2))])
    _sc, h = fold(bpm)
    per = 60.0 / bpm
    ph0 = float(np.argmax(h)) * per / nb
    grid = ph0 + np.arange(-int(ph0 / per) - 1, int(dur / per) + 2) * per
    best, best_d = -1.0, 0.0
    for dd in np.arange(-per / 12, per / 12, 0.001):
        s = float(G._interp(fe, fr, grid + dd).sum())
        if s > best:
            best, best_d = s, dd
    grid = grid + best_d
    grid = grid[(grid >= -1e-6) & (grid < dur)]
    s_grid = float(G._interp(fe, fr, grid).mean()) if len(grid) else 0.0
    s_trk = float(G._interp(fe, fr, beats0).mean()) if len(beats0) else 0.0
    steady = s_grid >= 0.93 * s_trk
    if not steady:
        return bpm0, False, beats0, int(an.get("phase", 0))
    ph_old = int(an.get("phase", 0))
    if an.get("steady") and abs(bpm - bpm0) / bpm0 < 0.002 and len(beats0) > 8:
        # сетка почти та же — сильная доля та же, что нашёл анализатор
        downs = beats0[ph_old::4]
        ref = downs[int(np.argmin(np.abs(downs - dur * 0.4)))] if len(downs) else 0.0
        return bpm, True, grid, int(np.argmin(np.abs(grid - ref))) % 4
    # сетка заметно другая — сильная доля по басу на новой сетке (как у анализатора: бочка на «раз»,
    # малый на «два» и «четыре»)
    try:
        B = G._bands(x)
        L = np.log1p(B * 1e4)
        _fb, centers = G._filterbank()
        prev = np.maximum.reduce([np.roll(L, 1, 1), L, np.roll(L, -1, 1)])
        prev = np.vstack([prev[:1], prev[:1], prev[:-2]])
        fl = np.maximum(0, L - prev)
        low = G._smooth(fl[:, centers < 180].sum(1), 3)
        mid = G._smooth(fl[:, (centers >= 180) & (centers < 2500)].sum(1), 3)
        lowb = G._interp(low, G.FPS, grid)
        midb = G._interp(mid, G.FPS, grid)
        phase = int(np.argmax([lowb[p::4].mean() + 0.15 * midb[(p + 2) % 4::4].mean() if len(lowb[p::4]) else 0
                               for p in range(4)]))
    except Exception:                                    # noqa: BLE001
        downs = beats0[ph_old::4]
        ref = downs[int(np.argmin(np.abs(downs - dur * 0.4)))] if len(downs) else 0.0
        phase = int(np.argmin(np.abs(grid - ref))) % 4
    return bpm, True, grid, phase


def G_resample(x):
    """44.1 кГц стерео → 22.05 кГц моно (для хромы), с фильтром от наложения частот."""
    return D.resample(x.mean(axis=1).astype(np.float32), 0.5)


def _bar_chroma(x22, bars) -> list:
    """Хрома по тактам (12 чисел на такт) — «отпечаток» мелодии и гармонии: повтор припева виден по ней."""
    C, tc = D.chroma_frames(x22, 22050)
    out = []
    for i in range(len(bars) - 1):
        m = (tc >= bars[i]) & (tc < bars[i + 1])
        v = C[m].mean(axis=0) if m.any() else np.zeros(12)
        v = v - v.mean()                                  # сравниваем рисунок нот, а не общий уровень
        nv = float(np.linalg.norm(v))
        out.append([round(float(z / nv), 3) for z in v] if nv > 1e-6 else [0.0] * 12)
    return out


def stem_info(path, stems: dict, an: dict) -> dict:
    """По разделённым дорожкам: где поют (по тактам), тональность вокала и бита, громкости."""
    f = ANALYSIS / f"{_key(path)}_stems.json"
    try:
        d = json.loads(f.read_text("utf-8"))
        if d.get("v") == AN_VERSION and d.get("method") == stems.get("method"):
            return d
    except Exception:                                    # noqa: BLE001
        pass
    voc, inst = stems["vocals"], stems["inst"]
    bars = bar_times(an)
    vm = voc.mean(axis=1)
    im = inst.mean(axis=1)
    vdb, idb = [], []
    for i in range(len(bars) - 1):
        a, b = int(bars[i] * SR), int(bars[i + 1] * SR)
        a, b = max(0, a), min(len(vm), max(a + 1, b))
        vdb.append(D.db(float(np.sqrt(np.mean(vm[a:b] ** 2) + 1e-12))) if b > a else -90.0)
        idb.append(D.db(float(np.sqrt(np.mean(im[a:b] ** 2) + 1e-12))) if b > a else -90.0)
    vdb = np.array(vdb)
    idb = np.array(idb)
    top = np.percentile(vdb, 90) if len(vdb) else -20
    # голос «есть», если он не тише 14 дБ от громких мест вокала и не тонет в бите
    act = ((vdb > top - 14) & (vdb > idb - 22)).astype(float)
    v22, i22 = G_resample(voc), G_resample(inst)
    vch = D.chroma(v22, 22050)
    ich = D.chroma(i22, 22050)
    d = {"v": AN_VERSION, "method": stems.get("method"), "bars": [round(float(b), 4) for b in bars],
         "vdb": [round(float(v), 2) for v in vdb], "idb": [round(float(v), 2) for v in idb],
         "act": act.tolist(), "vkey": D.detect_key(vch), "ikey": D.detect_key(ich),
         "vchroma": [round(float(v), 5) for v in vch], "ichroma": [round(float(v), 5) for v in ich],
         "vbc": _bar_chroma(v22, bars), "ibc": _bar_chroma(i22, bars),
         "v_rms": D.rms_db(voc), "i_rms": D.rms_db(inst), "has_vocals": bool(act.mean() > 0.2 and top > -40)}
    try:
        f.write_text(json.dumps(d), "utf-8")
    except Exception:                                    # noqa: BLE001
        pass
    return d


def bar_times(an: dict) -> np.ndarray:
    """Начала тактов (сек) по сильным долям; до первого — достроено назад."""
    beats = np.asarray(an["beats"], float)
    ph = int(an.get("phase", 0))
    if len(beats) < 8:
        p = 60.0 / an["bpm"]
        return np.arange(0, an["dur"] + 4 * p, 4 * p)
    bars = beats[ph::4]
    p4 = float(np.median(np.diff(bars))) if len(bars) > 2 else 4 * 60.0 / an["bpm"]
    pre = []
    t = bars[0] - p4
    while t > -p4 * 0.999:
        pre.append(t)
        t -= p4
    return np.concatenate([pre[::-1], bars, [bars[-1] + p4]])


def beats_ext(an: dict, before=8, after=8) -> np.ndarray:
    """Доли с продолжением на before/after долей за края (для карт растяжения). Если трекер нашёл
    первую долю далеко от начала (тихое вступление), продолжение достаёт до нуля — иначе такт 0
    (он достроен назад до начала файла) попадает не на ту долю и весь трек едет на доли."""
    b = np.asarray(an["beats"], float)
    if len(b) < 2:
        p = 60 / an["bpm"]
        b = np.arange(0, an["dur"], p)
    p0 = float(np.median(np.diff(b[:16]))) if len(b) > 3 else 60 / an["bpm"]
    p1 = float(np.median(np.diff(b[-16:]))) if len(b) > 3 else p0
    before = max(int(before), int(math.ceil(max(0.0, b[0]) / max(1e-3, p0))) + 8)
    after = max(int(after), int(math.ceil(max(0.0, float(an["dur"]) - b[-1]) / max(1e-3, p1))) + 8)
    pre = b[0] - p0 * np.arange(before, 0, -1)
    post = b[-1] + p1 * np.arange(1, after + 1)
    return np.concatenate([pre, b, post])


# ------------------------------------------------------------------ #
#  Совместимость                                                      #
# ------------------------------------------------------------------ #

def tempo_fit(t_voc: float, t_inst: float):
    """Как класть доли вокала на доли бита: k долей бита на долю вокала, растяжение."""
    best = None
    for k in (0.5, 1.0, 2.0):
        ratio = k * t_voc / t_inst                       # во сколько раз ускорить вокал
        c = abs(math.log2(ratio))
        if best is None or c < best[2]:
            best = (k, ratio, c)
    return best[0], best[1]


def key_shift(vchroma, ichroma, vkey, ikey, max_shift=6):
    """Лучший сдвиг вокала в полутонах и оценка 0..1."""
    vc = np.asarray(vchroma, float)
    ic = np.asarray(ichroma, float)
    res = []
    for s in range(-max_shift, max_shift + 1):
        corr = float(np.corrcoef(np.roll(vc, s), ic)[0, 1])
        kd = D.camelot_distance(D.shift_key(vkey, s), ikey)
        bonus = {0.0: 0.32, 0.15: 0.26, 0.4: 0.12}.get(round(kd, 2), 0.0)
        score = corr + bonus - 0.035 * abs(s) - (0.08 if abs(s) > 4 else 0)
        res.append((score, s, corr, kd))
    res.sort(reverse=True)
    _sc, s, corr, kd = res[0]
    fit = max(0.0, min(1.0, 0.5 + 0.5 * corr - 0.4 * kd))
    return s, fit, res


def pair_score(fa: dict, fb: dict) -> tuple[float, str]:
    """Быстрая оценка пары по признакам библиотеки (темп, лад, энергия). 0..1 и пояснение."""
    ta, tb = float(fa.get("bpm") or 0), float(fb.get("bpm") or 0)
    if ta <= 0 or tb <= 0:
        return 0.0, ""
    _k, ratio = tempo_fit(ta, tb)
    st = math.exp(-(math.log2(ratio) / 0.1) ** 2)
    ka = {"tonic": D.KEYS.index(fa.get("key", "C")) if fa.get("key") in D.KEYS else 0, "mode": fa.get("mode", "major")}
    kb = {"tonic": D.KEYS.index(fb.get("key", "C")) if fb.get("key") in D.KEYS else 0, "mode": fb.get("mode", "major")}
    for k in (ka, kb):
        k["camelot"] = D.camelot(k["tonic"], k["mode"])
    best = min((D.camelot_distance(D.shift_key(ka, s), kb) + 0.06 * abs(s), s) for s in range(-3, 4))
    sk = max(0.0, 1.0 - best[0])
    se = 1.0 - min(1.0, abs(float(fa.get("energy", 0.5)) - float(fb.get("energy", 0.5))) * 1.5)
    score = 0.45 * st + 0.4 * sk + 0.15 * se
    why = f"{ta:.0f}→{tb:.0f} BPM ({(ratio - 1) * 100:+.0f} %), {D.key_name(ka['tonic'], ka['mode'])} / " \
          f"{D.key_name(kb['tonic'], kb['mode'])}" + (f", сдвиг {best[1]:+d}" if best[1] else "")
    return score, why


# ------------------------------------------------------------------ #
#  Части песни и роли                                                 #
# ------------------------------------------------------------------ #

ROLE_RU = {"intro": "вступление", "outro": "концовка", "chorus": "припев", "verse": "куплет", "break": "проигрыш",
           "inst": "проигрыш"}


def _sections_bars(an, bars, P=4):
    """Части трека в индексах тактов [(a, b, int, kiai)], границы выровнены по фразам из P тактов.
    Анализатор режет по смене энергии и часто ошибается на такт-два (куплет 22 такта, припев 6) —
    тогда бит переключается посреди фразы. Сетка фраз — сдвиг 0…P−1, на который ложится больше
    всего границ (с весом по перепаду громкости); внутренние границы притягиваются к ней."""
    out = []
    for s in an["sections"]:
        a = int(np.argmin(np.abs(bars - s["s"])))
        b = int(np.argmin(np.abs(bars - s["e"])))
        if b > a:
            out.append([a, b, float(s.get("int", 0.5)), bool(s.get("kiai"))])
    out.sort()
    for i in range(1, len(out)):
        out[i][0] = out[i - 1][1]
    out = [o for o in out if o[1] > o[0]]
    if len(out) < 2 or P < 2:
        return [tuple(o) for o in out]
    o = phrase_offset(out, P)
    lo, hi = out[0][0], out[-1][1]
    cuts = []
    for i in range(1, len(out)):
        c = out[i][0]
        c = o + P * int(round((c - o) / P))
        cuts.append(min(hi, max(lo, c)))
    edges = [lo] + cuts + [hi]
    res = []
    for i, s in enumerate(out):
        a, b = edges[i], edges[i + 1]
        if b <= a:
            continue
        if res and res[-1][1] > a:
            a = res[-1][1]
            if b <= a:
                continue
        res.append([a, b, s[2], s[3]])
    for i in range(1, len(res)):                        # без дыр: начало — конец предыдущей
        res[i][0] = res[i - 1][1]
    return [tuple(r) for r in res if r[1] > r[0]]


def phrase_offset(secs, P=4) -> int:
    """Сдвиг сетки фраз (0…P−1) по границам частей: сколько границ на неё ложится, с весом по
    перепаду громкости между соседними частями."""
    sc = [0.0] * P
    for i in range(1, len(secs)):
        w = 0.4 + abs(float(secs[i][2]) - float(secs[i - 1][2])) + (0.3 if bool(secs[i][3]) != bool(secs[i - 1][3]) else 0)
        sc[secs[i][0] % P] += w
    if len(secs) and secs[0][0] > 0:
        sc[secs[0][0] % P] += 0.3
    return int(np.argmax(sc))


def _win_sim(C, a1, b1, a2, b2, w=4) -> float:
    """Похожесть двух частей: лучшее совпадение окон по w тактов (часть «содержит ту же фразу»)."""
    best = 0.0
    w1 = min(w, b1 - a1)
    w2 = min(w, b2 - a2)
    ww = min(w1, w2)
    if ww < 2:
        return 0.0
    for s1 in range(a1, b1 - ww + 1, 2 if b1 - a1 > 8 else 1):
        x = C[s1:s1 + ww]
        if len(x) < ww:
            continue
        for s2 in range(a2, b2 - ww + 1, 1):
            y = C[s2:s2 + ww]
            if len(y) < ww:
                continue
            v = float((x * y).sum(axis=1).mean())
            if v > best:
                best = v
    return best


def chorus_group(secs, bc, strength, seeds) -> set:
    """Индексы частей-припевов: от «семян» (самых мощных мест) — все, где повторяется их фраза;
    без семян — самая сильная повторяющаяся пара."""
    n = len(secs)
    if n == 0 or not bc:
        return set(seeds)
    C = np.asarray(bc, float)
    S = np.zeros((n, n))
    for i in range(n):
        for j in range(i + 1, n):
            S[i, j] = S[j, i] = _win_sim(C, secs[i][0], secs[i][1], secs[j][0], secs[j][1])
    off = S[np.triu_indices(n, 1)] if n > 1 else np.zeros(1)
    thr = max(0.82, float(np.percentile(off, 70)) if len(off) else 0.82)
    med = float(np.median(strength)) if len(strength) else 0.5
    grp = set(seeds)
    if not grp and n > 1:
        cand = [(S[i, j], i, j) for i in range(n) for j in range(i + 1, n)
                if strength[i] >= med - 0.05 and strength[j] >= med - 0.05 and S[i, j] >= thr]
        if cand:
            _s, i, j = max(cand, key=lambda c: c[0] + 0.3 * (strength[c[1]] + strength[c[2]]))
            grp = {i, j}
    changed = True
    while changed:
        changed = False
        for i in range(n):
            if i not in grp and strength[i] >= med - 0.1 and any(S[i, j] >= thr for j in grp):
                grp.add(i)
                changed = True
    return grp


def roles_beat(an_i, si_i, P=4):
    """Части бита с ролями: intro / verse / chorus / break / outro. Припевы бита — kiai и всё, что
    повторяет их гармонию (хрома бита по тактам)."""
    bars = np.asarray(si_i["bars"], float)
    secs = _sections_bars(an_i, bars, P)
    if not secs:
        secs = [(0, max(1, len(bars) - 1), 0.6, False)]
    n = len(secs)
    ints = [s[2] for s in secs]
    seeds = {i for i, s in enumerate(secs) if s[3]}
    grp = chorus_group([(a, b) for a, b, _i, _k in secs], si_i.get("ibc") or [], ints, seeds)
    grp = {i for i in grp if secs[i][2] >= 0.45 or secs[i][3]}
    out = []
    for i, (a, b, inten, kiai) in enumerate(secs):
        role = "chorus" if i in grp else ("break" if inten < 0.3 else "verse")
        if i == 0 and role != "chorus" and inten < 0.55 and n > 2:
            role = "intro"
        elif i == n - 1 and role != "chorus" and inten < 0.45 and n > 2:
            role = "outro"
        out.append({"a": a, "b": b, "int": inten, "role": role})
    if not any(s["role"] == "chorus" for s in out):
        best = max((s for s in out if s["role"] in ("verse", "break")), key=lambda s: s["int"], default=None)
        if best:
            best["role"] = "chorus"
    return out


def roles_vocal(an_v, si_v, P=4):
    """Части вокального трека: chorus / verse (поют) или inst (не поют)."""
    bars = np.asarray(si_v["bars"], float)
    act = np.asarray(si_v["act"], float)
    vdb = np.asarray(si_v.get("vdb") or [], float)
    secs = _sections_bars(an_v, bars, P)
    out = []
    for a, b, inten, kiai in secs:
        seg = act[a:min(b, len(act))]
        r = float(seg.mean()) if len(seg) else 0.0
        lv = float(np.mean(vdb[a:min(b, len(vdb))])) if len(vdb) and b > a else -90.0
        out.append({"a": a, "b": b, "int": inten, "role": "inst" if r < 0.3 else "verse", "act": r,
                    "kiai": kiai, "lv": lv})
    vs = [s for s in out if s["role"] != "inst"]
    if not vs:
        return out
    lvs = np.array([s["lv"] for s in vs])
    lvn = (lvs - lvs.min()) / max(1e-6, lvs.max() - lvs.min()) if len(lvs) > 1 else np.ones(len(lvs))
    strength = [0.5 * s["int"] + 0.5 * float(l) for s, l in zip(vs, lvn)]
    seeds = {j for j, s in enumerate(vs) if s["kiai"]}
    grp = chorus_group([(s["a"], s["b"]) for s in vs], si_v.get("vbc") or [], strength, seeds)
    if not grp:
        grp = {int(np.argmax(strength))}
    for j in grp:
        vs[j]["role"] = "chorus"
    return out


# ------------------------------------------------------------------ #
#  План                                                               #
# ------------------------------------------------------------------ #

class Options:
    def __init__(self, **kw):
        self.seed = 0
        self.speed = 1.0                    # 0.85 — slowed, 1.2 — sped up
        self.tempo = "auto"                 # auto / inst / voc / mid
        self.backbone = "auto"              # auto / voc (структура вокала) / beat (структура бита) /
        #                                     both (оба трека целиком, как в оригиналах, без нарезки)
        self.max_len = 0.0                  # 0 — целиком
        self.extra_semis = 0                # ручная поправка тона вокала
        self.transitions = True
        self.intro_hook = None              # None — сам решит
        self.vocal_gain_db = 0.0
        self.chorus_only = False
        self.__dict__.update(kw)

    def as_dict(self):
        return dict(self.__dict__)


def _tempo_plan(an_v, an_i, opt):
    tv, ti = float(an_v["bpm"]), float(an_i["bpm"])
    k, ratio = tempo_fit(tv, ti)
    mode = opt.tempo
    if mode == "auto":
        mode = "inst" if abs(math.log2(ratio)) <= math.log2(1.07) else "mid"
    if mode == "voc":
        T = tv * k
    elif mode == "mid":
        T = math.sqrt(tv * k * ti)
    else:
        T = ti
    T *= opt.speed
    # бит: ровный — меняем скорость передискретизацией (как диджей: барабаны не мажутся, тон едет
    # вместе со скоростью), плавающий — фазовым вокодером по долям (тон тот же)
    steady = bool(an_i.get("steady"))
    resample = steady and abs(T / ti - 1) > 1e-4
    beat_semis = 12 * math.log2(T / ti) if resample else (12 * math.log2(opt.speed) if abs(opt.speed - 1) > 1e-4 else 0.0)
    return {"tv": tv, "ti": ti, "k": k, "ratio": ratio, "T": T, "resample": resample, "beat_semis": beat_semis}


def plan(an_v, si_v, an_i, si_i, opt: Options) -> dict:
    """Решения бота: темп, тон, части и что под чем звучит."""
    rng = random.Random(opt.seed * 7919 + 13)
    tp = _tempo_plan(an_v, an_i, opt)
    k = tp["k"]
    s_rel, kfit, _all = key_shift(si_v["vchroma"], si_i["ichroma"], si_v["vkey"], si_i["ikey"])
    s_rel += int(opt.extra_semis)
    backbone = opt.backbone
    variant = opt.seed
    if backbone == "auto":
        backbone = ("voc", "beat", "both")[opt.seed % 3]
        variant = opt.seed // 3
    B = roles_beat(an_i, si_i, 4)
    A = roles_vocal(an_v, si_v, vocal_phrase(k))
    if not any(s["role"] != "inst" for s in A):
        raise RuntimeError("в треке для вокала не нашлось пения — попробуй поменять треки местами")
    has_b_voc = bool(si_i.get("has_vocals"))
    hook = opt.intro_hook if opt.intro_hook is not None else (has_b_voc and rng.random() < 0.55)
    if backbone == "voc":
        segs = _plan_voc(A, B, k, rng, opt, tp)
    elif backbone == "both":
        segs = _plan_both(A, B, k, variant, opt, tp)
    else:
        segs = _plan_beat(A, B, k, rng, opt, tp)
    if not any(sg.get("voc") for sg in segs):
        raise RuntimeError("не получилось положить вокал на бит")
    if hook:
        for sg in segs:
            if sg["role"] == "intro" and not sg.get("voc"):
                sg["hook"] = True
    total = sum(sg["beats"] for sg in segs)
    return {**tp, "semis_rel": s_rel, "kfit": kfit, "segs": segs, "backbone": backbone, "hook": hook,
            "total_beats": total, "roles_v": A, "roles_b": B}


def _cycle(lst, i):
    return lst[i % len(lst)] if lst else None


def vocal_phrase(k) -> int:
    """Длина фразы вокала в его тактах: 4 такта проекта и больше (при k = 0.5 такт вокала — полтакта)."""
    return 4 if k >= 1 else 8


def _fill_beat(sec, beats_needed, pos=0.0):
    """Кусок бита (часть sec) на beats_needed долей с места pos (тактов от начала части); если часть
    короче — повторяется целыми фразами по 4 такта. → (куски, где остановились)."""
    LB = sec["b"] - sec["a"]
    U = LB if LB < 4 else LB // 4 * 4
    U = max(1, U)
    pos = float(pos) % U
    out = []
    t = 0.0
    while t < beats_needed - 1e-6:
        ln = min((U - pos) * 4, beats_needed - t)
        if out and abs(out[-1]["bar"] + out[-1]["beats"] / 4 - (sec["a"] + pos)) < 1e-6:
            out[-1]["beats"] += ln                       # продолжение того же места — одним клипом
        else:
            out.append({"at": t, "bar": sec["a"] + pos, "beats": ln})
        t += ln
        pos = (pos + ln / 4) % U
    return out, pos


def _plan_voc(A, B, k, rng, opt, tp):
    """Основа — структура вокальной песни; под каждую часть — подходящая часть бита. Подряд идущие
    части одной роли — одна часть бита без перескоков (бит играет дальше, а не начинается заново
    каждые 4 такта)."""
    vi = [i for i, s in enumerate(A) if s["role"] != "inst"]
    first, last = vi[0], vi[-1]
    keep = []
    intro_bars_v = int(round(8 / k)) if k >= 1 else 16
    pre = A[:first]
    if pre:
        a = max(pre[0]["a"], A[first]["a"] - intro_bars_v)
        if A[first]["a"] - a >= 1:
            keep.append({"va": a, "vb": A[first]["a"], "role": "intro", "int": 0.2})
    for s in A[first:last + 1]:
        if s["role"] == "inst":
            L = s["b"] - s["a"]
            lim = int(round(8 / k)) if k >= 1 else 16
            keep.append({"va": s["a"], "vb": s["a"] + min(L, lim), "role": "break", "int": s["int"]})
        else:
            keep.append({"va": s["a"], "vb": s["b"], "role": s["role"], "int": s["int"]})
    post = A[last + 1:]
    if post:
        keep.append({"va": post[0]["a"], "vb": post[0]["a"] + min(post[0]["b"] - post[0]["a"], int(round(4 / max(k, 0.5)))),
                     "role": "outro", "int": 0.2})
    if opt.chorus_only:
        keep = [s for s in keep if s["role"] in ("chorus", "intro", "outro")]
    if k < 1:                                          # такт вокала — полтакта проекта: границы по чётным тактам
        for s in keep:
            s["va"] -= s["va"] % 2
            s["vb"] -= s["vb"] % 2
            if s["vb"] <= s["va"]:
                s["vb"] = s["va"] + 2
    merged = []                                        # куплет+куплет подряд — одна часть
    for s in keep:
        m = merged[-1] if merged else None
        if m is not None and m["role"] == s["role"] and m["vb"] == s["va"]:
            m["vb"] = s["vb"]
            m["int"] = max(m["int"], s["int"])
        else:
            merged.append(dict(s))
    keep = merged
    chorusB = [s for s in B if s["role"] == "chorus"]
    verseB = [s for s in B if s["role"] == "verse"] or chorusB
    breakB = [s for s in B if s["role"] == "break"] or verseB
    introB = [s for s in B if s["role"] == "intro"] or [min(B, key=lambda s: s["int"])]
    outroB = [s for s in B if s["role"] == "outro"] or introB
    ci = rng.randrange(len(chorusB)) if (opt.seed and chorusB) else 0
    vj = rng.randrange(len(verseB)) if (opt.seed and verseB) else 0
    segs = []
    max_beats = opt.max_len * tp["T"] / 60 if opt.max_len > 0 else 1e9
    used = 0.0
    for s in keep:
        beats = (s["vb"] - s["va"]) * 4 * k
        if used + beats > max_beats and s["role"] != "outro" and used > 0:
            continue
        role = s["role"]
        if role == "chorus":
            bsec = _cycle(chorusB, ci)
            ci += 1
        elif role == "verse":
            bsec = _cycle(verseB, vj)
            vj += 1
        elif role == "intro":
            bsec = introB[0]
        elif role == "outro":
            bsec = outroB[-1]
        else:
            bsec = breakB[0]
        pieces, _pos = _fill_beat(bsec, beats)
        segs.append({"role": role, "beats": beats, "voc": role in ("chorus", "verse"), "va": s["va"], "vb": s["vb"],
                     "beat": pieces, "bsec": bsec, "int": max(s["int"], bsec["int"] * 0.8)})
        used += beats
    return segs


def _vocal_runs(A):
    """Сплошные куски пения: [(начало, конец)] в тактах вокала."""
    runs = []
    for s in A:
        if s["role"] == "inst":
            continue
        if runs and runs[-1][1] == s["a"]:
            runs[-1][1] = s["b"]
        else:
            runs.append([s["a"], s["b"]])
    return runs


def _vrole_at(A, va):
    for s in A:
        if s["a"] <= va < s["b"]:
            return s["role"]
    return "inst"


def _plan_beat(A, B, k, rng, opt, tp):
    """Основа — структура бита; на его части ложится вокал той же роли — сплошным куском с начала
    подходящей части вокала (а не нарезкой фраз из разных мест песни). Кончилась часть вокала —
    следующая часть той же роли; припевы по кругу."""
    Pv = vocal_phrase(k)
    per = Pv * 4 * k                                    # долей проекта на фразу вокала
    starts = {"chorus": [], "verse": []}                 # начала кусков одной роли подряд
    for i, s in enumerate(A):
        if s["role"] in starts and not (i and A[i - 1]["role"] == s["role"] and A[i - 1]["b"] == s["a"]):
            starts[s["role"]].append(s["a"])
    if not starts["verse"]:
        starts["verse"] = list(starts["chorus"])
    if not starts["chorus"]:
        starts["chorus"] = list(starts["verse"])
    ptr = {"chorus": 0, "verse": rng.randrange(max(1, len(starts["verse"]))) if opt.seed > 1 else 0}
    runs = _vocal_runs(A)

    def run_end(va):
        for a, b in runs:
            if a <= va < b:
                return b
        return va

    def take(role):
        v = _cycle(starts[role], ptr[role])
        ptr[role] += 1
        return v
    segs = []
    max_beats = opt.max_len * tp["T"] / 60 if opt.max_len > 0 else 1e9
    used = 0.0
    vpos, vrole_prev = None, None
    for s in B:
        beats = (s["b"] - s["a"]) * 4.0
        if used >= max_beats:
            break
        role = s["role"]
        if opt.chorus_only and role == "verse":
            continue
        seg = {"role": role, "beats": beats, "voc": False, "beat": [{"at": 0.0, "bar": s["a"], "beats": beats}],
               "bsec": s, "int": s["int"], "phr": []}
        if role in ("chorus", "verse") and starts[role] and beats >= min(per, 16) - 1e-6:
            # припев бита сразу за припевом бита — голос поёт дальше, а не начинает заново
            if vpos is None or vrole_prev != role or segs and not segs[-1].get("voc"):
                vpos = take(role)
            t = 0.0
            guard = 0
            while beats - t >= min(per, 8.0) - 1e-6 and guard < 200:
                guard += 1
                # дальше по песне, пока поют ту же роль; иначе — начало следующей такой части
                if _vrole_at(A, vpos) != role or run_end(vpos) - vpos < Pv / 2:
                    vpos = take(role)
                ln_v = min(Pv, run_end(vpos) - vpos)                 # тактов вокала
                ln = min(ln_v * 4 * k, beats - t)
                if ln <= 0:
                    break
                seg["phr"].append({"at": t, "va": vpos, "len": ln})
                t += ln
                vpos += ln / (4 * k)
            seg["voc"] = bool(seg["phr"])
            if abs(t - beats) > 1e-6:                     # кусок части без голоса — дальше с начала фразы
                vpos = None
            vrole_prev = role
        else:
            vrole_prev = None
        segs.append(seg)
        used += beats
    return segs


_PAIR = {("chorus", "chorus"): 2.0, ("verse", "verse"): 1.0, ("verse", "chorus"): 0.6, ("chorus", "verse"): 0.25,
         ("chorus", "break"): -0.5, ("verse", "break"): -0.15, ("chorus", "intro"): -0.4, ("verse", "intro"): -0.1,
         ("chorus", "outro"): -0.5, ("verse", "outro"): -0.3}


def _plan_both(A, B, k, variant, opt, tp):
    """Оба трека целиком, как в оригиналах: бит от начала до конца, вокал одним куском от первой
    спетой фразы до последней — без нарезки и перестановок. Решается только, где вокалу вступить:
    перебор мест по сетке фраз бита, чтобы припев вокала лёг на припев бита, куплет — на куплет,
    а голос не вылез за конец бита. «Ещё вариант» — следующее по качеству место."""
    segs = []
    max_beats = opt.max_len * tp["T"] / 60 if opt.max_len > 0 else 1e9
    used = 0.0
    for s in B:
        if used >= max_beats:
            break
        beats = (s["b"] - s["a"]) * 4.0
        segs.append({"role": s["role"], "beats": beats, "voc": False,
                     "beat": [{"at": 0.0, "bar": s["a"], "beats": beats}], "bsec": s, "int": s["int"], "phr": []})
        used += beats
    total_bars = int(round(sum(sg["beats"] for sg in segs) / 4))
    brole = []
    for sg in segs:
        brole += [sg["role"]] * int(round(sg["beats"] / 4))
    runs = _vocal_runs(A)
    v0, v1 = runs[0][0], runs[-1][1]
    if k < 1:
        v0 -= v0 % 2
    nv = int(math.ceil((v1 - v0) * k))                  # тактов проекта под вокал
    vrole = []
    for pb in range(nv):
        vrole.append(_vrole_at(A, v0 + int(pb / k)))
    first_ch = next((i for i, r in enumerate(vrole) if r == "chorus"), None)
    b0 = B[0]["a"]
    grid = (B[1]["a"] - b0) % 4 if len(B) > 1 else 0
    ch_starts = set()
    cur = 0
    for sg in segs:
        if sg["role"] == "chorus":
            ch_starts.add(cur)
        cur += int(round(sg["beats"] / 4))
    cands = []
    for pos in range(0, max(1, total_bars - 4)):
        if (pos - grid) % 4:
            continue
        sc = 0.0
        for i, vr in enumerate(vrole):
            if vr == "inst":
                continue
            pb = pos + i
            if pb >= total_bars:
                sc -= 1.5
                continue
            sc += _PAIR.get((vr, brole[pb]), 0.0)
        if first_ch is not None and pos + first_ch in ch_starts:
            sc += 3.0
        sc -= 0.04 * pos
        cands.append((sc, pos))
    if not cands:
        cands = [(0.0, 0)]
    cands.sort(key=lambda c: -c[0])
    top = cands[:3]
    _sc, pos = top[variant % len(top)]
    st = pos * 4.0
    ln = min(nv * 4.0, total_bars * 4.0 - st)
    cur = 0.0
    for sg in segs:
        a, b = cur, cur + sg["beats"]
        if st < b and st + ln > a:
            sg["voc"] = True
            mid = max(a, st)
            sg["vrole"] = _vrole_at(A, v0 + int((mid - st) / (4 * k)))
            if a <= st < b:
                sg["phr"].append({"at": st - a, "va": v0, "len": ln})
        cur = b
    return segs


# ------------------------------------------------------------------ #
#  Сборка проекта                                                     #
# ------------------------------------------------------------------ #

def build_project(tv: dict, ti: dict, an_v, si_v, an_i, si_i, pl: dict, opt: Options) -> dict:
    """План → проект студии: источники (бит целиком на сетке проекта, вокал целиком на своей
    «виртуальной» сетке), клипы по частям, каналы, микшер, переходы."""
    T = pl["T"]
    k = pl["k"]
    spb = 60.0 / T
    bars_i = np.asarray(si_i["bars"], float)
    bars_v = np.asarray(si_v["bars"], float)
    beats_i = beats_ext(an_i, 8, 16)
    beats_v = beats_ext(an_v, 8, 16)
    name = f"Мэшап: {track_title(tv)} × {track_title(ti)}"
    p = PR.new_project(name[:120], template="none")
    p["bpm"] = round(T, 4)
    p["mode"] = "song"
    p["mashup"] = {"voc": tv.get("path"), "inst": ti.get("path"), "opt": opt.as_dict(), "semis": pl["semis_rel"],
                   "T": T}
    # ── бит целиком: такт 0 бита = доля 0 источника ──
    b0 = float(bars_i[0])
    dur_i = float(an_i["dur"])
    if pl["resample"]:
        src_i_kw = {"trim": [round(b0, 5), round(dur_i + 0.5, 5)], "warp": None, "resample": round(T / pl["ti"], 6)}
    elif not an_i.get("steady"):
        j0 = int(np.argmin(np.abs(beats_i - b0)))
        dst = (np.arange(len(beats_i)) - j0) * spb
        pr = np.stack([dst, beats_i], 1)
        pr = pr[pr[:, 0] >= -4 * spb]
        src_i_kw = {"warp": {"pairs": pr.round(5).tolist(), "len": round(float(pr[-1, 0]), 4), "nfft": 4096},
                    "trim": None}
    else:
        src_i_kw = {"trim": [round(b0, 5), round(dur_i + 0.5, 5)], "warp": None}
    ss_extra = pl["beat_semis"] if not pl["resample"] else 0.0       # тон бита при вокодере — только ускорение
    ch_beat = PR.new_channel("Бит", "audio", insert=1, color="#d8a26c")
    ch_voc = PR.new_channel("Вокал", "audio", insert=2, color="#6c9ad8")
    ch_hook = PR.new_channel("Оригинал бита", "audio", insert=3, color="#b38fd8")
    p["channels"] += [ch_beat, ch_voc]
    for i, nm in ((1, "Бит"), (2, "Вокал"), (3, "Оригинал бита"), (4, "Переходы")):
        p["mixer"][i]["name"] = nm
    for i, nm in enumerate(["Бит", "Вокал", "Оригинал бита", "Переходы"]):
        p["lanes"][i]["name"] = nm
    src_i = PR.new_source(f"Бит — {track_title(ti)}", ti["path"], "inst", semis=ss_extra, formant=False, **src_i_kw)
    src_i["bpm"] = round(T, 4)
    p["sources"].append(src_i)
    src_h = None
    # ── вокал целиком: такт 0 вокала = 0 с источника, доля вокала = k долей проекта ──
    vb0 = float(bars_v[0])
    jv0 = int(np.argmin(np.abs(beats_v - vb0)))
    dst_v = (np.arange(len(beats_v)) - jv0) * k * spb
    prv = np.stack([dst_v, beats_v], 1)
    prv = prv[prv[:, 0] >= -4 * spb]
    semis_v = pl["semis_rel"] + pl["beat_semis"]
    src_v = PR.new_source(f"Вокал — {track_title(tv)}", tv["path"], "vocals",
                          warp={"pairs": prv.round(5).tolist(), "len": round(float(prv[-1, 0]), 4), "nfft": 2048},
                          semis=round(semis_v, 4), formant=True)
    src_v["bpm"] = round(T, 4)
    p["sources"].append(src_v)
    vg = D.undb((si_i["i_rms"] - 3.0) - si_v["v_rms"] + opt.vocal_gain_db)
    vg = float(min(4.0, max(0.25, vg)))
    # ── клипы ──
    cur = 0.0
    voc_clips = []
    hook_end = 0.0
    c0 = 0.0
    for sg in pl["segs"]:
        if sg.get("hook"):
            hook_end = c0 + sg["beats"]
        c0 += sg["beats"]
    h_kw = dict(src_i_kw)
    if h_kw.get("warp"):                                 # голос оригинала нужен только в начале — не тянуть весь трек
        h_kw["warp"] = dict(h_kw["warp"], len=round((hook_end + 2) * spb, 4))
    for sg in pl["segs"]:
        for bp in sg["beat"]:
            src, ch, lane = src_i, ch_beat, 0
            if sg.get("hook"):
                if src_h is None:
                    src_h = PR.new_source(f"Оригинал — {track_title(ti)}", ti["path"], "mix", semis=ss_extra,
                                          formant=False, **h_kw)
                    p["sources"].append(src_h)
                    p["channels"].append(ch_hook)
                src, ch, lane = src_h, ch_hook, 2
            p["clips"].append({"id": PR.uid(), "kind": "audio", "ref": src["id"], "chan": ch["id"], "lane": lane,
                               "start": round(cur + bp["at"], 4), "len": round(bp["beats"], 4),
                               "off": round(bp["bar"] * 4 * spb, 5), "gain": 1.0 if lane == 0 else 0.9,
                               "fin": 0.0, "fout": 0.0})
        if sg.get("voc"):
            if "phr" in sg:
                for ph in sg["phr"]:
                    voc_clips.append((cur + ph["at"], ph["va"], float(ph.get("len", 4 * 4 * k))))
            else:
                voc_clips.append((cur, sg["va"], sg["beats"]))
        sg["start"] = cur
        cur += sg["beats"]
    total = cur
    merged = []                                         # подряд и в вокале, и в проекте — одним клипом
    for st, va, ln in sorted(voc_clips):
        if merged:
            m = merged[-1]
            if abs(st - (m[0] + m[2])) < 1e-6 and abs(va * 4 * k - (m[1] * 4 * k + m[2])) < 1e-6:
                m[2] += ln
                continue
        merged.append([st, va, ln])
    for i, (st, va, ln) in enumerate(merged):
        # затакт перед фразой (до доли) и хвост после — только в тишину между фразами: иначе
        # клипы налезают друг на друга на одной дорожке и голос звучит дважды
        prev_end = merged[i - 1][0] + merged[i - 1][2] if i else -1e9
        next_st = merged[i + 1][0] if i + 1 < len(merged) else 1e9
        pre = max(0.0, min(1.0, (st - prev_end) / 2))
        tail = max(0.0, min(0.75, (next_st - (st + ln)) / 2))
        off = va * 4 * k * spb - pre * spb
        start = st - pre
        if off < 0 or start < 0:
            cut = max(-off / spb, -start)
            off += cut * spb
            start += cut
        end = min(st + ln + tail, total)
        if end - start <= 0.05:
            continue
        p["clips"].append({"id": PR.uid(), "kind": "audio", "ref": src_v["id"], "chan": ch_voc["id"], "lane": 1,
                           "start": round(start, 4), "len": round(end - start, 4), "off": round(off, 5),
                           "gain": round(vg, 3), "fin": round(max(0.05, min(0.5, pre)), 3),
                           "fout": round(max(0.1, min(1.0, tail + 0.25, (end - start) / 4)), 3)})
    # бит: стык несмежных кусков источника — короткие фейды, без щелчка
    bcl = sorted((c for c in p["clips"] if c["lane"] in (0, 2)), key=lambda c: c["start"])
    for a, b in zip(bcl, bcl[1:]):
        if abs(a["start"] + a["len"] - b["start"]) < 1e-3 and \
                abs(a["off"] + a["len"] * spb - b["off"]) > 1e-3:
            a["fout"] = max(a["fout"], 0.05)
            b["fin"] = max(b["fin"], 0.05)
    beat_clips = [c for c in p["clips"] if c["lane"] in (0, 2)]
    if beat_clips:
        last = max(beat_clips, key=lambda c: c["start"])
        last["fout"] = min(8.0, last["len"])
        first = min(beat_clips, key=lambda c: c["start"])
        if pl["segs"][0]["role"] == "intro":
            first["fin"] = min(2.0, first["len"] / 2)
    # ── переходы: райзер и удар перед припевами ──
    if opt.transitions:
        ch_fx = PR.new_channel("Подъём", "sampler", {"sample": "kit:Райзер", "vol": 0.5, "keytrack": 0},
                               insert=4, color="#8fc46c")
        ch_imp = PR.new_channel("Удар", "sampler", {"sample": "kit:Импакт", "vol": 0.55, "keytrack": 0},
                                insert=4, color="#d86c8f")
        p["channels"] += [ch_fx, ch_imp]
        riser_beats = 4.0 * T / 60.0
        pat_r = PR.new_pattern("Подъём", 8.0)
        pat_r["notes"][ch_fx["id"]] = [[round(max(0.0, 8.0 - riser_beats), 4), round(min(riser_beats, 8.0), 4), 60, 0.8]]
        pat_r["color"] = "#5f8a3f"
        pat_i = PR.new_pattern("Удар", 4.0)
        pat_i["notes"][ch_imp["id"]] = [[0.0, 0.25, 60, 0.85]]
        pat_i["color"] = "#8a3f5f"
        p["patterns"] = [pat_r, pat_i]
        prev = None
        for sg in pl["segs"]:
            a = sg["start"]
            if sg["role"] == "chorus" and a >= 8 and (prev is None or prev["role"] != "chorus"):
                p["clips"].append({"id": PR.uid(), "kind": "pat", "ref": pat_r["id"], "lane": 3, "start": float(a - 8),
                                   "len": 8.0})
                p["clips"].append({"id": PR.uid(), "kind": "pat", "ref": pat_i["id"], "lane": 3, "start": float(a),
                                   "len": 4.0})
            prev = sg
        p["sel_pattern"] = pat_r["id"]
    else:
        p["patterns"] = [PR.new_pattern("Паттерн 1")]
        p["sel_pattern"] = p["patterns"][0]["id"]
    # ── микшер ──
    mx = p["mixer"]
    mx[1]["fx"][0] = FX.new_slot("eq", FX.FX_PRESETS["eq"]["Вырез под вокал"])
    mx[2]["fx"][0] = FX.new_slot("eq", {"f1": 140, "g5": 2.5, "f5": 3500, "g6": 2.0, "f6": 10000})
    mx[2]["fx"][1] = FX.new_slot("deesser", {})
    mx[2]["fx"][2] = FX.new_slot("comp", {"thr": -22, "ratio": 3, "att": 6, "rel": 120, "makeup": 3})
    mx[2]["fx"][3] = FX.new_slot("reverb", {"size": 0.35, "damp": 0.35, "pre": 25, "wet": 0.14, "locut": 300})
    mx[2]["fx"][4] = FX.new_slot("delay", {"time": 3, "fb": 0.22, "mix": 0.08, "damp": 3500})
    mx[3]["fx"][0] = FX.new_slot("eq", {"f1": 120})
    mx[4]["fx"][0] = FX.new_slot("reverb", {"size": 0.6, "wet": 0.25})
    mx[4]["vol"] = 0.7
    mx[0]["fx"][0] = FX.new_slot("comp", FX.FX_PRESETS["comp"]["Склейка мастера"])
    mx[0]["fx"][1] = FX.new_slot("limiter", {"gain": 2.0, "ceil": -0.5})
    p["sel_channel"] = ch_voc["id"]
    p["mashup"]["total_beats"] = total
    return p


def explain(tv, ti, si_v, si_i, pl, method) -> str:
    T = pl["T"]
    k = pl["k"]
    lines = [f"Готово: вокал «{track_title(tv)}» на бите «{track_title(ti)}»."]
    tv_eff = pl["tv"] * k
    dv = (T / tv_eff - 1) * 100
    if abs(T - pl["ti"]) < 0.05:
        lines.append(f"Темп {T:.1f} BPM — как у бита. Вокал подогнан по долям: {pl['tv']:.1f}" +
                     (f" (×{k:g} = {tv_eff:.1f})" if k != 1 else "") + f" → {T:.1f} BPM ({dv:+.1f} %).")
    else:
        how = "передискретизацией, как у диджея (барабаны чёткие)" if pl["resample"] else "по долям вокодером"
        lines.append(f"Темп {T:.1f} BPM: бит {pl['ti']:.1f} → {T:.1f} ({(T / pl['ti'] - 1) * 100:+.1f} %, {how}), "
                     f"вокал {pl['tv']:.1f}" + (f" (×{k:g})" if k != 1 else "") + f" → {T:.1f} ({dv:+.1f} %).")
    vk, ik = si_v["vkey"], si_i["ikey"]
    s = pl["semis_rel"]
    bs = pl["beat_semis"]
    if s:
        lines.append(f"Тональность: бит {ik['name']} ({ik['camelot']}), вокал {vk['name']} ({vk['camelot']}) — вокал "
                     f"{'поднят' if s > 0 else 'опущен'} на {abs(s)} пт. → {D.shift_key(vk, s)['name']}, тембр голоса сохранён.")
    else:
        lines.append(f"Тональность: бит {ik['name']} ({ik['camelot']}), вокал {vk['name']} ({vk['camelot']}) — "
                     f"по тону сходятся без сдвига.")
    if abs(bs) > 0.05:
        lines.append(f"Бит из-за смены скорости звучит на {bs:+.2f} пт. — вокал подстроен туда же, строй совпадает.")
    lines.append("Основа — " + {
        "voc": "структура вокальной песни: куплеты и припевы идут как в оригинале, под ними меняются части бита.",
        "beat": "структура бита: его части по порядку, на них — вокал той же роли сплошными кусками.",
        "both": "оба трека целиком, как в оригиналах: бит от начала до конца, вокал одним куском без нарезки, "
                "вступает так, чтобы припев лёг на припев бита."}.get(pl["backbone"], ""))
    spb = 60 / T
    tot = pl["total_beats"] * spb
    lines.append(f"Структура ({int(tot // 60)}:{int(tot % 60):02d}):")
    for sg in pl["segs"]:
        a = sg.get("start", 0) * spb
        bars = sg["beats"] / 4
        bs_role = ROLE_RU.get(sg["bsec"]["role"], "часть") + " бита"
        vr = sg.get("vrole") or sg["role"]
        what = ({"chorus": "припев", "inst": "проигрыш"}.get(vr, "куплет") + " вокала") if sg.get("voc") else \
            ("бит с голосом оригинала" if sg.get("hook") else "только бит")
        lines.append(f"  {int(a // 60)}:{int(a % 60):02d}  {ROLE_RU.get(sg['role'], sg['role'])}, {bars:g} т. — {what}"
                     f" (под ним {bs_role})")
    fit_v = math.exp(-(math.log2(T / tv_eff)) ** 2 / 0.006)
    fit_b = math.exp(-(math.log2(T / pl["ti"])) ** 2 / 0.01)
    score = int(round(100 * (0.45 * (0.6 * fit_v + 0.4 * fit_b) + 0.45 * pl["kfit"] + 0.1)))
    lines.append(f"Совместимость: {score} %.")
    if method != "model":
        lines.append("Вокал отделён без нейросети (нет onnxruntime или интернета) — качество хуже обычного.")
    lines.append("Всё лежит в плейлисте: фразы можно двигать, громкость и эффекты — на дорожках «Вокал» и «Бит». "
                 "«Ещё вариант» — другая раскладка, «поменяй местами» — наоборот.")
    return "\n".join(lines)


# ------------------------------------------------------------------ #
#  Работа целиком (в фоне)                                            #
# ------------------------------------------------------------------ #

def prepare(track: dict, progress=None, cancel=None):
    """Анализ + разделение одного трека. → (an, si, method)."""
    import daw_sep
    path = track["path"]
    an = analyze(path, lambda f, s: progress(f * 0.15, s) if progress else None)
    if cancel is not None and cancel.is_set():
        raise RuntimeError("отменено")
    st = daw_sep.separate(path, lambda f, s: progress(0.15 + f * 0.8, s) if progress else None, cancel)
    si = stem_info(path, st, an)
    method = st["method"]
    del st
    return an, si, method


def make_mashup(tv: dict, ti: dict, opt: Options, progress=None, cancel=None, cache: dict | None = None):
    """Вокал tv + бит ti → (проект, текст объяснения, план)."""
    cache = cache if cache is not None else {}

    def sub(lo, hi, label):
        def f(x, s):
            if progress:
                progress(lo + (hi - lo) * x, f"{label}: {s}")
        return f
    if tv["path"] in cache:
        an_v, si_v, mv = cache[tv["path"]]
    else:
        an_v, si_v, mv = prepare(tv, sub(0.0, 0.48, "Вокал"), cancel)
        cache[tv["path"]] = (an_v, si_v, mv)
    if ti["path"] in cache:
        an_i, si_i, mi = cache[ti["path"]]
    else:
        an_i, si_i, mi = prepare(ti, sub(0.48, 0.96, "Бит"), cancel)
        cache[ti["path"]] = (an_i, si_i, mi)
    if progress:
        progress(0.97, "Собираю аранжировку…")
    pl = plan(an_v, si_v, an_i, si_i, opt)
    p = build_project(tv, ti, an_v, si_v, an_i, si_i, pl, opt)
    text = explain(tv, ti, si_v, si_i, pl, "model" if mv == "model" and mi == "model" else "fallback")
    return p, text, pl


def choose_roles(ta: dict, tb: dict, cache: dict, progress=None, cancel=None):
    """Кто даёт вокал, кто бит: у кого голоса больше."""
    out = []
    for t in (ta, tb):
        if t["path"] not in cache:
            cache[t["path"]] = prepare(t, progress, cancel)
        out.append(cache[t["path"]][1])
    sa, sb = out
    va = float(np.mean(sa["act"])) + (0.15 if sa["has_vocals"] else -0.5)
    vb = float(np.mean(sb["act"])) + (0.15 if sb["has_vocals"] else -0.5)
    return (ta, tb) if va >= vb else (tb, ta)


def suggest_pairs(track: dict, library: list, store, limit=8) -> list:
    """[(оценка, трек, пояснение)] — по сохранённым признакам библиотеки."""
    if store is None:
        return []
    fa = store.audio(track) if hasattr(store, "audio") else None
    if not fa:
        return []
    res = []
    for t in library:
        if t is track or t.get("path") == track.get("path"):
            continue
        fb = store.audio(t)
        if not fb:
            continue
        sc, why = pair_score(fa, fb)
        if sc > 0:
            res.append((sc, t, why))
    res.sort(key=lambda r: -r[0])
    return res[:limit]


# ------------------------------------------------------------------ #
#  Разбор сообщений                                                   #
# ------------------------------------------------------------------ #

_SPLIT = re.compile(r"\s+(?:и|с|со|на|под|plus|vs|x|х|with|and|feat\.?)\s+|\s*[+×,;]\s*", re.I)
_STOP = {"сделай", "сделать", "смешай", "смешать", "соедини", "склей", "мэшап", "мешап", "mashup", "из", "трек",
         "трека", "треков", "песни", "песня", "вокал", "вокалом", "бит", "битом", "минус", "минусом", "пожалуйста",
         "плиз", "please", "make", "a", "the", "давай", "мне", "между", "двух", "двумя", "идеальный", "крутой",
         "подбери", "подобрать", "пару", "к", "для", "что", "подойдёт", "подойдет", "от"}


def _norm(s):
    s = (s or "").lower().replace("ё", "е")
    s = re.sub(r"\(.*?\)|\[.*?\]", " ", s)
    s = re.sub(r"soundloadmate\.com|official|video|audio|lyrics|prod\.?", " ", s)
    return re.sub(r"[^\w\s]", " ", s)


def _words(s):
    return [w for w in _norm(s).split() if w and w not in _STOP]


def find_tracks(query: str, library: list, limit=3) -> list:
    """Лучшие совпадения трека по словам (раскладка и транслит — через search_util)."""
    q = _words(query)
    if not q:
        return []
    try:
        import search_util as SU
        qv = [set(SU.variants(w)) | {w} for w in q]
    except Exception:                                    # noqa: BLE001
        qv = [{w} for w in q]
    res = []
    for t in library:
        hay = _norm(f"{t.get('artist', '')} {t.get('title', '')} {os.path.basename(t.get('path', ''))}")
        hw = set(hay.split())
        sc = 0.0
        for vs in qv:
            if any(v in hw for v in vs):
                sc += 1.0
            elif any(len(v) >= 3 and v in hay for v in vs):
                sc += 0.6
        if sc > 0:
            res.append((sc / len(q) + 0.001 / math.sqrt(max(1, len(hw))), t))
    res.sort(key=lambda r: -r[0])
    return [t for s, t in res[:limit] if s >= 0.5]


def parse(text: str, library: list) -> dict:
    """Сообщение → намерение и параметры."""
    s = (text or "").strip()
    low = s.lower().replace("ё", "е")
    r = {"intent": None, "tracks": [], "opts": {}, "raw": s}
    if not s:
        return r
    if re.search(r"(помощ|help|что умеешь|как пользоваться|команды)", low):
        r["intent"] = "help"
        return r
    if re.search(r"(еще|другой вариант|по-другому|по другому|переделай|не то)", low) and len(low) < 40:
        r["intent"] = "again"
    if re.search(r"(помен|наоборот|swap)", low):
        r["intent"] = "swap"
    if re.search(r"(экспорт|сохрани|в библиотеку|в mp3|в wav|выгрузи)", low):
        r["intent"] = "export"
    if re.search(r"(slowed|замедл|медлен)", low):
        r["opts"]["speed"] = 0.85
    if re.search(r"(sped ?up|speed ?up|ускор|nightcore|найткор|быстре)", low):
        r["opts"]["speed"] = 1.2
    if re.search(r"(нормальн\w* скорост|без ускор|без замедл)", low):
        r["opts"]["speed"] = 1.0
    if re.search(r"(короч|коротк|покороче|2 минут)", low):
        r["opts"]["max_len"] = 125.0
    if re.search(r"(длинн|полн\w* верси|целиком)", low):
        r["opts"]["max_len"] = 0.0
    if re.search(r"(без переход|без райзер|без эффект)", low):
        r["opts"]["transitions"] = False
    if re.search(r"(только припев|одни припевы)", low):
        r["opts"]["chorus_only"] = True
    if re.search(r"(по структуре бита|по биту)", low):
        r["opts"]["backbone"] = "beat"
    if re.search(r"(по структуре вокала|по вокалу|по песне)", low):
        r["opts"]["backbone"] = "voc"
    if re.search(r"(оба целиком|целиком оба|без нарезк|не нарез|не реж|как есть|неизменн|без изменени\w* структур|"
                 r"подряд оба|линейн)", low):
        r["opts"]["backbone"] = "both"
    m = re.search(r"(выше|подними|вверх|ниже|опусти|вниз)\s*(?:на\s*)?([+-]?\d+)?", low)
    if m and ("тон" in low or "вокал" in low or "голос" in low):
        n = int(m.group(2) or 1)
        r["opts"]["extra_semis_delta"] = abs(n) if m.group(1) in ("выше", "подними", "вверх") else -abs(n)
    if re.search(r"(громче вокал|вокал громче|голос громче)", low):
        r["opts"]["vocal_gain_delta"] = 2.5
    if re.search(r"(тише вокал|вокал тише|голос тише)", low):
        r["opts"]["vocal_gain_delta"] = -2.5
    if re.search(r"(подбер|подобрат|что подойд|пару к|пару для|с чем смешать)", low):
        r["intent"] = "suggest"
    parts = None
    m = re.search(r"вокал\w*\s+(?:из\s+|от\s+)?(.+?)\s+(?:на|под|и)\s+(?:бит\w*|минус\w*|музык\w*)?\s*(?:из\s+|от\s+)?(.+)$", low)
    if m:
        parts = [m.group(1), m.group(2)]
        r["order"] = "voc_first"
    else:
        m = re.search(r"(?:бит|минус)\w*\s+(?:из\s+|от\s+)?(.+?)\s+(?:и|с|под|на)\s+вокал\w*\s+(?:из\s+|от\s+)?(.+)$", low)
        if m:
            parts = [m.group(2), m.group(1)]
            r["order"] = "voc_first"
    if parts is None:
        body = re.sub(r"^(сделай|сделать|смешай|соедини|склей|давай|подбери|подобрать)\s+", "", low)
        body = re.sub(r"^(мэшап|мешап|mashup|пару)\s+(из\s+)?(к\s+|для\s+)?", "", body)
        parts = [x for x in _SPLIT.split(body) if x and x.strip()]
    found = []
    for chunk in parts[:3]:
        ts = find_tracks(chunk, library, 1)
        if ts and all(ts[0] is not f for f in found):
            found.append(ts[0])
    r["tracks"] = found
    if r["intent"] is None:
        if len(found) >= 2 or re.search(r"(мэшап|мешап|mashup|смешай|соедини|склей)", low):
            r["intent"] = "mashup"
        elif len(found) == 1:
            r["intent"] = "analyze"
        elif r["opts"]:
            r["intent"] = "again"
    return r


HELP = """Я делаю мэшапы: вокал одного трека на бите другого — с подгонкой темпа по долям, тональности и сведением.

Как просить:
  смешай Numb и In the End
  вокал из Blinding Lights на бит Faint
  подбери пару к One Step Closer
  ещё вариант / поменяй местами
  по структуре бита / по структуре вокала
  оба целиком — бит и вокал как в оригиналах, без нарезки
  slowed / sped up / нормальная скорость
  короче / полная версия / без переходов / только припев
  вокал выше на 1 / вокал ниже на 2 / громче вокал / тише вокал
  экспорт — сохранить готовый мэшап в библиотеку

Можно и без слов: выбери треки в полях «Вокал» и «Бит» и нажми «Сделать мэшап».
Первый раз на каждый трек уходит 1–3 минуты: нейросеть отделяет голос от музыки. Потом — быстро."""
