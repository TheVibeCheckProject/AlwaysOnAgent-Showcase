"""The supervisor notices a worker thread that ended (2026-10-28 audit 3.3): a busy port made uvicorn call sys.exit(1),
which isn't an Exception, so the API thread ended silently while tasks ran on and DeskAI couldn't connect."""
import threading

import supervisor


def bare_supervisor():
    s = supervisor.UnifiedSupervisor.__new__(supervisor.UnifiedSupervisor)      # no daemon: just the thread logic
    s.running = True
    return s


def test_a_thread_that_ended_is_reported_but_not_during_shutdown():
    s = bare_supervisor()
    done = threading.Thread(target=lambda: None, name="WebHUDWorker")
    done.start()
    done.join()
    alive = threading.Event()
    running = threading.Thread(target=alive.wait, name="ScheduleRunner", daemon=True)
    running.start()
    try:
        assert s._dead_threads([done, running]) == ["WebHUDWorker"]
        s.running = False                                  # shutting down: threads ending is expected
        assert s._dead_threads([done, running]) == []
    finally:
        alive.set()


def test_the_api_thread_retries_a_busy_port_then_ends(monkeypatch):
    import uvicorn
    s = bare_supervisor()
    starts = []

    def busy_port(self):
        starts.append(1)
        raise SystemExit(1)                                # what uvicorn does when it can't bind
    monkeypatch.setattr(uvicorn.Server, "run", busy_port)
    monkeypatch.setattr(supervisor.time, "sleep", lambda s: None)
    monkeypatch.setattr(supervisor.UnifiedSupervisor, "API_ATTEMPTS", 3)
    s._run_hud_thread()                                    # returns instead of raising or looping forever
    assert len(starts) == 3
