import json
from datetime import timedelta

from django.contrib import messages
from django.db.models import Avg, Count, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View

from apps.messaging.models import Conversation
from apps.orgs.mixins import AdminRequiredMixin, OrgMemberRequiredMixin

from . import services
from .forms import AssistantSettingsForm
from .help_articles import ARTICLES, BY_SLUG
from .models import AssistantLog, AssistantSettings
from .providers import PRESETS, ProviderError, chat, resolve


def _json(request):
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        data = None
    return data if isinstance(data, dict) else {}


class HelpCenterView(OrgMemberRequiredMixin, View):
    template_name = "assistant/help_center.html"

    def get(self, request):
        q = request.GET.get("q", "").strip()
        sections = {}
        for article in (services.search(q, limit=10) if q else ARTICLES):
            sections.setdefault(article.section, []).append(article)
        return render(request, self.template_name, {
            "sections": sections, "q": q, "ai": resolve(request.org),
            "help_enabled": AssistantSettings.for_org(request.org).help_enabled,
        })


class HelpArticleView(OrgMemberRequiredMixin, View):
    template_name = "assistant/help_article.html"

    def get(self, request, slug):
        article = BY_SLUG.get(slug)
        if article is None:
            raise Http404
        related = [a for a in services.search(" ".join(article.keywords[:4]), limit=4) if a.slug != slug][:3]
        return render(request, self.template_name, {"article": article, "related": related})


class HelpAskView(OrgMemberRequiredMixin, View):
    """POST JSON {question, history: [{role, content}], page} -> {answer, sources, used_ai, log}."""

    def post(self, request):
        data = _json(request)
        question = str(data.get("question", "")).strip()
        if not question:
            return JsonResponse({"error": "Type a question first."}, status=400)
        history = [m for m in data.get("history") or [] if isinstance(m, dict)]
        if not AssistantSettings.for_org(request.org).help_enabled:  # AI help is off: answer from the help center only
            articles = services.search(question)
            return JsonResponse({"answer": services.builtin_answer(articles), "used_ai": False, "log": None,
                                 "sources": [{"title": a.title, "url": services.article_url(a)} for a in articles]})
        try:
            answer = services.ask_help(org=request.org, membership=request.membership, question=question, history=history,
                                       page=str(data.get("page", ""))[:200])
        except services.LimitReached as exc:
            return JsonResponse({"error": str(exc)}, status=429)
        return JsonResponse({"answer": answer.text, "sources": answer.sources, "used_ai": answer.used_ai,
                             "notice": "The AI is unavailable right now, so this answer comes from the help center." if answer.error else "",
                             "log": answer.log_id})


class HelpFeedbackView(OrgMemberRequiredMixin, View):
    def post(self, request):
        data = _json(request)
        updated = AssistantLog.objects.filter(org=request.org, user=request.user, pk=data.get("log") or 0).update(
            helpful=bool(data.get("helpful")))
        return JsonResponse({"ok": bool(updated)})


class ReplyDraftView(OrgMemberRequiredMixin, View):
    """POST JSON {instruction?} for a conversation -> {draft}."""

    def post(self, request, pk):
        conversation = get_object_or_404(Conversation.objects.for_org(request.org).select_related("contact"), pk=pk)
        if not AssistantSettings.for_org(request.org).reply_drafts_enabled:
            return JsonResponse({"error": "AI reply drafts are switched off for this workspace."}, status=403)
        if not conversation.messages.exists():
            return JsonResponse({"error": "There's nothing to reply to yet."}, status=400)
        try:
            draft, log_id = services.draft_reply(org=request.org, membership=request.membership, conversation=conversation,
                                                 instruction=str(_json(request).get("instruction", "")).strip())
        except services.LimitReached as exc:
            return JsonResponse({"error": str(exc)}, status=429)
        except ProviderError as exc:
            return JsonResponse({"error": str(exc)}, status=503)
        return JsonResponse({"draft": draft, "log": log_id})


class AssistantSettingsView(AdminRequiredMixin, View):
    template_name = "assistant/settings.html"

    def form(self, request, data=None):
        platform = resolve_platform_label()
        return AssistantSettingsForm(data, instance=AssistantSettings.for_org(request.org), platform_label=platform)

    def render_page(self, request, form):
        since = timezone.now() - timedelta(days=30)
        logs = AssistantLog.objects.for_org(request.org).filter(created_at__gte=since)
        usage = logs.aggregate(total=Count("pk"), by_ai=Count("pk", filter=Q(used_ai=True)),
                               thumbs_up=Count("pk", filter=Q(helpful=True)), thumbs_down=Count("pk", filter=Q(helpful=False)),
                               avg_latency=Avg("latency_ms", filter=Q(used_ai=True)))
        return render(request, self.template_name, {
            "form": form, "current": resolve(request.org), "usage": usage,
            "recent_errors": logs.exclude(error="")[:5],
            "presets": {k: {"base_url": p.base_url, "model": p.model, "needs_key": p.needs_key, "note": p.note} for k, p in PRESETS.items()},
        })

    def get(self, request):
        return self.render_page(request, self.form(request))

    def post(self, request):
        if request.POST.get("action") == "test":
            config = resolve(request.org)
            if not config.is_ai:
                messages.info(request, "Using the built-in help center: nothing to test. Pick an AI provider to use AI answers.")
            else:
                try:
                    reply = chat(config, [{"role": "user", "content": "Reply with the single word: ready"}], max_tokens=20)
                    messages.success(request, f"{config.label} answered: “{reply[:80]}”.")
                except ProviderError as exc:
                    messages.error(request, f"Test failed: {exc}")
            return redirect("assistant:ai_settings")
        form = self.form(request, request.POST)
        if not form.is_valid():
            return self.render_page(request, form)
        form.save()
        messages.success(request, "AI assistant settings saved. Use “Test connection” to check them.")
        return redirect("assistant:ai_settings")


def resolve_platform_label():
    from django.conf import settings

    preset = PRESETS.get(settings.AI_PROVIDER)
    label = preset.label.split(":")[0] if preset else (settings.AI_PROVIDER or "Built-in help")
    model = settings.AI_MODEL or (preset.model if preset else "")
    return f"{label}{' · ' + model if model and settings.AI_PROVIDER != 'builtin' else ''}"
