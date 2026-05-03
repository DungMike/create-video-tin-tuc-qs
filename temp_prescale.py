import os
import sys

BASE = r"e:\CRAWL VIDEO - AUDIO - QS"
sys.path.insert(0, BASE)

from src.utils.decor_videos import _prescale_decor_video, _load_index, _save_index, _decor_dir

items = _load_index()
changed = False
decor_dir = _decor_dir()

for item in items:
    original = os.path.join(decor_dir, item["filename"])
    if not os.path.isfile(original):
        continue
    
    # Check if prescaled file exists
    if item.get("prescaledFilename"):
        prescaled_path = os.path.join(decor_dir, item["prescaledFilename"])
        if os.path.isfile(prescaled_path):
            print(f"Skipping {item['filename']} (already prescaled)")
            continue
    
    print(f"\nPrescaling {item['filename']}...")
    res = _prescale_decor_video(original)
    if res:
        item["prescaledFilename"] = os.path.basename(res)
        changed = True
        print(f" -> Success: {item['prescaledFilename']}")
    else:
        print(f" -> Failed")

if changed:
    _save_index(items)
    print("\nIndex updated.")
