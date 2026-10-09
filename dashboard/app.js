// Findings dashboard for scanner report.json. Plain DOM, no dependencies. Every string from the
// report (which quotes scanned repository code) is inserted as text, never as HTML.
"use strict";

const STATUS = {
  completed: { label: "Completed", icon: "✓", note: "whole scope evaluated" },
  partial: { label: "Partial", icon: "◐", note: "some files not evaluated" },
  unavailable: { label: "Unavailable", icon: "!", note: "evidence missing; nothing evaluated" },
  error: { label: "Error", icon: "✕", note: "crashed or invalid output" },
  not_applicable: { label: "Not applicable", icon: "–", note: "no files this check examines" },
};
const CONFIDENCE = ["high", "medium", "low"];
const $ = (id) => document.getElementById(id);

function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

const safeUrl = (url) => (typeof url === "string" && /^https?:\/\//i.test(url) ? url : null);
const fmt = (n) => Number(n || 0).toLocaleString("en-US");
const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);

function statusBadge(status) {
  const s = STATUS[status] || { label: status, icon: "?" };
  return el("span", { class: "status" }, el("span", { class: `icon st-${status}`, "aria-hidden": "true" }, s.icon), s.label);
}

function confidenceBadge(conf) {
  return el("span", { class: "conf" },
    el("span", { class: "swatch", style: `background: var(--conf-${conf})`, "aria-hidden": "true" }),
    `${cap(conf)} confidence`);
}

let report = null;
const filters = { layer: "", owner: "", check: "", confidence: "", text: "" };

function showMessage(text) {
  const box = $("message");
  box.hidden = !text;
  box.textContent = text || "";
}

function load(data, origin) {
  if (!data || typeof data !== "object" || !Array.isArray(data.findings) || !Array.isArray(data.checks) || !data.summary) {
    showMessage(`${origin} is not a scanner report.json (missing findings/checks/summary).`);
    return;
  }
  report = data;
  Object.keys(filters).forEach((key) => (filters[key] = ""));
  $("f-text").value = "";
  showMessage("");
  render();
}

function renderHeader() {
  const repo = report.repository || {};
  const title = $("repo-title");
  title.replaceChildren(repo.url && safeUrl(repo.url) ? el("a", { href: repo.url, target: "_blank", rel: "noopener" }, repo.id) : repo.id || "Repository");
  const sha = repo.commit_sha || "";
  const commitUrl = repo.url && safeUrl(repo.url) && repo.commit_source === "git" ? `${repo.url}/tree/${sha}` : null;
  $("repo-meta").replaceChildren(
    "Commit ", commitUrl ? el("a", { href: commitUrl, target: "_blank", rel: "noopener", class: "mono" }, sha.slice(0, 12)) : el("span", { class: "mono" }, sha.slice(0, 12)),
    repo.commit_source === "content-hash" ? " (content hash, not a Git commit)" : "",
    ` · scanned ${report.scanned_at ? new Date(report.scanned_at).toLocaleString() : "?"}`,
    ` · ${fmt(report.files?.collected)} files`,
    ` · scanner ${report.scanner?.version || "?"}`,
  );
  document.title = `${repo.id || "Report"} · Compute-waste audit`;
}

function tile({ label, value, sub, lead, hero }) {
  return el("div", { class: `tile${hero ? " hero" : ""}${value ? "" : " zero"}` },
    el("div", { class: "label" }, lead, label),
    el("div", { class: "value" }, fmt(value)),
    sub ? el("div", { class: "sub" }, sub) : null);
}

function renderSummary() {
  const s = report.summary;
  $("finding-tiles").replaceChildren(
    tile({ label: "Findings", value: s.findings_total, sub: `${fmt(s.checks_total)} checks ran of ${fmt(s.taxonomy_checks_total)} in the taxonomy`, hero: true }),
    ...CONFIDENCE.map((c) => tile({
      label: `${cap(c)} confidence`, value: s.findings_by_confidence?.[c],
      lead: el("span", { class: "swatch", style: `background: var(--conf-${c})`, "aria-hidden": "true" }),
    })),
  );
  $("status-tiles").replaceChildren(...Object.entries(STATUS).map(([key, meta]) => tile({
    label: meta.label, value: s.checks_by_status?.[key], sub: meta.note,
    lead: el("span", { class: `icon st-${key}`, "aria-hidden": "true" }, meta.icon),
  })));
  const broken = (report.adapters || []).filter((a) => a.status !== "ok");
  const alert = $("adapter-alert");
  alert.hidden = broken.length === 0;
  alert.replaceChildren(...broken.map((a) => el("div", {},
    el("b", {}, `Owner ${a.owner} detectors ${a.status}: `), a.reason || "", " Its checks are not in this report.")));
  $("summary").hidden = false;
}

function renderByCheck() {
  const counts = new Map();
  for (const f of report.findings) counts.set(f.check_id, (counts.get(f.check_id) || 0) + 1);
  const rows = [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  const max = Math.max(1, ...rows.map((r) => r[1]));
  const patterns = Object.fromEntries(report.checks.map((c) => [c.check_id, c.pattern]));
  const box = $("by-check");
  if (!rows.length) {
    box.replaceChildren(el("p", { class: "hint" }, "No findings in the evaluated scope."));
    return;
  }
  box.replaceChildren(...rows.map(([check, n]) => el("button", {
    class: `bar-row${filters.check === check ? " active" : ""}`, role: "listitem", type: "button",
    title: `${check}: ${patterns[check] || ""} — ${n} finding${n === 1 ? "" : "s"}`,
    "aria-pressed": filters.check === check ? "true" : "false",
    onclick: () => { filters.check = filters.check === check ? "" : check; $("f-check").value = filters.check; renderFindings(); renderByCheck(); },
  },
  el("span", { class: "bar-label" }, check),
  el("span", { class: "bar-track" }, el("span", { class: "bar-fill", style: `display:block;width:${(n / max) * 100}%` })),
  el("span", { class: "bar-value" }, fmt(n)))));
}

function renderImpact() {
  const impact = report.impact || {};
  $("impact-text").textContent = impact.explanation || "No impact estimate in this report.";
  const m = impact.planned_methodology;
  const url = m && safeUrl(m.reference);
  $("impact-method").replaceChildren(...(m ? ["Planned: ", url ? el("a", { href: url, target: "_blank", rel: "noopener" }, m.name) : m.name, ` (${m.formula}).`] : []));
  if (impact.detector_measurements?.length) {
    $("impact-method").append(el("div", {}, `${impact.detector_measurements.length} detector measurement(s) recorded in the report.`));
  }
}

function fillSelect(id, key, values, labelFor) {
  const select = $(id);
  select.replaceChildren(el("option", { value: "" }, "All"), ...values.map((v) => el("option", { value: v }, labelFor ? labelFor(v) : v)));
  select.value = filters[key];
  select.onchange = () => { filters[key] = select.value; renderFindings(); if (key === "check") renderByCheck(); };
}

function renderFilters() {
  const uniq = (key) => [...new Set(report.findings.map((f) => f[key]).filter(Boolean))].sort();
  fillSelect("f-layer", "layer", uniq("layer"));
  fillSelect("f-owner", "owner", uniq("owner"), (o) => `Owner ${o}`);
  fillSelect("f-check", "check", uniq("check_id"));
  fillSelect("f-confidence", "confidence", CONFIDENCE.filter((c) => report.findings.some((f) => f.confidence === c)), cap);
  $("f-text").oninput = (e) => { filters.text = e.target.value.trim().toLowerCase(); renderFindings(); };
}

function matches(f) {
  if (filters.layer && f.layer !== filters.layer) return false;
  if (filters.owner && f.owner !== filters.owner) return false;
  if (filters.check && f.check_id !== filters.check) return false;
  if (filters.confidence && f.confidence !== filters.confidence) return false;
  if (filters.text) {
    const hay = `${f.file} ${f.summary} ${f.identity} ${f.check_id} ${f.pattern}`.toLowerCase();
    if (!hay.includes(filters.text)) return false;
  }
  return true;
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const area = el("textarea", { style: "position:fixed;opacity:0" });
    area.value = text;
    document.body.append(area);
    area.select();
    const ok = document.execCommand("copy");
    area.remove();
    return ok;
  }
}

function evidenceBlock(items) {
  return items.map((ev) => {
    if (typeof ev.line_start === "number") {
      const lines = String(ev.value).split("\n");
      return el("pre", { class: "evidence", "aria-label": `Evidence from ${ev.locator} line ${ev.line_start}` },
        ...lines.map((text, i) => el("div", {}, el("span", { class: "ln" }, ev.line_start + i), text)));
    }
    return el("pre", { class: "evidence" }, el("span", { class: "ln" }, ev.field || ""), JSON.stringify(ev.value));
  });
}

function findingCard(f) {
  const status = el("span", { class: "copied", role: "status" });
  const refs = (f.references || []).map(safeUrl).filter(Boolean);
  return el("article", { class: "finding" },
    el("div", { class: "finding-head" },
      el("span", { class: "line mono" }, f.line ? `Line ${f.line}` : "No line"),
      confidenceBadge(f.confidence),
      el("span", { class: "pill mono", title: "Detector identity (stable across line moves)" }, f.identity)),
    el("p", { class: "summary-text" }, f.summary),
    ...evidenceBlock(f.evidence || []),
    el("p", { class: "kv" }, el("b", {}, "Recommendation: "), f.recommendation),
    refs.length ? el("ul", { class: "refs", "aria-label": "References" }, ...refs.map((u) => el("li", {}, el("a", { href: u, target: "_blank", rel: "noopener" }, u)))) : null,
    el("div", { class: "finding-actions" },
      el("button", {
        class: "primary", type: "button",
        onclick: async () => { status.textContent = (await copyText(f.agent_prompt)) ? "Copied" : "Copy failed: open the prompt below"; setTimeout(() => (status.textContent = ""), 2500); },
      }, "Copy prompt for your coding agent"),
      status),
    el("details", {}, el("summary", {}, "Show prompt"), el("pre", {}, f.agent_prompt)));
}

function renderFindings() {
  const shown = report.findings.filter(matches);
  $("findings-count").textContent = `Showing ${fmt(shown.length)} of ${fmt(report.findings.length)} findings`;
  const box = $("findings");
  if (!shown.length) {
    box.replaceChildren(el("div", { class: "empty" }, report.findings.length
      ? "No findings match these filters."
      : "No findings in the evaluated scope. Check Coverage & limitations below: unavailable or partial checks are not a clean bill of health."));
    return;
  }
  const checks = Object.fromEntries(report.checks.map((c) => [c.check_id, c]));
  const byCheck = new Map();
  for (const f of shown) {
    if (!byCheck.has(f.check_id)) byCheck.set(f.check_id, new Map());
    const files = byCheck.get(f.check_id);
    if (!files.has(f.file)) files.set(f.file, []);
    files.get(f.file).push(f);
  }
  const groups = [...byCheck.entries()].sort((a, b) => count(b[1]) - count(a[1]) || a[0].localeCompare(b[0]));
  box.replaceChildren(...groups.map(([checkId, files], index) => {
    const c = checks[checkId] || {};
    return el("details", { class: "group", open: index === 0 || filters.check ? true : null },
      el("summary", {},
        el("span", { class: "group-title" }, `${checkId} · ${c.pattern || ""}`),
        el("span", { class: "pill" }, `${fmt(count(files))} finding${count(files) === 1 ? "" : "s"}`),
        el("span", { class: "pill" }, `Owner ${c.owner || "?"}`),
        c.layer ? el("span", { class: "pill" }, `Layer: ${c.layer}`) : null,
        el("span", { class: "pill" }, `${fmt(c.evaluated_size)}/${fmt(c.scope_size)} files evaluated`)),
      ...[...files.entries()].sort((a, b) => String(a[0]).localeCompare(String(b[0]))).map(([file, items]) => [
        el("div", { class: "file mono" }, file),
        ...items.sort((a, b) => (a.line || 0) - (b.line || 0)).map(findingCard),
      ]));
  }));
}
const count = (files) => [...files.values()].reduce((n, items) => n + items.length, 0);

function renderCoverage() {
  const order = ["error", "unavailable", "partial", "completed", "not_applicable"]; // needs attention first
  const checks = [...report.checks].sort((a, b) => order.indexOf(a.status) - order.indexOf(b.status) || a.check_id.localeCompare(b.check_id));
  $("checks-table").replaceChildren(
    el("thead", {}, el("tr", {}, ...["Check", "Pattern", "Owner", "Layer", "Status", "Files evaluated", "Findings", "Why / limitations"].map((h) => el("th", { scope: "col" }, h)))),
    el("tbody", {}, ...checks.map((c) => {
      const notes = [...(c.limitations || []), ...(c.notes || [])];
      // An incomplete check without a scanner-level reason explains itself in its first limitation.
      const why = c.reason || (c.status !== "completed" ? (c.limitations || [])[0] : null);
      return el("tr", {},
        el("td", { class: "mono nowrap" }, c.check_id),
        el("td", {}, c.pattern || ""),
        el("td", {}, c.owner),
        el("td", {}, c.layer || ""),
        el("td", {}, statusBadge(c.status), c.status_source === "scanner" ? el("div", { class: "hint" }, "decided by scanner") : null),
        el("td", { class: "num" }, c.scope_size ? `${fmt(c.evaluated_size)} / ${fmt(c.scope_size)}` : "–"),
        el("td", { class: "num" }, fmt(c.finding_count)),
        el("td", {},
          why ? el("div", {}, why) : null,
          notes.length ? el("details", {}, el("summary", {}, `Limitations (${notes.length})`),
            el("ul", {}, ...notes.map((n) => el("li", {}, n)))) : null));
    })));

  const files = report.files || {};
  const skipped = Object.entries(files.skipped || {}).map(([k, v]) => `${k.replace(/_/g, " ")}: ${fmt(v)}`).join(", ") || "none";
  const limits = files.limits || {};
  $("files-dl").replaceChildren(...[
    ["Collected", `${fmt(files.collected)} of ${fmt(files.seen)} seen`],
    ["Skipped", skipped],
    ["Truncated", files.truncated ? "Yes: file or byte cap reached; some files were not scanned" : "No"],
    ["Limits", `${fmt(limits.max_files)} files, ${fmt(limits.max_file_bytes)} bytes per file`],
    ["Top types", Object.entries(files.by_extension || {}).slice(0, 8).map(([k, v]) => `${k} ${fmt(v)}`).join(", ")],
    ["Scan time", report.timings ? `${report.timings.total_seconds}s (excluding clone)` : "?"],
  ].flatMap(([k, v]) => [el("dt", {}, k), el("dd", {}, v)]));

  $("adapters-table").replaceChildren(
    el("thead", {}, el("tr", {}, ...["Owner", "Adapter", "Status", "Checks"].map((h) => el("th", { scope: "col" }, h)))),
    el("tbody", {}, ...(report.adapters || []).map((a) => el("tr", {},
      el("td", {}, a.owner), el("td", { class: "mono" }, a.name),
      el("td", {}, a.status === "ok" ? "OK" : statusBadge(a.status), a.reason ? el("div", { class: "hint" }, a.reason) : null),
      el("td", { class: "num" }, fmt((a.checks || []).length))))));

  $("limitations").replaceChildren(...(report.limitations || []).map((l) => el("li", {}, l)));
}

function render() {
  renderHeader();
  renderSummary();
  renderByCheck();
  renderImpact();
  renderFilters();
  renderFindings();
  renderCoverage();
  for (const id of ["overview", "findings-section", "coverage-section"]) $(id).hidden = false;
}

$("file-input").addEventListener("change", (event) => {
  const file = event.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    try { load(JSON.parse(reader.result), file.name); } catch (error) { showMessage(`Could not read ${file.name}: ${error.message}`); }
  };
  reader.readAsText(file);
});

const requested = new URLSearchParams(location.search).get("report");
if (requested) {
  fetch(requested)
    .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
    .then((data) => load(data, requested))
    .catch((error) => showMessage(`Could not load ${requested}: ${error.message}. Opened from file://? Use "Load report.json" instead, or serve this folder (python3 -m http.server).`));
} else if (window.SCAN_REPORT) {
  load(window.SCAN_REPORT, "the bundled sample report");
} else {
  showMessage("No report loaded. Use \"Load report.json\" or add ?report=<url>.");
}
