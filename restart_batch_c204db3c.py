import sys
import os
import json
from datetime import datetime

# Add src to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.config import Config
from src.utils.batch_pipeline import BatchPipelineRunner

BATCH_ID = "batch_c204db3c"

def _utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"

def main():
    progress_path = os.path.join(Config.STORAGE_DIR, "batch", BATCH_ID, "progress.json")
    
    if not os.path.exists(progress_path):
        print(f"Error: Progress file not found at {progress_path}")
        return

    print(f"Loading progress from {progress_path}")
    with open(progress_path, "r", encoding="utf-8") as f:
        progress = json.load(f)

    # Reset batch level status
    progress["status"] = "pending"
    progress["completedUrls"] = 0
    progress["failedUrls"] = 0
    progress["updatedAt"] = _utc_now()

    # Reset items
    for item in progress.get("items", []):
        item["status"] = "pending"
        item["stage"] = "pending"
        item["percent"] = 0
        item["message"] = "Cho xu ly (restarted)..."
        item["outputVideo"] = None
        item["jobId"] = None
        item["error"] = None
        # Keep audioRelativePath if it exists and is ready
        if item.get("audioStatus") != "ready":
            item["audioStatus"] = "none"
            item["audioRelativePath"] = None

    print(f"Saving reset progress for {BATCH_ID}")
    with open(progress_path, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)

    print(f"Starting BatchPipelineRunner for {BATCH_ID}")
    try:
        runner = BatchPipelineRunner.from_saved_batch(BATCH_ID)
        print("Runner initialized. Starting execution...")
        runner.run_batch()
        print("Batch execution finished.")
    except Exception as e:
        print(f"Error running batch: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
