# Canvas: drafts written and refined together

A canvas is a draft the owner and the agent work on over several turns, without the chat filling up with wall
after wall of revisions. There are two kinds:

- **document**: a guide, script, caption set, plan or email, shown as a clean readable page;
- **page**: a website page or change, previewed the way it will look on example-site.com (the site's
  live stylesheets, its `<main class="blog-post">` structure).

The owner picked these two uses (drafting together, previews before going live). Mini apps were declined: the
chat already does what they would do. A live stats dashboard can come later as a tool.

## Where things happen

| Piece | Home |
|---|---|
| Drafts, versions, the writer, the two page templates | `core/canvas.py` (tables `canvas_docs`, `canvas_versions`) |
| Chat tools: `canvas_write`, `canvas_edit`, `canvas_revert`, `canvas_list`, `canvas_publish` (group **canvas**) | `core/assistant_tools/` |
| `GET /api/canvas`, `GET /api/canvas/{id}`, `GET /api/canvas/{id}/preview` | `interfaces/hud/canvas_api.py` |
| The chat reply's `canvas` field (`{id, title, kind, version}`), so the app shows a card | `interfaces/hud/chat.py` |
| The canvas screen (one web view for both kinds, plus a version list) | DeskAI |
| Publishing | AgentWork (a separate tool), on a branch, reviewed as usual |

## How a turn goes (and why it's cheap)

1. "Draft this week's guide about grief cards": the chat model calls `canvas_write(title, kind, instruction)`.
2. **A dedicated writing call** gets only the title and instruction (for a page, also part of a real guide from
   the live site as a style reference, cached for a day). It has **no conversation and no memory**, and returns
   an HTML fragment.
3. The chat model hears only an outline: "Canvas #3 "Grief cards guide" (document), version 1 of 1: 640 words;
   sections: …". It never carries or repeats the draft, and the app shows the card.
4. "Make the intro punchier": `canvas_edit(id, instruction)` sends the current draft and that one instruction
   to the writer, and saves version 2. `canvas_revert` makes an old version the newest again; history only grows.
5. "Publish it" (the owner's own words are required): `canvas_publish(id, project, notes)` queues an AgentWork
   job with the draft's HTML. It builds on a branch, and nothing goes live without review.
6. For the **website** project, the finished job's card shows **🚀 Push live**. The owner's tap builds and tests the
   site with the change, then pushes `main`, which deploys it (TOOLS.md, "Going live"). In chat, "push it to the
   live site" shows that card.

Content is always an **HTML fragment** (the site's own format), so previews and publishing need no conversion and
no Markdown library.

## Safety

Drafts are written by an AI, so a preview may show things but never run anything:
- the writer's output is cleaned (no `<script>`, `<style>`, `<iframe>`, event handlers or `javascript:` links);
- `/preview` is served with a Content-Security-Policy: `default-src 'none'; script-src 'none'; … sandbox`. Only
  the site's stylesheets, Google fonts and images may load, and the page gets an isolated origin that can't
  reach the agent's API;
- DeskAI loads it with the token in a header, never in the URL.

## Don't undo

- The chat model never gets the full draft in a tool result.
- The writer call carries no conversation or memory.
- Scripts stay blocked in previews.
- Publishing needs the owner's word and goes through AgentWork.
