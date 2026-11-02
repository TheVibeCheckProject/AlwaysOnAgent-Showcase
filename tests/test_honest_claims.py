"""
Replies that claim what didn't happen get an honest note (core/memory.py unbacked_claim_note).
The lines are the real ones from the 2026-10-13 chat about applying task-189.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import memory, self_update  # noqa: E402
from core.memory import unbacked_claim_note  # noqa: E402


def test_the_go_live_card_counts_as_a_card():
    """offer_go_live shows the job's card: the reply about it isn't told "there's no card"."""
    said = "Here is the card for task-189—just tap the ⚡ Apply update chip on it when you're ready."
    shown = [{"name": "offer_go_live", "result": "The card for task-189 is shown with a ⚡ Apply update chip."}]
    assert "no card" not in unbacked_claim_note(said, shown, task_exists=lambda t: True)
    refused = [{"name": "offer_go_live", "result": "Error: can't offer it: task-189 was already applied once"}]
    assert "There's no card this time" in unbacked_claim_note(said, refused, task_exists=lambda t: True)


def test_a_made_up_task_is_called_out():
    said = ("You caught me. Let's do this right: I'm kicking off a dedicated background task (`task-190`) to "
            "execute the new automated research code block on the codebase.")
    out = unbacked_claim_note(said, [], task_exists=lambda t: t == "task-189")
    assert "There's no task-190" in out
    # A real task, mentioned plainly: no note
    fine = "Task-189 finished: 436 tests passed. Say \"apply task-189\" to put it live."
    assert unbacked_claim_note(fine, [], task_exists=lambda t: True) == fine


def test_saying_an_update_went_live_when_it_didnt(monkeypatch):
    said = "Yes, it worked! Task-189 is fully applied and merged into live, and the new research worker is ready."
    monkeypatch.setattr(self_update, "status", lambda job: None)                 # never applied
    assert memory.LIVE_NOTE in unbacked_claim_note(said, [{"name": "get_system_status"}], task_exists=lambda t: True)
    # Really applied (the go-live record says so): the claim stands
    monkeypatch.setattr(self_update, "status", lambda job: {"status": "applied"} if job == "task-189" else None)
    assert unbacked_claim_note(said, [], task_exists=lambda t: True) == said
    # Everyday words aren't go-live claims
    for text in ("I applied your new quiet hours.", "Your list changes are saved.", "The weather is live on the map."):
        monkeypatch.setattr(self_update, "status", lambda job: None)
        assert memory.LIVE_NOTE not in unbacked_claim_note(text, [], task_exists=lambda t: True), text


def test_saying_background_research_happened_when_it_didnt():
    said = "While you were away, I researched the latest developments on Python 3.14 in the background."
    assert memory.BACKGROUND_NOTE in unbacked_claim_note(said, [], has_background_record=lambda r: False)
    # Really researched: claim stands
    assert unbacked_claim_note(said, [], has_background_record=lambda r: True) == said
    # Calling background_status that returned valid findings backs it
    trace = [{"name": "background_status", "result": "Background work finished. Found: Python 3.14 developments"}]
    assert unbacked_claim_note(said, trace, has_background_record=lambda r: False) == said
    # Everyday words aren't background research claims
    assert memory.BACKGROUND_NOTE not in unbacked_claim_note("I searched for the weather in Austin.", [], has_background_record=lambda r: False)

