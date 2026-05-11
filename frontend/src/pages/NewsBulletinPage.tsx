import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { AppShell, HeroCard, PageSection } from "@/components/app-shell";
import { BatchSourcePicker } from "@/components/batch-source-picker";
import { LoadingCard } from "@/components/loading-card";
import { StatusAlert } from "@/components/status-alert";
import { TopNav } from "@/components/top-nav";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import {
  ApiError,
  addBulletinResourcesFromSource,
  clearBulletinResources,
  createNewsBulletin,
  getChannels,
  getNewsBulletin,
  getNewsBulletinProgress,
  listNewsBulletins,
  parseNewsScript,
  startBulletinRender,
  updateBulletinChannels,
  updateNewsBulletinScript,
  uploadBulletinResources,
} from "@/lib/api";
import type {
  BulletinChannelProgress,
  BulletinDetailResponse,
  BulletinListItem,
  BulletinResourceDetails,
  BulletinResourceItem,
  BulletinResourceSummary,
  Channel,
  ChannelGroup,
  ParsedScript,
  ParsedNewsItem,
  ReviewClip,
} from "@/types/api";

type Step = "channels" | "script" | "resources" | "progress";

const STEP_ORDER: Step[] = ["channels", "script", "resources", "progress"];

function stageLabel(stage: string): string {
  const map: Record<string, string> = {
    pending: "Cho xu ly",
    tts_audio: "Tao audio (TTS)",
    timeline: "Tao timeline",
    prerender: "Pre-render clips",
    concat_audio: "Ghep audio",
    render: "Render video",
    overlay: "Overlay",
    completed: "Hoan tat",
    failed: "That bai",
  };
  return map[stage] ?? stage;
}

function statusBadge(status: string) {
  if (status === "completed") return "bg-green-500/20 text-green-400";
  if (status === "failed") return "bg-red-500/20 text-red-400";
  if (status === "running") return "bg-blue-500/20 text-blue-400";
  return "bg-gray-500/20 text-gray-400";
}

function hasResources(summary: BulletinResourceSummary, newsId: number): boolean {
  const res = summary[String(newsId)];
  return ((res?.vidClips ?? 0) + (res?.images ?? 0)) > 0;
}

export function NewsBulletinPage() {
  const { bulletinId } = useParams();
  const navigate = useNavigate();

  const [step, setStep] = useState<Step>("channels");
  const [isLoading, setIsLoading] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  const [scriptText, setScriptText] = useState("");
  const [lastParsedScriptText, setLastParsedScriptText] = useState("");
  const [parsedScript, setParsedScript] = useState<ParsedScript | null>(null);

  const [channels, setChannels] = useState<Channel[]>([]);
  const [groups, setGroups] = useState<ChannelGroup[]>([]);
  const [selectedChannelIds, setSelectedChannelIds] = useState<string[]>([]);
  const [filterGroupId, setFilterGroupId] = useState("");

  const [bulletinDetail, setBulletinDetail] = useState<BulletinDetailResponse | null>(null);
  const [resourceSummary, setResourceSummary] = useState<BulletinResourceSummary>({});
  const [resourceDetails, setResourceDetails] = useState<BulletinResourceDetails>({});

  const [bulletinList, setBulletinList] = useState<BulletinListItem[]>([]);
  const [showList, setShowList] = useState(false);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    getChannels()
      .then((res) => {
        setChannels(res.channels);
        setGroups(res.groups);
      })
      .catch(() => undefined);
  }, []);

  const allResourcesReady = useMemo(
    () => Boolean(parsedScript?.newsItems.length) && parsedScript!.newsItems.every((item) => hasResources(resourceSummary, item.id)),
    [parsedScript, resourceSummary],
  );

  const isLocked = Boolean(bulletinDetail && bulletinDetail.status !== "draft");
  const activeChannelIds = useMemo(() => new Set(channels.filter((channel) => channel.isActive).map((channel) => channel.channelId)), [channels]);
  const selectedActiveChannelCount = selectedChannelIds.filter((id) => activeChannelIds.has(id)).length;
  const scriptDirty = scriptText.trim() !== lastParsedScriptText.trim();

  useEffect(() => {
    if (!bulletinId) {
      setStep("channels");
      setBulletinDetail(null);
      setResourceSummary({});
      setResourceDetails({});
      setParsedScript(null);
      setScriptText("");
      setLastParsedScriptText("");
      return;
    }

    setIsLoading(true);
    getNewsBulletin(bulletinId)
      .then((detail) => {
        setBulletinDetail(detail);
        setParsedScript(detail.parsedScript);
        setScriptText(detail.scriptText || "");
        setLastParsedScriptText(detail.scriptText || "");
        setResourceSummary(detail.resourceSummary);
        setResourceDetails(detail.resourceDetails ?? {});
        setSelectedChannelIds(detail.channelIds);

        if (detail.status !== "draft") {
          setStep("progress");
        } else if (!detail.channelIds.length) {
          setStep("channels");
        } else if (!detail.parsedScript?.newsItems?.length) {
          setStep("script");
        } else {
          setStep("resources");
        }
      })
      .catch((err) => {
        setErrorMessage(err instanceof ApiError ? err.message : "Khong the tai bulletin.");
      })
      .finally(() => setIsLoading(false));
  }, [bulletinId]);

  useEffect(() => {
    if (step !== "progress" || !bulletinId) return;
    const poll = () => {
      getNewsBulletinProgress(bulletinId)
        .then((progress) => {
          setBulletinDetail((prev) =>
            prev
              ? {
                  ...prev,
                  status: progress.status,
                  channelIds: progress.channelIds,
                  channels: progress.channels,
                  updatedAt: progress.updatedAt,
                }
              : prev,
          );
          if (progress.status === "completed" || progress.status === "failed") {
            setIsSubmitting(false);
          }
        })
        .catch(() => undefined);
    };
    poll();
    pollRef.current = setInterval(poll, 3000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [step, bulletinId]);

  useEffect(() => {
    if (!showList) return;
    listNewsBulletins()
      .then((res) => setBulletinList(res.bulletins))
      .catch(() => undefined);
  }, [showList]);

  const filteredChannels = filterGroupId ? channels.filter((channel) => channel.groupId === filterGroupId) : channels;

  const channelProgress = bulletinDetail?.channels ?? {};
  const channelProgressItems = Object.values(channelProgress);
  const overallPercent = channelProgressItems.length
    ? Math.round(channelProgressItems.reduce((sum, item) => sum + Math.max(0, Math.min(100, item.percent)), 0) / channelProgressItems.length)
    : 0;

  const canOpenStep = (target: Step): boolean => {
    if (target === "channels") return !isLocked;
    if (target === "script") return !isLocked && selectedActiveChannelCount > 0;
    if (target === "resources") return !isLocked && Boolean(parsedScript?.newsItems.length) && Boolean(bulletinId);
    return Boolean(bulletinId);
  };

  const flashSuccess = (message: string) => {
    setSuccessMessage(message);
    window.setTimeout(() => setSuccessMessage(null), 3000);
  };

  const parseCurrentScript = async (): Promise<ParsedScript | null> => {
    setErrorMessage(null);
    if (!scriptText.trim()) {
      setErrorMessage("Can nhap noi dung kich ban hoac upload file .txt.");
      return null;
    }
    const res = await parseNewsScript(scriptText);
    setParsedScript(res.parsed);
    setLastParsedScriptText(scriptText);
    flashSuccess(`Da parse thanh cong: ${res.newsCount} tin tuc.`);
    return res.parsed;
  };

  const handleFileUpload = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.currentTarget.files?.[0];
    if (!file || isLocked) return;
    const reader = new FileReader();
    reader.onload = () => {
      if (typeof reader.result === "string") setScriptText(reader.result);
    };
    reader.readAsText(file, "utf-8");
  };

  const handleParseScript = async () => {
    setIsLoading(true);
    try {
      await parseCurrentScript();
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the parse kich ban.");
    } finally {
      setIsLoading(false);
    }
  };

  const handleChannelsNext = async () => {
    setErrorMessage(null);
    if (!selectedActiveChannelCount) {
      setErrorMessage("Can chon it nhat 1 channel active.");
      return;
    }
    if (bulletinId) {
      setIsLoading(true);
      try {
        await updateBulletinChannels(bulletinId, selectedChannelIds);
        setBulletinDetail((prev) => (prev ? { ...prev, channelIds: selectedChannelIds } : prev));
        flashSuccess("Da cap nhat channels.");
      } catch (err) {
        setErrorMessage(err instanceof ApiError ? err.message : "Khong the cap nhat channels.");
        return;
      } finally {
        setIsLoading(false);
      }
    }
    setStep("script");
  };

  const handleScriptNext = async () => {
    setErrorMessage(null);
    if (!selectedActiveChannelCount) {
      setErrorMessage("Can chon channel truoc khi tao bulletin.");
      setStep("channels");
      return;
    }

    setIsLoading(true);
    try {
      const parsed = !parsedScript || scriptDirty ? await parseCurrentScript() : parsedScript;
      if (!parsed?.newsItems.length) {
        setErrorMessage("Kich ban can co it nhat 1 tin tuc.");
        return;
      }

      if (bulletinId) {
        if (scriptDirty) {
          const res = await updateNewsBulletinScript(bulletinId, scriptText);
          setParsedScript(res.parsed);
          setLastParsedScriptText(scriptText);
          setResourceSummary(res.resourceSummary);
          setResourceDetails(res.resourceDetails ?? {});
          setBulletinDetail((prev) => (prev ? { ...prev, parsedScript: res.parsed, newsCount: res.newsCount } : prev));
          flashSuccess(res.resourcesReset ? "Da cap nhat script va reset tai nguyen theo danh sach tin moi." : "Da cap nhat script.");
        }
        setStep("resources");
        return;
      }

      const res = await createNewsBulletin(scriptText, selectedChannelIds);
      navigate(`/news-bulletin/${res.bulletinId}`, { replace: true });
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the luu kich ban.");
    } finally {
      setIsLoading(false);
    }
  };

  const handleResourceUpload = async (newsIdx: number, kind: "images" | "videos", files: FileList) => {
    if (!bulletinId || !files.length || isLocked) return;
    const formData = new FormData();
    Array.from(files).forEach((file) => formData.append(kind, file));
    try {
      const res = await uploadBulletinResources(bulletinId, newsIdx, formData);
      setResourceSummary(res.resourceSummary);
      setResourceDetails(res.resourceDetails ?? {});
      flashSuccess(`Da them ${res.addedVideos + res.addedImages} file cho tin ${newsIdx}.`);
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the upload resources.");
    }
  };

  const handleAssignSelectedClips = async (newsIdx: number, clips: ReviewClip[]) => {
    if (!bulletinId || !clips.length || isLocked) return;
    setErrorMessage(null);
    try {
      const res = await addBulletinResourcesFromSource(
        bulletinId,
        newsIdx,
        clips.map((clip) => clip.relativePath),
      );
      setResourceSummary(res.resourceSummary);
      setResourceDetails(res.resourceDetails ?? {});
      flashSuccess(`Da gan ${res.addedVideos + res.addedImages} clip/anh vao tin ${newsIdx}.`);
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the gan clip vao tin.");
    }
  };

  const handleClearResources = async (newsIdx: number) => {
    if (!bulletinId || isLocked) return;
    setErrorMessage(null);
    try {
      const res = await clearBulletinResources(bulletinId, newsIdx);
      setResourceSummary(res.resourceSummary);
      setResourceDetails(res.resourceDetails ?? {});
      flashSuccess(`Da don tai nguyen cua tin ${newsIdx}.`);
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the don tai nguyen.");
    }
  };

  const handleSubmitRender = async () => {
    if (!bulletinId) return;
    setErrorMessage(null);
    if (!allResourcesReady) {
      setErrorMessage("Can them tai nguyen cho tat ca tin truoc khi render.");
      return;
    }
    setIsSubmitting(true);
    try {
      const res = await startBulletinRender(bulletinId);
      setBulletinDetail((prev) => (prev ? { ...prev, status: res.status } : prev));
      setStep("progress");
    } catch (err) {
      setIsSubmitting(false);
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the bat dau render.");
    }
  };

  const toggleChannel = (channel: Channel) => {
    if (isLocked || !channel.isActive) return;
    setSelectedChannelIds((prev) =>
      prev.includes(channel.channelId) ? prev.filter((id) => id !== channel.channelId) : [...prev, channel.channelId],
    );
  };

  if (isLoading && !parsedScript && !bulletinDetail && bulletinId) {
    return (
      <AppShell>
        <LoadingCard message="Dang tai..." />
      </AppShell>
    );
  }

  return (
    <AppShell>
      <TopNav />
      <HeroCard
        eyebrow="News Bulletin"
        title="Tao video ban tin tuc tong hop"
        description="Chon channels -> Nhap/parse kich ban -> Chon tai nguyen -> Render video cho nhieu kenh YouTube cung luc."
        stats={[
          { label: "Tin tuc", value: parsedScript?.newsItems.length ?? 0 },
          { label: "Channels", value: selectedChannelIds.length },
          { label: "Tai nguyen OK", value: parsedScript ? `${parsedScript.newsItems.filter((item) => hasResources(resourceSummary, item.id)).length}/${parsedScript.newsItems.length}` : "0/0" },
          { label: "Status", value: bulletinDetail?.status ?? "draft" },
        ]}
      />

      {errorMessage ? <StatusAlert title="Loi" message={errorMessage} variant="destructive" /> : null}
      {successMessage ? <StatusAlert title="OK" message={successMessage} /> : null}

      <PageSection>
        <div className="flex flex-wrap gap-2">
          {STEP_ORDER.map((item, index) => (
            <Button
              key={item}
              variant={step === item ? "default" : "outline"}
              onClick={() => {
                if (canOpenStep(item)) setStep(item);
              }}
              disabled={!canOpenStep(item)}
            >
              {index + 1}. {item === "channels" ? "Channels" : item === "script" ? "Kich ban" : item === "resources" ? "Tai nguyen" : "Tien trinh"}
            </Button>
          ))}
          <div className="ml-auto">
            <Button variant="ghost" onClick={() => setShowList(!showList)}>
              {showList ? "An danh sach" : "Xem danh sach bulletins"}
            </Button>
          </div>
        </div>
      </PageSection>

      {showList ? (
        <PageSection>
          <h2 className="text-base font-semibold text-foreground mb-3">Danh sach Bulletins</h2>
          {bulletinList.length === 0 ? (
            <p className="text-sm text-muted-foreground">Chua co bulletin nao.</p>
          ) : (
            <div className="grid gap-2">
              {bulletinList.map((item) => (
                <Link
                  key={item.bulletinId}
                  to={`/news-bulletin/${item.bulletinId}`}
                  className="flex items-center justify-between rounded-lg border border-border/70 bg-background/70 p-3 hover:bg-background transition-colors"
                >
                  <div>
                    <span className="font-medium text-foreground">{item.bulletinId}</span>
                    <span className="ml-3 text-xs text-muted-foreground">
                      {item.newsCount} tin | {item.channelCount} channels
                    </span>
                  </div>
                  <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${statusBadge(item.status)}`}>{item.status}</span>
                </Link>
              ))}
            </div>
          )}
        </PageSection>
      ) : null}

      {step === "channels" ? (
        <PageSection>
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-foreground">1. Chon channels</h2>
            <Link to="/channels" className="text-sm text-primary hover:underline">
              Quan ly channels
            </Link>
          </div>

          <div className="flex flex-wrap gap-2 mb-4">
            <Button variant={filterGroupId === "" ? "default" : "outline"} size="sm" onClick={() => setFilterGroupId("")} disabled={isLocked}>
              Tat ca
            </Button>
            {groups.map((group) => (
              <Button key={group.groupId} variant={filterGroupId === group.groupId ? "default" : "outline"} size="sm" onClick={() => setFilterGroupId(group.groupId)} disabled={isLocked}>
                {group.groupName}
              </Button>
            ))}
          </div>

          <div className="grid gap-2">
            {filteredChannels.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                Chua co channel nao. <Link to="/channels" className="text-primary hover:underline">Tao channel moi</Link>
              </p>
            ) : null}
            {filteredChannels.map((channel) => (
              <label
                key={channel.channelId}
                className={`flex items-center gap-3 rounded-lg border border-border/70 bg-background/70 p-3 transition-colors ${
                  channel.isActive && !isLocked ? "cursor-pointer hover:bg-background" : "opacity-60"
                }`}
              >
                <input
                  type="checkbox"
                  checked={selectedChannelIds.includes(channel.channelId)}
                  onChange={() => toggleChannel(channel)}
                  disabled={isLocked || !channel.isActive}
                  className="h-4 w-4"
                />
                <div className="flex-1">
                  <div className="flex items-center gap-2">
                    <span className={`h-2 w-2 rounded-full ${channel.isActive ? "bg-green-500" : "bg-gray-400"}`} />
                    <span className="text-sm font-medium text-foreground">{channel.channelName}</span>
                    {channel.groupId ? <span className="text-xs text-muted-foreground">{groups.find((group) => group.groupId === channel.groupId)?.groupName}</span> : null}
                  </div>
                  <div className="text-xs text-muted-foreground mt-0.5">
                    Voice: {channel.voiceId || "-"} | Transition: {channel.transitionVideoPath ? "yes" : "-"}
                  </div>
                </div>
              </label>
            ))}
          </div>

          <div className="mt-5 flex flex-wrap items-center justify-between gap-3">
            <span className="text-sm text-muted-foreground">Da chon {selectedActiveChannelCount} channel active.</span>
            <Button onClick={handleChannelsNext} disabled={isLoading || isLocked || selectedActiveChannelCount === 0}>
              Next
            </Button>
          </div>
        </PageSection>
      ) : null}

      {step === "script" ? (
        <PageSection>
          <h2 className="text-lg font-semibold text-foreground mb-4">2. Nhap va parse kich ban</h2>
          <div className="grid gap-4">
            <div className="grid gap-2">
              <Label>Upload file .txt</Label>
              <Input type="file" accept=".txt" onChange={handleFileUpload} disabled={isLocked} />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="script-textarea">Hoac paste noi dung kich ban</Label>
              <Textarea
                id="script-textarea"
                rows={16}
                value={scriptText}
                onChange={(event) => setScriptText(event.target.value)}
                disabled={isLocked}
                placeholder={"//intro\nBAN TIN THE GIOI TOI...\n//resume-news-1\nUkraina tung con mua UAV...\n//detail-news-1\n1. Ukraina tan cong UAV quy mo lon...\n//end-outro\nCam on quy vi da theo doi..."}
                className="min-h-[300px] font-mono text-sm"
              />
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <Button onClick={handleParseScript} disabled={isLoading || isLocked}>
                {isLoading ? "Dang parse..." : "Parse kich ban"}
              </Button>
              {parsedScript && scriptDirty ? <span className="text-xs text-amber-500">Kich ban da thay doi, Next se parse/cap nhat lai.</span> : null}
            </div>
          </div>

          {parsedScript ? (
            <div className="mt-6 grid gap-4">
              <Separator />
              <h3 className="text-base font-semibold text-foreground">Preview kich ban da parse</h3>
              {parsedScript.intro.text ? (
                <div className="rounded-lg border border-border/70 bg-background/70 p-3">
                  <div className="text-xs font-semibold uppercase tracking-wider text-primary mb-1">INTRO</div>
                  <p className="text-sm text-foreground whitespace-pre-wrap">{parsedScript.intro.text}</p>
                </div>
              ) : null}
              {parsedScript.newsItems.map((item) => (
                <div key={item.id} className="rounded-lg border border-border/70 bg-background/70 p-3">
                  <div className="text-xs font-semibold uppercase tracking-wider text-primary mb-1">Tin #{item.id}</div>
                  <div className="mb-2">
                    <span className="text-xs font-medium text-muted-foreground">Tom tat:</span>
                    <p className="text-sm text-foreground">{item.resumeText.slice(0, 180)}{item.resumeText.length > 180 ? "..." : ""}</p>
                  </div>
                  <div>
                    <span className="text-xs font-medium text-muted-foreground">Chi tiet ({item.detailText.length} ky tu):</span>
                    <p className="text-sm text-foreground">{item.detailText.slice(0, 260)}{item.detailText.length > 260 ? "..." : ""}</p>
                  </div>
                </div>
              ))}
              {parsedScript.outro.text ? (
                <div className="rounded-lg border border-border/70 bg-background/70 p-3">
                  <div className="text-xs font-semibold uppercase tracking-wider text-primary mb-1">OUTRO</div>
                  <p className="text-sm text-foreground whitespace-pre-wrap">{parsedScript.outro.text.slice(0, 240)}</p>
                </div>
              ) : null}
            </div>
          ) : null}

          <div className="mt-5 flex flex-wrap items-center justify-between gap-3">
            <Button variant="outline" onClick={() => setStep("channels")} disabled={isLocked}>
              Back
            </Button>
            <Button onClick={handleScriptNext} disabled={isLoading || isLocked || !scriptText.trim()}>
              Next
            </Button>
          </div>
        </PageSection>
      ) : null}

      {step === "resources" && parsedScript ? (
        <PageSection>
          <h2 className="text-lg font-semibold text-foreground mb-2">3. Chon va review tai nguyen rieng cho tung tin</h2>
          <p className="text-sm text-muted-foreground mb-4">
            Moi tin co bo tai nguyen doc lap de bien tap hinh anh/video minh hoa sat voi noi dung cua tin do.
          </p>

          <div className="grid gap-5">
            {parsedScript.newsItems.map((item) => (
              <NewsResourceEditor
                key={item.id}
                item={item}
                bulletinReady={Boolean(bulletinId)}
                disabled={isLocked}
                summary={resourceSummary[String(item.id)]}
                details={(resourceDetails ?? {})[String(item.id)]}
                onUpload={handleResourceUpload}
                onAssign={handleAssignSelectedClips}
                onClear={handleClearResources}
              />
            ))}
          </div>

          <div className="mt-5 flex flex-wrap items-center justify-between gap-3">
            <Button variant="outline" onClick={() => setStep("script")} disabled={isLocked}>
              Back
            </Button>
            <Button onClick={handleSubmitRender} disabled={isSubmitting || isLocked || !allResourcesReady}>
              {isSubmitting ? "Dang submit..." : "Submit & Render"}
            </Button>
          </div>
        </PageSection>
      ) : null}

      {step === "progress" && bulletinDetail ? (
        <PageSection>
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-lg font-semibold text-foreground">4. Tien trinh tao video</h2>
              <p className="mt-1 text-sm text-muted-foreground">
                Status: <span className="font-medium text-foreground">{bulletinDetail.status}</span> | Channels: {channelProgressItems.length}
              </p>
            </div>
            <div className="text-2xl font-semibold text-foreground">{overallPercent}%</div>
          </div>

          <div className="mb-5 h-3 w-full overflow-hidden rounded-full bg-muted">
            <div className="h-full rounded-full bg-primary transition-all duration-500" style={{ width: `${overallPercent}%` }} />
          </div>

          <div className="grid gap-3">
            {channelProgressItems.map((ch: BulletinChannelProgress) => (
              <div key={ch.channelId} className="rounded-xl border border-border/70 bg-background/70 p-4 shadow-sm">
                <div className="flex items-center justify-between gap-3 mb-2">
                  <div className="flex min-w-0 items-center gap-2">
                    <span className="truncate text-sm font-semibold text-foreground">{ch.channelName}</span>
                    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${statusBadge(ch.status)}`}>{ch.status}</span>
                  </div>
                  <span className="whitespace-nowrap text-xs text-muted-foreground">{stageLabel(ch.stage)}</span>
                </div>
                <div className="h-2 w-full rounded-full bg-muted overflow-hidden">
                  <div className="h-full rounded-full bg-primary transition-all duration-500" style={{ width: `${Math.max(0, Math.min(100, ch.percent))}%` }} />
                </div>
                <div className="mt-1 flex items-center justify-between gap-2">
                  <span className="truncate text-xs text-muted-foreground">{ch.message}</span>
                  <span className="text-xs font-medium text-foreground">{ch.percent}%</span>
                </div>
                {ch.error ? <p className="mt-2 text-xs text-red-400">{ch.error}</p> : null}
                {ch.outputVideo ? (
                  <div className="mt-3 flex gap-2">
                    <Button asChild variant="outline" size="sm">
                      <a href={`/media/${ch.outputVideo}`} target="_blank" rel="noopener noreferrer">Preview</a>
                    </Button>
                    <Button asChild size="sm">
                      <a href={`/media/${ch.outputVideo}`} download>Download</a>
                    </Button>
                  </div>
                ) : null}
              </div>
            ))}
            {!channelProgressItems.length ? <p className="text-sm text-muted-foreground">Chua co tien trinh channel.</p> : null}
          </div>
        </PageSection>
      ) : null}
    </AppShell>
  );
}

function NewsResourceEditor({
  item,
  bulletinReady,
  disabled,
  summary,
  details,
  onUpload,
  onAssign,
  onClear,
}: {
  item: ParsedNewsItem;
  bulletinReady: boolean;
  disabled: boolean;
  summary?: { vidClips: number; images: number };
  details?: { vidClips: BulletinResourceItem[]; images: BulletinResourceItem[] };
  onUpload: (newsIdx: number, kind: "images" | "videos", files: FileList) => void;
  onAssign: (newsIdx: number, clips: ReviewClip[]) => void;
  onClear: (newsIdx: number) => void;
}) {
  const [selectedSourceClips, setSelectedSourceClips] = useState<ReviewClip[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const ready = ((summary?.vidClips ?? 0) + (summary?.images ?? 0)) > 0;
  const resources = details ?? { vidClips: [], images: [] };
  const totalResources = resources.vidClips.length + resources.images.length;

  return (
    <div className="rounded-xl border border-border/70 bg-background/70 p-4 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-foreground">
            Tin #{item.id}: {item.resumeText.slice(0, 100)}{item.resumeText.length > 100 ? "..." : ""}
          </h3>
          <p className="mt-1 text-xs text-muted-foreground">Detail: {item.detailText.length} ky tu</p>
        </div>
        <div className="flex flex-wrap gap-2 text-xs">
          <span className="rounded-full bg-blue-500/10 px-2 py-0.5 text-blue-400">{summary?.vidClips ?? 0} video</span>
          <span className="rounded-full bg-green-500/10 px-2 py-0.5 text-green-400">{summary?.images ?? 0} anh</span>
          <span className={`rounded-full px-2 py-0.5 ${ready ? "bg-green-500/10 text-green-400" : "bg-amber-500/10 text-amber-500"}`}>
            {ready ? "OK" : "Thieu"}
          </span>
        </div>
      </div>

      <div className="mt-4 rounded-lg border border-border/60 bg-card/40 p-3">
        <p className="text-sm text-foreground whitespace-pre-wrap">{item.detailText.slice(0, 520)}{item.detailText.length > 520 ? "..." : ""}</p>
      </div>

      <div className="mt-4 grid gap-3 md:grid-cols-3">
        <div className="grid gap-1">
          <Label>Upload video clips</Label>
          <Input
            type="file"
            accept=".mp4,.mov,.mkv,.webm"
            multiple
            disabled={!bulletinReady || disabled}
            onChange={(event) => {
              if (event.currentTarget.files) onUpload(item.id, "videos", event.currentTarget.files);
            }}
          />
        </div>
        <div className="grid gap-1">
          <Label>Upload anh</Label>
          <Input
            type="file"
            accept=".jpg,.jpeg,.png,.webp"
            multiple
            disabled={!bulletinReady || disabled}
            onChange={(event) => {
              if (event.currentTarget.files) onUpload(item.id, "images", event.currentTarget.files);
            }}
          />
        </div>
        <div className="grid content-end">
          <Button type="button" variant="outline" disabled={disabled} onClick={() => setPickerOpen((current) => !current)}>
            {pickerOpen ? "An nguon rieng" : "Chon nguon rieng cho tin nay"}
          </Button>
        </div>
      </div>

      {pickerOpen ? (
        <div className="mt-4 rounded-lg border border-border/70 bg-card/50 p-4">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <div>
              <h4 className="text-sm font-semibold text-foreground">Nguon rieng cho tin #{item.id}</h4>
              <p className="mt-1 text-xs text-muted-foreground">Dang chon {selectedSourceClips.length} clip cho tin nay.</p>
            </div>
            <Button
              type="button"
              variant="secondary"
              disabled={!bulletinReady || disabled || selectedSourceClips.length === 0}
              onClick={() => onAssign(item.id, selectedSourceClips)}
            >
              Gan {selectedSourceClips.length} clip vao tin #{item.id}
            </Button>
          </div>
          <BatchSourcePicker disabled={disabled} onSelectionChange={setSelectedSourceClips} />
        </div>
      ) : null}

      <div className="mt-4 rounded-lg border border-border/70 bg-card/50 p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <h4 className="text-sm font-semibold text-foreground">Review tai nguyen tin #{item.id}</h4>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={!bulletinReady || disabled || totalResources === 0}
            onClick={() => onClear(item.id)}
          >
            Don tai nguyen tin nay
          </Button>
        </div>

        {totalResources === 0 ? (
          <p className="text-sm text-muted-foreground">Chua co tai nguyen rieng cho tin nay.</p>
        ) : (
          <div className="grid gap-4">
            {resources.vidClips.length ? (
              <ResourceReviewGrid title="Video minh hoa" items={resources.vidClips} kind="video" />
            ) : null}
            {resources.images.length ? (
              <ResourceReviewGrid title="Anh minh hoa" items={resources.images} kind="image" />
            ) : null}
          </div>
        )}
      </div>
    </div>
  );
}

function ResourceReviewGrid({
  title,
  items,
  kind,
}: {
  title: string;
  items: BulletinResourceItem[];
  kind: "video" | "image";
}) {
  return (
    <div className="grid gap-2">
      <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{title}</div>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {items.map((item) => (
          <div key={item.relativePath} className="overflow-hidden rounded-lg border border-border/70 bg-background/70">
            {kind === "video" ? (
              <video controls preload="metadata" src={`/media/${item.relativePath}`} className="aspect-video w-full bg-black object-cover" />
            ) : (
              <img src={`/media/${item.relativePath}`} alt={item.filename} className="aspect-video w-full bg-muted object-cover" />
            )}
            <div className="truncate px-3 py-2 text-xs text-muted-foreground" title={item.filename}>
              {item.filename}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
