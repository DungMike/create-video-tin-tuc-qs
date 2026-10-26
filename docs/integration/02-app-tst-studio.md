# 02. App tạo ảnh: TST Studio + TST bridge + skills

> Thư mục: `C:\Users\hiv\Documents\Cua Dung\app-tst-studio` (gọi tắt `TST`).
> Vai trò trong pipeline (**loại 1**): audio + `.srt` → chia timeline → bible nhân vật + prompt (AI) → ảnh nhân vật → ảnh cảnh qua app TST → dựng video bằng ffmpeg.
> Mọi `file:line` tính từ `TST`.

[← 01 TTS](01-app-tts-dgt.md) · [Tổng quan](00-tong-quan-kien-truc.md) · [03 Story Video Studio →](03-app-story-video-studio.md)

---

## 1. Thành phần

| Thành phần | Vị trí | Ghi chú |
|---|---|---|
| Studio server | `studio\server\index.js` (324 dòng), `lib.js`, `video.js`, `fonts.js` | Express, `127.0.0.1:5180`, không auth, không CORS |
| Studio frontend | `studio\src\` (React + Vite chạy dạng middleware) | Trang `/`, `/p/:pid`, `/p/:pid/s/:sid`, `/p/:pid/video/:section` |
| Skill điều phối | `.claude\skills\srt-flow\scripts\flow.py` | Nhận biết bước, chạy split/build/ảnh |
| Skill prompt | `.claude\skills\srt-prompt-anh\scripts\srt_prompts.py` + `references\huong-dan-prompt.md` | `split` / `show` / `build` |
| Skill tạo ảnh | `.claude\skills\srt-tao-anh\scripts\tao_anh.py` | Gọi TST bridge |
| Skill dựng video | `.claude\skills\srt-video\scripts\dung_video.py` + `presets.json` | ffmpeg |
| TST bridge | `tst-bridge\TSTApiBridge\*.cs`, `tst-bridge\bin\bridge.config.json`, `tst-bridge\start-with-api.ps1` | Startup hook nạp vào `TSTAutoGenerateImage.exe`, cổng `127.0.0.1:5179` |
| Tài liệu sẵn có | `docs\01-TST-nghiep-vu-va-API.md`, `docs\02-Ke-hoach-luong-SRT-anh.md`, `docs\03-Tich-hop-OpenAPI.md`, `docs\openapi\tst-studio.openapi.yaml`, `docs\openapi\tst-bridge.openapi.yaml` | `docs\03` §4 mô tả hợp đồng file cho bước AI, §6 liệt kê 12 bẫy tích hợp |

> `.agents\skills\` là bản Codex **cũ** của `.claude\skills\`; `dung_video.py` và `presets.json` ở đó đã lỗi thời. Studio chỉ dùng `.claude\skills` (`lib.js:10`).

**Khởi động:**
- `start-studio.cmd` hoặc `npm run dev` trong `studio\` (chạy `node server/index.js`, chế độ dev có Vite middleware).
- Thêm `--prod` để phục vụ `dist\` (`index.js:312-320`).
- App TST phải mở bằng `tst-bridge\start-with-api.ps1` (gắn `DOTNET_STARTUP_HOOKS`).
- **Đăng nhập license TST luôn do người bấm.**

**Biến môi trường:**

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `PORT` | 5180 | Cổng studio |
| `PYTHON` | `python` | Lệnh Python (`lib.js:16`) |
| `TST_API` | `http://127.0.0.1:5179/api` | Địa chỉ bridge |
| `TST_API_KEY` | – | Gửi kèm header `X-API-Key`, timeout 4 s mỗi lần gọi (`index.js:19-20, 42`) |
| `FFMPEG`, `FFPROBE` | – | `dung_video.py` đọc (dòng 46-47) |

Python luôn được chạy với `PYTHONUTF8=1`, `PYTHONIOENCODING=utf-8`, `NODE=<node hiện tại>` (`lib.js:18`).

---

## 2. Studio API (cổng 5180)

- Lỗi có dạng `{"error":{"message"}}`; vượt giới hạn file (`LIMIT_FILE_SIZE`) trả 413 (`index.js:306-310`).
- Upload dùng multer: thư mục tạm `os.tmpdir()/tst-studio-upload`, tối đa 2 GiB mỗi file, `originalname` được giải mã lại từ latin1 sang utf8 (`index.js:26-34`).

### 2.1 Route trong `index.js`

| Method | Path | Dòng | Input → Output |
|---|---|---|---|
| GET | `/api/tst` | 60 | → `{online, ok, bridgeVersion, appVersion, appReady, loggedIn, compatible, missingMembers, status}`, hoặc `{online:false, loggedIn:false, error}`. Gọi bridge `/health` + `/status`. |
| POST | `/api/tst/stop` | 61 | → kết quả bridge `POST /queue/stop` |
| GET | `/api/projects` | 65 | → `[project + storyCount + cover]` |
| POST | `/api/projects` | 81 | `{name*, style*, id?, description?, style_skill?, aspect_ratio?, character_ratio?, rules?}` → 201. Tạo `project.json`, `stories/` và mẫu `.claude/skills/<style_skill>/SKILL.md` (`lib.js:134-187`). |
| GET | `/api/projects/:pid` | 82 | → `{project, stories: StorySummary[]}`. **Chậm:** chạy `flow.py --trang-thai` cho từng truyện. |
| PATCH | `/api/projects/:pid` | 86 | name / description / style / aspect_ratio / character_ratio / rules |
| **POST** | **`/api/projects/:pid/stories`** | 104-137 | multipart `srt` (bắt buộc, `.srt`, ≥ 1 cue), `audio` (tuỳ chọn), `title`, `id` → 201 `StorySummary`. **Đồng bộ:** chạy luôn `flow.py <srt>` (timeout 120 s, chấp nhận exit 0 hoặc 2) để chia timeline. |
| GET | `/api/projects/:pid/stories/:sid` | 139 | → `StoryDetail`, kèm trạng thái flow (`lib.js:269-325`) |
| PATCH | `/api/projects/:pid/stories/:sid` | 141 | `{title?, lang?}`; `lang` ∈ `vi, th, ko, ja, zh-TW, es, en`, chuỗi rỗng = tự nhận diện |
| POST | `/api/projects/:pid/stories/:sid/audio` | 155 | multipart `audio` → `{audio: url}` |
| DELETE | `/api/projects/:pid/stories/:sid` | 172 | Chuyển vào `projects/<pid>/_trash/<sid>-<ms>`. 409 nếu đang có job. |
| **POST** | **`/api/projects/:pid/stories/:sid/run`** | 227-275 | `{step, from?, to?, ids?, draft?}` → **202** `PipelineJob` (xem §2.3) |
| GET | `/api/jobs?project=&story=` | 277 | → các job, mới nhất trước, mỗi job kèm 3 dòng log cuối |
| GET | `/api/jobs/:id?since=N` | 284 | → job + log từ dòng N |
| POST | `/api/jobs/:id/cancel` | 289 | Kill cả cây tiến trình bằng `taskkill /pid /T /F` (`killTree` 222-225) |
| GET | `/media/*` | 304 | File tĩnh trong `projects/` (từ chối dotfile, hỗ trợ Range) |

### 2.2 Route trong `video.js` (gắn dưới `/api`)

| Method | Path | Dòng | Ghi chú |
|---|---|---|---|
| GET | `/api/video/presets` | 62 | Nội dung `presets.json` |
| GET | `/api/fonts?refresh=1` | 63 | `[{family, vi, langs, styles}]` |
| GET / PUT | `/api/projects/:pid/video` | 65, 72 | PUT `{config:{motion, transition, subtitle, post, output}}` → `{config (mặc định gộp với bản đã lưu), saved}`. Ghi vào `projects/<pid>/video.json`. |
| POST | `/api/video/sample` | 83 | `dung_video.py --mau` |
| GET | `/api/projects/:pid/stories/:sid/video` | 88 | `{renders, overrides, cacheBytes, audio}` |
| POST | `…/video/plan` | 99 | `dung_video.py <dir> --ke-hoach` |
| PUT | `…/video/overrides` | 105 | `{overrides:{"<id>":{motion?, transition?}}}`, ghi vào `<story>/video.json` |
| DELETE | `…/video/renders/:file` | 121 | Xoá file `.mp4` và `.json` đi kèm |
| DELETE | `…/video/cache` | 130 | → `{freed}` |
| POST | `…/video/preview` | 140 | `{kind: motion|transition|window, …}`. Các preview của cùng một truyện chạy tuần tự. |

### 2.3 Các bước của `/run` (`index.js:191-219`)

```js
split:      [FLOW, p.srt]                                   ok: [0, 2]
build:      [SRT_PROMPTS, "build", p.work]
characters: [TAO_ANH, p.prompts, "--chi-nhan-vat"]          tst: true
scenes:     [TAO_ANH, p.prompts, --from, --to]              tst: true
regen:      [TAO_ANH, p.prompts, "--tao-lai", ids, ...]     tst: true   // ids khớp ^(\d+|nv:[\w-]+)$
export:     [TAO_ANH, p.prompts, "--xuat"]
video:      [DUNG_VIDEO, p.dir, "--tu", from||"0", (--den to), (--nhap)]   // thời gian ^\d+(:\d{1,2}){0,2}(\.\d+)?$
```

**Điều kiện chặn** (`index.js:231-247`). Vi phạm trả 409:
- Mỗi truyện chỉ chạy **1 job** một lúc (`activeJob`, dòng 185).
- `split` bị từ chối khi đã có `segments.json`.
- `build` cần bible và đủ prompt.
- **Mọi bước khác, kể cả `export` và `video`, cần có `prompts.json`.**
- Các bước có `tst: true` cần `/api/tst` báo `online` và `loggedIn`.

**Mô hình job:**
- Chỉ có polling, không SSE.
- Job nằm trong `Map` ở RAM, id `job1`, `job2`… **Mất khi studio restart.**
- Log tối đa 5000 dòng.

```js
{ id, projectId, storyId, step, status /* running|done|error|cancelled */, exitCode,
  startedAt, endedAt, lineCount, lines /* từ since */ }
```

**Dấu tiến độ trong log:**

| Nguồn | Dòng log |
|---|---|
| Studio | dòng đầu luôn là `$ python <args>` |
| `tao_anh.py` | `[n/total] ảnh 12 -> …` |
| `dung_video.py` | `PROGRESS a/b (cảnh X, còn khoảng Ns)`, dòng cuối `OUTPUT <đường dẫn tuyệt đối>` |

Studio không giới hạn số truyện chạy cùng lúc; tất cả dùng chung hàng đợi của TST.

---

## 3. TST bridge API (cổng 5179)

**Cơ chế:**
- Là .NET startup hook, nạp vào `TSTAutoGenerateImage.exe` qua `DOTNET_STARTUP_HOOKS` (`start-with-api.ps1:38-40`).
- HTTP/1.1 viết trên `TcpListener`: 1 request mỗi kết nối, body tối đa 512 MB (`Http.cs:153-305`).
- Mọi handler chạy trên UI thread của app (`AppGateway.OnUi`).
- Khi **chưa đăng nhập** trả `503 not_logged_in`; khi app đang khởi động trả `503 app_starting` (`AppGateway.cs:96-116`).

**Cấu hình và xác thực:**
- File `bin\bridge.config.json`, gồm:
  - `host` (127.0.0.1), `port` (5179)
  - `apiKey`, `webhookUrl`
  - `defaultOutputFolder` (mặc định `Documents\TSTAutoGenerateImage\API`)
- Khi có `apiKey`, client gửi qua `X-API-Key`, `?apiKey=` hoặc `Authorization: Bearer` (`BridgeHost.cs:53-63`).
- Lỗi có dạng `{"error":{"code","message"}}`. Field lạ bị chặn với 400 (`ApiRoutes.cs:979-986`).

**Endpoint (`ApiRoutes.cs:14-43`, đủ 27 route):**

| Method | Path | Ghi chú |
|---|---|---|
| GET | `/api` | Mục lục |
| GET | `/api/health` | Không cần đăng nhập. `{ok, bridgeVersion, appVersion, appReady, loggedIn, compatible, missingMembers}` |
| GET | `/api/status` | `{user{username, expiredDate, daysRemaining, maxThreads}, running, pump, queue{total, pending, waiting, processing, done, error}, accounts}` |
| GET | `/api/options` | jobTypes, tỉ lệ khung hình, chất lượng, thời lượng |
| GET / PATCH / PUT / POST | `/api/settings` | `threads` (1..20 theo license), `imageAspectRatio`, `imageQuality`, `imageToImageStrength`, `referenceMode`, `skipIfOutputExists`, `splitPromptsByBlankLine`… |
| GET | `/api/logs?source=queue|accounts&tail=` | |
| **POST** | **`/api/jobs[?wait=s]`** | Xem body ở dưới → 201 `{batchId, name, outputFolder, autoStart, count, jobs[]}` |
| POST | `/api/jobs/from-folder` | `{type, folder, aspectRatio, quality, duration, autoStart, callbackUrl}` |
| GET | `/api/jobs?status=&type=&batchId=&limit=&offset=` | `{total, offset, limit, jobs}` |
| GET | `/api/jobs/{id}` | Job |
| GET | `/api/jobs/{id}/output` | File ảnh. 409 nếu job chưa xong. |
| POST | `/api/jobs/{id}/retry?autoStart=true` | Xoá file cũ, tăng `retryCount` |
| DELETE | `/api/jobs/{id}` | |
| **GET** | **`/api/batches/{batchId}?wait=0..3600`** | Long-poll, kiểm tra mỗi 1 s → `{batchId, status: pending|running|completed, counts, jobs}` |
| POST | `/api/queue/start` · `/stop` · `/retry-errors` · `/clear-done` · `/clear` | `{types?}` |
| GET / POST / DELETE | `/api/accounts`, `/api/accounts/login`, `/api/accounts/login/stop`, `/api/accounts/{email}` | Tài khoản Grok, chỉ dùng cho job video |

**Body `POST /api/jobs`:**
- `type`: `text-to-image` | `image-to-image` | `text-to-video` | `image-to-video` (chấp nhận viết tắt `t2i`, `i2i`…).
- Prompt: `prompt` | `prompts[]` | `promptsText`.
- Ảnh vào (chỉ với loại `image-*`):
  - `images[]`: mỗi ảnh một job;
  - `referenceImages[]`: chế độ tham chiếu. Mỗi prompt một job; app chọn ảnh theo `@mention` (regex `@([\w\-\.]+)`), không có mention thì lấy ảnh đầu.
- Khác: `outputFolder`, `name`, `aspectRatio`, `quality`, `duration`, `autoStart` (mặc định true), `callbackUrl`.
- Ảnh có thể là đường dẫn, URL http(s), `data:` URI, hoặc `{path}` / `{url, fileName}` / `{base64, fileName}`.

**Job:**
```
{id:"job_YYYYMMDDHHMMSS_xxxxxx", batchId, index, type,
 status: pending|waiting|processing|done|error,
 prompt, referenceImages[], aspectRatio, outputPath, outputUrl, errorMessage, retryCount, createdAt}
```

**Bất đồng bộ:**
- `autoStart` bật QueuePump, vòng nền chạy mỗi 1 s.
- Theo dõi bằng long-poll `batches/{id}?wait=`, hoặc webhook `job.completed` / `job.failed` / `batch.completed` (thử 3 lần, không ký).
- App tự retry mỗi job 5 lần.
- **Tốc độ khoảng 3-5 ảnh/phút** (`docs\01:421-436`).

---

## 4. `flow.py`: nhận biết bước

**CLI:** `flow.py <srt> [--trang-thai [--json]] [--from 1] [--to N] [--api URL] [--api-key KEY] [--khong-dung] [--dry-run]`

**Exit code:**

| Code | Ý nghĩa |
|---|---|
| 0 | Xong |
| 1 | Lỗi |
| **2** | **Cần AI viết bible hoặc prompt** |
| 3 | Vừa tạo ảnh nhân vật, dừng chờ duyệt (bỏ qua bằng `--khong-dung`) |

**Trạng thái `next`** (`status` 74-102, `next_step` 105-118), xét theo thứ tự:

| `next` | Điều kiện | Ai làm |
|---|---|---|
| `split` | chưa có `segments.json` | script |
| `bible` | chưa có `bible.json` | **AI** |
| `prompts` | có đoạn chưa có prompt trong `batches/*.json` | **AI** |
| `build` | thiếu `prompts.json`, hoặc nó cũ hơn segments/bible/batch (so mtime) | script |
| `characters` | thiếu ảnh nhân vật | script + TST |
| `scenes` | thiếu ảnh cảnh | script + TST |
| `done` | đủ tất cả | — |

Không có trạng thái riêng cho `export` hay `video`.

**`--trang-thai --json`** (lấy dòng stdout **cuối cùng**). Ví dụ thật từ `projects\campuchia\stories\k1a-61-65-ngay-13-10`:

```json
{"next":"done","segments":285,"bible":{"exists":true,"characters":12,"style_ok":true},
 "prompts":{"done":285,"total":285,"next_batch":null,"next_batch_file":"009.json"},"built":true,
 "characters":{"done":12,"total":12},"images":{"done":285,"total":285},
 "exports":{"timeline":true,"slideshow":true},"style_skill":"C:\\...\\ve-campuchia\\SKILL.md"}
```

- `next_batch` có dạng `[lo, hi]`, mỗi batch 35 đoạn (`BATCH=35`, dòng 26).
- `next_batch_file` luôn là tên `NNN.json` kế tiếp còn trống.

**Bước AI hiện nay** do người làm tay: gõ `chạy flow cho projects/<pid>/stories/<sid>/<sid>.srt` trong Claude Code. Studio chỉ hiện câu lệnh đó kèm nút copy (`Pipeline.tsx:26, 42-51`). **Trong code chưa có chỗ nào gọi LLM.**

> ⚠️ Chạy `flow.py <srt>` **không có `--trang-thai`** sẽ thực thi mọi bước kế tiếp, **kể cả tạo ảnh TST**. Vì vậy bước AI tự động **không được cấp Bash** (xem [04 §7](04-thiet-ke-pipeline.md#7-loại-1-bước-ai-headless)).

---

## 5. Hợp đồng file của một truyện

```
projects/<pid>/project.json
projects/<pid>/video.json                              (tuỳ chọn, cấu hình video của chủ đề)
projects/<pid>/.claude/skills/*/SKILL.md               (bộ skill của chủ đề; skill vẽ chính = <style_skill>, đọc đầu tiên)
projects/<pid>/stories/<sid>/
  story.json  <sid>.srt  <sid>.<audio>  video.json (override từng cảnh)
  <sid>_anh/        segments.json  transcript.txt  bible.json  batches/NNN.json
  <sid>.prompts.json
  <sid>_images/     nv_<cid>.jpg  NNN_HH-MM-SS.jpg  state.json  timeline.csv  slideshow.ffconcat  _tst/
  <sid>_video/      *.mp4 + *.json  _cache/  _preview/
```

Mọi đường dẫn suy ra từ **tên gốc (stem) của file SRT** (`lib.js storyPaths` 207-228).

**`project.json`:**
```json
{"id","name","description","style_skill","aspect_ratio":"16:9","character_ratio":"9:16",
 "style":"<câu style tiếng Anh>","rules":{"switch_at":600,"early":[9,12],"late":[12,20]},"created":"YYYY-MM-DD"}
```
Chủ đề hiện có: `an-thai`, `campuchia`, `gia-dinh-ve-tay`, `truyen-ma`.

**`story.json`:**
```json
{"id","title","srt":"<sid>.srt","audio":"<sid>.mp3"|null,"created","source":{"srt","audio"},"lang"?}
```

**`segments.json`** (`srt_prompts.py split`, dòng 190-193):
```json
{"source","output","rules":{"switch_at":600.0,"early":[9.0,12.0],"late":[12.0,20.0]},"total_duration",
 "segments":[{"id":1,"start":"00:00:00,000","end":"00:00:09,739","duration":9.739,"zone":"early","in_range":true,"text":"..."}]}
```

Cách chia đoạn:
- Thời điểm của từng từ được **ước lượng bên trong cue** theo trọng số (`word_weight`: độ dài từ cộng khoảng nghỉ ở dấu câu).
- Quy hoạch động (DP) ưu tiên cắt ở cuối câu.
- Vì vậy **cue dài cỡ đoạn văn của DGT vẫn chia đúng** (`build_points` 92-123); `text` của đoạn được ghép xuyên qua ranh giới cue (dòng 185).

**`bible.json`** (`references\huong-dan-prompt.md:7-40`):
```json
{"aspect_ratio":"16:9","style":"<chép nguyên văn từ project.json>","setting":"...",
 "characters":[{"id":"fah","name":"Fah (ฟ้า)","prompt_name":"Fah","role":"<vi>","description":"<English, 35-70 words>"}]}
```

**`batches/NNN.json`:**
```json
[{"id":1,"prompt":"<English, 30-90 words>","characters":["fah"]}]
```

**`build`** (`cmd_build` 237-333):

| | Trường hợp |
|---|---|
| **Lỗi** (exit 1, không ghi gì) | thiếu field của nhân vật; JSON sai; id đoạn không tồn tại; id nhân vật không có trong bible; còn đoạn chưa có prompt |
| **Cảnh báo** | có dấu tiếng Việt; tên nhân vật không khớp danh sách; có "then"; < 15 hoặc > 130 từ |

Kết quả `<sid>.prompts.json`:
```json
{"source","total_duration","aspect_ratio","style","rules",
 "characters":[{"id","name","prompt_name","role","description","prompt"}],
 "images":[{"id","start","end","duration","prompt","characters":[],"text"}]}
```

---

## 6. `tao_anh.py`: tạo ảnh qua bridge

**CLI:** `tao_anh.py <x.prompts.json> [--api] [--api-key] [--out] [--ratio] [--ratio-nv] [--from] [--to] [--chi-nhan-vat] [--tao-lai 5,7,nv:thu] [--thu-lai 2] [--dry-run] [--xuat]`

**Gửi job:** toàn bộ ảnh còn thiếu được gửi trong tối đa 3 lời gọi `POST /jobs`. Tất cả dùng `outputFolder=<sid>_images/_tst` và `autoStart: true`.

| Nhóm | Job type | Tỉ lệ khung |
|---|---|---|
| Nhân vật | `text-to-image` | `character_ratio` (mặc định 9:16) |
| Cảnh không có nhân vật | `text-to-image` | `aspect_ratio` của truyện |
| Cảnh có nhân vật | `image-to-image` | `aspect_ratio` của truyện |

Với cảnh có nhân vật:
- `referenceImages` là đường dẫn `nv_<id>`, xếp theo thứ tự trong bible.
- Prompt được bọc bằng `REF_HEADER`: "image 1 (@nv_fah) is Fah; … Scene: …".

**Theo dõi:** `POST /queue/start`, sau đó lặp `GET /batches/{bid}?wait=15`:
- job `done`: đổi tên thành `nv_<id>.<ext>` hoặc `<id:03d>_<HH-MM-SS>.<ext>`;
- job lỗi: retry cho tới khi hết `--thu-lai`.

**`state.json`** được ghi atomic, lưu tên file tương đối:
```json
{"characters":{"fah":{"job_id","batch_id","tries":0,"file":"nv_fah.jpg"}},
 "images":{"7":{"job_id","batch_id","tries":0,"file":"007_00-01-03.jpg","error":"<tuỳ chọn>"}},"engine":"tst"}
```

**Đầu ra:**
- `timeline.csv` (UTF-8 BOM) luôn được ghi.
- `slideshow.ffconcat` chỉ được ghi khi có đủ ảnh.

> ⚠️ **`tao_anh.py` thoát với mã 0 kể cả khi có ảnh lỗi.** Pipeline phải tự kiểm `images.done == images.total` (lấy từ `flow --trang-thai`) và chạy `regen`/`scenes` lại nếu thiếu.

---

## 7. srt-video (`dung_video.py`)

**CLI:** `dung_video.py <story dir | .srt | .prompts.json> [--tu 0] [--den mm:ss] [--nhap] [--config FILE] [--out FILE] [--workers N] [--ngon-ngu vi|th|ko|ja|zh-TW|es|en] [--ke-hoach] [--mau] [--thu-chuyen-dong ID | --thu-chuyen-canh ID --anh N --giay 6]`

**Đầu vào** (`Story` 134-170):

| Thứ cần | Lấy từ đâu |
|---|---|
| SRT | `story.json["srt"]`, không có thì lấy file `*.srt` đầu tiên |
| Audio | `story.json["audio"]`, không có thì `<stem>` + `.mp3/.wav/.m4a/.aac/.ogg/.flac/.opus` |
| Timeline | **Bắt buộc** `<stem>_anh/segments.json` |
| Ảnh | `<stem>_images/state.json`. Thiếu ảnh nào thì dùng lại ảnh trước đó. |
| Cấu hình | `presets.json["defaults"]` → `projects/<pid>/video.json` → `--config`, sau đó chặn giới hạn bằng `sanitize` (285-331) |
| Override | `<story>/video.json["overrides"]` |

**Phụ đề:** `build_ass` → `screens_from_cues` (758-782) → `chunk_words` (715-742) với giới hạn `max_chars × lines` (mặc định 42 × 2). Mỗi màn hình là một sự kiện `Dialogue` riêng.

> **Kết luận:** cue 45 s / 499 ký tự từ DGT được tự tách thành khoảng 6-10 màn phụ đề 1-2 dòng, nên **không cần chia lại SRT trước khi đưa vào TST.**

**Đầu ra:**
- `<stem>_video/<stem>_<from>-<to>[_nhap]_<YYYYMMDD-HHMMSS>.mp4`, kèm file `.json` cùng tên: `{file, created, width, height, fps, from, to, duration, pieces, encoder, elapsed, size, …}`.
- stdout in `PROGRESS a/b` và dòng cuối `OUTPUT <đường dẫn>`.

**Hiệu năng:**
- Các mảnh render song song (`output.workers`, mặc định 6), có cache ở `_video/_cache/pieces/`.
- NVENC được tự dùng nếu có.
- Ví dụ thật: truyện campuchia 78:45, 285 mảnh, render khoảng 24 phút, file 5.37 GB.

---

## 8. Lỗi và bẫy đã biết (ảnh hưởng tự động hoá)

| # | Vấn đề | Vị trí | Cách pipeline xử lý |
|---|---|---|---|
| 1 | `uniqueStoryId`: tên không phải chữ Latin (Thái, Nhật…) cho slug rỗng, vòng lặp sinh id `-2`, sau đó bị `safeId` chặn (400) | `lib.js:354-358` | Pipeline tự đặt id ASCII `<slug≤48>-<runShort>`. Sửa lỗi ở P3. |
| 2 | `tao_anh.py` thoát 0 kể cả khi có ảnh lỗi | `tao_anh.py` | Kiểm `images.done == total`, tạo lại tối đa 3 lần |
| 3 | Upload truyện chạy split đồng bộ tới 120 s; split lỗi thì thư mục vẫn còn lại | `index.js:104-137` | Timeout HTTP ≥ 150 s; GET trước rồi mới POST |
| 4 | Job nằm trong RAM, mất khi restart | `index.js:183` | Suy lại trạng thái từ `flow --trang-thai`, rồi gọi lại bước |
| 5 | Bước `video` đòi có `prompts.json`; `/run` không nhận `config` | `index.js:241` | Luôn build trước; cấu hình video lấy từ chủ đề |
| 6 | Không có bước "ai" | — | Thêm `STEPS.ai` + `ai_flow.py` (P3) |
| 7 | `projects\campuchia\video.json` có `subtitle.max_chars: 60` nhưng script bỏ qua; độ dài dòng chỉ đọc từ `subtitle.langs.<lang>.max_chars` | `dung_video.py` | Ghi chú cho người cấu hình |

## 9. Quy tắc trong `CLAUDE.md` của TST

| Quy tắc | Pipeline tuân thủ bằng cách |
|---|---|
| "Hỏi trước khi tạo ảnh lần đầu cho một truyện (tốn lượt license), nói rõ số ảnh" | **Cổng duyệt prompt** bắt buộc: hiện số ảnh nhân vật + số ảnh cảnh, lưu `approvedAt`. Chưa duyệt thì không gọi `characters`/`scenes`. |
| "Không bao giờ chia lại timeline của truyện đã có `segments.json`" | Thao tác "chạy lại AI" chỉ xoá `bible.json` + `batches/`, **giữ `segments.json`**. Pipeline không bao giờ gọi `split` cho truyện đã có. |
| "Không tự tắt/mở app TST; đăng nhập license luôn do người bấm" | Pipeline chỉ đọc `/api/tst`. TST tắt hoặc chưa đăng nhập thì item chuyển `waiting_service`, chờ người xử lý. |
| "Python script chỉ dùng thư viện chuẩn; dùng `encoding="utf-8"`" | `ai_flow.py` mới tuân theo quy tắc này. |

## 10. Cách pipeline gọi TST Studio (tóm tắt, chi tiết ở [04](04-thiet-ke-pipeline.md))

1. `GET /api/tst` → `online && loggedIn`. Nếu không thoả thì `waiting_service`; quy tắc này chỉ áp dụng cho các bước cần TST.
2. `GET /api/projects/<pid>/stories/<sid>`: nếu 404 thì `POST /api/projects/<pid>/stories` (multipart `srt` + `audio` + `id` + `title`).
3. `POST …/run {step:"ai"}` *(bước mới)*, poll `/api/jobs/:id` cho tới khi `flow.next == "build"`.
4. `POST …/run {step:"build"}`.
5. **Chờ duyệt prompt.**
6. `POST …/run {step:"characters"}`.
7. Nếu bật cổng duyệt ảnh nhân vật thì chờ duyệt.
8. `POST …/run {step:"scenes"}`, kiểm `images.done == total`, thiếu thì `regen`.
9. `POST …/run {step:"video"}` → lấy `OUTPUT <path>` từ log, hoặc `GET …/video` → `renders`.
