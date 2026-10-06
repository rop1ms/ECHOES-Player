# lyrics_sync.py
"""
ИИ-синхронизация текста с треком: нейросеть Whisper (faster-whisper, локально,
без отправки аудио в интернет) «слушает» песню и находит, в какую секунду
звучит каждое слово. Затем ваш текст построчно выравнивается по этим словам
(алгоритм Нидлмана — Вунша с нечётким сравнением, поэтому ошибки распознавания
не страшны), и получается LRC с таймкодами.

Модель скачивается один раз при первом запуске (small ≈ 460 МБ).
"""
from __future__ import annotations

import difflib
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

_TS = re.compile(r"\[\d{1,3}:\d{2}(?:[.:]\d{1,3})?\]")
_SECTION = re.compile(r"^\s*[\[(][^\])]{1,40}[\])]\s*$")       # [Припев], (Chorus)
_WORD = re.compile(r"[^\W_]+", re.UNICODE)

MODEL_DEFAULT = "small"
MATCH_MIN = 0.7                 # похожесть слов, с которой считаем их «тем же словом»


class SyncCancelled(Exception):
    pass


# ── установка / загрузка ── #

def whisper_installed() -> bool:
    try:
        import faster_whisper                        # noqa: F401
        return True
    except Exception:                                # noqa: BLE001
        return False


def install_whisper(log=None) -> bool:
    """pip install faster-whisper в тот же Python, на котором работает плеер."""
    exe = Path(sys.executable)
    py = exe.with_name("python.exe") if exe.with_name("python.exe").exists() else exe
    flags = 0x08000000 if os.name == "nt" else 0       # CREATE_NO_WINDOW
    try:
        p = subprocess.Popen([str(py), "-m", "pip", "install", "--prefer-binary", "--no-warn-script-location",
                              "--disable-pip-version-check", "faster-whisper"],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             encoding="utf-8", errors="replace", creationflags=flags)
        for line in p.stdout:                        # type: ignore[union-attr]
            line = line.strip()
            if log and line.startswith(("Collecting", "Downloading", "Installing", "Successfully")):
                log(line[:90])
        p.wait()
        if p.returncode != 0:
            return False
    except Exception as e:                           # noqa: BLE001
        print("[sync] pip:", e)
        return False
    import importlib
    importlib.invalidate_caches()
    return whisper_installed()


# ── подготовка текста ── #

def plain_lines(text: str) -> list[str]:
    """Строки текста без таймкодов, заголовков секций и пустых строк."""
    out = []
    for raw in (text or "").splitlines():
        s = _TS.sub("", raw).strip()
        if not s or _SECTION.match(s):
            continue
        if re.match(r"^\[(ar|ti|al|by|offset|length|re|ve):", s, re.I):
            continue
        out.append(s)
    return out


def _words(s: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(s)]


def _sim(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    if difflib.SequenceMatcher(None, a, b).quick_ratio() < MATCH_MIN:
        return 0.0
    r = difflib.SequenceMatcher(None, a, b).ratio()
    return r if r >= MATCH_MIN else 0.0


def _guess_language(text: str):
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return None
    cyr = sum(1 for c in letters if "Ѐ" <= c <= "ӿ")
    return "ru" if cyr / len(letters) > 0.5 else None


# ── выравнивание ── #

def align_lines(lines: list[str], heard: list[tuple[str, float, float]]) -> list[float]:
    """lines — строки текста; heard — слова, услышанные Whisper: (слово, начало, конец).
    Возвращает время начала каждой строки (сек), по возрастанию."""
    L = []                                           # (слово, номер строки, позиция в строке)
    for li, line in enumerate(lines):
        for k, w in enumerate(_words(line)):
            L.append((w, li, k))
    T = [(w, s, e) for w, s, e in heard if w]
    n, m = len(L), len(T)
    anchor: dict[int, tuple[float, int]] = {}        # строка -> (время слова, его позиция в строке)
    if n and m:
        GAP = -0.5
        score = [[0.0] * (m + 1) for _ in range(n + 1)]
        move = [[0] * (m + 1) for _ in range(n + 1)]   # 1 — диагональ, 2 — вверх, 3 — влево
        for i in range(1, n + 1):
            score[i][0], move[i][0] = i * GAP, 2
        for j in range(1, m + 1):
            score[0][j], move[0][j] = j * GAP, 3
        simc: dict[tuple[str, str], float] = {}
        for i in range(1, n + 1):
            lw = L[i - 1][0]
            for j in range(1, m + 1):
                key = (lw, T[j - 1][0])
                sm = simc.get(key)
                if sm is None:
                    sm = simc[key] = _sim(*key)
                diag = score[i - 1][j - 1] + (3.0 * sm - 1.0 if sm else -1.0)
                up, left = score[i - 1][j] + GAP, score[i][j - 1] + GAP
                if diag >= up and diag >= left:
                    score[i][j], move[i][j] = diag, 1
                elif up >= left:
                    score[i][j], move[i][j] = up, 2
                else:
                    score[i][j], move[i][j] = left, 3
        i, j = n, m
        while i > 0 or j > 0:
            mv = move[i][j]
            if mv == 1:
                if simc.get((L[i - 1][0], T[j - 1][0])):
                    w, li, k = L[i - 1]
                    if li not in anchor or k < anchor[li][1]:
                        anchor[li] = (T[j - 1][1], k)
                i, j = i - 1, j - 1
            elif mv == 2:
                i -= 1
            else:
                j -= 1

    # время начала строки по её опорному слову (минус ~0.3 с на каждое слово перед ним)
    nl = len(lines)
    times: list[float | None] = [None] * nl
    for li, (t, k) in anchor.items():
        times[li] = max(0.0, t - 0.3 * k)
    # строки без опоры — равномерно между соседями (по числу слов)
    wc = [max(1, len(_words(x))) for x in lines]
    known = [i for i in range(nl) if times[i] is not None]
    if not known:
        end = heard[-1][2] if heard else 3.0 * nl
        start = heard[0][1] if heard else 0.0
        tot = sum(wc)
        acc = 0
        for i in range(nl):
            times[i] = start + (end - start) * acc / tot
            acc += wc[i]
    else:
        first, last = known[0], known[-1]
        for i in range(first - 1, -1, -1):           # до первой опоры — шагом ~2.5 с назад
            times[i] = max(0.0, times[i + 1] - 2.5)       # type: ignore[operator]
        for i in range(last + 1, nl):                  # после последней — шагом ~3 с вперёд
            times[i] = times[i - 1] + 3.0                  # type: ignore[operator]
        for a, b in zip(known, known[1:]):
            if b - a > 1:
                tot = sum(wc[a:b])
                acc = wc[a]
                for i in range(a + 1, b):
                    times[i] = times[a] + (times[b] - times[a]) * acc / tot   # type: ignore[operator]
                    acc += wc[i]
    out, prev = [], -1.0
    for t in times:                                   # строго по возрастанию
        t = max(float(t or 0.0), prev + 0.2)
        out.append(t)
        prev = t
    return out


def build_lrc(lines: list[str], times: list[float]) -> str:
    rows = []
    for line, t in zip(lines, times):
        m, s = divmod(t, 60)
        rows.append(f"[{int(m):02d}:{s:05.2f}] {line}")
    return "\n".join(rows)


# ── основной вызов ── #

_MODEL = {}


def _ffmpeg() -> str | None:
    p = shutil.which("ffmpeg")
    if p:
        return p
    local = Path(__file__).resolve().parent.parent / "ffmpeg" / "ffmpeg.exe"
    return str(local) if local.exists() else None


def _decode(path: str):
    """Трек -> моно 16 кГц float32 через ffmpeg (минуя PyAV: у него бывают конфликты версий)."""
    import numpy as np
    exe = _ffmpeg()
    if not exe:
        return path                                  # пусть faster-whisper декодирует сам
    flags = 0x08000000 if os.name == "nt" else 0
    r = subprocess.run([exe, "-nostdin", "-loglevel", "error", "-i", path, "-vn", "-f", "f32le",
                        "-ac", "1", "-ar", "16000", "-"], capture_output=True, creationflags=flags)
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError("не удалось прочитать аудио: " + r.stderr.decode("utf-8", "replace")[:150])
    return np.frombuffer(r.stdout, dtype=np.float32)


def _load_model(size: str, progress):
    if size in _MODEL:
        return _MODEL[size]
    from faster_whisper import WhisperModel
    progress("Загружаю модель распознавания (первый раз — скачивание, до ~460 МБ)…")
    cpu = os.cpu_count() or 4
    mdl = WhisperModel(size, device="cpu", compute_type="int8", cpu_threads=max(2, cpu - 1))
    _MODEL[size] = mdl
    return mdl


def sync_lyrics(audio_path: str, text: str, progress=None, cancel: threading.Event | None = None,
                model: str = MODEL_DEFAULT) -> str:
    """Возвращает LRC-текст. progress(str) — статус для интерфейса."""
    progress = progress or (lambda s: None)
    lines = plain_lines(text)
    if not lines:
        raise ValueError("Сначала впишите или найдите текст песни")
    if not Path(audio_path).exists():
        raise FileNotFoundError("Файл трека не найден")
    mdl = _load_model(model, progress)
    progress("Слушаю трек… 0%")
    prompt = " ".join(lines[:2])[:200]
    segs, info = mdl.transcribe(_decode(audio_path), language=_guess_language(text), word_timestamps=True,
                                vad_filter=False, beam_size=1, condition_on_previous_text=False,
                                initial_prompt=prompt)
    dur = float(getattr(info, "duration", 0) or 0)
    heard = []
    for sg in segs:
        if cancel is not None and cancel.is_set():
            raise SyncCancelled()
        for w in (sg.words or []):
            for sub in _words(w.word):
                heard.append((sub, float(w.start), float(w.end)))
        if dur:
            progress(f"Слушаю трек… {min(99, int(sg.end / dur * 100))}%")
    if not [h for h in heard if h[0]]:
        raise RuntimeError("В треке не удалось расслышать слова (инструментал или очень тихий вокал)")
    progress("Сопоставляю с текстом…")
    times = align_lines(lines, heard)
    return build_lrc(lines, times)
