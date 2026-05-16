import { useEffect, useState } from "react";
import { AppShell, HeroCard, PageSection } from "@/components/app-shell";
import { BannerTitleEditor } from "@/components/BannerTitleEditor";
import { LoadingCard } from "@/components/loading-card";
import { StatusAlert } from "@/components/status-alert";
import { TopNav } from "@/components/top-nav";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import {
  ApiError,
  createChannelApi,
  createGroupApi,
  deleteChannelApi,
  deleteGroupApi,
  getChannels,
  getDecorVideos,
  getChannelDecorImages,
  uploadChannelDecorImage,
  deleteChannelDecorImage,
  updateDecorImageConfig,
  getVoices,
  updateChannelApi,
  uploadChannelTransition,
} from "@/lib/api";
import type { Channel, ChannelGroup, DecorImage, DecorVideo, VoiceRecord } from "@/types/api";

export function ChannelManagerPage() {
  const [channels, setChannels] = useState<Channel[]>([]);
  const [groups, setGroups] = useState<ChannelGroup[]>([]);
  const [voices, setVoices] = useState<VoiceRecord[]>([]);
  const [decorVideos, setDecorVideos] = useState<DecorVideo[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  // New group form
  const [newGroupName, setNewGroupName] = useState("");
  const [newGroupLang, setNewGroupLang] = useState("");

  // New channel form
  const [newChannelName, setNewChannelName] = useState("");
  const [newChannelGroupId, setNewChannelGroupId] = useState("");
  const [newChannelVoiceId, setNewChannelVoiceId] = useState("");
  const [newChannelDecorVideoId, setNewChannelDecorVideoId] = useState("");
  const [newChannelSourceText, setNewChannelSourceText] = useState("");

  // Edit state
  const [editingChannelId, setEditingChannelId] = useState<string | null>(null);
  const [editForm, setEditForm] = useState<Partial<Channel>>({});
  const [channelDecorImages, setChannelDecorImages] = useState<DecorImage[]>([]);
  const [editingDecorImage, setEditingDecorImage] = useState<{ channelId: string; image: DecorImage } | null>(null);

  const refresh = async () => {
    try {
      const [channelsRes, voicesRes, decorRes] = await Promise.all([
        getChannels(),
        getVoices(),
        getDecorVideos(),
      ]);
      setChannels(channelsRes.channels);
      setGroups(channelsRes.groups);
      setVoices(voicesRes.voices);
      setDecorVideos(decorRes.decorVideos);
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the tai du lieu.");
    }
  };

  useEffect(() => {
    setIsLoading(true);
    refresh().finally(() => setIsLoading(false));
  }, []);

  const showSuccess = (msg: string) => {
    setSuccessMessage(msg);
    setErrorMessage(null);
    setTimeout(() => setSuccessMessage(null), 3000);
  };

  const handleCreateGroup = async () => {
    if (!newGroupName.trim()) return;
    setErrorMessage(null);
    try {
      await createGroupApi({ groupName: newGroupName, language: newGroupLang });
      setNewGroupName("");
      setNewGroupLang("");
      await refresh();
      showSuccess("Da tao group moi.");
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the tao group.");
    }
  };

  const handleDeleteGroup = async (groupId: string) => {
    if (!confirm("Xac nhan xoa group nay?")) return;
    try {
      await deleteGroupApi(groupId);
      await refresh();
      showSuccess("Da xoa group.");
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the xoa group.");
    }
  };

  const handleCreateChannel = async () => {
    if (!newChannelName.trim()) return;
    setErrorMessage(null);
    try {
      await createChannelApi({
        channelName: newChannelName,
        groupId: newChannelGroupId,
        voiceId: newChannelVoiceId,
        decorVideoId: newChannelDecorVideoId,
        sourceText: newChannelSourceText,
      });
      setNewChannelName("");
      setNewChannelGroupId("");
      setNewChannelVoiceId("");
      setNewChannelDecorVideoId("");
      setNewChannelSourceText("");
      await refresh();
      showSuccess("Da tao channel moi.");
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the tao channel.");
    }
  };

  const handleDeleteChannel = async (channelId: string) => {
    if (!confirm("Xac nhan xoa channel nay?")) return;
    try {
      await deleteChannelApi(channelId);
      await refresh();
      showSuccess("Da xoa channel.");
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the xoa channel.");
    }
  };

  const startEdit = (channel: Channel) => {
    setEditingChannelId(channel.channelId);
    setEditForm(channel);
    loadDecorImages(channel.channelId);
  };

  const loadDecorImages = async (channelId: string) => {
    try {
      const res = await getChannelDecorImages(channelId);
      setChannelDecorImages(res.decorImages);
    } catch {
      setChannelDecorImages([]);
    }
  };

  const handleUploadDecorImage = async (channelId: string, file: File) => {
    const formData = new FormData();
    formData.append("image", file);
    formData.append("name", file.name.replace(/\.png$/i, ""));
    try {
      await uploadChannelDecorImage(channelId, formData);
      await loadDecorImages(channelId);
      showSuccess("Da upload anh decor.");
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the upload anh decor.");
    }
  };

  const handleDeleteDecorImage = async (channelId: string, imageId: string) => {
    try {
      await deleteChannelDecorImage(channelId, imageId);
      setChannelDecorImages((prev) => prev.filter((d) => d.id !== imageId));
      showSuccess("Da xoa anh decor.");
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the xoa anh decor.");
    }
  };

  const handleSaveEdit = async () => {
    if (!editingChannelId) return;
    try {
      await updateChannelApi(editingChannelId, editForm);
      setEditingChannelId(null);
      setEditForm({});
      await refresh();
      showSuccess("Da cap nhat channel.");
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the cap nhat channel.");
    }
  };

  const handleTransitionUpload = async (channelId: string, file: File) => {
    const formData = new FormData();
    formData.append("video", file);
    try {
      await uploadChannelTransition(channelId, formData);
      await refresh();
      showSuccess("Da upload transition video.");
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Khong the upload transition.");
    }
  };

  const groupName = (groupId: string) => groups.find((g) => g.groupId === groupId)?.groupName ?? "";

  if (isLoading) {
    return (
      <AppShell>
        <LoadingCard message="Dang tai channel manager..." />
      </AppShell>
    );
  }

  return (
    <AppShell>
      <TopNav />
      <HeroCard
        eyebrow="Channel Manager"
        title="Quan ly cac kenh YouTube"
        description="Moi channel la 1 kenh YouTube voi voice, transition va overlay rieng."
        stats={[
          { label: "Channels", value: channels.length },
          { label: "Groups", value: groups.length },
          { label: "Voices", value: voices.length },
          { label: "Decor Videos", value: decorVideos.length },
        ]}
      />

      {errorMessage ? <StatusAlert title="Loi" message={errorMessage} variant="destructive" /> : null}
      {successMessage ? <StatusAlert title="Thanh cong" message={successMessage} /> : null}

      {/* Groups Section */}
      <PageSection>
        <h2 className="text-lg font-semibold text-foreground mb-4">Channel Groups</h2>
        <div className="grid gap-3">
          {groups.map((group) => (
            <div key={group.groupId} className="flex items-center justify-between rounded-lg border border-border/70 bg-background/70 p-3">
              <div>
                <span className="font-medium text-foreground">{group.groupName}</span>
                {group.language ? <span className="ml-2 text-xs text-muted-foreground">({group.language})</span> : null}
                <span className="ml-2 text-xs text-muted-foreground">{group.groupId}</span>
              </div>
              <Button variant="ghost" size="sm" onClick={() => handleDeleteGroup(group.groupId)} className="text-destructive hover:text-destructive">
                Xoa
              </Button>
            </div>
          ))}
        </div>

        <Separator className="my-4" />

        <div className="flex flex-wrap items-end gap-3">
          <div className="grid gap-1">
            <Label htmlFor="new-group-name">Ten group</Label>
            <Input id="new-group-name" value={newGroupName} onChange={(e) => setNewGroupName(e.target.value)} placeholder="VD: Tin quân sự tiếng Việt" />
          </div>
          <div className="grid gap-1">
            <Label htmlFor="new-group-lang">Ngon ngu</Label>
            <Input id="new-group-lang" value={newGroupLang} onChange={(e) => setNewGroupLang(e.target.value)} placeholder="vi, en, ru..." />
          </div>
          <Button onClick={handleCreateGroup} disabled={!newGroupName.trim()}>Tao group</Button>
        </div>
      </PageSection>

      {/* Channels Section */}
      <PageSection>
        <h2 className="text-lg font-semibold text-foreground mb-4">Channels ({channels.length})</h2>
        <div className="grid gap-4">
          {channels.map((channel) => (
            <div key={channel.channelId} className="rounded-xl border border-border/70 bg-background/70 p-4 shadow-sm">
              {editingChannelId === channel.channelId ? (
                <div className="grid gap-3">
                  <div className="grid gap-2 md:grid-cols-2">
                    <div className="grid gap-1">
                      <Label>Ten channel</Label>
                      <Input value={editForm.channelName ?? ""} onChange={(e) => setEditForm((f) => ({ ...f, channelName: e.target.value }))} />
                    </div>
                    <div className="grid gap-1">
                      <Label>Group</Label>
                      <select value={editForm.groupId ?? ""} onChange={(e) => setEditForm((f) => ({ ...f, groupId: e.target.value }))} className="h-10 rounded-md border border-input bg-background px-3 text-sm">
                        <option value="">-- Khong co group --</option>
                        {groups.map((g) => <option key={g.groupId} value={g.groupId}>{g.groupName}</option>)}
                      </select>
                    </div>
                    <div className="grid gap-1">
                      <Label>Voice</Label>
                      <select value={editForm.voiceId ?? ""} onChange={(e) => setEditForm((f) => ({ ...f, voiceId: e.target.value }))} className="h-10 rounded-md border border-input bg-background px-3 text-sm">
                        <option value="">-- Chon voice --</option>
                        {voices.map((v) => <option key={v.voiceId} value={v.voiceId}>{v.voiceName} - {v.voiceId}</option>)}
                      </select>
                    </div>
                    <div className="grid gap-1">
                      <Label>Decor Video (PiP)</Label>
                      <select value={editForm.decorVideoId ?? ""} onChange={(e) => setEditForm((f) => ({ ...f, decorVideoId: e.target.value }))} className="h-10 rounded-md border border-input bg-background px-3 text-sm">
                        <option value="">-- Khong co --</option>
                        {decorVideos.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
                      </select>
                    </div>
                  </div>
                  <div className="grid gap-1">
                    <Label>Source text (watermark)</Label>
                    <Input value={editForm.sourceText ?? ""} onChange={(e) => setEditForm((f) => ({ ...f, sourceText: e.target.value }))} />
                  </div>
                  <div className="grid gap-1">
                    <Label>Transition video</Label>
                    <Input type="file" accept=".mp4,.mov,.webm" onChange={(e) => {
                      const file = e.currentTarget.files?.[0];
                      if (file) handleTransitionUpload(channel.channelId, file);
                    }} />
                    {channel.transitionVideoPath ? <p className="text-xs text-muted-foreground">Hien tai: {channel.transitionVideoPath}</p> : null}
                  </div>
                  {/* Decor Images (PNG banners) */}
                  <div className="grid gap-2">
                    <Label>Ảnh Decor (banner phía dưới video)</Label>
                    <div className="flex flex-wrap gap-3">
                      {channelDecorImages.map((di) => (
                        <div key={di.id} className="relative group rounded-md border border-border overflow-hidden cursor-pointer" style={{ width: 192, height: 60 }}
                          onClick={() => setEditingDecorImage({ channelId: channel.channelId, image: di })}
                        >
                          <img src={`/media/${di.relativePath}`} alt={di.name} className="w-full h-full object-cover" />
                          <div className="absolute inset-0 bg-black/60 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity">
                            <span className="text-white text-xs mr-1 max-w-[80px] truncate">{di.name}</span>
                            <button className="text-cyan-400 text-xs hover:underline mr-1" onClick={(e) => { e.stopPropagation(); setEditingDecorImage({ channelId: channel.channelId, image: di }); }}>Sửa</button>
                            <button className="text-red-400 text-xs hover:underline" onClick={(e) => { e.stopPropagation(); handleDeleteDecorImage(channel.channelId, di.id); }}>Xoá</button>
                          </div>
                        </div>
                      ))}
                    </div>
                    <Input type="file" accept=".png" onChange={(event) => { const file = event.currentTarget.files?.[0]; if (file) handleUploadDecorImage(channel.channelId, file); event.currentTarget.value = ""; }} />
                    <p className="text-xs text-muted-foreground">Chỉ hỗ trợ PNG trong suốt. Khuyến nghị: 1920×300px. Click vào ảnh để cấu hình vị trí tiêu đề.</p>
                    {/* Banner Title Editor inline */}
                    {editingDecorImage && editingDecorImage.channelId === channel.channelId && (
                      <BannerTitleEditor
                        decorImage={editingDecorImage.image}
                        channelId={channel.channelId}
                        onSave={async (config) => {
                          await updateDecorImageConfig(channel.channelId, editingDecorImage.image.id, config);
                          await loadDecorImages(channel.channelId);
                          setEditingDecorImage(null);
                          showSuccess("Đã lưu cấu hình tiêu đề banner.");
                        }}
                        onClose={() => setEditingDecorImage(null)}
                      />
                    )}
                  </div>
                  <div className="flex gap-2">
                    <Button onClick={handleSaveEdit}>Luu</Button>
                    <Button variant="outline" onClick={() => setEditingChannelId(null)}>Huy</Button>
                  </div>
                </div>
              ) : (
                <div className="flex items-start justify-between gap-4">
                  <div className="grid gap-1">
                    <div className="flex items-center gap-2">
                      <span className={`inline-block h-2 w-2 rounded-full ${channel.isActive ? "bg-green-500" : "bg-gray-400"}`} />
                      <span className="text-base font-semibold text-foreground">{channel.channelName}</span>
                      {channel.groupId ? <span className="rounded-full bg-primary/10 px-2 py-0.5 text-xs text-primary">{groupName(channel.groupId)}</span> : null}
                    </div>
                    <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
                      <span>Voice: {channel.voiceId || "—"}</span>
                      <span>Transition: {channel.transitionVideoPath ? "✓" : "—"}</span>
                      <span>Decor: {channel.decorVideoId || "—"}</span>
                      <span>Source: {channel.sourceText || "—"}</span>
                    </div>
                    <span className="text-xs text-muted-foreground">{channel.channelId}</span>
                  </div>
                  <div className="flex gap-2">
                    <Button variant="outline" size="sm" onClick={() => startEdit(channel)}>Sua</Button>
                    <Button variant="ghost" size="sm" onClick={() => handleDeleteChannel(channel.channelId)} className="text-destructive hover:text-destructive">Xoa</Button>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>

        <Separator className="my-4" />

        <h3 className="text-sm font-semibold text-foreground mb-3">Tao channel moi</h3>
        <div className="grid gap-3 md:grid-cols-2">
          <div className="grid gap-1">
            <Label>Ten channel</Label>
            <Input value={newChannelName} onChange={(e) => setNewChannelName(e.target.value)} placeholder="VD: Tin TG Quan Su VN #1" />
          </div>
          <div className="grid gap-1">
            <Label>Group</Label>
            <select value={newChannelGroupId} onChange={(e) => setNewChannelGroupId(e.target.value)} className="h-10 rounded-md border border-input bg-background px-3 text-sm">
              <option value="">-- Chon group --</option>
              {groups.map((g) => <option key={g.groupId} value={g.groupId}>{g.groupName}</option>)}
            </select>
          </div>
          <div className="grid gap-1">
            <Label>Voice</Label>
            <select value={newChannelVoiceId} onChange={(e) => setNewChannelVoiceId(e.target.value)} className="h-10 rounded-md border border-input bg-background px-3 text-sm">
              <option value="">-- Chon voice --</option>
              {voices.map((v) => <option key={v.voiceId} value={v.voiceId}>{v.voiceName} - {v.voiceId}</option>)}
            </select>
          </div>
          <div className="grid gap-1">
            <Label>Decor Video (PiP)</Label>
            <select value={newChannelDecorVideoId} onChange={(e) => setNewChannelDecorVideoId(e.target.value)} className="h-10 rounded-md border border-input bg-background px-3 text-sm">
              <option value="">-- Khong co --</option>
              {decorVideos.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
            </select>
          </div>
          <div className="grid gap-1 md:col-span-2">
            <Label>Source text (watermark)</Label>
            <Input value={newChannelSourceText} onChange={(e) => setNewChannelSourceText(e.target.value)} placeholder="VD: Nguon: Tong hop" />
          </div>
        </div>
        <Button className="mt-3" onClick={handleCreateChannel} disabled={!newChannelName.trim()}>Tao channel</Button>
      </PageSection>
    </AppShell>
  );
}
