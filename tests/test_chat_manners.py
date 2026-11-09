"""How the chat talks (2026-11-03 review of a week of real chats): it handed choices back when Alex asked for its
pick, asked the same card twice, and ended nearly every reply with a question."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.assistant_tools import ASK_USER_STATE, AssistantTools
from core.database import DatabaseManager, get_iso_now
from core.memory import ASSISTANT_PERSONA, trim_tail_question

CARD = {"question": "Which rendering architecture fits best?", "options": ["Monolith", "Microservices"]}


@pytest.fixture
def db(tmp_path):
    d = DatabaseManager(tmp_path / "state.db")
    with d.get_connection() as conn:          # the memory's table, as the chat has it
        conn.execute("CREATE TABLE IF NOT EXISTS mem_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, conv TEXT, "
                     "role TEXT, content TEXT, created_at TEXT, tokens INTEGER, compressed INTEGER DEFAULT 0)")
    return d


def say(db, role, text):
    with db.get_connection() as conn:
        conn.execute("INSERT INTO mem_messages (conv, role, content, created_at, tokens) VALUES ('main', ?, ?, ?, 1)",
                     (role, text, get_iso_now()))


# The real messages that got a card back instead of an answer
@pytest.mark.parametrize("text", [
    "I'm just developing it now. So I dont know just yet. how would you structure it?",
    "I was asking you what you would choose.",
    "I dont know. I don't know the best way.",
    "I really don't know. and I don't know what approach to take either. any insight would be helpful",
    "Something else / not sure",
])
def test_no_card_when_the_user_wants_the_assistants_pick(db, tmp_path, text):
    t = AssistantTools(db, None, tmp_path, user_text=text)
    out = t.call("ask_user", CARD)
    assert out.startswith("Error: not asked") and t.question is None


def test_a_card_still_goes_when_a_detail_is_missing(db, tmp_path):
    t = AssistantTools(db, None, tmp_path, user_text="change the colors on the site")
    assert t.call("ask_user", CARD).startswith("Asked.") and t.question
    assert db.get_state(ASK_USER_STATE, "")


def test_no_second_card_right_after_one(db, tmp_path):
    t = AssistantTools(db, None, tmp_path, user_text="make me a render pipeline")
    assert t.call("ask_user", CARD).startswith("Asked.")
    say(db, "assistant", "Which rendering architecture fits best?")          # that reply, saved after the card
    say(db, "user", "hmm")
    t2 = AssistantTools(db, None, tmp_path, user_text="hmm")
    assert "previous reply was already a question card" in t2.call("ask_user", CARD)
    say(db, "assistant", "I'd go with microservices: ...")                   # an answer in between: cards allowed
    t3 = AssistantTools(db, None, tmp_path, user_text="ok which project is this for")
    assert t3.call("ask_user", {"question": "Which project is this for?", "options": ["site", "app"]}).startswith("Asked.")


# ---------------------------------------------------------------- closing questions, two replies in a row
ASKED = "Silky smooth 40-second Pindle runs. How does it look in motion now compared to the stuttery version?"


def test_a_second_closing_question_in_a_row_is_dropped():
    reply = ("Cutting it from 13 nodes to 6 with a 40-second run is a real gain. "
             "Is Opus setting up a grid-based scan for the stash, or a smarter sorting routine?")
    assert trim_tail_question(reply, ASKED) == "Cutting it from 13 nodes to 6 with a 40-second run is a real gain."
    two = "Makes sense, the stash reuses the inventory grid math. Are you chaining it next? Or Chaos first?"
    assert trim_tail_question(two, ASKED) == "Makes sense, the stash reuses the inventory grid math."


def test_closing_questions_that_stay():
    reply = "Cutting it from 13 nodes to 6 is a real gain. Is the stash next?"
    assert trim_tail_question(reply, "Got it, I'll leave that alone.") == reply        # previous didn't ask
    assert trim_tail_question(reply, ASKED, asked_card=True) == reply                  # a question card
    only = "Which mailbox do you mean, Gmail or Zoho?"
    assert trim_tail_question(only, ASKED) == only                                     # nothing else to say
    short = "Nice. Stash next?"
    assert trim_tail_question(short, ASKED) == short                                   # too little left
    statement = "Done: watch #5 tells you about every new inbox email."
    assert trim_tail_question(statement, ASKED) == statement


def test_the_persona_says_no_praise_and_gives_its_pick():
    from core.memory import FINAL_REMINDER
    assert "Don't praise the user" in ASSISTANT_PERSONA and "give your recommendation" in ASSISTANT_PERSONA
    assert "No praise" in FINAL_REMINDER            # measured: only the closing reminder held (eval_tone.py)
