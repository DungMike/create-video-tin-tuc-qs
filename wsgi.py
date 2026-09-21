from src.utils.story_video_batch import resume_batch_queue
from src.web_app import app

# Entry point cua server that (WSGI server import module nay), nen khoi phuc hang doi
# batch o day chu khong phai trong src.web_app — test import app o do.
resume_batch_queue()


if __name__ == "__main__":
    app.run()
