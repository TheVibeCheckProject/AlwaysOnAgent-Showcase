"""
Shared constants and word patterns for the assistant tools.

Moved into core/assistant_tools/ on 2026-11-04 (was core/tool_constants.py).

Split out of core/assistant_tools.py §1 on 2026-11-03. The size limits, the
file rules for the project tools, and the word patterns that check the user's
own message before risky tools act. Imported by core/assistant_tools.py and
by the tool mixins; assistant_tools.py re-exports them all.
"""

import re

MAX_RESULT_CHARS = 5500   # tool output is resent with the prompt; semi-generous budget (<1,400 tokens)
TEXT_EXTENSIONS = {".py", ".md", ".txt", ".html", ".js", ".css", ".json", ".jsonl", ".bat", ".cmd", ".ps1",
                   ".yml", ".yaml", ".toml", ".cfg", ".ini", ".log", ".sh", ".csv"}
BLOCKED_NAMES = re.compile(r"(^\.env)|(\.db(-wal|-shm)?$)|(\.(pem|key|pfx|p12|crt)$)|(secret|credential|token)", re.IGNORECASE)
BLOCKED_DIRS = {".git", "__pycache__", "node_modules", "models", "output_videos", "temp_recordings", "temp_frames",
                ".venv", "venv", "hdri", "textures"}
WEB_TOOLS = {"web_search", "web_research", "get_news", "fetch_webpage"}
MAIL_TOOLS = {"gmail_important", "gmail_search", "gmail_read", "gmail_unread"}
# Words that ask to see a file; without one, showing a file the chat just showed is the model replaying an old request
ASKS_FOR_A_FILE = re.compile(r"\b(show|send|attach|see|watch|play|open|listen|view|find|again|video|clip|tiktok|"
                             r"mp4|wav|mp3|audio|song|pdf|picture|pic|photo|image|screenshot|file)s?\b", re.IGNORECASE)
# The user's own words that allow a tool to delete, forget or stop something (AssistantTools._asked_to, audit 2.3)
ASKS_TO_FORGET = re.compile(r"\b(forget|forgot|erase|wipe|delete|remove|scrub|stop remembering|don'?t (remember|keep|"
                            r"store|save))\b", re.IGNORECASE)
ASKS_TO_REMOVE = re.compile(r"\b(delete|remove|get rid of|drop|cancel|scrap|kill|clear)\b", re.IGNORECASE)
ASKS_TO_STOP = re.compile(r"\b(cancel|stop|abort|kill|halt|end|quit|turn off|no longer|don'?t need)\b", re.IGNORECASE)
# The user wants the assistant's own pick, or has no answer to give: ask_user hands the choice back (2026-11-02)
WANTS_ADVICE = re.compile(
    r"\b(what|which|how) would you\b|\bwhat do you (think|recommend|suggest)\b|\byou would (choose|pick|do|use|go)\b|"
    r"\byour (pick|choice|call|recommendation|opinion|take)\b|\b(what|which)( one)? should i\b|\bi was asking you\b|"
    r"\bi (really |honestly )?(don'?t|do not|dont) know\b|\bnot sure\b|\bno idea\b|"
    r"\bany (insight|ideas|advice|suggestions?|tips)\b|\bwhat'?s the best (way|approach|option)\b", re.IGNORECASE)
# The owner asking about the agent's own numbers (the self-check): always loads the stats group's my_numbers
SELF_NUMBERS = re.compile(r"\b(you|your|yourself)\b[^.?!\n]{0,30}\b(numbers|stats|scores?|self-?check)\b|"
                          r"\b(numbers|stats|scores?)\b[^.?!\n]{0,20}\byourself\b|\bhow did you do\b|\bself-?check\b",
                          re.IGNORECASE)
ASK_USER_STATE = "ask_user_after_message"   # the last message id when a card was asked (no two cards in a row)
_URL =re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)


def _urls_in(text: str) -> list[str]:
    return [u.rstrip(".,;:!?") for u in _URL.findall(text or "")]


def _url_key(url: str) -> str:
    """One form for an address: no scheme, no www, no trailing slash (the model rewrites links a little)."""
    return re.sub(r"^https?://(www\.)?", "", (url or "").strip().lower()).rstrip("/")

# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════

__all__ = ['ASKS_FOR_A_FILE', 'ASKS_TO_FORGET', 'ASKS_TO_REMOVE', 'ASKS_TO_STOP', 'ASK_USER_STATE', 'BLOCKED_DIRS', 'BLOCKED_NAMES', 'MAIL_TOOLS', 'MAX_RESULT_CHARS', 'SELF_NUMBERS', 'TEXT_EXTENSIONS', 'WANTS_ADVICE', 'WEB_TOOLS', '_URL', '_url_key', '_urls_in']
