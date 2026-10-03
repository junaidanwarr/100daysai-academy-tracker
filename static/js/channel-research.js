/*
 * Channel research workspace (templates/research/_channel_research.html).
 *
 * Reads an .xlsx (SheetJS) or .csv (PapaParse) research sheet in the browser,
 * then drives the summary cards, the angle breakdown and the channel table.
 * Nothing is sent to the server. The last file is remembered in localStorage
 * under a key that includes the signed-in user, so two people sharing a
 * computer do not see each other's research.
 */
(function () {
  "use strict";

  var root = document.getElementById("cr-root");
  if (!root) return;
  var $ = function (id) { return document.getElementById(id); };

  var PAGE_SIZE = 10;
  var STORE_KEY = root.dataset.storeKey || "channelResearch.v1";
  // Read-only: a submitted sheet shown to its reviewer. No upload, no storage.
  var READONLY = root.dataset.readonly || null;
  var SAMPLE_URL = root.dataset.sampleUrl;

  // Display order, with the header variants each column accepts.
  var COLUMNS = [
    { key: "n", label: "#", num: true, variants: ["#", "No", "Sr", "Number", "Sr No", "S No"] },
    { key: "name", label: "Channel Name", variants: ["Channel Name", "Name", "Channel"] },
    { key: "link", label: "Channel Link", variants: ["Channel Link", "Link", "URL"], nosort: true },
    { key: "angle", label: "Angle of the Channel", variants: ["Angle of the Channel", "Angle", "Content Angle"] },
    { key: "cat", label: "Category", variants: ["Category", "Niche", "Type"] },
    { key: "videos", label: "Total Videos", num: true, variants: ["Total Videos", "Videos", "Video Count"] },
    { key: "subs", label: "Total Subscribers", num: true, variants: ["Total Subscribers", "Subscribers", "Subs"] },
    { key: "views", label: "Total Views", num: true, variants: ["Total Views", "Views"] },
    { key: "avg", label: "Avg Views / Video", num: true, variants: ["Avg Views / Video", "Avg Views", "Average Views"] },
    { key: "size", label: "Size", variants: ["Size", "Channel Size"] },
    { key: "notes", label: "Notes", variants: ["Notes", "Note", "Comments"] }
  ];
  var SIZE_ORDER = { Tiny: 1, Small: 2, Medium: 3, Big: 4 };
  var SIZE_TONE = { Big: "emerald", Medium: "blue", Small: "amber", Tiny: "slate" };

  function norm(h) {
    var s = String(h == null ? "" : h).trim().toLowerCase();
    return s === "#" ? "#" : s.replace(/[^a-z0-9]/g, "");
  }
  var LOOKUP = {};
  COLUMNS.forEach(function (c) { c.variants.forEach(function (v) { LOOKUP[norm(v)] = c.key; }); });

  var state = { rows: [], source: null, q: "", cat: "", size: "", sortKey: null, sortDir: 1, page: 1 };

  // ---- Parsing -------------------------------------------------------------

  function toNumber(v) {
    if (v == null || v === "") return null;
    if (typeof v === "number") return isFinite(v) ? v : null;
    var m = String(v).trim().replace(/[,\s]/g, "").match(/^(-?\d*\.?\d+)([kmb])?$/i);
    if (!m) return null;
    return parseFloat(m[1]) * ({ k: 1e3, m: 1e6, b: 1e9 }[(m[2] || "").toLowerCase()] || 1);
  }
  function toText(v) { return v == null ? "" : String(v).trim(); }
  function safeUrl(v) {
    var s = toText(v);
    if (!s) return null;
    if (/^www\./i.test(s) || /^(youtube\.com|youtu\.be)\//i.test(s)) s = "https://" + s;
    return /^https?:\/\/\S+$/i.test(s) ? s : null;
  }
  function normSize(v) {
    var t = toText(v).toLowerCase();
    return { big: "Big", large: "Big", medium: "Medium", mid: "Medium", small: "Small", tiny: "Tiny" }[t] || toText(v);
  }

  // The heading row is the first of the top 25 rows that names a channel
  // column plus two others, so a sheet can keep its title rows.
  function findHeader(aoa) {
    for (var i = 0; i < Math.min(aoa.length, 25); i++) {
      var row = aoa[i] || [], map = {}, found = {};
      for (var c = 0; c < row.length; c++) {
        var key = LOOKUP[norm(row[c])];
        if (key && !found[key]) { map[c] = key; found[key] = true; }
      }
      if (found.name && Object.keys(found).length >= 3) return { index: i, map: map, found: found };
    }
    return null;
  }

  function rowsFrom(aoa, links) {
    var hdr = findHeader(aoa);
    if (!hdr) {
      throw new Error('No column headings found. The file needs a heading row with "Channel Name" and at least two other columns, such as "Category" and "Total Subscribers".');
    }
    var linkCol = Object.keys(hdr.map).find(function (c) { return hdr.map[c] === "link"; });
    var out = [], skipped = 0;
    for (var r = hdr.index + 1; r < aoa.length; r++) {
      var raw = aoa[r] || [], rec = {};
      Object.keys(hdr.map).forEach(function (c) { rec[hdr.map[c]] = raw[c]; });
      var name = toText(rec.name);
      if (!name) { if (raw.some(function (x) { return toText(x) !== ""; })) skipped++; continue; }
      var target = linkCol != null && links && links[r] ? links[r][linkCol] : null;
      var videos = toNumber(rec.videos), views = toNumber(rec.views), avg = toNumber(rec.avg);
      if (avg == null && views != null && videos) avg = views / videos;
      out.push({
        n: toNumber(rec.n), name: name, link: safeUrl(rec.link) || safeUrl(target),
        angle: toText(rec.angle), cat: toText(rec.cat), videos: videos, subs: toNumber(rec.subs),
        views: views, avg: avg, size: normSize(rec.size), notes: toText(rec.notes)
      });
    }
    out.forEach(function (row, i) { if (row.n == null) row.n = i + 1; });
    return {
      rows: out, skipped: skipped,
      missing: COLUMNS.filter(function (c) { return !hdr.found[c.key]; }).map(function (c) { return c.label; })
    };
  }

  function readXlsx(file) {
    return file.arrayBuffer().then(function (buf) {
      if (!window.XLSX) throw new Error("The spreadsheet reader did not load. Check your connection and reload the page.");
      var wb = XLSX.read(buf, { type: "array" });
      var sheet = wb.SheetNames.find(function (n) { return n.trim().toLowerCase() === "all channels"; }) || wb.SheetNames[0];
      var ws = wb.Sheets[sheet];
      if (!ws || !ws["!ref"]) throw new Error('The sheet "' + sheet + '" is empty.');
      // Built by hand so hyperlink targets survive (a "Visit" cell linked to a URL).
      var range = XLSX.utils.decode_range(ws["!ref"]), aoa = [], links = [];
      for (var R = range.s.r; R <= range.e.r; R++) {
        var row = [], lrow = [];
        for (var C = range.s.c; C <= range.e.c; C++) {
          var cell = ws[XLSX.utils.encode_cell({ r: R, c: C })];
          row.push(cell && cell.v != null ? cell.v : null);
          lrow.push(cell && cell.l && cell.l.Target ? cell.l.Target : null);
        }
        aoa.push(row); links.push(lrow);
      }
      var parsed = rowsFrom(aoa, links);
      parsed.sheet = sheet;
      return parsed;
    });
  }

  function readCsv(file) {
    return new Promise(function (resolve, reject) {
      if (!window.Papa) { reject(new Error("The CSV reader did not load. Check your connection and reload the page.")); return; }
      Papa.parse(file, {
        skipEmptyLines: "greedy",
        complete: function (res) {
          if (!res.data || !res.data.length) { reject(new Error("The CSV file has no rows.")); return; }
          try { resolve(rowsFrom(res.data, null)); } catch (e) { reject(e); }
        },
        error: function (err) { reject(new Error("Could not read the CSV file: " + err.message)); }
      });
    });
  }

  function handleFile(file) {
    if (!file) return;
    var ext = (file.name.split(".").pop() || "").toLowerCase();
    if (ext !== "xlsx" && ext !== "csv") {
      setStatus("error", [strong(file.name), " is a ." + (ext || "?") + " file. Upload an .xlsx or .csv file instead."]);
      return;
    }
    setStatus("", ["Reading ", strong(file.name), "…"]);
    (ext === "xlsx" ? readXlsx(file) : readCsv(file)).then(function (parsed) {
      if (!parsed.rows.length) throw new Error("Found the headings but no channel rows below them.");
      load(parsed.rows, { file: file.name, sheet: parsed.sheet || null });
      var parts = [strong(file.name)];
      if (parsed.sheet) parts.push(' · sheet "' + parsed.sheet + '"');
      parts.push(" · " + plural(parsed.rows.length, "channel") + " loaded");
      if (parsed.skipped) parts.push(" · " + parsed.skipped + " row(s) without a channel name skipped");
      if (parsed.missing.length) parts.push(" · no column for " + parsed.missing.join(", "));
      setStatus("success", parts);
    }).catch(function (err) {
      setStatus("error", ["Could not load ", strong(file.name), ". " + (err && err.message ? err.message : "The file could not be read.")]);
    });
  }

  // ---- State ---------------------------------------------------------------

  function save() {
    if (READONLY) return;
    try { localStorage.setItem(STORE_KEY, JSON.stringify({ rows: state.rows, source: state.source })); } catch (e) { /* storage blocked: still works for this visit */ }
  }
  function load(rows, source) {
    state.rows = rows; state.source = source;
    state.q = ""; state.cat = ""; state.size = ""; state.sortKey = null; state.sortDir = 1; state.page = 1;
    $("cr-q").value = "";
    save();
    render();
  }
  function clearData() {
    try { localStorage.removeItem(STORE_KEY); } catch (e) { /* ignore */ }
    state.rows = []; state.source = null; state.q = ""; state.cat = ""; state.size = ""; state.page = 1;
    $("cr-q").value = "";
    setStatus("", ["No file loaded yet."]);
    render();
  }
  function loadSample() {
    var btn = $("cr-sample");
    btn.disabled = true;
    fetch(SAMPLE_URL).then(function (r) {
      if (!r.ok) throw new Error();
      return r.json();
    }).then(function (data) {
      var rows = data.map(function (r) {
        return { n: r[0], name: r[1], link: safeUrl(r[2]), angle: r[3] || "", cat: r[4] || "", videos: r[5], subs: r[6], views: r[7], avg: r[8], size: normSize(r[9]), notes: r[10] || "" };
      });
      load(rows, { file: "Research_Template.xlsx", sheet: "All Channels", sample: true });
      setStatus("success", ["Sample loaded: ", strong("Research_Template.xlsx"), ' · sheet "All Channels" · ' + plural(rows.length, "channel") + ". Upload your own file to replace it."]);
    }).catch(function () {
      setStatus("error", ["The sample could not be loaded. Upload a file instead."]);
    }).then(function () { btn.disabled = false; });
  }

  function matches(row, useCat) {
    if (useCat && state.cat && row.cat !== state.cat) return false;
    if (state.size && row.size !== state.size) return false;
    if (state.q) {
      var hay = (row.name + " " + row.angle + " " + row.cat + " " + row.notes).toLowerCase();
      if (hay.indexOf(state.q.toLowerCase()) < 0) return false;
    }
    return true;
  }
  function filtered(useCat) {
    return state.rows.filter(function (r) { return matches(r, useCat !== false); });
  }
  function sorted(rows) {
    if (!state.sortKey) return rows;
    var key = state.sortKey, dir = state.sortDir;
    var col = COLUMNS.find(function (c) { return c.key === key; });
    return rows.slice().sort(function (a, b) {
      var x = a[key], y = b[key];
      if (key === "size") { x = SIZE_ORDER[x] || null; y = SIZE_ORDER[y] || null; }
      var xe = x == null || x === "", ye = y == null || y === "";
      if (xe || ye) return xe && ye ? 0 : (xe ? 1 : -1);  // blanks last either way
      if (col.num || key === "size") return (x - y) * dir;
      return String(x).localeCompare(String(y), undefined, { sensitivity: "base", numeric: true }) * dir;
    });
  }

  // ---- Formatting ----------------------------------------------------------

  var fmtInt = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
  var fmtCompact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });
  function int(v) { return v == null ? "—" : fmtInt.format(Math.round(v)); }
  function compact(v) { return v == null ? "—" : fmtCompact.format(v); }
  function plural(n, word) { return n + " " + word + (n === 1 ? "" : "s"); }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function strong(text) { return el("strong", null, text); }

  // Status lines are built from text nodes only: file names and messages come
  // from the user's file and are never parsed as HTML.
  function setStatus(kind, parts) {
    var box = $("cr-status");
    if (!box) return;
    box.className = "cr-status" + (kind ? " alert-box alert-" + kind : "");
    box.setAttribute("role", kind === "error" ? "alert" : "status");
    box.textContent = "";
    parts.forEach(function (p) { box.appendChild(typeof p === "string" ? document.createTextNode(p) : p); });
    if (state.rows.length && kind !== "error") {
      var btn = el("button", "btn-ghost btn-sm cr-clear", "Clear data");
      btn.type = "button";
      btn.addEventListener("click", clearData);
      box.appendChild(btn);
    }
  }

  // ---- Rendering -----------------------------------------------------------

  function aggregate(rows) {
    var subs = 0, views = 0, biggest = null, cats = {};
    rows.forEach(function (r) {
      subs += r.subs || 0; views += r.views || 0;
      if (r.subs != null && (!biggest || r.subs > biggest.subs)) biggest = r;
      cats[r.cat || "Uncategorized"] = true;
    });
    return { count: rows.length, subs: subs, views: views, avg: rows.length ? views / rows.length : null, biggest: biggest, cats: Object.keys(cats).length };
  }

  function setStat(id, value, hint) {
    $(id).textContent = value;
    $(id).title = hint || value;
    var h = $(id + "-hint");
    if (h) h.textContent = hint || " ";
  }

  function renderSummary(rows) {
    var a = aggregate(rows), none = !rows.length, all = state.rows.length;
    setStat("cr-s-count", String(a.count), none ? "" : (a.count === all ? "All loaded" : "of " + all + " loaded"));
    setStat("cr-s-subs", none ? "0" : compact(a.subs), none ? "" : int(a.subs));
    setStat("cr-s-views", none ? "0" : compact(a.views), none ? "" : int(a.views));
    setStat("cr-s-avg", none ? "0" : compact(a.avg), none ? "" : int(a.avg));
    setStat("cr-s-big", a.biggest ? a.biggest.name : "—", a.biggest ? compact(a.biggest.subs) + " subscribers" : "");
    setStat("cr-s-cats", none ? "0" : String(a.cats), "");
    $("cr-scope").textContent = !all ? "Nothing loaded"
      : rows.length === all ? "All " + plural(all, "channel")
      : "Filtered: " + rows.length + " of " + plural(all, "channel");
  }

  function renderBreakdown() {
    // Search and size apply; category does not, so every angle stays clickable.
    var rows = filtered(false), groups = {}, body = $("cr-breakdown-body");
    rows.forEach(function (r) { var k = r.cat || "Uncategorized"; (groups[k] = groups[k] || []).push(r); });
    var total = 0, max = 0;
    var list = Object.keys(groups).map(function (k) {
      var g = aggregate(groups[k]); g.name = k; total += g.subs; if (g.subs > max) max = g.subs; return g;
    }).sort(function (a, b) { return b.subs - a.subs || a.name.localeCompare(b.name); });

    body.textContent = "";
    var empty = $("cr-breakdown-empty");
    empty.hidden = list.length > 0;
    empty.textContent = state.rows.length ? "No channels match the current search." : "The breakdown fills in once a file is loaded.";

    list.forEach(function (g) {
      var tr = el("tr", state.cat === g.name ? "is-selected" : null);
      tr.tabIndex = 0;
      tr.setAttribute("aria-label", (state.cat === g.name ? "Show all categories" : "Filter the table to " + g.name));
      tr.appendChild(el("td", "cr-strong", g.name));
      tr.appendChild(el("td", "numeric", String(g.count)));
      tr.appendChild(el("td", "numeric", int(g.subs)));
      var td = el("td"), wrap = el("div", "cr-share"), track = el("div", "cr-share-track"), fill = el("div", "cr-share-fill");
      fill.style.width = (max ? Math.max(2, g.subs / max * 100) : 0) + "%";
      track.appendChild(fill); wrap.appendChild(track);
      wrap.appendChild(el("span", "numeric tiny", total ? (g.subs / total * 100).toFixed(1) + "%" : "—"));
      td.appendChild(wrap); tr.appendChild(td);
      tr.appendChild(el("td", "numeric", int(g.views)));
      tr.appendChild(el("td", "numeric", int(g.avg)));
      tr.appendChild(el("td", null, g.biggest ? g.biggest.name : "—"));
      var pick = function () { setCategory(state.cat === g.name ? "" : g.name); };
      tr.addEventListener("click", pick);
      tr.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
      body.appendChild(tr);
    });
  }

  function renderHead() {
    var row = $("cr-head-row");
    row.textContent = "";
    COLUMNS.forEach(function (c) {
      var th = el("th", c.num ? "numeric" : null);
      th.scope = "col";
      if (c.nosort) { th.textContent = c.label; row.appendChild(th); return; }
      var active = state.sortKey === c.key;
      if (active) th.setAttribute("aria-sort", state.sortDir === 1 ? "ascending" : "descending");
      var btn = el("button", "cr-sort");
      btn.type = "button";
      btn.appendChild(el("span", null, c.label));
      btn.appendChild(el("span", "cr-arrow", active ? (state.sortDir === 1 ? "▲" : "▼") : "▲▼"));
      btn.setAttribute("aria-label", "Sort by " + c.label);
      btn.addEventListener("click", function () {
        if (state.sortKey === c.key) state.sortDir = -state.sortDir;
        else { state.sortKey = c.key; state.sortDir = (c.num || c.key === "size") ? -1 : 1; }  // numbers: biggest first
        state.page = 1;
        render();
      });
      th.appendChild(btn);
      row.appendChild(th);
    });
  }

  function cell(tr, cls, content) {
    var td = el("td", cls);
    if (typeof content === "string") td.textContent = content; else td.appendChild(content);
    tr.appendChild(td);
  }

  function renderTable(rows) {
    var body = $("cr-body");
    body.textContent = "";
    var pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
    if (state.page > pages) state.page = pages;
    var start = (state.page - 1) * PAGE_SIZE;

    rows.slice(start, start + PAGE_SIZE).forEach(function (r) {
      var tr = el("tr");
      cell(tr, "numeric mono cr-faint", r.n == null ? "—" : String(r.n));
      cell(tr, "cr-strong cr-name", r.name);
      if (r.link) {
        var a = el("a", "btn-secondary btn-sm cr-visit", "🔗 Visit");
        a.href = r.link; a.target = "_blank"; a.rel = "noopener noreferrer"; a.title = r.link;
        a.setAttribute("aria-label", "Visit " + r.name + " on YouTube (opens in a new tab)");
        cell(tr, null, a);
      } else cell(tr, "cr-faint", "—");
      cell(tr, "cr-angle", r.angle || "—");
      cell(tr, "muted cr-cat", r.cat || "—");
      cell(tr, "numeric", int(r.videos));
      cell(tr, "numeric", int(r.subs));
      cell(tr, "numeric", int(r.views));
      cell(tr, "numeric", int(r.avg));
      cell(tr, null, r.size ? el("span", "badge badge-" + (SIZE_TONE[r.size] || "slate"), r.size) : el("span", "cr-faint", "—"));
      cell(tr, "muted cr-notes", r.notes || "—");
      body.appendChild(tr);
    });

    var noRows = !rows.length, nothingLoaded = !state.rows.length;
    $("cr-empty").hidden = !noRows;
    $("cr-sample").hidden = !nothingLoaded;
    $("cr-empty-title").textContent = nothingLoaded ? "Upload a CSV or XLSX file to populate the research table" : "No channels match these filters";
    $("cr-empty-text").textContent = nothingLoaded ? "Use the upload area above, or drop a file onto it." : "Try a different search term, or clear the filters.";

    $("cr-count").textContent = "Showing " + rows.length + " of " + plural(state.rows.length, "channel");
    $("cr-page").textContent = "Page " + state.page + " of " + pages;
    $("cr-prev").disabled = state.page <= 1;
    $("cr-next").disabled = state.page >= pages;
  }

  function renderFilters() {
    var sel = $("cr-cat"), cats = {};
    state.rows.forEach(function (r) { if (r.cat) cats[r.cat] = true; });
    var names = Object.keys(cats).sort(function (a, b) { return a.localeCompare(b); });
    if (state.cat && names.indexOf(state.cat) < 0) state.cat = "";
    sel.textContent = "";
    var all = el("option", null, "All categories"); all.value = ""; sel.appendChild(all);
    names.forEach(function (n) { var o = el("option", null, n); o.value = n; sel.appendChild(o); });
    sel.value = state.cat;
    $("cr-size").value = state.size;
    $("cr-reset").hidden = !(state.q || state.cat || state.size);
    var off = !state.rows.length;
    $("cr-q").disabled = off; sel.disabled = off; $("cr-size").disabled = off;
    var s = state.source;
    $("cr-source").textContent = !s ? "No file loaded" : (s.sample ? "Sample · " : "") + s.file + (s.sheet ? " · " + s.sheet : "");
  }

  // The student's "Submit this sheet for review" form, when the page offers it.
  var submitForm = $("cr-submit");
  var nicheAuto = true;
  function topCategory() {
    var counts = {}, best = "", most = 0;
    state.rows.forEach(function (r) {
      if (!r.cat) return;
      counts[r.cat] = (counts[r.cat] || 0) + 1;
      if (counts[r.cat] > most) { most = counts[r.cat]; best = r.cat; }
    });
    return best;
  }
  function renderSubmit() {
    if (!submitForm) return;
    var sample = state.source && state.source.sample;
    submitForm.hidden = !state.rows.length || !!sample;
    if (submitForm.hidden) return;
    var withLink = state.rows.filter(function (r) { return r.link; }).length;
    $("cr-submit-summary").textContent =
      plural(state.rows.length, "channel") + " from " + (state.source ? state.source.file : "your sheet") +
      " will be sent to your instructor as your next research attempt. " +
      (withLink === state.rows.length ? "" : (state.rows.length - withLink) + " without a channel link will appear in the sheet but cannot be checked against the niche rules.");
    var niche = $("id_sheet-niche");
    if (niche && nicheAuto) niche.value = topCategory();
  }

  function render() {
    renderFilters();
    renderSubmit();
    var rows = filtered();
    renderSummary(rows);
    renderBreakdown();
    renderHead();
    renderTable(sorted(rows));
  }
  function setCategory(name) { state.cat = name; state.page = 1; render(); }

  // ---- Wiring --------------------------------------------------------------

  var drop = $("cr-drop"), input = $("cr-file");
  if (drop) {
  drop.addEventListener("click", function () { input.click(); });
  drop.addEventListener("keydown", function (e) {
    if (e.target === drop && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); input.click(); }
  });
  $("cr-pick").addEventListener("click", function (e) { e.stopPropagation(); input.click(); });
  input.addEventListener("change", function () { handleFile(input.files[0]); input.value = ""; });
  ["dragenter", "dragover"].forEach(function (t) {
    drop.addEventListener(t, function (e) { e.preventDefault(); drop.classList.add("is-over"); });
  });
  ["dragleave", "dragend"].forEach(function (t) {
    drop.addEventListener(t, function (e) { if (!drop.contains(e.relatedTarget)) drop.classList.remove("is-over"); });
  });
  drop.addEventListener("drop", function (e) {
    e.preventDefault();
    drop.classList.remove("is-over");
    var files = e.dataTransfer && e.dataTransfer.files;
    if (files && files.length > 1) setStatus("error", ["Drop one file at a time."]);
    else if (files && files.length) handleFile(files[0]);
  });
  // A file dropped just beside the zone must not navigate away from the page.
  window.addEventListener("dragover", function (e) { e.preventDefault(); });
  window.addEventListener("drop", function (e) { if (!drop.contains(e.target)) e.preventDefault(); });
  }

  if (submitForm) {
    var nicheInput = $("id_sheet-niche");
    if (nicheInput) nicheInput.addEventListener("input", function () { nicheAuto = nicheInput.value.trim() === ""; });
    submitForm.addEventListener("submit", function () {
      $("id_sheet-sheet").value = JSON.stringify({
        file: state.source ? state.source.file : null,
        sheet: state.source ? state.source.sheet : null,
        rows: state.rows
      });
    });
  }

  var qTimer;
  $("cr-q").addEventListener("input", function (e) {
    clearTimeout(qTimer);
    qTimer = setTimeout(function () { state.q = e.target.value.trim(); state.page = 1; render(); }, 120);
  });
  $("cr-cat").addEventListener("change", function (e) { setCategory(e.target.value); });
  $("cr-size").addEventListener("change", function (e) { state.size = e.target.value; state.page = 1; render(); });
  $("cr-reset").addEventListener("click", function () {
    state.q = ""; state.cat = ""; state.size = ""; state.page = 1; $("cr-q").value = ""; render();
  });
  $("cr-prev").addEventListener("click", function () { if (state.page > 1) { state.page--; render(); } });
  $("cr-next").addEventListener("click", function () { state.page++; render(); });
  $("cr-sample").addEventListener("click", loadSample);

  if (READONLY) {
    try {
      var submitted = JSON.parse(document.getElementById(READONLY).textContent || "null");
      if (submitted && Array.isArray(submitted.rows)) {
        state.rows = submitted.rows;
        state.source = { file: submitted.file || "Submitted sheet", sheet: submitted.sheet || null };
      }
    } catch (e) { /* malformed sheet: show the empty state */ }
    render();
    return;
  }

  try {
    var saved = JSON.parse(localStorage.getItem(STORE_KEY) || "null");
    if (saved && Array.isArray(saved.rows) && saved.rows.length) {
      state.rows = saved.rows; state.source = saved.source || null;
      setStatus("success", ["Restored ", strong(state.source ? state.source.file : "your last file"), " · " + plural(saved.rows.length, "channel") + " from your last visit."]);
    }
  } catch (e) { /* nothing saved, or storage blocked */ }

  render();
})();
