import { ExternalLink, FileAudio, FileText, FolderSearch, Loader2, Play, RefreshCw, Trash2, Upload } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type DragEvent } from "react";

import { Badge } from "@/components/ui/badge";
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
  PipelineInputMode,
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

import { AUDIO_EXTENSIONS, SUBTITLE_LANGS, formatBytes, selectClassName } from "./pipelineLabels";

const FORM_STORAGE_KEY = "pipeline.form.v1";
const MEDIA_ACCEPT = [...AUDIO_EXTENSIONS, "srt"].map((ext) => `.${ext}`).join(",");

interface Entry {
  key: string;
  /** Mỗi chế độ giữ danh sách riêng; chỉ hiện và gửi các dòng của chế độ đang chọn. */
  input: PipelineInputMode;
  name: string;
  size: number;
  kind: string;
  type: PipelineVideoType;
  /** Chế độ media: file audio (upload) hoặc đường dẫn audio (quét thư mục). */
  file?: File;
  path?: string;
  /** Chế độ media: tên gốc viết thường để ghép audio với .srt upload. */
  stem?: string;
  srtFile?: File;
  srtPath?: string;
  srtName?: string;
  /** Loại 1: chủ đề TST riêng của video này (bộ skill viết prompt ảnh); trống = chủ đề mặc định của lượt chạy. */
  tstProjectId?: string;
  valid: boolean;
  warning?: string;
}

interface StoredForm {
  inputMode: PipelineInputMode;
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
  inputMode: "script",
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

const extOf = (name: string) => name.toLowerCase().split(".").pop() ?? "";
const stemOf = (name: string) => name.replace(/\.[^.]+$/, "").toLowerCase();

/** Dòng upload ở chế độ media: hợp lệ khi đã có cả audio lẫn .srt cùng tên. */
function pairUploadedMedia(entry: Entry): Entry {
  const audio = entry.file;
  const srt = entry.srtFile;
  return {
    ...entry,
    name: audio?.name ?? srt?.name ?? entry.name,
    size: audio?.size ?? 0,
    kind: audio ? extOf(audio.name) : "srt",
    srtName: srt?.name,
    valid: Boolean(audio && srt),
    warning: !audio ? "Thiếu file audio cùng tên" : !srt ? "Thiếu file .srt cùng tên" : undefined,
  };
}

const INPUT_MODES: { value: PipelineInputMode; label: string; hint: string }[] = [
  { value: "script", label: "Kịch bản .docx / .txt", hint: "→ tạo audio + srt" },
  { value: "media", label: "Có sẵn audio + .srt", hint: "→ dựng video luôn" },
];

function InputModeToggle({ value, onChange }: { value: PipelineInputMode; onChange: (value: PipelineInputMode) => void }) {
  return (
    <div className="inline-flex flex-wrap overflow-hidden rounded-md border border-input" role="radiogroup" aria-label="Loại đầu vào">
      {INPUT_MODES.map((mode) => (
        <button
          key={mode.value}
          type="button"
          role="radio"
          aria-checked={value === mode.value}
          onClick={() => onChange(mode.value)}
          className={cn(
            "px-3 py-1.5 text-sm font-medium transition-colors",
            value === mode.value ? "bg-primary text-primary-foreground" : "bg-background hover:bg-accent",
          )}
        >
          {mode.label} <span className="font-normal opacity-80">{mode.hint}</span>
        </button>
      ))}
    </div>
  );
}

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

/** Bộ skill của một chủ đề TST: AI dùng để viết bible + prompt ảnh cho các video loại 1 thuộc chủ đề này. */
function TopicSkills({ id, project, count, tstUrl }: { id: string; project?: PipelineTstProject; count: number; tstUrl: string }) {
  const skills = project?.skills ?? [];
  return (
    <div className="rounded-xl border border-border/70 bg-muted/20 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-sm">
          <span className="font-medium">{project?.name ?? id}</span>{" "}
          <span className="text-xs text-muted-foreground">
            {id}
            {project?.aspectRatio ? ` · ${project.aspectRatio}` : ""} · {count} video
          </span>
        </div>
        {tstUrl ? (
          <a
            className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
            href={`${tstUrl}/p/${encodeURIComponent(id)}?skills=1`}
            target="_blank"
            rel="noreferrer"
          >
            Quản lý bộ skill <ExternalLink className="h-3 w-3" />
          </a>
        ) : null}
      </div>
      {!project ? (
        <p className="mt-1 text-xs text-amber-700 dark:text-amber-300">Không thấy chủ đề này trong TST Studio.</p>
      ) : skills.length ? (
        <ul className="mt-2 space-y-1">
          {skills.map((skill) => (
            <li key={skill.name} className="text-xs">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono font-medium">{skill.name}</span>
                {skill.main ? (
                  <Badge variant="outline" className="px-1.5 py-0 text-[10px]">
                    skill vẽ chính
                  </Badge>
                ) : null}
              </div>
              {skill.description ? (
                <p className="line-clamp-2 break-words text-muted-foreground" title={skill.description}>
                  {skill.description}
                </p>
              ) : null}
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-1 text-xs text-amber-700 dark:text-amber-300">Chủ đề chưa có skill nào: AI sẽ không có hướng dẫn vẽ riêng.</p>
      )}
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
  const [projectsLoading, setProjectsLoading] = useState(false);
  const [tstUrl, setTstUrl] = useState("");
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
    loadProjects();
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

  // Chủ đề TST + bộ skill; tải lại sau khi thêm / sửa skill bên TST Studio
  function loadProjects() {
    setProjectsLoading(true);
    getPipelineTstProjects()
      .then((res) => {
        setProjects(res.projects);
        setTstUrl(res.tstUrl ?? "");
        setProjectsError(null);
      })
      .catch((err: unknown) => setProjectsError(err instanceof Error ? err.message : "Không lấy được chủ đề TST."))
      .finally(() => setProjectsLoading(false));
  }

  const media = form.inputMode === "media";
  const shownEntries = entries.filter((entry) => entry.input === form.inputMode);
  const validEntries = shownEntries.filter((entry) => entry.valid);
  const hasType1 = validEntries.some((entry) => entry.type === 1);
  const topicOf = (entry: Entry) => entry.tstProjectId || form.tstProjectId;
  const projectById = useMemo(() => new Map(projects.map((project) => [project.id, project])), [projects]);
  const defaultProject = projectById.get(form.tstProjectId);
  // Chủ đề đang dùng cho các video loại 1 (kèm số video), để hiện bộ skill AI sẽ dùng
  const usedTopics = useMemo(() => {
    const counts = new Map<string, number>();
    validEntries.filter((entry) => entry.type === 1).forEach((entry) => {
      const id = entry.tstProjectId || form.tstProjectId;
      if (id) counts.set(id, (counts.get(id) ?? 0) + 1);
    });
    return [...counts.entries()];
  }, [validEntries, form.tstProjectId]);
  const hasType2 = validEntries.some((entry) => entry.type === 2);

  const addScriptFiles = (files: File[]) => {
    const accepted: Entry[] = [];
    const rejected: string[] = [];
    files.forEach((file) => {
      const kind = extOf(file.name);
      if (kind !== "docx" && kind !== "txt") {
        rejected.push(file.name);
        return;
      }
      if (file.name.startsWith("~$")) return;
      accepted.push({ key: nextKey(), input: "script", name: file.name, size: file.size, kind, type: form.defaultType, file, valid: true });
    });
    setEntries((current) => [...current, ...accepted]);
    const hasMedia = rejected.some((name) => extOf(name) === "srt" || AUDIO_EXTENSIONS.includes(extOf(name)));
    setNotice(
      rejected.length
        ? `Bỏ qua ${rejected.length} file không phải .docx/.txt: ${rejected.join(", ")}${hasMedia ? " — dùng audio có sẵn thì chọn “Có sẵn audio + .srt”." : ""}`
        : null,
    );
  };

  const addMediaFiles = (files: File[]) => {
    const next = [...entries];
    const rejected: string[] = [];
    files.forEach((file) => {
      const kind = extOf(file.name);
      const isSrt = kind === "srt";
      if (!isSrt && !AUDIO_EXTENSIONS.includes(kind)) {
        rejected.push(file.name);
        return;
      }
      if (file.name.startsWith("~$")) return;
      const stem = stemOf(file.name);
      const part = isSrt ? { srtFile: file } : { file };
      // Ghép vào dòng upload cùng tên đang thiếu đúng phần này (audio hoặc .srt).
      const index = next.findIndex(
        (entry) => entry.input === "media" && !entry.path && entry.stem === stem && !(isSrt ? entry.srtFile : entry.file),
      );
      if (index >= 0) next[index] = pairUploadedMedia({ ...next[index], ...part });
      else next.push(pairUploadedMedia({ key: nextKey(), input: "media", stem, name: file.name, size: 0, kind, type: form.defaultType, valid: false, ...part }));
    });
    setEntries(next);
    const unpaired = next.filter((entry) => entry.input === "media" && !entry.path && !entry.valid).length;
    const messages = [
      rejected.length ? `Bỏ qua ${rejected.length} file không phải audio/.srt: ${rejected.join(", ")}.` : "",
      unpaired ? `${unpaired} file chưa có cặp — audio và .srt phải trùng tên (vd Tap 1.mp3 + Tap 1.srt).` : "",
    ].filter(Boolean);
    setNotice(messages.length ? messages.join(" ") : null);
  };

  const addFiles = (files: FileList | File[]) => (media ? addMediaFiles : addScriptFiles)(Array.from(files));

  const scanFolder = async () => {
    if (!form.folderPath.trim()) return;
    const mode = form.inputMode;
    setScanning(true);
    setError(null);
    try {
      const result = await scanPipelineFolder(form.folderPath.trim(), recursive, mode);
      const known = new Set(entries.filter((entry) => entry.input === mode).map((entry) => entry.path).filter(Boolean));
      const added = result.files
        .filter((file) => !known.has(file.path))
        .map<Entry>((file) => ({
          key: nextKey(),
          input: mode,
          name: file.name,
          size: file.size,
          kind: file.kind,
          type: form.defaultType,
          path: file.path,
          srtPath: file.srtPath ?? undefined,
          srtName: file.srtName ?? undefined,
          valid: file.valid,
          warning: file.warning,
        }));
      setEntries((current) => [...current, ...added]);
      const warnings = result.warnings.length ? ` ${result.warnings.join("; ")}` : "";
      setNotice(
        added.length
          ? `Thêm ${added.length} ${mode === "media" ? "file audio" : "file"} từ ${result.path}.${warnings}`
          : `${mode === "media" ? "Không có cặp audio + .srt mới trong thư mục." : "Không có file .docx/.txt mới trong thư mục."}${warnings}`,
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
      ...links.map<Entry>((link) => ({ key: nextKey(), input: "script", name: link, size: 0, kind: "gdoc", type: form.defaultType, path: link, valid: true })),
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
  const setEntryTopic = (key: string, tstProjectId: string) =>
    setEntries((current) => current.map((entry) => (entry.key === key ? { ...entry, tstProjectId } : entry)));

  const missing = useMemo(() => {
    const problems: string[] = [];
    if (!validEntries.length) problems.push(media ? "chưa có cặp audio + .srt" : "chưa có kịch bản");
    if (!media && !form.voiceRef) problems.push("chưa chọn giọng đọc");
    const noTopic = validEntries.filter((entry) => entry.type === 1 && !(entry.tstProjectId || form.tstProjectId)).length;
    if (noTopic) problems.push(`${noTopic} video loại 1 chưa chọn chủ đề TST`);
    if (hasType2 && !form.presetId && !form.libraryIds.length) problems.push("loại 2 cần thư viện clip");
    return problems;
  }, [media, validEntries, form.voiceRef, form.tstProjectId, form.libraryIds.length, form.presetId, hasType2]);

  const submit = async () => {
    if (missing.length) return;
    const mode = form.inputMode;
    setSubmitting(true);
    setError(null);
    const files: File[] = [];
    const upload = (file: File) => {
      files.push(file);
      return `upload:${files.length - 1}`;
    };
    const items = validEntries.map((entry) => {
      const source = entry.file ? upload(entry.file) : entry.path ?? "";
      const tstProjectId = entry.type === 1 && entry.tstProjectId ? entry.tstProjectId : undefined;
      if (entry.input !== "media") return { source, type: entry.type, tstProjectId };
      return { source, srt: entry.srtFile ? upload(entry.srtFile) : entry.srtPath ?? "", type: entry.type, tstProjectId };
    });
    const config: PipelineRunConfig = {
      defaultType: form.defaultType,
      ingest: { blankLineBetweenParagraphs: form.blankLine },
      // Chế độ media không gọi app TTS: server không đòi giọng đọc.
      tts: { voiceRef: mode === "media" ? "" : form.voiceRef, format: "mp3", autoRetry: true },
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
      setEntries((current) => current.filter((entry) => entry.input !== mode));
      setName("");
      setNotice(`Đã tạo ${run.runId} với ${run.items.length} ${mode === "media" ? "cặp audio + srt" : "kịch bản"}.`);
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

      <div className="space-y-1.5">
        <InputModeToggle
          value={form.inputMode}
          onChange={(inputMode) => {
            update({ inputMode });
            setNotice(null);
          }}
        />
        <p className="text-xs text-muted-foreground">
          {media
            ? "Ghép mỗi file audio với file .srt cùng tên → kiểm tra phụ đề → dựng video. Không gọi app TTS, không cần chọn giọng đọc."
            : "Mỗi kịch bản (hoặc thư mục chứa nhiều file .txt/.docx) → app TTS tạo audio + srt → dựng video."}
        </p>
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
          <div className="text-sm font-medium">
            {media ? "Kéo thả file audio + .srt (cùng tên) vào đây" : "Kéo thả file .docx / .txt vào đây"}
          </div>
          <div className="text-xs text-muted-foreground">
            {media ? `hoặc bấm để chọn — audio: ${AUDIO_EXTENSIONS.join(", ")}` : "hoặc bấm để chọn nhiều file"}
          </div>
          <input
            ref={fileInput}
            type="file"
            multiple
            accept={media ? MEDIA_ACCEPT : ".docx,.txt"}
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
              placeholder={media ? "D:\\audio-srt\\14-10" : "D:\\kich-ban\\14-10"}
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
            {media
              ? "Lấy mọi file audio có .srt cùng tên trong cùng thư mục. File chỉ được đọc, không bị xoá hay sửa."
              : "File trong thư mục chỉ được đọc; docx gốc không bị xoá hay sửa."}
          </p>
          {media ? null : (
            <>
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
            </>
          )}
        </div>
      </div>

      {notice ? <p className="text-sm text-muted-foreground">{notice}</p> : null}

      {shownEntries.length ? (
        <div className="overflow-x-auto rounded-2xl border border-border/70">
          <table className="w-full text-sm">
            <thead className="bg-muted/50 text-left text-xs uppercase text-muted-foreground">
              <tr>
                <th className="px-3 py-2">#</th>
                <th className="px-3 py-2">{media ? "Audio + phụ đề" : "Kịch bản"}</th>
                <th className="px-3 py-2">Nguồn</th>
                <th className="px-3 py-2">Loại video · chủ đề</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              {shownEntries.map((entry, index) => (
                <tr key={entry.key} className={cn("border-t border-border/60", !entry.valid && "opacity-60")}>
                  <td className="px-3 py-2 text-muted-foreground">{index + 1}</td>
                  <td className="px-3 py-2">
                    <div className="flex items-center gap-2">
                      {entry.input === "media" ? (
                        <FileAudio className="h-4 w-4 shrink-0 text-muted-foreground" />
                      ) : (
                        <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
                      )}
                      <span className="font-medium">{entry.name}</span>
                      {entry.kind !== "gdoc" && entry.size ? <span className="text-xs text-muted-foreground">{formatBytes(entry.size)}</span> : null}
                    </div>
                    {entry.input === "media" && entry.srtName && (entry.file || entry.path) ? (
                      <div className="pl-6 text-xs text-muted-foreground">+ {entry.srtName}</div>
                    ) : null}
                    {entry.warning ? <div className="text-xs text-amber-700 dark:text-amber-300">⚠ {entry.warning}</div> : null}
                  </td>
                  <td className="max-w-[18rem] truncate px-3 py-2 text-xs text-muted-foreground" title={entry.path}>
                    {entry.file || entry.srtFile ? "Upload" : entry.kind === "gdoc" ? "Google Docs" : entry.path}
                  </td>
                  <td className="px-3 py-2">
                    {entry.valid ? (
                      <div className="flex flex-col items-start gap-1">
                        <TypeToggle value={entry.type} onChange={(type) => setEntryType(entry.key, type)} />
                        {entry.type === 1 ? (
                          <select
                            className={cn(selectClassName, "h-7 w-48 px-2 text-xs", !topicOf(entry) && "border-amber-500")}
                            value={entry.tstProjectId ?? ""}
                            onChange={(event) => setEntryTopic(entry.key, event.target.value)}
                            title="Chủ đề TST của video này: AI viết prompt ảnh theo bộ skill của chủ đề"
                            aria-label={`Chủ đề TST cho ${entry.name}`}
                          >
                            <option value="">{defaultProject ? `Mặc định: ${defaultProject.name}` : "— Chọn chủ đề —"}</option>
                            {projects.map((project) => (
                              <option key={project.id} value={project.id}>
                                {project.name}
                              </option>
                            ))}
                          </select>
                        ) : null}
                      </div>
                    ) : (
                      "—"
                    )}
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
        {media ? null : (
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
        )}
      </div>

      {media ? null : (
      <label className="flex items-start gap-2 text-sm">
        <Checkbox checked={form.blankLine} onCheckedChange={(value) => update({ blankLine: value === true })} className="mt-0.5" />
        <span>
          Chèn dòng trống giữa các đoạn
          <span className="block text-xs text-muted-foreground">
            App TTS chỉ tách đoạn ở dòng trống. Bật lên thì mỗi dòng thành 1 đoạn đọc riêng (phụ đề ngắn hơn).
          </span>
        </span>
      </label>
      )}

      {hasType1 ? (
        <fieldset className="min-w-0 space-y-3 rounded-2xl border border-border/70 p-4">
          <legend className="px-1 text-sm font-semibold">Loại 1 · Ảnh minh hoạ (TST + AI)</legend>
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="pipeline-tst-project">Chủ đề mặc định (video chưa chọn chủ đề riêng)</Label>
              <div className="flex gap-2">
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
                <Button variant="outline" size="icon" onClick={loadProjects} disabled={projectsLoading} title="Tải lại chủ đề + bộ skill từ TST Studio">
                  <RefreshCw className={cn("h-4 w-4", projectsLoading && "animate-spin")} />
                </Button>
              </div>
              {projectsError ? <p className="text-xs text-amber-700 dark:text-amber-300">{projectsError}</p> : null}
              {form.tstProjectId && projects.length && !defaultProject ? (
                <p className="text-xs text-amber-700 dark:text-amber-300">Chủ đề {form.tstProjectId} không còn trong TST Studio.</p>
              ) : null}
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
          <div className="space-y-2">
            <p className="text-xs text-muted-foreground">
              Mỗi video loại 1 chọn chủ đề ở cột <b>Loại video · chủ đề</b>. AI viết bible nhân vật + prompt ảnh theo <b>bộ skill</b> của
              chủ đề đó (skill vẽ chính đọc đầu tiên). Thêm / sửa skill ở TST Studio rồi bấm ↻ để cập nhật.
            </p>
            {usedTopics.map(([id, count]) => (
              <TopicSkills key={id} id={id} project={projectById.get(id)} count={count} tstUrl={tstUrl} />
            ))}
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
