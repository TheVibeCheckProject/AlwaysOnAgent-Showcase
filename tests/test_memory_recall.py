"""
Memory recall benchmark
=======================
Plants about 30 things the assistant should remember among ~400 messages of unrelated chatter,
lets compression push them out of the recent window, then asks about each one and checks whether
the answer's source reaches the prompt that build_context() builds. It also measures how many
tokens of memory each prompt carries, including for small talk that needs none.

No LLM answers anything, so it's deterministic and free. Summaries come from a stand-in of
realistic length that doesn't contain the planted details: this measures fact and archive
retrieval, not summary quality. It uses the real local embedding model when it's downloaded
(otherwise the word-hash stand-in, with which paraphrased questions mostly miss).

Cases marked known_gap are things the current design can't recall yet (see the memory plan).
They're reported, not asserted, and should turn into hits as later phases land; when one does,
drop its known_gap flag so it stays fixed.

Run:  python tests/test_memory_recall.py
"""

import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# Tests never run the real TiktokVideos (it would render and send real videos): point the agent at a
# folder where it isn't installed, so an accidental call fails harmlessly
os.environ.setdefault("TIKTOK_VIDEOS_DIR", str(Path(tempfile.gettempdir()) / "no_tiktokvideos_in_tests"))
os.environ.setdefault("IMAGEGEN_DIR", str(Path(tempfile.gettempdir()) / "no_imagegen_in_tests"))
os.environ.setdefault("SOCIALPOSTENGINE_DIR", str(Path(tempfile.gettempdir()) / "no_socialpostengine_in_tests"))
os.environ.setdefault("VIBESTATS_DIR", str(Path(tempfile.gettempdir()) / "no_vibestats_in_tests"))

import core.config  # noqa: F401  (loads .env)
from core.database import DatabaseManager
from core.memory import ASSISTANT_PERSONA, MemoryManager

TEST_DIR = Path(tempfile.mkdtemp(prefix="memory_recall_"))
# Floors for cases that aren't known gaps (baseline 2026-09-23: 100% / 100%); enforced with the real model only
MIN_RECALL = 1.0
MIN_TOP3 = 0.95
MEMORY_PARTS = ("profile", "summary_recent", "facts", "summaries", "archive")
# Rebuilt for every message (the profile and the latest summary sit in the cacheable start of the prompt)
PER_MESSAGE_PARTS = ("facts", "summaries", "archive")

# Stored as facts, the way extraction would have saved them: (content, category, importance)
FACTS = [
    ("The user's dog is a golden retriever named Pepper.", "person", 3),
    ("The user lives in Springfield, Illinois.", "profile", 3),
    ("The user wants TikTok videos posted at 10am.", "preference", 3),
    ("The user's website example-site.com must use vanilla HTML, CSS and JS only.", "instruction", 5),
    ("The user's laptop has an NVIDIA GTX 1650 with 4GB of VRAM.", "fact", 3),
    ("The user is allergic to peanuts.", "profile", 3),
    ("The user's sister Maya is getting married on 2026-11-14.", "person", 3),
    ("The user's sister Maya works as a nurse at St. Bernards.", "person", 3),
    ("The user wants the assistant to always ask before posting anything publicly.", "instruction", 5),
    ("The user drinks their coffee black.", "preference", 2),
    ("The user is learning Spanish with Duolingo.", "project", 3),
    ("The user's GitHub username is alexm-dev.", "profile", 3),
    ("The user's brand colors are teal and violet.", "preference", 3),
]
# Plausible facts that compete for the same slots
DISTRACTORS = [
    ("The user prefers dark mode in every app.", "preference", 2),
    ("The user's favorite movie is Interstellar.", "fact", 2),
    ("The user plays pickup basketball on weekends.", "fact", 2),
    ("The user uses Windows 11 on their laptop.", "fact", 3),
    ("The user wants replies kept short, like text messages.", "instruction", 4),
    ("The user's TikTok account is about anonymous affirmation cards.", "project", 4),
    ("The user likes lo-fi music while coding.", "preference", 2),
    ("The user drives a 2016 Honda Civic.", "fact", 2),
    ("The user prefers Edge over Chrome for testing.", "preference", 3),
    ("The user's best friend is named Jordan.", "person", 3),
    ("The user wants videos to feel fluid, with no flicker.", "preference", 4),
    ("The user is saving up for a new GPU.", "plan", 3),
    ("The user's cat is named Biscuit.", "person", 3),
    ("The user dislikes cilantro.", "preference", 1),
    ("The user wants weekly progress recaps on Sundays.", "instruction", 3),
    ("The user works on the agent mostly late at night.", "fact", 2),
    ("The user uses DeskAI to talk to the assistant.", "fact", 3),
    ("The user's HUD runs on the same PC as the agent.", "fact", 2),
]
# People in the user's life: many similar facts, so the right one has to win on relevance
PEOPLE = [("best friend", "Jordan", "an electrician", "Memphis"), ("cousin", "Leah", "a dental hygienist", "Chicago"),
          ("coworker", "Marcus", "a line cook", "Decatur"), ("neighbor", "Tasha", "a mail carrier", "Springfield"),
          ("nephew", "Eli", "a college student", "Peoria"), ("aunt", "Rosa", "a bank teller", "Tulsa"),
          ("college roommate", "Dante", "a software tester", "Dallas"), ("gym friend", "Priya", "a pharmacist", "Champaign"),
          ("brother-in-law", "Caleb", "a truck driver", "Bloomington"), ("old manager", "Nina", "a store manager", "Quincy"),
          ("barber", "Omar", "a barber", "Springfield"), ("grandmother", "Grace", "a retired teacher", "Galesburg"),
          ("church friend", "Theo", "a youth pastor", "Joliet"), ("coworker", "Ivy", "a cashier", "Aurora"),
          ("uncle", "Sam", "a farmer", "Rockford")]
MONTHS = ["January", "February", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
for n, (rel, name, job, city) in enumerate(PEOPLE):
    DISTRACTORS += [(f"The user's {rel} {name} works as {job}.", "person", 3),
                    (f"The user's {rel} {name} lives in {city}.", "person", 3),
                    (f"The user's {rel} {name}'s birthday is {MONTHS[n % len(MONTHS)]} {n + 5}.", "person", 3)]
# Said in conversation only (never saved as facts): (role, text)
SAID = [
    ("user", "The wax seal should peel off with the envelope flap, not split in half."),
    ("user", "Let's use the Open-Meteo API for weather because it needs no key."),
    ("user", "The card flip should take about 1.2 seconds."),
    ("user", "I bought a Samsung T7 1TB SSD for the video renders."),
    ("user", "The treasure chest lid should creak open slowly, about 3 seconds."),
    ("user", "My landlord is fixing the AC on Thursday."),
    ("user", "For the thumbnail font let's go with Outfit Bold."),
    ("user", "Exported videos have to stay under 50 MB."),
    ("user", "My mom's birthday is March 3rd."),
    ("user", "The HUD should run on port 8080."),
    ("assistant", "I set the memory compression trigger to 2,000 tokens."),
]
# Question and answer said back to back: the question is what matches, the answer is only in the reply
SAID_PAIRS = [
    ("What sample rate should the voiceover audio use?", "Use 48 kHz so it lines up with the video track."),
]
EVENTS = [
    "Background task task-012 \"Resize thumbnails\" failed (exit code 1) via groq. Result excerpt: CUDA out of memory on the GPU.",
    "Background task task-015 \"Add footer\" finished successfully via groq. Result excerpt: Created footer.html with social links.",
    "The user queued background task task-020 via voice note: build a sitemap for the website",
]
# (question, text that must reach the prompt, kind, known_gap)
QUESTIONS = [
    ("what's my puppy called?", "Pepper", "fact", False),
    ("where do I live?", "Springfield", "fact", False),
    ("what time should my videos go out?", "10am", "fact", False),
    ("can I use React on my site?", "vanilla", "fact", False),
    ("what graphics card do I have?", "GTX 1650", "fact", False),
    ("is there anything I can't eat?", "peanuts", "fact", False),
    ("when is Maya's wedding?", "2026-11-14", "fact", False),
    ("what does Maya do for a living?", "nurse", "fact", False),
    ("can you post this clip to TikTok for me right now?", "ask before posting", "fact", False),
    ("how do I take my coffee?", "black", "fact", False),
    ("what language am I studying?", "Spanish", "fact", False),
    ("what's my github handle?", "alexm-dev", "fact", False),
    ("what colors does my brand use?", "teal", "fact", False),
    # The memory is implied rather than asked about: weather needs the user's town, snacks the allergy.
    # The weather one was already missed before the cache-first layout (2026-09-23); in real use the town
    # is usually in the core profile, and the persona tells the model to search memory for it.
    ("what's the weather going to be like tomorrow?", "Springfield", "implied", True),
    ("any snack ideas for tonight?", "peanuts", "implied", True),
    ("how was the wax seal supposed to open?", "peel off", "archive", False),
    ("why did we pick that weather API?", "no key", "archive", False),
    ("how long should the card flip last?", "1.2 seconds", "archive", False),
    ("which external drive did I buy?", "T7", "archive", False),
    ("how fast should the chest lid open?", "3 seconds", "archive", False),
    ("when is the AC getting fixed?", "Thursday", "archive", False),
    ("which font did we pick for thumbnails?", "Outfit Bold", "archive", False),
    ("what's the size limit for exported videos?", "50 MB", "archive", False),
    ("when is my mom's birthday?", "March 3", "archive", False),
    ("what port is the dashboard on?", "8080", "archive", False),
    ("what did you set the compression trigger to?", "2,000", "archive", False),
    ("what sample rate did we pick for the voiceover?", "48 kHz", "archive", False),
    # Task results and other events (searchable since 2026-09-23)
    ("why did the thumbnail resize task fail?", "out of memory", "event", False),
    ("did the footer task work?", "footer.html", "event", False),
    ("what was that voice note task about?", "sitemap", "event", False),
]
# Asked right after a turn that names who "she"/"he" is (the previous message is searched along with it)
FOLLOW_UPS = [
    (("Let's talk about my sister Maya.", "Sure, what about Maya?"), "what does she do for work?", "nurse"),
    (("My nephew Eli is visiting next week.", "Nice! Anything you want to plan?"), "where does he live?", "Peoria"),
]
SMALL_TALK = ["hey, how's it going?", "thanks, that's perfect!", "what's 12 times 8?", "lol nice",
              "good morning!", "ok cool, sounds good"]

FILLER = [
    ("Can you check the render queue for batch {i}?", "Batch {i} is queued behind {k} others."),
    ("What's the status of upload number {i}?", "Upload {i} is still processing on the server."),
    ("Remind me to stretch after session {i}.", "Okay, I'll remind you after session {i}."),
    ("Did the preview for clip {i} look okay?", "Clip {i}'s preview looks fine to me."),
    ("How many followers did post {i} bring in?", "Post {i} brought in {k} new followers."),
    ("Any new comments on video {i}?", "Video {i} has {k} new comments, mostly positive."),
]


class StandInLLM:
    """Summaries of realistic length that carry none of the planted details."""

    def chat(self, messages, **kw):
        return ("- The user and assistant went over render batches, upload progress and comment counts.\n"
                "- The assistant reported queue positions and follower numbers for several posts.\n"
                "- The user asked for stretch reminders between work sessions; the assistant agreed.\n"
                "- Previews for several clips were reviewed and looked fine.\n") * 3


def _embedder():
    from core.embedder import MODELS_DIR, LocalEmbedder
    if any(MODELS_DIR.rglob("model_quantized.onnx")):
        emb = LocalEmbedder()
        if emb.ensure_loaded():
            return emb, "real bge-small model"
    from tests.test_memory import FakeEmbedder
    return FakeEmbedder(), "word-hash stand-in (download the model for meaningful numbers)"


def _filler(mem, start, n):
    for i in range(start, start + n):
        q, a = FILLER[i % len(FILLER)]
        mem.add_message("user", q.format(i=i, k=(i * 7) % 43 + 2))
        mem.add_message("assistant", a.format(i=i, k=(i * 7) % 43 + 2))
        mem.maybe_compress()


RANKED_SECTIONS = ("Memories relevant to this message\n", "Related things said before (from the archive)\n")
LAST_PARTS = {}          # token breakdown of the most recent _probe() call


def _probe(mem, question, needle=""):
    """(needle reached the prompt, memory tokens, the needle's rank among the retrieved items shown or None)."""
    parts = {}
    msgs = mem.build_context(question, breakdown=parts)
    LAST_PARTS.clear()
    LAST_PARTS.update(parts)
    text = "\n".join(m["content"] for m in msgs).replace(ASSISTANT_PERSONA, "")
    rank = None
    for header in RANKED_SECTIONS:          # wherever the section is, whatever its heading level
        if header in text:
            lines = re.split(r"\n\n#", text.split(header, 1)[1], maxsplit=1)[0].splitlines()
            rank = rank or next((i + 1 for i, l in enumerate(lines) if needle and needle.lower() in l.lower()), None)
    return needle.lower() in text.lower(), sum(parts.get(p, 0) for p in MEMORY_PARTS), rank


def run_benchmark(verbose=True):
    emb, emb_name = _embedder()
    mem = MemoryManager(DatabaseManager(TEST_DIR / "recall.db"), llm=StandInLLM(), embedder=emb)
    for content, cat, imp in DISTRACTORS + FACTS:
        mem.add_fact(content, cat, imp)
    n = 0
    for role, text in SAID:
        _filler(mem, n, 8)
        n += 8
        mem.add_message(role, text)
        mem.maybe_compress()
    for question, answer in SAID_PAIRS:
        _filler(mem, n, 8)
        n += 8
        mem.add_message("user", question)
        mem.add_message("assistant", answer)
        mem.maybe_compress()
    for text in EVENTS:
        _filler(mem, n, 8)
        n += 8
        mem.record_event(text)
    _filler(mem, n, 120)          # push everything planted well out of the recent window

    results = []      # (kind, question, needle, known_gap, hit, memory tokens, rank)
    for question, needle, kind, gap in QUESTIONS:
        results.append((kind, question, needle, gap, *_probe(mem, question, needle)))
    small_talk, small_talk_parts = [], dict.fromkeys(MEMORY_PARTS, 0.0)
    for q in SMALL_TALK:
        small_talk.append(_probe(mem, q)[1])
        small_talk_parts.setdefault("per_message", 0.0)
        small_talk_parts["per_message"] += sum(LAST_PARTS.get(p, 0) for p in PER_MESSAGE_PARTS) / len(SMALL_TALK)
        for p in MEMORY_PARTS:
            small_talk_parts[p] += LAST_PARTS.get(p, 0) / len(SMALL_TALK)
    for (said, reply), question, needle in FOLLOW_UPS:
        mem.add_message("user", said)
        mem.add_message("assistant", reply)
        results.append(("follow-up", question, needle, False, *_probe(mem, question, needle)))

    scored = [r for r in results if not r[3]]
    recall = sum(r[4] for r in scored) / len(scored)
    report = {
        "embedder": emb_name,
        "recall": recall,
        "top3": sum(1 for r in scored if r[6] and r[6] <= 3) / len(scored),
        "by_kind": {},
        "avg_memory_tokens": round(sum(r[5] for r in results) / len(results)),
        "max_memory_tokens": max(r[5] for r in results),
        "small_talk_memory_tokens": round(sum(small_talk) / len(small_talk)),
        "small_talk_parts": {p: round(v) for p, v in small_talk_parts.items() if p != "per_message"},
        "small_talk_per_message_tokens": round(small_talk_parts["per_message"]),
        "misses": [(r[0], r[1], r[2]) for r in results if not r[4]],
        "buried": [(r[0], r[1], r[6]) for r in results if r[4] and r[6] and r[6] > 3],
    }
    for kind in dict.fromkeys(r[0] for r in results):
        rows = [r for r in results if r[0] == kind]
        report["by_kind"][kind] = (sum(r[4] for r in rows), sum(1 for r in rows if r[6] and r[6] <= 3),
                                   len(rows), all(r[3] for r in rows))
    if verbose:
        print(f"  Embedder: {emb_name}")
        print(f"  {'kind':<10} {'in prompt':>9} {'ranked top 3':>13}")
        for kind, (hits, top3, total, gap) in report["by_kind"].items():
            print(f"  {kind:<10} {f'{hits}/{total}':>9} {f'{top3}/{total}':>13}{'   (known gap, not scored)' if gap else ''}")
        print(f"  Recall (scored cases): {recall:.0%} reach the prompt, {report['top3']:.0%} ranked in the top 3")
        print(f"  Memory tokens per prompt: avg {report['avg_memory_tokens']}, max {report['max_memory_tokens']}; "
              f"small talk avg {report['small_talk_memory_tokens']} (ideally near 0)")
        print(f"  Small talk memory tokens by part: {report['small_talk_parts']}")
        print(f"  Small talk memory tokens rebuilt every message (not cacheable): {report['small_talk_per_message_tokens']}")
        for kind, question, needle in report["misses"]:
            print(f"    missed [{kind}] {question!r} (needed {needle!r})")
        for kind, question, rank in report["buried"]:
            print(f"    buried [{kind}] {question!r} at rank {rank}")
    return report


def test_memory_recall_benchmark():
    report = run_benchmark()
    if not report["embedder"].startswith("real"):
        print("  (floors not enforced without the real embedding model)")
        return
    assert report["recall"] >= MIN_RECALL, f"recall {report['recall']:.0%} fell below {MIN_RECALL:.0%}"
    assert report["top3"] >= MIN_TOP3, f"top-3 rate {report['top3']:.0%} fell below {MIN_TOP3:.0%}"


if __name__ == "__main__":
    try:
        print("[test_memory_recall_benchmark]")
        test_memory_recall_benchmark()
        print("\nRECALL BENCHMARK PASSED")
    finally:
        shutil.rmtree(TEST_DIR, ignore_errors=True)
