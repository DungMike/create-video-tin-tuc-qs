import os
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from PIL import Image
from src.utils.logger import logger
from src.config import Config

class NewsScraper:
    """Scraper to fetch images from news sources based on keywords"""
    def __init__(self, job_id: str, dirs: dict):
        self.job_id = job_id
        self.output_dir = dirs['raw_images']

    def fetch_news_images(self, keywords: list) -> list:
        downloaded_paths = []
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        for kw in keywords:
            logger.info(f"News scraping (VnExpress mock) for keyword: {kw}")
            # Example logic for vnexpress search:
            search_url = f"https://timkiem.vnexpress.net/?q={requests.utils.quote(kw)}"
            try:
                # Basic scrape
                resp = requests.get(search_url, headers=headers, timeout=10)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.content, 'html.parser')
                    # Find article image thumbs
                    img_tags = soup.find_all('img')
                    count = 0
                    for img in img_tags:
                        img_url = img.get('data-src') or img.get('src')
                        if img_url and img_url.startswith('http'):
                            # Try to download
                            path = self._download_image(img_url, f"news_{kw.replace(' ', '_')}_{count}")
                            if path:
                                downloaded_paths.append(path)
                                count += 1
                        if count >= 5: # Limit news images
                            break
            except Exception as e:
                logger.error(f"Error scraping news for '{kw}': {e}")
                
        return downloaded_paths

    def _download_image(self, url: str, filename_base: str) -> str:
        try:
            resp = requests.get(url, timeout=10)
            content_type = resp.headers.get("Content-Type", "").lower()
            if (
                resp.status_code == 200
                and len(resp.content) > 10000
                and content_type.startswith("image/")
                and "svg" not in content_type
            ):
                ext = content_type.split("/")[-1].split(";")[0]
                if ext not in {"jpg", "jpeg", "png", "webp"}:
                    ext = "jpg"
                filepath = os.path.join(self.output_dir, f"{filename_base}.{ext}")
                with open(filepath, 'wb') as f:
                    f.write(resp.content)
                if self._validate_image(filepath):
                    return filepath
                if os.path.exists(filepath):
                    os.remove(filepath)
        except:
            pass
        return ""

    def _validate_image(self, filepath: str) -> bool:
        try:
            with Image.open(filepath) as img:
                img.verify()
            return True
        except Exception:
            return False
