# artist_net.py
"""
Онлайн-данные об исполнителях для темы Echoes Music (как карточка артиста
в Яндекс Музыке): фото, поклонники, популярные треки, альбомы, похожие
исполнители, биография, слушатели/прослушивания.

Источники (без ключей, кроме Last.fm):
  • Deezer API      — фото, поклонники, число альбомов, топ-треки (с «рангом»),
                      альбомы с годами, похожие исполнители;
  • Wikipedia REST  — краткая биография (ru, затем en), с проверкой, что
                      статья именно про музыканта;
  • Last.fm         — слушатели, прослушивания, био, теги (если в настройках
                      указан ключ — тот же, что для «Настроения дня»).

Всё кэшируется в ~/.neon_player/net_cache (данные — на 3 дня, картинки —
навсегда), так что повторное открытие мгновенное и работает офлайн.
Модуль без Qt: fetch_artist() вызывается из фонового потока.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

import requests

try:
    from track_identity import similarity, norm
except Exception:                                  # noqa: BLE001
    from difflib import SequenceMatcher

    def norm(s):
        return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (s or "").casefold())).strip()

    def similarity(a, b):
        return SequenceMatcher(None, norm(a), norm(b)).ratio()

CACHE = Path.home() / ".neon_player" / "net_cache"
IMG_DIR = CACHE / "img"
ART_DIR = CACHE / "artists"
TTL = 3 * 86400
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) EchoesPlayer/1.0"}
_TLS = threading.local()
_FAILED = {}                                       # имя → когда не ответил ни один сервер (не долбить сеть)


def _session() -> requests.Session:
    """Своя сессия на поток: соединения переиспользуются (keep-alive). Без неё каждый запрос — новое
    TLS-рукопожатие; карточка артиста — десятки запросов, и несколько карточек грузили процессор на 2–3 ядра."""
    s = getattr(_TLS, "s", None)
    if s is None:
        s = requests.Session()
        s.headers.update(UA)
        _TLS.s = s
    return s


def _get(url, params=None, timeout=8):
    try:
        r = _session().get(url, params=params, timeout=timeout)
        if r.status_code == 200:
            return r.json()
    except Exception as e:                         # noqa: BLE001
        print("[artist_net]", url.split("/")[2], e.__class__.__name__)
    return None


def _img_path(url: str) -> Path:
    ext = ".png" if url.lower().split("?")[0].endswith(".png") else ".jpg"
    return IMG_DIR / (hashlib.md5(url.encode()).hexdigest() + ext)


def cached_image(url: str) -> str:
    """Путь к уже скачанной картинке ('' — ещё нет), без сети."""
    if not url:
        return ""
    path = _img_path(url)
    try:
        return str(path) if path.stat().st_size > 200 else ""
    except OSError:
        return ""


def image(url: str) -> str:
    """Скачать картинку в кэш (если ещё нет) и вернуть путь к файлу ('' — не вышло)."""
    if not url:
        return ""
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    path = _img_path(url)
    if path.exists() and path.stat().st_size > 200:
        return str(path)
    try:
        r = _session().get(url, timeout=10)
        if r.status_code == 200 and len(r.content) > 200:
            tmp = path.with_suffix(".part")
            tmp.write_bytes(r.content)
            tmp.replace(path)
            return str(path)
    except Exception:                              # noqa: BLE001
        pass
    return ""


def _slug(name):
    return hashlib.md5(norm(name).encode()).hexdigest()


def cached(name):
    p = ART_DIR / f"{_slug(name)}.json"
    try:
        d = json.loads(p.read_text("utf-8"))
        return d
    except Exception:                              # noqa: BLE001
        return None


def _save(name, data):
    try:
        ART_DIR.mkdir(parents=True, exist_ok=True)
        (ART_DIR / f"{_slug(name)}.json").write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    except Exception:                              # noqa: BLE001
        pass


# ── Deezer ──────────────────────────────────────────────────────────────── #

def _deezer(name):
    j = _get("https://api.deezer.com/search/artist", {"q": name, "limit": 8})
    cands = (j or {}).get("data") or []
    best, bs = None, 0.0
    for a in cands:
        s = similarity(name, a.get("name", ""))
        s += min(0.08, (a.get("nb_fan") or 0) / 5e6)          # при равенстве — популярнее
        if s > bs:
            best, bs = a, s
    if not best or bs < 0.82:
        return None
    aid = best["id"]
    info = _get(f"https://api.deezer.com/artist/{aid}") or best
    top = (_get(f"https://api.deezer.com/artist/{aid}/top", {"limit": 10}) or {}).get("data") or []
    albums = (_get(f"https://api.deezer.com/artist/{aid}/albums", {"limit": 30}) or {}).get("data") or []
    related = (_get(f"https://api.deezer.com/artist/{aid}/related", {"limit": 12}) or {}).get("data") or []
    seen, alb = set(), []
    for a in sorted(albums, key=lambda x: x.get("release_date") or "", reverse=True):
        key = norm(a.get("title", ""))
        if key in seen:
            continue
        seen.add(key)
        alb.append({"title": a.get("title", ""), "year": (a.get("release_date") or "")[:4],
                    "type": a.get("record_type", ""), "cover_url": a.get("cover_big") or a.get("cover_medium"),
                    "fans": a.get("fans", 0)})
    return {
        "deezer_id": aid,
        "name": info.get("name") or name,
        "picture_url": info.get("picture_xl") or info.get("picture_big") or "",
        "fans": int(info.get("nb_fan") or 0),
        "albums_count": int(info.get("nb_album") or 0),
        "top": [{"title": t.get("title_short") or t.get("title", ""), "rank": int(t.get("rank") or 0),
                 "duration": int(t.get("duration") or 0), "album": (t.get("album") or {}).get("title", ""),
                 "cover_url": (t.get("album") or {}).get("cover_medium", ""),
                 "artists": ", ".join(c.get("name", "") for c in (t.get("contributors") or [])) or name}
                for t in top],
        "albums": alb[:16],
        "related": [{"name": r.get("name", ""), "fans": int(r.get("nb_fan") or 0),
                     "picture_url": r.get("picture_medium") or ""} for r in related],
    }


# ── Wikipedia ───────────────────────────────────────────────────────────── #

_MUSIC_WORDS = re.compile(r"музык|певец|певиц|рэпер|исполнит|групп|продюсер|композит|диджей|"
                          r"musician|singer|rapper|band|songwriter|producer|dj|group|artist|composer",
                          re.I)


def _wiki(name):
    for lang, titles in (("ru", [name, f"{name} (музыкант)", f"{name} (рэпер)", f"{name} (группа)"]),
                         ("en", [name, f"{name} (musician)", f"{name} (rapper)", f"{name} (band)"])):
        for t in titles:
            j = _get(f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{quote(t.replace(' ', '_'))}")
            if not j or j.get("type") == "disambiguation":
                continue
            desc = (j.get("description") or "") + " " + (j.get("extract") or "")[:300]
            if _MUSIC_WORDS.search(desc):
                return {"bio": j.get("extract", ""), "bio_src": f"Википедия ({lang})",
                        "wiki_url": ((j.get("content_urls") or {}).get("desktop") or {}).get("page", ""),
                        "wiki_img": ((j.get("originalimage") or j.get("thumbnail") or {}).get("source", ""))}
    return None


# ── Last.fm ─────────────────────────────────────────────────────────────── #

def _lastfm(name, key):
    if not key:
        return None
    j = _get("https://ws.audioscrobbler.com/2.0/", {"method": "artist.getinfo", "artist": name,
                                                    "autocorrect": 1, "api_key": key, "format": "json",
                                                    "lang": "ru"})
    a = (j or {}).get("artist")
    if not a:
        return None
    bio = re.sub(r"<a [^>]*>.*?</a>", "", (a.get("bio") or {}).get("summary", ""), flags=re.S)
    bio = html.unescape(re.sub(r"<[^>]+>", "", bio)).strip()
    st = a.get("stats") or {}
    tt = _get("https://ws.audioscrobbler.com/2.0/", {"method": "artist.gettoptracks", "artist": name,
                                                     "autocorrect": 1, "limit": 15, "api_key": key,
                                                     "format": "json"})
    top = [{"title": t.get("name", ""), "playcount": int(t.get("playcount") or 0),
            "listeners": int(t.get("listeners") or 0)}
           for t in ((tt or {}).get("toptracks") or {}).get("track", [])]
    return {"listeners": int(st.get("listeners") or 0), "playcount": int(st.get("playcount") or 0),
            "lastfm_top": top,
            "tags": [t.get("name", "") for t in ((a.get("tags") or {}).get("tag") or [])][:6],
            "lastfm_bio": bio}


# ── всё вместе ──────────────────────────────────────────────────────────── #

def _attach_images(data: dict, full: bool) -> bool:
    """Пути к картинкам карточки; full=False — только фото артиста (для плиток профиля), остальные
    обложки — когда откроют страницу артиста. → были ли докачки."""
    pic_url = data.get("picture_url") or data.get("wiki_img", "")
    groups = [(data.get("top", []), "cover_url", "cover"), (data.get("albums", []), "cover_url", "cover"),
              (data.get("related", []), "picture_url", "picture")]
    if not full:
        data["picture"] = image(pic_url)
        for items, uk, pk in groups:
            for it in items:
                it[pk] = cached_image(it.get(uk, ""))
        data["partial"] = True
        return True
    jobs = [pic_url] + [it.get(uk, "") for items, uk, _pk in groups for it in items]
    need = [u for u in jobs if u and not cached_image(u)]
    if need:
        with ThreadPoolExecutor(max_workers=4) as ex:
            list(ex.map(image, need))
    data["picture"] = cached_image(pic_url)
    for items, uk, pk in groups:
        for it in items:
            it[pk] = cached_image(it.get(uk, ""))
    changed = bool(need) or bool(data.pop("partial", False))
    return changed


def fetch_artist(name: str, lastfm_key: str = "", force: bool = False, images: bool = True) -> dict:
    """Собрать карточку артиста (в фоне). Возвращает dict; ключ 'sources' —
    какие серверы ответили, 'error' — если ни один. images=False — из картинок только фото."""
    c = cached(name)
    if c and not force and time.time() - c.get("ts", 0) < TTL:
        if images and c.get("partial"):
            if _attach_images(c, True):
                _save(name, c)
        return c
    if not force and time.time() - _FAILED.get(name, 0) < 600:
        if c:
            c["stale"] = True
            return c
        return {"name": name, "sources": [], "error": "Серверы не ответили — проверьте интернет"}
    data = {"name": name, "ts": int(time.time()), "sources": []}
    with ThreadPoolExecutor(max_workers=3) as ex:
        fd = ex.submit(_deezer, name)
        fw = ex.submit(_wiki, name)
        fl = ex.submit(_lastfm, name, lastfm_key)
        dz, wk, lf = fd.result(), fw.result(), fl.result()
    if dz:
        data.update(dz)
        data["sources"].append("Deezer")
    if wk:
        data.update(wk)
        data["sources"].append("Wikipedia")
    if lf:
        data.update(lf)
        pc = {norm(t["title"]): t for t in lf.get("lastfm_top", [])}
        if data.get("top"):
            for t in data["top"]:
                m = pc.get(norm(t["title"]))
                if m:
                    t["playcount"], t["listeners"] = m["playcount"], m["listeners"]
        elif lf.get("lastfm_top"):
            data["top"] = [{"title": t["title"], "playcount": t["playcount"], "listeners": t["listeners"],
                            "rank": t["playcount"], "duration": 0, "album": "", "cover_url": "",
                            "artists": name} for t in lf["lastfm_top"][:10]]
        data["sources"].append("Last.fm")
        if not data.get("bio") and lf.get("lastfm_bio"):
            data["bio"], data["bio_src"] = lf["lastfm_bio"], "Last.fm"
    # картинки — параллельно в кэш
    _attach_images(data, images)
    if not data["sources"]:
        _FAILED[name] = time.time()
        data["error"] = "Серверы не ответили — проверьте интернет"
        if c:
            c["stale"] = True
            return c                               # лучше старые данные, чем ничего
    else:
        _save(name, data)
    return data


def human(n: int) -> str:
    """1234567 → «1,2 млн», 45300 → «45,3 тыс.»"""
    n = int(n or 0)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".").replace(".", ",") + " млн"
    if n >= 10_000:
        return f"{n / 1000:.0f} тыс."
    if n >= 1000:
        return f"{n / 1000:.1f}".rstrip("0").rstrip(".").replace(".", ",") + " тыс."
    return str(n)
