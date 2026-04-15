import argparse
import os
import sys
import time
import uuid

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import Config
from src.utils.logger import logger
from src.utils.file_manager import setup_directories, cleanup_job_files
from src.processors.audio_utils import validate_audio, get_audio_duration
from src.crawlers.image_crawler import ImageCrawler
from src.crawlers.news_scraper import NewsScraper
from src.crawlers.video_crawler import VideoCrawler
from src.processors.image_processor import ImageProcessor
from src.processors.video_processor import VideoProcessor
from src.composer.timeline import TimelineComposer
from src.composer.renderer import Renderer

def main():
    parser = argparse.ArgumentParser(description="Auto Video Generator")
    parser.add_argument("--audio", required=True, help="Path to input audio file")
    parser.add_argument("--topic", required=True, help="Main topic")
    parser.add_argument("--keywords", required=True, nargs="+", help="List of keywords to search")
    args = parser.parse_args()

    job_id = str(uuid.uuid4())[:8]
    logger.info(f"=== Starting new video job: {job_id} ===")
    logger.info(f"Topic: {args.topic}")
    logger.info(f"Keywords: {args.keywords}")

    if not validate_audio(args.audio):
        logger.error("Invalid audio file. Exiting.")
        return

    audio_duration = get_audio_duration(args.audio)
    logger.info(f"Audio duration: {audio_duration} seconds")
    
    dirs = setup_directories(job_id)

    logger.info(">>> Phase 2: Crawling Assets")
    img_crawler = ImageCrawler(job_id, dirs)
    vid_crawler = VideoCrawler(job_id, dirs)
    news_scraper = NewsScraper(job_id, dirs)

    raw_images = img_crawler.fetch_images(args.keywords)
    news_images = news_scraper.fetch_news_images(args.keywords)
    raw_images.extend(news_images)
    
    raw_videos = vid_crawler.fetch_videos(args.keywords)
    
    logger.info(f"Crawled {len(raw_images)} images and {len(raw_videos)} videos.")

    logger.info(">>> Phase 3: Processing Materials")
    img_processor = ImageProcessor(job_id, dirs)
    vid_processor = VideoProcessor(job_id, dirs)

    img_clips = img_processor.process_images(raw_images)
    vid_clips = vid_processor.process_videos(raw_videos)

    logger.info(f"Processed {len(img_clips)} image clips and {len(vid_clips)} video clips.")

    if not img_clips and not vid_clips:
        logger.error("No clips were successfully generated. Aborting.")
        return

    logger.info(">>> Phase 4: Timeline Assembly")
    timeline_composer = TimelineComposer(job_id, dirs)
    concat_file = timeline_composer.create_timeline(vid_clips, img_clips, audio_duration)

    logger.info(">>> Phase 5: Final Render")
    renderer = Renderer(job_id, dirs)
    output_video = renderer.render(concat_file, args.audio, audio_duration)

    if output_video:
        logger.info(f"Job completed successfully. Output: {output_video}")
    else:
        logger.error("Pipeline failed at render phase.")

if __name__ == "__main__":
    start_time = time.time()
    main()
    logger.info(f"Total execution time: {time.time() - start_time:.2f} seconds")
