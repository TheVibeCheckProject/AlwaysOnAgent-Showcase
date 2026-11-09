# Computer Use Fail-Safes & Resilience Guide

> **Reference only, not source of truth.** Plans and research; parts may describe features that are not built,
> deployed or verified. What is actually live: [AI_HANDOFF.md](AI_HANDOFF.md). Roadmap: [IDEAS.md](IDEAS.md) #27.

**Created:** 2026-10-07  
**Purpose:** Plain-English explanation of how autonomous computer use handles edge cases, stops loops, recovers from surprises, and keeps you in control.

---

## 1. What Can Go Wrong & What Happens

When an AI controls a real computer (moving the real mouse, typing on the real keyboard, and browsing websites), things can get messy. Below is exactly what can happen and how each fail-safe steps in:

```
┌───────────────────────────────────────┬────────────────────────────────────────────────────────┐
│ The Real-World Surprise               │ What The Fail-Safe Does                                │
├───────────────────────────────────────┼────────────────────────────────────────────────────────┤
│ 1. The bot gets stuck in a loop       │ Tripping the Circuit Breaker: Stops after 3 repeated   │
│    (clicking back and forth).         │ actions, forces a new strategy, or asks you.           │
├───────────────────────────────────────┼────────────────────────────────────────────────────────┤
│ 2. The bot clicks a button, but       │ No-Op Verification: Measures screen pixel change.      │
│    nothing happens (it was disabled). │ If 0% changed, tries keyboard shortcuts or alternative.│
├───────────────────────────────────────┼────────────────────────────────────────────────────────┤
│ 3. Windows is at 125% or 150% scale   │ Per-Monitor V2 DPI Shield: Forces Windows to use real  │
│    and the mouse clicks off-target.   │ physical hardware pixels, so clicks land dead-center.  │
├───────────────────────────────────────┼────────────────────────────────────────────────────────┤
│ 4. The browser window gets minimized  │ Window Guard: Checks if window is hidden or minimized, │
│    or pushed behind another window.   │ restores it automatically before touching the mouse.   │
├───────────────────────────────────────┼────────────────────────────────────────────────────────┤
│ 5. A link opens 10 new browser tabs   │ Tab Watchdog: Closes temporary popup tabs (`Ctrl+W`)   │
│    and uses up all your RAM.          │ and returns focus to the main workspace.               │
├───────────────────────────────────────┼────────────────────────────────────────────────────────┤
│ 6. A Cloudflare "Verify you are       │ Challenge Interceptor: Pauses immediately, sends a     │
│    human" or CAPTCHA appears.         │ loud phone alert with a button to Take Over in 3 sec.  │
└───────────────────────────────────────┴────────────────────────────────────────────────────────┘
```

---

## 2. In-Depth: How Each Fail-Safe Works

### 🔄 Fail-Safe 1: Repetition & Oscillation Circuit Breaker
* **What happens without it:** If an AI model decides to click a button, but the button requires a checkbox ticked first, an unassisted model can repeatedly click the same button 10 times until it runs out of steps.
* **What happens with it:**
  ```
  Step 1: Click "Next" -> Page didn't change
  Step 2: Click "Next" -> Page didn't change
  Step 3: Click "Next" -> 🚨 CIRCUIT BREAKER TRIPS!
  ```
  - The tool catches the repetition in a rolling 6-action window.
  - It intercepts the loop, injects an explicit warning into the AI's thoughts (*"You have repeated this action 3 times with no effect. Do NOT click 'Next'. Look around the page for required checkboxes or errors"*).
  - If the bot still cannot figure it out, it stops and asks you on your phone with an outlined picture.

---

### ⚡ Fail-Safe 2: No-Op Visual Verification ("Did it actually click?")
* **What happens without it:** Sometimes a website button looks clickable, but has a transparent loading mask or is grayed out. The bot moves the mouse, clicks, and immediately assumes the page is loading, leading to confusing errors 3 steps later.
* **What happens with it:**
  - The tool takes a micro-snapshot right before the click and right after the click.
  - It calculates the pixel difference ($\Delta$).
  - If the screen had **0.0% visual change** and the address bar didn't move:
    1. It knows the click didn't register.
    2. It attempts focusing the element and sending the physical `Enter` or `Space` key.
    3. It avoids false assumptions and keeps logs completely truthful.

---

### 🖥️ Fail-Safe 3: Per-Monitor V2 DPI Scaling Shield
* **What happens without it:** Laptops, 4K monitors, and external displays often use display scaling (like 125% or 150% zoom in Windows Settings). Under older automation libraries, Windows "virtualizes" coordinates: the screen reader says a button is at $(800, 400)$, but the physical mouse clicks at $(1000, 500)$, missing the button entirely!
* **What happens with it:**
  - At process launch, before any window is touched, the code calls:
    `SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)`
  - Windows disables coordinate scaling virtualization for the agent.
  - Accessibility coordinates and physical mouse pixels align with **1:1 laser precision**, regardless of what zoom level or monitor you use.

---

### 🪟 Fail-Safe 4: Minimized & Off-Screen Window Guard
* **What happens without it:** If you or Windows accidentally minimize the Edge window while the agent is running in the background, the mouse clicks blindly onto your desktop wallpaper or taskbar icons.
* **What happens with it:**
  - Before every single action, the tool checks:
    1. Is the window minimized (`IsIconic(hwnd)`)? If so, it calls `ShowWindow(hwnd, SW_RESTORE)` to bring it right back.
    2. Are the window coordinates valid on your actual screen (not pushed off-screen)?
    3. Is another application stealing focus? If so, it restores the task window to the front.

---

### 📑 Fail-Safe 5: Tab Accumulation & Leak Watchdog
* **What happens without it:** When clicking links that open in new tabs (`target="_blank"`) or third-party sign-in windows, Edge accumulates dozens of open tabs. Over time, this eats several gigabytes of RAM and slows down the machine.
* **What happens with it:**
  - The agent tracks how many tabs it opened.
  - When an authentication popup or secondary page finishes its job, the tool automatically sends `Ctrl+W` to close the transient tab.
  - It returns focus to the main tab, keeping Edge clean, fast, and using minimal memory.

---

### 🚨 Fail-Safe 6: Cloudflare & Error Interceptor
* **What happens without it:** The bot lands on a Cloudflare "Checking your browser before accessing..." screen, doesn't know what it is, and tries to click random text.
* **What happens with it:**
  - The tool automatically recognizes Cloudflare challenges, Turnstile checkboxes, and HTTP 502/404 error pages.
  - **For temporary errors:** It automatically reloads (`F5`) or navigates back (`Alt+Left`).
  - **For Cloudflare/CAPTCHA:** It pauses safely, takes a screenshot, and pushes a loud **"Needs You" alert** to your phone with a direct link to **Take Over**. You solve the check on your phone in 3 seconds, tap **Hand Back**, and the agent continues autonomously!

---

## 3. How You Stay in Complete Control

Everything follows the owner's core safety rules:

1. **You Approve Sensitive Actions:**
   It will **never** click `Submit`, `Send`, `Pay`, `Buy`, `Delete`, or `Publish` on its own. It always outlines the button in red, takes a screenshot, sends an alert to your phone, and waits for your tap.
2. **Immediate Kill-Switch:**
   Moving the physical mouse on your desk immediately disengages the bot. You can also press `Ctrl + Alt + End` at any time to halt all operations instantly.
3. **No Passwords Stored in Plaintext:**
   Passwords live encrypted in Windows Credential Manager. The AI model never sees your passwords, and they are never written to any file or log.
4. **Nothing Committed to Git:**
   All personal details, browser caches, run logs, and session files live strictly in `data/` on your machine, which is completely git-ignored.
## 4. External Review (Ziggy, 2026-10-07)

Reviewed the fail-safe design against the AgentComputerUse implementation. Two open questions for the builder:

### Q1: Kill-switch self-triggering (mouse-move disengage)
The doc states "moving the physical mouse on your desk immediately disengages the bot." The agent itself moves the cursor via pynput/ctypes — a naive movement listener cannot distinguish the agent's own synthetic moves from the owner's physical hand. Confirm the implementation uses one of:
- a flag set around synthetic moves (listener ignores moves while the flag is held),
- low-level input-source inspection (`LLMHF_INJECTED` on Windows hook events),
- or a dedicated "agent is driving" mutex.

Without one of these, the kill switch either never fires (if it ignores all movement during runs) or fires on the agent's own moves. This is the highest-priority item to verify — a kill switch that can't tell friend from foe is load-bearing safety theater.

### Q2: CAPTCHA pause when the phone bridge is down (Fail-Safe 6)
The Challenge Interceptor pauses and pushes a "Take Over" alert to the phone via the DeskAI bridge. If the bridge is unreachable (laptop asleep, network down, bridge crashed), "pause and wait" becomes "wait forever" with the run hanging mid-task. Recommend documenting and implementing a bounded wait: e.g., N-minute timeout → safe abort (close transient tabs, release the window, log state for resume). The abort path should be tested, not just the happy path.

### Verified as consistent with the repo
- Approval gates on Submit/Send/Pay/Buy/Delete/Publish (human-in-the-loop before irreversible actions).
- Passwords in Windows Credential Manager via DPAPI; model never sees secrets; nothing secret in logs.
- `data/` (profiles, credential index, recipes, logs) is gitignored — no personal data in the public repo.
- Enhancement phases 1–6 match landed commits in AgentComputerUse; Phase 7 (SoM visual fallback) is the remaining build.

---

### Builder Resolutions & Technical Design (Integrated in Phase 8)

#### Resolution to Q1: Kill-Switch Friend-vs-Foe Discrimination
To ensure the mouse listener never triggers on the agent's own moves while instantly disengaging when the user's hand touches the mouse:
1. **Low-Level Hook Inspection (`LLMHF_INJECTED`):** In the Windows low-level mouse hook (`WH_MOUSE_LL` / `MSLLHOOKSTRUCT`), Windows sets bit 0 (`LLMHF_INJECTED = 0x01`) on all synthetic mouse moves injected via `SendInput` or `ctypes`. Real physical movements from the mouse sensor report `flags & LLMHF_INJECTED == 0`.
2. **Synthetic Move Guard (`_agent_is_driving` context manager):** In `AgentComputerUse/hands.py`, all Bézier steps execute inside a context manager that sets an internal driving state. The mouse listener ignores moves while the agent is actively driving *and* `LLMHF_INJECTED` is true. Any physical mouse move ($\Delta > 15\text{px}$) immediately trips the safety kill-switch (<15ms).

#### Resolution to Q2: Bounded Watchdog for CAPTCHA & Phone Bridge
To eliminate the risk of the agent hanging indefinitely if the phone bridge is unreachable or the user is away:
1. **180-Second Bounded Timer:** When a challenge is detected, a strict 3-minute countdown begins.
2. **Safe Graceful Abort:** If the user does not take over within 3 minutes:
   - The agent writes an execution checkpoint to `data/runs/checkpoint_<timestamp>.json` with the current URL, screenshot, and form state.
   - It sends `Ctrl + W` to close any ephemeral popup tabs.
   - It releases window focus and exits the loop with code `ABORT_HANDOFF_TIMEOUT`.
   - It logs the exact point of pause so the user can resume with a single tap later.
3. **Automated Verification:** The bounded abort path is covered by automated unit tests in `tests/test_failsafes.py`.

