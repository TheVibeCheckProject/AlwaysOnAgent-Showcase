"""The request and response bodies of the API."""

from typing import Any, Dict, List, Literal, Optional

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
    source: Optional[str] = Field(default=None, max_length=120)


class DaemonStateRequest(BaseModel):
    status: str = Field(..., min_length=1, max_length=20)


class EngineChangeRequest(BaseModel):
    engine: str = Field(..., min_length=1, max_length=30)


class MediaTriggerRequest(BaseModel):
    quote: Optional[str] = None
    action: str = Field(default="video")
    scene: Optional[str] = None          # 'chest' / 'bottle' / 'card'; omitted: TiktokVideos takes turns


MemoryCategory = Literal["profile", "preference", "project", "plan", "person", "instruction", "fact"]


class FactCreateRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)
    category: MemoryCategory = Field(default="fact")
    importance: int = Field(default=5, ge=1, le=5)
    pinned: bool = Field(default=True)


class FactPatchRequest(BaseModel):
    content: Optional[str] = Field(default=None, min_length=1, max_length=2000)
    category: Optional[MemoryCategory] = None
    importance: Optional[int] = Field(default=None, ge=1, le=5)
    pinned: Optional[bool] = None
    status: Optional[Literal["active", "faded"]] = None


class MemoryReviewAnswer(BaseModel):
    keep: List[int] = Field(default_factory=list, max_length=20)     # the check's item numbers; [] keeps nothing


class ProcedureCreateRequest(BaseModel):
    area: str = Field(default="general", min_length=1, max_length=40)
    when: str = Field(..., min_length=3, max_length=160)
    do: str = Field(..., min_length=8, max_length=400)
    pinned: bool = False


class ProcedurePatchRequest(BaseModel):
    area: Optional[str] = Field(default=None, min_length=1, max_length=40)
    when: Optional[str] = Field(default=None, min_length=3, max_length=160)
    do: Optional[str] = Field(default=None, min_length=8, max_length=400)
    pinned: Optional[bool] = None
    status: Optional[Literal["active", "retired"]] = None


class TaskRunRequest(BaseModel):
    task_id: Optional[str] = None
    command: Optional[str] = None
    prompt: Optional[str] = None
    title: Optional[str] = None
    engine: Optional[str] = "auto"


class TaskCancelRequest(BaseModel):
    task_id: Optional[str] = None


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    model: Optional[str] = "auto"
    engine: Optional[str] = "auto"
    conversation_id: Optional[str] = None
    attachments: List[str] = Field(default_factory=list, max_length=5)     # ids from POST /api/chat/attachments
    turn_id: Optional[str] = Field(None, max_length=64)    # the app's id for this message, for POST /api/chat/cancel


class ChatCancel(BaseModel):
    turn_id: str = Field(..., min_length=1, max_length=64)


class ChatRefineRequest(BaseModel):
    text: Optional[str] = Field(default=None, max_length=10000, description="The draft text currently typed in the chat box")
    message: Optional[str] = Field(default=None, max_length=10000)
    prompt: Optional[str] = Field(default=None, max_length=10000)
    instruction: Optional[str] = Field(default=None, max_length=500, description="Optional extra direction on how to refine")

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
    conversation_id: Optional[str] = None


class ProposalOut(BaseModel):
    id: str
    instruction: str
    reason: str = ""
    project: Optional[str] = None
    kind: Literal["task", "add_project", "tiktok", "pins", "computer", "email", "calendar_event", "text", "call"] = "task"   # tiktok/pins: a public post, made on Run; computer: AgentComputerUse runs it on Run
    phone: Optional[Dict[str, str]] = None   # text/call: {"name", "number", "body"}: the phone opens Messages or the dialer


class QuestionOut(BaseModel):
    id: str
    text: str
    options: List[str]
    allow_other: bool = True


class ChatResponse(BaseModel):
    success: bool = True
    reply: str
    model: str = "auto"
    agent_status: str = "idle"
    task_id: Optional[str] = None
    proposals: List[ProposalOut] = []
    question: Optional[QuestionOut] = None
    canvas: Optional[Dict[str, Any]] = None    # {"id", "title", "kind", "version"}: the draft this reply wrote or changed


class AgentWorkJobRequest(BaseModel):
    project: str = Field(..., min_length=1, max_length=100)
    instruction: str = Field(..., max_length=4000)


class AgentWorkProjectRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=41)
    repo: str = Field(..., min_length=8, max_length=300)
    description: str = Field(..., max_length=300)


class PushRegisterRequest(BaseModel):
    token: str = Field(..., min_length=20, max_length=4096)
    device_name: Optional[str] = Field(default="", max_length=80)


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
    quote: Optional[str] = Field(default=None, max_length=300)
    text: Optional[str] = Field(default=None, max_length=300)                 # reminder
    instruction: Optional[str] = Field(default=None, max_length=2000)         # task
    project: Optional[str] = Field(default=None, max_length=100)              # task: an AgentWork project
    count: Optional[int] = Field(default=None, ge=1, le=5)                    # pins: how many per run


class SchedulePatchRequest(BaseModel):
    name: Optional[str] = Field(default=None, max_length=80)
    cron: Optional[str] = Field(default=None, min_length=5, max_length=100)
    enabled: Optional[bool] = None
    post_to_tiktok: Optional[bool] = None
    quote: Optional[str] = Field(default=None, max_length=300)
    text: Optional[str] = Field(default=None, max_length=300)
    instruction: Optional[str] = Field(default=None, max_length=2000)
    project: Optional[str] = Field(default=None, max_length=100)
    count: Optional[int] = Field(default=None, ge=1, le=5)


class InboxFeedbackRequest(BaseModel):
    reaction: str = Field(..., min_length=1, max_length=50, description="Reaction: thumbs_up, thumbs_down, less_like_this")
    reason: Optional[str] = Field(default=None, max_length=500)

