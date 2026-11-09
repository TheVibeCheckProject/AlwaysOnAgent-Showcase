"""The request and response bodies of the API."""

from typing import Any, Literal

from pydantic import BaseModel, Field

# hermes and groq are older names for cloud, still accepted
TaskEngine = Literal["auto", "antigravity", "cloud", "ollama", "media_pipeline", "hermes", "groq"]
TaskPriority = Literal["low", "medium", "high", "urgent"]


class TaskCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    prompt: str = Field(..., min_length=1)
    priority: TaskPriority = Field(default="medium")
    engine: TaskEngine = Field(default="auto")
    mode: str = Field(default="accept-edits", pattern=r"^[a-z][a-z-]{0,30}$")
    timeout_seconds: int = Field(default=300, ge=10, le=3600)
    # Who started it, shown in the task's details (e.g. "Hark"); the X-Task-Source header works too
    source: str | None = Field(default=None, max_length=120)


class DaemonStateRequest(BaseModel):
    status: str = Field(..., min_length=1, max_length=20)


class EngineChangeRequest(BaseModel):
    engine: str = Field(..., min_length=1, max_length=30)


class MediaTriggerRequest(BaseModel):
    quote: str | None = None
    action: str = Field(default="video")
    scene: str | None = None          # 'chest' / 'bottle' / 'window' / 'card'; omitted: TiktokVideos takes turns


MemoryCategory = Literal["profile", "preference", "project", "plan", "person", "instruction", "fact"]


class FactCreateRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)
    category: MemoryCategory = Field(default="fact")
    importance: int = Field(default=5, ge=1, le=5)
    pinned: bool = Field(default=True)


class FactPatchRequest(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=2000)
    category: MemoryCategory | None = None
    importance: int | None = Field(default=None, ge=1, le=5)
    pinned: bool | None = None
    status: Literal["active", "faded"] | None = None


class MemoryReviewAnswer(BaseModel):
    keep: list[int] = Field(default_factory=list, max_length=20)     # the check's item numbers; [] keeps nothing


class ProcedureCreateRequest(BaseModel):
    area: str = Field(default="general", min_length=1, max_length=40)
    when: str = Field(..., min_length=3, max_length=160)
    do: str = Field(..., min_length=8, max_length=400)
    pinned: bool = False


class ProcedurePatchRequest(BaseModel):
    area: str | None = Field(default=None, min_length=1, max_length=40)
    when: str | None = Field(default=None, min_length=3, max_length=160)
    do: str | None = Field(default=None, min_length=8, max_length=400)
    pinned: bool | None = None
    status: Literal["active", "retired"] | None = None


class TaskRunRequest(BaseModel):
    task_id: str | None = None
    command: str | None = None
    prompt: str | None = None
    title: str | None = None
    engine: str | None = "auto"


class TaskCancelRequest(BaseModel):
    task_id: str | None = None


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    model: str | None = "auto"
    engine: str | None = "auto"
    conversation_id: str | None = None
    attachments: list[str] = Field(default_factory=list, max_length=5)     # ids from POST /api/chat/attachments
    turn_id: str | None = Field(None, max_length=64)    # the app's id for this message, for POST /api/chat/cancel


class ChatCancel(BaseModel):
    turn_id: str = Field(..., min_length=1, max_length=64)


class ChatRefineRequest(BaseModel):
    text: str | None = Field(default=None, max_length=10000, description="The draft text currently typed in the chat box")
    message: str | None = Field(default=None, max_length=10000)
    prompt: str | None = Field(default=None, max_length=10000)
    instruction: str | None = Field(default=None, max_length=500, description="Optional extra direction on how to refine")

    @property
    def input_text(self) -> str:
        return (self.text or self.message or self.prompt or "").strip()


class ChatRefineResponse(BaseModel):
    success: bool = True
    refined: str
    original: str
    model: str = "auto"


class AttachmentUpload(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)                    # the file name, with its extension
    # the file, base64 (up to 15 MB): capped here too, so a huge body is refused before it's decoded (audit 1.6)
    data_b64: str = Field(..., min_length=1, max_length=21_000_000)
    conversation_id: str | None = None


class ProposalOut(BaseModel):
    id: str
    instruction: str
    reason: str = ""
    project: str | None = None
    kind: Literal["task", "add_project", "tiktok", "pins", "computer", "email", "calendar_event", "text", "call"] = "task"   # tiktok/pins: a public post, made on Run; computer: AgentComputerUse runs it on Run
    phone: dict[str, str] | None = None   # text/call: {"name", "number", "body"}: the phone opens Messages or the dialer


class QuestionOut(BaseModel):
    id: str
    text: str
    options: list[str]
    allow_other: bool = True


class ChatResponse(BaseModel):
    success: bool = True
    reply: str
    model: str = "auto"
    agent_status: str = "idle"
    task_id: str | None = None
    proposals: list[ProposalOut] = []
    question: QuestionOut | None = None
    canvas: dict[str, Any] | None = None    # {"id", "title", "kind", "version"}: the draft this reply wrote or changed


class AgentWorkJobRequest(BaseModel):
    project: str = Field(..., min_length=1, max_length=100)
    instruction: str = Field(..., max_length=4000)


class AgentWorkProjectRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=41)
    repo: str = Field(..., min_length=8, max_length=300)
    description: str = Field(..., max_length=300)


class PushRegisterRequest(BaseModel):
    token: str = Field(..., min_length=20, max_length=4096)
    device_name: str | None = Field(default="", max_length=80)


class PushTokenRequest(BaseModel):
    token: str = Field(..., min_length=1, max_length=4096)


class MediaLinkRequest(BaseModel):
    # A media file's path or full URL (/videos/x.mp4, /images/x.jpg, /api/videos/x.mp4/thumbnail,
    # /api/tasks/{id}/thumb); any ?token= on it is dropped
    url: str = Field(..., min_length=2, max_length=2048)
    ttl_seconds: int = Field(default=24 * 3600, ge=60, le=7 * 24 * 3600)


ScheduleKind = Literal["video", "pins", "report", "reminder", "task"]


class ScheduleCreateRequest(BaseModel):
    name: str = Field(default="", max_length=80)
    kind: ScheduleKind
    cron: str = Field(..., min_length=5, max_length=100)
    enabled: bool = True
    post_to_tiktok: bool = False           # video: also post it (the app's switch is the owner's OK)
    quote: str | None = Field(default=None, max_length=300)
    text: str | None = Field(default=None, max_length=300)                 # reminder
    instruction: str | None = Field(default=None, max_length=2000)         # task
    project: str | None = Field(default=None, max_length=100)              # task: an AgentWork project
    count: int | None = Field(default=None, ge=1, le=5)                    # pins: how many per run


class SchedulePatchRequest(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    cron: str | None = Field(default=None, min_length=5, max_length=100)
    enabled: bool | None = None
    post_to_tiktok: bool | None = None
    quote: str | None = Field(default=None, max_length=300)
    text: str | None = Field(default=None, max_length=300)
    instruction: str | None = Field(default=None, max_length=2000)
    project: str | None = Field(default=None, max_length=100)
    count: int | None = Field(default=None, ge=1, le=5)


class WatchCreateRequest(BaseModel):
    kind: Literal["mail", "page", "search", "calendar"] = "mail"
    target: str = Field(default="", max_length=500)       # page: the web address; search: what to search
    gone: str = Field(default="", max_length=120)         # page: tell when this disappears
    ask: str = Field(default="", max_length=120)          # page: the optional AI check, in plain words
    every_minutes: int | None = Field(default=None, ge=5, le=7 * 24 * 60)
    lead_minutes: int | None = Field(default=None, ge=1, le=24 * 60)   # calendar
    source: Literal["gmail", "zoho", "both"] = "both"
    every_email: bool = False                   # every new inbox email, bulk mail skipped (core/watches.py)
    sender: str = Field(default="", max_length=80)
    subject: str = Field(default="", max_length=80)
    words: str = Field(default="", max_length=120)
    once: bool = False                          # stop after the first match
    deadline: str = Field(default="", max_length=20)     # a follow-up: YYYY-MM-DD or YYYY-MM-DDTHH:MM
    note: str = Field(default="", max_length=200)        # a follow-up: what he's waiting on


class WatchPatchRequest(BaseModel):
    sender: str | None = Field(default=None, max_length=80)
    subject: str | None = Field(default=None, max_length=80)
    words: str | None = Field(default=None, max_length=80)
    once: bool | None = None


class InboxFeedbackRequest(BaseModel):
    reaction: str = Field(..., min_length=1, max_length=50, description="Reaction: thumbs_up, thumbs_down, less_like_this")
    reason: str | None = Field(default=None, max_length=500)

