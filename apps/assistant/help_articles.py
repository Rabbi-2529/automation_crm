"""The help center. The AI assistant answers from these articles, and without AI they're shown directly.

Keep them short and true to the app: menu paths as they appear in the sidebar, and who (role) can do what.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Article:
    slug: str
    title: str
    section: str
    body: str
    keywords: tuple = field(default_factory=tuple)
    url_name: str = ""  # the page it's about; opened from "Go to page"


ARTICLES = [
    Article("getting-around", "Getting around the CRM", "Basics", """
The sidebar has three groups: **CRM** (contacts, companies, deals, tasks, calendar, inbox, automations, reports), **Marketing** (segments, sequences, broadcasts, web forms, lead scoring) and **Workspace** (teams, members, tags, templates, pipelines and settings). Click a group name to open it; the group of the page you're on opens by itself.

The workspace switcher at the top left changes between workspaces you belong to. Your profile and password are under your initials at the top right.

Roles: **Agents** work with contacts, deals, tasks and messages. **Managers** can also delete records, import/export CSV and manage tags, pipelines, templates, web forms and lead scoring. **Admins** also manage members, custom fields, messaging channels, integrations, the AI assistant and workspace settings.
""", ("menu", "sidebar", "navigation", "role", "permission", "agent", "manager", "admin", "workspace", "switch"), "core:dashboard"),

    Article("contacts", "Adding and managing contacts", "CRM", """
Go to **CRM → Contacts → + New contact**. Fill in name, email and/or phone, company, owner, lifecycle stage and tags. The CRM warns you about a possible duplicate (same email or phone).

On a contact page you can add notes and files, log calls, send email / SMS / WhatsApp, create deals and tasks, and see the full timeline of everything that happened.

In the contact list, tick several contacts to **bulk** add a tag, change owner or lifecycle stage (managers can also bulk delete). Use the filters above the list to search by name, tag, owner, stage or source.
""", ("contact", "lead", "customer", "add", "create", "duplicate", "bulk", "tag", "owner", "timeline", "note", "file"), "contacts:contact_list"),

    Article("lifecycle", "Lifecycle stages", "CRM", """
Each contact has a lifecycle stage: Lead → Qualified → Opportunity → Customer (plus Churned and Other). It moves forward automatically: creating a deal makes a lead an **Opportunity**, and winning a deal makes them a **Customer**. Nothing ever moves a contact backwards automatically; you can change the stage by hand on the contact.
""", ("lifecycle", "stage", "lead", "qualified", "opportunity", "customer"), "contacts:contact_list"),

    Article("companies", "Companies", "CRM", """
**CRM → Companies** keeps organisations. Link contacts to a company on the contact form (or create a new company from there). A company page shows its contacts, deals and timeline.
""", ("company", "companies", "organisation", "organization", "account", "business"), "contacts:company_list"),

    Article("import-export", "Importing and exporting contacts (CSV)", "CRM", """
Managers: **CRM → Imports → New import**. Upload a CSV, match each column to a contact field (custom fields too), choose what happens to duplicates (skip, update or create), optionally set an owner and tags, then start. The import runs in the background and shows created / updated / skipped rows and errors.

To export, use **Export** on the contact list (managers); it exports what your filters show.
""", ("import", "csv", "excel", "upload", "export", "download", "spreadsheet", "bulk", "migrate"), "contacts:import_list"),

    Article("deals", "Deals and the pipeline board", "CRM", """
**CRM → Deals** shows a kanban board per pipeline. Click **+ New deal**, set title, value, contact/company, owner and expected close date. Drag a card to move it between stages; the column totals and the weighted forecast update.

Open a deal and click **Won** or **Lost** (pick a lost reason). Won/lost is a status, so a closed deal keeps its stage; you can **Reopen** it. Deals that sit too long in a stage are flagged as rotting.

Managers set up pipelines, stages, win probabilities and lost reasons under **Workspace → Pipelines**.
""", ("deal", "pipeline", "kanban", "board", "stage", "won", "lost", "reason", "forecast", "opportunity", "sale", "close"), "pipeline:deal_board"),

    Article("tasks", "Tasks, reminders and the calendar", "CRM", """
**CRM → Tasks → + New task**: title, type (to-do, call, email, meeting, follow-up), priority, due date/time, reminder, assignee and the related contact/company/deal. Reminders are emailed before the due time. The sidebar badge shows tasks due today (red when some are overdue).

**CRM → Calendar** shows tasks and deal close dates; drag to reschedule and click a day to add a task. To see your tasks in Google Calendar, Outlook or Apple Calendar use **Calendar → Sync to Google Calendar** and subscribe to your private link (one-way).
""", ("task", "todo", "to-do", "reminder", "due", "overdue", "calendar", "google calendar", "outlook", "meeting", "follow up", "schedule"), "tasks:task_list"),

    Article("inbox", "Email, SMS and WhatsApp inbox", "CRM", """
Send a message from a contact page (**Email**, **SMS** or **WhatsApp** buttons) or reply in **CRM → Inbox**. Replies come back into the same conversation; unknown senders can be saved as contacts. Filter by channel, unread, your contacts or archived.

Templates (**Workspace → Message templates**) fill in variables like `{{ contact.first_name }}`. Email opens and clicks are tracked. In the inbox, **Draft with AI** writes a reply suggestion you can edit before sending.

WhatsApp only allows free text within 24 hours of the contact's last message; after that use a WhatsApp template with an approved template name.
""", ("inbox", "email", "sms", "whatsapp", "message", "reply", "conversation", "template", "send", "open", "click", "tracking", "draft"), "messaging:inbox"),

    Article("calls", "Logging calls", "CRM", """
On a contact page click **Log call**: direction, outcome (connected, no answer, busy, voicemail, wrong number), duration and notes. The call appears on the timeline and counts as contacting the lead (for speed-to-lead and reports).
""", ("call", "phone", "log", "voicemail", "outcome"), "contacts:contact_list"),

    Article("automations", "Automations (workflows)", "CRM", """
**CRM → Automations → New workflow**. Pick a **trigger** (contact created, tag added, deal moved/won/lost, form submitted, Facebook lead, email/SMS/WhatsApp received, contact inactive, deal close date near, date field, speed-to-lead, incoming webhook…), optional **conditions**, then **actions** in order: send email/SMS/WhatsApp, create task, assign owner (incl. round robin), set stage, add/remove tag, add note, create/move deal, wait, continue only if, call a webhook, add to sequence, change lead score.

Save, then switch it **on**. Each run is logged with every step, so you can see why something did or didn't happen. Automated messages skip unsubscribed contacts and wait for the sending hours.
""", ("automation", "workflow", "trigger", "action", "condition", "wait", "round robin", "assign", "auto", "rule", "run"), "automation:workflow_list"),

    Article("reports", "Reports, targets and report emails", "CRM", """
**CRM → Reports** shows revenue won, win rate, average deal, sales cycle, pipeline and forecast, new leads, and charts (revenue over time, funnel, forecast, lost reasons, lead sources). Filter by period, pipeline and people. Each chart has a **Table** view.

**Leaderboard** compares reps (revenue, deals, activity, target progress); managers can export CSV. Managers set monthly/quarterly **Targets**. **Email me reports** sends you a daily/weekly/monthly pipeline digest (managers can also get a team summary).
""", ("report", "dashboard", "chart", "revenue", "win rate", "funnel", "forecast", "leaderboard", "target", "goal", "kpi", "digest", "export"), "reports:overview"),

    Article("segments", "Segments", "Marketing", """
**Marketing → Segments** are saved lists defined by conditions (lifecycle stage, tags, owner, source, score/grade, opt-outs, custom fields, days since activity…). They update by themselves and are used for broadcasts and in automation conditions.
""", ("segment", "list", "audience", "filter", "group", "condition"), "campaigns:segment_list"),

    Article("sequences", "Email and SMS sequences", "Marketing", """
**Marketing → Sequences**: a series of emails/SMS sent with delays (e.g. day 0, day 2, day 5). Add contacts by hand, from a segment or with an automation. A contact leaves automatically when they **reply** or unsubscribe. Steps wait for the sending hours.
""", ("sequence", "drip", "follow up", "nurture", "campaign", "series", "cadence"), "campaigns:sequence_list"),

    Article("broadcasts", "Broadcasts (bulk email / SMS)", "Marketing", """
**Marketing → Broadcasts → New broadcast**: pick email or SMS, a segment and the message, then send now or schedule. Unsubscribed contacts are skipped and every email has an unsubscribe link. The broadcast page shows sent, opened, clicked, failed and unsubscribed.
""", ("broadcast", "bulk", "newsletter", "mass", "blast", "campaign", "promotion", "schedule"), "campaigns:broadcast_list"),

    Article("web-forms", "Web forms", "Marketing", """
Managers: **Marketing → Web forms → New form**. Choose fields (custom fields too), consent text, owner, lifecycle stage and tags for new leads. Share the public link or embed the snippet on your website. Submissions create or update the contact (existing data is never overwritten, only blanks filled) and can start automations. Spam is blocked with hidden checks.
""", ("form", "web form", "website", "embed", "landing page", "capture", "signup", "spam"), "leads:form_list"),

    Article("lead-scoring", "Lead scoring and grades", "Marketing", """
Managers: **Marketing → Lead scoring**. Profile rules add points while a contact matches conditions (e.g. job title contains "Director"); behaviour rules add points each time something happens (opens an email, submits a form, replies, Facebook lead…). Scores turn into **Hot / Warm / Cold** grades by thresholds; a grade change can start an automation.
""", ("score", "scoring", "points", "grade", "hot", "warm", "cold", "qualify", "priority"), "leads:scoring"),

    Article("unsubscribe", "Unsubscribes, consent and quiet hours", "Marketing", """
Marketing emails include an unsubscribe link; replying **STOP** to SMS or WhatsApp opts the contact out of that channel (START opts back in). You can also change it on the contact page under *Communication preferences*.

Admins set **sending hours** under **Workspace → Messaging channels → Sending rules**: automated messages wait until the window opens. Messages you write yourself always go immediately.
""", ("unsubscribe", "opt out", "stop", "consent", "gdpr", "quiet hours", "sending hours", "spam"), "messaging:channel_settings"),

    Article("teams-members", "Teams and members", "Workspace", """
Admins invite people under **Workspace → Members** and pick their role (agent, manager, admin). **Workspace → Teams** groups people (with a team lead); reports can be filtered by team. Deactivating a member keeps their history.
""", ("member", "invite", "user", "staff", "team", "role", "add user", "colleague"), "orgs:members"),

    Article("custom-fields", "Custom fields and tags", "Workspace", """
Admins add fields for contacts, companies or deals under **Workspace → Custom fields** (text, number, date, dropdown, checkbox, URL, email). They show on forms, imports, segments, automations and the API.

Managers manage **Workspace → Tags** (name and colour). Tags can be added on records, in bulk, by imports, web forms and automations.
""", ("custom field", "field", "attribute", "tag", "label", "colour", "color", "property"), "contacts:custom_field_list"),

    Article("channels", "Setting up email, SMS and WhatsApp", "Workspace", """
Admins: **Workspace → Messaging channels**.
- **Outgoing email**: your SMTP server (e.g. Gmail with an app password) or the platform default.
- **Incoming email**: an IMAP mailbox checked every 2 minutes so replies land in the inbox.
- **SMS**: Twilio or any HTTP gateway (BulkSMSBD, SSL Wireless…).
- **WhatsApp**: WhatsApp Cloud API phone number ID, access token and app secret, plus the webhook URL and verify token shown on the tab.
Each tab has a **test** button.
""", ("smtp", "imap", "gmail", "sms gateway", "twilio", "bulksmsbd", "whatsapp setup", "setup", "configure", "settings", "channel"), "messaging:channel_settings"),

    Article("integrations", "API, webhooks and Facebook lead ads", "Workspace", """
Admins: **Workspace → Integrations**.
- **REST API**: create an API key (shown once) and see the docs at /api/docs/. Use it to connect your website, Zapier/Make or other systems.
- **Webhooks**: send chosen events (new contact, deal won, message received…) to another system's URL, signed and retried.
- **Facebook lead ads**: add the Meta app secret and your pages; new leads become contacts automatically and can start automations.
""", ("api", "api key", "integration", "zapier", "make", "webhook", "facebook", "meta", "lead ads", "instagram", "connect", "sync"), "integrations:overview"),

    Article("ai-assistant", "The AI assistant", "Workspace", """
Ask the **Help** button (bottom right) how to do something; it answers from this help center. In the inbox, **Draft with AI** suggests a reply you can edit; always read it before sending.

Admins choose the AI under **Workspace → AI assistant**: built-in help only, **Ollama** (free open-source models on your own server), **Groq** or **OpenRouter** (free tiers of open-source models), or any OpenAI-compatible API. It can be changed at any time. *About your business* gives the reply drafts your products, prices and tone.
""", ("ai", "assistant", "chatbot", "help", "ollama", "llama", "groq", "openrouter", "draft", "gpt", "model"), "assistant:ai_settings"),
]

BY_SLUG = {a.slug: a for a in ARTICLES}
