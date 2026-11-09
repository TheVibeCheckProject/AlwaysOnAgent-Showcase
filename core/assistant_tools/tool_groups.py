"""
Tool groups for the chat assistant.

Moved into core/assistant_tools/ on 2026-11-04 (was core/tool_groups.py).

Split out of core/assistant_tools.py §2 on 2026-11-03. The tools in sets that
travel together: every reply gets "core"; the other groups load only when the
message is about them. Imported by core/assistant_tools.py and by the routing
mixin; assistant_tools.py re-exports them all.
"""


# Tool groups: a reply gets "core" plus the groups the conversation is about, not all 30 tools. Their
# descriptions were ~4,000 tokens resent on every call and every tool round; with memory and history that went
# past free-tier request limits (Groq: "413 Payload Too Large", 2026-09-30). more_tools adds a group mid-reply
# when the words didn't give it away.
TOOL_GROUPS: dict[str, list[str]] = {
    # connect_google moved to the Google groups (2026-10-27): it's only useful there, and core goes with every call
    "core": ["search_memory", "forget_memory", "ask_user", "propose_task", "get_system_status", "list_tasks",
             "get_task", "cancel_task", "read_logs", "background_status", "look_into"],
    "web": ["get_weather", "web_search", "web_research", "get_news", "fetch_webpage", "research_ai_opportunities"],
    "videos": ["render_video", "post_video_to_tiktok", "list_videos"],
    "stats": ["post_pins", "make_pins", "approve_pins", "reject_pins", "weekly_report", "my_numbers"],
    "projects": ["start_project_job", "propose_project", "list_project_files", "read_project_file",
                 "offer_go_live", "preview_task_diff"],
    "schedules": ["create_schedule", "list_schedules", "change_schedule", "proactive_settings"],
    # DeskAI's Today screen (core/today.py, 2026-10-28): its recipe and the weather town
    "today": ["today_settings"],
    "mail": ["gmail_important", "gmail_search", "gmail_read", "gmail_unread", "subscriptions", "propose_email",
             "contacts_search", "zoho_mail", "zoho_mail_read", "connect_zoho", "connect_google"],
    "calendar": ["calendar_events", "propose_event", "contacts_search", "connect_google"],
    # Texts and calls: the phone is the hands (DeskAI opens Messages or the dialer, filled in; the user sends)
    "phone": ["propose_text", "propose_call", "contacts_search"],
    # The owner's own lists (Google Tasks via Connectors): adds and check-offs happen right away (owner's choice)
    "lists": ["list_items", "add_to_list", "check_off"],
    "drive": ["drive_search", "drive_read", "connect_google"],
    "files": ["read_attachment", "recent_image_prompts", "mini_files"],
    "canvas": ["canvas_write", "canvas_edit", "canvas_revert", "canvas_list", "canvas_publish"],
    "computer": ["computer_task", "computer_screenshots"],
    "watches": ["watch_mail", "watch_for", "list_watches", "change_watch", "stop_watch"],
    # Offered only while the weekly memory check waits for an answer (tool_specs), never by words or more_tools
    "memory_check": ["memory_check"],
}
# more_tools' menu, one short label each: it goes with every call (2026-10-27: the long labels made more_tools the
# biggest tool, 450 tokens; the router has its own descriptions in ROUTER_SYSTEM_PROMPT)
GROUP_ABOUT = {
    "web": "weather, web search, news, web pages",
    "watches": "watch mail, sites, news",
    "videos": "render or post videos (TikTok)",
    "stats": "Pinterest pins, numbers report",
    "projects": "code changes (AgentWork), project files",
    "schedules": "schedules, reminders, your proactive messages",
    "today": "the Today screen, weather town",
    "mail": "Gmail, Zoho Mail, sending email, contacts",
    "calendar": "Google Calendar, contacts",
    "drive": "Google Drive files",
    "lists": "the user's lists and to-dos",
    "phone": "texting or calling someone",
    "files": "files on the Mini, attachments, /image prompts",
    "canvas": "drafts to write and refine",
    "computer": "clicking through a website or app on the PC",
}
# How to use each group's tools, sent only with that group (AssistantTools.guides, and more_tools when it loads one).
# Until 2026-10-27 all of this was in the persona and went with every message (docs/AUDIT_2026-10-27.md T1).
GROUP_GUIDES = {
    "web": "research_ai_opportunities searches for free AI credits, developer previews, free tiers and hackathons "
           "fitting their building stack, saving digests to canvas and memory.",
    "videos": "render_video makes a affirmation video (about 6 minutes; the rainy window about 15), with their quote if they gave one; the user "
              "sees a live card and a notification. post_video_to_tiktok shows a card they post with one tap on Run "
              "('latest' = the newest): you never post yourself, so say it's ready for their tap, not that it's "
              "posted. list_videos shows the rendered videos.",
    "stats": "post_pins shows a card they post with one tap: you never post yourself. make_pins / approve_pins / "
             "reject_pins: NEW Pinterest pins from the website's messages, as drafts with a preview sheet; nothing "
             "joins the posting queue until the user approves ('approve all', 'approve 1, 3'). my_numbers: your own "
             "week in chat, counted (closing questions, praise, corrections...); read it when asked about yourself.",
    "projects": "start_project_job works on one of their projects (code changes, fixes, features) via AgentWork, in "
                "a fresh copy on its own branch ('alwaysonagent' is your own code). Nothing goes live without the "
                "user's review. A finished change to YOU or to the website goes live only when the user taps its "
                "chip on the job's card (you, tested with automatic rollback; the website, built and tested, then "
                "pushed): when they want it live, use offer_go_live to show that card. You never put anything live "
                "yourself, and other projects stay on their branch. Ask which project first if it isn't clear. "
                "propose_project: for a repo that isn't an AgentWork project yet, get its https git link and a "
                "one-line description (ask for what's missing), then propose adding it; the user taps Add. "
                "list_project_files / read_project_file read your own code (never .env or secrets).",
    "schedules": "create_schedule makes things happen by themselves at set times: a video (posted to TikTok too if "
                 "they asked for TikTok), a reminder pushed to their phone, or a background task. list_schedules and "
                 "change_schedule (pause, resume, retime, delete) manage them; they're also in DeskAI's Schedules "
                 "screen. Confirm what you set up in plain words, with the next run time. You also reach out first "
                 "(brief, notices, suggestions, questions, the Sunday growth review with up to 3 suggestions: do or "
                 "propose one the usual way when asked). If they want fewer messages, a different brief time, quiet "
                 "hours or no questions, use proactive_settings; 'less of this kind' is a preference memory keeps. "
                 "Messages that aren't urgent wait until the user is around; the brief comes when they show up "
                 "(brief time 'auto'). proactive_settings also follows YouTube channels: their new videos are told.",
    "mail": "gmail_search and gmail_read search and read their Gmail; gmail_unread gives exact unread counts; "
            "subscriptions lists paid services and mailing lists found there. propose_email sends or drafts an email "
            "(or a reply) only through a card the user taps; you never delete or change their mail. contacts_search "
            "finds an address. Zoho Mail (wecare@example-site.com, the website's mailbox): zoho_mail shows "
            "unread counts, the newest or a search; zoho_mail_read reads one; reading only; connect_zoho has the "
            "steps to connect it. Mail without saying which: check Gmail, and mention Zoho when it fits (Pinterest "
            "and the website's mail go there). An email's date is when it was sent, not when what it describes "
            "happens; 'tomorrow' in an email means the day after it was sent. If Google isn't approved yet, "
            "connect_google gives a link to (re)connect it.",
    "calendar": "calendar_events reads their calendar; propose_event adds an event through a card they tap. If Google "
                "isn't approved yet, connect_google gives a link to (re)connect it.",
    "drive": "drive_search and drive_read find and read their Drive files. If Google isn't approved yet, "
             "connect_google gives a link to (re)connect it.",
    "lists": "list_items, add_to_list and check_off keep the user's own lists and to-dos (Google Tasks: shopping, "
             "things to do, with due dates). Adding and checking off happen right away, no card; say what you did.",
    "phone": "propose_text drafts a text and propose_call a call (a contact's name or a number); their phone opens "
             "Messages or the dialer filled in, and they send or call themselves. You never send a text yourself.",
    "files": "Images: the user makes them with /image <prompt> (--style <name>, --raw). Their image prompts "
             "are NOT in your long-term memory (their choice); recent_image_prompts shows the last ones for a day. "
             "For 'enhance my last prompt', read it there, write the improved prompt, and give it back ready to send "
             "as /image. mini_files looks at the PC's own files: find (by name; 'videos', 'screenshots' narrow the "
             "kind), list a folder, read text, show media. To show a file, put the line show returns in your reply "
             "exactly as it is; never write a file link or path yourself. It only looks: it can't move, change or "
             "delete anything.",
    "canvas": "For anything the user will read and refine with you (a guide, script, captions, plan, email) or a "
              "website page to preview, use canvas_write, then canvas_edit for each change and canvas_revert to go "
              "back. A writer does the text; you describe it briefly, never paste the draft into chat. "
              "canvas_publish hands a finished draft to AgentWork, only when they say to publish.",
    "computer": "computer_task proposes a task in which AgentComputerUse uses the PC's real mouse and keyboard, in "
                "Edge and in apps. Nothing runs until the user taps Run. Risky steps (submit, send, sign up) and "
                "anything it's unsure about come to their phone as a 'Needs you' alert with a screenshot. It never "
                "solves CAPTCHAs. Every run saves a screenshot per step: computer_screenshots shows them in the chat "
                "(copy its image lines into your reply as they are). Never guess where they are.",
    "watches": "Whatever the user asks you to watch, make a watch for it; never say you can't watch something. Email: "
               "watch_mail (a sender, subject or words in Gmail and/or Zoho, or every new inbox email with "
               "newsletters skipped; with a deadline and a note it's 'if X hasn't replied by Friday, remind me'). "
               "Anything else: watch_for. A web page (a product's stock, a court case's docket page, a price, a "
               "site's news page): kind 'page'; a topic, person or news: kind 'search'; something on the calendar: "
               "kind 'calendar'. A court case is its public docket page (look its address up on the web first). A "
               "busy page (a news front page) changes all the time: give words to look for, an ask, or watch a "
               "search instead. list_watches, "
               "change_watch (narrow or change one in place) and stop_watch manage them. A watch checks only what "
               "its tool result says: never promise anything else. Never say a watch was made, changed, muted or "
               "stopped unless the tool did it.",
}
GROUP_WORDS = {
    # "today" alone is everyday speech: only the screen, its recipe or feed, or the weather town (2026-10-28: "use
    # Springfield for my weather in the Today screen" got a weather report and nothing saved)
    "today": r"\btoday (screen|page|tab|feed|edition)|\b(my|the|on|in|to) today\b(?! (is|was|i|we|at|it)\b)|"
             r"\brecipe\b|\bmy feed\b|weather (town|location|city)|(for|as) (my|the) weather|my weather\b",
    "watches": r"follow(-| )?up|hasn'?t (replied|written|answered|gotten back)|haven'?t heard|"
               r"no (reply|answer|response) (by|from)|\bif .{1,40} (doesn'?t|don'?t) (reply|answer|respond)|"
               r"\bwatch(es|ing)?\b|\b(un)?mute[ds]?\b|\bspam\b|\bignore\b|only (show|tell|alert|notify|ping)|"
               r"let me know (when|if|whenever)|tell me (when|if|whenever)|notify me|alert me|"
               r"ping me|heads up (when|if)|keep an eye|whenever|\bmonitor|\bkeep tabs|\btrack (this|that|the|it|when)|"
               r"(back )?in stock|price (drops?|goes)",
    "web": r"weather|\bwea\w*r\b|forecast|rain|temperature|news|search|serch|research|investigate|deep dive|documentation|look ?up|google|https?://|www\.|\.com\b|latest|price|"
           r"score|who (is|was|won)|what is|when (is|does)|how much|near me|time is it in|time in [a-z]|right now",
    "videos": r"(?:make|create|render|post|generate|produce|new)\s+video|\bvideos\b|tiktok|render|clip|reel|affirmation|quote|voice ?over",
    "stats": r"\bpins?\b|pinterest|\bapprove\b|\breject\b|\bdrafts?\b|report|numbers|stats|analytics|views|followers|visits|traffic|subscribers|"
             r"how are we doing|how('s| is) (it|the site) (going|doing)|self-?check|your (own )?(numbers|stats|scores?)|"
             r"how did you do",
    "projects": r"project|repo|\bcode\b|website|\bsite\b|\bbug|\bfix|feature|agentwork|branch|commit|deploy|"
                r"\bpage\b|css|html|script|source|\.py\b|\bappl(y|ied)\b|update yourself|self-update|\bdiff\b|\bpatch\b|\binspect\b|"
                r"push (it |this |that )?(to the )?live|go live|live site",
    "schedules": r"schedul|every (day|morning|evening|night|week|month|hour|mon|tue|wed|thu|fri|sat|sun)|daily|"
                 r"weekly|monthly|remind|tomorrow|tonight|\bat \d|\d ?(am|pm)\b|o'clock|recurring|each (day|week)|"
                 r"\bbrief|quiet hours|proactive|questions?\b|notifications?|stop (asking|messaging)|"
                 r"\b(un)?follow\b.{0,40}(youtube|channel)|youtube channel",
    "calendar": r"calendar|appointment|meeting|\bevents?\b|agenda|\bbusy\b|free (on|at|this|next)|what'?s on (my|the|for)|schedule (a|an|my) (meeting|appointment|call)|book (a|an)",
    "lists": r"\blists?\b|to-?dos?\b|shopping|grocer|\bcheck (it |that )?off\b|cross (it |that )?off|"
             r"add .{1,40} to (my|the)\b|remind me to buy|need to (buy|get|pick up)|on my plate|done with",
    "phone": r"\btext(s|ing|ed)?\b|\bsms\b|\bcall(ing)?\b|\bdial\b|\bring (him|her|them|my)|"
             r"message (my|him|her|them)\b|send (him|her|them|my \w+) a message|let (him|her|them|my \w+) know",
    "drive": r"google drive|\bdrive\b|google docs?|\bdocs?\b|spreadsheet|\bsheets?\b|slides|my files?\b",
    "mail": r"e-?mails?|gmail|zoho|inbox|\bmail\b|subscri|unsubscribe|newsletter|receipt|\bbills?\b|invoice|renew|\breply\b|\bcontacts?\b|phone number|"
            r"messages? from",
    "files": r"attach|\batt-|document|\bpdf\b|\bimage|photo|picture|screenshot|prompt|"
             r"\bfiles? (on|in|from)\b|\bfolders?\b|my (desktop|downloads|documents|music|pictures)\b|"
             r"\.(mp4|mov|wav|mp3|m4a|png|jpe?g)\b|\b(mp4|wav|mp3)s?\b|on the (mini|pc)\b|audio file|sound file",
    "canvas": r"\bdraft|canvas|\bguide\b|\bwrite (me |up )?(a|an|the|this)\b|script|caption|\bpreview|mock ?up|"
              r"how (will|would) it look|publish|blog post|article|newsletter|outline",
    "computer": r"\b(use|on) (the|my) (computer|pc|mini)\b|computer task|agentcomputeruse|fill (it |this )?(out|in)|"
                r"screenshots?|screen ?shots?|"
                r"\bform\b|apply (for|to)|application|sign (me )?up|giveaway|sweepstake|enter (me |the |a |this )|"
                r"go (to|on) (the |that )?(site|website)|click (through|on)|browse to|job (posting|listing)s?|"
                r"log ?in to|on (the|their) (site|website)",
}

# 5-10 short example requests per group to match requests by meaning (embeddings)
GROUP_EXAMPLES: dict[str, list[str]] = {
    "lists": [
        "add milk to the shopping list",
        "what's on my to-do list",
        "put 'mail the forms' on my list for Friday",
        "check off eggs",
        "I need to buy batteries",
        "what do I still have to do this week",
    ],
    "phone": [
        "text my brother that I'll be late",
        "send a text to mom saying happy birthday",
        "call the doctor's office",
        "message Sam that I'm on my way",
        "can you text him I'm outside",
        "let my wife know I'm leaving work",
    ],
    "web": [
        "what's the weather forecast for today",
        "search the web for the latest developments",
        "what are the top news headlines right now",
        "can you read and summarize this website article",
        "what is the capital city of Australia",
        "look up recent stock market and crypto prices",
        "browse this link and explain what it says",
        "who won the sports championship yesterday",
    ],
    "videos": [
        "make a new video for TikTok",
        "render a clip with an inspirational quote",
        "post this reel to our TikTok account",
        "generate a fresh affirmation video",
        "show me the list of rendered videos",
        "render a video using the latest quote",
        "create a video post for social media",
    ],
    "stats": [
        "how are our stats and analytics doing",
        "post new pins to Pinterest",
        "give me the weekly performance report",
        "how many visitors and page views did we get",
        "check the site numbers and subscriber count",
        "how many affirmation cards were sent this week",
        "show me our follower growth and metrics",
        "how are the site numbers doing this week",
        "can you see your own numbers",
        "how did you do in chat this week",
    ],
    "projects": [
        "fix the bug in the website layout",
        "start an AgentWork job to add a new page",
        "apply the self-update to yourself",
        "check the code in the repository",
        "what files are in the project folder",
        "inspect the python script for errors",
        "make a code change to the site",
        "suggest adding a repo to AgentWork",
    ],
    "today": [
        "use Springfield, IL for my weather",
        "show more golf on my Today screen",
        "no more crypto stuff in Today",
        "hide the finished tasks tile on the today screen",
        "what's in my Today recipe",
        "only 6 cards on Today",
    ],
    "schedules": [
        "remind me tomorrow morning at 9am to call the doctor",
        "set up a daily reminder to take my vitamins",
        "what scheduled reminders do I have active",
        "cancel the reminder scheduled for 8pm",
        "change the time of my weekly reminder",
        "schedule a recurring task every weekday",
        "alert me tonight at 10pm",
        "send my morning brief at 7 instead",
        "stop asking me questions for a while",
        "set quiet hours from 11pm",
    ],
    "calendar": [
        "what's on my calendar tomorrow",
        "am I free friday afternoon",
        "add a dentist appointment on thursday at 3",
        "when is my next meeting",
    ],
    "drive": [
        "find my resume in google drive",
        "what does the budget spreadsheet say",
        "open the notes doc I wrote last week",
    ],
    "mail": [
        "what's in my inbox",
        "reply to that email and say thanks",
        "send sam an email saying I'm running late",
        "did the bank email me anything today",
        "find the email about my flight booking",
        "am I subscribed to any paid services",
        "unsubscribe me from that newsletter",
        "read the latest message from my landlord",
        "check for any new receipts or invoices in Gmail",
        "anything from my landlord",
        "do I have any important unread emails",
    ],
    "files": [
        "read the attached PDF document",
        "summarize the attachment I sent you",
        "what prompt did we use to generate that image",
        "inspect the screenshot or photo attached",
        "show me recent image generation prompts",
        "extract the text from this attached file",
        "what did I draw earlier",
    ],
    "canvas": [
        "draft this week's guide about grief cards",
        "write me a TikTok script for tomorrow",
        "make the intro of the draft punchier",
        "show me how the new page will look",
        "go back to the previous version of the draft",
        "write an email to the venue about the deposit",
        "publish the guide to the website",
        "what drafts do we have going",
    ],
    "computer": [
        "go on the county website and find when the office is open",
        "fill out this job application for me",
        "sign me up for that free giveaway",
        "use the computer to check the price on their site",
        "find three jobs near me and put them in a spreadsheet",
        "log in to the site and look up my order status",
        "click through the store's site and find the return policy",
    ],
}

# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════

__all__ = ['GROUP_ABOUT', 'GROUP_EXAMPLES', 'GROUP_GUIDES', 'GROUP_WORDS', 'TOOL_GROUPS']
