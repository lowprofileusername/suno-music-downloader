# -*- coding: utf-8 -*-
"""
Suno 公开音频下载核心库（匿名、无需登录/Cookie）。

协议要点（2026-08 之后 Suno 把 CDN 媒体改成静态加密）：
  1. 解析链接          /s/<share>  /song/<uuid>  /playlist/<uuid>  /@<handle>
  2. 元数据            GET  studio-api.prod.suno.com/api/clip/<id>
                       取 media_urls[0].url（m4a-opus）
  3. 密钥（匿名可拿）   POST studio-api.prod.suno.com/api/mango/rights
                       {"content_params":{"content_id":<id>,"content_type":"clip"}}
                       -> {key, iv, glt}
  4. 解包              user_key = SHA256(glt)
                       key/iv 为 AES-GCM 包裹：nonce=前12B, tag=后16B, AAD=clip id
  5. 音频本体          AES-CTR-128(user_key 解出的 key, counter=iv)
  6. 转码（可选）      ffmpeg -> mp3 192k + 内嵌封面/ID3

所有失败都抛明确异常，不做「假装成功」的兜底。
"""
from __future__ import annotations

import base64
import hashlib
import json
import random
import re
import shutil
import subprocess
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

import requests
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

API = "https://studio-api.prod.suno.com"
SITE = "https://suno.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36")
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                     r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


class SunoError(Exception):
    """基础错误。"""


class BlockedError(SunoError):
    """Suno 返回 403/429：立刻停止，不要重试轰炸。"""


class NotFoundError(SunoError):
    """链接/资源不存在或不是公开可见。"""


class DecryptError(SunoError):
    """解密后仍不是合法音频容器。"""


def sanitize(name: str, limit: int = 80) -> str:
    name = unicodedata.normalize("NFC", str(name or "")).strip()
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return (name[:limit] or "untitled")


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e:  # 明确报错，不当成「没有转码也行」
        raise SunoError(f"未找到 ffmpeg，也无法加载 imageio-ffmpeg：{e}")


@dataclass
class Track:
    id: str
    title: str = "untitled"
    duration: float = 0.0
    artist: str = ""
    model: str = ""
    tags: str = ""
    prompt: str = ""
    cover: str = ""
    media_url: str = ""
    content_type: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def mmss(self) -> str:
        s = int(self.duration or 0)
        return f"{s // 60}:{s % 60:02d}"


class SunoClient:
    """一个会话对应一轮下载；内部自带限速与退避。"""

    def __init__(self, pause: tuple[float, float] = (1.5, 3.0),
                 proxy: str | None = None, retries: int = 3):
        self.pause = pause
        self.retries = retries
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": SITE,
            "Referer": SITE + "/",
        })
        if proxy:
            self.session.proxies = {"http": proxy, "https": proxy}
        self._last_call = 0.0

    # ---------- 底层 ----------
    def _throttle(self):
        wait = random.uniform(*self.pause) - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.time()

    def _request(self, method: str, url: str, **kw):
        last = None
        for i in range(self.retries):
            self._throttle()
            try:
                r = self.session.request(method, url, timeout=(15, 90), **kw)
            except requests.RequestException as e:
                last = e
                time.sleep(2 * (i + 1))
                continue
            if r.status_code in (403, 429):
                raise BlockedError(f"HTTP {r.status_code} —— Suno 要求减速，已停止本轮任务")
            if r.status_code == 404:
                raise NotFoundError(f"找不到该资源（可能被删除或未公开）：{url}")
            if r.status_code >= 500:
                last = RuntimeError(f"HTTP {r.status_code}")
                time.sleep(2 * (i + 1))
                continue
            r.raise_for_status()
            return r
        raise SunoError(f"请求失败：{url}（{last}）")

    def _json(self, method: str, url: str, **kw):
        return self._request(method, url, **kw).json()

    # ---------- 元数据 ----------
    def clip(self, cid: str) -> Track:
        d = self._json("GET", f"{API}/api/clip/{cid}")
        return _track_from_meta(d)

    def playlist(self, pid: str) -> list[Track]:
        d = self._json("GET", f"{API}/api/playlist/{pid}")
        items = d.get("playlist_clips") or []
        items.sort(key=lambda x: x.get("relative_index", 0))
        return [_track_from_meta(x.get("clip") or {}) for x in items if x.get("clip")]

    def profile(self, handle: str) -> list[Track]:
        d = self._json("GET", f"{API}/api/profiles/{handle}", params={
            "playlists_sort_by": "created_at", "clips_sort_by": "created_at"})
        return [_track_from_meta(c) for c in (d.get("clips") or []) if c.get("id")]

    def resolve_share(self, code: str) -> str:
        """把 /s/<code> 短链还原成 clip uuid。"""
        r = self._request("GET", f"{SITE}/s/{code}", allow_redirects=True)
        m = UUID_RE.search(r.url) or UUID_RE.search(r.text)
        if not m:
            raise NotFoundError(f"短链未能解析出歌曲：{code}")
        return m.group(0)

    # ---------- 解密 ----------
    def rights(self, cid: str) -> dict:
        d = self._json("POST", f"{API}/api/mango/rights", json={
            "content_params": {"content_id": cid, "content_type": "clip"}})
        if not (d.get("key") and d.get("iv") and d.get("glt")):
            raise SunoError("rights 返回缺少 key/iv/glt，Suno 接口可能已变更")
        return d

    @staticmethod
    def decrypt(blob: bytes, cid: str, rights: dict) -> bytes:
        user_key = hashlib.sha256(rights["glt"].encode()).digest()

        def unwrap(b64: str) -> bytes:
            w = base64.b64decode(b64)
            d = Cipher(algorithms.AES(user_key), modes.GCM(w[:12], w[-16:])).decryptor()
            d.authenticate_additional_data(cid.encode())
            return d.update(w[12:-16]) + d.finalize()

        dec = Cipher(algorithms.AES(unwrap(rights["key"])),
                     modes.CTR(unwrap(rights["iv"]))).decryptor()
        return dec.update(blob) + dec.finalize()

    # ---------- 下载 ----------
    def fetch_media(self, track: Track,
                    progress: Callable[[int, int], None] | None = None,
                    cancelled: Callable[[], bool] | None = None) -> bytes:
        """返回已解密、可直接播放的音频字节。"""
        if not track.media_url:
            meta = self.clip(track.id)
            track.media_url = meta.media_url
        if not track.media_url:
            raise NotFoundError(f"「{track.title}」没有可下载的媒体地址（可能未公开）")

        self._throttle()
        with self.session.get(track.media_url, stream=True, timeout=(15, 120)) as r:
            if r.status_code in (403, 429):
                raise BlockedError("HTTP 403/429 —— Suno 拒绝取流，已停止")
            if r.status_code == 404:
                raise NotFoundError(f"媒体文件 404：{track.media_url}")
            r.raise_for_status()
            total = int(r.headers.get("Content-Length") or 0)
            buf, done = bytearray(), 0
            for chunk in r.iter_content(1 << 16):
                if cancelled and cancelled():
                    raise SunoError("已取消")
                buf += chunk
                done += len(chunk)
                if progress:
                    progress(done, total)
        blob = bytes(buf)

        if blob[4:8] == b"ftyp":          # 未加密（旧资源）
            return blob
        plain = self.decrypt(blob, track.id, self.rights(track.id))
        if plain[4:8] != b"ftyp":
            raise DecryptError(f"「{track.title}」解密后仍不是合法 MP4 容器"
                               f"（头部 {plain[:12]!r}），协议可能已变更")
        return plain


def _track_from_meta(d: dict) -> Track:
    md = d.get("metadata") or {}
    media = (d.get("media_urls") or [{}])[0]
    artist = (d.get("display_name") or d.get("handle")
              or d.get("user_display_name") or d.get("user_handle") or "")
    return Track(
        id=d.get("id") or "",
        title=d.get("title") or "untitled",
        duration=float(md.get("duration") or 0),
        artist=artist,
        model=d.get("model_name") or "",
        tags=md.get("tags") or "",
        prompt=md.get("prompt") or "",
        cover=d.get("image_large_url") or d.get("image_url") or "",
        media_url=media.get("url") or "",
        content_type=media.get("content_type") or "",
        raw=d,
    )


# ---------- 落地 ----------
def save_track(client: SunoClient, track: Track, out_dir: Path, fmt: str = "mp3",
               cover: bool = True, lyrics: bool = True,
               progress: Callable[[int, int], None] | None = None,
               cancelled: Callable[[], bool] | None = None) -> list[Path]:
    """下载 + 解密 + 转码，返回产出文件列表。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    base = out_dir / sanitize(track.title)
    produced: list[Path] = []

    plain = client.fetch_media(track, progress, cancelled)
    m4a = base.with_suffix(".m4a")
    m4a.write_bytes(plain)

    want_mp3 = fmt in ("mp3", "both")
    want_m4a = fmt in ("m4a", "both")

    if want_mp3:
        mp3 = base.with_suffix(".mp3")
        _transcode(m4a, mp3, track, cover_url=track.cover if cover else "")
        produced.append(mp3)
        if fmt == "mp3":            # 只要 mp3 时，原始 m4a 是中间产物
            m4a.unlink(missing_ok=True)
    if want_m4a:
        if cover and track.cover:
            _attach_cover(m4a, track)
            _save_cover(client, base, track.cover)
        produced.append(m4a)

    if lyrics and track.prompt:
        (base.with_suffix(".lrc")).write_text(track.prompt, encoding="utf-8")
    return [p for p in produced if p.exists()]


def _save_cover(client: "SunoClient", base: Path, url: str) -> None:
    try:
        img = client.session.get(url, timeout=60).content
        (base.with_name(base.name + ".cover.jpg")).write_bytes(img)
    except Exception:
        pass


def _meta_args(track: Track) -> list[str]:
    args = ["-metadata", f"title={track.title}"]
    if track.artist:
        args += ["-metadata", f"artist={track.artist}"]
    if track.model:
        args += ["-metadata", f"album=Suno {track.model}"]
    if track.tags:
        args += ["-metadata", f"comment={track.tags[:250]}"]
    args += ["-metadata", f"genre={track.tags.split(',')[0][:60] if track.tags else 'Suno AI'}"]
    return args


def _transcode(src: Path, dst: Path, track: Track, cover_url: str) -> None:
    ff = _ffmpeg()
    cover_file = None
    cmd = [ff, "-y", "-loglevel", "error", "-i", str(src)]
    if cover_url:
        cover_file = src.with_name(src.stem + ".cover.jpg")
        try:
            cover_file.write_bytes(requests.get(cover_url, timeout=60).content)
            cmd += ["-i", str(cover_file), "-map", "0:a", "-map", "1:v",
                    "-c:v", "mjpeg", "-disposition:v", "attached_pic"]
        except Exception:
            cover_file = None
    if cover_file is None:
        cmd += ["-map", "0:a"]
    cmd += ["-c:a", "libmp3lame", "-b:a", "192k", "-id3v2_version", "3"]
    cmd += _meta_args(track) + [str(dst)]
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode != 0 or not dst.exists():
        raise SunoError("转 MP3 失败：" +
                        p.stderr.decode("utf-8", "ignore").strip()[-300:])
    if cover_file:
        cover_file.unlink(missing_ok=True)


def _attach_cover(m4a: Path, track: Track) -> None:
    """给 m4a 加封面；失败就保留无封面版本（不影响音频本身）。"""
    try:
        cover_file = m4a.with_name(m4a.stem + ".cover.jpg")
        cover_file.write_bytes(requests.get(track.cover, timeout=60).content)
        tmp = m4a.with_name(m4a.stem + ".cov.m4a")
        cmd = [_ffmpeg(), "-y", "-loglevel", "error", "-i", str(m4a),
               "-i", str(cover_file), "-map", "0:a", "-map", "1:v",
               "-c", "copy", "-disposition:v", "attached_pic"] + \
              _meta_args(track) + [str(tmp)]
        p = subprocess.run(cmd, capture_output=True)
        if p.returncode == 0 and tmp.exists() and tmp.stat().st_size:
            tmp.replace(m4a)
        else:
            tmp.unlink(missing_ok=True)
        cover_file.unlink(missing_ok=True)
    except Exception:
        pass


# ---------- 链接解析 ----------
def parse_inputs(text: str) -> list[tuple[str, str]]:
    """-> [(kind, target)]，kind ∈ song/playlist/profile/share/uuid。"""
    out, seen = [], set()
    for line in str(text or "").splitlines():
        for token in re.split(r"[\s,]+", line.strip()):
            if not token:
                continue
            item = None
            m = re.search(r"suno\.com/s/([A-Za-z0-9]+)", token)
            if m:
                item = ("share", m.group(1))
            if not item:
                m = re.search(r"suno\.com/song/([0-9a-fA-F-]{36})", token)
                if m:
                    item = ("song", m.group(1))
            if not item:
                m = re.search(r"suno\.com/playlist/([0-9a-fA-F-]{36})", token)
                if m:
                    item = ("playlist", m.group(1))
            if not item:
                m = re.search(r"suno\.com/@([A-Za-z0-9_.-]+)", token)
                if m:
                    item = ("profile", m.group(1))
            if not item and re.fullmatch(r"[0-9a-fA-F-]{36}", token):
                item = ("uuid", token.lower())
            if item and item not in seen:
                seen.add(item)
                out.append(item)
    return out


def resolve(client: SunoClient, items: Iterable[tuple[str, str]]) -> list[Track]:
    tracks: list[Track] = []
    seen: set[str] = set()
    for kind, target in items:
        if kind == "share":
            cid = client.resolve_share(target)
            got = [client.clip(cid)]
        elif kind == "song":
            got = [client.clip(target)]
        elif kind == "uuid":
            try:
                got = [client.clip(target)]
            except NotFoundError:
                got = client.playlist(target)
        elif kind == "playlist":
            got = client.playlist(target)
        elif kind == "profile":
            got = client.profile(target)
        else:
            continue
        for t in got:
            if t.id and t.id not in seen:
                seen.add(t.id)
                tracks.append(t)
    return tracks
