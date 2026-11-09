#!/usr/bin/env python3
"""
Always-On Agent Unified Supervisor
===================================
Runs the Autonomous AI Background Daemon, the Web HUD / DeskAI API and the push notifier
concurrently within a single supervised process.

Features:
- Spawns and supervises both worker threads.
- Auto-recovers on unhandled exceptions.
- Provides graceful shutdown on SIGINT / SIGTERM.
- Rotates unified logs in worker.log.
"""

import logging
import os
import signal
import sys
import threading
import time

# Suppress third-party loggers and HF Hub token warnings
import warnings
from pathlib import Path
from typing import ClassVar

from core.config import LogRedactionFilter
from core.daemon import AutonomousDaemon
from core.push import TaskPushWatcher
from core.schedules import ScheduleRunner
from interfaces.hud import app as hud_app

warnings.filterwarnings("ignore", module="huggingface_hub")
warnings.filterwarnings("ignore", message=".*unauthenticated requests to the HF Hub.*")
for noisy_lib in ("httpx", "httpcore", "huggingface_hub", "urllib3", "transformers", "onnxruntime", "uvicorn.access"):
    logging.getLogger(noisy_lib).setLevel(logging.WARNING)

try:
    import colorama
    colorama.init(autoreset=True)
    COLOR_ENABLED = True
    C_CYAN = colorama.Fore.CYAN + colorama.Style.BRIGHT
    C_BLUE = colorama.Fore.BLUE + colorama.Style.BRIGHT
    C_GREEN = colorama.Fore.GREEN + colorama.Style.BRIGHT
    C_YELLOW = colorama.Fore.YELLOW + colorama.Style.BRIGHT
    C_WHITE = colorama.Fore.WHITE + colorama.Style.BRIGHT
    C_MAGENTA = colorama.Fore.MAGENTA + colorama.Style.BRIGHT
    C_DIM = colorama.Style.DIM
    C_RESET = colorama.Style.RESET_ALL
except Exception:
    COLOR_ENABLED = False
    C_CYAN = C_BLUE = C_GREEN = C_YELLOW = C_WHITE = C_MAGENTA = C_DIM = C_RESET = ""


class SupervisorColorFormatter(logging.Formatter):
    """High-aesthetic ANSI color formatter for Windows Command Prompt and PowerShell."""
    LEVEL_COLORS: ClassVar[dict[int, str]] = {
        logging.DEBUG: colorama.Fore.LIGHTBLACK_EX if COLOR_ENABLED else "",
        logging.INFO: colorama.Fore.CYAN if COLOR_ENABLED else "",
        logging.WARNING: colorama.Fore.YELLOW + colorama.Style.BRIGHT if COLOR_ENABLED else "",
        logging.ERROR: colorama.Fore.RED + colorama.Style.BRIGHT if COLOR_ENABLED else "",
        logging.CRITICAL: colorama.Back.RED + colorama.Fore.WHITE + colorama.Style.BRIGHT if COLOR_ENABLED else "",
    }

    def format(self, record):
        time_str = self.formatTime(record, "%Y-%m-%d %H:%M:%S")
        lvl_color = self.LEVEL_COLORS.get(record.levelno, "")
        reset = C_RESET if COLOR_ENABLED else ""
        dim = C_DIM if COLOR_ENABLED else ""
        magenta = C_MAGENTA if COLOR_ENABLED else ""

        name_tag = f"{magenta}[{record.name}]{reset}"
        level_tag = f"{lvl_color}[{record.levelname}]{reset}"
        return f"{dim}[{time_str}]{reset} {name_tag} {level_tag} {record.getMessage()}"


handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(SupervisorColorFormatter())
# Every line through the root logger (the API server's included) has tokens and ?token=/&sig= values hidden
handler.addFilter(LogRedactionFilter())
logging.root.handlers = [handler]
logging.root.setLevel(logging.INFO)
logger = logging.getLogger("Supervisor")


def get_lan_ip() -> str:
    """Returns local IPv4 address on the primary network interface."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"


def get_tailscale_ip() -> str | None:
    """Returns local Tailscale IPv4 address if available."""
    import shutil
    import subprocess
    ts_bin = shutil.which("tailscale")
    if not ts_bin:
        for p in (r"C:\Program Files\Tailscale\tailscale.exe", r"C:\Program Files (x86)\Tailscale\tailscale.exe"):
            if os.path.exists(p):
                ts_bin = p
                break
    if ts_bin:
        try:
            out = subprocess.check_output([ts_bin, "ip", "-4"], timeout=0.8, text=True).strip()
            if out:
                return out.splitlines()[0].strip()
        except Exception:
            pass
    return None


def _warm_memory_models() -> str:
    """Pre-warms the local models: bge-small (tool routing, web_research) and memory search's model (IDEAS #15)."""
    try:
        from core.embedder import get_embedder, get_memory_embedder
        get_embedder().ensure_loaded()
        mem_model = get_memory_embedder()
        if mem_model.ensure_loaded():
            return f"{getattr(mem_model, 'model', 'BGE-small-en-v1.5 INT8 ONNX')} (Ready)"
        return "Keyword Search Fallback"
    except Exception:
        return "Keyword Search Fallback"


def _cloud_status() -> tuple:
    """Cloud LLM provider status, and the chat model tried first. The model comes from CHAT_MODELS in
    core/llm/__init__.py (the first route whose provider has a key), never a name typed here: a hard-coded name
    went stale when the chat order changed (2026-10-06) and the banner kept advertising the wrong model."""
    chat_status = "unknown"
    try:
        from core.llm import CloudLLM
        _llm = CloudLLM()
        # Read keys the way core/llm/ does (CLOUDFLARE_API_TOKEN, GOOGLE_API_KEY fallback), not a guessed
        # {PROVIDER}_API_KEY name: that guess never listed Cloudflare.
        cloud_keys = [p for p in CloudLLM.PROVIDERS if p != "ollama" and _llm.keys.get(p)]
        cloud_status = ", ".join(cloud_keys) if cloud_keys else "none configured"
        chat_status = next((r for r in CloudLLM.CHAT_MODELS if _llm.keys.get(CloudLLM._split(r)[0])),
                           "no provider key set")
    except Exception:
        cloud_status = "auto"
    return chat_status, cloud_status


class UnifiedSupervisor:
    def __init__(self, workspace_dir: Path):
        self.workspace_dir = workspace_dir
        self.running = True

        # Initialize the Daemon
        self.daemon = AutonomousDaemon(
            workspace_dir=workspace_dir,
            interval_seconds=int(os.environ.get("DAEMON_INTERVAL", "30")),
            db_file="data/state.db",
            log_file="logs/worker.log"
        )

        # Phone notifications when tasks finish or fail (DeskAI, via Firebase)
        self.push_watcher = TaskPushWatcher(self.daemon.db)
        # Scheduled videos, reminders and tasks (core/schedules.py); what ran goes into the assistant's memory
        self.schedule_runner = ScheduleRunner(self.daemon.db, on_event=self._record_schedule_event)

        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def _record_schedule_event(self, text: str) -> None:
        # ScheduleRunner's on_event is Callable[[str], None]; record_event returns the event id,
        # which the runner ignores, so this wrapper drops it instead of loosening the runner's type.
        self.daemon.memory.record_event(text)

    def _handle_signal(self, signum, frame):
        logger.info(f"Received signal {signum}. Initiating graceful shutdown...")
        self.running = False
        self.daemon.running = False
        self.push_watcher.running = False
        self.schedule_runner.running = False
        if hasattr(self, "hud_server") and self.hud_server:
            self.hud_server.should_exit = True

    def _run_daemon_thread(self):
        while self.running:
            try:
                self.daemon.run()
            except Exception:
                logger.exception("Daemon thread encountered error")
                if self.running:
                    time.sleep(5)

    def _run_hud_thread(self):
        import uvicorn
        host = os.environ.get("HUD_HOST", "0.0.0.0")
        port = int(os.environ.get("HUD_PORT", "8080"))
        failures = 0
        while self.running:
            try:
                config = uvicorn.Config(hud_app, host=host, port=port, log_level="warning")
                self.hud_server = uvicorn.Server(config)
                self.hud_server.run()
                failures = 0
            except (Exception, SystemExit) as e:
                # A busy port makes uvicorn call sys.exit(1): SystemExit isn't an Exception, so it used to end this
                # thread silently while tasks ran on and DeskAI couldn't connect (2026-10-28 audit 3.3)
                failures += 1
                logger.error(f"Web HUD thread stopped ({type(e).__name__}: {e}); attempt {failures} of "
                             f"{self.API_ATTEMPTS}")
                if failures >= self.API_ATTEMPTS:
                    return                    # the supervisor sees the thread end and relaunches the agent
            if self.running:
                time.sleep(5)

    API_ATTEMPTS = 6           # API start-ups in a row that may fail (about 30 s) before the agent is relaunched

    def _dead_threads(self, threads) -> list:
        """Names of the worker threads that ended while the agent should be running."""
        return [t.name for t in threads if not t.is_alive()] if self.running else []

    def _print_banner(self, ascii_logo: str, memory_status: str, chat_status: str, cloud_status: str,
                      lan_ip: str, ts_ip: str | None, port: int) -> None:
        hud_url = f"http://localhost:{port}"
        lan_url = f"http://{lan_ip}:{port}" if lan_ip != "127.0.0.1" else None
        ts_url = f"http://{ts_ip}:{port}" if ts_ip else None
        box_border = f"{C_BLUE}+" + ("-" * 72) + f"+{C_RESET}"

        # Everything printed here is also saved (colour codes included) to data/startup_specs.txt, so the
        # MatrixRain screen can replay these specs at login and melt them into the rain
        banner = []

        def show(line: str = "") -> None:
            print(line)
            banner.append(line)

        show(f"{C_CYAN}{ascii_logo}{C_RESET}")
        show(f"  {C_WHITE}>> {C_GREEN}AUTONOMOUS 24/7 AI AGENT {C_WHITE}/// {C_BLUE}DESKAI COMPANION {C_WHITE}/// {C_YELLOW}MISSION CONTROL {C_WHITE}<<{C_RESET}\n")

        show(box_border)
        show(f"{C_BLUE}|{C_RESET}  {C_GREEN}*{C_RESET} {C_WHITE}Workspace{C_RESET}           : {C_DIM}{self.workspace_dir}{C_RESET}")
        show(f"{C_BLUE}|{C_RESET}  {C_GREEN}*{C_RESET} {C_WHITE}Autonomous Daemon{C_RESET}   : {C_GREEN}ONLINE{C_RESET} {C_DIM}(Interval: {self.daemon.interval_seconds}s, Continuous Loop){C_RESET}")
        show(f"{C_BLUE}|{C_RESET}  {C_GREEN}*{C_RESET} {C_WHITE}Conversational LLM{C_RESET}  : {C_CYAN}{chat_status}{C_RESET} {C_DIM}(first of CHAT_MODELS){C_RESET}")
        show(f"{C_BLUE}|{C_RESET}  {C_GREEN}*{C_RESET} {C_WHITE}Cloud LLM Routing{C_RESET}   : {C_CYAN}{cloud_status}{C_RESET}")
        show(f"{C_BLUE}|{C_RESET}  {C_GREEN}*{C_RESET} {C_WHITE}Semantic Memory{C_RESET}     : {C_GREEN}ACTIVE{C_RESET} {C_DIM}({memory_status}){C_RESET}")
        show(f"{C_BLUE}|{C_RESET}  {C_GREEN}*{C_RESET} {C_WHITE}Local API{C_RESET}           : {C_YELLOW}{hud_url}{C_RESET}")
        if lan_url:
            show(f"{C_BLUE}|{C_RESET}  {C_GREEN}*{C_RESET} {C_WHITE}LAN / WiFi Access{C_RESET}   : {C_YELLOW}{lan_url}{C_RESET}")
        if ts_url:
            show(f"{C_BLUE}|{C_RESET}  {C_GREEN}*{C_RESET} {C_WHITE}Tailscale Remote{C_RESET}    : {C_YELLOW}{ts_url}{C_RESET}")
        show(box_border + "\n")
        banner.append(f"  {C_GREEN}>> All systems initialized and actively supervised. Ready for tasks.{C_RESET}")
        try:
            specs = Path(__file__).resolve().parent / "data" / "startup_specs.txt"
            specs.parent.mkdir(exist_ok=True)
            specs.write_text("\n".join(banner) + "\n", encoding="utf-8")
        except OSError:
            pass

    def _start_workers(self):
        t_hud = threading.Thread(target=self._run_hud_thread, name="WebHUDWorker", daemon=True)
        t_daemon = threading.Thread(target=self._run_daemon_thread, name="DaemonWorker", daemon=True)
        t_push = threading.Thread(target=self.push_watcher.run, name="PushWatcher", daemon=True)
        t_schedules = threading.Thread(target=self.schedule_runner.run, name="ScheduleRunner", daemon=True)

        t_hud.start()
        time.sleep(0.15)
        t_daemon.start()
        t_push.start()
        t_schedules.start()
        return t_hud, t_daemon, t_push, t_schedules

    def _watch_workers(self, workers) -> None:
        try:
            while self.running:
                time.sleep(1)
                dead = self._dead_threads(workers)
                if dead:
                    # Nothing restarts one thread on its own, so the agent restarts whole: run_forever.ps1 relaunches
                    # on any exit code but 3, backing off if it keeps dying (2026-10-28 audit 3.3)
                    logger.error(f"Worker thread(s) stopped: {', '.join(dead)}. Exiting so the agent is relaunched.")
                    logging.shutdown()
                    os._exit(1)
        except (KeyboardInterrupt, SystemExit):
            self.running = False
            self.daemon.running = False
            if hasattr(self, "hud_server") and self.hud_server:
                self.hud_server.should_exit = True

    def run(self):
        os.environ["SUPERVISOR_ACTIVE"] = "1"
        lan_ip = get_lan_ip()
        ts_ip = get_tailscale_ip()
        port = int(os.environ.get("HUD_PORT", "8080"))

        memory_status = _warm_memory_models()
        chat_status, cloud_status = _cloud_status()

        ascii_logo = r"""
    _    _                            ___        _                  _   
   / \  | |_      ____ _ _   _ ___   / _ \ _ __ / \   __ _  ___ _ _| |_ 
  / _ \ | \ \ /\ / / _` | | | / __| | | | | '_ / _ \ / _` |/ _ \ '_ \ __|
 / ___ \| |\ V  V / (_| | |_| \__ \ | |_| | | / ___ \ (_| |  __/ | | | |_ 
/_/   \_\_| \_/\_/ \__,_|\__, |___/  \___/|_|/_/   \_\__, |\___|_| |_|\__|
                         |___/                        |___/               """

        self._print_banner(ascii_logo, memory_status, chat_status, cloud_status, lan_ip, ts_ip, port)

        workers = self._start_workers()

        logger.info(f"{C_GREEN}All systems initialized and actively supervised. Ready for tasks.{C_RESET}")

        self._watch_workers(workers)

        logger.info("Unified Supervisor stopped cleanly.")


if __name__ == "__main__":
    workspace = Path(__file__).resolve().parent
    supervisor = UnifiedSupervisor(workspace)
    supervisor.run()
