"""
Chat §5 — Images (the /image reply itself).

An image request is answered by the ImageGen tool; the background-job plumbing that runs it (start/get,
_IMAGE_JOBS) lives in interfaces/hud/chat.py, which owns that shared state. (Split out of
interfaces/hud/chat.py; chat.py re-exports the names other code uses.)
"""


from core import imagegen, recent_images
from interfaces.hud import state
from interfaces.hud.models import ChatResponse

SCENE_WAIT = 50       # seconds a turn waits for an attached image's scene note (DeskAI waits 120 s for the reply)
IMAGE_PREFIXES = ("/image", "/imagine", "/draw", "generate an image of", "create an image of", "draw an image of")


def _image_reply(user_msg: str, on_progress=None) -> ChatResponse | None:
    """The reply to an image request (ImageGen makes the image), or None if the message isn't one.
    on_progress gets what ImageGen is doing meanwhile ("AI Horde: 3rd in line")."""
    prefix = next((p for p in IMAGE_PREFIXES if user_msg.lower().startswith(p)), None)
    if prefix is None:
        return None
    request = user_msg[len(prefix):].strip()
    if not request or request.lower() in ("help", "-h", "--help"):
        try:
            guide = imagegen.help_text()
        except imagegen.ImageGenError as e:
            guide = f"⚠️ The image guide isn't available: {e}"
        return ChatResponse(success=True, reply=guide, model="system", agent_status="idle", task_id=None)

    try:
        made = imagegen.generate(request, state.IMAGES_DIR, on_progress=on_progress)
    except imagegen.ImageGenError as e:
        state.logger.warning(f"ImageGen: {e}")
        info = e.result
        recent_images.remember(state.db, info.get("prompt") or request, style=info.get("style") or "", ok=False)
        return ChatResponse(success=False, reply=f"⚠️ Could not generate the image: {e}.", model="auto",
                            agent_status="idle", task_id=None)

    prompt, enhanced, filename = made["prompt"], made.get("enhanced") or "", made["filename"]
    state.db.append_log("INFO", f"[WebHUD] Generated image for: '{prompt[:40]}'")
    details = [f"**Engine:** `{made['model']}`"]
    if made.get("style"):
        details.append(f"**Style:** `{made['style']}`")
    if enhanced:
        details.append("✨ *Prompt enriched by Gemini*")
    expansion_note = f"\n> *\"{enhanced}\"*\n" if enhanced else ""
    reply = (
        f"🎨 **Generated Image:** \"{prompt}\"\n\n"
        f"{expansion_note}"
        f"![{prompt}](/images/{filename})\n\n"
        f"{' • '.join(details)}\n\n*(Saved to `{filename}`)*"
    )
    # Short-term only (the owner's choice): kept for follow-ups like "enhance my last prompt", never in
    # long-term memory (no archive, no facts extracted)
    recent_images.remember(state.db, prompt, enhanced=enhanced, style=made.get("style") or "",
                           model=made["model"], filename=filename)
    return ChatResponse(success=True, reply=reply, model=made["model"], agent_status="idle", task_id=None)
