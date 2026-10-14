import { Check, ExternalLink, Loader2, RotateCcw } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { approvePipelineItem, getPipelineReview, rejectPipelineItem } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { PipelineItem, PipelineRejectAction, PipelineReview, PipelineRun } from "@/types/api";

import { gateOf, selectClassName } from "./pipelineLabels";

const BATCH_SIZE = 35;

export function PipelineReviewDialog({
  run,
  item,
  onClose,
  onDone,
}: {
  run: PipelineRun;
  item: PipelineItem;
  onClose: () => void;
  onDone: (run: PipelineRun) => void;
}) {
  const gate = gateOf(item) ?? "prompts";
  const [review, setReview] = useState<PipelineReview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [batchText, setBatchText] = useState("");
  const [regenIds, setRegenIds] = useState<string[]>([]);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    getPipelineReview(run.runId, item.itemId, gate)
      .then(setReview)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "Không tải được dữ liệu duyệt."));
  }, [run.runId, item.itemId, gate]);

  const scenes = useMemo(
    () => (review?.scenes ?? []).filter((scene) => !filter || scene.characters.includes(filter)),
    [review, filter],
  );
  const totalImages = review ? (gate === "prompts" ? review.counts.characters + review.counts.scenes : review.counts.scenes) : 0;

  const act = async (fn: () => Promise<PipelineRun>) => {
    setBusy(true);
    setError(null);
    try {
      onDone(await fn());
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Thao tác lỗi.");
    } finally {
      setBusy(false);
    }
  };

  const reject = (action: PipelineRejectAction) => {
    const batchNos = batchText
      .split(/[,\s]+/)
      .map((value) => Number(value))
      .filter((value) => Number.isInteger(value) && value > 0);
    void act(() =>
      rejectPipelineItem(run.runId, item.itemId, {
        gate,
        action,
        note: note.trim() || undefined,
        batchNos: action === "redo_batches" ? batchNos : undefined,
        characterIds: action === "regen_characters" ? regenIds : undefined,
      }),
    );
  };

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="flex max-h-[92vh] max-w-6xl flex-col overflow-hidden">
        <DialogHeader>
          <DialogTitle>
            {gate === "prompts" ? "Duyệt prompt" : "Duyệt ảnh nhân vật"} · {item.title}
          </DialogTitle>
          <DialogDescription>
            {review
              ? gate === "prompts"
                ? `AI đã viết ${review.characters.length} nhân vật và ${review.counts.scenes} prompt cảnh. Duyệt thì app TST sẽ tạo ${totalImages} ảnh (tốn lượt license).`
                : `Kiểm tra ảnh nhân vật. Duyệt thì tạo tiếp ${review.counts.scenes} ảnh cảnh.`
              : "Đang tải…"}
          </DialogDescription>
        </DialogHeader>

        {error ? <p className="text-sm text-destructive">{error}</p> : null}
        {!review && !error ? <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" /> : null}

        {review ? (
          <Tabs defaultValue={gate === "characters" ? "characters" : "scenes"} className="flex min-h-0 flex-1 flex-col">
            <TabsList className="self-start">
              <TabsTrigger value="characters">Nhân vật ({review.characters.length})</TabsTrigger>
              <TabsTrigger value="scenes">Prompt cảnh ({review.scenes.length})</TabsTrigger>
            </TabsList>

            <TabsContent value="characters" className="min-h-0 flex-1 overflow-y-auto">
              {review.setting ? <p className="mb-3 text-sm text-muted-foreground">Bối cảnh: {review.setting}</p> : null}
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {review.characters.map((character) => (
                  <div key={character.id} className="flex gap-3 rounded-xl border border-border/70 p-3">
                    {character.image ? (
                      <img src={character.image} alt={character.name} className="h-32 w-24 shrink-0 rounded-md object-cover" />
                    ) : null}
                    <div className="min-w-0 space-y-1 text-sm">
                      <div className="flex items-center gap-2 font-medium">
                        {gate === "characters" ? (
                          <Checkbox
                            checked={regenIds.includes(character.id)}
                            onCheckedChange={(value) =>
                              setRegenIds((current) =>
                                value === true ? [...current, character.id] : current.filter((id) => id !== character.id),
                              )
                            }
                            title="Chọn để tạo lại ảnh"
                          />
                        ) : null}
                        {character.name}
                        <span className="font-mono text-xs text-muted-foreground">{character.id}</span>
                      </div>
                      {character.role ? <div className="text-xs text-muted-foreground">{character.role}</div> : null}
                      <p className="text-xs leading-5">{character.description}</p>
                    </div>
                  </div>
                ))}
              </div>
            </TabsContent>

            <TabsContent value="scenes" className="min-h-0 flex-1 overflow-hidden">
              <div className="mb-2 flex items-center gap-2 text-sm">
                <span className="text-muted-foreground">Lọc theo nhân vật:</span>
                <select className={cn(selectClassName, "w-56")} value={filter} onChange={(event) => setFilter(event.target.value)}>
                  <option value="">Tất cả</option>
                  {review.characters.map((character) => (
                    <option key={character.id} value={character.id}>
                      {character.name}
                    </option>
                  ))}
                </select>
                <span className="text-xs text-muted-foreground">{scenes.length} cảnh</span>
              </div>
              <div className="h-full max-h-[52vh] overflow-y-auto rounded-xl border border-border/70">
                <table className="w-full text-xs">
                  <thead className="sticky top-0 bg-muted text-left uppercase text-muted-foreground">
                    <tr>
                      <th className="px-2 py-1.5">#</th>
                      <th className="px-2 py-1.5">Thời gian</th>
                      <th className="px-2 py-1.5">Lời kể</th>
                      <th className="px-2 py-1.5">Prompt</th>
                      <th className="px-2 py-1.5">Nhân vật</th>
                    </tr>
                  </thead>
                  <tbody>
                    {scenes.map((scene) => (
                      <tr key={scene.id} className="border-t border-border/60 align-top">
                        <td className="px-2 py-1.5 text-muted-foreground">
                          {scene.id}
                          <div className="text-[10px]">lô {Math.ceil(scene.id / BATCH_SIZE)}</div>
                        </td>
                        <td className="whitespace-nowrap px-2 py-1.5 font-mono">{scene.start.slice(0, 8)}</td>
                        <td className="max-w-[18rem] px-2 py-1.5">{scene.text}</td>
                        <td className="max-w-[28rem] px-2 py-1.5">{scene.prompt ?? <span className="text-destructive">(chưa có)</span>}</td>
                        <td className="px-2 py-1.5">{scene.characters.join(", ")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </TabsContent>
          </Tabs>
        ) : null}

        {review ? (
          <div className="space-y-3 border-t border-border/70 pt-3">
            <Textarea
              placeholder="Ghi chú cho AI khi chạy lại (vd: nhân vật chính là nữ, bối cảnh thập niên 80…)"
              value={note}
              onChange={(event) => setNote(event.target.value)}
              rows={2}
            />
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex flex-wrap items-center gap-2">
                <Button variant="outline" size="sm" disabled={busy} onClick={() => reject("rerun_ai")}>
                  <RotateCcw className="h-4 w-4" />
                  Chạy lại AI (viết lại toàn bộ)
                </Button>
                {gate === "prompts" ? (
                  <>
                    <Input
                      className="h-8 w-40"
                      placeholder="lô, vd 2, 5"
                      value={batchText}
                      onChange={(event) => setBatchText(event.target.value)}
                    />
                    <Button variant="outline" size="sm" disabled={busy || !batchText.trim()} onClick={() => reject("redo_batches")}>
                      Viết lại các lô này
                    </Button>
                  </>
                ) : (
                  <Button variant="outline" size="sm" disabled={busy || !regenIds.length} onClick={() => reject("regen_characters")}>
                    Tạo lại ảnh {regenIds.length || ""} nhân vật đã chọn
                  </Button>
                )}
                {review.tstUrl ? (
                  <Button asChild variant="ghost" size="sm">
                    <a href={review.tstUrl} target="_blank" rel="noreferrer">
                      Sửa tay trong TST Studio <ExternalLink className="h-3.5 w-3.5" />
                    </a>
                  </Button>
                ) : null}
              </div>
              <Button disabled={busy} onClick={() => void act(() => approvePipelineItem(run.runId, item.itemId, gate))}>
                {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
                {gate === "prompts" ? `Duyệt — tạo ${totalImages} ảnh` : "Duyệt — tạo ảnh cảnh"}
              </Button>
            </div>
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
