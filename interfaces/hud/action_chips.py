"""
Action Chips Generator for DeskAI Live Task Cards
==================================================
Builds contextual 1-tap interactive buttons for task objects.
DeskAI renders these as chips beneath live task cards:
  - 'stream': opens client media player at URL
  - 'post'  : triggers HTTP POST endpoint (e.g. retry, cancel, publish)
  - 'chat'  : sends pre-formatted message or command into chat
  - 'none'  : a status label, nothing to tap (e.g. "✅ Live")
"""

from typing import Any, Dict, List, Optional

GO_LIVE_LABEL = {"alwaysonagent": "⚡ Apply update", "website": "🚀 Push live"}


def _go_live_chips(task_id: str, project: str) -> List[Dict[str, Any]]:
    """Preview, then the go-live chip as the job's state allows: ready, going live, live, or try again.
    A tap on the go-live chip calls POST /api/tasks/{id}/apply directly (no chat turn)."""
    from core import self_update
    state = (self_update.status(task_id) or {}).get("status")
    if state in ("applied",):
        return [{"label": "✅ Live", "action": "none", "variant": "secondary"}]
    if state in ("rolled_back", "rollback_failed"):
        return [{"label": "↩️ Rolled back", "action": "chat", "command": f"Why was {task_id} rolled back?",
                 "variant": "secondary"}]
    chips = [{"label": "🔍 Preview diff", "action": "chat", "command": f"Show diff for {task_id}",
              "variant": "secondary"}]
    if state and state != "failed":
        chips.append({"label": "⏳ Going live…", "action": "none", "variant": "secondary"})
    else:
        chips.append({"label": ("🔁 Try again" if state == "failed" else GO_LIVE_LABEL[project]), "action": "post",
                      "url": f"/api/tasks/{task_id}/apply", "variant": "primary"})
    return chips


def build_task_action_chips(task: Dict[str, Any], result: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """
    Evaluates task phase, metadata, and results to generate contextual action chips.
    Returns: [{"label": str, "action": str, "url"|"command": str, "variant": "primary"|"secondary"|"danger"}]
    """
    chips: List[Dict[str, Any]] = []
    task_id = task.get("id")
    if not task_id:
        return chips

    phase = task.get("phase")
    meta = task.get("metadata") or {}
    project = meta.get("project")
    action = meta.get("action")
    needs_input = bool(task.get("needs_input"))

    # 1. In-Progress Tasks
    if phase == "in_progress":
        chips.append({
            "label": "⏹ Cancel",
            "action": "post",
            "url": f"/api/cancel/{task_id}",
            "variant": "danger"
        })
        return chips

    # 2. Completed Tasks
    if phase == "completed":
        # Video pipeline renders
        if result and result.get("type") == "video":
            chips.append({
                "label": "▶ Play Preview",
                "action": "stream",
                "url": result.get("url"),
                "variant": "primary"
            })
            if action != "tiktok":
                filename = result.get("filename")
                chips.append({
                    "label": "🚀 Post to TikTok",
                    "action": "post",
                    "url": f"/api/videos/{filename}/publish",
                    "variant": "secondary"
                })

        # An AgentWork change that can go live (the agent itself, or the website): the tap is the go-ahead
        if project in ("alwaysonagent", "website") and not needs_input:
            chips.extend(_go_live_chips(task_id, project))
        # Any other AgentWork project (e.g. one set up from DeskAI's Repos screen): its change can be looked at,
        # on its branch; there's no go-live step for it (2026-10-06: the sandbox demo's card had no way to see it)
        elif project and task.get("engine") == "agentwork" and not needs_input:
            chips.append({"label": "🔍 Preview diff", "action": "chat", "command": f"Show diff for {task_id}",
                          "variant": "secondary"})

        # AgentWork task requiring human input
        if needs_input:
            chips.append({
                "label": "💬 Discuss Blockers",
                "action": "chat",
                "command": f"Explain why {task_id} needs my input",
                "variant": "secondary"
            })

    # 3. Failed or Cancelled Tasks
    elif phase in ("failed", "cancelled"):
        chips.append({
            "label": "🔄 Retry Task",
            "action": "post",
            "url": f"/api/retry/{task_id}",
            "variant": "primary"
        })

    return chips
