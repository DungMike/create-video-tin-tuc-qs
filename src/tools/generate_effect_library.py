import argparse
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.utils.effects_library import ensure_effect_library_generated
from src.utils.logger import logger


def main():
    parser = argparse.ArgumentParser(description="Generate FFmpeg effect library previews and metadata.")
    parser.add_argument("--force", action="store_true", help="Regenerate preview videos even if they already exist.")
    args = parser.parse_args()

    logger.info("Generating effects library previews...")
    ensure_effect_library_generated(force_preview_regeneration=args.force)
    logger.info("Effects library generation completed.")


if __name__ == "__main__":
    main()
