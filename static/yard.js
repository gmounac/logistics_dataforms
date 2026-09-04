/* Shared helpers for the yard forms. Loaded by every page after yard.css. */
window.Yard = (() => {
  const $ = (id) => document.getElementById(id);

  // ------------------------------------------------------------- theme
  // Three states: "system" (follow the OS), "light", "dark". The choice lives
  // in localStorage per device. This script is in <head> with no defer, so the
  // apply below runs before the body paints — no flash of the wrong theme.
  const THEME_KEY = "yardTheme";
  const THEMES = ["system", "light", "dark"];
  const themeLabel = { system: "Theme: auto", light: "Theme: light", dark: "Theme: dark" };

  function readTheme() {
    try { const t = localStorage.getItem(THEME_KEY); return THEMES.includes(t) ? t : "system"; }
    catch (e) { return "system"; }
  }
  function applyTheme(t) {
    const root = document.documentElement;
    if (t === "light" || t === "dark") root.setAttribute("data-theme", t);
    else root.removeAttribute("data-theme");
  }
  function setTheme(t) {
    try { t === "system" ? localStorage.removeItem(THEME_KEY) : localStorage.setItem(THEME_KEY, t); }
    catch (e) { /* private mode: session-only */ }
    applyTheme(t);
  }
  applyTheme(readTheme());

  function mountThemeToggle() {
    const header = document.querySelector("header");
    if (!header || header.querySelector(".theme-toggle")) return;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "theme-toggle";
    btn.textContent = themeLabel[readTheme()];
    btn.addEventListener("click", () => {
      const next = THEMES[(THEMES.indexOf(readTheme()) + 1) % THEMES.length];
      setTheme(next);
      btn.textContent = themeLabel[next];
    });
    header.appendChild(btn);
  }
  async function mountAccount() {
    const header = document.querySelector("header");
    if (!header || location.pathname === "/login" || header.querySelector(".account")) return;
    let me;
    try { me = await fetch("/api/me").then((r) => (r.ok ? r.json() : null)); } catch (e) { return; }
    if (!me) return;
    const span = document.createElement("span");
    span.className = "account";
    span.textContent = me.username + " · ";
    const out = document.createElement("button");
    out.type = "button";
    out.className = "linklike";
    out.textContent = "Sign out";
    out.addEventListener("click", async () => {
      await fetch("/api/logout", { method: "POST" });
      location.href = "/login";
    });
    span.appendChild(out);
    header.appendChild(span);
  }

  document.addEventListener("DOMContentLoaded", () => {
    try { mountThemeToggle(); } catch (e) { console.error(e); }
    try { mountAccount(); } catch (e) { console.error(e); }
  });

  // ------------------------------------------------------------- form bits
  function fill(select, values, placeholder = "Select", keep = false) {
    const prev = keep ? select.value : "";
    select.innerHTML = "";
    const p = new Option(placeholder, "", true, true);
    p.disabled = true;
    select.add(p);
    values.forEach((v) => select.add(new Option(v, v)));
    select.value = values.includes(prev) ? prev : "";
  }

  function setError(id, msg) {
    // a validation error on comments is useless behind a collapsed Notes section
    if (id === "comments" && msg) openNotes();
    const err = $(id + "Err");
    if (err) {
      err.textContent = msg || "";
      err.classList.toggle("show", !!msg);
    }
    const field = $(id);
    if (field) msg ? field.setAttribute("aria-invalid", "true") : field.removeAttribute("aria-invalid");
  }

  function clearErrors() {
    document.querySelectorAll(".err").forEach((e) => e.classList.remove("show"));
    document.querySelectorAll("[aria-invalid]").forEach((e) => e.removeAttribute("aria-invalid"));
  }

  function alert(kind, msg) {
    const a = $("alert");
    a.className = "alert show " + kind;
    a.textContent = msg;
    a.scrollIntoView({ block: "nearest" });
  }
  function hideAlert() { $("alert").classList.remove("show"); }

  function daysAgo(dateStr) {
    const today = new Date(); today.setHours(0, 0, 0, 0);
    return Math.floor((today - new Date(dateStr + "T00:00")) / 86400000);
  }

  /** Fill date/time inputs with now, cap date at today, and wire the
   *  "comments required after 3 days" rule to the comments label. */
  function initDateTime(dateId = "date", timeId = "time") {
    const now = new Date();
    const pad = (n) => String(n).padStart(2, "0");
    const today = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
    $(dateId).value = today;
    $(dateId).max = today;
    if (timeId && $(timeId)) $(timeId).value = `${pad(now.getHours())}:${pad(now.getMinutes())}`;
    const update = () => {
      const old = $(dateId).value && daysAgo($(dateId).value) > 3;
      const label = $("commentsLabel");
      if (label) label.classList.toggle("req", old);
      if ($("comments")) $("comments").required = old;
      if (old) openNotes();   // a backdated entry needs a reason typed in
    };
    $(dateId).addEventListener("change", update);
    update();
  }

  // ------------------------------------------------------- collapsible Notes
  // The Notes section is optional on every form, so it starts folded behind a
  // "＋ Add note" button. openNotes() unfolds it (called on demand, when an
  // entry is backdated, or when a comment becomes required).
  let openNotes = () => {};
  function collapsibleNotes() {
    const comments = $("comments");
    if (!comments) return;
    const section = comments.closest("fieldset") || comments.closest(".field");
    if (!section || section.dataset.collapsed) return;
    section.dataset.collapsed = "1";
    section.hidden = true;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "ghost";
    btn.id = "notesToggle";
    btn.textContent = "＋ Add note";
    section.parentNode.insertBefore(btn, section);
    openNotes = () => {
      if (btn.hidden) return;
      section.hidden = false;
      btn.hidden = true;
    };
    btn.addEventListener("click", () => { openNotes(); comments.focus(); });
  }
  document.addEventListener("DOMContentLoaded", () => { try { collapsibleNotes(); } catch (e) { console.error(e); } });

  function atISO(dateId = "date", timeId = "time") {
    const t = $(timeId) ? $(timeId).value : "00:00";
    return new Date($(dateId).value + "T" + t).toISOString();
  }

  /** Common required-field + comments checks. Returns true when clean. */
  function validateCommon(required) {
    clearErrors();
    let ok = true;
    for (const [id, msg] of Object.entries(required)) {
      const f = $(id);
      if (f && !f.disabled && !f.value.trim()) { setError(id, msg); ok = false; }
    }
    if ($("comments") && $("comments").required && !$("comments").value.trim()) {
      setError("comments", "Comments are required for entries older than 3 days"); ok = false;
    }
    return ok;
  }

  function typeLabel(c) {
    const size = c.size === "TEU" ? "20'" : "40'";
    return c.container_type === "Dry" ? `Dry ${size}` : `${c.reefer_type} ${size}`;
  }

  function fmt(iso) { return iso ? new Date(iso).toLocaleString() : "–"; }

  // -------------------------------------------------------------------- API
  let optionsCache = null;
  async function options() {
    if (!optionsCache) optionsCache = await fetch("/api/options").then((r) => r.json());
    return optionsCache;
  }

  /** Any method, JSON body optional. Returns {ok, data, message}. Never throws on HTTP errors. */
  async function request(method, path, body) {
    const res = await fetch(path, {
      method,
      headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    if (res.status === 401) {
      // session gone — send them to sign in and come back here
      location.href = "/login?next=" + encodeURIComponent(location.pathname + location.search);
      return { ok: false, data: {}, message: "Session expired." };
    }
    const data = res.status === 204 ? {} : await res.json().catch(() => ({}));
    if (res.ok) return { ok: true, data };
    const message = Array.isArray(data.detail)
      ? data.detail.map((d) => d.msg.replace(/^Value error, /, "")).join(" ")
      : data.detail || "Request failed.";
    return { ok: false, data, message };
  }
  const post = (path, body) => request("POST", path, body);
  const patch = (path, body) => request("PATCH", path, body);
  const del = (path) => request("DELETE", path);

  /** Wrap a submit handler: validates, disables the button, handles errors. */
  function onSubmit(formId, btnId, buildBody, path, onSuccess) {
    $(formId).addEventListener("submit", async (ev) => {
      ev.preventDefault();
      hideAlert();
      const body = buildBody();
      if (!body) return;
      const btn = $(btnId);
      btn.disabled = true;
      try {
        if (body.__unmatched) {
          const { __unmatched, ...rec } = body;
          const r = await post("/api/unmatched", rec);
          if (!r.ok) { alert("bad", r.message); return; }
          receipt(r.data.container_number, [["Saved as", "Unmatched record — not in the yard list"],
            ["At", fmt(r.data.at)], ["Number check", r.data.check_digit_ok ? "OK" : "Check digit looks wrong — verify the number"]]);
          resetKeepingTime(formId, () => { pickers.forEach((p) => p.reset()); const i = $("containerNumber"); if (i) i.focus(); });
          return;
        }
        const r = await post(path, body);
        if (!r.ok) { alert("bad", r.message); return; }
        onSuccess(r.data);
      } catch (e) {
        alert("bad", "Network error. Check the connection and try again.");
        console.error(e);
      } finally {
        btn.disabled = false;
      }
    });
  }

  /** Reset a form but keep date/time, then run a callback. */
  function resetKeepingTime(formId, after) {
    const d = $("date").value, t = $("time") ? $("time").value : null;
    $(formId).reset();
    $("date").value = d;
    if (t !== null) $("time").value = t;
    $("date").dispatchEvent(new Event("change"));   // re-evaluate the comments rule
    if (after) after();
  }

  // ----------------------------------------------------------------- picker
  /**
   * Searchable picker over containers currently in the yard.
   *   picker({ inputId, listId, filter: {reefer, plugged}, onSelect, onClear })
   * Renders an "on file" summary into #onfile if present. Exposes .selected.
   */
  const NUMBER_RE = /^[A-Z]{4}\d{7}$/;
  const pickers = [];

  function picker({ inputId = "containerNumber", listId = "pickerList", filter = {}, onSelect, onClear,
                    unlisted = null }) {
    // unlisted: null = must pick from list; or { kind, fields: ["unit_manufacturer", "reefer_type"] }
    const input = $(inputId), list = $(listId);
    const state = { selected: null, unlistedNumber: null, unlisted };
    let timer = null;
    pickers.push(state);

    const qs = () => {
      const p = new URLSearchParams({ limit: 8 });
      for (const [k, v] of Object.entries(filter)) if (v !== undefined) p.set(k, v);
      return p;
    };

    function open(items) {
      list.innerHTML = "";
      const typed = input.value.trim().toUpperCase();
      if (!items.length) {
        const li = document.createElement("li");
        li.className = "empty";
        li.textContent = "No container in the yard matches";
        list.appendChild(li);
      }
      if (unlisted && NUMBER_RE.test(typed) && !items.some((st) => st.container.number === typed)) {
        const li = document.createElement("li");
        li.className = "unlisted";
        li.innerHTML = `<span class="num"></span><span class="meta">Not in the list — record anyway</span>`;
        li.querySelector(".num").textContent = typed;
        // pointerdown covers both touch (Android) and mouse (Windows); preventDefault
        // keeps focus on the input so its blur handler doesn't close the list first
        li.addEventListener("pointerdown", (e) => { e.preventDefault(); chooseUnlisted(typed); });
        list.appendChild(li);
      }
      for (const st of items) {
        const li = document.createElement("li");
        li.setAttribute("role", "option");
        li.innerHTML = `<span class="num"></span><span class="meta"></span>`;
        li.querySelector(".num").textContent = st.container.number;
        li.querySelector(".meta").textContent = typeLabel(st.container) + (st.is_plugged ? " · plugged" : "");
        li.addEventListener("pointerdown", (e) => { e.preventDefault(); choose(st); });
        list.appendChild(li);
      }
      list.classList.add("open");
      input.setAttribute("aria-expanded", "true");
    }
    function close() { list.classList.remove("open"); input.setAttribute("aria-expanded", "false"); }

    function renderOnfile(st) {
      const box = $("onfile");
      if (!box) return;
      const c = st.container, p = st.plugged_in;
      const rows = [
        ["Line", c.shipping_line],
        ["Type", typeLabel(c) + (c.unit_manufacturer ? " · " + c.unit_manufacturer : "")],
        ["Arrived", fmt(st.arrived_at) + (st.visit_count ? ` · visit ${st.visit_count}` : "")],
        ["Cargo", st.cargo_status || "–"],
        ["PTI", st.pti_status || "n/a"],
      ];
      if (p) rows.push(["Plugged", `${fmt(p.at)} · ${p.purpose} · ${p.generator} · set ${p.set_point_c}°C` + (p.seal_number ? ` · seal ${p.seal_number}` : "")]);
      if (st.cleaned_this_visit) rows.push(["Cleaning", st.cleaned_this_visit + (st.cleaning_done ? " (done this visit)" : "")]);
      box.querySelector("dl").innerHTML = rows.map(() => "<dt></dt><dd></dd>").join("");
      const dts = box.querySelectorAll("dt"), dds = box.querySelectorAll("dd");
      rows.forEach(([k, v], i) => { dts[i].textContent = k; dds[i].textContent = v; });
      const warn = box.querySelector(".warn");
      if (warn) warn.textContent = "";
      box.classList.add("show");
    }

    function choose(st) {
      state.selected = st;
      state.unlistedNumber = null;
      hideUnlistedBox();
      input.value = st.container.number;
      close();
      setError(inputId, "");
      renderOnfile(st);
      if (onSelect) onSelect(st);
    }
    function chooseUnlisted(number) {
      state.selected = null;
      state.unlistedNumber = number;
      input.value = number;
      close();
      setError(inputId, "");
      const box = $("onfile");
      if (box) box.classList.remove("show");
      showUnlistedBox(number);
      if (onClear) onClear();
    }
    function showUnlistedBox(number) {
      const box = $("unlistedBox");
      if (!box) return;
      box.querySelector(".num").textContent = number;
      box.classList.add("show");
      const sel = box.querySelector("#unlistedManufacturer");
      if (sel) sel.focus();
    }
    function hideUnlistedBox() {
      const box = $("unlistedBox");
      if (box) box.classList.remove("show");
    }
    function clear() {
      if (!state.selected && !state.unlistedNumber) return;
      state.selected = null;
      state.unlistedNumber = null;
      hideUnlistedBox();
      const box = $("onfile");
      if (box) box.classList.remove("show");
      if (onClear) onClear();
    }
    async function search(q) {
      try {
        const p = qs(); p.set("q", q);
        const r = await fetch(`/api/yard/on-site?${p}`);
        if (!r.ok) throw new Error(r.statusText);
        open(await r.json());
      } catch { alert("bad", "Could not search the yard. Check the connection."); }
    }

    input.addEventListener("input", () => {
      const q = input.value.trim().toUpperCase();
      const current = state.selected ? state.selected.container.number : state.unlistedNumber;
      if (current && q !== current) clear();
      clearTimeout(timer);
      if (q.length < 2) { close(); return; }
      timer = setTimeout(() => search(q), 250);
    });
    input.addEventListener("focus", () => {
      // pull the field up so the results list sits above the phone's soft keyboard;
      // wait for the keyboard to settle so the scroll isn't undone by the resize
      const field = input.closest(".field") || input;
      setTimeout(() => field.scrollIntoView({ block: "start", behavior: "smooth" }), 250);
      if (!state.selected && !state.unlistedNumber && input.value.trim().length >= 2) search(input.value.trim());
    });
    input.addEventListener("blur", close);
    input.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });

    state.reset = () => { state.selected = null; state.unlistedNumber = null; hideUnlistedBox(); const b = $("onfile"); if (b) b.classList.remove("show"); };
    state.require = (msg = "Pick a container from the list") => {
      if (state.selected || state.unlistedNumber) return true;
      setError(inputId, unlisted ? "Pick a container, or choose “record anyway” for one that isn't listed" : msg);
      return false;
    };
    state.number = () => state.selected ? state.selected.container.number : state.unlistedNumber;
    /** Turn a normal event body into an unmatched-record body when needed. */
    state.wrap = (body) => {
      if (!state.unlistedNumber) return body;
      const { at, comments, container_number, ...details } = body;
      const extra = {};
      const m = $("unlistedManufacturer"), r = $("unlistedReeferType");
      if (m && m.value) extra.unit_manufacturer = m.value;
      if (r && r.value) extra.reefer_type = r.value;
      return { __unmatched: true, kind: unlisted.kind, container_number, at, comments, details: { ...details, ...extra } };
    };
    return state;
  }

  /** Markup for the "record anyway" box; withUnit adds manufacturer + reefer type. */
  function unlistedHTML(withUnit) {
    return `
      <div id="unlistedBox" class="unlisted-box">
        <div><span class="num"></span> is not where the yard expects it (no gate in on record).</div>
        <div class="sub">It will be saved as an unmatched record for someone to sort out, so nothing is lost.</div>
        ${withUnit ? `
        <div class="row" style="margin-top:10px">
          <div class="field"><label for="unlistedManufacturer">Unit manufacturer</label><select id="unlistedManufacturer"></select></div>
          <div class="field"><label for="unlistedReeferType">Reefer type</label><select id="unlistedReeferType"></select></div>
        </div>` : ""}
      </div>`;
  }

  /** The standard picker markup, so pages don't repeat it. */
  function pickerHTML(hint) {
    return `
      <div class="picker">
        <input id="containerNumber" autocomplete="off" autocapitalize="characters" spellcheck="false"
               placeholder="Start typing to search the yard" role="combobox"
               aria-expanded="false" aria-controls="pickerList" aria-autocomplete="list" required />
        <ul id="pickerList" role="listbox"></ul>
      </div>
      <div class="hint">${hint}</div>
      <div class="err" id="containerNumberErr"></div>
      <div id="onfile" class="onfile"><dl></dl><div class="warn"></div></div>`;
  }

  /** Show the confirmation receipt. rows = [[label, value], ...] */
  function receipt(number, rows) {
    $("rNumber").textContent = number;
    const dl = $("receipt").querySelector("dl");
    dl.innerHTML = rows.map(() => "<dt></dt><dd></dd>").join("");
    const dts = dl.querySelectorAll("dt"), dds = dl.querySelectorAll("dd");
    rows.forEach(([k, v], i) => { dts[i].textContent = k; dds[i].textContent = v; });
    $("receipt").classList.add("show");
    // the submit button is at the foot of a long form; bring the confirmation
    // into view so it isn't left off-screen above after the form resets
    $("receipt").scrollIntoView({ block: "start", behavior: "smooth" });
  }

  /** Populate the unlisted box selects from options (no-op if absent). */
  function fillUnlisted(o) {
    const m = $("unlistedManufacturer"), r = $("unlistedReeferType");
    if (m) fill(m, o.unit_manufacturers, "Unknown");
    if (r) fill(r, o.reefer_types, "Unknown");
  }

  return { $, fill, setError, clearErrors, alert, hideAlert, daysAgo, initDateTime, atISO,
           validateCommon, typeLabel, fmt, options, post, patch, del, request, onSubmit, resetKeepingTime,
           picker, pickerHTML, unlistedHTML, fillUnlisted, receipt, NUMBER_RE,
           collapsibleNotes, openNotes: (...a) => openNotes(...a) };
})();
