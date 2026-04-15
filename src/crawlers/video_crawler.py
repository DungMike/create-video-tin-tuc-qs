import os
import yt_dlp
from src.utils.logger import logger
from src.config import Config

class VideoCrawler:
    def __init__(self, job_id: str, dirs: dict):
        self.job_id = job_id
        self.output_dir = dirs['raw_videos']

    def fetch_videos(self, keywords: list) -> list:
        downloaded_paths = []
        for kw in keywords:
            logger.info(f"Crawling YouTube videos for: {kw}")
            out_tmpl = os.path.join(self.output_dir, f"{kw.replace(' ', '_')}_%(id)s.%(ext)s")
            ydl_opts = {
                'format': 'bestvideo[ext=mp4][height>=720]/best[ext=mp4]/best',
                'outtmpl': out_tmpl,
                'noplaylist': True,
                'quiet': True,
                'no_warnings': True,
                'max_downloads': Config.YOUTUBE_DOWNLOAD_LIMIT_PER_KEYWORD,
                'match_filter': yt_dlp.utils.match_filter_func("duration < " + str(Config.MAX_SOURCE_VIDEO_DURATION)),
                'extract_flat': False
            }
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    search_query = f"ytsearch{Config.YOUTUBE_DOWNLOAD_LIMIT_PER_KEYWORD}:{kw} news"
                    ydl.download([search_query])
            except Exception as e:
                logger.error(f"Error downloading YouTube videos for '{kw}': {e}")
                
        # Collect paths
        if os.path.exists(self.output_dir):
            for file in os.listdir(self.output_dir):
                if file.endswith('.mp4'):
                    full_path = os.path.join(self.output_dir, file)
                    if full_path not in downloaded_paths:
                        downloaded_paths.append(full_path)
                    
        return downloaded_paths
