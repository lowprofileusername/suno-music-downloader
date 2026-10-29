# Suno 下载协议技术笔记

实测时间：2026-10-28（Windows / Python 3.13 / Chrome 132 UA）。所有结论均来自本机实测请求，非推测。

## 1. 现状：CDN 媒体已静态加密

- `GET https://studio-api.prod.suno.com/api/clip/<id>` 匿名可访问，返回完整元数据。
- 其中 `audio_url` 恒为占位符：`https://studio-api.prod.suno.com/api/forbidden`。
- `media_urls` 形如：

```json
"media_urls": [
  {
    "url": "https://d2lwuy8qc234o3.cloudfront.net/1/clip/<id>.m4a",
    "content_type": "m4a-opus",
    "delivery": "progressive",
    "encoding": "1.0.0"
  }
]
```

- 直接下载该 URL 得到的是**密文**：首字节无 `ftyp`，整段为高熵随机数据（实测 1,816,335 B，头部 `d7 dc 36 58 33 ac 3a 5f`）。
- `https://cdn1.suno.ai/<id>.mp3` 与 `cdn2` 均返回 **403**（2026-08 后失效，mp3 条目也已从 `media_urls` 移除）。
- Suno 官网的下载按钮有月度额度（Free 7 / Pro 20 / Premier 60），本方案不走该按钮。

## 2. 解密协议（"Mango"）

### 2.1 取密钥

```
POST https://studio-api.prod.suno.com/api/mango/rights
Content-Type: application/json
Origin: https://suno.com
User-Agent: <普通浏览器 UA>

{"content_params": {"content_id": "<clip uuid>", "content_type": "clip"}}
```

响应：

```json
{"key": "<base64>", "iv": "<base64>", "glt": "<guest token string>"}
```

匿名即可获得，与网页播放器给未登录听众的密钥一致。

### 2.2 解包

```
user_key = SHA256(glt.encode())            # 32 字节

unwrap(b64):
    w     = base64.b64decode(b64)
    nonce = w[0:12]
    tag   = w[-16:]
    ct    = w[12:-16]
    AES-GCM(user_key, nonce, tag).decrypt(ct, AAD = clip_id.encode())

key     = unwrap(rights["key"])
counter = unwrap(rights["iv"])
```

### 2.3 解音频

音频本体是 **AES-CTR-128**，key = 上面解出的 `key`，初始计数器 = 解出的 `counter`：

```python
dec = Cipher(algorithms.AES(key), modes.CTR(counter)).decryptor()
plain = dec.update(blob) + dec.finalize()
assert plain[4:8] == b"ftyp"     # isom / M4A 容器，此后可直接播放
```

实测：解密后头部 `00 00 00 1c 66 74 79 70 69 73 6f 6d`（`ftypisom`），`mvhd` 显示时长 111.2 s，音轨为 Opus。

同一流程在陌生作者的公开单曲（*Night to Night*，3:47，3.76 MB）上同样成立，说明该通道与作者身份无关，只取决于内容是否公开。

## 3. 其它接口（均为匿名）

| 用途 | 请求 | 关键字段 |
| --- | --- | --- |
| 单曲 | `GET /api/clip/<uuid>` | `title`、`metadata.{tags,prompt,duration}`、`image_large_url`、`media_urls[0].url` |
| 播放列表 | `GET /api/playlist/<uuid>` | `playlist_clips[]`（元素含 `clip` 对象与 `relative_index`，需按 index 排序） |
| 作者主页 | `GET /api/profiles/<handle>?playlists_sort_by=created_at&clips_sort_by=created_at` | `clips[]`。**两个排序参数都是必填**，其它值（recent/popular/trending）会报 `Invalid song sort.` |
| 短链还原 | `GET https://suno.com/s/<code>` | 307 重定向到 `/song/<uuid>?sh=<code>`，从最终 URL 或 HTML 中取 uuid |

`clips_sort_by` / `playlists_sort_by` 的有效值里只有 `created_at`、`play_count` 这类字段名被接受。

## 4. 转码与标签

```
ffmpeg -y -i in.m4a -i cover.jpg -map 0:a -map 1:v -c:v mjpeg -disposition:v attached_pic \
       -c:a libmp3lame -b:a 192k -id3v2_version 3 \
       -metadata title=… -metadata artist=… -metadata album=… -metadata genre=… -metadata comment=… out.mp3
```

- 源文件是 Opus（约 130 kbps 等效），转 192k MP3 足够，再高只是放大体积。
- M4A 加封面用 `-c copy -disposition:v attached_pic`；失败时保留无封面版本（音频本身不受影响）。
- ffmpeg 来源：优先系统 PATH，其次 `imageio_ffmpeg.get_ffmpeg_exe()`。

## 5. 礼貌与风控

- 曲目之间随机停顿 1.5–3.0 秒。
- 遇到 403/429 立刻抛 `BlockedError` 并终止整批，不重试轰炸。
- 网络抖动（ChunkedEncodingError 等）按 2/4/6 秒退避重试 3 次，仍失败才报错。

## 6. 变更监测点

如果哪天下载失败，按这个顺序排查：

1. `/api/clip/<id>` 是否还能匿名 200；`media_urls` 结构是否变了。
2. `/api/mango/rights` 是否还返回 `key/iv/glt`；字段是否改名。
3. 解密后前 8 字节是否仍是 `ftyp`；若不是，包裹方式或 CTR 参数可能已变。
4. 是否出现了新的 `content_type`（历史上是 mp3 → m4a-opus）。
