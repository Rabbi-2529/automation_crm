// Sales report charts (Chart.js). Colours validated for the white card surface:
// single series = violet #7c3aed; second series = orange #eb6834 (dataviz validator: all checks pass).
(function () {
  "use strict";
  const data = JSON.parse(document.getElementById("chart-data").textContent);
  const currency = data.currency || "BDT";
  const SERIES_1 = "#7c3aed", SERIES_2 = "#eb6834";
  const INK = "#0f172a", MUTED = "#64748b", GRID = "#e2e8f0", BASELINE = "#cbd5e1";
  const money = n => `${currency} ${Math.round(Number(n)).toLocaleString("en-US")}`;
  const compact = n => new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(n);

  function base(horizontal) {
    const valueAxis = { beginAtZero: true, border: { display: false }, grid: { color: GRID, lineWidth: 1, drawTicks: false },
                        ticks: { color: MUTED, padding: 6, maxTicksLimit: 6, font: { family: "Outfit" } } };
    const categoryAxis = { border: { color: BASELINE }, grid: { display: false }, ticks: { color: MUTED, font: { family: "Outfit" } } };
    return {
      responsive: true, maintainAspectRatio: false, animation: { duration: 250 },
      indexAxis: horizontal ? "y" : "x",
      interaction: { mode: "index", intersect: false },  // hovering anywhere in a band shows its tooltip (bigger hit target)
      scales: horizontal ? { x: valueAxis, y: categoryAxis } : { x: categoryAxis, y: valueAxis },
      plugins: {
        legend: { display: false },
        tooltip: { backgroundColor: "#ffffff", titleColor: INK, bodyColor: INK, borderColor: GRID, borderWidth: 1, padding: 10,
                   boxPadding: 4, usePointStyle: true, titleFont: { family: "Outfit", weight: "600" }, bodyFont: { family: "Outfit" } },
      },
    };
  }

  function bars(color, horizontal) {
    // <= 24px thick, 4px rounded data end, square at the baseline.
    return { backgroundColor: color, hoverBackgroundColor: color, maxBarThickness: 24, borderRadius: 4, borderSkipped: "start",
             categoryPercentage: 0.7, barPercentage: 0.9 };
  }

  function table(id, headers, rows) {
    const wrap = document.querySelector(`[data-table-wrap="${id}"]`);
    if (!wrap) return;
    const t = document.createElement("table");
    t.className = "table";
    const head = t.createTHead().insertRow();
    headers.forEach((h, i) => { const th = document.createElement("th"); th.textContent = h; if (i) th.className = "text-right"; head.append(th); });
    const body = t.createTBody();
    rows.forEach(r => { const tr = body.insertRow(); r.forEach((v, i) => { const td = tr.insertCell(); td.textContent = v; if (i) td.className = "text-right tabular-nums"; }); });
    wrap.replaceChildren(t);
  }

  function chart(id, config) {
    const canvas = document.getElementById(`chart-${id}`);
    if (canvas) new Chart(canvas, config);
  }

  // Revenue won over time (single series: no legend, the title names it).
  const revenue = data.revenue.points;
  chart("revenue", {
    type: "bar",
    data: { labels: revenue.map(p => p.label), datasets: [{ label: "Revenue won", data: revenue.map(p => Number(p.value)), ...bars(SERIES_1) }] },
    options: { ...base(false), scales: { ...base(false).scales, y: { ...base(false).scales.y, ticks: { ...base(false).scales.y.ticks, callback: v => compact(v) } } },
               plugins: { ...base(false).plugins, tooltip: { ...base(false).plugins.tooltip, callbacks: {
                 label: c => ` ${money(c.parsed.y)} · ${revenue[c.dataIndex].count} deal${revenue[c.dataIndex].count === 1 ? "" : "s"}` } } } },
  });
  table("revenue", ["Period", "Revenue", "Deals"], revenue.map(p => [p.label, money(p.value), p.count]));

  // Funnel: horizontal bars, conversion from the previous stage in the tooltip and table.
  if (data.funnel.length) {
    chart("funnel", {
      type: "bar",
      data: { labels: data.funnel.map(r => r.label), datasets: [{ label: "Deals", data: data.funnel.map(r => r.count), ...bars(SERIES_1, true) }] },
      options: { ...base(true), plugins: { ...base(true).plugins, tooltip: { ...base(true).plugins.tooltip, callbacks: {
        label: c => { const r = data.funnel[c.dataIndex]; return ` ${r.count} deal${r.count === 1 ? "" : "s"}${r.conversion != null ? ` · ${r.conversion}% of previous stage` : ""}`; } } } } },
    });
    table("funnel", ["Stage", "Deals", "From previous"], data.funnel.map(r => [r.label, r.count, r.conversion != null ? `${r.conversion}%` : "—"]));
  }

  // Forecast: two series of the same unit on one axis -> legend required.
  const months = data.forecast.months;
  chart("forecast", {
    type: "bar",
    data: { labels: months.map(m => m.label), datasets: [
      { label: "Pipeline value", data: months.map(m => Number(m.value)), ...bars(SERIES_1) },
      { label: "Weighted", data: months.map(m => Number(m.weighted)), ...bars(SERIES_2) },
    ] },
    options: { ...base(false),
      scales: { ...base(false).scales, y: { ...base(false).scales.y, ticks: { ...base(false).scales.y.ticks, callback: v => compact(v) } } },
      plugins: { ...base(false).plugins,
        legend: { display: true, position: "top", align: "end", labels: { usePointStyle: true, pointStyle: "rectRounded", boxWidth: 10, color: INK, font: { family: "Outfit" } } },
        tooltip: { ...base(false).plugins.tooltip, callbacks: {
          label: c => ` ${c.dataset.label}: ${money(c.parsed.y)}`,
          afterBody: items => `${months[items[0].dataIndex].count} open deal${months[items[0].dataIndex].count === 1 ? "" : "s"}` } } } },
  });
  const extra = [data.forecast.overdue, data.forecast.undated].filter(r => r.count);
  table("forecast", ["Close month", "Pipeline value", "Weighted", "Deals"],
        [...months, ...extra].map(m => [m.label, money(m.value), money(m.weighted), m.count]));

  // Lost reasons and lead sources: horizontal single-series bars.
  if (data.lost.length) {
    chart("lost", {
      type: "bar",
      data: { labels: data.lost.map(r => r.label), datasets: [{ label: "Deals lost", data: data.lost.map(r => r.count), ...bars(SERIES_1, true) }] },
      options: { ...base(true), plugins: { ...base(true).plugins, tooltip: { ...base(true).plugins.tooltip, callbacks: {
        label: c => ` ${data.lost[c.dataIndex].count} deal(s) · ${money(data.lost[c.dataIndex].value)}` } } } },
    });
    table("lost", ["Reason", "Deals", "Value"], data.lost.map(r => [r.label, r.count, money(r.value)]));
  }
  if (data.sources.length) {
    chart("sources", {
      type: "bar",
      data: { labels: data.sources.map(r => r.label), datasets: [{ label: "New leads", data: data.sources.map(r => r.count), ...bars(SERIES_1, true) }] },
      options: base(true),
    });
    table("sources", ["Source", "New leads"], data.sources.map(r => [r.label, r.count]));
  }

  // Chart / Table toggles.
  document.addEventListener("click", e => {
    const button = e.target.closest("[data-view]");
    if (!button) return;
    const id = button.dataset.for, showTable = button.dataset.view === "table";
    document.querySelector(`[data-chart-wrap="${id}"]`).classList.toggle("hidden", showTable);
    document.querySelector(`[data-table-wrap="${id}"]`).classList.toggle("hidden", !showTable);
    button.parentElement.querySelectorAll("[data-view]").forEach(b => {
      const active = b === button;
      b.setAttribute("aria-pressed", active);
      b.classList.toggle("bg-white", active); b.classList.toggle("shadow-sm", active); b.classList.toggle("font-medium", active);
      b.classList.toggle("text-slate-600", !active);
    });
  });
})();
