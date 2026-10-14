# 01. App TTS: DgtAutoTTSMM + DGT Automation API

> Thư mục: `C:\Users\hiv\Documents\Cua Dung\DgtAutoTTSMM_v5\DgtAutoTTSMM_v5 - clone` (gọi tắt `ROOT`).
> Vai trò trong pipeline: kịch bản `.txt` → `.mp3` + `.srt`.
> Mọi `file:line` dưới đây tính từ `ROOT`.

[← Tổng quan](00-tong-quan-kien-truc.md) · [02 TST Studio →](02-app-tst-studio.md)

---

## 1. Kiến trúc

Thư mục này chứa **hai sản phẩm khác nhau**:

| Thành phần | Mô tả | Mã nguồn |
|---|---|---|
| `ROOT\DgtAutoTTSMM.exe` | App của nhà cung cấp (assembly `DgtAutoTTSMM3` v5.0.3.8), WinForms .NET Framework 4.8 x64. Tạo giọng qua **MiniMax web TTS**: điều khiển Chrome đã cài (`C:\Program Files\Google\Chrome\Application\chrome.exe`, profile `ROOT\minimaxdgt\`) qua remote debugging. | **Không có** (bị obfuscate; `.tools\*-analysis\*.decompiled.cs` chỉ có chữ ký hàm) |
| `src\DgtAutomation.Api` | **Automation API** tự viết: ASP.NET Core minimal API .NET 10, v0.2.0. Hàng đợi SQLite `.automation-data\jobs.sqlite`. Đọc giọng từ LiteDB. | Có |
| `src\DgtAutomation.Desktop` | "Bridge" điều khiển cửa sổ DGT bằng Win32 message + UI Automation. **Mỗi job chạy một tiến trình mới**, nói chuyện bằng JSON qua stdin/stdout. | Có |
| `src\DgtAutomation.Web` | Giao diện React 19 + Vite. Bản build được phục vụ từ `src\DgtAutomation.Api\wwwroot`. | Có |
| `tests\*` | 54 test xUnit (không gọi EXE thật). | Có |

Luồng của một job:

```
QueueWorker (Services\QueueWorker.cs:10-58)
  → LegacyDesktopAdapter.ExecuteAsync (Adapters\LegacyDesktopAdapter.cs:26-79)
    → DesktopBridgeClient: chạy `dotnet DgtAutomation.Desktop.dll`, ghi 1 dòng JSON vào stdin, đọc JSON lines từ stdout
      (Adapters\DesktopBridgeClient.cs:26-57)
      → DgtSession (src\DgtAutomation.Desktop\DgtSession.cs): bấm/gõ trong cửa sổ DGT
        → DGT gọi MiniMax → các file mp3 từng đoạn → input_full.mp3 + input_full.srt
  → PublishAsync: kiểm tra voice / audio / SRT → chép ra cạnh file txt gốc
```

Adapter có sẵn: `legacy-desktop` (điều khiển cửa sổ DGT) và `simulation` (dùng khi test). **Chưa có adapter gọi thẳng MiniMax.** Kế hoạch gốc gọi adapter đó là `OfficialTtsAdapter` (`docs\API_AUTOMATION_PLAN.vi.md:55-63`).

## 2. Khởi động và điều kiện bắt buộc làm tay

| Việc | Cách làm |
|---|---|
| Chạy API + web | `scripts\Start-Api.ps1 [-Port] [-Simulation] [-DataDirectory] [-AppDirectory]`. Script build bridge rồi chạy `dotnet run --project src/DgtAutomation.Api` với các biến `Automation__DataDirectory/AppDirectory/AllowSimulation/DesktopEnabled/DesktopBridgePath/DotnetPath` (dòng 23-45). |
| Chạy dev có quyền admin | `scripts\Start-Dev-Admin.ps1`: bật UAC, sau đó `npm run dev` (API + Vite cổng 5173). |
| Dừng | `scripts\Stop-Api.ps1` |
| Gửi batch thử | `scripts\Submit-Batch.ps1 -Paths ... -VoiceRef ... -Wait`. Lưu ý script này gửi `create_srt=$false` (dòng 47). |
| Build | .NET SDK 10.0.401 (`global.json`). `scripts\Setup.ps1` tải SDK về `.tools\dotnet`. `TreatWarningsAsErrors` đang bật. |

**Phải làm bằng tay trước khi pipeline chạy được** (không tự động hoá được):

1. Mở `DgtAutoTTSMM.exe`, **đúng 1 cửa sổ**, để rảnh: không chạy tay, không mở hộp thoại nào. Nếu không, job sẽ báo `desktop_app_not_running`, `desktop_multiple_instances`, `desktop_busy` hoặc `desktop_modal_open` (`DgtSession.cs:26-28, 66-74`).
2. Đăng nhập và cấu hình trong DGT:
   - tài khoản MiniMax (import file txt), token, proxy;
   - license/credit của nhà cung cấp (khoá theo máy qua `DgtCore.dll`).
3. **API phải chạy cùng mức quyền với DGT.** DGT hiện chạy quyền Administrator nên API cũng phải vậy. Lệch quyền sẽ báo `desktop_permission_mismatch` (`DgtSession.cs:33-36`).
4. Cần một phiên desktop tương tác: không chạy được như Windows service. Khi màn hình khoá hoặc RDP ngắt kết nối thì chưa được kiểm thử.
5. Giọng phải được clone sẵn trong DGT (mục Voice Clone → My Voices).
6. Các cài đặt trong DGT (lưu ở `config.db` có mật khẩu) ảnh hưởng đến cách chia đoạn và thời gian SRT, API không điều khiển được:
   - `MaxLength`, `MinLength`
   - các cài đặt khoảng lặng
   - `Thread`

## 3. Quy ước chung của API

- **Base URL:** `http://127.0.0.1:8787` (`src\DgtAutomation.Api\appsettings.json:2`). Chỉ nghe trên loopback, **không có xác thực**.
- **JSON dạng `snake_case`.** Field lạ bị **từ chối** với 400 (`Domain\Contracts.cs:7-11`, `UnmappedMemberHandling.Disallow`).
- **Header `Idempotency-Key`** (tuỳ chọn): 1-128 ký tự ASCII nhìn thấy được. Cùng key và cùng payload thì trả lại kết quả cũ (`replayed: true`); cùng key nhưng payload khác thì 409 (`Services\SubmissionService.cs:124-133`).
- **Lỗi** có dạng `{code, message}` (`Program.cs:45-72`):

| HTTP | `code` |
|---|---|
| 400 | `invalid_json`, `invalid_request` |
| 404 | `not_found` |
| 409 | `invalid_transition`, `idempotency_conflict`, `input_changed` |
| 413 | `file_too_large` |
| 422 | `invalid_input` |
| 429 | `queue_full` |
| 503 | `store_busy` (kèm `Retry-After: 1`) |

- **Giới hạn** (`Infrastructure\AutomationOptions.cs:11-18`, ghi đè được bằng env `Automation__*`):

| Tham số | Giá trị |
|---|---|
| `MaxFileBytes` | 10 MiB |
| `MaxBatchItems` | 1000 |
| `MaxQueuedJobs` | 5000 |
| **`DesktopTimeoutSeconds`** | **1800** (30 phút cho mỗi file) |

## 4. Danh sách endpoint (đủ 36 route, `Program.cs:77-232`)

### 4.1 Hệ thống

| Method | Path | Dòng | Response |
|---|---|---|---|
| GET | `/health` | 77 | `{status:"ok", service:"dgt-automation-api", version:"0.2.0"}` |
| GET | `/openapi/v1.json` | 78 | Tài liệu OpenAPI |
| GET | `/api/v1/runtime` | 80 | `{worker, active_job_id, adapters:[{adapter, ready, reason, produces_audio}], api_process{process_id, elevated}, desktop_privileges{app_process_id, app_elevated, bridge_process_id, bridge_elevated}, auto_retry{interval_seconds, max_attempts}}` |
| GET | `/api/v1/capabilities` | 89 | `{durable_queue, file_concurrency:1, queue_policy:"batch_fifo", max_file_bytes, max_batch_items, operations[], execution[], note}` |

> Pipeline dùng `GET /api/v1/runtime` để kiểm tra sức khoẻ. `adapters[legacy-desktop].ready=false` với `reason="desktop_busy"` **là bình thường khi đang có job chạy**, vì bridge đang giữ khoá.

### 4.2 File đầu vào

| Method | Path | Dòng | Request → Response |
|---|---|---|---|
| POST | `/api/v1/files` | 98 | multipart `file` + `kind` (`text` \| `voice_sample`) → 201 `FileView{file_id, original_name, extension, kind, size, sha256, created_at}` |
| GET | `/api/v1/files/{id}` | 110 | `FileView` |
| POST | `/api/v1/imports/preview` | 112 | `{paths:[abs], kind:"text"}` → `[{path, original_name, size, kind}]` |
| POST | `/api/v1/imports` | 122 | Body như trên → 201 `[FileView]`. **Không lưu đường dẫn gốc**, nên kết quả không được chép ra cạnh file txt. |
| POST | `/api/v1/imports/folder/preview` | 151 | `{directory_path, recursive=false}` → `{directory_path, recursive, files:[{path, relative_name, size}]}` |

### 4.3 Profile và giọng

| Method | Path | Dòng | Ghi chú |
|---|---|---|---|
| GET / POST | `/api/v1/profiles` | 135, 137 | `{name, settings:{model_id?, language="Vietnamese", speed=1 (0.5-2), pitch=0 (-12..12)}}`. Profile có đánh version. |
| GET / PUT | `/api/v1/profiles/{id}` | 136, 143 | |
| GET | `/api/v1/voices` | 148 | `[{voice_ref, name, adapter, state, created_at, source_job_id, source:"app_database", language, app_voice{record_id, voice_name, voice_id, language}}]` |
| GET | `/api/v1/voices/source` | 149 | `{database_path, collection:"Voice", available, count, error_code}` |

**`voice_ref` có dạng `app-clone:<_id>`**, trong đó `<_id>` là id dòng trong collection `Voice` của `ROOT\voiceclone.db` (LiteDB). Không dùng được `voice_id` vì mọi giọng clone đều lưu giá trị `"clone"` (`Services\VoiceCatalog.cs:10, 31-66`).

Khi chạy, bridge mở Voice Clone → My Voices, lọc theo tên và ngôn ngữ, bấm **Use** đúng dòng, kiểm tra combo `Voice:`, rồi ghi biên nhận `voice-selection.json`. API so khớp biên nhận này trước khi xuất file (`LegacyDesktopAdapter.cs:116-119`).

### 4.4 Tạo audio (pipeline dùng các endpoint này)

| Method | Path | Dòng | Request → Response |
|---|---|---|---|
| **POST** | **`/api/v1/runs/file`** | 152 | `FileRunRequest` (xem dưới) → 202 `{batch_id, job_ids[], replayed, status_url:"/api/v1/batches/{id}"}` |
| POST | `/api/v1/runs/folder` | 154 | `FolderRunRequest`: như trên nhưng thay `file_path` bằng `directory_path` + `recursive`. Chỉ lấy `.txt`, sắp xếp tự nhiên (1, 2, 10). |
| POST | `/api/v1/runs/clone` | 156 | `{sample_path, name, rights_confirmed:true, adapter}`. Mẫu dài 10-60 s, mp3 ≤ 2 MB. |
| POST | `/api/v1/batches` | 159 | `{name, items:[{file_id, client_ref?}], adapter, profile_id?, voice?{voice_ref \| clone_job_id}, execution?{file_concurrency:1, failure_policy}, output?, auto_retry=true}` |
| POST | `/api/v1/tts-jobs` | 168 | `{file_id, adapter, profile_id?, voice?, output?, client_ref?}` → 202 `{job_id, batch_id, replayed, status_url}` |
| POST | `/api/v1/voice-clone-jobs` | 170 | `{sample_file_id, name, rights_confirmed, adapter}` |

`FileRunRequest` (`Domain\Contracts.cs`):

```jsonc
{
  "file_path": "C:/storage/pipeline/runs/pl-xxxx/items/it-01/tap-01.txt", // tuyệt đối, chỉ .txt (Services\LocalRunService.cs:41-42)
  "voice_ref": "app-clone:12",
  "adapter": "legacy-desktop",          // mặc định
  "name": "pl-xxxx/it-01",              // tuỳ chọn, tên batch
  "profile_id": null,                   // tuỳ chọn
  "output": { "format": "mp3", "create_srt": true },   // format: mp3 | wav
  "failure_policy": "continue",         // continue | stop
  "auto_retry": true
}
```

### 4.5 Trạng thái, điều khiển, tải file

| Method | Path | Dòng | Response |
|---|---|---|---|
| GET | `/api/v1/queue` | 150 | `{policy, file_concurrency, head_batch_id, batches:[{batch_id, name, adapter, kind, state, position, total, succeeded, failed, cancelled, created_at}]}` |
| GET | `/api/v1/batches?limit=50&offset=0` | 172 | Danh sách batch |
| GET | `/api/v1/batches/summaries?limit=20&offset=0` | 174 | Danh sách rút gọn |
| GET | `/api/v1/batches/{id}` | 176 | `BatchView{batch_id, name, adapter, state, failure_policy, total, succeeded, failed, cancelled, created_at, jobs[JobView], auto_retry{enabled, attempts, max_attempts, interval_seconds, next_retry_at, exhausted}}` |
| GET | `/api/v1/jobs/{id}` | 177 | `JobView{job_id, batch_id, sequence, kind, adapter, state, stage, attempts, snapshot, created_at, started_at, finished_at, error_code, error_message, waiting_reason, result_voice_ref, artifacts[ArtifactView]}` |
| GET | `/api/v1/jobs/{id}/events?after=0&limit=100` | 178 | `[{cursor, job_id, state, stage, detail, created_at}]` |
| GET | `/api/v1/jobs/{id}/artifacts` | 180 | `[ArtifactView{artifact_id, job_id, kind: audio\|subtitles\|execution_report, name, content_type, size, sha256, download_url, saved_path}]` |
| GET | `/api/v1/artifacts/{id}/content` | 185 | Nội dung file (hỗ trợ Range) |
| POST | `/api/v1/batches/{id}/pause` · `/resume` · `/continue` · `/cancel` | 192-219 | Điều khiển batch |
| POST | `/api/v1/batches/{id}/auto-retry` | 209 | `{enabled: bool}` |
| POST | `/api/v1/jobs/{id}/cancel` · `/retry` | 220, 227 | `retry` chỉ dùng được với job ở trạng thái `failed` |

## 5. Trạng thái và xử lý lỗi

**Job state:**

| Nhóm | Giá trị |
|---|---|
| Chưa kết thúc | `queued`, `running`, `cancel_requested`, `needs_attention` |
| Kết thúc (`JobStates.IsTerminal`) | `succeeded`, `failed`, `cancelled` |

**Batch state** (`ResponseMapper.cs:52-68`): `queued`, `running`, `paused`, **`blocked`**, `cancel_requested`, `completed`, `completed_with_errors`, `cancelled`.

**Stage của job** (không có phần trăm):

```
queued → starting → validating_input → [reconciling_output] → selecting_voice → importing_txt
       → creating_audio → verifying_audio → saving_output → completed
```

**`waiting_reason`** cho job đang `queued`: `queue_order`, `batch_paused`, `clone_dependency`, hoặc lý do adapter chưa sẵn sàng.

**Quy tắc pipeline phải tuân theo:**

- **Chỉ chạy 1 file một lúc trên toàn hệ thống**, xếp hàng FIFO theo batch. `file_concurrency` bắt buộc bằng 1 (`SubmissionService.cs:18`).
- Batch đang `paused`, `blocked` hoặc có job `needs_attention` vẫn giữ chỗ trong hàng và chặn các batch phía sau.
- **`blocked` nghĩa là "đang chờ"**, vì job khác đang giữ DGT. Không được coi là lỗi. Lưu ý `Submit-Batch.ps1 -Wait` lại ném lỗi khi gặp `blocked` (dòng 61).
- **Lỗi do file** (422 `desktop_voice_missing`, `srt_invalid`, `audio_invalid`…): job chuyển `failed`. Nếu `failure_policy:"stop"` thì batch cũng tạm dừng.
- **Lỗi do môi trường** (`desktop_app_not_running`, `desktop_busy`, `desktop_modal_open`, `desktop_not_responding`, `desktop_permission_mismatch`, bridge timeout…): job chuyển `needs_attention` (`LegacyDesktopAdapter.cs:15-20`, `QueueWorker.cs:38-55`). Pipeline hiển thị trạng thái này là `waiting_service`.
- **Auto-retry:** mỗi 120 s, tối đa 5 lần liên tiếp (`AutoRetryService.cs`, `JobStore.RetryInterrupted` 511-533).
  - Khi retry, nếu lần chạy trước đã có `*_full.mp3` hoàn chỉnh thì dùng lại, với điều kiện: thư mục im lặng 15 s, có biên nhận, giọng khớp, qua kiểm tra ffmpeg và SRT (`LegacyDesktopAdapter.cs:84-110`).
- **Khi API khởi động lại**, job đang chạy chuyển `needs_attention` (`JobStore.RecoverInterrupted` 413-427).
- Bridge chịu được DGT treo UI tối đa 10 phút (`DgtSession.cs:250-259`).

## 6. Cách DGT chia đoạn và sinh SRT

| Khía cạnh | Hành vi |
|---|---|
| Đầu vào | `.txt` UTF-8 nghiêm ngặt; BOM được chấp nhận và bị bỏ (`Infrastructure\FileStore.cs:9, 62-66`). File rỗng hoặc chỉ có khoảng trắng bị từ chối. |
| Chụp đầu vào | File được chép vào `.automation-data\inputs\file_<guid>.txt` kèm SHA-256, và kiểm tra lại trước khi chạy (`FileStore.cs:130-139`). |
| **Chia đoạn** | **1 đoạn = 1 khối văn bản ngăn cách bởi dòng trống.** Các dòng liền nhau (không có dòng trống) thuộc cùng một đoạn. Khối dài hơn khoảng 500 ký tự bị cắt thành đoạn ≤ 499 ký tự (`SplitByMultipleEmptyLines(input, maxLength=500)` trong EXE). |
| Ngôn ngữ | Gắn với giọng clone (Thái, Indonesia, Việt…). `language` của profile chỉ dùng khi clone. |
| **SRT** | DGT tự tạo khi bridge tick ô "Tự động tạo Srt" (`DgtSession.cs:93`). **Thời gian lấy từ độ dài mp3 của từng đoạn**, không dùng Whisper hay căn chỉnh. Cue nối liền nhau, không có khoảng hở. |
| Độ dài cue | **Cỡ một đoạn văn**: tối đa 499 ký tự, thường 30-45 s. Ví dụ: `1.mp3` dài 45.04 s thì cue 1 là `00:00:00,000 --> 00:00:45,036`. |
| Định dạng SRT | UTF-8 **không BOM**, xuống dòng LF. Có thể còn sót ký tự CR trong cue nhiều dòng nếu nguồn dùng CRLF. |
| Audio | MP3 **32 kHz mono, khoảng 96 kb/s**. `format:"wav"` sẽ chuyển sang `pcm_s16le` bằng ffmpeg (`AudioOutputProcessor.cs:28-36`). |
| Kiểm tra SRT trước khi xuất | Mỗi dòng thời gian khớp `^\d{2,}:\d{2}:\d{2},\d{3}\s+-->\s+…`; thời điểm bắt đầu không lùi; end > start; end ≤ độ dài audio + 2 s (`LegacyDesktopAdapter.cs:192-211`). |
| Tốc độ thực tế | 123 KB văn bản Thái → 68 phút audio, mất khoảng 8 phút 15 giây. |

> **Hệ quả cho pipeline:** cue dài cỡ đoạn văn **không cần chia lại**. Cả `dung_video.py` (TST) và `story_subtitles.resegment` (Story Video Studio) đều tự tách thành các màn phụ đề ngắn. Xem [02 §7](02-app-tst-studio.md#7-srt-video-dung_videopy) và [03 §3](03-app-story-video-studio.md#3-audio-tts-và-phụ-đề).

## 7. Đầu ra: lấy đường dẫn mp3/srt ở đâu

Thư mục làm việc của DGT: `.automation-data\desktop-work\<job_id>\attempt-N\`. Trong đó có:
- `input.txt`
- `input\<n>.mp3` (mp3 từng đoạn)
- `input.dgt` (LiteDB)
- **`input_full.mp3`** và **`input_full.srt`**
- `voice-selection.json`, `execution.json`

Sau khi kiểm tra, `PublishAsync` (`LegacyDesktopAdapter.cs:112-148`) chép file ra hai nơi:

- **Cạnh file txt gốc:** `X.txt` → `X.mp3` + `X.srt`. Chỉ áp dụng khi đầu vào đi qua `runs/file` hoặc `runs/folder` (`FileStore.PublishBesideInputAsync` 175-219).
  - Nếu một trong hai tên đã tồn tại thì **cả hai** được đổi thành `X_2`, `X_3`… và không bao giờ ghi đè (dòng 190-196, 210-212).
- **Bản lưu trữ:** `.automation-data\outputs\<batch>\<job>\attempt-N\`

> ⚠️ **Luôn đọc `artifacts[].saved_path`** (kind `audio` / `subtitles`) trong `GET /api/v1/jobs/{id}`. Không tự suy ra là `X.mp3`.

`execution.json`: `{produces_audio, job_id, duration_seconds, voice_ref, voice_name, requested_voice, applied_voice, sha256, settings, adopted_from_attempt, saved_paths}`.

**Dung lượng:** `desktop-work` không bao giờ được dọn (hiện khoảng **13 GB**). Pipeline nên có nhắc nhở hoặc tác vụ dọn định kỳ (xem [05](05-ke-hoach-trien-khai.md)).

## 8. Công thức gọi cho pipeline

```python
import requests, time

DGT = "http://127.0.0.1:8787"

def tts_file(txt_path: str, voice_ref: str, key: str) -> dict:
    r = requests.post(f"{DGT}/api/v1/runs/file",
                      json={"file_path": txt_path, "voice_ref": voice_ref,
                            "output": {"format": "mp3", "create_srt": True},
                            "failure_policy": "continue", "auto_retry": True},
                      headers={"Idempotency-Key": key}, timeout=30)
    r.raise_for_status()
    return r.json()                      # {batch_id, job_ids:[...], replayed, status_url}

def poll_job(job_id: str) -> dict:
    job = requests.get(f"{DGT}/api/v1/jobs/{job_id}", timeout=10).json()
    # state: queued/running/cancel_requested → chờ tiếp
    #        needs_attention                  → waiting_service (DGT tắt, đang bận, có hộp thoại…)
    #        succeeded                        → lấy saved_path
    #        failed / cancelled               → lỗi
    return job

def outputs(job: dict) -> tuple[str, str]:
    by_kind = {a["kind"]: a["saved_path"] for a in job["artifacts"]}
    return by_kind["audio"], by_kind["subtitles"]
```

- Khoá idempotency của pipeline: `pl-<runId>-<itemId>-tts-<attempt>`. Nếu server pipeline restart rồi gửi lại thì nhận đúng batch cũ (`replayed: true`), không sinh job trùng.
- Mỗi item nên có **thư mục riêng** (`items/<itemId>/<slug>.txt`), để `X.mp3`/`X.srt` không bao giờ bị đổi tên `_2`.

## 9. Điểm mở rộng (khi cần sửa app TTS, ngoài phạm vi hiện tại)

| Nhu cầu | Chỗ sửa |
|---|---|
| Nhận `.docx` trực tiếp | `FileStore.TextExtensions` (`FileStore.cs:10`) + bộ lọc `.txt` ở `LocalRunService.cs:22, 41`. Pipeline đã tự chuyển docx nên **không cần**. |
| Gọi MiniMax trực tiếp, không qua GUI | Viết `IJobAdapter` mới (`Adapters\IJobAdapter.cs:5-10`), đăng ký ở `Program.cs` khoảng dòng 29-30 (`OfficialTtsAdapter` trong kế hoạch gốc). |
| Webhook khi xong | `JobStore.Complete` (379-399) |
| Đầu ra thêm (SRT chia nhỏ, JSON) | `LegacyDesktopAdapter.PublishAsync` (112-149) |

## 10. Script DocxToTxt.ps1 (tham chiếu cho bước INGEST)

**File:** `C:\Users\hiv\Documents\Cua Dung\script convert docx to txt\DocxToTxt.ps1`, kèm `Chuyen-Doi-Docx-Sang-Txt.bat` (`chcp 65001` rồi gọi PowerShell `-ExecutionPolicy Bypass`).

**Thuật toán:** dùng thuần .NET, không cần Word. `ZipFile.OpenRead` → đọc `word/document.xml` dạng UTF-8 (dòng 32-42), rồi chạy regex theo thứ tự:

1. Xoá `<mc:Fallback>…</mc:Fallback>` để text box không bị lặp (47).
2. Xoá `<w:instrText>` và `<w:delText>` (50-51).
3. Xoá khối `<w:tabs>` (56).
4. Đổi cấu trúc thành ký tự (62-67):

   | Word XML | Thành |
   |---|---|
   | `</w:p>`, `<w:p …/>` | CRLF |
   | `<w:br>` (mọi loại ngắt), `<w:cr>` | CRLF |
   | `<w:tab>` | TAB |
   | `<w:noBreakHyphen>` | `-` |

5. Xoá mọi tag còn lại (70).
6. Giải mã `&#n;`, `&#xh;`, `&lt; &gt; &quot; &apos; &amp;` (77-83).
7. Chuẩn hoá xuống dòng, `TrimEnd` từng dòng, `Trim` toàn văn bản (86-88).

**Không lấy được:** header, footer, footnote, comment, số thứ tự list tự động.

**Đầu ra:**
- `<thư mục docx>\<tên><hậu tố>.txt`, **UTF-8 có BOM**, **CRLF** (159-167).
- File `.txt` trùng tên bị **ghi đè không hỏi**.
- ⚠️ **Xoá file `.docx` gốc** nếu văn bản trích ra khác rỗng (172-175).
- Bỏ qua file tạm `~$*` (145-147).
- Chỉ chạy tương tác (`Read-Host`), không gọi được từ script khác.

**Pipeline** viết lại thuật toán này bằng Python stdlib, khớp đúng hành vi nhưng **không xoá docx**. Đặc tả nằm ở [04 §5](04-thiet-ke-pipeline.md#5-ingest-docx--txt).

> Lưu ý khi ghép với DGT: mỗi đoạn Word thành 1 dòng. DGT chỉ tách đoạn ở **dòng trống**, nên các đoạn Word liền nhau (không có đoạn rỗng xen giữa) sẽ bị gộp thành một đoạn TTS, tối đa khoảng 499 ký tự.

## 11. Thông tin nhạy cảm: không đưa vào tài liệu hay log

Pipeline **không đọc** các file sau:
- `config.db`, `proxy.db` (LiteDB có mật khẩu);
- `dgt-ytd-chrome.xml` (mật khẩu chỉ mã hoá base64);
- token MiniMax, `api.txt`, `local_api.txt`.

API cũng đã cố ý không trả về `user`, `ipaddress`, URL mẫu giọng hay token (`docs\APP_VOICES.vi.md:30`).
