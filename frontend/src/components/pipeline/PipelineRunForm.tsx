import { FileText, FolderSearch, Loader2, Play, Trash2, Upload } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type DragEvent } from "react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  createPipelineRun,
  getOutputDrives,
  getPipelineTstProjects,
  getPipelineVoices,
  getStoryLibraries,
  getSubtitleFonts,
  getSubtitlePresets,
  getTVEffectStyles,
  listPipelinePresets,
  scanPipelineFolder,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import type {
  OutputDriveOption,
  PipelinePreset,
  PipelineRun,
  PipelineRunConfig,
  PipelineTstProject,
  PipelineVideoType,
  PipelineVoice,
  StoryLibrary,
  SubtitleFontInfo,
  SubtitlePresetInfo,
  TVEffectStyle,
} from "@/types/api";

import { SUBTITLE_LANGS, formatBytes, selectClassName } from "./pipelineLabels";

const FORM_STORAGE_KEY = "pipeline.form.v1";

interface Entry {
  key: string;
  name: string;
  size: number;
  kind: string;
  type: PipelineVideoType;
  file?: File;
  path?: string;
  valid: boolean;
  warning?: string;
}

interface StoredForm {
  defaultType: PipelineVideoType;
  voiceRef: string;
  blankLine: boolean;
  tstProjectId: string;
  lang: string;
  gateCharacters: boolean;
  libraryIds: string[];
  subtitlePreset: string;
  subtitleFont: string;
  tvEffectStyleId: string;
  outputDrive: string;
  clipUsageMode: "reuse" | "once";
  folderPath: string;
  presetId: string;
}

const DEFAULT_FORM: StoredForm = {
  defaultType: 2,
  voiceRef: "",
  blankLine: false,
  tstProjectId: "",
  lang: "",
  gateCharacters: false,
  libraryIds: [],
  subtitlePreset: "clean",
  subtitleFont: "",
  tvEffectStyleId: "",
  outputDrive: "",
  clipUsageMode: "reuse",
  folderPath: "",
  presetId: "",
};

function readStoredForm(): StoredForm {
  try {
    const raw = window.localStorage.getItem(FORM_STORAGE_KEY);
    return raw ? { ...DEFAULT_FORM, ...(JSON.parse(raw) as Partial<StoredForm>) } : DEFAULT_FORM;
  } catch {
    return DEFAULT_FORM;
  }
}

function writeStoredForm(form: StoredForm) {
  try {
    window.localStorage.setItem(FORM_STORAGE_KEY, JSON.stringify(form));
  } catch {
    // localStorage bị chặn: chỉ mất phần nhớ lựa chọn.
  }
}

let entrySeq = 0;
const nextKey = () => `e${++entrySeq}`;

function TypeToggle({ value, onChange }: { value: PipelineVideoType; onChange: (value: PipelineVideoType) => void }) {
  return (
    <div className="inline-flex overflow-hidden rounded-md border border-input">
      {([1, 2] as const).map((type) => (
        <button
          key={type}
          type="button"
          onClick={() => onChange(type)}
          className={cn(
            "px-2.5 py-1 text-xs font-medium transition-colors",
            value === type ? "bg-primary text-primary-foreground" : "bg-background hover:bg-accent",
          )}
          title={type === 1 ? "Loại 1: ảnh minh hoạ (TST + AI)" : "Loại 2: clip nền"}
        >
          {type === 1 ? "1 · Ảnh" : "2 · Clip nền"}
        </button>
      ))}
    </div>
  );
}

export function PipelineRunForm({ onCreated }: { onCreated: (run: PipelineRun) => void }) {
  const [form, setForm] = useState<StoredForm>(readStoredForm);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [name, setName] = useState("");
  const [recursive, setRecursive] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [docLinks, setDocLinks] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);

  const [voices, setVoices] = useState<PipelineVoice[]>([]);
  const [voicesError, setVoicesError] = useState<string | null>(null);
  const [projects, setProjects] = useState<PipelineTstProject[]>([]);
  const [projectsError, setProjectsError] = useState<string | null>(null);
  const [libraries, setLibraries] = useState<StoryLibrary[]>([]);
  const [presets, setPresets] = useState<SubtitlePresetInfo[]>([]);
  const [fonts, setFonts] = useState<SubtitleFontInfo[]>([]);
  const [tvStyles, setTvStyles] = useState<TVEffectStyle[]>([]);
  const [drives, setDrives] = useState<OutputDriveOption[]>([]);
  const [configPresets, setConfigPresets] = useState<PipelinePreset[]>([]);

  const update = (patch: Partial<StoredForm>) =>
    setForm((current) => {
      const next = { ...current, ...patch };
      writeStoredForm(next);
      return next;
    });

  useEffect(() => {
    getPipelineVoices()
      .then((res) => setVoices(res.voices))
      .catch((err: unknown) => setVoicesError(err instanceof Error ? err.message : "Không lấy được giọng đọc."));
    getPipelineTstProjects()
      .then((res) => setProjects(res.projects))
      .catch((err: unknown) => setProjectsError(err instanceof Error ? err.message : "Không lấy được chủ đề TST."));
    getStoryLibraries().then((res) => setLibraries(res.libraries)).catch(() => undefined);
    getSubtitlePresets().then((res) => setPresets(res.presets.filter((p) => !p.hidden))).catch(() => undefined);
    getSubtitleFonts().then((res) => setFonts(res.fonts)).catch(() => undefined);
    getTVEffectStyles().then((res) => setTvStyles(res.styles)).catch(() => undefined);
    listPipelinePresets().then((res) => setConfigPresets(res.presets)).catch(() => undefined);
    getOutputDrives()
      .then((res) => {
        setDrives(res.drives);
        setForm((current) => (current.outputDrive ? current : { ...current, outputDrive: res.defaultDrive }));
      })
      .catch(() => undefined);
  }, []);

  const validEntries = entries.filter((entry) => entry.valid);
  const hasType1 = validEntries.some((entry) => entry.type === 1);
  const hasType2 = validEntries.some((entry) => entry.type === 2);

  const addFiles = (files: FileList | File[]) => {
    const accepted: Entry[] = [];
    const rejected: string[] = [];
    Array.from(files).forEach((file) => {
      const kind = file.name.toLowerCase().split(".").pop() ?? "";
      if (kind !== "docx" && kind !== "txt") {
        rejected.push(file.name);
        return;
      }
      if (file.name.startsWith("~$")) return;
      accepted.push({ key: nextKey(), name: file.name, size: file.size, kind, type: form.defaultType, file, valid: true });
    });
    setEntries((current) => [...current, ...accepted]);
    setNotice(rejected.length ? `Bỏ qua ${rejected.length} file không phải .docx/.txt: ${rejected.join(", ")}` : null);
  };

  const scanFolder = async () => {
    if (!form.folderPath.trim()) return;
    setScanning(true);
    setError(null);
    try {
      const result = await scanPipelineFolder(form.folderPath.trim(), recursive);
      const known = new Set(entries.map((entry) => entry.path).filter(Boolean));
      const added = result.files
        .filter((file) => !known.has(file.path))
        .map<Entry>((file) => ({
          key: nextKey(),
          name: file.name,
          size: file.size,
          kind: file.kind,
          type: form.defaultType,
          path: file.path,
          valid: file.valid,
          warning: file.warning,
        }));
      setEntries((current) => [...current, ...added]);
      setNotice(
        added.length
          ? `Thêm ${added.length} file từ ${result.path}.${result.warnings.length ? ` ${result.warnings.join("; ")}` : ""}`
          : "Không có file .docx/.txt mới trong thư mục.",
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Không quét được thư mục.");
    } finally {
      setScanning(false);
    }
  };

  const addDocLinks = () => {
    const links = docLinks
      .split(/\s+/)
      .map((link) => link.trim())
      .filter((link) => /^https?:\/\/docs\.google\.com\//.test(link));
    if (!links.length) {
      setNotice("Không có link Google Docs hợp lệ (https://docs.google.com/...).");
      return;
    }
    setEntries((current) => [
      ...current,
      ...links.map<Entry>((link) => ({ key: nextKey(), name: link, size: 0, kind: "gdoc", type: form.defaultType, path: link, valid: true })),
    ]);
    setDocLinks("");
    setNotice(`Thêm ${links.length} link Google Docs (tài liệu phải ở chế độ ai có link đều xem được).`);
  };

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    if (event.dataTransfer.files.length) addFiles(event.dataTransfer.files);
  };

  const setEntryType = (key: string, type: PipelineVideoType) =>
    setEntries((current) => current.map((entry) => (entry.key === key ? { ...entry, type } : entry)));

  const missing = useMemo(() => {
    const problems: string[] = [];
    if (!validEntries.length) problems.push("chưa có kịch bản");
    if (!form.voiceRef) problems.push("chưa chọn giọng đọc");
    if (hasType1 && !form.tstProjectId) problems.push("loại 1 cần chủ đề TST");
    if (hasType2 && !form.presetId && !form.libraryIds.length) problems.push("loại 2 cần thư viện clip");
    return problems;
  }, [validEntries.length, form.voiceRef, form.tstProjectId, form.libraryIds.length, form.presetId, hasType1, hasType2]);

  const submit = async () => {
    if (missing.length) return;
    setSubmitting(true);
    setError(null);
    const files: File[] = [];
    const items = validEntries.map((entry) => {
      if (entry.file) {
        files.push(entry.file);
        return { source: `upload:${files.length - 1}`, type: entry.type };
      }
      return { source: entry.path ?? "", type: entry.type };
    });
    const config: PipelineRunConfig = {
      defaultType: form.defaultType,
      ingest: { blankLineBetweenParagraphs: form.blankLine },
      tts: { voiceRef: form.voiceRef, format: "mp3", autoRetry: true },
      type1: {
        tstProjectId: form.tstProjectId || undefined,
        lang: form.lang || null,
        gates: { prompts: true, characters: form.gateCharacters },
      },
      type2: form.presetId ? { presetId: form.presetId } : {
        sharedConfig: {
          libraryIds: form.libraryIds,
          outputDrive: form.outputDrive || undefined,
          clipUsageMode: form.clipUsageMode,
          subtitlePreset: form.subtitlePreset || "clean",
          subtitleFont: form.subtitleFont || undefined,
          subtitleMaxCharsPerLine: 42,
          subtitleMaxLines: 2,
          ...(form.tvEffectStyleId ? { tvEffectStyleId: form.tvEffectStyleId } : {}),
        },
      },
      output: { drive: form.outputDrive || undefined },
    };
    try {
      const run = await createPipelineRun({ name, config, items }, files);
      setEntries([]);
      setName("");
      setNotice(`Đã tạo ${run.runId} với ${run.items.length} kịch bản.`);
      onCreated(run);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Không tạo được run.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <h2 className="text-lg font-semibold">Tạo lượt chạy mới</h2>
        <div className="flex items-center gap-2 text-sm">
          <span className="text-muted-foreground">Loại mặc định cho file mới:</span>
          <TypeToggle value={form.defaultType} onChange={(defaultType) => update({ defaultType })} />
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <div
          onDragOver={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          onClick={() => fileInput.current?.click()}
          className={cn(
            "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-2xl border-2 border-dashed p-6 text-center transition-colors",
            dragging ? "border-primary bg-primary/5" : "border-border hover:border-primary/60",
          )}
        >
          <Upload className="h-6 w-6 text-muted-foreground" />
          <div className="text-sm font-medium">Kéo thả file .docx / .txt vào đây</div>
          <div className="text-xs text-muted-foreground">hoặc bấm để chọn nhiều file</div>
          <input
            ref={fileInput}
            type="file"
            multiple
            accept=".docx,.txt"
            className="hidden"
            onChange={(event) => {
              if (event.target.files) addFiles(event.target.files);
              event.target.value = "";
            }}
          />
        </div>
        <div className="space-y-2 rounded-2xl border border-border/70 p-4">
          <Label htmlFor="pipeline-folder">Hoặc đường dẫn thư mục trên máy chạy server</Label>
          <div className="flex gap-2">
            <Input
              id="pipeline-folder"
              placeholder="D:\kich-ban\14-10"
              value={form.folderPath}
              onChange={(event) => update({ folderPath: event.target.value })}
              onKeyDown={(event) => {
                if (event.key === "Enter") void scanFolder();
              }}
            />
            <Button variant="secondary" onClick={() => void scanFolder()} disabled={scanning || !form.folderPath.trim()}>
              {scanning ? <Loader2 className="h-4 w-4 animate-spin" /> : <FolderSearch className="h-4 w-4" />}
              Quét
            </Button>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <Checkbox checked={recursive} onCheckedChange={(value) => setRecursive(value === true)} />
            Quét cả thư mục con
          </label>
          <p className="text-xs text-muted-foreground">
            File trong thư mục chỉ được đọc; docx gốc không bị xoá hay sửa.
          </p>
          <Label htmlFor="pipeline-gdocs" className="pt-2">Hoặc link Google Docs (mỗi dòng 1 link)</Label>
          <div className="flex gap-2">
            <Textarea
              id="pipeline-gdocs"
              rows={1}
              placeholder="https://docs.google.com/document/d/..."
              value={docLinks}
              onChange={(event) => setDocLinks(event.target.value)}
            />
            <Button variant="secondary" onClick={addDocLinks} disabled={!docLinks.trim()}>
              Thêm
            </Button>
          </div>
        </div>
      </div>

      {notice ? <p className="text-sm text-muted-foreground">{notice}</p> : null}

      {entries.length ? (
        <div className="overflow-x-auto rounded-2xl border border-border/70">
          <table className="w-full text-sm">
            <thead className="bg-muted/50 text-left text-xs uppercase text-muted-foreground">
              <tr>
                <th className="px-3 py-2">#</th>
                <th className="px-3 py-2">Kịch bản</th>
                <th className="px-3 py-2">Nguồn</th>
                <th className="px-3 py-2">Loại video</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              {entries.map((entry, index) => (
                <tr key={entry.key} className={cn("border-t border-border/60", !entry.valid && "opacity-60")}>
                  <td className="px-3 py-2 text-muted-foreground">{index + 1}</td>
                  <td className="px-3 py-2">
                    <div className="flex items-center gap-2">
                      <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
                      <span className="font-medium">{entry.name}</span>
                      {entry.kind !== "gdoc" ? <span className="text-xs text-muted-foreground">{formatBytes(entry.size)}</span> : null}
                    </div>
                    {entry.warning ? <div className="text-xs text-amber-700 dark:text-amber-300">⚠ {entry.warning}</div> : null}
                  </td>
                  <td className="max-w-[18rem] truncate px-3 py-2 text-xs text-muted-foreground" title={entry.path}>
                    {entry.file ? "Upload" : entry.kind === "gdoc" ? "Google Docs" : entry.path}
                  </td>
                  <td className="px-3 py-2">
                    {entry.valid ? <TypeToggle value={entry.type} onChange={(type) => setEntryType(entry.key, type)} /> : "—"}
                  </td>
                  <td className="px-3 py-2 text-right">
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => setEntries((current) => current.filter((item) => item.key !== entry.key))}
                      title="Bỏ file này"
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      <div className="grid gap-4 md:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="pipeline-name">Tên lượt chạy (tuỳ chọn)</Label>
          <Input id="pipeline-name" value={name} onChange={(event) => setName(event.target.value)} placeholder="Kênh Thái 14-10" />
        </div>
        <div className="space-y-2">
          <Label htmlFor="pipeline-voice">Giọng đọc (app TTS)</Label>
          {voices.length ? (
            <select
              id="pipeline-voice"
              className={selectClassName}
              value={form.voiceRef}
              onChange={(event) => update({ voiceRef: event.target.value })}
            >
              <option value="">— Chọn giọng —</option>
              {voices.map((voice) => (
                <option key={voice.voiceRef} value={voice.voiceRef}>
                  {voice.name}
                  {voice.language ? ` · ${voice.language}` : ""}
                </option>
              ))}
            </select>
          ) : (
            <Input
              id="pipeline-voice"
              value={form.voiceRef}
              onChange={(event) => update({ voiceRef: event.target.value })}
              placeholder="app-clone:12"
            />
          )}
          {voicesError ? <p className="text-xs text-amber-700 dark:text-amber-300">{voicesError}</p> : null}
        </div>
      </div>

      <label className="flex items-start gap-2 text-sm">
        <Checkbox checked={form.blankLine} onCheckedChange={(value) => update({ blankLine: value === true })} className="mt-0.5" />
        <span>
          Chèn dòng trống giữa các đoạn
          <span className="block text-xs text-muted-foreground">
            App TTS chỉ tách đoạn ở dòng trống. Bật lên thì mỗi dòng thành 1 đoạn đọc riêng (phụ đề ngắn hơn).
          </span>
        </span>
      </label>

      {hasType1 ? (
        <fieldset className="space-y-3 rounded-2xl border border-border/70 p-4">
          <legend className="px-1 text-sm font-semibold">Loại 1 · Ảnh minh hoạ (TST + AI)</legend>
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="pipeline-tst-project">Chủ đề TST (skill vẽ)</Label>
              <select
                id="pipeline-tst-project"
                className={selectClassName}
                value={form.tstProjectId}
                onChange={(event) => update({ tstProjectId: event.target.value })}
              >
                <option value="">— Chọn chủ đề —</option>
                {projects.map((project) => (
                  <option key={project.id} value={project.id}>
                    {project.name} ({project.id})
                  </option>
                ))}
              </select>
              {projectsError ? <p className="text-xs text-amber-700 dark:text-amber-300">{projectsError}</p> : null}
            </div>
            <div className="space-y-2">
              <Label htmlFor="pipeline-lang">Ngôn ngữ phụ đề</Label>
              <select id="pipeline-lang" className={selectClassName} value={form.lang} onChange={(event) => update({ lang: event.target.value })}>
                {SUBTITLE_LANGS.map((lang) => (
                  <option key={lang.value} value={lang.value}>
                    {lang.label}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <p className="text-xs text-muted-foreground">
            Sau khi AI viết xong bible + prompt, item dừng ở bước <b>Duyệt prompt</b> (bắt buộc, hiện số ảnh sẽ tạo). Chưa duyệt thì chưa tốn lượt tạo ảnh TST.
          </p>
          <label className="flex items-center gap-2 text-sm">
            <Checkbox checked={form.gateCharacters} onCheckedChange={(value) => update({ gateCharacters: value === true })} />
            Dừng thêm một lần để duyệt ảnh nhân vật trước khi tạo ảnh cảnh
          </label>
        </fieldset>
      ) : null}

      {hasType2 ? (
        <fieldset className="space-y-3 rounded-2xl border border-border/70 p-4">
          <legend className="px-1 text-sm font-semibold">Loại 2 · Clip nền</legend>
          <div className="space-y-2">
            <Label htmlFor="pipeline-preset">Cấu hình</Label>
            <select id="pipeline-preset" className={cn(selectClassName, "max-w-xl")} value={form.presetId} onChange={(event) => update({ presetId: event.target.value })}>
              <option value="">Chọn nhanh bên dưới</option>
              {configPresets.map((preset) => (
                <option key={preset.id} value={preset.id}>
                  Preset: {preset.name} ({preset.sharedConfig.libraryIds?.length ?? 0} thư viện)
                </option>
              ))}
            </select>
            <p className="text-xs text-muted-foreground">
              Preset lưu ở trang Story Video (nút “Lưu cấu hình cho Pipeline”) giữ đủ sóng âm, CTA, khung TV, bố cục, kiểu phụ đề.
            </p>
          </div>
          {form.presetId ? null : (
          <>
          <div className="space-y-2">
            <Label>Thư viện clip</Label>
            <div className="flex flex-wrap gap-2">
              {libraries.map((library) => {
                const checked = form.libraryIds.includes(library.id);
                return (
                  <label
                    key={library.id}
                    className={cn(
                      "flex cursor-pointer items-center gap-2 rounded-lg border px-2.5 py-1.5 text-sm",
                      checked ? "border-primary bg-primary/5" : "border-border",
                    )}
                  >
                    <Checkbox
                      checked={checked}
                      onCheckedChange={(value) =>
                        update({
                          libraryIds: value === true
                            ? [...form.libraryIds, library.id]
                            : form.libraryIds.filter((id) => id !== library.id),
                        })
                      }
                    />
                    {library.name}
                    <span className="text-xs text-muted-foreground">{library.clipCount} clip{library.styled ? " · đã bake" : ""}</span>
                  </label>
                );
              })}
              {!libraries.length ? <span className="text-sm text-muted-foreground">Chưa có thư viện.</span> : null}
            </div>
          </div>
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <div className="space-y-2">
              <Label htmlFor="pipeline-sub-preset">Hiệu ứng phụ đề</Label>
              <select id="pipeline-sub-preset" className={selectClassName} value={form.subtitlePreset} onChange={(event) => update({ subtitlePreset: event.target.value })}>
                {(presets.length ? presets : [{ id: "clean", name: "Clean", description: "" }]).map((preset) => (
                  <option key={preset.id} value={preset.id}>
                    {preset.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="pipeline-sub-font">Font phụ đề</Label>
              <select id="pipeline-sub-font" className={selectClassName} value={form.subtitleFont} onChange={(event) => update({ subtitleFont: event.target.value })}>
                <option value="">Mặc định</option>
                {fonts.map((font) => (
                  <option key={font.family} value={font.family}>
                    {font.family}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="pipeline-tv-style">Hiệu ứng TV</Label>
              <select id="pipeline-tv-style" className={selectClassName} value={form.tvEffectStyleId} onChange={(event) => update({ tvEffectStyleId: event.target.value })}>
                <option value="">Theo cấu hình chung</option>
                {tvStyles.map((style) => (
                  <option key={style.id} value={style.id}>
                    {style.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="pipeline-clip-mode">Dùng clip</Label>
              <select
                id="pipeline-clip-mode"
                className={selectClassName}
                value={form.clipUsageMode}
                onChange={(event) => update({ clipUsageMode: event.target.value as StoredForm["clipUsageMode"] })}
              >
                <option value="reuse">Tái sử dụng</option>
                <option value="once">Mỗi clip 1 lần (cần MongoDB)</option>
              </select>
            </div>
          </div>
          <p className="text-xs text-muted-foreground">
            Sóng âm, CTA, khung TV, lớp nhiễu theo cấu hình chung ở trang Story Video. Pipeline luôn tắt chế độ tối ưu (không tạm dừng app khác).
          </p>
          </>
          )}
        </fieldset>
      ) : null}

      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="space-y-2">
          <Label htmlFor="pipeline-drive">Ổ xuất video</Label>
          <select id="pipeline-drive" className={cn(selectClassName, "w-80")} value={form.outputDrive} onChange={(event) => update({ outputDrive: event.target.value })}>
            {drives.map((drive) => (
              <option key={drive.drive} value={drive.drive}>
                {drive.drive}: · {drive.outputDir} · trống {formatBytes(drive.freeBytes)}
              </option>
            ))}
            {!drives.length ? <option value="">Mặc định</option> : null}
          </select>
        </div>
        <div className="flex flex-col items-end gap-1">
          {missing.length ? <span className="text-xs text-muted-foreground">Còn thiếu: {missing.join(", ")}</span> : null}
          <Button size="lg" onClick={() => void submit()} disabled={submitting || missing.length > 0}>
            {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
            Bắt đầu chạy ({validEntries.length})
          </Button>
        </div>
      </div>
      {error ? <p className="text-sm text-destructive">{error}</p> : null}
    </div>
  );
}
