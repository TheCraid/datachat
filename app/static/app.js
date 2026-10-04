/* DataChat front end: plain JavaScript, no build step. */
(() => {
  "use strict";

  // ------------------------------------------------------------------ helpers
  const view = document.getElementById("view");
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const uid = () => (window.crypto && crypto.randomUUID ? crypto.randomUUID().replace(/-/g, "") : Math.random().toString(36).slice(2) + Date.now().toString(36));
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch { /* storage unavailable */ } },
  };
  let sessionId = store.get("dc_session");
  if (!sessionId || !/^[A-Za-z0-9_-]{8,64}$/.test(sessionId)) { sessionId = uid(); store.set("dc_session", sessionId); }

  const threads = {};          // dataset id -> { convId, cards: [] }
  const datasetCache = {};     // dataset id -> summary

  function toast(msg) {
    const t = document.getElementById("toast");
    t.textContent = msg; t.hidden = false;
    clearTimeout(toast._t); toast._t = setTimeout(() => { t.hidden = true; }, 2600);
  }

  async function api(path, opts = {}) {
    const r = await fetch(path, opts);
    let data = null;
    try { data = await r.json(); } catch { /* not JSON */ }
    if (!r.ok) throw new Error((data && data.detail) || `Request failed (${r.status})`);
    return data;
  }

  const MONEY = /(amount|revenue|sales|pay|price|value|cost|spend|tip|bonus|salary|deduction|payroll|income)/i;
  const NOT_MONEY = /(pct|percent|share|rate|count|qty|quantity|lakh|orders|number|avg_rating|rating)/i;
  const isMoney = (col) => col && MONEY.test(col) && !NOT_MONEY.test(col);
  const isPct = (col) => col && /(pct|percent|share|_rate$)/i.test(col);
  function fmt(v, col) {
    if (v === null || v === undefined) return "–";
    if (typeof v === "number") {
      const abs = Math.abs(v);
      const digits = Number.isInteger(v) ? 0 : abs >= 100 ? 0 : 2;
      const s = v.toLocaleString("en-IN", { maximumFractionDigits: digits, minimumFractionDigits: 0 });
      return isMoney(col) ? "₹" + s : isPct(col) ? s + "%" : s;
    }
    if (typeof v === "boolean") return v ? "Yes" : "No";
    const str = String(v);
    if (/^\d{4}-\d{2}-\d{2}T00:00:00$/.test(str)) return str.slice(0, 10);
    return str;
  }
  function fmtShort(v, col) {
    if (typeof v !== "number") return fmt(v, col);
    const a = Math.abs(v), pre = isMoney(col) ? "₹" : "";
    if (a >= 1e7) return pre + (v / 1e7).toFixed(a >= 1e8 ? 0 : 1) + " Cr";
    if (a >= 1e5) return pre + (v / 1e5).toFixed(a >= 1e6 ? 0 : 1) + " L";
    if (a >= 1e3) return pre + (v / 1e3).toFixed(a >= 1e4 ? 0 : 1) + "k";
    return pre + (Number.isInteger(v) ? v : v.toFixed(1));
  }
  const nice = (name) => String(name).replace(/_/g, " ");

  // ------------------------------------------------------------------ icons
  const ICON = {
    upload: '<svg width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="color:var(--accent)"><path d="M12 16V4"/><path d="M7 9l5-5 5 5"/><path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"/></svg>',
    store: '<svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="#1D4ED8" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="9" cy="20" r="1.4"/><circle cx="18" cy="20" r="1.4"/><path d="M2 3h3l2.6 12.4a1.5 1.5 0 0 0 1.5 1.1h8.6a1.5 1.5 0 0 0 1.5-1.1L21 8H6"/></svg>',
    food: '<svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="#C2410C" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 11h18"/><path d="M5 11a7 7 0 0 1 14 0"/><path d="M4 15h16l-1.5 4h-13z"/></svg>',
    hr: '<svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="#0F766E" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="9" cy="8" r="3.2"/><path d="M3 20a6 6 0 0 1 12 0"/><path d="M16 4.5a3 3 0 0 1 0 6"/><path d="M18 14.5a5.5 5.5 0 0 1 3 5.5"/></svg>',
    bulb: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#C2410C" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 18h6"/><path d="M10 21h4"/><path d="M12 3a6 6 0 0 0-3.5 10.9c.6.4 1 1.1 1 1.8V16h5v-.3c0-.7.4-1.4 1-1.8A6 6 0 0 0 12 3z"/></svg>',
    warn: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#C2410C" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3l9.5 17h-19z"/><path d="M12 10v4"/><path d="M12 17.5v.01"/></svg>',
    file: '<svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="#4B5563" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8z"/><path d="M14 3v5h5"/><path d="M8 13h8M8 17h5"/></svg>',
  };

  // ------------------------------------------------------------------ router
  function parseHash() {
    const h = location.hash.replace(/^#\/?/, "");
    const [route, qs] = h.split("?");
    return { route: route || "", params: new URLSearchParams(qs || "") };
  }
  function setActive(route) {
    document.querySelectorAll(".nav a[data-route]").forEach((a) => a.classList.toggle("active", a.dataset.route === route));
  }
  async function router() {
    const { route, params } = parseHash();
    setActive(route);
    window.scrollTo(0, 0);
    try {
      if (route === "workspace") await renderWorkspace(params.get("ds") || store.get("dc_last_ds"));
      else if (route === "dashboard") await renderDashboard();
      else if (route === "benchmark") await renderBenchmark();
      else await renderStart();
    } catch (e) {
      view.innerHTML = `<div class="wrap"><div class="panel"><strong>Something went wrong</strong><span class="note">${esc(e.message)}</span><a class="btn" href="#/">Back to start</a></div></div>`;
    }
  }
  window.addEventListener("hashchange", router);

  // ------------------------------------------------------------------ start page
  async function renderStart() {
    document.title = "DataChat – Ask your data anything";
    const samples = await api("/api/datasets");
    const subtitle = (d) => d.tables.map((t) => t.name).join(" · ");
    view.innerHTML = `
    <div class="wrap">
      <section class="hero">
        <p class="eyebrow">Text-to-SQL analyst</p>
        <h1>Ask your data anything.<br>Get charts, not code.</h1>
        <p>Upload a spreadsheet, ask a question in plain English, and get the answer as a chart, a short insight and the exact SQL that produced it.</p>
      </section>
      <section class="split">
        <div class="drop" id="drop">
          ${ICON.upload}
          <div class="title">Drop a CSV or Excel file here</div>
          <div class="hint">Up to 10 MB. Each sheet becomes a table. Uploads are kept in memory for an hour, then deleted.</div>
          <label for="file" class="btn">Choose a file</label>
          <input id="file" class="sr-only" type="file" accept=".csv,.xlsx">
          <div class="status" id="upstatus" aria-live="polite"></div>
        </div>
        <div class="samples">
          <div class="label">Or start with a sample dataset</div>
          ${samples.map((d) => `
            <a class="sample" href="#/workspace?ds=${esc(d.id)}">
              ${ICON[d.id] || ICON.file}
              <div><div class="t">${esc(d.title)}</div><div class="s">${esc(subtitle(d))}</div></div>
              <span class="go">Open</span>
            </a>`).join("")}
        </div>
      </section>
      <section class="steps3">
        <div class="step3"><span class="n">01 · ASK</span><span class="h">Plain English in</span><span class="d">Ask follow-ups like a conversation: “now only for Karnataka”.</span></div>
        <div class="step3"><span class="n">02 · CHECK</span><span class="h">Safe, self-correcting SQL</span><span class="d">Read-only queries only. If a query fails, the agent reads the error and fixes it.</span></div>
        <div class="step3"><span class="n">03 · SEE</span><span class="h">Chart, insight, SQL</span><span class="d">Every number in the insight is checked against the query result.</span></div>
      </section>
      <footer class="foot"><span>Built with FastAPI, LangGraph and DuckDB. All sample data is synthetic.</span><a href="#/benchmark">How accurate is it? See the benchmark</a></footer>
    </div>`;
    const drop = document.getElementById("drop"), input = document.getElementById("file");
    const status = document.getElementById("upstatus");
    const send = async (file) => {
      if (!file) return;
      status.className = "status"; status.textContent = `Uploading ${file.name}…`;
      const fd = new FormData(); fd.append("file", file);
      try {
        const ds = await api("/api/upload", { method: "POST", body: fd });
        datasetCache[ds.id] = ds;
        location.hash = `#/workspace?ds=${encodeURIComponent(ds.id)}`;
      } catch (e) { status.className = "status err"; status.textContent = e.message; }
    };
    input.addEventListener("change", () => send(input.files[0]));
    ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
    ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
    drop.addEventListener("drop", (e) => send(e.dataTransfer.files[0]));
  }

  // ------------------------------------------------------------------ workspace
  async function renderWorkspace(dsId) {
    if (!dsId) { location.hash = "#/"; return; }
    let ds = datasetCache[dsId];
    if (!ds) {
      try { ds = await api(`/api/datasets/${encodeURIComponent(dsId)}`); }
      catch (e) {
        view.innerHTML = `<div class="wrap"><div class="panel"><strong>Dataset not available</strong><span class="note">${esc(e.message)}</span><a class="btn" href="#/">Choose a dataset</a></div></div>`;
        return;
      }
      datasetCache[dsId] = ds;
    }
    store.set("dc_last_ds", dsId);
    document.title = `${ds.title} – DataChat`;
    const th = threads[dsId] || (threads[dsId] = { convId: uid(), cards: [] });
    const suggestions = ds.suggestions && ds.suggestions.length ? ds.suggestions : defaultSuggestions(ds);
    view.innerHTML = `
    <div class="ws">
      <aside class="side">
        <div class="panel">
          <div class="panel-h"><strong>${esc(ds.title)}</strong><a href="#/" style="font-size:13px">Change</a></div>
          <div class="cap">Tables</div>
          ${ds.tables.map((t, i) => `
            <details class="tbl" ${i === 0 ? "open" : ""}>
              <summary><span>${esc(t.name)}</span><span class="rows">${t.rows.toLocaleString("en-IN")}</span></summary>
              <div class="cols">${t.columns.map((c) => `<div title="${esc(c.description || "")}"><span>${esc(c.name)}</span><span>${esc(c.kind)}</span></div>`).join("")}</div>
            </details>`).join("")}
        </div>
        <div class="panel">
          <div class="cap">Try asking</div>
          ${suggestions.map((s) => `<button type="button" class="suggest" data-q="${esc(s)}">${esc(s)}</button>`).join("")}
        </div>
      </aside>
      <section class="main" aria-label="Conversation">
        <div class="thread" id="thread"></div>
        <form class="ask" id="askform">
          <label for="ask" class="sr-only">Ask a question about ${esc(ds.title)}</label>
          <input id="ask" type="text" autocomplete="off" maxlength="500" placeholder="Ask a question… e.g. ${esc(suggestions[0] || "")}">
          <button class="btn" type="submit" id="askbtn">Ask</button>
        </form>
        <div class="under"><span>Follow-ups use the last ${3} questions as context.</span><button type="button" class="linkbtn" id="newconv">New conversation</button></div>
      </section>
    </div>`;
    const thread = document.getElementById("thread");
    const drawAll = () => {
      thread.innerHTML = th.cards.length ? "" : `<div class="empty-thread">Ask a question about <strong>${esc(ds.title)}</strong>, or pick a suggestion.</div>`;
      th.cards.forEach((c) => { thread.appendChild(cardShell(c)); });
    };
    drawAll();
    const form = document.getElementById("askform"), input = document.getElementById("ask");
    form.addEventListener("submit", (e) => { e.preventDefault(); const q = input.value.trim(); if (q) { input.value = ""; ask(ds, th, q, thread); } });
    view.querySelectorAll(".suggest").forEach((b) => b.addEventListener("click", () => ask(ds, th, b.dataset.q, thread)));
    document.getElementById("newconv").addEventListener("click", () => { th.convId = uid(); th.cards = []; drawAll(); input.focus(); });
    thread.addEventListener("click", (e) => onCardAction(e, ds, th));
    input.focus();
  }

  // Suggestions for uploaded files: skip id-like columns and use low-cardinality text columns as categories.
  function defaultSuggestions(ds) {
    const t = ds.tables[0];
    const idLike = (c) => /(^|[\s_])(id|code|no|number|key)$/i.test(c.name) || /^id([\s_]|$)/i.test(c.name);
    const cats = t.columns.filter((c) => c.kind === "text" && !idLike(c) && c.distinct >= 2 && c.distinct <= Math.min(30, t.rows / 2))
      .sort((a, b) => a.distinct - b.distinct);
    const nums = t.columns.filter((c) => c.kind === "number" && !idLike(c));
    const num = nums.find((c) => MONEY.test(c.name)) || nums[0];
    const date = t.columns.find((c) => c.kind === "date");
    const [cat, cat2] = cats;
    const out = [`How many rows are in ${t.name}?`];
    if (cat && num) out.push(`Average ${nice(num.name)} by ${nice(cat.name)}`);
    if (cat && num) out.push(`Which ${nice(cat.name)} has the highest total ${nice(num.name)}?`);
    if (date) out.push(`How many ${t.name} per year of ${nice(date.name)}?`);
    else if (cat2) out.push(`Count of rows by ${nice(cat2.name)}`);
    else if (nums.length >= 2) out.push(`Does ${nice(nums.find((c) => c !== num).name)} affect ${nice(num.name)}?`);
    return out;
  }

  const FRIENDLY = { guardrail: "Checking the question", write_sql: "Writing SQL", check_sql: "Checking the SQL", run_sql: "Running the query",
    fix_sql: "Fixing the query", give_up: "Stopping", make_chart: "Choosing a chart", write_insight: "Writing the insight", cache: "Reusing an earlier answer" };

  async function ask(ds, th, question, thread) {
    if (th.busy) return;
    th.busy = true;
    const btn = document.getElementById("askbtn"); if (btn) btn.disabled = true;
    const card = { id: uid(), question, status: "running", steps: [], sql: null, result: null, chart: null, insight: null,
      verified: true, message: "", metrics: null, tab: "chart", explain: null };
    th.cards.push(card);
    if (thread.querySelector(".empty-thread")) thread.innerHTML = "";
    thread.appendChild(cardShell(card));
    scrollToCard(card);
    try {
      const resp = await fetch("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dataset_id: ds.id, question, session_id: sessionId, conversation_id: th.convId }) });
      if (!resp.ok) {
        let msg = `Request failed (${resp.status})`;
        try { msg = (await resp.json()).detail || msg; } catch { /* ignore */ }
        throw new Error(msg);
      }
      const reader = resp.body.getReader(), dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf("\n\n")) >= 0) {
          const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
          let ev = "message", data = "";
          chunk.split("\n").forEach((line) => {
            if (line.startsWith("event:")) ev = line.slice(6).trim();
            else if (line.startsWith("data:")) data += line.slice(5).trim();
          });
          if (data) handleEvent(card, ev, JSON.parse(data));
        }
      }
      if (card.status === "running") { card.status = "error"; card.message = "The connection closed before the answer finished."; }
    } catch (e) {
      card.status = "error"; card.message = e.message;
    } finally {
      th.busy = false; if (btn) btn.disabled = false;
      redraw(card);
    }
  }

  function handleEvent(card, ev, data) {
    if (ev === "step") card.steps.push(data);
    else if (ev === "sql") card.sql = data.sql;
    else if (ev === "result") card.result = data;
    else if (ev === "chart") { card.chart = data; if (data.type === "table" || data.type === "empty") card.tab = "table"; }
    else if (ev === "insight") { card.insight = data.text; card.verified = data.verified; card.reason = data.reason; }
    else if (ev === "done") { card.status = data.status; card.message = data.message; card.metrics = data.metrics; if (data.sql) card.sql = data.sql; }
    else if (ev === "error") { card.status = "error"; card.message = data.message; }
    if (card.status !== "answered" && card.status !== "running" && card.tab === "chart" && !card.result) card.tab = "steps";
    redraw(card);
  }

  function cardShell(card) {
    const el = document.createElement("div");
    el.id = "card-" + card.id;
    el.style.display = "contents";
    el.innerHTML = cardHTML(card);
    return el;
  }
  function redraw(card) {
    const el = document.getElementById("card-" + card.id);
    if (el) el.innerHTML = cardHTML(card);
  }
  function scrollToCard(card) {
    const el = document.getElementById("card-" + card.id);
    if (el && el.firstElementChild) el.firstElementChild.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function cardHTML(c) {
    const last = c.steps[c.steps.length - 1];
    let head;
    if (c.status === "running") {
      head = `<div class="insight"><div class="live"><span class="spinner" aria-hidden="true"></span><span>${esc(last ? `${FRIENDLY[last.step] || last.step}: ${last.detail}` : "Starting…")}</span></div></div>`;
    } else if (c.status === "answered") {
      head = `<div class="insight">${ICON.bulb}<div><div class="cap">Insight</div><div class="txt">${esc(c.insight || "")}</div>
        ${c.verified ? "" : `<div class="note">${c.reason === "unavailable"
          ? "Plain summary: the insight model did not respond in time. The chart and table are complete."
          : "Plain summary: the model's wording contained numbers that could not be checked against the result."}</div>`}</div></div>`;
    } else {
      const title = { blocked: "Blocked", not_answerable: "Not answerable from this data", failed: "Could not answer", error: "Error" }[c.status] || "Error";
      head = `<div class="insight bad">${ICON.warn}<div><div class="cap">${esc(title)}</div><div class="txt">${esc(c.message || "Something went wrong.")}</div></div></div>`;
    }
    const hasResult = !!c.result;
    const tabs = [["chart", "Chart", hasResult], ["table", "Table", hasResult], ["sql", "SQL", !!c.sql], ["steps", "Steps", true]];
    const tabBar = `<div class="tabs" role="tablist" aria-label="Answer views">${tabs.filter((t) => t[2]).map(([id, label]) =>
      `<button type="button" role="tab" class="tab" aria-selected="${c.tab === id}" data-act="tab" data-tab="${id}" data-card="${c.id}">${label}</button>`).join("")}</div>`;
    let pane = "";
    if (c.tab === "chart" && hasResult) pane = renderChart(c.chart || { type: "table" }, c.result);
    else if (c.tab === "table" && hasResult) pane = renderTable(c.result);
    else if (c.tab === "sql" && c.sql) pane = `<pre class="sql">${highlightSQL(c.sql)}</pre>${c.explain ? `<div class="explain">${esc(c.explain)}</div>` : ""}`;
    else pane = renderSteps(c.steps);
    const m = c.metrics;
    const secs = m ? `${(m.latency_ms / 1000).toFixed(1)} s` : "";
    const meta = !m ? "" : m.cached ? `cached answer · no new model calls · ${secs}`
      : `${(m.models || []).join(", ") || "no model"} · ${m.llm_calls} LLM call${m.llm_calls === 1 ? "" : "s"} · ${secs}`;
    const actions = hasResult && c.status === "answered" ? `
      <div class="actions"><div class="btns">
        <button type="button" class="btn ghost" data-act="copy" data-card="${c.id}">Copy SQL</button>
        <button type="button" class="btn ghost" data-act="csv" data-card="${c.id}">Download CSV</button>
        <button type="button" class="btn ghost" data-act="explain" data-card="${c.id}">Explain this SQL</button>
        <button type="button" class="btn dark" data-act="pin" data-card="${c.id}">Pin to dashboard</button>
      </div><span class="meta">${esc(meta)}</span></div>` : (meta ? `<div class="actions"><span></span><span class="meta">${esc(meta)}</span></div>` : "");
    return `<div class="q">${esc(c.question)}</div>
      <article class="card" aria-busy="${c.status === "running"}">${head}${tabBar}<div class="pane" role="tabpanel">${pane}</div>${actions}</article>`;
  }

  async function onCardAction(e, ds, th) {
    const b = e.target.closest("[data-act]");
    if (!b) return;
    const card = th.cards.find((c) => c.id === b.dataset.card);
    if (!card) return;
    const act = b.dataset.act;
    if (act === "tab") { card.tab = b.dataset.tab; redraw(card); return; }
    if (act === "copy") {
      try { await navigator.clipboard.writeText(card.sql || ""); toast("SQL copied"); } catch { toast("Copy is not available in this browser"); }
    } else if (act === "csv") {
      const r = card.result, q = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
      const csv = [r.columns.map((c) => q(c.name)).join(","), ...r.rows.map((row) => row.map(q).join(","))].join("\n");
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
      a.download = "datachat-result.csv"; a.click(); URL.revokeObjectURL(a.href);
    } else if (act === "explain") {
      b.disabled = true; card.tab = "sql"; card.explain = "Explaining…"; redraw(card);
      try { card.explain = (await api("/api/explain", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sql: card.sql }) })).explanation; }
      catch (err) { card.explain = err.message; }
      redraw(card);
    } else if (act === "pin") {
      try {
        await api("/api/pins", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ session_id: sessionId, dataset_id: ds.id, question: card.question, sql: card.sql }) });
        toast("Pinned to your dashboard");
      } catch (err) { toast(err.message); }
    }
  }

  function renderSteps(steps) {
    if (!steps.length) return `<p class="note">Waiting for the first step…</p>`;
    return `<ol class="steps">${steps.map((s) => `<li><span class="st ${esc(s.status)}">${esc(s.status.toUpperCase())}</span><span>${esc(FRIENDLY[s.step] || s.step)}: ${esc(s.detail)}</span></li>`).join("")}</ol>`;
  }

  function renderTable(r) {
    if (!r.rows.length) return `<p class="note">The query returned no rows.</p>`;
    const head = r.columns.map((c) => `<th scope="col">${esc(c.name)}</th>`).join("");
    const body = r.rows.map((row) => `<tr>${row.map((v, i) => `<td class="${typeof v === "number" ? "num" : ""}">${esc(fmt(v, r.columns[i].name))}</td>`).join("")}</tr>`).join("");
    return `<div class="tablebox"><table class="data"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>
      ${r.truncated ? `<p class="note">Showing the first ${r.rows.length.toLocaleString("en-IN")} rows.</p>` : ""}`;
  }

  // ------------------------------------------------------------------ charts
  const SERIES = ["var(--accent)", "var(--warm)", "var(--ok)"];
  function colIndex(r, name) { return r.columns.findIndex((c) => c.name === name); }

  function renderChart(spec, r) {
    if (!r.rows.length || spec.type === "empty") return `<p class="note">The query returned no rows, so there is nothing to plot.</p>`;
    if (spec.type === "kpi") {
      const li = spec.label ? colIndex(r, spec.label) : -1;
      return `<div class="kpis">${spec.values.map((n) => { const i = colIndex(r, n); return `<div class="kpi"><div class="v">${esc(fmt(r.rows[0][i], n))}</div><div class="l">${esc(nice(n))}${li >= 0 ? ` · ${esc(fmt(r.rows[0][li]))}` : ""}</div></div>`; }).join("")}</div>`;
    }
    if (spec.type === "bar" || spec.type === "diverging") {
      const xi = colIndex(r, spec.x), yi = colIndex(r, spec.y[0]);
      const vals = r.rows.map((row) => (typeof row[yi] === "number" ? row[yi] : 0));
      const max = Math.max(...vals.map(Math.abs), 1e-9);
      const cap = `<div class="chart-cap">${esc(nice(spec.y[0]))} by ${esc(nice(spec.x))}</div>`;
      if (spec.type === "diverging") {
        return cap + `<div class="div">${r.rows.map((row, k) => { const v = vals[k], w = (Math.abs(v) / max * 100).toFixed(1);
          return `<span class="lab" title="${esc(fmt(row[xi]))}">${esc(fmt(row[xi]))}</span><div class="neg">${v < 0 ? `<i style="width:${w}%"></i>` : ""}</div><div class="pos">${v > 0 ? `<i style="width:${w}%"></i>` : ""}</div><span class="val ${v < 0 ? "n" : "p"}">${esc(fmt(v, spec.y[0]))}</span>`; }).join("")}</div>`;
      }
      return cap + `<div class="hbars">${r.rows.map((row, k) => `<span class="lab" title="${esc(fmt(row[xi]))}">${esc(fmt(row[xi]))}</span><div class="track"><div class="fill" style="width:${(Math.abs(vals[k]) / max * 100).toFixed(1)}%"></div></div><span class="val">${esc(fmt(vals[k], spec.y[0]))}</span>`).join("")}</div>`;
    }
    if (spec.type === "line") return lineChart(spec, r);
    if (spec.type === "scatter") return scatterChart(spec, r);
    return `<p class="note">This result has no clear chart shape, so it is shown as a table.</p>` + renderTable(r);
  }

  function axisTicks(min, max) {
    if (min === max) { max = min + 1; }
    const span = max - min, step = Math.pow(10, Math.floor(Math.log10(span / 3)));
    const s = [1, 2, 5, 10].map((m) => m * step).find((x) => span / x <= 5) || step * 10;
    const lo = Math.floor(min / s) * s, hi = Math.ceil(max / s) * s, out = [];
    for (let v = lo; v <= hi + s / 2; v += s) out.push(+v.toFixed(10));
    return out;
  }

  function lineChart(spec, r) {
    const W = 600, H = 250, L = 64, R = 16, T = 12, B = 34;
    const xi = colIndex(r, spec.x), ys = spec.y.map((n) => colIndex(r, n));
    const rows = r.rows.filter((row) => row[xi] !== null);
    const all = rows.flatMap((row) => ys.map((i) => row[i])).filter((v) => typeof v === "number");
    if (!all.length) return renderTable(r);
    const ticks = axisTicks(Math.min(0, ...all), Math.max(...all));
    const y0 = ticks[0], y1 = ticks[ticks.length - 1];
    const px = (k) => L + (rows.length === 1 ? (W - L - R) / 2 : k * (W - L - R) / (rows.length - 1));
    const py = (v) => T + (H - T - B) * (1 - (v - y0) / (y1 - y0 || 1));
    const monthly = rows.every((row) => /^\d{4}-\d{2}-01/.test(String(row[xi])));
    const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    const label = (v) => { const s = String(fmt(v)); return monthly ? `${MON[+s.slice(5, 7) - 1]} ${s.slice(0, 4)}` : s.slice(0, 10); };
    const xl = [0, Math.floor((rows.length - 1) / 2), rows.length - 1].filter((v, i, a) => a.indexOf(v) === i);
    const grid = ticks.map((t) => `<line class="grid" x1="${L}" x2="${W - R}" y1="${py(t)}" y2="${py(t)}"/><text x="${L - 8}" y="${py(t) + 4}" text-anchor="end">${esc(fmtShort(t, spec.y[0]))}</text>`).join("");
    const lines = ys.map((yi, s) => {
      const pts = rows.map((row, k) => (typeof row[yi] === "number" ? `${px(k).toFixed(1)},${py(row[yi]).toFixed(1)}` : null)).filter(Boolean).join(" ");
      const dots = rows.length <= 40 ? rows.map((row, k) => typeof row[yi] === "number" ? `<circle cx="${px(k).toFixed(1)}" cy="${py(row[yi]).toFixed(1)}" r="3.5" fill="${SERIES[s]}"><title>${esc(label(row[xi]))}: ${esc(fmt(row[yi], spec.y[s]))}</title></circle>` : "").join("") : "";
      return `<polyline fill="none" stroke="${SERIES[s]}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round" points="${pts}"/>${dots}`;
    }).join("");
    const xlabels = xl.map((k) => `<text x="${px(k)}" y="${H - 10}" text-anchor="${k === 0 ? "start" : k === rows.length - 1 ? "end" : "middle"}">${esc(label(rows[k][xi]))}</text>`).join("");
    const legend = spec.y.length > 1 ? `<div class="legend">${spec.y.map((n, s) => `<span><i style="background:${SERIES[s]}"></i>${esc(nice(n))}</span>`).join("")}</div>` : "";
    return `<div class="chart-cap">${esc(spec.y.map(nice).join(", "))} over ${esc(nice(spec.x))}</div>
      <svg class="plot" viewBox="0 0 ${W} ${H}" role="img" aria-label="Line chart of ${esc(spec.y.map(nice).join(", "))} over ${esc(nice(spec.x))}">
      ${grid}<line class="axis" x1="${L}" x2="${W - R}" y1="${H - B}" y2="${H - B}"/>${lines}${xlabels}</svg>${legend}`;
  }

  function scatterChart(spec, r) {
    const W = 680, H = 280, L = 64, R = 16, T = 12, B = 40;
    const xi = colIndex(r, spec.x), yi = colIndex(r, spec.y[0]);
    const pts = r.rows.filter((row) => typeof row[xi] === "number" && typeof row[yi] === "number");
    if (!pts.length) return renderTable(r);
    const xt = axisTicks(Math.min(...pts.map((p) => p[xi])), Math.max(...pts.map((p) => p[xi])));
    const yt = axisTicks(Math.min(...pts.map((p) => p[yi])), Math.max(...pts.map((p) => p[yi])));
    const px = (v) => L + (W - L - R) * (v - xt[0]) / (xt[xt.length - 1] - xt[0] || 1);
    const py = (v) => T + (H - T - B) * (1 - (v - yt[0]) / (yt[yt.length - 1] - yt[0] || 1));
    const grid = yt.map((t) => `<line class="grid" x1="${L}" x2="${W - R}" y1="${py(t)}" y2="${py(t)}"/><text x="${L - 8}" y="${py(t) + 4}" text-anchor="end">${esc(fmtShort(t, spec.y[0]))}</text>`).join("")
      + xt.map((t) => `<text x="${px(t)}" y="${H - 22}" text-anchor="middle">${esc(fmtShort(t, spec.x))}</text>`).join("");
    const dots = pts.slice(0, 1000).map((p) => `<circle cx="${px(p[xi]).toFixed(1)}" cy="${py(p[yi]).toFixed(1)}" r="4" fill="var(--accent)" fill-opacity=".6"><title>${esc(fmt(p[xi], spec.x))}, ${esc(fmt(p[yi], spec.y[0]))}</title></circle>`).join("");
    return `<div class="chart-cap">${esc(nice(spec.y[0]))} against ${esc(nice(spec.x))}</div>
      <svg class="plot" viewBox="0 0 ${W} ${H}" role="img" aria-label="Scatter plot of ${esc(nice(spec.y[0]))} against ${esc(nice(spec.x))}">${grid}
      <line class="axis" x1="${L}" x2="${W - R}" y1="${H - B}" y2="${H - B}"/>${dots}
      <text x="${(L + W - R) / 2}" y="${H - 4}" text-anchor="middle">${esc(nice(spec.x))}</text></svg>`;
  }

  const KW = "SELECT|FROM|WHERE|GROUP|BY|ORDER|HAVING|LIMIT|OFFSET|JOIN|LEFT|RIGHT|INNER|OUTER|FULL|CROSS|ON|USING|AS|AND|OR|NOT|IN|IS|NULL|CASE|WHEN|THEN|ELSE|END|WITH|UNION|ALL|DISTINCT|ASC|DESC|BETWEEN|LIKE|ILIKE|FILTER|OVER|PARTITION|QUALIFY|INTERVAL|CAST|TRUE|FALSE|EXCEPT|INTERSECT|ROUND|SUM|COUNT|AVG|MIN|MAX|DATE_TRUNC|EXTRACT|COALESCE|NULLIF";
  const TOKEN = new RegExp(`('(?:[^']|'')*')|(\\b\\d+(?:\\.\\d+)?\\b)|(\\b(?:${KW})\\b)`, "gi");
  function layoutSQL(sql) {
    const s = String(sql || "").trim();
    if (s.includes("\n")) return s;
    return s.replace(/\s+(FROM|WHERE|GROUP BY|ORDER BY|HAVING|LIMIT|(?:LEFT |RIGHT |INNER |FULL )?JOIN|UNION(?: ALL)?|QUALIFY|SELECT)\b/gi, "\n$1");
  }
  function highlightSQL(raw) {
    const sql = layoutSQL(raw);
    let out = "", last = 0;
    String(sql).replace(TOKEN, (m, str, num, kw, idx) => {
      out += esc(sql.slice(last, idx));
      out += str ? `<span class="st">${esc(m)}</span>` : num ? `<span class="nm">${esc(m)}</span>` : `<span class="kw">${esc(m)}</span>`;
      last = idx + m.length;
      return m;
    });
    return out + esc(sql.slice(last));
  }

  // ------------------------------------------------------------------ dashboard
  async function renderDashboard() {
    document.title = "Dashboard – DataChat";
    view.innerHTML = `<div class="wrap wide"><p class="note">Loading your pinned answers…</p></div>`;
    const pins = await api(`/api/pins?session_id=${encodeURIComponent(sessionId)}`);
    const tile = (p) => {
      let body;
      if (p.state === "ok") body = renderChart(p.chart, p.result);
      else if (p.state === "expired") body = `<p class="note">The uploaded dataset for this chart has expired. Upload the file again and re-pin the question.</p>`;
      else body = `<p class="note">${esc(p.message || "This query could not run.")}</p>`;
      return `<section class="tile"><div class="tile-h"><div><h2>${esc(p.question)}</h2><span class="sub">${esc(p.dataset_title)}</span></div>
        <button type="button" class="btn ghost" data-unpin="${p.id}" aria-label="Unpin ${esc(p.question)}">Unpin</button></div>${body}</section>`;
    };
    view.innerHTML = `<div class="wrap wide">
      <div class="page-h"><div><h1>Your dashboard</h1><p>${pins.length} pinned answer${pins.length === 1 ? "" : "s"}. Each card re-runs its saved SQL when you open this page.</p></div>
        <a class="btn" href="#/workspace">Ask another question</a></div>
      ${pins.length ? `<div class="grid2">${pins.map(tile).join("")}</div>` : `<div class="empty-thread">Nothing pinned yet. In the workspace, use <strong>Pin to dashboard</strong> on any answer.</div>`}
    </div>`;
    view.querySelectorAll("[data-unpin]").forEach((b) => b.addEventListener("click", async () => {
      try { await api(`/api/pins/${b.dataset.unpin}?session_id=${encodeURIComponent(sessionId)}`, { method: "DELETE" }); renderDashboard(); }
      catch (e) { toast(e.message); }
    }));
  }

  // ------------------------------------------------------------------ benchmark
  async function renderBenchmark() {
    document.title = "Benchmark – DataChat";
    const b = await api("/api/benchmark");
    const intro = `<section class="hero" style="max-width:780px"><h1 style="font-size:36px">How accurate is DataChat?</h1>
      <p style="font-size:17px"><strong>Execution accuracy</strong> is the share of test questions whose query result matches the correct answer. The test set covers the three sample datasets, graded easy, medium and hard.</p></section>`;
    if (!b.available) {
      view.innerHTML = `<div class="wrap">${intro}<section class="panel"><strong>No results yet</strong>
        <span class="note">The evaluation has not been run on this deployment. Run <code>python -m eval.run_eval</code> with a Groq API key, commit <code>eval/results/latest.json</code>, and this page fills in. Nothing on this page is estimated.</span></section></div>`;
      return;
    }
    const pc = (v) => (v === null || v === undefined ? "–" : (v * 100).toFixed(1) + "%");
    const bars = b.runs.map((r) => `<div class="m"><span>${esc(r.label)}</span><span>${esc(r.note || "")}</span></div>
      <div class="tr ${r.kind === "finetuned" ? "ft" : ""}"><i style="width:${((r.accuracy || 0) * 100).toFixed(1)}%"></i></div><span class="pc">${pc(r.accuracy)}</span>`).join("");
    const rows = b.runs.map((r) => `<tr><td>${esc(r.label)}</td><td class="num">${pc(r.by_difficulty?.easy)}</td><td class="num">${pc(r.by_difficulty?.medium)}</td>
      <td class="num">${pc(r.by_difficulty?.hard)}</td><td class="num">${pc(r.self_fixed_rate)}</td><td class="num">${r.latency_ms_p50 ? (r.latency_ms_p50 / 1000).toFixed(1) + " s" : "–"}</td>
      <td class="num">${r.cost_per_1k_usd !== undefined && r.cost_per_1k_usd !== null ? "$" + r.cost_per_1k_usd.toFixed(2) : "–"}</td></tr>`).join("");
    view.innerHTML = `<div class="wrap">${intro}
      <section class="panel" style="padding:26px;gap:18px"><h2 style="margin:0;font-size:18px">Execution accuracy by model</h2>
        <div class="bench-bars">${bars}</div>
        <span class="note">${esc(b.n_questions)} questions · run on ${esc(String(b.generated_at || "").slice(0, 10))}</span></section>
      <section class="panel" style="padding:26px"><h2 style="margin:0;font-size:18px">Accuracy, speed and cost</h2>
        <div class="tablebox"><table class="data"><thead><tr><th>Model</th><th>Easy</th><th>Medium</th><th>Hard</th><th>Needed a fix</th><th>Median time</th><th>Cost / 1k questions</th></tr></thead>
        <tbody>${rows}</tbody></table></div></section></div>`;
  }

  router();
})();
