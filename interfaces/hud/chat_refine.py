"""
Chat §4 — Refine.

The chat box's ✨ button: improve a draft before sending. (Split out of interfaces/hud/chat.py; chat.py
re-exports the names other code uses and includes this module's router.)
"""


from fastapi import APIRouter, HTTPException, Response

from interfaces.hud import state
from interfaces.hud.chat_secrets import _holds_signin
from interfaces.hud.models import ChatRefineRequest, ChatRefineResponse

router = APIRouter()

REFINE_SYSTEM_PROMPT = (
    "You are an expert prompt engineer and assistant optimizer.\n"
    "Your job is to enhance and refine the draft prompt or message typed by the user in their chat box before they send it to AlwaysOnAgent.\n\n"
    "Rules:\n"
    "1. Preserve the user's intent, core meaning, tone, and any specific constraints, names, code, or values.\n"
    "2. Make the prompt clearer, more specific, and effective for the assistant to understand and act on.\n"
    "3. If the user input starts with a command prefix (e.g. /task, /image, /render, etc.), keep the command intact and refine the instruction part.\n"
    "4. Return ONLY the refined prompt text. Do NOT add conversational filler, intros (like 'Here is the refined prompt:'), explanations, or surrounding quotation marks."
)


def refine_text(raw_text: str, instruction: str | None = None) -> tuple[str, str]:
    """Refines or enhances a user's prompt text using the available LLM routes.
    Returns (refined_text, model_name). If models fail, falls back to original text."""
    text = (raw_text or "").strip()
    if not text:
        return text, "none"
    # 2026-10-14: the chat box is where sign-in links and Zoho codes get pasted; refining one would send it to a
    # model, which the chat itself never does. Hand it back untouched; sending it goes through the chat's intercept.
    if _holds_signin(text) or _holds_signin(instruction or ""):
        return text, "none"

    user_prompt = f"Prompt to enhance:\n{text}"
    if instruction and instruction.strip():
        user_prompt += f"\n\nSpecial refinement instruction: {instruction.strip()}"

    messages = [
        {"role": "system", "content": REFINE_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    from core.llm import CloudLLM
    mem = state.get_memory()
    llm = getattr(mem, "llm", None)
    if llm is None:
        try:
            cloud = CloudLLM()          # its first argument is a Groq key, not the database
            if cloud.is_available():
                llm = cloud
        except (AttributeError, TypeError):
            llm = None

    if llm is not None and getattr(llm, "is_available", lambda: True)():
        try:
            from core.llm_usage import usage_purpose
            # Background routes: Flash-Lite, Gemma, Groq... never Flash, whose ~20 a day are for chat replies
            with usage_purpose("refine"):
                content, route = llm.complete(messages, max_tokens=600, temperature=0.4, timeout=20,
                                              models=CloudLLM.BACKGROUND_MODELS)
                if content and content.strip():
                    cleaned = content.strip()
                    quoted = ((cleaned.startswith('"') and cleaned.endswith('"'))
                              or (cleaned.startswith("“") and cleaned.endswith("”")))
                    if quoted and len(cleaned) >= 2:
                        cleaned = cleaned[1:-1].strip()
                    return cleaned, route
        except Exception as e:
            state.logger.warning(f"LLM prompt refinement failed: {e}")

    return text, "raw"


@router.post("/api/chat/refine", response_model=ChatRefineResponse)
@router.post("/api/chat/enhance", response_model=ChatRefineResponse)
def chat_refine_endpoint(payload: ChatRefineRequest, response: Response):
    """
    Enhance or refine draft text typed into the DeskAI chat input box.
    Returns the refined text ready to replace or populate the input field.
    """

    raw_text = payload.input_text
    if not raw_text:
        raise HTTPException(status_code=400, detail="Missing or empty text field")

    refined, model_used = refine_text(raw_text, instruction=payload.instruction)
    return ChatRefineResponse(
        success=True,
        refined=refined,
        original=raw_text,
        model=model_used,
    )
