// Workflow builder: renders trigger / conditions / actions editors from the server's schema and posts the
// whole definition as JSON. The server re-validates everything (apps/automation/views.py::clean_definition).
(function () {
  "use strict";

  const schema = JSON.parse(document.getElementById("builder-schema").textContent);
  const state = JSON.parse(document.getElementById("builder-definition").textContent);
  state.trigger_config = state.trigger_config || {};
  state.conditions = state.conditions || { match: "all", rules: [] };
  state.conditions.rules = state.conditions.rules || [];
  state.actions = state.actions || [];

  const triggers = Object.fromEntries(schema.triggers.map(t => [t.key, t]));
  const actions = Object.fromEntries(schema.actions.map(a => [a.key, a]));
  const condFields = Object.fromEntries(schema.conditionFields.map(f => [f.key, f]));
  const OWNER = "__owner__";

  // Tiny DOM helper: text always goes through textContent, never innerHTML.
  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === undefined || value === null || value === false) continue;
      if (key === "class") el.className = value;
      else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
      else if (key === "value") el.value = value;
      else el.setAttribute(key, value === true ? "" : value);
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      el.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return el;
  }

  function options(select, items, selected, blank) {
    const chosen = new Set([].concat(selected ?? []).map(String));
    if (blank !== undefined) select.append(h("option", { value: "" }, blank));
    for (const item of items) {
      if (item.children) {
        const group = h("optgroup", { label: item.text });
        item.children.forEach(child => group.append(h("option", { value: child.id, selected: chosen.has(String(child.id)) }, child.text)));
        select.append(group);
      } else {
        select.append(h("option", { value: item.id, selected: chosen.has(String(item.id)) }, item.text));
      }
    }
    return select;
  }

  // ---------- Config fields ----------
  function fieldInput(spec, config, onStructuralChange) {
    if (config[spec.name] === undefined && spec.default !== null && spec.default !== undefined) config[spec.name] = spec.default;
    const value = config[spec.name];
    const set = v => { config[spec.name] = v; };
    const id = `f-${Math.random().toString(36).slice(2, 9)}`;
    let input;

    switch (spec.type) {
      case "textarea":
        input = h("textarea", { id, rows: 5, oninput: e => set(e.target.value) });
        input.value = value ?? "";
        break;
      case "number":
        input = h("input", { id, type: "number", min: 0, step: "any", value: value ?? "", oninput: e => set(e.target.value) });
        break;
      case "integer":
        input = h("input", { id, type: "number", step: 1, value: value ?? "", oninput: e => set(e.target.value) });
        break;
      case "checkbox":
        input = h("input", { id, type: "checkbox", checked: !!value, onchange: e => set(e.target.checked) });
        return h("label", { class: "flex items-center gap-2 text-sm font-medium text-slate-700" }, input, spec.label);
      case "conditions":
        config[spec.name] = config[spec.name] || { match: "all", rules: [] };
        return h("div", {}, h("p", { class: "text-sm font-medium text-slate-700" }, spec.label),
          conditionsEditor(config[spec.name], onStructuralChange));
      case "select":
        input = options(h("select", { id }), spec.options, value, spec.required ? undefined : "—");
        break;
      case "user": {
        const users = (spec.allow_owner ? [{ id: OWNER, text: "Record owner" }] : []).concat(schema.choices.user);
        input = options(h("select", { id }), users, value, spec.required ? "Pick someone" : "—");
        break;
      }
      case "users":
        input = options(h("select", { id, multiple: true, "data-placeholder": "Pick people" }), schema.choices.user, value || []);
        input.addEventListener("change", () => set([...input.selectedOptions].map(o => o.value)));
        break;
      case "tag": case "lifecycle": case "pipeline": case "stage": case "email_template": case "sms_template":
      case "webform": case "sequence": case "date_field":
        input = options(h("select", { id }), schema.choices[spec.type] || [], value, spec.required ? `Pick a ${spec.label.toLowerCase()}` : "—");
        break;
      default:
        input = h("input", { id, type: spec.type === "url" ? "url" : "text", value: value ?? "", oninput: e => set(e.target.value) });
    }
    if (input.tagName === "SELECT" && !input.multiple) input.addEventListener("change", () => set(input.value));
    return h("div", {},
      h("label", { for: id, class: "text-sm font-medium text-slate-700" }, spec.label,
        spec.required ? null : h("span", { class: "font-normal text-slate-400" }, " (optional)")),
      input,
      spec.help ? h("p", { class: "mt-1 text-xs text-slate-500" }, spec.help) : null);
  }

  // ---------- Conditions ----------
  function valueWidget(rule, field) {
    const vt = field.valueType;
    if (["lifecycle", "source", "deal_status", "user", "tag", "stage", "grade", "yesno", "segment"].includes(vt)) {
      const select = options(h("select", { "aria-label": "Value" }), schema.choices[vt] || [], rule.value, "Pick…");
      select.addEventListener("change", () => { rule.value = select.value; });
      return select;
    }
    return h("input", { type: vt === "number" ? "number" : "text", value: rule.value ?? "", "aria-label": "Value", placeholder: "Value",
                        oninput: e => { rule.value = e.target.value; } });
  }

  function conditionsEditor(conditions, rerender) {
    const wrap = h("div", { class: "mt-2 space-y-2" });
    if (conditions.rules.length > 1) {
      const match = options(h("select", { "aria-label": "Match" }),
        [{ id: "all", text: "All of these are true" }, { id: "any", text: "Any of these is true" }], conditions.match);
      match.addEventListener("change", () => { conditions.match = match.value; });
      wrap.append(h("div", { class: "max-w-xs" }, match));
    }
    conditions.rules.forEach((rule, index) => {
      const field = condFields[rule.field];
      const fieldSelect = options(h("select", { "aria-label": "Field" }),
        schema.conditionFields.map(f => ({ id: f.key, text: f.label })), rule.field, "Pick a field");
      fieldSelect.addEventListener("change", () => {
        rule.field = fieldSelect.value; rule.op = ""; rule.value = ""; rerender();
      });
      const row = h("div", { class: "grid items-start gap-2 rounded-lg bg-slate-50 p-2 ring-1 ring-slate-200 sm:grid-cols-[1.3fr_1fr_1.3fr_auto]" },
        fieldSelect);
      if (field) {
        const ops = schema.operators[field.kind];
        if (!rule.op) rule.op = ops[0].id;
        const opSelect = options(h("select", { "aria-label": "Operator" }), ops, rule.op);
        opSelect.addEventListener("change", () => { rule.op = opSelect.value; rerender(); });
        row.append(opSelect, schema.noValueOps.includes(rule.op) ? h("span") : valueWidget(rule, field));
      } else {
        row.append(h("span"), h("span"));
      }
      row.append(h("button", { type: "button", class: "btn btn-secondary px-3 py-2 text-red-600", "aria-label": "Remove condition",
                               onclick: () => { conditions.rules.splice(index, 1); rerender(); } }, "✕"));
      wrap.append(row);
    });
    wrap.append(h("button", { type: "button", class: "text-sm font-medium text-violet-600 hover:underline",
                              onclick: () => { conditions.rules.push({ field: "", op: "", value: "" }); rerender(); } }, "+ Add condition"));
    if (!conditions.rules.length) wrap.prepend(h("p", { class: "text-sm text-slate-500" }, "No conditions: every record that hits the trigger goes through."));
    return wrap;
  }

  // ---------- Sections ----------
  function renderTrigger(root) {
    const select = options(h("select", { id: "trigger-type", "data-placeholder": "When this happens…" }),
      groupBy(schema.triggers), state.trigger_type, "Pick a trigger");
    select.addEventListener("change", () => { state.trigger_type = select.value; state.trigger_config = {}; renderAll(); });
    root.append(h("label", { for: "trigger-type", class: "text-sm font-medium text-slate-700" }, "When"), select);
    const trigger = triggers[state.trigger_type];
    if (trigger) {
      if (trigger.help) root.append(h("p", { class: "mt-1 text-xs text-slate-500" }, trigger.help));
      const fields = h("div", { class: "mt-4 grid gap-4 sm:grid-cols-2" });
      trigger.config.forEach(spec => fields.append(fieldInput(spec, state.trigger_config, renderAll)));
      if (trigger.config.length) root.append(fields);
    }
  }

  function groupBy(items) {
    const groups = {};
    items.forEach(item => (groups[item.group] = groups[item.group] || []).push({ id: item.key, text: item.label }));
    return Object.entries(groups).map(([text, children]) => ({ text, children }));
  }

  const STEP_STYLE = { wait: "ring-amber-200 bg-amber-50/40", condition_gate: "ring-sky-200 bg-sky-50/40" };

  function renderActions(root) {
    state.actions.forEach((step, index) => {
      const spec = actions[step.type];
      step.config = step.config || {};
      const move = (delta) => {
        const target = index + delta;
        if (target < 0 || target >= state.actions.length) return;
        [state.actions[index], state.actions[target]] = [state.actions[target], state.actions[index]];
        renderAll();
      };
      const card = h("div", { class: `rounded-xl bg-white p-4 shadow-sm ring-1 ${STEP_STYLE[step.type] || "ring-slate-200"}` },
        h("div", { class: "flex items-center justify-between gap-2" },
          h("p", { class: "font-medium" }, h("span", { class: "mr-2 inline-grid h-6 w-6 place-items-center rounded-full bg-violet-600 text-xs text-white" }, index + 1),
            spec ? spec.label : `Unknown action (${step.type})`),
          h("div", { class: "flex gap-1" },
            h("button", { type: "button", class: "btn btn-secondary px-2 py-1", "aria-label": "Move up", disabled: index === 0, onclick: () => move(-1) }, "↑"),
            h("button", { type: "button", class: "btn btn-secondary px-2 py-1", "aria-label": "Move down", disabled: index === state.actions.length - 1, onclick: () => move(1) }, "↓"),
            h("button", { type: "button", class: "btn btn-secondary px-2 py-1 text-red-600", "aria-label": "Remove step",
                          onclick: () => { state.actions.splice(index, 1); renderAll(); } }, "✕"))));
      if (spec) {
        if (spec.help) card.append(h("p", { class: "mt-1 text-xs text-slate-500" }, spec.help));
        const grid = h("div", { class: "mt-3 grid gap-4 sm:grid-cols-2" });
        spec.config.forEach(field => {
          const el = fieldInput(field, step.config, renderAll);
          if (["textarea", "conditions"].includes(field.type) || (field.type === "text" && ["subject", "title"].includes(field.name))) {
            el.classList.add("sm:col-span-2");
          }
          grid.append(el);
        });
        card.append(grid);
      }
      root.append(card);
      if (index < state.actions.length - 1) root.append(h("div", { class: "ml-5 h-4 w-px bg-slate-300", "aria-hidden": "true" }));
    });

    const add = options(h("select", { id: "add-action", "data-placeholder": "+ Add a step…" }), groupBy(schema.actions), "", "+ Add a step…");
    add.addEventListener("change", () => {
      if (!add.value) return;
      state.actions.push({ type: add.value, config: {} });
      renderAll();
      document.querySelector("#actions-root > div:last-of-type")?.scrollIntoView({ behavior: "smooth", block: "center" });
    });
    root.append(h("div", { class: `${state.actions.length ? "mt-4" : ""} max-w-sm` }, add));
  }

  function renderAll() {
    const sections = { "trigger-root": renderTrigger, "conditions-root": root => root.append(conditionsEditor(state.conditions, renderAll)),
                       "actions-root": renderActions };
    for (const [id, render] of Object.entries(sections)) {
      const root = document.getElementById(id);
      if (!root) continue;  // conditions-only pages (segments, scoring rules, sequence exits) have just one section
      if (window.jQuery) jQuery(root).find("select.select2-hidden-accessible").select2("destroy");
      root.replaceChildren();
      render(root);
      if (window.crmInitUI) window.crmInitUI(root);
    }
  }

  document.getElementById("builder-form").addEventListener("submit", () => {
    const form = document.getElementById("builder-form");
    if (form.elements.conditions_json) {  // conditions-only mode
      form.elements.conditions_json.value = JSON.stringify(state.conditions);
      return;
    }
    state.name = form.elements.name.value;
    state.description = form.elements.description.value;
    state.is_active = form.elements.is_active.checked;
    state.allow_reenroll = form.elements.allow_reenroll.checked;
    form.elements.definition.value = JSON.stringify(state);
  });

  // ui.js loads with `defer` too; wait for it so Select2 is available.
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => setTimeout(renderAll));
  else renderAll();
})();
