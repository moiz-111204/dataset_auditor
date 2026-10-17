/* Dataset Bias & Privacy Auditor - dashboard logic.
 * All API text is inserted with textContent (never innerHTML) so data cannot inject markup.
 * The session id lives only in this page's memory, so two tabs never share results. */
"use strict";

(() => {
  const MAX_BYTES = 10 * 1024 * 1024;
  const SEVERITIES = ["critical", "high", "medium", "low"];

  const state = { report: null, charts: {}, groupAttr: null, busy: false };
  const $ = (id) => document.getElementById(id);

  /* ---------- DOM helpers ---------- */

  function el(tag, props = {}, children = []) {
    const node = document.createElement(tag);
    if (props.class) node.className = props.class;
    if (props.text !== undefined) node.textContent = String(props.text);
    if (props.attrs) for (const [k, v] of Object.entries(props.attrs)) node.setAttribute(k, v);
    for (const child of [].concat(children)) if (child) node.append(child);
    return node;
  }

  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

  function pct(value, digits = 1) { return `${(value * 100).toFixed(digits)}%`; }

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function severityBadge(severity, count) {
    const badge = el("span", { class: `badge badge-${severity}` }, [el("span", { text: severity })]);
    if (count !== undefined) badge.append(el("span", { class: "num", text: count }));
    return badge;
  }

  function table(headers, rows, numericCols = []) {
    const thead = el("thead", {}, el("tr", {}, headers.map((h, i) =>
      el("th", { text: h, class: numericCols.includes(i) ? "num" : "", attrs: { scope: "col" } }))));
    const tbody = el("tbody", {}, rows.map((row) => el("tr", {}, row.map((cell, i) =>
      cell instanceof Node ? el("td", {}, cell)
        : el("td", { text: cell, class: numericCols.includes(i) ? "num" : "" })))));
    return el("table", {}, [thead, tbody]);
  }

  /* ---------- API ---------- */

  function friendlyError(status, body) {
    const detail = body && typeof body.detail === "string" ? body.detail : "";
    if (status === 413) return detail || "That file is too large. The limit is 10 MB and 50,000 rows.";
    if (status === 415) return detail || "Please upload a .csv file.";
    if (status === 429) return "Too many requests in a short time. Please wait a minute and try again.";
    if (status === 404) return detail || "That analysis has expired. Please upload the file again.";
    if (status === 400 || status === 422) return detail || "The file or settings could not be read.";
    return detail || `Something went wrong on the server (status ${status}). Please try again.`;
  }

  async function api(path, options = {}) {
    let res;
    try {
      res = await fetch(path, options);
    } catch {
      throw new Error("Could not reach the server. Check your connection and try again.");
    }
    let body = null;
    try { body = await res.json(); } catch { body = null; }
    if (!res.ok) throw new Error(friendlyError(res.status, body));
    return body;
  }

  /* ---------- Busy / error state ---------- */

  function setBusy(busy, message = "Analyzing…") {
    state.busy = busy;
    $("status").hidden = !busy;
    $("status-text").textContent = message;
    document.querySelectorAll("#dropzone, #sample-buttons button, #rerun-btn")
      .forEach((b) => { b.disabled = busy; });
  }

  function showError(message) {
    const box = $("error");
    box.textContent = message || "";
    box.hidden = !message;
  }

  async function run(task, message) {
    if (state.busy) return;
    showError("");
    setBusy(true, message);
    try {
      const report = await task();
      renderReport(report);
    } catch (err) {
      showError(err.message);
    } finally {
      setBusy(false);
    }
  }

  /* ---------- Upload & samples ---------- */

  function uploadFile(file) {
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".csv")) { showError("Please choose a .csv file."); return; }
    if (file.size > MAX_BYTES) { showError("That file is larger than the 10 MB limit."); return; }
    if (file.size === 0) { showError("That file is empty."); return; }
    const form = new FormData();
    form.append("file", file);
    run(() => api("/api/upload", { method: "POST", body: form }), `Analyzing ${file.name}…`);
  }

  function sampleLabel(name) {
    const base = name.replace(/\.csv$/i, "").replace(/[_-]+/g, " ");
    return base.charAt(0).toUpperCase() + base.slice(1);
  }

  async function loadSamples() {
    const holder = $("sample-buttons");
    try {
      const { datasets } = await api("/api/sample-datasets");
      clear(holder);
      if (!datasets.length) { holder.append(el("span", { class: "muted", text: "No samples bundled." })); return; }
      for (const ds of datasets) {
        const btn = el("button", { class: "btn btn-secondary btn-chip", text: sampleLabel(ds.name),
          attrs: { type: "button", title: ds.description || ds.name } });
        btn.addEventListener("click", () => run(
          () => api(`/api/analyze-sample/${encodeURIComponent(ds.name)}`, { method: "POST" }),
          `Analyzing ${ds.name}…`));
        holder.append(btn);
      }
    } catch {
      clear(holder);
      holder.append(el("span", { class: "muted", text: "Samples unavailable." }));
    }
  }

  function initUpload() {
    const input = $("file-input");
    const zone = $("dropzone");
    zone.addEventListener("click", () => input.click());
    input.addEventListener("change", () => { uploadFile(input.files[0]); input.value = ""; });
    ["dragenter", "dragover"].forEach((type) => zone.addEventListener(type, (e) => {
      e.preventDefault();
      zone.classList.add("is-dragover");
    }));
    ["dragleave", "drop"].forEach((type) => zone.addEventListener(type, () => zone.classList.remove("is-dragover")));
    zone.addEventListener("drop", (e) => {
      e.preventDefault();
      if (!state.busy) uploadFile(e.dataTransfer.files[0]);
    });
    // Dropping a file elsewhere on the page should not navigate away from the dashboard.
    window.addEventListener("dragover", (e) => e.preventDefault());
    window.addEventListener("drop", (e) => e.preventDefault());
  }

  /* ---------- Settings ---------- */

  function renderSettings(report) {
    const columns = report.summary_stats.columns || Object.keys(report.summary_stats.charts.missing_by_column || {});
    const settings = report.detected_settings;
    const select = $("label-select");
    clear(select);
    select.append(el("option", { text: "None (skip label checks)", attrs: { value: "" } }));
    for (const col of columns) {
      const opt = el("option", { text: col, attrs: { value: col } });
      if (col === settings.label_column) opt.selected = true;
      select.append(opt);
    }
    const list = $("protected-list");
    clear(list);
    columns.forEach((col, i) => {
      const id = `protected-${i}`;
      const box = el("input", { attrs: { type: "checkbox", id, value: col } });
      box.checked = settings.protected_columns.includes(col);
      list.append(el("label", { attrs: { for: id } }, [box, el("span", { text: col })]));
    });
    $("settings").hidden = false;
  }

  function rerun() {
    const report = state.report;
    if (!report) return;
    const body = {
      label_column: $("label-select").value || null,
      protected_columns: [...document.querySelectorAll("#protected-list input:checked")].map((b) => b.value),
    };
    run(() => api(`/api/reanalyze/${encodeURIComponent(report.session_id)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }), "Re-running analysis…");
  }

  /* ---------- Overview ---------- */

  const RISK_COLORS = { Low: "--risk-low", Medium: "--risk-medium", High: "--risk-high", Critical: "--risk-critical" };

  function renderOverview(report) {
    const stats = report.summary_stats;
    $("risk-score").textContent = report.risk_score;
    $("gauge-label").textContent = `${report.risk_level} risk`;
    const arc = $("gauge-arc");
    arc.setAttribute("stroke-dasharray", `${Math.max(report.risk_score, 0.5)} 100`);
    arc.style.stroke = `var(${RISK_COLORS[report.risk_level] || "--risk-low"})`;

    const badge = $("ai-badge");
    badge.textContent = report.ai_enabled ? "AI explanations" : "Template explanations";
    badge.classList.toggle("is-ai", report.ai_enabled);
    badge.title = report.ai_enabled
      ? `Written by ${stats.ai && stats.ai.model ? stats.ai.model : "an LLM"} from aggregate statistics only`
      : `Built-in templates (${stats.ai && stats.ai.fallback_reason ? stats.ai.fallback_reason : "AI not configured"})`;

    const time = new Date(report.timestamp);
    $("dataset-meta").textContent = `${report.dataset_name} · ${report.row_count.toLocaleString()} rows · `
      + `${report.column_count} columns · analyzed ${time.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
    const total = stats.total_issues;
    $("issue-count").textContent = total;
    $("issue-word").textContent = total === 1 ? "issue" : "issues";

    const badges = $("severity-badges");
    clear(badges);
    for (const sev of SEVERITIES) badges.append(el("li", {}, severityBadge(sev, stats.issues_by_severity[sev] || 0)));

    $("overall-summary").textContent = report.overall_summary || "";
    const actions = $("priority-actions");
    clear(actions);
    for (const action of report.priority_actions || []) actions.append(el("li", { text: action }));

    const sid = encodeURIComponent(report.session_id);
    $("download-html").href = `/api/download/${sid}/datacard/html`;
    $("download-md").href = `/api/download/${sid}/datacard/md`;
  }

  /* ---------- Findings ---------- */

  function findingCard(f) {
    const blocks = [
      el("header", { class: "finding-head" }, [severityBadge(f.severity), el("h4", { text: f.title })]),
    ];
    if (f.columns.length) {
      blocks.push(el("ul", { class: "chips", attrs: { "aria-label": "Affected columns" } },
        f.columns.map((c) => el("li", { class: "chip", text: c }))));
    }
    blocks.push(el("p", { class: "finding-desc", text: f.description }));
    if (f.explanation) {
      blocks.push(el("div", { class: "finding-block" }, [el("h5", { text: "Why it matters" }), el("p", { text: f.explanation })]));
    }
    if (f.recommendation) {
      blocks.push(el("div", { class: "finding-block" }, [el("h5", { text: "Recommendation" }), el("p", { text: f.recommendation })]));
    }
    blocks.push(el("p", { class: "source", text: f.explanation_source === "llm"
      ? "Explanation written by AI from aggregate statistics only"
      : "Explanation from a built-in template" }));
    return el("article", { class: `finding sev-${f.severity}` }, blocks);
  }

  function renderFindings(containerId, findings, emptyText) {
    const holder = $(containerId);
    clear(holder);
    if (!findings.length) { holder.append(el("p", { class: "no-findings", text: emptyText })); return; }
    for (const f of findings) holder.append(findingCard(f));
  }

  /* ---------- Charts ---------- */

  function destroyChart(key) {
    if (state.charts[key]) { state.charts[key].destroy(); delete state.charts[key]; }
  }

  function baseOptions(extra = {}) {
    const text = cssVar("--text-2");
    const grid = cssVar("--grid");
    return {
      responsive: true,
      maintainAspectRatio: false,
      animation: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? false : { duration: 300 },
      plugins: {
        legend: { display: false },
        tooltip: { backgroundColor: cssVar("--text"), titleColor: cssVar("--surface"), bodyColor: cssVar("--surface"),
          padding: 10, cornerRadius: 8, displayColors: false },
      },
      scales: {
        x: { grid: { display: false }, ticks: { color: text }, border: { color: grid } },
        y: { grid: { color: grid }, ticks: { color: text }, border: { display: false }, beginAtZero: true },
      },
      ...extra,
    };
  }

  function toggleTable(id, show) { $(id).closest("details").hidden = !show; }

  function chartsAvailable() { return typeof window.Chart !== "undefined"; }

  function renderClassChart(report) {
    destroyChart("class");
    const dist = report.summary_stats.charts.label_distribution || {};
    const label = report.detected_settings.label_column;
    const labels = Object.keys(dist);
    const counts = Object.values(dist);
    const total = counts.reduce((a, b) => a + b, 0);
    const empty = $("class-empty");
    const wrap = $("class-chart").parentElement;
    clear($("class-table"));
    if (!label || labels.length < 2) {
      $("class-caption").textContent = "";
      empty.textContent = label ? `'${label}' has fewer than two values.` : "Choose an outcome column in the settings to see class balance.";
      empty.hidden = false; wrap.hidden = true; toggleTable("class-table", false);
      return;
    }
    empty.hidden = true; wrap.hidden = false; toggleTable("class-table", true);
    $("class-caption").textContent = `Rows per value of '${label}'.`;
    $("class-table").append(table([label, "Rows", "Share"],
      labels.map((l, i) => [l, counts[i].toLocaleString(), pct(counts[i] / total)]), [1, 2]));
    if (!chartsAvailable()) return;
    const opts = baseOptions();
    opts.plugins.tooltip.callbacks = { label: (ctx) => `${ctx.parsed.y.toLocaleString()} rows (${pct(ctx.parsed.y / total)})` };
    state.charts.class = new window.Chart($("class-chart"), {
      type: "bar",
      data: { labels, datasets: [{ data: counts, backgroundColor: cssVar("--series-1"),
        borderRadius: 4, borderSkipped: "start", maxBarThickness: 56 }] },
      options: opts,
    });
  }

  function disparityFinding(report, attr) {
    return report.findings.find((f) => f.details && f.details.check === "demographic_disparity"
      && f.details.protected_column === attr && f.details.flagged_groups);
  }

  function renderGroupSelect(report) {
    const groups = report.summary_stats.charts.group_rates || {};
    const attrs = Object.keys(groups);
    if (!attrs.includes(state.groupAttr)) {
      // Prefer the attribute with the largest disparity.
      const flagged = attrs.find((a) => disparityFinding(report, a));
      state.groupAttr = flagged || attrs[0] || null;
    }
    const select = $("group-select");
    clear(select);
    for (const a of attrs) {
      const opt = el("option", { text: a, attrs: { value: a } });
      if (a === state.groupAttr) opt.selected = true;
      select.append(opt);
    }
    $("group-select-wrap").hidden = attrs.length < 2;
  }

  function renderGroupChart(report) {
    destroyChart("group");
    const groups = report.summary_stats.charts.group_rates || {};
    const info = groups[state.groupAttr];
    const empty = $("group-empty");
    const wrap = $("group-chart").parentElement;
    const legend = $("group-legend");
    clear(legend); clear($("group-table"));
    if (!info) {
      $("group-caption").textContent = "";
      empty.textContent = report.detected_settings.label_column
        ? "Select at least one protected attribute in the settings to compare outcome rates."
        : "Choose an outcome column and protected attributes in the settings to compare outcome rates.";
      empty.hidden = false; wrap.hidden = true; toggleTable("group-table", false);
      return;
    }
    empty.hidden = true; wrap.hidden = false; toggleTable("group-table", true);
    const labels = Object.keys(info.rates);
    const rates = Object.values(info.rates);
    const finding = disparityFinding(report, state.groupAttr);
    const flagged = finding ? finding.details.flagged_groups : {};
    const top = Math.max(...rates);
    const threshold = top * 0.8;
    $("group-caption").textContent = `Share of rows where '${report.detected_settings.label_column}' = `
      + `'${info.positive_label}', per '${state.groupAttr}' group (groups under 20 rows are skipped).`;

    legend.append(
      el("li", {}, [el("span", { class: "swatch", attrs: { style: `background:${cssVar("--series-1")}` } }), el("span", { text: "Group rate" })]),
      el("li", {}, [el("span", { class: "swatch", attrs: { style: `background:${cssVar("--flagged")}` } }), el("span", { text: "Below four-fifths of the top rate" })]),
      el("li", {}, [el("span", { class: "swatch-line" }), el("span", { text: `80% of top rate (${pct(threshold)})` })]),
    );
    $("group-table").append(table([state.groupAttr, "Positive rate", "Ratio to top", "Flag"],
      labels.map((g, i) => [g, pct(rates[i]), top ? (rates[i] / top).toFixed(2) : "-",
        g in flagged ? "Below 0.8" : ""]), [1, 2]));
    if (!chartsAvailable()) return;

    const opts = baseOptions();
    opts.scales.y.ticks.callback = (v) => `${v}%`;
    opts.scales.y.suggestedMax = Math.min(100, Math.ceil(top * 100 / 10) * 10 + 10);
    opts.plugins.tooltip.filter = (item) => item.datasetIndex === 0;
    opts.plugins.tooltip.callbacks = {
      label: (ctx) => {
        const g = labels[ctx.dataIndex];
        const ratio = top ? (rates[ctx.dataIndex] / top).toFixed(2) : "-";
        return [`Rate: ${ctx.parsed.y.toFixed(1)}%`, `Ratio to top group: ${ratio}`]
          .concat(g in flagged ? ["Below the four-fifths threshold"] : []);
      },
    };
    state.charts.group = new window.Chart($("group-chart"), {
      type: "bar",
      data: {
        labels,
        datasets: [
          { data: rates.map((r) => r * 100), borderRadius: 4, borderSkipped: "start", maxBarThickness: 56, order: 2,
            backgroundColor: labels.map((g) => (g in flagged ? cssVar("--flagged") : cssVar("--series-1"))) },
          { type: "line", data: labels.map(() => threshold * 100), borderColor: cssVar("--text-2"), borderWidth: 2,
            borderDash: [6, 4], pointRadius: 0, pointHitRadius: 0, fill: false, order: 1 },
        ],
      },
      options: opts,
    });
  }

  function renderMissingChart(report) {
    destroyChart("missing");
    const shares = report.summary_stats.charts.missing_by_column || {};
    const entries = Object.entries(shares).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]);
    const empty = $("missing-empty");
    const wrap = $("missing-wrap");
    clear($("missing-table"));
    if (!entries.length) {
      empty.textContent = "No missing values were found in any column.";
      empty.hidden = false; wrap.hidden = true; toggleTable("missing-table", false);
      return;
    }
    empty.hidden = true; wrap.hidden = false; toggleTable("missing-table", true);
    wrap.style.height = `${Math.max(140, entries.length * 34 + 50)}px`;
    $("missing-table").append(table(["Column", "Missing"], entries.map(([c, v]) => [c, pct(v)]), [1]));
    if (!chartsAvailable()) return;
    const opts = baseOptions({ indexAxis: "y" });
    opts.scales = {
      x: { beginAtZero: true, max: 100, grid: { color: cssVar("--grid") }, border: { display: false },
        ticks: { color: cssVar("--text-2"), callback: (v) => `${v}%` } },
      y: { grid: { display: false }, ticks: { color: cssVar("--text-2") }, border: { color: cssVar("--grid") } },
    };
    opts.plugins.tooltip.callbacks = { label: (ctx) => `${ctx.parsed.x.toFixed(1)}% of rows missing` };
    state.charts.missing = new window.Chart($("missing-chart"), {
      type: "bar",
      data: { labels: entries.map(([c]) => c), datasets: [{ data: entries.map(([, v]) => v * 100),
        backgroundColor: cssVar("--series-1"), borderRadius: 4, borderSkipped: "start", maxBarThickness: 22 }] },
      options: opts,
    });
  }

  function renderCharts() {
    if (!state.report) return;
    renderClassChart(state.report);
    renderGroupSelect(state.report);
    renderGroupChart(state.report);
    renderMissingChart(state.report);
  }

  /* ---------- PII ---------- */

  function renderPII(report, findings) {
    const det = report.summary_stats.pii_detectors || {};
    $("pii-detectors").textContent = det.presidio_ner
      ? `Detectors: regex rules and Presidio NER (${det.ner_model}). Actual values are never shown.`
      : `Detectors: regex rules only (NER unavailable: ${det.presidio_reason || "not loaded"}). Actual values are never shown.`;
    const holder = $("pii-table");
    clear(holder);
    if (findings.length) {
      holder.append(table(["PII type", "Severity", "Columns", "Rows affected"],
        findings.map((f) => [f.details.pii_label || f.title, severityBadge(f.severity), f.columns.join(", "),
          `${(f.details.rows_affected || 0).toLocaleString()} (${f.details.percent_rows}%)`]), [3]));
    }
    renderFindings("findings-pii", findings, "No personal data was detected. The scan can still miss unusual formats, so review free-text columns by hand.");
  }

  /* ---------- Tabs ---------- */

  function selectTab(tab, focus = false) {
    document.querySelectorAll('[role="tab"]').forEach((t) => {
      const selected = t === tab;
      t.setAttribute("aria-selected", String(selected));
      t.tabIndex = selected ? 0 : -1;
      $(t.getAttribute("aria-controls")).hidden = !selected;
    });
    if (focus) tab.focus();
    // Charts drawn inside a hidden panel have no size, so redraw once the panel is visible.
    requestAnimationFrame(renderCharts);
  }

  function initTabs() {
    const tabs = [...document.querySelectorAll('[role="tab"]')];
    tabs.forEach((tab, i) => {
      tab.addEventListener("click", () => selectTab(tab));
      tab.addEventListener("keydown", (e) => {
        let next = null;
        if (e.key === "ArrowRight") next = tabs[(i + 1) % tabs.length];
        if (e.key === "ArrowLeft") next = tabs[(i - 1 + tabs.length) % tabs.length];
        if (e.key === "Home") next = tabs[0];
        if (e.key === "End") next = tabs[tabs.length - 1];
        if (next) { e.preventDefault(); selectTab(next, true); }
      });
    });
  }

  /* ---------- Render ---------- */

  function renderReport(report) {
    const firstRender = !state.report;
    state.report = report;
    const byCat = (c) => report.findings.filter((f) => f.category === c);
    const bias = byCat("bias");
    const missing = byCat("missing_data");
    const pii = byCat("pii");

    renderSettings(report);
    renderOverview(report);
    $("count-bias").textContent = bias.length;
    $("count-missing").textContent = missing.length;
    $("count-pii").textContent = pii.length;
    renderFindings("findings-bias", bias, "No bias issues were found by these checks.");
    renderFindings("findings-missing", missing, "No missing-data issues were found.");
    renderPII(report, pii);

    $("results").hidden = false;
    renderCharts();
    if (firstRender) {
      $("results").scrollIntoView({ behavior: "smooth", block: "start" });
      $("results-title").focus({ preventScroll: true });
    }
  }

  /* ---------- Theme ---------- */

  function storedTheme() {
    try { return localStorage.getItem("auditor-theme"); } catch { return null; }
  }

  function currentTheme() {
    const forced = document.documentElement.dataset.theme;
    if (forced) return forced;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  function updateThemeButton() {
    const next = currentTheme() === "dark" ? "light" : "dark";
    $("theme-toggle").setAttribute("aria-label", `Switch to ${next} theme`);
  }

  function initTheme() {
    const saved = storedTheme();
    if (saved === "light" || saved === "dark") document.documentElement.dataset.theme = saved;
    updateThemeButton();
    $("theme-toggle").addEventListener("click", () => {
      const next = currentTheme() === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem("auditor-theme", next); } catch { /* storage unavailable */ }
      updateThemeButton();
      renderCharts();
    });
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
      updateThemeButton();
      renderCharts();
    });
  }

  /* ---------- Init ---------- */

  function init() {
    initTheme();
    initUpload();
    initTabs();
    $("rerun-btn").addEventListener("click", rerun);
    $("group-select").addEventListener("change", (e) => { state.groupAttr = e.target.value; renderGroupChart(state.report); });
    if (window.Chart) {
      window.Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
    }
    loadSamples();
  }

  init();
})();
