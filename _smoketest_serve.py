import os, sys, traceback
os.chdir(r"F:\CRAWL VIDEO - AUDIO - QS")
try:
    from waitress import serve
    from src.web_app import app
    print("SERVE_START", flush=True)
    serve(app, host="127.0.0.1", port=5099)
except Exception:
    traceback.print_exc()
    sys.exit(1)
