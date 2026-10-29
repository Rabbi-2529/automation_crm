/* ThingBD CRM help assistant: the floating Help panel and the Help page share this chat.
   Markup: an element with [data-assistant] holding [data-chat-log], form[data-chat-form] (textarea name=question)
   and data-ask-url / data-feedback-url. The conversation is kept for this browser tab (sessionStorage). */
(function () {
  const STORE = "crm.assistant.history";
  const csrf = () => (document.cookie.match(/csrftoken=([^;]+)/) || [])[1] || document.querySelector("[name=csrfmiddlewaretoken]")?.value || "";

  function escapeHtml(text) {
    return text.replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  }

  // Small, safe Markdown: escaped first, then bold, code, internal links and lists. External links are not linked.
  function render(markdown) {
    const inline = s => escapeHtml(s)
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/`([^`]+)`/g, '<code class="rounded bg-slate-100 px-1 text-xs">$1</code>')
      .replace(/\[([^\]]+)\]\((\/[^)\s]*)\)/g, '<a class="text-violet-700 underline" href="$2">$1</a>');
    const out = [];
    let list = null;
    for (const raw of markdown.split("\n")) {
      const line = raw.trim();
      const bullet = line.match(/^[-*•]\s+(.*)/), numbered = line.match(/^\d+[.)]\s+(.*)/);
      if (bullet || numbered) {
        const tag = bullet ? "ul" : "ol";
        if (!list || list.tag !== tag) { if (list) out.push(`</${list.tag}>`); list = {tag}; out.push(`<${tag} class="${tag === "ul" ? "list-disc" : "list-decimal"} space-y-1 pl-5">`); }
        out.push(`<li>${inline((bullet || numbered)[1])}</li>`);
        continue;
      }
      if (list) { out.push(`</${list.tag}>`); list = null; }
      if (line) out.push(`<p>${inline(line.replace(/^#+\s*/, ""))}</p>`);
    }
    if (list) out.push(`</${list.tag}>`);
    return out.join("");
  }

  function load() { try { return JSON.parse(sessionStorage.getItem(STORE)) || []; } catch (e) { return []; } }
  function save(history) { try { sessionStorage.setItem(STORE, JSON.stringify(history.slice(-20))); } catch (e) {} }

  function init(root) {
    const log = root.querySelector("[data-chat-log]"), form = root.querySelector("[data-chat-form]");
    const input = form.querySelector("textarea"), button = form.querySelector("button[type=submit]");
    let history = load();

    function bubble(role, content, extra = {}) {
      const wrap = document.createElement("div");
      wrap.className = role === "user" ? "ml-auto max-w-[85%] rounded-xl bg-violet-600 px-3 py-2 text-sm text-white"
                                       : "max-w-[92%] space-y-2 rounded-xl bg-white px-3 py-2 text-sm text-slate-700 ring-1 ring-slate-200";
      wrap.innerHTML = role === "user" ? `<p class="whitespace-pre-line">${escapeHtml(content)}</p>` : render(content);
      if (extra.notice) wrap.insertAdjacentHTML("beforeend", `<p class="text-xs text-amber-700">${escapeHtml(extra.notice)}</p>`);
      if (extra.sources?.length) {
        wrap.insertAdjacentHTML("beforeend", '<p class="border-t border-slate-100 pt-2 text-xs text-slate-500">Help articles: ' +
          extra.sources.map(s => `<a class="text-violet-700 hover:underline" href="${escapeHtml(s.url)}">${escapeHtml(s.title)}</a>`).join(" · ") + "</p>");
      }
      if (extra.log) {
        const vote = document.createElement("p");
        vote.className = "flex items-center gap-2 text-xs text-slate-400";
        vote.innerHTML = `${extra.used_ai ? "AI answer · may be wrong" : "From the help center"} · Helpful? <button data-v="1" class="hover:text-emerald-600">👍</button><button data-v="0" class="hover:text-red-600">👎</button>`;
        vote.addEventListener("click", async e => {
          const v = e.target.dataset.v;
          if (v === undefined) return;
          await fetch(root.dataset.feedbackUrl, {method: "POST", headers: {"Content-Type": "application/json", "X-CSRFToken": csrf()},
                                                 body: JSON.stringify({log: extra.log, helpful: v === "1"})});
          vote.textContent = "Thanks for the feedback.";
        });
        wrap.appendChild(vote);
      }
      log.appendChild(wrap);
      log.scrollTop = log.scrollHeight;
      return wrap;
    }

    log.querySelectorAll("[data-generated]").forEach(n => n.remove());
    history.forEach(m => bubble(m.role, m.content, m.extra || {}));
    root.querySelectorAll("[data-suggest]").forEach(b => b.addEventListener("click", () => { input.value = b.textContent.trim(); form.requestSubmit(); }));
    root.querySelector("[data-chat-clear]")?.addEventListener("click", () => { history = []; save(history); log.querySelectorAll(":scope > div").forEach(n => n.remove()); });
    input.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); } });

    form.addEventListener("submit", async e => {
      e.preventDefault();
      const question = input.value.trim();
      if (!question || button.disabled) return;
      bubble("user", question);
      input.value = "";
      button.disabled = true;
      const thinking = bubble("assistant", "Thinking…");
      thinking.classList.add("animate-pulse");
      try {
        const response = await fetch(root.dataset.askUrl, {method: "POST", headers: {"Content-Type": "application/json", "X-CSRFToken": csrf()},
          body: JSON.stringify({question, page: document.title, history: history.map(({role, content}) => ({role, content}))})});
        const data = await response.json();
        thinking.remove();
        if (!response.ok) { bubble("assistant", data.error || "Something went wrong."); return; }
        const extra = {sources: data.sources, log: data.log, used_ai: data.used_ai, notice: data.notice};
        bubble("assistant", data.answer, extra);
        history.push({role: "user", content: question}, {role: "assistant", content: data.answer, extra});
        save(history);
      } catch (err) {
        thinking.remove();
        bubble("assistant", "Couldn't reach the server. Check your connection and try again.");
      } finally {
        button.disabled = false;
        input.focus();
      }
    });
  }

  document.addEventListener("DOMContentLoaded", () => document.querySelectorAll("[data-assistant]").forEach(init));
  window.crmAssistantRender = render;
})();
