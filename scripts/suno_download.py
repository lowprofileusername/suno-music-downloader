# -*- coding: utf-8 -*-
"""Suno 下载命令行入口。

用法：
  python suno_download.py <链接...> [-o 输出目录] [-f mp3|m4a|both] [选项]
  python suno_download.py <链接...> --list        # 只解析并列出，不下载

支持：suno.com/s/<短码> · /song/<uuid> · /playlist/<uuid> · /@<作者> · 裸 uuid
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from suno_core import (BlockedError, NotFoundError, SunoClient, SunoError,  # noqa: E402
                       parse_inputs, resolve, sanitize, save_track)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="下载 Suno 公开歌曲（匿名、无需登录）",
        formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("urls", nargs="+", help="一个或多个 Suno 链接（也可每行一个）")
    p.add_argument("-o", "--out", default="suno_downloads", help="输出目录，默认 ./suno_downloads")
    p.add_argument("-f", "--format", default="mp3", choices=["mp3", "m4a", "both"],
                   help="输出格式，默认 mp3")
    p.add_argument("--no-cover", action="store_true", help="不保存封面")
    p.add_argument("--no-lyrics", action="store_true", help="不保存歌词")
    p.add_argument("--skip-existing", action="store_true", help="跳过已存在的同名文件")
    p.add_argument("--proxy", default=None, help="代理，如 socks5://127.0.0.1:1080")
    p.add_argument("--pause", default="1.5,3.0", help="两首之间的随机间隔秒数，默认 1.5,3.0")
    p.add_argument("--list", action="store_true", help="只解析链接并列出曲目，不下载")
    return p


def main(argv=None) -> int:
    # 中文 Windows 下输出被重定向时会退回到 GBK，这里统一成 UTF-8
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    args = build_parser().parse_args(argv)
    lo, hi = (float(x) for x in args.pause.split(","))
    out_dir = Path(args.out).expanduser()
    client = SunoClient(pause=(lo, hi), proxy=args.proxy)

    items = parse_inputs("\n".join(args.urls))
    if not items:
        print("没识别出任何 Suno 链接。支持：suno.com/s/xxx、/song/xxx、/playlist/xxx、/@作者、裸 uuid")
        return 2

    try:
        tracks = resolve(client, items)
    except (BlockedError, NotFoundError, SunoError) as e:
        print("解析失败：", e)
        return 1

    print(f"共解析到 {len(tracks)} 首：")
    for i, t in enumerate(tracks, 1):
        print(f"  {i:>2}. {t.title}  ({t.mmss})  {t.id[:8]}…")
    if args.list:
        return 0

    ok = fail = 0
    for i, t in enumerate(tracks, 1):
        target = out_dir / sanitize(t.title)
        probe = target.with_suffix(".mp3") if args.format in ("mp3", "both") \
            else target.with_suffix(".m4a")
        if args.skip_existing and probe.exists():
            print(f"[{i}/{len(tracks)}] 跳过（已存在）：{t.title}")
            continue
        print(f"[{i}/{len(tracks)}] 下载：{t.title}")
        t0 = time.time()

        def prog(done, total):
            if total:
                pct = done * 100 // total
                print(f"\r    取流 {pct:>3}%  {done/1048576:.1f}/{total/1048576:.1f} MB",
                      end="", flush=True)

        try:
            files = save_track(client, t, out_dir, fmt=args.format,
                               cover=not args.no_cover, lyrics=not args.no_lyrics,
                               progress=prog)
            print(f"\r    完成 {time.time()-t0:.1f}s -> "
                  + "、".join(f.name for f in files))
            ok += 1
        except BlockedError as e:
            print("\r    停止：", e)
            break
        except (NotFoundError, SunoError) as e:
            print("\r    失败：", e)
            fail += 1

    print(f"—— 成功 {ok} 首，失败 {fail} 首，输出目录 {out_dir.resolve()}")
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(main())
