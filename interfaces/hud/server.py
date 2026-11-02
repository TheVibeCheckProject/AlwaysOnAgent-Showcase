"""
The API server DeskAI talks to (FastAPI; supervisor.py runs it with uvicorn on HUD_HOST:HUD_PORT, default
0.0.0.0:8080). Each area's endpoints are in their own module; docs/API.md lists them all.
"""

import logging
import os
from typing import Optional

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from interfaces.hud import (canvas_api, chat, companion, computer_api, events_api, files_api, github_api, media,
                            memory_api, schedules_api, state, tasks, today_api)
from interfaces.hud.auth import HUDAuthMiddleware, install_log_redaction

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s")
# uvicorn's access log prints each request's URL with its query string: never let ?token= reach a log (2026-10-07)
install_log_redaction()

app = FastAPI(title="AlwaysOnAgent", version=state.API_VERSION, description="The API the DeskAI app uses")

# Finished videos (TiktokVideos) and generated images (ImageGen, in AgentGallery), with Range streaming.
# No CORS middleware: a wildcard policy would let any website the owner visits queue tasks on this machine;
# the few companion endpoints that need it set the headers themselves.
app.mount("/videos", StaticFiles(directory=str(state.VIDEOS_DIR)), name="videos")
app.mount("/images", StaticFiles(directory=str(state.IMAGES_DIR)), name="images")
app.add_middleware(HUDAuthMiddleware)

for module in (companion, tasks, events_api, media, memory_api, schedules_api, canvas_api, chat, computer_api,
               github_api, files_api, today_api):
    app.include_router(module.router)


def run_hud(host: Optional[str] = None, port: Optional[int] = None):
    """Runs the server on its own (supervisor.py normally runs it alongside the daemon)."""
    import uvicorn
    h = host or os.environ.get("HUD_HOST", "0.0.0.0")
    p = port or int(os.environ.get("HUD_PORT", 8080))
    state.logger.info(f"Starting the AlwaysOnAgent API on http://{h}:{p}")
    uvicorn.run(app, host=h, port=p, log_level="warning")


if __name__ == "__main__":
    run_hud()
