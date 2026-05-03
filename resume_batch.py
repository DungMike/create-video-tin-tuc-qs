"""
Script to patch item 1 to completed and resume pipeline from item 2 onwards.
Run: venv\\Scripts\\python resume_batch.py
"""
import sys, os, json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.config import Config
from src.utils.batch_pipeline import BatchPipelineRunner

BATCH_ID = "batch_3a48d5eb"


def main():
    progress_path = os.path.join(Config.STORAGE_DIR, "batch", BATCH_ID, "progress.json")
    print(f"Storage dir: {Config.STORAGE_DIR}")
    print(f"Progress path: {progress_path}, exists: {os.path.exists(progress_path)}")

    with open(progress_path, "r", encoding="utf-8-sig") as f:
        progress = json.load(f)

    items = progress.get("items", [])
    print(f"\nLoaded {BATCH_ID}: status={progress.get('status')}, items={len(items)}")
    for i, item in enumerate(items):
        print(f"  [{i}] {item.get('outputName')} | {item.get('status')} | {item.get('stage')} | {item.get('percent')}%")

    out_video = "output/kenh-4-26-4.mp4"
    out_abs = os.path.join(Config.STORAGE_DIR, out_video)
    print(f"\nItem 1 output: {out_abs}, exists: {os.path.exists(out_abs)}")

    items[1]["status"] = "completed"
    items[1]["stage"] = "completed"
    items[1]["percent"] = 100
    items[1]["message"] = "Render hoan tat: kenh-4-26-4.mp4"
    items[1]["outputVideo"] = out_video
    progress["status"] = "running"
    progress["completedUrls"] = sum(1 for it in items if it.get("status") == "completed")

    with open(progress_path, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)
    print(f"\nPatched item 1 -> completed. completedUrls={progress['completedUrls']}. Saved.")

    try:
        runner = BatchPipelineRunner.from_saved_batch(BATCH_ID)
    except Exception as e:
        print(f"ERROR loading runner: {e}")
        import traceback; traceback.print_exc()
        return

    pending_indexes = [
        i for i, item in enumerate(runner.items)
        if runner.progress["items"][i].get("status") in ("pending", "running") and i >= 2
    ]
    print(f"Resuming items: {pending_indexes}")

    if not pending_indexes:
        print("No pending items!")
        return

    runner.run_batch(target_indexes=pending_indexes)
    print("All done!")


if __name__ == "__main__":
    main()
