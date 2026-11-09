"""Looking at the Mini's files from chat (core/mini_files.py, interfaces/hud/files_api.py): read only, inside the roots,
never secrets. 2026-10-27, Alex: show images, mp4 and wav files in chat when asked."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import mini_files
from core.assistant_tools import AssistantTools
from core.database import DatabaseManager


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "home"
    (root / "Videos").mkdir(parents=True)
    (root / "Videos" / "cabin bear.mp4").write_bytes(b"v" * 100)
    (root / "Music").mkdir()
    (root / "Music" / "intro.wav").write_bytes(b"w" * 50)
    (root / "Pictures").mkdir()
    (root / "Pictures" / "sinkhole.jpg").write_bytes(b"i")
    (root / "notes.txt").write_text("hello from the mini", encoding="utf-8")
    (root / ".env").write_text("SECRET=1", encoding="utf-8")
    (root / "api_token.txt").write_text("x", encoding="utf-8")
    (root / "AppData" / "Local").mkdir(parents=True)
    (root / "AppData" / "Local" / "photo.jpg").write_bytes(b"i")
    (tmp_path / "outside.jpg").write_bytes(b"i")
    monkeypatch.setattr(mini_files, "roots", lambda: [root.resolve()])
    return root


def test_find_by_name_and_kind(home):
    assert [Path(h["path"]).name for h in mini_files.find("bear")] == ["cabin bear.mp4"]
    assert [Path(h["path"]).name for h in mini_files.find("videos")] == ["cabin bear.mp4"]     # a kind word
    names = {Path(h["path"]).name for h in mini_files.find("*.jpg")}
    assert names == {"sinkhole.jpg"}                                    # not the one in AppData
    assert not mini_files.find("token") and not mini_files.find(".env")


def test_test_leftovers_and_caches_are_skipped(home):
    """The first live search returned pytest scratch files (aoa-aside\\pt_deploy) instead of real videos."""
    junk = home / "aoa-aside" / "pt_deploy" / "videos"
    junk.mkdir(parents=True)
    (junk / "vibe_check_1.mp4").write_bytes(b"x")
    (home / "build" / "out").mkdir(parents=True)
    (home / "build" / "out" / "bear.mp4").write_bytes(b"x")
    assert [Path(h["path"]).name for h in mini_files.find("*.mp4")] == ["cabin bear.mp4"]


def test_secrets_and_outside_paths_are_refused(home, tmp_path):
    for bad in (str(home / ".env"), str(home / "api_token.txt"), str(home / "AppData" / "Local" / "photo.jpg"),
                str(tmp_path / "outside.jpg"), str(home / "Videos" / ".." / ".." / "outside.jpg")):
        with pytest.raises(mini_files.FileAccessError):
            mini_files.check(bad)


def test_hidden_folders_and_any_agent_copys_data_are_refused(home):
    """2026-10-28 audit 0.1: the deny-list missed sign-ins kept in dot-folders (~\\.kaggle\\kaggle.json was readable
    and went to the cloud model), and only one exact spelling of the agent's data folder was refused."""
    (home / ".kaggle").mkdir()
    (home / ".kaggle" / "kaggle.json").write_text('{"key": "x"}', encoding="utf-8")
    (home / ".claude.json").write_text("{}", encoding="utf-8")
    lite = home / "Projects" / "AlwaysOnAgent-Lite" / "data"
    lite.mkdir(parents=True)
    (lite / "memory.json").write_text("{}", encoding="utf-8")
    for bad in (home / ".kaggle" / "kaggle.json", home / ".claude.json", lite / "memory.json"):
        with pytest.raises(mini_files.FileAccessError):
            mini_files.read_text(str(bad))
    assert not mini_files.find("kaggle") and not mini_files.find("*.json")
    assert ".claude.json" not in str(mini_files.listing(str(home)))


def test_a_big_text_file_is_read_by_window_not_whole(home, monkeypatch):
    """2026-10-28 audit 0.3: read() loaded the whole file to return 4,000 characters (a multi-GB log on the Mini)."""
    monkeypatch.setattr(mini_files, "WHOLE_READ_MAX_BYTES", 1000)
    log = home / "render.log"
    log.write_bytes("".join(f"line {i:05d}\n" for i in range(2000)).encode())      # 11 bytes a line, 22,000 bytes
    first = mini_files.read_text(str(log), chars=33)
    assert first.startswith("line 00000\nline 00001\nline 00002") and "read again from 33" in first
    later = mini_files.read_text(str(log), start=11 * 1500, chars=10)
    assert later.startswith("line 01500")
    assert "more bytes" not in mini_files.read_text(str(log), start=log.stat().st_size - 11)


def test_show_gives_a_line_deskai_can_play_and_the_route_serves_it(home):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from interfaces.hud import files_api
    line = mini_files.show_line(str(home / "Videos" / "cabin bear.mp4"))
    assert line.startswith("[▶ cabin bear.mp4 (100 B)](/api/files/m/") and line.endswith("/cabin%20bear.mp4)")
    assert mini_files.show_line(str(home / "Pictures" / "sinkhole.jpg")).startswith("![sinkhole.jpg](")
    assert mini_files.show_line(str(home / "Music" / "intro.wav")).startswith("[🔊 intro.wav")
    app = FastAPI()
    app.include_router(files_api.router)
    url = line[line.index("(/api") + 1:-1]
    r = TestClient(app).get(url)
    assert r.status_code == 200 and r.content == b"v" * 100 and r.headers["content-type"] == "video/mp4"
    secret_ref = mini_files.ref_for(home / ".env")
    assert TestClient(app).get(f"/api/files/m/{secret_ref}/.env").status_code == 404


def test_a_file_the_tool_showed_reaches_the_reply_even_if_the_model_drops_it(home):
    """2026-10-27: a fallback model wrote "show C:\\...\\vibe_check_….mp4" instead of the line; the video never came."""
    from core.memory import ensure_media_shown
    line = mini_files.show_line(str(home / "Videos" / "cabin bear.mp4"))
    trace = [{"name": "mini_files", "arguments": "{}", "result": f"To show it, put this line in your reply exactly "
                                                                 f"as it is:\n{line}"}]
    out = ensure_media_shown("Found it! Here is the latest:\n\nshow C:\\Users\\x\\cabin bear.mp4", trace)
    assert out.endswith(line) and "show C:\\" not in out
    assert ensure_media_shown(f"Here:\n{line}", trace) == f"Here:\n{line}"        # already there: unchanged
    assert ensure_media_shown("hi", []) == "hi"
    # The same evening a model wrote a video as a picture: a blank card, "unable to load image stream"
    assert ensure_media_shown("![vibe_check_1.mp4](https://x.com/vibe_check_1.mp4)", []) == \
        "[▶ vibe_check_1.mp4](https://x.com/vibe_check_1.mp4)"
    assert ensure_media_shown("![](/x/intro.wav)", []) == mini_files.show_line(str(home / "Music" / "intro.wav"))
    assert ensure_media_shown("![pic](/images/a.jpg)", []) == "![pic](/images/a.jpg)"


def test_a_shown_video_has_a_thumbnail_shared_with_the_videos_hub(home, monkeypatch, tmp_path):
    """2026-10-27, Alex: "That video really really needs a thumbnail. I already have the thumbnails in the videos hub"."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from core import task_thumbs
    from interfaces.hud import files_api, state
    monkeypatch.setattr(state, "VIDEOS_DIR", home / "Videos")
    made = []
    monkeypatch.setattr(task_thumbs, "extract_frame",
                        lambda video, out, **k: made.append(out) or out.parent.mkdir(parents=True, exist_ok=True)
                        or out.write_bytes(b"\xff\xd8jpeg") or True)
    app = FastAPI()
    app.include_router(files_api.router)
    line = mini_files.show_line(str(home / "Videos" / "cabin bear.mp4"))
    url = line[line.index("(/api") + 1:-1] + "/thumbnail"
    r = TestClient(app).get(url)
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert made == [home / "Videos" / ".thumbnails" / "cabin bear.mp4.jpg"]       # the hub's own cache
    assert TestClient(app).get(url).status_code == 200 and len(made) == 1           # cached, not made again
    wav = mini_files.show_line(str(home / "Music" / "intro.wav"))
    assert TestClient(app).get(wav[wav.index("(/api") + 1:-1] + "/thumbnail").status_code == 404


def test_a_made_up_link_becomes_the_real_file_or_its_name(home):
    """2026-10-27 18:06: with no tool call, a model wrote "[▶ x.mp4](/videos/x.mp4)", a card that could never load."""
    from core.memory import ensure_media_shown
    out = ensure_media_shown("[▶ cabin bear.mp4](/videos/cabin%20bear.mp4)", [])
    assert out == mini_files.show_line(str(home / "Videos" / "cabin bear.mp4"))
    assert ensure_media_shown("![gone.mp4](/videos/gone.mp4)", []) == "gone.mp4 (I couldn't find that file on the PC)"
    real = mini_files.show_line(str(home / "Music" / "intro.wav"))
    assert ensure_media_shown(real, []) == real                                # real links are left alone
    assert ensure_media_shown("[clip](https://x.com/a.mp4)", []) == "[clip](https://x.com/a.mp4)"


def test_a_file_just_shown_isnt_shown_again_unless_asked(home, tmp_path):
    """2026-10-27 18:17: "yup you're right the normal lineups!" and then "?" each got the TikTok video again."""
    db = DatabaseManager(tmp_path / "s.db")
    from core.memory import MemoryManager
    MemoryManager(db)                                                          # creates mem_messages
    video = str(home / "Videos" / "cabin bear.mp4")
    conn = db.get_connection()
    conn.execute("INSERT INTO mem_messages (conv, role, content, created_at, tokens) VALUES ('c', 'assistant', ?, 'now', 9)",
                 (mini_files.show_line(video),))
    conn.commit()
    t = AssistantTools(db, None, tmp_path, user_text="yup your right the normal lineups!")
    assert t.call("mini_files", {"action": "show", "path": video}).startswith("Error: that file is already")
    t = AssistantTools(db, None, tmp_path, user_text="show it again")
    assert "/api/files/m/" in t.call("mini_files", {"action": "show", "path": video})
    t = AssistantTools(db, None, tmp_path, user_text="?")
    assert "/api/files/m/" in t.call("mini_files", {"action": "show", "path": str(home / "Music" / "intro.wav")})


def test_screen_shots_in_two_words_is_the_screenshots_kind(home):
    (home / "Pictures" / "step-01.jpg").write_bytes(b"i")
    assert {Path(h["path"]).name for h in mini_files.find("screen shots")} == {"sinkhole.jpg", "step-01.jpg"}


def test_the_chat_tool(home, tmp_path):
    t = AssistantTools(DatabaseManager(tmp_path / "s.db"), None, tmp_path)
    assert "cabin bear.mp4" in t.call("mini_files", {"action": "find", "query": "bear"})
    assert "hello from the mini" in t.call("mini_files", {"action": "read", "path": str(home / "notes.txt")})
    assert "Videos\\" in t.call("mini_files", {"action": "list", "path": str(home)})
    assert "/api/files/m/" in t.call("mini_files", {"action": "show", "path": str(home / "Music" / "intro.wav")})
    assert t.call("mini_files", {"action": "read", "path": str(home / ".env")}).startswith("Error:")


def test_made_up_media_links_are_looked_up_at_most_twice_and_never_guessed(home, monkeypatch):
    """2026-10-28 audit 0.9: each made-up link walked the disk (up to 6 s) and showed the first file with that name."""
    from core.memory import ensure_media_shown
    (home / "Music" / "take1").mkdir()
    (home / "Music" / "take1" / "intro.wav").write_bytes(b"w")         # a second intro.wav
    (home / "Videos" / "bear.mp4").write_bytes(b"v")                    # only one of these
    walks = []
    real_find = mini_files.find
    monkeypatch.setattr(mini_files, "find", lambda *a, **k: walks.append(a) or real_find(*a, **k))
    reply = ("[▶ bear.mp4](/videos/bear.mp4) [🔊 intro.wav](intro.wav) "
             "[▶ a.mp4](a.mp4) [▶ b.mp4](b.mp4)")
    out = ensure_media_shown(reply, [])
    assert len(walks) == 2                                                # not one per link
    assert "/api/files/m/" in out                                        # bear.mp4: one file, shown
    assert "several files on the PC have that name" in out               # intro.wav: two, none guessed
    assert out.count("(not looked up)") == 2
