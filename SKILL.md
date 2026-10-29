---
name: suno-music-downloader
display_name: Suno 音乐下载器
display_name_en: Suno Music Downloader
description: 把 Suno 歌曲下载到本地 MP3/M4A，支持单曲、分享短链、播放列表和作者主页。当用户给出 suno.com 链接（/s/ 短链、/song/、/playlist/、/@作者）或提出"把这首 Suno 音乐下下来/保存/转成 MP3/批量下载"时使用本技能。匿名下载，不需要登录或 Cookie，自动完成解密、转码、封面与歌词保存。
description_zh: 一条命令把 Suno 音乐下载到本地，支持单曲、分享短链、播放列表、作者主页四种链接，自动解密、转 MP3、内嵌封面与歌词。
description_en: Download Suno songs to local MP3/M4A from single tracks, share links, playlists or creator profiles. Anonymous, no login or cookies; handles decryption, transcoding, cover art and lyrics.
category: media
version: 1.0.0
author: WorkBuddy
---

# Suno 音乐下载

## Overview

Suno 从 2026 年 8 月起把 CDN 音频改成**静态加密**：直接抓 `.m4a` 得到的是高熵密文（没有 `ftyp`/`moov` 容器结构），官方 `audio_url` 也退化成占位符 `https://studio-api.prod.suno.com/api/forbidden`，旧工具链（`cdn1.suno.ai/<id>.mp3`、gcui-art/suno-api 等）全部失效。

本技能走的是 **Suno 自己的 rights 通道**：向 `POST /api/mango/rights` 索取该曲目的密钥（匿名听众即可获得，与网页播放器一致），再在本地用 AES-CTR-128 解出原始音频。**不需要登录、不需要 Cookie、不需要绕过任何付费下载额度。**

## 快速开始

### 图形界面（推荐给日常使用）

双击 `启动 Suno 下载器.bat`（本技能目录），或：

```bash
python <本技能目录>/scripts/suno_gui.py
```

### 命令行

```bash
python <本技能目录>/scripts/suno_download.py "<Suno链接>" -o "<输出目录>" -f mp3
```

`<本技能目录>` 指本 SKILL.md 所在目录。常用参数：

| 参数 | 说明 |
| --- | --- |
| `-o, --out` | 输出目录，默认 `./suno_downloads` |
| `-f, --format` | `mp3`（默认）/ `m4a`（原始 Opus）/ `both` |
| `--list` | 只解析并列出曲目，不下载 |
| `--skip-existing` | 跳过已存在的同名文件 |
| `--no-cover` / `--no-lyrics` | 不保存封面 / 歌词 |
| `--proxy` | 代理，如 `socks5://127.0.0.1:1080` |
| `--pause` | 两首之间的随机间隔，默认 `1.5,3.0`（秒） |

示例：

```bash
# 分享短链（最常见）
python scripts/suno_download.py "https://suno.com/s/6wTAMkQkvmlbMWQD" -o "D:\音乐"

# 整个播放列表，全部转成 MP3
python scripts/suno_download.py "https://suno.com/playlist/<uuid>" -o "D:\音乐" -f mp3

# 先看一眼有什么，再决定下不下
python scripts/suno_download.py "https://suno.com/@作者" --list
```

## 支持的链接

| 形态 | 示例 | 走哪个接口 |
| --- | --- | --- |
| 分享短链 | `suno.com/s/<code>` | 跟随 307 重定向解析出 song uuid |
| 单曲 | `suno.com/song/<uuid>` | `/api/clip/<uuid>` |
| 播放列表 | `suno.com/playlist/<uuid>` | `/api/playlist/<uuid>` → `playlist_clips[].clip` |
| 作者主页 | `suno.com/@<handle>` | `/api/profiles/<handle>` |
| 裸 uuid | `4d4e95e0-1fce-…` | 先当单曲，404 时按播放列表重试 |

也可以一次粘多行，程序会逐条解析并去重。

## 能力边界（important）

**能下载**：任何**公开可播放**的曲目——包括别人分享给你的、挂在公开播放列表里的、作者主页公开的。已实测通过：自己账号的歌 + 陌生作者的公开单曲。

**下载不了**（会明确报错，不会假装成功）：

- 未公开 / 仅自己可见的歌曲 —— 元数据接口返回 404，报 `NotFoundError`。
- 作者主页把歌曲藏起来的情况 —— `/api/profiles/<handle>` 的 `clips` 可能为空（例如 `@dynfeisu` 返回 `num_total_clips: 0`），此时请改用单曲链接或播放列表链接。
- 需要登录才能播放的内容 —— 本通道不发任何身份凭证。
- Suno 返回 **403/429** —— 抛 `BlockedError` 并**立即停止整批任务**，不做重试轰炸。等一会儿再跑即可。

**合规提示**：Suno 服务条款禁止 robots/scraping。本技能只复现网页播放器对**公开内容**的正常请求节奏（曲目间隔 1.5–3 秒随机），请用于自己的作品或他人明确分享给你的作品，不要批量抓取陌生创作者的内容。

## 免安装的单文件 exe（拷到别的电脑直接用）

`build/dist/` 下已经打好包（PyInstaller onefile）：

| 文件 | 说明 |
| --- | --- |
| `SunoDownloader-x64.exe` | 图形界面版，73 MB。双击即用，不需要装 Python。 |
| `SunoDownloader-x64-cli.exe` | 命令行版，44 MB。参数与本页 CLI 完全一致。 |

- 适用：**Windows 10 / 11，64 位**。ffmpeg、Qt、证书库全部内置，目标机无需任何依赖。
- 首次启动约 5–8 秒（onefile 会先解压到临时目录），之后正常。
- 重新打包：`python build/make_exe.py gui` 或 `python build/make_exe.py cli`（需先 `pip install pyinstaller`）。
- **架构（已定稿：只做 x64，不再考虑 32 位）**：Qt6 / PySide6、Qt5 / PySide2、PyQt5 在 PyPI 上**都没有 win32 包**（已逐个 `pip download --platform win32` 验证），32 位图形界面版做不出来。真正的 32 位 Windows 请改用源码 + Python 运行（`suno_gui.py` / `suno_download.py`）。`build/make_exe.py` 的架构固定为 x64，不要再尝试加 x86 分支。
- 若杀软误报：onefile exe 是自解压程序，容易被启发式规则误判，用源码方式运行即可规避。

## 源码仓库

本目录本身就是一个 git 仓库，远程为 **私有的** `https://github.com/lowprofileusername/suno-music-downloader`
（本地 `git remote -v` 可确认；`gh` 可执行文件在 `C:\Program Files\GitHub CLI\gh.exe`，不在 PATH 里）。

- 改完代码要同步时：在本目录 `git add -A` → `git status` 核对 → `git commit` → `git push`。
- `.gitignore` 已排除 `build/dist/` 与 `build/*.spec`（exe 走 GitHub Release，不进仓库）。
- 仅在用户明确要求时才发版：新建 tag 并把 `build/dist/` 下的 exe 上传为 Release 附件。

## 依赖（源码方式）

`requests` · `cryptography` · `imageio-ffmpeg`（提供 ffmpeg 二进制，用于转 MP3；若系统 PATH 已有 ffmpeg 则优先用系统的）· `PySide6-Essentials`（仅图形界面需要）。

双击 bat 会自动检测并安装；手动安装：

```bash
pip install requests cryptography imageio-ffmpeg PySide6-Essentials
```

## 故障排查

| 现象 | 原因与处理 |
| --- | --- |
| `解密后仍不是合法 MP4 容器` | Suno 改了加密协议。抓一版新的 `/api/mango/rights` 响应对照 `references/tech_notes.md` 里的字段，改 `suno_core.py` 的 `decrypt()`。 |
| `HTTP 403/429` | 被限流，脚本会自动停止。等 10–30 分钟再试，或加 `--proxy`。 |
| `未找到 ffmpeg` | `pip install imageio-ffmpeg`，或把 ffmpeg 放进 PATH。 |
| `找不到该资源（可能被删除或未公开）` | 链接失效、歌曲被删或设为私有。 |
| 输出只有 `.m4a` 没有 `.mp3` | 转码失败会抛错并说明原因，不会静默产出残缺文件。 |

协议细节、字段样例与实测记录见 `references/tech_notes.md`。
