import logging
import os
import sys
from logging.handlers import RotatingFileHandler

class SafeConsoleHandler(logging.StreamHandler):
    def emit(self, record):
        try:
            msg = self.format(record)
            stream = self.stream
            stream.write(msg + self.terminator)
            self.flush()
        except UnicodeEncodeError:
            msg = self.format(record)
            safe_msg = msg.encode(stream.encoding or "utf-8", errors="backslashreplace").decode(
                stream.encoding or "utf-8",
                errors="strict",
            )
            stream = self.stream
            stream.write(safe_msg + self.terminator)
            self.flush()
        except Exception:
            pass

def setup_logger(name="auto_video"):
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    # Console handler
    ch = SafeConsoleHandler(sys.stderr)
    ch.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    # File handler
    log_dir = "logs"
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    fh = RotatingFileHandler(
        os.path.join(log_dir, 'app.log'),
        maxBytes=10485760,
        backupCount=5,
        encoding="utf-8",
    )
    fh.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    fh.setFormatter(file_formatter)
    logger.addHandler(fh)

    return logger

logger = setup_logger()
