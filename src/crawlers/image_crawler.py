import os
import requests
from duckduckgo_search import DDGS
from src.utils.logger import logger
from src.config import Config
from PIL import Image

class ImageCrawler:
    def __init__(self, job_id: str, dirs: dict):
        self.job_id = job_id
        self.output_dir = dirs['raw_images']

    def fetch_images(self, keywords: list) -> list:
        downloaded_paths = []
        with DDGS() as ddgs:
            for kw in keywords:
                logger.info(f"Searching DDG images for keyword: {kw}")
                try:
                    results = list(ddgs.images(
                        kw,
                        region="vn-vi",
                        safesearch="off",
                        size="Large",
                        max_results=Config.IMAGES_TO_DOWNLOAD_PER_KEYWORD
                    ))
                    for idx, res in enumerate(results):
                        url = res.get("image")
                        if url:
                            path = self._download_image(url, f"{kw.replace(' ', '_')}_{idx}")
                            if path and self._validate_image(path):
                                downloaded_paths.append(path)
                except Exception as e:
                    logger.error(f"Error crawling DDG images for '{kw}': {e}")
        return downloaded_paths

    def _download_image(self, url: str, filename_base: str) -> str:
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                ext = url.split('.')[-1].split('?')[0]
                if ext.lower() not in ['jpg', 'jpeg', 'png', 'webp']:
                    ext = 'jpg'
                filepath = os.path.join(self.output_dir, f"{filename_base}.{ext}")
                with open(filepath, 'wb') as f:
                    f.write(resp.content)
                return filepath
        except Exception as e:
            logger.debug(f"Failed to download image {url}: {e}")
        return ""

    def _validate_image(self, filepath: str) -> bool:
        try:
            with Image.open(filepath) as img:
                img.verify()
            return True
        except Exception:
            try:
                os.remove(filepath)
            except:
                pass
            return False
