# daw_sep.py
"""
Разделение трека на вокал и бит (минус) — для мэшап-бота студии.

Основной способ — нейросеть MDX-Net (модель Kim Vocal 2 из открытого набора Ultimate Vocal
Remover, ~67 МБ, качается один раз в ~/.neon_player/studio/models). Запускается через
onnxruntime на процессоре: спектр 7680/1024, куски по ~6 с с обрезкой краёв, как в UVR.
Вокал = выход модели × компенсация, бит = трек − вокал (сумма всегда даёт исходник).

Без onnxruntime или без интернета — запасной способ: голос обычно в центре стерео и тональный,
маска «центр × гармоника» по спектру (заметно хуже, но мэшап собрать можно).

Вокал кэшируется (FLAC) в ~/.neon_player/studio/stems — второй раз трек не делится.
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
from pathlib import Path

import numpy as np

import daw_dsp as D

STUDIO = Path.home() / ".neon_player" / "studio"
MODELS = STUDIO / "models"
STEMS = STUDIO / "stems"
STEMS_KEEP = 60                       # столько разделённых треков хранить

MODEL = {
    "file": "Kim_Vocal_2.onnx",
    "url": "https://github.com/TRvlvr/model_repo/releases/download/all_public_uvr_models/Kim_Vocal_2.onnx",
    "size": 66759214,
    "n_fft": 7680, "hop": 1024, "dim_f": 3072, "dim_t": 256, "comp": 1.009,
}

_LOCK = threading.Lock()              # одна тяжёлая работа за раз
_SESSION = None


def _log(msg):
    try:
        print("[studio sep]", msg)
    except Exception:                 # noqa: BLE001
        pass


def model_file() -> Path:
    return MODELS / MODEL["file"]


def have_runtime() -> bool:
    try:
        import onnxruntime  # noqa: F401
        return True
    except Exception:                 # noqa: BLE001
        return False


def model_ready() -> bool:
    f = model_file()
    return f.exists() and f.stat().st_size == MODEL["size"]


def download_model(progress=None, cancel=None) -> Path:
    import requests
    MODELS.mkdir(parents=True, exist_ok=True)
    dst = model_file()
    if model_ready():
        return dst
    part = dst.with_suffix(".part")
    got = 0
    with requests.get(MODEL["url"], stream=True, timeout=30) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or MODEL["size"])
        with open(part, "wb") as f:
            for c in r.iter_content(1 << 20):
                if cancel is not None and cancel.is_set():
                    raise RuntimeError("отменено")
                f.write(c)
                got += len(c)
                if progress:
                    progress(got / max(1, total))
    if part.stat().st_size != MODEL["size"]:
        part.unlink(missing_ok=True)
        raise RuntimeError("модель скачалась не полностью")
    os.replace(part, dst)
    return dst


def _session():
    global _SESSION
    if _SESSION is None:
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.inter_op_num_threads = 1
        # внутри — физические ядра минус одно: на всех ядрах сразу музыка и интерфейс плеера подтормаживали
        n = os.cpu_count() or 4
        so.intra_op_num_threads = max(1, min(n - 1, max(2, n // 2 - 1)))
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        _SESSION = ort.InferenceSession(str(model_file()), so, providers=["CPUExecutionProvider"])
    return _SESSION


def release():
    """Модель держит ~300 МБ — отпустить, когда мэшап собран."""
    global _SESSION
    _SESSION = None
    try:
        import mem_trim
        mem_trim.trim_later(1500)
    except Exception:                 # noqa: BLE001
        pass


# ------------------------------------------------------------------ #
#  Кэш                                                                #
# ------------------------------------------------------------------ #

def track_key(path) -> str:
    try:
        st = os.stat(path)
        sig = f"{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}"
    except OSError:
        sig = str(path)
    return hashlib.sha1(sig.encode("utf-8", "ignore")).hexdigest()[:20]


def _cache_file(path, method) -> Path:
    return STEMS / f"{track_key(path)}_{method}_vocals.flac"


def cached(path) -> str | None:
    """Какой способ уже есть в кэше: 'model' / 'fallback' / None."""
    for m in ("model", "fallback"):
        if _cache_file(path, m).exists():
            return m
    return None


def _prune():
    try:
        fs = sorted(STEMS.glob("*_vocals.flac"), key=lambda p: p.stat().st_mtime, reverse=True)
        for p in fs[STEMS_KEEP:]:
            p.unlink(missing_ok=True)
    except Exception:                 # noqa: BLE001
        pass


# ------------------------------------------------------------------ #
#  MDX-Net                                                            #
# ------------------------------------------------------------------ #

def _stft(x: np.ndarray, n_fft: int, hop: int) -> np.ndarray:
    """Как torch.stft(center=True, reflect, окно Ханна): (len,) → (bins, frames) complex."""
    pad = n_fft // 2
    xp = np.pad(x, (pad, pad), mode="reflect")
    n = 1 + (len(xp) - n_fft) // hop
    idx = np.arange(n)[:, None] * hop + np.arange(n_fft)[None, :]
    fr = xp[idx] * D.hann(n_fft)
    return np.fft.rfft(fr, axis=1).T


def _istft(S: np.ndarray, n_fft: int, hop: int, length: int) -> np.ndarray:
    """Обратное к _stft (как torch.istft center=True)."""
    win = D.hann(n_fft)
    fr = np.fft.irfft(S.T, n_fft, axis=1) * win
    n = fr.shape[0]
    total = n_fft + hop * (n - 1)
    y = np.zeros(total, np.float64)
    wsum = np.zeros(total, np.float64)
    w2 = win * win
    for i in range(n):
        s = i * hop
        y[s:s + n_fft] += fr[i]
        wsum[s:s + n_fft] += w2
    y /= np.maximum(wsum, 1e-8)
    pad = n_fft // 2
    return y[pad:pad + length].astype(np.float32)


def _mdx(mix: np.ndarray, progress=None, cancel=None) -> np.ndarray:
    """mix (2, n) float32 → вокал (2, n)."""
    M = MODEL
    n_fft, hop, dim_f, dim_t = M["n_fft"], M["hop"], M["dim_f"], M["dim_t"]
    chunk = hop * (dim_t - 1)
    trim = n_fft // 2
    gen = chunk - 2 * trim
    n = mix.shape[1]
    pad = gen + trim - (n % gen)
    mixture = np.concatenate([np.zeros((2, trim), np.float32), mix, np.zeros((2, pad), np.float32)], 1)
    k = mixture.shape[1] // gen
    sess = _session()
    name = sess.get_inputs()[0].name
    out = []
    bins = n_fft // 2 + 1
    t0 = time.perf_counter()
    for i in range(k):
        if cancel is not None and cancel.is_set():
            raise RuntimeError("отменено")
        wav = mixture[:, i * gen:i * gen + chunk]
        if wav.shape[1] < chunk:
            wav = np.pad(wav, ((0, 0), (0, chunk - wav.shape[1])))
        spec = np.stack([_stft(wav[c], n_fft, hop) for c in range(2)])          # (2, bins, frames)
        spec = spec[:, :dim_f, :]
        inp = np.empty((1, 4, dim_f, spec.shape[2]), np.float32)
        inp[0, 0], inp[0, 1] = spec[0].real, spec[0].imag
        inp[0, 2], inp[0, 3] = spec[1].real, spec[1].imag
        inp[:, :, :3, :] = 0.0
        pred = sess.run(None, {name: inp})[0][0]                                 # (4, dim_f, frames)
        full = np.zeros((2, bins, pred.shape[2]), np.complex64)
        full[0, :dim_f] = pred[0] + 1j * pred[1]
        full[1, :dim_f] = pred[2] + 1j * pred[3]
        w = np.stack([_istft(full[c], n_fft, hop, chunk) for c in range(2)])
        out.append(w[:, trim:chunk - trim])
        if progress:
            el = time.perf_counter() - t0
            progress((i + 1) / k, el / (i + 1) * (k - i - 1))
    y = np.concatenate(out, 1)[:, :n]
    return y * M["comp"]


# ------------------------------------------------------------------ #
#  Запасной способ: центр стерео × гармоника                          #
# ------------------------------------------------------------------ #

def _fallback(mix: np.ndarray, progress=None, cancel=None) -> np.ndarray:
    from scipy import ndimage as ndi
    n_fft, hop = 4096, 1024
    n = mix.shape[1]
    L = _stft(mix[0], n_fft, hop)
    R = _stft(mix[1], n_fft, hop)
    if progress:
        progress(0.3, 0)
    if cancel is not None and cancel.is_set():
        raise RuntimeError("отменено")
    pl, pr = np.abs(L) ** 2, np.abs(R) ** 2
    sim = 2 * np.abs(L * np.conj(R)) / (pl + pr + 1e-12)            # 1 — звук ровно в центре
    centre = np.clip((sim - 0.75) / 0.2, 0, 1) ** 2
    Mm = np.abs(0.5 * (L + R))
    harm = ndi.median_filter(Mm, size=(1, 17))                      # вдоль времени — тянущиеся (голос)
    perc = ndi.median_filter(Mm, size=(17, 1))                      # вдоль частот — удары
    hmask = harm ** 2 / (harm ** 2 + perc ** 2 + 1e-12)
    freqs = np.fft.rfftfreq(n_fft, 1.0 / D.SR)[:, None]
    band = ((freqs > 140) & (freqs < 9000)).astype(np.float32)
    mask = np.clip(centre * hmask * band, 0, 1)
    mask = ndi.uniform_filter(mask, size=(3, 3))
    if progress:
        progress(0.7, 0)
    V = 0.5 * (L + R) * mask
    v = _istft(V, n_fft, hop, n)
    return np.stack([v, v])


# ------------------------------------------------------------------ #
#  Главное                                                            #
# ------------------------------------------------------------------ #

def separate(path, progress=None, cancel=None, allow_download=True) -> dict:
    """Трек → {'mix', 'vocals', 'inst'} — float32 (n, 2) на 44.1 кГц, 'method': 'model'/'fallback'.

    progress(доля 0..1, текст) — для окна бота."""
    def prog(f, s):
        if progress:
            try:
                progress(f, s)
            except Exception:         # noqa: BLE001
                pass
    STEMS.mkdir(parents=True, exist_ok=True)
    prog(0.0, "Читаю трек…")
    mix = D.decode(path, D.SR, 2)
    have = cached(path)
    if have == "model" or (have == "fallback" and not (have_runtime() and (model_ready() or allow_download))):
        try:
            voc = D.decode(_cache_file(path, have), D.SR, 2)
            voc = _fit(voc, len(mix))
            os.utime(_cache_file(path, have), None)
            prog(1.0, "Вокал уже отделён раньше")
            return {"mix": mix, "vocals": voc, "inst": mix - voc, "method": have}
        except Exception as e:        # noqa: BLE001
            _log(f"cache read: {e!r}")
    method = "fallback"
    with _LOCK:
        voc = None
        if have_runtime():
            try:
                if not model_ready() and allow_download:
                    prog(0.0, "Качаю нейросеть для отделения вокала (67 МБ, один раз)…")
                    download_model(lambda f: prog(f * 0.15, f"Качаю нейросеть… {int(f * 100)}%"), cancel)
                if model_ready():
                    prog(0.15, "Нейросеть отделяет вокал от бита…")

                    def p2(f, eta):
                        prog(0.15 + 0.83 * f, f"Нейросеть отделяет вокал: {int(f * 100)}%"
                             + (f", осталось ~{int(eta)} с" if eta > 1 else ""))
                    v = _mdx(np.ascontiguousarray(mix.T), p2, cancel)
                    voc = np.ascontiguousarray(v.T)
                    method = "model"
            except RuntimeError as e:
                if "отменено" in str(e):
                    raise
                _log(f"model: {e!r}")
            except Exception as e:    # noqa: BLE001
                _log(f"model: {e!r}")
        if voc is None:
            prog(0.2, "Отделяю вокал по стерео-центру (без нейросети)…")
            v = _fallback(np.ascontiguousarray(mix.T), lambda f, _e: prog(0.2 + 0.7 * f, "Отделяю вокал…"), cancel)
            voc = np.ascontiguousarray(v.T)
    voc = _fit(voc.astype(np.float32), len(mix))
    try:
        D.encode(voc, _cache_file(path, method), fmt="flac")
        _prune()
    except Exception as e:            # noqa: BLE001
        _log(f"cache write: {e!r}")
    prog(1.0, "Вокал отделён")
    return {"mix": mix, "vocals": voc, "inst": mix - voc, "method": method}


def _fit(v: np.ndarray, n: int) -> np.ndarray:
    if len(v) >= n:
        return v[:n]
    return np.concatenate([v, np.zeros((n - len(v), v.shape[1]), np.float32)])
