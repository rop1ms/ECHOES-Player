# genre_ai.py
"""
ИИ жанров ECHOES — распознаёт жанр трека ПО ЗВУКУ и сводит в одно решение всё,
что о жанре известно из интернета.

Три части:

1. Признаки звука (extract_features). Из ~75 секунд середины трека считается
   вектор из 92 чисел: тембр (MFCC 20 × среднее/разброс/динамика), спектральный
   контраст по октавам, распределение энергии (саб-бас / бас / середина /
   верх / «воздух»), яркость, шумность («перегруз» гитар), темп и чёткость
   ритма, доля ударных (разделение гармоник и ударных, HPSS), динамика,
   тональность и лад. Только numpy/scipy — никаких тяжёлых нейросетевых
   библиотек ставить не надо.

2. Модель (GenreModel). Ансамбль из многоклассовой логистической регрессии
   (softmax, обучается тут же градиентным спуском Adam) и k ближайших
   соседей. Точность честно меряется кросс-валидацией (5 фолдов) и
   показывается в настройках. Учится на двух наборах:
     • эталоны — 30-секундные превью Deezer из плейлистов каждого жанра
       (build_references: скачиваются по кнопке, превью удаляются сразу
       после разбора, хранятся только векторы признаков);
     • ваша библиотека — треки, у которых жанр НАДЁЖНО подтверждён онлайн
       (несколько источников согласны). Так модель подстраивается именно
       под вашу музыку и потом угадывает жанр треков, о которых интернет
       ничего не знает (рипы с SoundCloud, неизвестные артисты…).
   Пока модель не обучена, работает «экспертная» эвристика по прототипам
   жанров (темп, бас, яркость, ударные, перегруз) — с маленьким весом.

3. Точный поиск (fuse). Жанры из тегов файла, Deezer (жанр альбома), iTunes,
   MusicBrainz (жанры КОНКРЕТНОЙ записи и релиза — голоса сообщества, а не
   только теги артиста), Last.fm и предсказание модели голосуют с весами:
   надёжность источника × точность совпадения трека × позиция жанра в списке.
   Поджанр поглощает родителя (phonk > hip-hop, metalcore > metal): если
   Deezer говорит «Rap/Hip Hop», а MusicBrainz и звук — «phonk», трек — фонк.
   На выходе — распределение вероятностей и уверенность.
"""
from __future__ import annotations

import json
import math
import os
import random
import tempfile
import threading
import time
from pathlib import Path

import numpy as np

_MEDIAN = []


def _median_filter():
    """scipy.ndimage грузится, только когда реально слушаем новый трек (не при каждом запуске плеера)."""
    if not _MEDIAN:
        try:
            from scipy.ndimage import median_filter as mf
        except Exception:                            # noqa: BLE001
            mf = None
        _MEDIAN.append(mf)
    return _MEDIAN[0]

import music_intel as mi

FEAT_VERSION = 1
MODEL_VERSION = 1
SR, N_FFT, HOP = mi.SR, mi.N_FFT, mi.HOP

FAMILIES = [name for _, name, *_ in mi.GENRE_FAMILIES]

# Поджанр → родитель. Поджанр забирает голоса родителя, если и сам заметен.
PARENT = {
    "metalcore": "metal", "metal": "rock", "punk": "rock", "emo": "rock", "indie": "rock",
    "dreampop": "indie", "phonk": "hiphop", "cloudrap": "hiphop", "drill": "hiphop",
    "trap": "hiphop", "dnb": "electronic", "dubstep": "electronic", "dance": "electronic",
    "synthwave": "electronic", "ambient": "electronic", "hyperpop": "pop", "jpop": "pop",
}

# Надёжность источников жанра.
SOURCE_WEIGHT = {
    "tag": 0.9,          # тег жанра в самом файле
    "deezer": 0.75,      # жанр АЛЬБОМА — грубый
    "itunes": 0.8,       # primaryGenreName — грубый, но точный
    "mb_rec": 1.3,       # MusicBrainz: жанры конкретной записи (голоса)
    "mb_rg": 1.0,        # MusicBrainz: жанры релиза
    "lastfm": 1.1,       # Last.fm: теги трека (+ артиста)
    "mb_artist": 0.45,   # MusicBrainz: теги исполнителя — про артиста, не трек
    "legacy": 0.75,      # старый кэш без разбивки по источникам
    "audio": 1.2,        # модель по звуку (× доверие к модели)
    "prior": 0.35,       # эвристика по звуку (модель ещё не обучена)
}

SOURCE_RU = {"tag": "тег файла", "deezer": "Deezer", "itunes": "iTunes", "mb_rec": "MusicBrainz",
             "mb_rg": "MusicBrainz (релиз)", "lastfm": "Last.fm", "mb_artist": "MusicBrainz (артист)",
             "legacy": "онлайн-кэш", "audio": "ИИ по звуку", "prior": "эвристика по звуку"}


def _log(msg):
    try:
        print(f"[genre-ai] {msg}")
    except Exception:                                # noqa: BLE001
        pass


# ------------------------------------------------------------------ #
#  1. Признаки звука                                                  #
# ------------------------------------------------------------------ #

N_MELS, N_MFCC = 40, 20
_CONTRAST_EDGES = [0, 200, 400, 800, 1600, 3200, 6400, SR / 2]
_BANDS = [(0, 60), (60, 250), (250, 2000), (2000, 6000), (6000, SR / 2)]
FEATURE_NAMES = (
    [f"mfcc{i}_m" for i in range(N_MFCC)] + [f"mfcc{i}_s" for i in range(N_MFCC)]
    + [f"dmfcc{i}_s" for i in range(N_MFCC)] + [f"contrast{i}" for i in range(7)]
    + ["cent_m", "cent_s", "bw_m", "roll_m", "flat_m", "flux_m", "flux_s", "zcr_m", "zcr_s"]
    + ["e_sub", "e_bass", "e_lowmid", "e_highmid", "e_air"]
    + ["bpm_l2", "pulse", "onsets", "tempo_stab", "perc", "loud", "dyn", "crest",
       "chroma_ent", "tonal", "mode"]
)
N_FEAT = len(FEATURE_NAMES)                          # 92

_cache = {}


def _mel_fb():
    if "mel" not in _cache:
        freqs = np.fft.rfftfreq(N_FFT, 1.0 / SR)
        mel = lambda f: 2595.0 * np.log10(1.0 + f / 700.0)
        imel = lambda m: 700.0 * (10 ** (m / 2595.0) - 1.0)
        pts = imel(np.linspace(mel(30.0), mel(11000.0), N_MELS + 2))
        fb = np.zeros((N_MELS, freqs.size), np.float32)
        for i in range(N_MELS):
            l, c, r = pts[i:i + 3]
            up = (freqs - l) / max(c - l, 1e-6)
            down = (r - freqs) / max(r - c, 1e-6)
            fb[i] = np.maximum(0.0, np.minimum(up, down))
        fb /= fb.sum(axis=1, keepdims=True) + 1e-12
        n = np.arange(N_MELS)
        dct = np.cos(np.pi / N_MELS * (n + 0.5)[None, :] * np.arange(N_MFCC)[:, None])
        dct *= np.sqrt(2.0 / N_MELS)
        dct[0] *= 1.0 / np.sqrt(2.0)
        _cache["mel"] = (fb, dct.astype(np.float32), freqs)
    return _cache["mel"]


def extract_features(x: np.ndarray):
    """Сигнал (моно, SR) → (вектор N_FEAT, словарь понятных признаков) или (None, None)."""
    if x is None or x.size < SR * 5:
        return None, None
    x = (x - float(np.mean(x))).astype(np.float32)
    peak = float(np.max(np.abs(x))) + 1e-9
    S = mi._stft_mag(x)
    if S.shape[0] < 40:
        return None, None
    fb, dct, freqs = _mel_fb()
    P = S.astype(np.float64) ** 2
    tot = P.sum(axis=1) + 1e-12
    frame_db = 10 * np.log10(tot / N_FFT + 1e-12)
    active = frame_db > (frame_db.max() - 40)
    if active.sum() < 20:
        return None, None
    Pa, Sa, tota = P[active], S[active], tot[active]

    melspec = Pa @ fb.T.astype(np.float64)
    logmel = 10 * np.log10(melspec + 1e-10)
    mfcc = logmel @ dct.T.astype(np.float64)
    dm = np.diff(mfcc, axis=0)

    contrast = []
    for lo, hi in zip(_CONTRAST_EDGES[:-1], _CONTRAST_EDGES[1:]):
        sel = (freqs >= lo) & (freqs < hi)
        band = np.sort(Sa[:, sel], axis=1)
        k = max(1, int(round(band.shape[1] * 0.2)))
        valley = np.log(band[:, :k].mean(axis=1) + 1e-6)
        peakb = np.log(band[:, -k:].mean(axis=1) + 1e-6)
        contrast.append(float(np.mean(peakb - valley)))

    cent = (Pa * freqs).sum(axis=1) / tota
    bw = np.sqrt((Pa * (freqs[None, :] - cent[:, None]) ** 2).sum(axis=1) / tota)
    cum = np.cumsum(Pa, axis=1)
    roll = freqs[np.minimum(np.argmax(cum >= 0.85 * cum[:, -1:], axis=1), freqs.size - 1)]
    flat = np.exp(np.mean(np.log(Sa + 1e-6), axis=1)) / (Sa.mean(axis=1) + 1e-9)

    bandsel = freqs < 8000
    L = np.log1p(100.0 * S[:, bandsel] / (S[:, bandsel].max() + 1e-9))
    flux = np.concatenate([[0.0], np.maximum(0.0, np.diff(L, axis=0)).sum(axis=1)])
    flux_n = flux / (flux.mean() + 1e-9)
    bpm, pulse = mi._tempo(flux)
    half = flux.size // 2
    b1, _ = mi._tempo(flux[:half]) if half > 200 else (bpm, 0)
    b2, _ = mi._tempo(flux[half:]) if half > 200 else (bpm, 0)
    tempo_stab = float(1.0 - min(1.0, abs(math.log2(max(b1, 1) / max(b2, 1)))))

    frames = np.lib.stride_tricks.sliding_window_view(x, N_FFT)[::HOP][:S.shape[0]]
    zcr = np.mean(np.abs(np.diff(np.sign(frames), axis=1)) > 0, axis=1)[active]

    energies = []
    for lo, hi in _BANDS:
        sel = (freqs >= lo) & (freqs < hi)
        energies.append(float(np.mean(Pa[:, sel].sum(axis=1) / tota)))

    # доля ударных: медианный фильтр по времени (гармоники) против частоты (атаки)
    perc = 0.5
    median_filter = _median_filter()
    if median_filter is not None:
        try:
            M = melspec[: min(melspec.shape[0], 2600)]
            H = median_filter(M, size=(17, 1), mode="nearest")
            Pm = median_filter(M, size=(1, 7), mode="nearest")
            perc = float(np.sum(Pm ** 2 / (H ** 2 + Pm ** 2 + 1e-20) * M) / (np.sum(M) + 1e-20))
        except Exception:                            # noqa: BLE001
            pass

    rms = np.sqrt(tot / N_FFT)
    rms_db = 20 * np.log10(rms[active] + 1e-9)
    loud = float(10 * np.log10(np.mean(rms[active] ** 2) + 1e-12))
    dyn = float(np.percentile(rms_db, 95) - np.percentile(rms_db, 10))
    crest = float(20 * np.log10(peak / (np.sqrt(np.mean(x ** 2)) + 1e-9)))

    sel = (freqs >= 55) & (freqs <= 4200)
    pcs = ((np.round(12 * np.log2(freqs[sel] / 440.0)) + 9) % 12).astype(int)
    chroma = np.zeros(12)
    np.add.at(chroma, pcs, Sa[:, sel].mean(axis=0))
    chroma /= chroma.sum() + 1e-9
    chroma_ent = float(-(chroma * np.log(chroma + 1e-12)).sum() / math.log(12))
    cmaj = [np.corrcoef(chroma, np.roll(mi._KK_MAJOR, k))[0, 1] for k in range(12)]
    cmin = [np.corrcoef(chroma, np.roll(mi._KK_MINOR, k))[0, 1] for k in range(12)]
    tonal = float(max(0.0, max(max(cmaj), max(cmin))))
    mode = float(max(cmaj) - max(cmin))

    vec = np.concatenate([
        mfcc.mean(axis=0), mfcc.std(axis=0), dm.std(axis=0), contrast,
        [math.log(cent.mean() + 1), cent.std() / (cent.mean() + 1), math.log(bw.mean() + 1),
         math.log(roll.mean() + 1), flat.mean(), flux.mean() / bandsel.sum(), flux_n.std(),
         zcr.mean(), zcr.std()],
        energies,
        [math.log2(max(bpm, 30) / 120.0), pulse, float(np.mean(flux_n > 1.6)), tempo_stab, perc,
         loud, dyn, crest, chroma_ent, tonal, mode],
    ]).astype(np.float32)
    if vec.size != N_FEAT or not np.all(np.isfinite(vec)):
        return None, None
    info = {"bpm": float(bpm), "pulse": float(pulse), "low": energies[0] + energies[1] * 0.6,
            "sub": energies[0], "centroid": float(cent.mean()), "perc": perc,
            "flat": float(flat.mean()), "dyn": dyn, "air": energies[4]}
    return vec, info


def analyze_file(path: str, duration: float = 0.0, seconds: float = 75.0):
    x = mi.decode_excerpt(path, duration, seconds=seconds)
    return extract_features(x)


# ------------------------------------------------------------------ #
#  Экспертная эвристика (пока модель не обучена)                       #
# ------------------------------------------------------------------ #

# (bpm, разброс bpm, бас, яркость Гц, ударные, шумность/перегруз, динамика дБ)
PROTOTYPES = {
    "metalcore": (140, 40, .25, 2800, .45, .25, 8), "metal": (130, 35, .22, 2600, .40, .22, 9),
    "phonk": (130, 25, .45, 1800, .50, .12, 8), "dnb": (172, 8, .30, 2200, .55, .12, 9),
    "dubstep": (140, 6, .40, 2000, .50, .15, 10), "hyperpop": (150, 30, .30, 2800, .45, .12, 7),
    "cloudrap": (140, 20, .40, 1500, .40, .08, 9), "drill": (142, 6, .45, 1700, .50, .10, 9),
    "trap": (140, 15, .45, 1800, .50, .10, 9), "hiphop": (90, 12, .38, 1700, .50, .08, 10),
    "emo": (150, 30, .20, 2300, .40, .15, 10), "punk": (170, 25, .18, 2700, .40, .20, 8),
    "dreampop": (110, 25, .20, 1800, .30, .10, 12), "synthwave": (110, 15, .30, 2000, .45, .07, 9),
    "lofi": (80, 10, .30, 1200, .45, .07, 10), "ambient": (90, 40, .20, 1200, .15, .05, 18),
    "dance": (124, 6, .35, 2200, .55, .08, 8), "electronic": (128, 12, .32, 2200, .50, .08, 9),
    "funk": (108, 12, .28, 2000, .50, .06, 12), "jpop": (130, 25, .22, 2400, .40, .08, 9),
    "rnb": (95, 15, .32, 1600, .40, .06, 11), "jazz": (120, 40, .18, 1600, .30, .05, 16),
    "classical": (100, 40, .12, 1300, .12, .04, 22), "soundtrack": (110, 40, .20, 1500, .20, .05, 18),
    "folk": (110, 30, .15, 1600, .25, .05, 14), "latin": (98, 10, .38, 2000, .55, .07, 9),
    "indie": (120, 25, .20, 2200, .40, .10, 11), "rock": (125, 25, .20, 2400, .40, .15, 10),
    "pop": (115, 20, .28, 2100, .45, .07, 9),
}


def prior_predict(info: dict) -> dict:
    """Грубое распределение по жанрам из понятных признаков (без обучения)."""
    if not info:
        return {}
    bpm = max(40.0, float(info.get("bpm") or 0) or 120.0)
    scores = {}
    for fam, (pb, wb, low, cent, perc, flat, dyn) in PROTOTYPES.items():
        # темп с учётом ошибки «в два раза» (у трэпа 70 и 140 — одно и то же)
        dt = min(abs(math.log2(bpm / pb)), abs(math.log2(bpm * 2 / pb)) + 0.15,
                 abs(math.log2(bpm / 2 / pb)) + 0.15)
        d = (dt / max(0.05, math.log2(1 + wb / pb))) ** 2
        d += ((info.get("low", .25) - low) / 0.12) ** 2
        # центроид здесь взвешен по МОЩНОСТИ (бас доминирует) — реальные значения
        # примерно в 2.5 раза ниже «классических» по амплитуде, отсюда 0.4
        d += (math.log(max(info.get("centroid", 800), 80) / (cent * 0.4)) / 0.45) ** 2
        d += ((info.get("perc", .4) - perc) / 0.15) ** 2
        d += ((info.get("flat", .08) - flat) / 0.08) ** 2
        d += ((info.get("dyn", 10) - dyn) / 5.0) ** 2
        scores[fam] = -0.5 * d
    m = max(scores.values())
    ex = {f: math.exp((s - m) / 3.0) for f, s in scores.items()}     # мягко: эвристика
    z = sum(ex.values())
    return {f: v / z for f, v in ex.items()}


# ------------------------------------------------------------------ #
#  2. Модель                                                          #
# ------------------------------------------------------------------ #

class GenreModel:
    """softmax-регрессия + kNN на стандартизованных признаках."""

    def __init__(self):
        self.classes: list[str] = []
        self.mu = self.sd = None
        self.W = self.b = None
        self.X = None                   # обучающие векторы (стандартизованные) — для kNN
        self.y = None
        self.cv = None                  # {"acc": .., "top2": .., "n": .., "folds": ..}
        self.n_ref = self.n_lib = 0
        self.trained_ts = 0

    @property
    def ready(self) -> bool:
        return self.W is not None and len(self.classes) >= 2

    # ── обучение ── #

    @staticmethod
    def _fit_softmax(X, y, C, sw, epochs=400, lr=0.05, l2=2e-3, seed=0):
        rng = np.random.default_rng(seed)
        n, d = X.shape
        W = rng.normal(0, 0.01, (d, C))
        b = np.zeros(C)
        Y = np.eye(C)[y]
        mW, vW, mb, vb = np.zeros_like(W), np.zeros_like(W), np.zeros_like(b), np.zeros_like(b)
        b1, b2 = 0.9, 0.999
        swn = sw / sw.sum()
        for t in range(1, epochs + 1):
            Z = X @ W + b
            Z -= Z.max(axis=1, keepdims=True)
            P = np.exp(Z)
            P /= P.sum(axis=1, keepdims=True)
            G = (P - Y) * swn[:, None]
            gW = X.T @ G + l2 * W
            gb = G.sum(axis=0)
            mW = b1 * mW + (1 - b1) * gW; vW = b2 * vW + (1 - b2) * gW ** 2
            mb = b1 * mb + (1 - b1) * gb; vb = b2 * vb + (1 - b2) * gb ** 2
            W -= lr * (mW / (1 - b1 ** t)) / (np.sqrt(vW / (1 - b2 ** t)) + 1e-8)
            b -= lr * (mb / (1 - b1 ** t)) / (np.sqrt(vb / (1 - b2 ** t)) + 1e-8)
        return W, b

    def fit(self, X, labels, weights=None, cv=True):
        X = np.asarray(X, np.float64)
        labels = list(labels)
        weights = np.ones(len(labels)) if weights is None else np.asarray(weights, float)
        counts = {}
        for l in labels:
            counts[l] = counts.get(l, 0) + 1
        self.classes = sorted(c for c, n in counts.items() if n >= 5)
        keep = np.array([l in self.classes for l in labels])
        if len(self.classes) < 2 or keep.sum() < 12:
            self.W = None
            return False
        X, w = X[keep], weights[keep]
        y = np.array([self.classes.index(l) for l, k in zip(labels, keep) if k])
        self.mu = X.mean(axis=0)
        self.sd = X.std(axis=0) + 1e-6
        Xs = np.clip((X - self.mu) / self.sd, -6, 6)
        cls_w = np.array([1.0 / np.sum(y == c) for c in range(len(self.classes))])
        sw = w * cls_w[y]
        if cv and len(y) >= 30:
            self.cv = self._cross_validate(Xs, y, sw)
        self.W, self.b = self._fit_softmax(Xs, y, len(self.classes), sw)
        self.X, self.y = Xs.astype(np.float32), y
        self.trained_ts = int(time.time())
        return True

    def _cross_validate(self, Xs, y, sw, folds=5):
        idx = np.arange(len(y))
        rng = np.random.default_rng(42)
        fold_of = np.empty(len(y), int)
        for c in np.unique(y):                       # стратификация по классам
            ci = rng.permutation(idx[y == c])
            fold_of[ci] = np.arange(ci.size) % folds
        hit = hit2 = 0
        for f in range(folds):
            tr, te = fold_of != f, fold_of == f
            if te.sum() == 0 or len(np.unique(y[tr])) < 2:
                continue
            W, b = self._fit_softmax(Xs[tr], y[tr], len(self.classes), sw[tr], epochs=250)
            P = self._proba(Xs[te], W, b, Xs[tr], y[tr])
            order = np.argsort(-P, axis=1)
            hit += int(np.sum(order[:, 0] == y[te]))
            hit2 += int(np.sum((order[:, :2] == y[te][:, None]).any(axis=1)))
        n = len(y)
        return {"acc": round(hit / n, 4), "top2": round(hit2 / n, 4), "n": n, "folds": folds,
                "chance": round(1.0 / len(self.classes), 4)}

    def _proba(self, Xs, W, b, Xtr, ytr, k=9):
        Z = Xs @ W + b
        Z -= Z.max(axis=1, keepdims=True)
        Ps = np.exp(Z)
        Ps /= Ps.sum(axis=1, keepdims=True)
        # kNN по косинусу
        A = Xs / (np.linalg.norm(Xs, axis=1, keepdims=True) + 1e-9)
        B = Xtr / (np.linalg.norm(Xtr, axis=1, keepdims=True) + 1e-9)
        sim = A @ B.T
        k = min(k, B.shape[0])
        nn = np.argpartition(-sim, k - 1, axis=1)[:, :k]
        Pk = np.full_like(Ps, 1e-3)
        for i in range(Xs.shape[0]):
            for j in nn[i]:
                Pk[i, ytr[j]] += math.exp(float(sim[i, j]) / 0.15)
        Pk /= Pk.sum(axis=1, keepdims=True)
        return 0.6 * Ps + 0.4 * Pk

    # ── предсказание ── #

    def predict(self, vec) -> dict:
        if not self.ready or vec is None:
            return {}
        v = np.asarray(vec, np.float64).reshape(1, -1)
        if v.shape[1] != self.mu.size:
            return {}
        Xs = np.clip((v - self.mu) / self.sd, -6, 6)
        p = self._proba(Xs, self.W, self.b, self.X.astype(np.float64), self.y)[0]
        return {c: float(p[i]) for i, c in enumerate(self.classes)}

    def trust(self) -> float:
        """Насколько верить модели (0.3..1.0) — по честной кросс-валидации."""
        if not self.ready:
            return 0.0
        if not self.cv:
            return 0.5
        ch = self.cv.get("chance", 0.1)
        return float(min(1.0, max(0.3, (self.cv["acc"] - ch) / max(1e-6, 1 - ch) + 0.25)))

    # ── сохранение ── #

    def to_json(self) -> dict:
        if not self.ready:
            return {"v": MODEL_VERSION, "ready": False}
        r = lambda a, n=4: np.round(np.asarray(a, float), n).tolist()
        return {"v": MODEL_VERSION, "feat_v": FEAT_VERSION, "ready": True, "classes": self.classes,
                "mu": r(self.mu, 5), "sd": r(self.sd, 5), "W": r(self.W), "b": r(self.b),
                "X": r(self.X, 3), "y": self.y.tolist(), "cv": self.cv, "n_ref": self.n_ref,
                "n_lib": self.n_lib, "ts": self.trained_ts}

    @classmethod
    def from_json(cls, d) -> "GenreModel":
        m = cls()
        if not d or not d.get("ready") or d.get("v") != MODEL_VERSION or d.get("feat_v") != FEAT_VERSION:
            return m
        m.classes = list(d["classes"])
        m.mu, m.sd = np.array(d["mu"]), np.array(d["sd"])
        m.W, m.b = np.array(d["W"]), np.array(d["b"])
        m.X, m.y = np.array(d["X"], np.float32), np.array(d["y"], int)
        m.cv, m.n_ref, m.n_lib, m.trained_ts = d.get("cv"), d.get("n_ref", 0), d.get("n_lib", 0), d.get("ts", 0)
        return m


# ------------------------------------------------------------------ #
#  3. Слияние источников                                              #
# ------------------------------------------------------------------ #

def _vote(acc: dict, src_hits: dict, names, src: str, weight: float, counts=None):
    """Голос одного источника: список жанров → семейства с весами по позиции/голосам."""
    if not names:
        return
    total_c = sum(counts) if counts else 0
    seen = set()
    for i, raw in enumerate(names):
        fams = mi.genre_families([raw])
        if not fams or fams[0] in seen:
            continue
        f = fams[0]
        seen.add(f)
        if counts and total_c > 0:
            w = weight * (0.35 + 0.65 * counts[i] / max(counts))
        else:
            w = weight / (1.0 + 0.35 * len(seen) - 0.35)
        acc[f] = acc.get(f, 0.0) + w
        src_hits.setdefault(f, set()).add(src)


def _absorb_parents(acc: dict):
    """Поджанр забирает голоса родителя, если и сам набрал ≥ 25% от него."""
    def depth(f):
        d = 0
        while f in PARENT and d < 5:
            f = PARENT[f]; d += 1
        return d
    for child in sorted(acc, key=depth, reverse=True):
        p = PARENT.get(child)
        if p and acc.get(p, 0) > 0 and acc[child] >= 0.25 * acc[p]:
            moved = 0.7 * acc[p]
            acc[child] += moved
            acc[p] -= moved
    return acc


def online_evidence(track: dict, meta: dict | None, artist: dict | None):
    """Голоса онлайн-источников и тега файла (без звука) → (acc, src_hits, weight_sum)."""
    acc, hits = {}, {}
    tag = str(track.get("genre", "") or "").strip()
    if tag:
        _vote(acc, hits, [g.strip() for g in tag.replace(";", "/").split("/") if g.strip()],
              "tag", SOURCE_WEIGHT["tag"])
    m = meta or {}
    by = m.get("by_src")
    if by:
        for key, d in by.items():
            if key not in SOURCE_WEIGHT or not d:
                continue
            sc = float(d.get("score", 1.0) or 1.0)
            w = SOURCE_WEIGHT[key] * max(0.5, min(1.0, (sc - 0.6) / 0.35 if sc < 0.95 else 1.0))
            _vote(acc, hits, d.get("g") or [], key, w, d.get("c"))
    else:
        _vote(acc, hits, m.get("genres") or [], "legacy", SOURCE_WEIGHT["legacy"])
        _vote(acc, hits, m.get("tags") or [], "lastfm", SOURCE_WEIGHT["lastfm"] * 0.8)
    if artist and artist.get("tags"):
        _vote(acc, hits, artist["tags"], "mb_artist", SOURCE_WEIGHT["mb_artist"])
    return acc, hits, sum(acc.values())


def fuse(track: dict, meta: dict | None, artist: dict | None, audio_pred: dict | None = None,
         audio_src: str = "audio", audio_trust: float = 1.0) -> dict:
    """Итоговое решение по жанру трека.

    → {"family", "families", "conf", "dist", "sources"} (пусто, если ничего не известно)."""
    acc, hits, online_w = online_evidence(track, meta, artist)
    # поджанр поглощает родителя только среди ОНЛАЙН-голосов: модель по звуку
    # сама различает metal/metalcore, её вероятности не перераспределяем
    _absorb_parents(acc)
    if audio_pred:
        w = SOURCE_WEIGHT[audio_src] * audio_trust
        # когда онлайн уверен, звук только уточняет; когда онлайн молчит — решает звук
        if online_w > 1.5:
            w *= 0.6
        for f, p in audio_pred.items():
            if p > 0.04:
                acc[f] = acc.get(f, 0.0) + w * p
                if p > 0.2:
                    hits.setdefault(f, set()).add(audio_src)
    acc = {f: v for f, v in acc.items() if v > 1e-6}
    if not acc:
        return {}
    z = sum(acc.values())
    dist = {f: v / z for f, v in sorted(acc.items(), key=lambda kv: -kv[1])}
    top = next(iter(dist))
    agree = len(hits.get(top, ()))
    conf = dist[top] * (1 - math.exp(-z / 1.2)) * min(1.0, 0.6 + 0.2 * agree)
    fams = [f for f, p in dist.items() if p >= 0.15][:3] or [top]
    return {"family": top, "families": fams, "conf": round(float(conf), 3),
            "dist": {f: round(p, 3) for f, p in list(dist.items())[:5]},
            "sources": sorted(hits.get(top, ()))}


def training_label(track, meta, artist):
    """Метка для обучения: только если онлайн-источники надёжно согласны."""
    r = fuse(track, meta, artist)
    if not r:
        return None
    acc, hits, w = online_evidence(track, meta, artist)
    srcs = hits.get(r["family"], set()) - {"mb_artist"}
    if w >= 0.8 and r["dist"][r["family"]] >= 0.55 and (len(srcs) >= 2 or w >= 1.3):
        return r["family"]
    return None


# ------------------------------------------------------------------ #
#  Хранилище модели и эталонов                                        #
# ------------------------------------------------------------------ #

class GenreAI:
    """Модель + эталоны + обучение. Один экземпляр на приложение (get_ai)."""

    def __init__(self, data_dir: Path):
        self.dir = Path(data_dir)
        self.model_path = self.dir / "genre_ai.json"
        self.refs_path = self.dir / "genre_refs.json"
        self.lock = threading.RLock()
        self.model = GenreModel()
        self.refs: list = []              # [{"f": family, "x": [...], "id": deezer_id}]
        try:
            self.model = GenreModel.from_json(json.loads(self.model_path.read_text("utf-8")))
        except Exception:                            # noqa: BLE001
            pass
        try:
            d = json.loads(self.refs_path.read_text("utf-8"))
            if d.get("feat_v") == FEAT_VERSION:
                self.refs = list(d.get("refs") or [])
        except Exception:                            # noqa: BLE001
            pass

    def _save_json(self, path, obj):
        try:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(obj, ensure_ascii=False), "utf-8")
            os.replace(tmp, path)
        except Exception as e:                       # noqa: BLE001
            _log(f"save {path.name}: {e!r}")

    def save_refs(self):
        with self.lock:
            self._save_json(self.refs_path, {"feat_v": FEAT_VERSION, "refs": self.refs})

    # ── предсказание для профиля трека ── #

    def predict_track(self, store, track) -> tuple[dict, str, float]:
        """(распределение, источник, доверие) по звуку трека — или ({}, "", 0)."""
        e = store.ai_entry(track)
        if not e:
            return {}, "", 0.0
        with self.lock:
            model = self.model
        if model.ready and e.get("x"):
            p = model.predict(e["x"])
            if p:
                return p, "audio", model.trust()
        if e.get("info"):
            return prior_predict(e["info"]), "prior", 1.0
        return {}, "", 0.0

    MIN_CONF = 0.12            # ниже — жанр считаем неизвестным (не засоряем волны/EQ)

    def genre_of(self, store, track) -> dict:
        meta, artist = store.meta(track)
        pred, src, trust = self.predict_track(store, track)
        r = fuse(track, meta, artist, pred or None, src or "audio", trust)
        if r and r["conf"] < self.MIN_CONF:
            r = dict(r, families=[])
        return r

    # ── обучение ── #

    def train(self, library, store) -> dict:
        X, labels, weights = [], [], []
        with self.lock:
            refs = list(self.refs)
        for r in refs:
            if r.get("f") in FAMILIES and len(r.get("x") or []) == N_FEAT:
                X.append(r["x"]); labels.append(r["f"]); weights.append(1.0)
        n_ref = len(X)
        for t in library:
            e = store.ai_entry(t)
            if not e or len(e.get("x") or []) != N_FEAT:
                continue
            meta, artist = store.meta(t)
            lab = training_label(t, meta, artist)
            if lab:
                X.append(e["x"]); labels.append(lab); weights.append(1.5)   # своя музыка важнее
        n_lib = len(X) - n_ref
        m = GenreModel()
        ok = bool(X) and m.fit(np.array(X), labels, weights)
        m.n_ref, m.n_lib = n_ref, n_lib
        if ok:
            with self.lock:
                self.model = m
            self._save_json(self.model_path, m.to_json())
        _log(f"train: refs={n_ref} lib={n_lib} ok={ok} cv={m.cv}")
        return self.status()

    def status(self) -> dict:
        with self.lock:
            m = self.model
            n_refs = len(self.refs)
        return {"ready": m.ready, "classes": list(m.classes), "cv": m.cv, "n_ref": m.n_ref,
                "n_lib": m.n_lib, "refs": n_refs, "ts": m.trained_ts}

    def status_text(self) -> str:
        s = self.status()
        if not s["ready"]:
            base = "Модель по звуку ещё не обучена — пока работает эвристика и онлайн-поиск."
            if s["refs"]:
                base += f" Эталонов: {s['refs']}."
            return base
        cv = s["cv"]
        acc = (f"точность {cv['acc'] * 100:.0f}% (топ-2: {cv['top2'] * 100:.0f}%, "
               f"случайно было бы {cv['chance'] * 100:.0f}%)") if cv else "точность ещё не измерена"
        return (f"Модель обучена: {len(s['classes'])} жанров, {s['n_ref']} эталонов + "
                f"{s['n_lib']} треков вашей библиотеки; {acc}.")

    # ── эталоны Deezer ── #

    def build_references(self, per_family=12, progress=None, stop=None) -> int:
        """Скачивает превью Deezer из плейлистов каждого жанра и превращает их
        в обучающие векторы. Превью удаляются сразу после разбора."""
        import requests
        progress = progress or (lambda *a: None)
        stop = stop or threading.Event()
        with self.lock:
            have = {(r.get("f"), r.get("id")) for r in self.refs}
            per_have = {}
            for r in self.refs:
                per_have[r.get("f")] = per_have.get(r.get("f"), 0) + 1
        jobs = []
        for fam in FAMILIES:
            need = per_family - per_have.get(fam, 0)
            if need <= 0:
                continue
            tracks = _reference_tracks(fam, need * 3)
            random.Random(fam).shuffle(tracks)
            artists = {}
            for t in tracks:
                if (fam, t["id"]) in have:
                    continue
                a = t.get("artist", "")
                if artists.get(a, 0) >= 2:           # не больше двух треков одного артиста
                    continue
                artists[a] = artists.get(a, 0) + 1
                jobs.append((fam, t))
                if sum(1 for f, _ in jobs if f == fam) >= need:
                    break
            if stop.is_set():
                return 0
        added = 0
        tmpdir = Path(tempfile.mkdtemp(prefix="echoes_refs_"))
        try:
            for i, (fam, t) in enumerate(jobs):
                if stop.is_set():
                    break
                progress(i, len(jobs), f"{mi.GENRE_RU.get(fam, fam)}: {t.get('title', '')}")
                f = tmpdir / f"{t['id']}.mp3"
                try:
                    r = requests.get(t["preview"], timeout=20, headers={"User-Agent": mi.UA})
                    if r.status_code != 200 or len(r.content) < 20000:
                        continue
                    f.write_bytes(r.content)
                    x = mi.decode_excerpt(str(f), 0.0, seconds=30.0)
                    vec, _ = extract_features(x)
                except Exception as e:               # noqa: BLE001
                    _log(f"ref {t.get('id')}: {e!r}")
                    vec = None
                finally:
                    try:
                        f.unlink()
                    except OSError:
                        pass
                if vec is None:
                    continue
                with self.lock:
                    self.refs.append({"f": fam, "id": t["id"], "x": np.round(vec, 4).tolist()})
                added += 1
                if added % 20 == 0:
                    self.save_refs()
        finally:
            try:
                for p in tmpdir.iterdir():
                    p.unlink()
                tmpdir.rmdir()
            except OSError:
                pass
        self.save_refs()
        return added


# Поисковые запросы плейлистов Deezer для эталонов каждого жанра.
REFERENCE_QUERIES = {
    "metalcore": ["metalcore", "deathcore"], "metal": ["heavy metal", "thrash metal"],
    "phonk": ["phonk", "drift phonk"], "dnb": ["drum and bass", "breakcore"],
    "dubstep": ["dubstep", "riddim"], "hyperpop": ["hyperpop", "digicore"],
    "cloudrap": ["cloud rap", "emo rap"], "drill": ["uk drill", "drill"],
    "trap": ["trap hits", "trap"], "hiphop": ["boom bap", "hip hop classics"],
    "emo": ["emo", "midwest emo"], "punk": ["punk rock", "pop punk"],
    "dreampop": ["dream pop", "shoegaze"], "synthwave": ["synthwave", "retrowave"],
    "lofi": ["lofi hip hop", "lofi beats"], "ambient": ["ambient", "ambient relax"],
    "dance": ["house music", "dance hits"], "electronic": ["techno", "electronic"],
    "funk": ["funk classics", "funk"], "jpop": ["j-pop", "anime songs"],
    "rnb": ["r&b", "neo soul"], "jazz": ["jazz classics", "jazz"],
    "classical": ["classical music", "piano classical"], "soundtrack": ["film soundtrack", "epic soundtrack"],
    "folk": ["folk acoustic", "indie folk"], "latin": ["reggaeton", "latin hits"],
    "indie": ["indie rock", "alternative indie"], "rock": ["rock classics", "hard rock"],
    "pop": ["pop hits", "top pop"],
}


def _reference_tracks(fam: str, want: int) -> list:
    out, seen = [], set()
    for q in REFERENCE_QUERIES.get(fam, [fam]):
        j = mi._jget("deezer", "https://api.deezer.com/search/playlist", {"q": q, "limit": 3})
        for pl in (j or {}).get("data") or []:
            pid = pl.get("id")
            if not pid:
                continue
            jt = mi._jget("deezer", f"https://api.deezer.com/playlist/{pid}/tracks", {"limit": 60})
            for t in (jt or {}).get("data") or []:
                if t.get("id") in seen or not t.get("preview"):
                    continue
                seen.add(t.get("id"))
                out.append({"id": t["id"], "preview": t["preview"], "title": t.get("title", ""),
                            "artist": (t.get("artist") or {}).get("name", "")})
            if len(out) >= want:
                return out
    return out


_AI = None
_AI_LOCK = threading.Lock()


def get_ai(data_dir=None) -> GenreAI | None:
    """Общий экземпляр (создаётся при первом вызове с data_dir)."""
    global _AI
    with _AI_LOCK:
        if _AI is None and data_dir is not None:
            _AI = GenreAI(Path(data_dir))
        return _AI
