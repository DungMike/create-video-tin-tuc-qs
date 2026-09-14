"""Flask app cho Story Video.

He thong chi con hai trang: /story-video va /story-video/settings. Toan bo API
cua chung nam trong blueprint story_video_bp; file nay chi giu phan bootstrap,
endpoint /api/voices (trang chinh dung de nap danh sach giong doc), route phuc
vu file media va catch-all tra ve SPA.
"""

import os

from flask import Flask, abort, jsonify, send_from_directory

from src.config import Config
from src.utils.tts_audio import load_voices

app = Flask(__name__, static_folder=None)
app.secret_key = Config.WEB_SECRET_KEY

# Register modular blueprints
from src.routes.story_video_routes import story_video_bp  # noqa: E402
app.register_blueprint(story_video_bp)
app.config["MAX_CONTENT_LENGTH"] = Config.MAX_UPLOAD_SIZE_MB * 1024 * 1024


def _frontend_dist_dir() -> str:
    return os.path.abspath(Config.FRONTEND_DIST_DIR)


@app.route("/api/voices", methods=["GET"])
def voices():
    return jsonify({"voices": load_voices(), "defaultVoiceId": Config.TTS_DEFAULT_VOICE_ID})


@app.route("/media/<path:relative_path>", methods=["GET"])
def media(relative_path: str):
    normalized = os.path.normpath(relative_path).replace("\\", "/")
    if normalized.startswith(".."):
        abort(404)
    # "output/..." paths live under OUTPUT_DIR, which may be on a different drive
    # than STORAGE_DIR. Serve them from OUTPUT_DIR; everything else from STORAGE_DIR.
    if normalized == "output" or normalized.startswith("output/"):
        base_dir = os.path.abspath(Config.OUTPUT_DIR)
        sub_path = normalized[len("output"):].lstrip("/")
    # Same story for "story_raw/...": STORY_RAW_DIR is overridable on its own (the
    # deployment keeps it on a big slow disk, off STORAGE_DIR), and the prefetch
    # review step previews those staged source videos straight from the browser.
    elif normalized == "story_raw" or normalized.startswith("story_raw/"):
        base_dir = os.path.abspath(Config.STORY_RAW_DIR)
        sub_path = normalized[len("story_raw"):].lstrip("/")
    else:
        base_dir = os.path.abspath(Config.STORAGE_DIR)
        sub_path = normalized
    if not sub_path:
        abort(404)
    return send_from_directory(base_dir, sub_path, as_attachment=False)


@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def spa(path: str):
    if path.startswith("api/") or path.startswith("media/"):
        abort(404)

    dist_dir = _frontend_dist_dir()
    if not os.path.isdir(dist_dir):
        return (
            "Frontend build chưa tồn tại. Chạy `npm run dev` để dùng UI dev server hoặc `npm run build` để tạo dist.",
            503,
        )

    candidate_path = os.path.join(dist_dir, path)
    if path and os.path.isfile(candidate_path):
        return send_from_directory(dist_dir, path)

    index_path = os.path.join(dist_dir, "index.html")
    if not os.path.isfile(index_path):
        return (
            "Frontend build chưa tồn tại. Chạy `npm run dev` để dùng UI dev server hoặc `npm run build` để tạo dist.",
            503,
        )

    return send_from_directory(dist_dir, "index.html")


if __name__ == "__main__":
    app.run(host=Config.WEB_HOST, port=Config.WEB_PORT, debug=False)
