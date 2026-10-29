// ThingBD CRM UI setup: Select2 on form selects, AJAX pickers, DataTables (client + server side).
// Runs on page load and again on every HTMX swap.
(function ($) {
  "use strict";

  const escapeHtml = text => $("<div>").text(text == null ? "" : String(text)).html();

  // Select2 fires jQuery events only; HTMX / plain listeners need a native "change".
  function nativeChange(el) { el.dispatchEvent(new Event("change", { bubbles: true })); }

  function initSelect2(scope) {
    $(scope).find("select").not("[data-no-select2], .select2-hidden-accessible").each(function () {
      const el = this;
      const $el = $(el);
      const inForm = $el.closest(".form-stack, [data-select2-scope]").length || $el.is("[data-select2], [data-select2-ajax]");
      if (!inForm || $el.closest(".dt-container").length) return;

      const firstOption = el.options.length ? el.options[0] : null;
      const hasBlank = firstOption && firstOption.value === "";
      const options = {
        width: "100%",
        placeholder: $el.data("placeholder") || (hasBlank ? firstOption.text : ""),
        allowClear: !el.multiple && hasBlank && !el.required && !$el.closest(".filter-bar").length,
        minimumResultsForSearch: el.multiple ? 0 : 8,
      };

      const ajaxUrl = $el.data("select2-ajax");
      if (ajaxUrl) {
        Object.assign(options, {
          allowClear: !el.required,
          minimumInputLength: 1,
          ajax: {
            url: ajaxUrl, delay: 250, dataType: "json",
            data: params => ({ q: params.term || "" }),
            processResults: data => ({ results: data.results }),
          },
          templateResult: item => item.loading ? item.text :
            $(`<span>${escapeHtml(item.text)}${item.hint ? `<span class="select2-result-hint">${escapeHtml(item.hint)}</span>` : ""}</span>`),
        });
      }
      $el.select2(options).on("select2:select select2:unselect select2:clear", () => nativeChange(el));
    });
  }

  // ---------- DataTables ----------
  function filterParams(form) {
    return form ? Object.fromEntries(new FormData(form).entries()) : {};
  }

  function syncExportLinks(table, form) {
    const params = new URLSearchParams(filterParams(form));
    const q = table.search();
    if (q) params.set("q", q);
    document.querySelectorAll("a[data-export-base]").forEach(link => {
      link.href = `${link.dataset.exportBase}?${params.toString()}`;
    });
  }

  function initDataTables(scope) {
    $(scope).find("table[data-datatable]").each(function () {
      if ($.fn.dataTable.isDataTable(this)) return;
      const rows = this.tBodies[0] ? this.tBodies[0].rows.length : 0;
      new DataTable(this, {
        order: [], pageLength: 25, lengthMenu: [10, 25, 50, 100],
        paging: rows > 25, info: rows > 25,
        language: { search: "", searchPlaceholder: "Search…", emptyTable: this.dataset.empty || "Nothing here yet." },
      });
    });

    $(scope).find("table[data-dt-source]").each(function () {
      if ($.fn.dataTable.isDataTable(this)) return;
      const tableEl = this;
      const form = tableEl.dataset.dtFilters ? document.querySelector(tableEl.dataset.dtFilters) : null;
      const table = new DataTable(tableEl, {
        serverSide: true, processing: true, searchDelay: 350,
        pageLength: Number(tableEl.dataset.pageLength || 25), lengthMenu: [10, 25, 50, 100],
        order: JSON.parse(tableEl.dataset.order || "[]"),
        search: { search: tableEl.dataset.initialSearch || "" },
        ajax: { url: tableEl.dataset.dtSource, data: d => Object.assign(d, filterParams(form)) },
        language: {
          search: "", searchPlaceholder: tableEl.dataset.searchPlaceholder || "Search…",
          emptyTable: tableEl.dataset.empty || "Nothing here yet.", zeroRecords: "No matches for these filters.",
          processing: "Loading…",
        },
        drawCallback: () => {
          syncExportLinks(table, form);
          tableEl.dispatchEvent(new CustomEvent("crm:table-drawn", { bubbles: true }));
        },
      });
      tableEl.crmTable = table;
      if (form) {
        form.addEventListener("change", () => table.ajax.reload());
        form.addEventListener("submit", event => { event.preventDefault(); table.ajax.reload(); });
      }
    });
  }

  function init(scope) {
    initSelect2(scope || document);
    initDataTables(scope || document);
  }

  $(() => init(document));
  document.addEventListener("htmx:load", event => init(event.target));
  window.crmInitUI = init;
})(jQuery);
