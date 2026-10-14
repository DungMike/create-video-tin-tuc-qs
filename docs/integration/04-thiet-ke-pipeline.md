# 04. Thiết kế Pipeline: kịch bản → video (orchestrator trong Story Video Studio)

> Trạng thái: **thiết kế, chưa có code**. Lộ trình thực hiện xem [05](05-ke-hoach-trien-khai.md).
> API của các app được gọi tới: [01 TTS](01-app-tts-dgt.md) · [02 TST Studio](02-app-tst-studio.md) · [03 Story Video Studio](03-app-story-video-studio.md).
> Sơ đồ tổng thể: [00](00-tong-quan-kien-truc.md).

---

## 1. Mục tiêu và nguyên tắc

**Đầu vào:**
- Nhiều file upload, hoặc một đường dẫn folder (tuỳ chọn quét thư mục con).
- Định dạng `.docx` hoặc `.txt`. Docx tự chuyển sang txt.

**Đầu ra:** mỗi kịch bản cho ra 1 video `.mp4` hoàn chỉnh, theo một trong hai loại:

| Loại | Luồng | Có AI? | Dừng chờ người? |
|---|---|---|---|
| **1. Ảnh minh hoạ** | txt → audio + srt (DGT) → TST Studio: chia timeline, AI viết bible + prompt, tạo ảnh, dựng video | Có (Claude Code CLI headless) | **Duyệt prompt (bắt buộc)**; duyệt ảnh nhân vật (tuỳ chọn, mặc định tắt) |
| **2. Clip nền** | txt → audio + srt (DGT) → Story Video Studio render batch | Không | Không |

**Nguyên tắc:**
1. **Orchestrator nằm trong Story Video Studio (SVS).** App này đã có hàng đợi render loại 2, lưu trạng thái ra file và có frontend React.
2. **Không sửa app TTS.** Gọi qua Automation API có sẵn.
3. **Sửa TST Studio ở mức tối thiểu:** thêm bước `ai` và sửa `uniqueStoryId`. Mọi việc tạo ảnh và dựng video vẫn đi qua `/run` có sẵn.
4. **Trạng thái nằm trên đĩa và mọi bước đều idempotent.** Restart bất kỳ app nào cũng không sinh job trùng; trạng thái được suy lại từ hệ thống bên ngoài.
5. **Dịch vụ tắt hoặc chưa đăng nhập không phải là lỗi.** Item chuyển `waiting_service` và tự chạy tiếp khi dịch vụ sẵn sàng.
6. Tôn trọng các quy tắc vận hành:
   - không tự bật/tắt app TST hay DGT;
   - không chia lại timeline;
   - không tạo ảnh trước khi người duyệt;
   - không xoá file gốc của người dùng.

## 2. Thành phần mới

**Trong SVS:**

| File | Vai trò |
|---|---|
| `src/utils/pipeline_store.py` | Đọc/ghi `run.json` atomic (theo mẫu `_save_json` ở `story_video_pipeline.py:59-96`), khoá theo từng run, ghi `events.jsonl` |
| `src/utils/pipeline_ingest.py` | Quét folder, nhận upload, chuyển docx → txt, chuẩn hoá txt, tạo slug |
| `src/utils/pipeline_clients.py` | `DgtClient` (HTTP `:8787`), `TstClient` (HTTP `:5180`), dùng `requests` |
| `src/utils/pipeline_sv.py` | Gửi và theo dõi batch loại 2 trong cùng process |
| `src/utils/pipeline_runner.py` | Reconciler chạy nền + các stage handler + `resume_pipeline_runs()` |
| `src/routes/pipeline_routes.py` | Blueprint `/api/pipeline/*` |
| `frontend/src/pages/PipelinePage.tsx` + `components/pipeline-*` | Giao diện |

**Trong TST Studio:**

| File | Vai trò |
|---|---|
| `.claude/skills/srt-flow/scripts/ai_flow.py` | Vòng lặp gọi `claude -p` để viết bible và prompt, rồi build (§7) |
| `.claude/skills/srt-flow/references/ai-headless-prompt.md` | Mẫu file nhiệm vụ cho AI |
| `studio/server/index.js`, `lib.js` | Thêm `STEPS.ai`, điều kiện chặn, `GET /api/ai`; sửa `uniqueStoryId` |

## 3. Lưu trữ và mô hình dữ liệu

```
STORAGE_DIR/pipeline/                       (PIPELINE_DIR)
  runs/<runId>/
    run.json                                trạng thái run + mọi item (ghi atomic)
    events.jsonl                            nhật ký sự kiện, chỉ ghi nối thêm
    uploads/<n>_<tên gốc>                   file người dùng upload (giữ nguyên)
    items/<itemId>/
      <slug>.txt                            txt đã chuẩn hoá (đầu vào DGT)
      <slug>.mp3, <slug>.srt                DGT xuất ra cạnh txt
      <slug>.source.json                    {kind, originalPath, sha256, warnings}
```

> Mỗi item một thư mục riêng, nên DGT luôn xuất đúng `<slug>.mp3` / `<slug>.srt` và không bị đổi tên `_2` (xem [01 §7](01-app-tts-dgt.md#7-đầu-ra-lấy-đường-dẫn-mp3srt-ở-đâu)). Pipeline vẫn luôn đọc `saved_path`.

### 3.1 `run.json`

```jsonc
{
  "runId": "pl-ab12cd34",
  "name": "Kênh Thái 14-10",
  "status": "running",            // queued|running|paused|completed|completed_with_errors|cancelled
  "createdAt": "2026-10-14T09:00:00Z", "updatedAt": "…",
  "config": {
    "defaultType": 2,
    "ingest": { "blankLineBetweenParagraphs": false },
    "tts": { "voiceRef": "app-clone:12", "format": "mp3", "autoRetry": true },
    "type1": {
      "tstProjectId": "campuchia",
      "lang": null,                                   // ghi vào story.json.lang, null = tự nhận diện
      "gates": { "prompts": true, "characters": false },
      "ai": { "model": "opus", "batchesPerInvocation": 4 },
      "video": { "from": "0", "to": null }
    },
    "type2": { "sharedConfig": { "libraryIds": ["…"], "subtitlePreset": "clean", "...": "…" } },
    "output": { "drive": "D", "subdir": "pipeline-pl-ab12cd34" }
  },
  "items": [ /* xem 3.2 */ ]
}
```

### 3.2 Item

```jsonc
{
  "itemId": "it-01", "index": 1,
  "title": "ตอนที่ 1 – Tập 1",                // tên gốc (Unicode), hiển thị và dùng làm title truyện TST
  "slug": "tap-1",                           // ASCII ≤ 48 ký tự; rỗng thì dùng "item-01"
  "type": 1,
  "source": { "kind": "docx", "originalName": "Tập 1.docx", "path": "…/uploads/1_Tập 1.docx", "sha256": "…" },
  "stage": "GATE_PROMPTS",
  "status": "awaiting_review",               // xem §4.2
  "waitingReason": null,                     // vd "dgt:desktop_app_not_running", "tst:not_logged_in"
  "progress": { "percent": 45, "message": "Chờ duyệt 297 ảnh (12 nhân vật + 285 cảnh)" },
  "files": { "txt": "…", "audio": "…", "srt": "…", "video": null },
  "tts": { "attempt": 1, "idempotencyKey": "pl-ab12cd34-it-01-tts-1", "batchId": "…", "jobId": "…", "state": "succeeded" },
  "tst": {
    "projectId": "campuchia", "storyId": "tap-1-ab12",
    "activeJob": { "step": "ai", "jobId": "job12", "since": 140 },
    "flow": { "next": "characters", "segments": 285, "...": "…" },   // bản cuối của flow --trang-thai --json
    "approvals": { "prompts": { "at": "…", "images": 297 }, "characters": null },
    "sceneRounds": 0
  },
  "sv": { "batchId": null, "outputName": null },
  "error": null,                             // {stage, code, message, at}
  "createdAt": "…", "startedAt": "…", "finishedAt": null
}
```

### 3.3 `events.jsonl`

Mỗi dòng một sự kiện:

```json
{"seq":57,"at":"2026-10-14T09:12:03Z","itemId":"it-01","stage":"TTS","status":"running","message":"DGT: creating_audio"}
```

UI đọc qua `GET /runs/<rid>/events?since=<seq>`.

## 4. Stage machine

### 4.1 Các stage

| Stage | Loại | Lane | Điều kiện hoàn thành | Việc làm |
|---|---|---|---|---|
| `INGEST` | 1, 2 | — (chạy đồng bộ khi tạo run) | Đã có `items/<id>/<slug>.txt` hợp lệ | §5 |
| `TTS` | 1, 2 | DGT (FIFO của DGT) | Job DGT `succeeded`, có `saved_path` cho audio và srt | §6 |
| `SRT_CHECK` | 1, 2 | — | SRT parse được, > 0 cue, cue cuối lệch độ dài audio ≤ 2 s | §6 |
| `SV_RENDER` | 2 | Hàng đợi batch SVS | Story trong batch `completed`, có `videoPath` | §8 |
| `TST_STORY` | 1 | — | Truyện tồn tại trong TST, `flow.next ≠ split` | §9 |
| `AI` | 1 | AI (1) | `flow.next` ∉ {`bible`, `prompts`} và `built=true` | §7 |
| `BUILD` | 1 | — | `flow.built = true` (nếu `next=build` thì gọi `/run build`) | §9 |
| `GATE_PROMPTS` | 1 | — | Người bấm Duyệt | §10 |
| `CHARACTERS` | 1 | Ảnh TST (1) | `characters.done == total` | §9 |
| `GATE_CHARACTERS` | 1 (tuỳ chọn) | — | Người bấm Duyệt; bỏ qua nếu `gates.characters=false` | §10 |
| `SCENES` | 1 | Ảnh TST (1) | `images.done == total` | §9 |
| `VIDEO` | 1 | Render loại 1 (1) | Job `video` xong, có `OUTPUT <path>` | §9 |
| `COLLECT` | 1 | — | Video đã có trong thư mục xuất chung | §9 |
| `DONE` | 1, 2 | — | — | — |

### 4.2 Trạng thái item

| `status` | Ý nghĩa | Reconciler có xử lý? |
|---|---|---|
| `pending` | Chờ tới lượt (lane đầy) | Có |
| `running` | Đang có job bên ngoài chạy | Có (poll) |
| `waiting_service` | Dịch vụ tắt, chưa đăng nhập, DGT `needs_attention`, Mongo tắt khi cần… `waitingReason` ghi rõ lý do | Có (thử lại mỗi tick) |
| `awaiting_review` | Đang dừng ở cổng duyệt | Không, chờ API approve/reject |
| `needs_attention` | Đã tự thử lại hết số lần mà vẫn thiếu (vd thiếu ảnh sau 3 vòng) | Không, chờ người retry |
| `failed` | Lỗi không tự khắc phục được (file hỏng, SRT sai…) | Không |
| `cancelled` | Người huỷ | Không |
| `done` | Xong | Không |

**Trạng thái run:**
- `completed`: mọi item `done`.
- `completed_with_errors`: mọi item đã kết thúc nhưng có `failed` hoặc `cancelled`.

## 5. INGEST: docx → txt

**Nguồn file:**
- **Upload:** lưu vào `uploads/` và giữ nguyên.
- **Folder:** đọc trực tiếp từ đường dẫn của người dùng, **chỉ đọc, không bao giờ ghi hay xoá ở đó**.

**Quét folder** (`POST /scan`):
- Lấy `*.docx` và `*.txt` (không phân biệt hoa thường), bỏ file tạm `~$*`, sắp xếp tự nhiên (1, 2, 10).
- Trùng tên gốc (`X.docx` và `X.txt` cùng chỗ): dùng `.txt` và cảnh báo.
- File > 10 MiB (giới hạn của DGT) bị đánh dấu không hợp lệ.

**Chuyển docx** (`pipeline_ingest.docx_to_text`): viết lại DocxToTxt.ps1 bằng Python stdlib, **khớp từng bước**.
- Lưu ý: dòng PowerShell dùng `-replace` thì **không phân biệt hoa thường**; dòng dùng `[regex]::Replace` thì **có phân biệt**.

```python
xml = zipfile.ZipFile(path).read("word/document.xml").decode("utf-8")
# [regex]::Replace: phân biệt hoa thường
xml = re.sub(r"(?s)<mc:Fallback[^>]*>.*?</mc:Fallback>", "", xml)
xml = re.sub(r"(?s)<w:instrText[^>]*>.*?</w:instrText>", "", xml)
xml = re.sub(r"(?s)<w:delText[^>]*>.*?</w:delText>", "", xml)
xml = re.sub(r"(?s)<w:tabs(\s[^>]*)?>.*?</w:tabs>", "", xml)
# -replace: không phân biệt hoa thường
for pat, rep in [(r"</w:p>", "\r\n"), (r"<w:p(\s[^>]*)?/>", "\r\n"), (r"<w:br(\s[^>]*)?/?>", "\r\n"),
                 (r"<w:cr(\s[^>]*)?/?>", "\r\n"), (r"<w:tab(\s[^>]*)?/?>", "\t"),
                 (r"<w:noBreakHyphen(\s[^>]*)?/?>", "-")]:
    xml = re.sub(pat, rep, xml, flags=re.I)
text = re.sub(r"<[^>]+>", "", xml, flags=re.I)
text = re.sub(r"&#(\d+);", lambda m: chr(int(m[1])), text)
text = re.sub(r"&#x([0-9A-Fa-f]+);", lambda m: chr(int(m[1], 16)), text)
for ent, ch in [("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&apos;", "'"), ("&amp;", "&")]:  # &amp; cuối cùng
    text = re.sub(re.escape(ent), ch, text, flags=re.I)
text = text.replace("\r\n", "\n").replace("\r", "\n")
text = "\r\n".join(line.rstrip() for line in text.split("\n")).strip()
```

- **Ghi:** `items/<id>/<slug>.txt`, UTF-8 **có BOM**, CRLF (DGT chấp nhận BOM). **Không xoá docx.**
- **Golden test:** đặt vài file docx mẫu ở `tests/fixtures/pipeline/`, so kết quả với file txt do `DocxToTxt.ps1` sinh ra (byte-for-byte).
  - Sai khác có thể gặp: `str.strip()` của Python và `String.Trim()` của .NET coi một vài ký tự điều khiển hiếm là khoảng trắng khác nhau. Test sẽ phát hiện.

**File `.txt` đầu vào:**
- Thử đọc `utf-8-sig`; thất bại thì thử `utf-16` (nếu có BOM UTF-16); vẫn thất bại thì item `failed` với `encoding_unsupported` (DGT chỉ nhận UTF-8 nghiêm ngặt).
- Chép sang `items/<id>/<slug>.txt` (UTF-8 BOM, giữ nguyên nội dung và cách xuống dòng).
- Rỗng hoặc chỉ có khoảng trắng: `failed` với `empty_text`.

**Tuỳ chọn `blankLineBetweenParagraphs`** (mặc định **tắt**):
- DGT chỉ tách đoạn TTS ở dòng trống ([01 §6](01-app-tts-dgt.md#6-cách-dgt-chia-đoạn-và-sinh-srt)).
- Bật lên thì mỗi dòng không rỗng trở thành một đoạn TTS riêng: nhiều cue SRT ngắn hơn, nhiều lời gọi MiniMax hơn.
- Spike P0 sẽ quyết định giá trị mặc định cuối cùng.
- Cảnh báo khi có đoạn > 499 ký tự (DGT sẽ tự cắt).

**Slug và id:**
- `slug = slugify(stem)`: bỏ dấu (NFD), `đ` → `d`, ký tự ngoài `[a-z0-9]` → `-`, gộp các `-` liền nhau, tối đa 48 ký tự.
- Slug rỗng (tên Thái, Nhật…) thì dùng `item-<NN>`.
- `itemId = it-<NN>`.
- Id truyện TST = `<slug>-<4 ký tự cuối runId>`.

## 6. TTS và SRT_CHECK

**TTS handler** (mỗi tick):

1. Chưa có `tts.jobId`:
   - Kiểm tra `GET :8787/health`. Lỗi kết nối thì `waiting_service` (`dgt:api_down`).
   - Gửi `POST /api/v1/runs/file`, `file_path` là txt của item, header `Idempotency-Key: pl-<runId>-<itemId>-tts-<attempt>`.
   - Lưu `batchId`, `jobId = job_ids[0]`.
   - Gửi ngay khi item qua INGEST; DGT tự xếp hàng FIFO.
2. Đã có `jobId`: gọi `GET /api/v1/jobs/{jobId}`.

| Trạng thái DGT | Pipeline xử lý |
|---|---|
| `queued` / `running` / `cancel_requested` | `running`, `progress.message = "DGT: <stage>"`. Nếu `waiting_reason` liên quan desktop thì ghi kèm. |
| `needs_attention` | `waiting_service`, `waitingReason = "dgt:<error_code>"` (vd `desktop_app_not_running`). DGT tự retry mỗi 120 s; người có thể mở DGT rồi bấm Continue. |
| `succeeded` | `files.audio`, `files.srt` = `saved_path` của artifact `audio` / `subtitles`. Kiểm tra file tồn tại → `SRT_CHECK`. |
| `failed` | Nếu `attempt < PIPELINE_TTS_RETRY + 1` thì tăng `attempt` (khoá mới) và gửi lại; không thì `failed` với `error.code = error_code` của DGT. |
| `cancelled` | `cancelled` |

**SRT_CHECK:**
- Dùng lại `story_subtitles.parse_srt` (`src/utils/story_subtitles.py:190`) và `get_audio_duration` (`src/processors/audio_utils.py:5`).
- Đạt khi: > 0 cue, `end` cue cuối ≤ thời lượng audio + 2 s, và ≥ thời lượng audio − 2 s.
- Không đạt thì `failed` (`srt_invalid`).
- **Không chia lại SRT:** cả `dung_video.py` và `resegment` đều tự tách cue dài ([02 §7](02-app-tst-studio.md#7-srt-video-dung_videopy), [03 §3](03-app-story-video-studio.md#3-audio-tts-và-phụ-đề)).

## 7. Loại 1: bước AI headless

### 7.1 Vì sao dùng một script riêng (`ai_flow.py`) chứ không gọi claude trực tiếp từ SVS

- Script chạy trong thư mục làm việc của TST, qua cơ chế job sẵn có (`spawn`, log, cancel `taskkill /T`), nên UI của TST cũng có nút "Chạy AI" (kế hoạch P5 trong `docs/02` của TST).
- Mọi tri thức về hợp đồng file (bible, batches, build) nằm cùng chỗ với skill.
- SVS chỉ cần `POST /run {step:"ai"}` rồi poll như các bước khác.

### 7.2 `ai_flow.py`

**CLI:** `ai_flow.py <story dir | .srt> [--model opus] [--lo-moi 4] [--claude PATH] [--timeout 1800] [--ghi-chu FILE]`

Chỉ dùng thư viện chuẩn, `encoding="utf-8"`.

```
lặp:
  st = flow.py <srt> --trang-thai --json                         (đọc dòng cuối)
  nếu st.next == "bible":
      ghi _anh/_ai/task_bible.md:
        - đọc: .claude/skills/srt-prompt-anh/SKILL.md, references/huong-dan-prompt.md,
               st.style_skill, projects/<pid>/project.json (style), _anh/transcript.txt
        - ghi: CHỈ _anh/bible.json (style chép nguyên văn từ project.json)
      gọi claude
  nếu st.next == "prompts":
      lấy K = --lo-moi batch tiếp theo (35 đoạn/batch, bắt đầu từ st.prompts.next_batch)
      với mỗi batch: chạy `srt_prompts.py show <dir> --from lo --to hi` → _anh/_ai/segments_NNN.txt
      ghi _anh/_ai/task_prompts_NNN.md:
        - đọc: SKILL.md, huong-dan-prompt.md, skill vẽ, bible.json, các segments_NNN.txt
        - ghi: CHỈ _anh/batches/NNN.json tương ứng
      gọi claude
  nếu st.next == "build":
      chạy `srt_prompts.py build <dir>`
        exit 0 → in cảnh báo, thoát 0
        exit 1 → ghi _anh/_ai/task_fix_NNN.md (kèm lỗi build) → gọi claude → lặp
  nếu st.next ∈ {characters, scenes, done}: thoát 0
  in "PROGRESS <prompts.done + (bible?1:0)>/<segments + 1>"
  nếu 2 lần gọi liên tiếp mà st không đổi: thoát 1 ("AI không tiến triển")
```

- `--ghi-chu FILE`: ghi chú của người duyệt khi bấm "Chạy lại AI". Nội dung được chèn vào mọi file nhiệm vụ.
- **Kiểm tra ranh giới ghi:** trước mỗi lần gọi, chụp mtime của thư mục truyện. Sau khi gọi, nếu có file thay đổi ngoài `_anh/bible.json`, `_anh/batches/*`, `_anh/_ai/*` thì in `CANH BAO` và thoát 1.
- **Exit code:** 0 xong; 1 lỗi hoặc không tiến triển; 4 không tìm thấy claude.

### 7.3 Lệnh claude

```
<CLAUDE_BIN> -p "Đọc và làm đúng theo file nhiệm vụ <abs>/_anh/_ai/task_xxx.md. Chỉ ghi các file được liệt kê trong đó."
  --model opus
  --output-format stream-json --verbose
  --tools "Read,Write,Edit,Glob,Grep"
  --permission-mode acceptEdits
  --permission-prompts none
  --strict-mcp-config
  --setting-sources project,local
  --session-id <uuid mới mỗi lần gọi>
  -n "tst-ai <pid>/<sid>"
cwd = gốc TST (C:\Users\hiv\Downloads\TOOL IMAGE\clone)
```

- **Không cấp Bash**, vì `flow.py` chạy không có `--trang-thai` sẽ **tự tạo ảnh**. Mọi script đều do `ai_flow.py` chạy; AI chỉ đọc và ghi file.
- **Prompt ngắn**, chi tiết đặt trong file nhiệm vụ, để tránh giới hạn độ dài dòng lệnh trên Windows.
- **Không dùng `--bare`** (chỉ đọc `ANTHROPIC_API_KEY`, không dùng đăng nhập OAuth của Claude Code).
- `--max-turns` **chưa xác minh** được trên bản CLI hiện có. Thay bằng timeout theo thời gian thực (`--timeout`, mặc định 30 phút mỗi lần gọi) cộng phát hiện không tiến triển.
- **Tìm binary** theo thứ tự:
  1. env `CLAUDE_BIN`
  2. `shutil.which("claude")`
  3. bản mới nhất của `~/.vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude.exe`
  - Hiện **`claude` chưa có trong PATH**; chỉ có bản đi kèm extension VS Code, đường dẫn đổi theo mỗi lần cập nhật. Khuyến nghị người dùng chạy `claude install` để có binary ổn định.
- Stdout dạng stream-json được rút gọn, chỉ in đoạn văn bản ngắn của assistant và tên tool đã dùng, để log TST dễ đọc.
- Tất cả cờ trên đã có trong `claude --help` của bản 2.1.296, **sẽ được xác nhận lại ở spike P0**.

### 7.4 Thay đổi trong TST Studio

**`index.js` `STEPS`:**
```js
ai: { args: (p, b) => [AI_FLOW, p.dir, ...(b.note ? ["--ghi-chu", noteFile] : [])] },
```

**Điều kiện chặn mới:**
- `ai` cần đã có `segments.json` và `flow.next` ∈ {`bible`, `prompts`, `build`}.
- `ai` **không** đòi `prompts.json`: sửa chỗ kiểm tra ở dòng 241 để bỏ qua `ai`.
- `ai` không cần TST online.

**Thêm route:**
- `GET /api/ai` → `{found, path, version}`. Lấy `version` bằng `claude --version`, cache 60 s.
- `POST /run {step:"ai", note?}`: nếu có `note` thì ghi vào `_anh/_ai/note.md` rồi truyền `--ghi-chu`.
- `POST /api/projects/:pid/stories/:sid/ai/reset {scope:"all"|"batches", batchNos?}`:
  - `all` xoá `bible.json` + `batches/*`;
  - `batches` chỉ xoá các `NNN.json` được liệt kê.
  - **Không bao giờ đụng `segments.json`.** Trả 409 nếu truyện đang có job, hoặc đã có ảnh cảnh (tránh làm lệch ảnh đã tạo).

**Sửa lỗi:** `lib.js uniqueStoryId` (dòng 354-358): slug rỗng thì dùng `truyen`, và luôn kiểm tra `safeId` trước khi trả về.

## 8. Loại 2: render qua SVS

**Gửi batch:** `pipeline_sv.submit_story_batch(items, shared_config)`.
- Gọi route có sẵn **trong cùng process** bằng `app.test_client().post("/api/story-video/batch/create", data={"payload": json.dumps(...)})`.
- Dùng lại nguyên phần kiểm tra, xoay vòng decor/waveform/CTA và hàng đợi của route. Không cần tách route ngay (các helper trả `jsonify`, cần app context).
- Tách thành hàm service để ở P5, giữ nguyên chữ ký wrapper.

**Payload:**
- `items[]`: `{inputType:"audio_file", inputValue:<files.audio>, subtitleFile:<files.srt>, outputName:<slug>}`.
- `sharedConfig` = `config.type2.sharedConfig`, cộng thêm `outputDrive = config.output.drive`, `outputSubdir = config.output.subdir` (route hỗ trợ từ P2), **`optimizeMode: false`**.

**Micro-batch:** mỗi tick, gom mọi item loại 2 đang ở `SV_RENDER` mà chưa có `sv.batchId` thành **một** batch. Chỉ gửi khi pipeline **không còn batch nào đang `queued`** (đã gửi nhưng chưa chạy). Như vậy các item tới sau gom vào batch kế tiếp và chạy song song theo `STORY_BATCH_MAX_WORKERS`.

**Theo dõi:**
- `load_batch_progress(batchId)` (`story_video_batch.py:68`), ánh xạ theo `stories[].outputName`.
- `results[].videoPath` là đường dẫn tuyệt đối, ghi vào `files.video`.
- Story `failed`: pipeline tự gửi lại item đó trong batch mới, tối đa 1 lần (**không** dùng `retry-failed` vì nó làm mất `tvEffectStyleId`).

**Khôi phục:** route tự sinh `batchId`. Nếu server chết ngay sau khi gửi mà chưa kịp ghi `run.json`:
- `resume_pipeline_runs()` quét `STORAGE_DIR/story_video/batches/*/progress.json`, tìm story có `inputValue` nằm trong `runs/<runId>/items/` để gắn lại.
- Sau khi tách service ở P5 thì pipeline tự cấp `batchId` trước khi gửi.

**Bắt buộc chọn `libraryIds`** trên form, không dùng thư viện mặc định một cách ngầm định.

## 9. Loại 1: từ TST_STORY đến COLLECT

| Stage | Handler |
|---|---|
| `TST_STORY` | `GET /api/projects/<pid>/stories/<sid>`. Nếu 404 thì `POST /api/projects/<pid>/stories` (multipart: `srt`, `audio`, `id=<sid>`, `title=<item.title>`; timeout 180 s vì split chạy đồng bộ). Có `config.type1.lang` thì `PATCH {lang}`. Nếu `flow.next == "split"` (split lỗi) thì `failed` (`tst_split_failed`). |
| `AI` | Nếu story đang có job `running` thì gắn vào job đó (lấy từ `GET /api/jobs?project=&story=`). Không có thì `POST …/run {step:"ai"}`. Poll `GET /api/jobs/:id?since=` mỗi tick, đọc `PROGRESS a/b`. Job `done` thì đọc lại flow. Job `error` thì `needs_attention` (kèm 20 dòng log cuối). Lane AI = 1. |
| `BUILD` | `flow.next == "build"` thì `POST …/run {step:"build"}` rồi poll. `built=true` thì sang stage sau. |
| `GATE_PROMPTS` | `awaiting_review`. `progress.message` ghi số ảnh nhân vật và số ảnh cảnh sẽ tạo. Xem §10. |
| `CHARACTERS` | Cần `GET /api/tst` → `online && loggedIn`, không thì `waiting_service` (`tst:not_logged_in`). Lane ảnh = 1; đồng thời **chờ nếu TST Studio đang chạy job `characters`/`scenes`/`regen` của truyện khác** (người chạy tay). `POST …/run {step:"characters"}`, poll. Sau đó kiểm `characters.done == total`; thiếu thì `regen` các id `nv:<id>` còn thiếu, tối đa 2 vòng, quá thì `needs_attention`. |
| `GATE_CHARACTERS` | Chỉ khi `gates.characters = true`. |
| `SCENES` | `POST …/run {step:"scenes"}`, poll `[n/total]`. Xong thì kiểm `images.done == total`. Thiếu thì chạy `scenes` lại; `tao_anh` tự bỏ qua ảnh đã có. Tối đa `PIPELINE_SCENE_RETRY_ROUNDS` (3) vòng, quá thì `needs_attention` kèm danh sách id thiếu. |
| `VIDEO` | Lane render loại 1 = 1. `POST …/run {step:"video", from: config.type1.video.from, to}`. Poll `PROGRESS a/b`, lấy dòng `OUTPUT <path>`. Không có dòng đó thì lấy bản render mới nhất trong `GET …/video` → `renders`. |
| `COLLECT` | Đưa mp4 vào `<ổ>/output/story-video/<subdir>/<slug>.mp4` theo `PIPELINE_COLLECT_MODE`: `link` (hardlink nếu cùng ổ, khác ổ thì copy, **mặc định**), `copy`, hoặc `none` (chỉ ghi đường dẫn). Không di chuyển file gốc, vì TST Studio còn liệt kê nó. |

> **TST Studio restart** làm mất job trong RAM (`index.js:183`): `GET /api/jobs/:id` trả 404. Khi đó handler đọc lại `flow --trang-thai` (qua `GET story`) rồi gọi lại bước. Mọi bước đều chạy tiếp an toàn: `tao_anh` bỏ qua ảnh đã có, `ai_flow` tiếp tục từ batch kế tiếp, `dung_video` có cache mảnh.

## 10. Cổng duyệt

**`GET /api/pipeline/runs/<rid>/items/<iid>/review?gate=prompts`** lấy dữ liệu từ `GET /api/projects/<pid>/stories/<sid>` của TST. URL ảnh đổi thành tuyệt đối `http://127.0.0.1:5180/media/…`.

```jsonc
{
  "gate": "prompts",
  "counts": { "characters": 12, "scenes": 285, "totalImages": 297 },
  "characters": [{ "id": "fah", "name": "Fah (ฟ้า)", "role": "…", "description": "…", "prompt": "…", "image": null }],
  "scenes": [{ "id": 1, "start": "00:00:00,000", "end": "00:00:09,739", "text": "…", "prompt": "…", "characters": ["fah"] }],
  "warnings": ["…cảnh báo build…"],
  "tstUrl": "http://127.0.0.1:5180/p/campuchia/s/tap-1-ab12"
}
```

| Thao tác | API | Tác dụng |
|---|---|---|
| **Duyệt** | `POST …/approve {gate}` | Ghi `tst.approvals.<gate> = {at, images}`. Đây là **lần xin phép tạo ảnh** theo `CLAUDE.md` của TST. Nếu `flow.next == "build"` (người đã sửa tay) thì build trước rồi mới sang stage sau. |
| Chạy lại AI | `POST …/reject {gate:"prompts", action:"rerun_ai", note}` | Xoá `bible.json` + `batches/*`, **giữ `segments.json`**, chạy `ai` kèm `note` |
| Làm lại vài batch | `POST …/reject {gate:"prompts", action:"redo_batches", batchNos:[3,4], note}` | Xoá các `batches/NNN.json` đó rồi chạy `ai` |
| Tạo lại ảnh nhân vật | `POST …/reject {gate:"characters", action:"regen_characters", characterIds:["fah"]}` | TST `/run {step:"regen", ids:"nv:fah"}`, rồi quay lại cổng |
| Sửa tay | `action:"manual"` | Giữ `awaiting_review`; người sửa trong TST Studio (`tstUrl`) rồi bấm Duyệt |

Pipeline có quyền xoá file trong `_anh/` của truyện do **chính nó tạo**. Các thao tác xoá đều qua API mới của TST (`POST …/ai/reset {scope:"all"|"batches", batchNos}`) để không ghi trực tiếp vào thư mục của app khác.

## 11. Reconciler, lane và khôi phục

- **Một luồng nền** (daemon) chạy mỗi `PIPELINE_TICK_SECONDS` (3 s). Mỗi tick:
  1. đọc các run chưa kết thúc;
  2. với mỗi item không ở `awaiting_review` / `needs_attention` / `failed` / `cancelled` / `done`, gọi handler của stage hiện tại;
  3. ghi `run.json` atomic và nối `events.jsonl`.
- **Lane là bộ đếm**, tính lại mỗi tick từ trạng thái item:

| Lane | Giới hạn | Ghi chú |
|---|---|---|
| TTS | không giới hạn phía pipeline | DGT tự xếp FIFO, 1 file một lúc |
| AI | `PIPELINE_AI_CONCURRENCY` (1) | Tránh rate limit của Claude |
| Ảnh TST | `PIPELINE_TST_IMAGE_CONCURRENCY` (1) | Hàng đợi TST dùng chung; còn chờ job tay |
| Render loại 1 | `PIPELINE_TYPE1_RENDER_CONCURRENCY` (1) | `dung_video` dùng 6 worker |
| Render loại 2 | quy tắc micro-batch (§8) | Hàng đợi SVS |

- `PIPELINE_RENDER_EXCLUSIVE=true` (tuỳ chọn): `VIDEO` loại 1 chờ khi SVS đang render batch, để không vượt giới hạn phiên NVENC của GPU.
- **Các item chạy gối nhau:** trong lúc item 2 đang TTS, item 1 có thể đang AI, chờ duyệt hoặc tạo ảnh.
- **Khoá:** `RLock` cho từng run, dùng chung giữa reconciler và các route approve / cancel / retry.
- **Khi khởi động:** `resume_pipeline_runs()` được gọi ở `src/web_app.py` (`__main__`) và `wsgi.py`, cạnh `resume_batch_queue()`.
  - Không đánh dấu lỗi gì; reconciler tự hỏi lại trạng thái bên ngoài.
  - Khoá idempotency bảo đảm không gửi trùng: DGT dùng `Idempotency-Key`; TST thì GET trước POST và gắn vào job đang chạy; SVS quét batch.
- **Tạm dừng run:** không bắt đầu thêm việc mới, các job bên ngoài đang chạy vẫn chạy tiếp.
- **Huỷ item:**
  - DGT: `POST /jobs/{id}/cancel`
  - TST: `POST /api/jobs/:id/cancel`
  - SVS: `POST batch/<id>/items/<storyId>/cancel`
  - rồi đặt `cancelled`.
- **Retry item:** `POST …/retry {fromStage?}` xoá `error`, đặt lại stage. Nếu là `TTS` thì tăng `attempt` để dùng khoá mới.

## 12. API `/api/pipeline/*`

Lỗi có dạng `{"error":{"code","message"}}` (giống `_error` của SVS). Chỉ nghe trên localhost.

| Method | Path | Request | Response |
|---|---|---|---|
| GET | `/api/pipeline/health` | — | Xem dưới |
| POST | `/api/pipeline/scan` | `{path, recursive}` | `{path, files:[{path, name, stem, kind:"docx"|"txt", size, valid, warning?}], warnings}` |
| POST | `/api/pipeline/runs` | multipart `payload` + `files[]`, **hoặc** JSON `{name, folderPath, recursive, config, items?:[{path, type}]}` | 201 `{runId}`. INGEST chạy đồng bộ; item lỗi INGEST vẫn được tạo với `failed`. |
| GET | `/api/pipeline/runs?limit=20` | — | `[{runId, name, status, counts{total, done, failed, awaiting_review, running}, createdAt}]` |
| GET | `/api/pipeline/runs/<rid>` | — | `run.json` |
| GET | `/api/pipeline/runs/<rid>/events?since=<seq>` | — | `{events:[…], next}` |
| POST | `/api/pipeline/runs/<rid>/pause` · `/resume` · `/cancel` | — | run |
| POST | `/api/pipeline/runs/<rid>/items/<iid>/retry` | `{fromStage?}` | item |
| POST | `/api/pipeline/runs/<rid>/items/<iid>/cancel` | — | item |
| GET | `/api/pipeline/runs/<rid>/items/<iid>/review?gate=` | — | §10 |
| POST | `/api/pipeline/runs/<rid>/items/<iid>/approve` | `{gate}` | item |
| POST | `/api/pipeline/runs/<rid>/items/<iid>/reject` | `{gate, action, note?, batchNos?, characterIds?}` | item |
| GET | `/api/pipeline/tts/voices` | — | Proxy `GET :8787/api/v1/voices` → `[{voiceRef, name, language}]` |
| GET | `/api/pipeline/tst/projects` | — | Proxy `GET :5180/api/projects` → `[{id, name, style_skill, aspect_ratio}]` |

**`GET /api/pipeline/health`:**

```jsonc
{
  "services": {
    "dgt":       { "ok": true, "version": "0.2.0", "ready": false, "reason": "desktop_busy", "activeJobId": "…" },
    "tstStudio": { "ok": true },
    "tst":       { "online": true, "loggedIn": false },
    "claude":    { "found": true, "path": "…\\claude.exe", "version": "2.1.296" },   // qua GET :5180/api/ai
    "svQueue":   { "activeBatchId": "sb-…", "queued": 1 },
    "mongo":     { "enabled": true, "ok": true }
  },
  "warnings": [
    "RENDER_SUSPEND_PROCESS_NAMES chứa tiến trình pipeline cần dùng",
    "Đã dùng hết ảnh decor",
    "DGT desktop-work chiếm 13 GB"
  ]
}
```

## 13. Giao diện (`/pipeline`)

Thêm route trong `frontend/src/router.tsx` **trước** catch-all `*` (L27-30), và một mục "Tạo video tự động" trong `components/top-nav.tsx`.

```
┌ Tạo video tự động ───────────────────────────────────────────────────────────────┐
│ Dịch vụ: ● TTS DGT sẵn sàng   ● TST Studio   ○ TST chưa đăng nhập   ● Claude 2.1  │
│          ● Hàng đợi render (1 batch đang chạy)                    [Làm mới]       │
├──────────────────────────────────────────────────────────────────────────────────┤
│ Đầu vào: [ Kéo thả .docx/.txt vào đây ]  hoặc  Folder: [D:\kich-ban\14-10] [Quét] │
│   ☑ Tập 01.docx   Loại: (•)1 ( )2     ☑ Tập 02.txt   Loại: ( )1 (•)2   ⚠ trùng tên │
│ Giọng đọc: [app-clone:12 · Thai Female ▼]   ☐ Chèn dòng trống giữa các đoạn        │
│ Loại 1 → Chủ đề TST: [campuchia ▼]  Ngôn ngữ phụ đề: [Tự động ▼]                   │
│          ☑ Duyệt prompt (bắt buộc)  ☐ Duyệt ảnh nhân vật                           │
│ Loại 2 → Thư viện clip: [▼ nhiều]  Preset cấu hình: [Kênh Thái CRT ▼]              │
│ Xuất ra ổ: [D ▼]                                              [ Bắt đầu chạy ]    │
├──────────────────────────────────────────────────────────────────────────────────┤
│ Run pl-ab12cd34 · đang chạy · 2/5 xong · 1 chờ duyệt       [Tạm dừng] [Huỷ]      │
│ #  Kịch bản   Loại  Stage           Trạng thái         Tiến độ     Thao tác       │
│ 1  Tập 01     1     GATE_PROMPTS    ⏸ chờ duyệt        297 ảnh     [Duyệt…]       │
│ 2  Tập 02     2     SV_RENDER       ▶ render 62%       ███████░░   [Huỷ]          │
│ 3  Tập 03     1     TTS             ◌ chờ DGT (desktop_busy)        [Huỷ]          │
│ 4  Tập 04     2     DONE            ✓                  [Mở video]                 │
│ 5  Tập 05     1     SCENES          ⚠ cần xử lý: thiếu 3 ảnh       [Thử lại]      │
└──────────────────────────────────────────────────────────────────────────────────┘
```

**Hộp thoại duyệt (loại 1):**
- Tab "Nhân vật": thẻ nhân vật gồm tên, vai trò, mô tả; ở cổng `characters` có thêm ảnh.
- Tab "Prompt cảnh": bảng `id`, thời gian, lời kể, prompt, nhân vật; lọc được theo nhân vật.
- Thanh cảnh báo build.
- Nút: **Duyệt (tạo 297 ảnh)**, Chạy lại AI (kèm ô ghi chú), Làm lại batch…, Mở trong TST Studio.

**Cấu hình loại 2:** không dựng lại form 3385 dòng của `StoryVideoPage.tsx`.
- P2 tạm dùng form rút gọn: thư viện, preset phụ đề, font, style TV, ổ xuất.
- P5 thêm nút "Lưu cấu hình cho Pipeline" ở `StoryVideoPage` để lưu nguyên `sharedConfig` thành preset (`STORAGE_DIR/pipeline/presets/*.json`).

## 14. Cấu hình mới

**SVS (`src/config.py` + `.env`):**

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `PIPELINE_DIR` | `<STORAGE_DIR>/pipeline` | Thư mục lưu run |
| `DGT_API_URL` | `http://127.0.0.1:8787` | API TTS |
| `TST_STUDIO_URL` | `http://127.0.0.1:5180` | TST Studio |
| `PIPELINE_TICK_SECONDS` | 3 | Chu kỳ reconciler |
| `PIPELINE_TTS_RETRY` | 1 | Số lần pipeline gửi lại job TTS lỗi (ngoài auto-retry của DGT) |
| `PIPELINE_AI_CONCURRENCY` | 1 | |
| `PIPELINE_TST_IMAGE_CONCURRENCY` | 1 | |
| `PIPELINE_TYPE1_RENDER_CONCURRENCY` | 1 | |
| `PIPELINE_RENDER_EXCLUSIVE` | false | Render loại 1 chờ khi SVS đang render |
| `PIPELINE_SCENE_RETRY_ROUNDS` | 3 | |
| `PIPELINE_COLLECT_MODE` | `link` | `link` \| `copy` \| `none` |
| `PIPELINE_DEFAULT_OUTPUT_DRIVE` | `D` | |

**TST Studio (env của tiến trình node):**

| Biến | Ý nghĩa |
|---|---|
| `CLAUDE_BIN` | Đường dẫn claude.exe |
| `AI_MODEL` | Mặc định `opus` |
| `AI_TIMEOUT` | Giây cho mỗi lần gọi claude, mặc định 1800 |

## 15. Ma trận lỗi

| Tình huống | Nhận biết | Item | Tự động | Người cần làm |
|---|---|---|---|---|
| DGT API tắt | lỗi kết nối `:8787` | `waiting_service` (`dgt:api_down`) | Thử lại mỗi tick | Chạy `Start-Api.ps1` (quyền admin) |
| DGT EXE chưa mở / có hộp thoại | job `needs_attention`, `error_code` | `waiting_service` | DGT tự retry 120 s × 5 | Mở DGT, đóng hộp thoại, bấm Continue |
| Text sai encoding / rỗng | INGEST | `failed` | — | Sửa file, retry |
| SRT lệch audio | SRT_CHECK | `failed` (`srt_invalid`) | — | Kiểm tra cài đặt DGT, retry từ TTS |
| TST Studio tắt | lỗi kết nối `:5180` | `waiting_service` | Thử lại | `start-studio.cmd` |
| TST chưa đăng nhập | `/api/tst` | `waiting_service` (`tst:not_logged_in`) | Thử lại | Đăng nhập license |
| claude không có / hết phiên đăng nhập | `GET /api/ai`, `ai_flow` exit 4/1 | `needs_attention` | — | `claude install` / đăng nhập lại, retry |
| AI viết JSON sai | build exit 1 | (trong `ai_flow`) | Vòng sửa lỗi | Nếu vẫn hỏng: duyệt, sửa tay |
| Thiếu ảnh sau 3 vòng | `images.done < total` | `needs_attention` | — | Kiểm tra TST, retry |
| Render loại 2 lỗi | story `failed` | gửi lại 1 lần, rồi `failed` | Có | Xem log batch |
| Hết ảnh decor | health warning | — | — | `/decor-images/reset-used` |
| Mongo tắt khi `clipUsageMode=once` | 503 `mongo_unavailable` | `waiting_service` | Thử lại | Bật Mongo |

## 16. Bảo mật

- Cả 4 API (DGT `:8787`, TST Studio `:5180`, TST bridge `:5179`, SVS `:5006`) đều **không có xác thực**. Bắt buộc chỉ nghe trên `127.0.0.1`.
- `/scan` và `POST /runs` nhận đường dẫn folder bất kỳ, nhưng **chỉ đọc** `.docx` / `.txt` và không theo symlink ra ngoài folder đã chọn.
- Không ghi giá trị bí mật vào `run.json`, `events.jsonl` hay log (API key, token, mật khẩu).
- AI chỉ được dùng các tool Read / Write / Edit / Glob / Grep; ranh giới ghi được `ai_flow.py` kiểm tra (§7.2).
