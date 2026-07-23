"""Escape hatch for RenderResourcePriority.

If the backend process was killed/crashed mid-batch before it could resume the
processes it suspended, this force-resumes anything currently suspended that
matches RENDER_SUSPEND_PROCESS_NAMES (or names passed as CLI args). Safe to run
anytime -- it only touches processes that are actually in a suspended state, and
never targets remoting_host.exe (Chrome Remote Desktop).

Usage:
  python tests/benchmarks/resume_all.py                # use .env config
  python tests/benchmarks/resume_all.py MktBrowser tstfox
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import psutil  # noqa: E402
from src.config import Config  # noqa: E402
from src.utils.render_priority import ALWAYS_EXCLUDED_PROCESS_NAMES, process_name_matches  # noqa: E402


def main():
    names = {a.strip().lower() for a in sys.argv[1:] if a.strip()}
    if not names:
        raw = getattr(Config, "RENDER_SUSPEND_PROCESS_NAMES", "") or ""
        names = {n.strip().lower() for n in raw.split(",") if n.strip()}
    names -= ALWAYS_EXCLUDED_PROCESS_NAMES
    if not names:
        print("No process names configured (RENDER_SUSPEND_PROCESS_NAMES) or given as args.")
        return

    print(f"Scanning for suspended processes matching: {sorted(names)}")
    resumed = 0
    checked = 0
    for p in psutil.process_iter(["name"]):
        name = p.info.get("name") or ""
        if not process_name_matches(name, names):
            continue
        checked += 1
        try:
            if p.status() == psutil.STATUS_STOPPED:
                p.resume()
                print(f"  resumed {name} (pid={p.pid})")
                resumed += 1
            else:
                print(f"  {name} (pid={p.pid}) not suspended, skipped")
        except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
            print(f"  error resuming {name} (pid={p.pid}): {exc}")
    print(f"Done. Checked {checked} matching process(es), resumed {resumed}.")


if __name__ == "__main__":
    main()
