let state;

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const asset = (path) => `/romm/${path.replace(/^\//, "")}`;
const cover = (path) => (path ? asset(path) : "");
const img = (cls, src, lazy = false, fallback = "") =>
  `<img class="${cls}" src="${src}" alt="" decoding="async" ${lazy ? 'loading="lazy"' : ""} ${fallback ? `onerror="this.onerror=null;this.src='${fallback}'"` : ""} />`;
// RomM ships a few legacy platforms (e.g. pico) as .ico only
const platIcon = (slug, lazy = false) => img("plat-icon", asset(`assets/platforms/${slug}.svg`), lazy, asset(`assets/platforms/${slug}.ico`));
const PUSH_ONLY_REASON = "Push-only: the sync folders are read-only (or PUSH_ONLY is set), so RomM copies are never written to disk.";

function ago(iso) {
  const min = Math.round((Date.now() - new Date(iso)) / 60000);
  if (min < 1) return "just now";
  if (min < 60) return `${min} min ago`;
  if (min < 1440) return `${Math.round(min / 60)} h ago`;
  return `${Math.round(min / 1440)} d ago`;
}
const clock = (iso) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
const stamp = (iso) => new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
// phones print 24h: the card row is tight next to a wide screenshot, and "00:07" is shorter than "12:07 AM"
const shortStamp = (iso) => new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", ...(sheetLayout.matches && { hourCycle: "h23" }),
  year: new Date(iso).getFullYear() === new Date().getFullYear() ? undefined : "numeric" });
const kb = (b) => (b == null ? "unknown" : b >= 1048576 ? `${(b / 1048576).toFixed(1)} MB` : `${Math.round(b / 1024)} KB`);
const SYNC_MODE_NAMES = { watch: "Incremental", periodic: "Periodic" };
const syncErrors = () => state.last_sync?.errors ?? 0;
const plural = (n, word) => `${n} ${n === 1 ? word : word.replace(/y$/, "ie")}${n === 1 ? "" : "s"}`;

const filesWith = (status) => state.games.flatMap((g) => g.files.filter((f) => f.status === status).map((f) => ({ game: g, file: f })));
const conflicts = () => filesWith("conflict");
const differs = () => filesWith("differs");  // different content on both sides and no sync history to say which is newer
const isOpen = (f) => f.status === "conflict" || f.status === "differs";
const blocked = () => filesWith("blocked");  // RomM-only files that can't be downloaded without overwriting or guessing a folder
const needCount = () => conflicts().length + differs().length + blocked().length + state.syncthing_conflicts.length + state.ambiguous.length;

function byPlatform() {
  const groups = new Map();
  for (const g of state.games) {
    if (!groups.has(g.platform_slug)) groups.set(g.platform_slug, { name: g.platform_name, slug: g.platform_slug, games: [] });
    groups.get(g.platform_slug).games.push(g);
  }
  return [...groups.values()];
}

function renderHero() {
  const count = needCount();
  const ls = state.last_sync;
  const tone = count ? "critical" : "ok";
  $("#ambient").className = `ambient ${tone}`;
  const headline = count ? plural(count, "issue") : "In sync";
  const errors = syncErrors();
  const listed = ls ? [...ls.pushed.map((f) => [f, "pushed to RomM"]), ...ls.pulled.map((f) => [f, "pulled from RomM"])] : [];
  $("#hero").innerHTML = `
    <div class="hero ${tone}"><div class="hero-card">
      <h1>${headline}</h1>
      ${errors ? `<h1 class="sync-errors">${plural(errors, "Sync Error")}</h1>` : ""}
      <div class="hero-sub">
        ${ls ? `<span>Last sync <strong>${clock(ls.finished_at)}</strong> (${ago(ls.finished_at)})</span>
          ${errors ? "" : `<span class="errors-ok">0 Sync Errors</span>`}` : "<span>No sync has finished yet</span>"}
        ${state.push_only ? `<span><strong>Push-only</strong>: nothing is written to the sync folders</span>` : ""}
      </div>
      ${listed.length ? `<details class="hero-toggle">
        <summary class="pill">${plural(listed.length, "change")}<span class="chev">▶</span></summary>
        <ul class="hero-files">
          ${listed.map(([f, dir]) => `<li>${esc(f.file_name)}${f.game ? ` <span style="color:var(--text-dim)">${esc(f.game)}</span>` : ""}<span class="dir">${dir}</span></li>`).join("")}
        </ul></details>` : ""}
    </div></div>`;
}

// Strips save/state extensions so "X.state.auto.png" and "X.srm" group under the same game "X".
const stem = (name) => name.replace(/(\.(srm|sav|sa\d|state\d*|auto|png|rtc|eep|fla|sra|mpk|dsv|bak))+$/i, "");

// One entry per game: open conflicts, blocked downloads and Syncthing conflict copies, newest problem first.
function needsGroups() {
  const groups = new Map();
  const group = (key, init) => groups.get(key) || groups.set(key, { ...init, conflicts: [], differs: [], blocked: [], syncthing: [], ambiguous: null }).get(key);
  // Not synced until RomM has one game for the name; it stands in for the game, which has no RomM match of its own
  const ambiguousKey = (folder, name) => `amb:${folder}/${name}`;
  for (const a of state.ambiguous) {
    group(ambiguousKey(a.local_platform, a.name), { game: { name: a.name, platform_slug: a.platform_slug, platform_name: a.platform_name, cover: a.cover } }).ambiguous = a;
  }
  for (const { game, file } of conflicts()) group(game.rom_id, { game }).conflicts.push(file);
  for (const { game, file } of differs()) group(game.rom_id, { game }).differs.push(file);
  for (const { game, file } of blocked()) group(game.rom_id, { game }).blocked.push(file);
  const byFile = new Map();
  for (const g of state.games) for (const f of g.files) if (f.local) byFile.set(f.file_name, g);
  const byStem = new Map(state.games.flatMap((g) => g.files.map((f) => [stem(f.file_name), g])));
  for (const c of state.syncthing_conflicts) {
    const game = byFile.get(c.file_name) || byStem.get(stem(c.file_name));
    const folder = c.path.split("/").slice(-2, -1)[0];
    const held = !game && groups.get(ambiguousKey(folder, stem(c.file_name)));
    (held || group(game ? game.rom_id : `st:${folder}/${stem(c.file_name)}`, { game, name: stem(c.file_name), folder })).syncthing.push(c);
  }
  const total = (g) => g.conflicts.length + g.differs.length + g.blocked.length + g.syncthing.length + (g.ambiguous ? 1 : 0);
  return [...groups.entries()].map(([key, g]) => ({ key: String(key), total: total(g), ...g }))
    .sort((a, b) => !!b.ambiguous - !!a.ambiguous || b.conflicts.length - a.conflicts.length || b.total - a.total || groupName(a).localeCompare(groupName(b)));
}
const groupName = (g) => (g.game ? g.game.name : g.name);

function groupCard(g) {
  const chips = (g.ambiguous ? `<span class="why">Not synced: ${g.ambiguous.roms.length} RomM matches</span>` : "") +
    [[g.conflicts.length, "conflict"], [g.differs.length, "difference"], [g.blocked.length, "held download"], [g.syncthing.length, "Syncthing copy"]]
    .filter(([n]) => n).map(([n, w], i) => `<span class="why ${i || !g.conflicts.length ? "soft" : ""}">${plural(n, w)}</span>`).join("");
  const sub = g.game ? `${platIcon(g.game.platform_slug)}${esc(g.game.platform_name)}` : `Folder /${esc(g.folder)}`;
  return `<button class="conflict-card ${g.game ? "" : "plain"}" data-group="${esc(g.key)}">
      ${g.game ? img("cover", cover(g.game.cover), true) : ""}
      <div>
        <div class="title">${esc(groupName(g))}</div>
        <div class="meta">${sub}</div>
        <div class="chips">${chips}</div>
      </div>
      <span class="count-badge">${g.total}</span>
    </button>`;
}

function renderNeedsYou() {
  const orphans = state.orphans;
  const groups = needsGroups();
  const head = `<div class="section-head"><h2>Needs you</h2>${groups.length ? `<span class="count">${plural(needCount(), "issue")} in ${plural(groups.length, "game")}</span>` : ""}</div>`;
  const orphanList = orphans.length ? `<div class="sub-head">No longer in RomM (history only)</div><ul class="info-list">
      ${orphans.map((o) => `<li><strong>${esc(o.file_name)}</strong> (ROM ${o.rom_id}), last synced ${ago(o.last_ts)}. The game was removed or re-added in RomM.</li>`).join("")}</ul>` : "";
  const duplicates = state.duplicates.length ? `<div class="sub-head">Same file in the platform folder and a core folder</div><ul class="info-list">
      ${state.duplicates.map((d) => `<li><strong>${esc(d.file_name)}</strong> in ${esc(d.platform)}: ${d.folders.map((f) => (f === d.platform ? "platform folder" : esc(f))).join(", ")}. Only the newest copy is synced.</li>`).join("")}</ul>` : "";
  $("#needs-you").innerHTML = head +
    (groups.length ? `<div class="conflict-grid">${groups.map(groupCard).join("")}</div>` : `<div class="empty-ok"><span class="pulse-dot"></span>Nothing to resolve. Both sides agree.</div>`) +
    orphanList + duplicates;
}

function fileRow(game, file, side) {
  const copy = file[side];
  const isConflict = isOpen(file);
  const attrs = isConflict ? `data-rom="${game.rom_id}" data-file="${esc(file.file_name)}" data-emu="${esc(file.emulator)}" data-kind="${file.kind}"` : `data-hist="${game.rom_id}"`;
  return `<button class="file-row ${isConflict ? "conflict" : ""}" ${attrs}>
    <span class="kind">${file.kind}</span><span class="fname">${esc(file.file_name)}</span>
    <span class="when">${side === "local" ? esc(copy.device) + " · " : ""}${ago(copy.modified_at)}</span></button>`;
}

const treeOpen = new Map();  // data-tree key -> open, so the periodic re-render keeps what the user opened or closed
const lazyBodies = new Map();  // data-tree key -> builder of the body of a tree that was rendered closed and not opened yet

// Closed trees get an empty body that fillTree() builds on first open; only trees the user opened are built now. `hit` just marks open issues.
function tree(cls, key, hit, summary, body) {
  const open = treeOpen.get(key) ?? false;
  if (!open) lazyBodies.set(key, body);
  return `<details class="tree ${cls} ${hit ? "has-conflict" : ""}" data-tree="${esc(key)}" ${open ? "open" : ""}>${summary}<div class="tree-body">${open ? body() : ""}</div></details>`;
}

function fillTree(details) {
  const body = lazyBodies.get(details.dataset.tree);
  if (!body) return;
  lazyBodies.delete(details.dataset.tree);
  details.querySelector(":scope > .tree-body").innerHTML = body();
}

function platformSummary(p, count) {
  const hit = p.games.some((g) => g.files.some(isOpen));
  return [hit, `<summary>${platIcon(p.slug, true)}${esc(p.name)}<span class="n">${count}</span><span class="chev">▶</span></summary>`];
}

const foldPill = (side) => `<button class="pill fold" data-fold="${side}"><span class="chev">▶</span></button>`;
const treesOf = (side) => [...document.querySelectorAll(`#panel-${side} details.tree`)];
function syncFolds() {
  document.querySelectorAll("[data-fold]").forEach((pill) => {
    const allOpen = treesOf(pill.dataset.fold).every((d) => d.open);
    const label = allOpen ? "Collapse all platforms" : "Expand all platforms";
    pill.classList.toggle("open", allOpen);
    pill.title = label;
    pill.setAttribute("aria-label", label);
  });
}

// The games whose files moved most recently: cover, when, how many files, and whether the game needs you. A click opens the game card.
const RECENT_ON_PHONE = 3;
// "now", "14m", "3h", "2d": the strip is too narrow for "14 min ago"
function agoShort(iso) {
  const min = Math.round((Date.now() - new Date(iso)) / 60000);
  return min < 1 ? "now" : min < 60 ? `${min}m` : min < 1440 ? `${Math.round(min / 60)}h` : `${Math.round(min / 1440)}d`;
}
function renderRecent() {
  const byId = new Map(state.games.map((g) => [g.rom_id, g]));
  const cards = state.recent.filter((r) => byId.has(r.rom_id)).map(({ rom_id, ts }) => {
    const g = byId.get(rom_id);
    const [tone, label] = g.files.some(isOpen) ? ["crit", "Conflicts"] : ["ok", "In Sync"];
    return `<button type="button" class="recent-card" data-hist="${rom_id}" title="${esc(g.name)}" aria-label="${esc(g.name)}, synced ${ago(ts)}, ${plural(g.files.length, "file")}, ${label}">
      ${img("cover", cover(g.cover), true)}
      <div class="recent-text"><span class="recent-when">${CLOCK}${agoShort(ts)}</span><span class="recent-files">${plural(g.files.length, "file")}</span></div>
      <span class="status-chip ${tone}" aria-hidden="true">${STATUS_ICONS[tone]}</span></button>`;
  });
  const box = $("#recent");
  box.hidden = !cards.length;
  box.innerHTML = cards.length ? `<div class="sub-head">Last Synced</div><div class="recent-grid">${cards.join("")}</div>` : "";
  fitRecent();
}
// always one row: as many cards as fit the width, at most 3 on a phone (2 when 3 would be cramped)
function fitRecent() {
  const grid = $(".recent-grid");
  if (!grid) return;
  const columns = getComputedStyle(grid).gridTemplateColumns.split(" ").length;
  const fit = sheetLayout.matches ? Math.min(columns, RECENT_ON_PHONE) : columns;
  [...grid.children].forEach((card, i) => { card.hidden = i >= fit; });
}
new ResizeObserver(fitRecent).observe($("#recent"));

function renderLocal() {
  const body = byPlatform().map((p) => {
    const files = p.games.flatMap((g) => g.files.filter((f) => f.local && f.status !== "blocked").map((f) => ({ g, f })));
    const [hit, summary] = platformSummary(p, plural(files.length, "file"));
    return tree("", `local:${p.slug}`, hit, summary, () => {
      const cores = new Map();
      for (const x of files) {
        const key = x.f.core || "";
        if (!cores.has(key)) cores.set(key, []);
        cores.get(key).push(x);
      }
      const flat = (cores.get("") || []).map(({ g, f }) => fileRow(g, f, "local")).join("");
      const nested = [...cores.entries()].filter(([k]) => k).map(([core, xs]) =>
        tree("sub", `local:${p.slug}/${core}`, xs.some(({ f }) => isOpen(f)),
          `<summary><span class="folder-glyph">▤</span>${esc(core)}<span class="n">${xs.length}</span><span class="chev">▶</span></summary>`,
          () => xs.map(({ g, f }) => fileRow(g, f, "local")).join(""))).join("");
      return `<div class="folder-glyph" style="padding:4px 10px">/${esc(p.games[0].fs_folder)}</div>${nested}${flat}`;
    });
  }).join("");
  $("#panel-local").innerHTML = `
    <div class="panel-head"><span class="side-badge"></span><h2>Syncthing</h2>${foldPill("local")}<span class="sub">${state.push_only ? "read-only" : "read/write"}</span></div>${body}`;
  syncFolds();
}

function renderRomm() {
  const u = state.user;
  const body = byPlatform().map((p) => {
    const [hit, summary] = platformSummary(p, plural(p.games.length, "game"));
    const games = () => p.games.map((g) => {
      const romm = g.files.filter((f) => f.romm);
      return tree("sub", `romm:${p.slug}/${g.rom_id}`, g.files.some(isOpen), `<summary><span class="game-line game-link" data-hist="${g.rom_id}">${img("mini-cover", cover(g.cover), true)}<span class="gname">${esc(g.name)}</span></span>
        <span class="n">${romm.length}</span><span class="chev">▶</span></summary>`,
        () => romm.map((f) => fileRow(g, f, "romm")).join(""));
    }).join("");
    return tree("", `romm:${p.slug}`, hit, summary, games);
  }).join("");
  $("#panel-romm").innerHTML = `
    <div class="panel-head"><span class="side-badge"></span><h2>RomM</h2>${foldPill("romm")}
      <span class="user-chip"><span class="avatar">${esc(u.username[0].toUpperCase())}</span>${esc(u.username)}<span class="role">${esc(u.role)}</span></span>
    </div>${body}`;
  syncFolds();
}

function renderUnmatched() {
  $("#unmatched").hidden = !state.unmatched.length;
  $("#unmatched-count").textContent = plural(state.unmatched.length, "game");
  $("#unmatched-body").innerHTML = state.unmatched.map((u) => `<li><strong>${esc(u.name)}</strong> in ${esc(u.local_platform)} · ${plural(u.files.length, "file")}.
    No RomM game with this file name on this platform. Check platform_mapping.yaml and the ROM's file name.</li>`).join("");
}

function renderBottom() {
  const files = state.games.flatMap((g) => g.files);
  const count = needCount();
  const ls = state.last_sync;
  const issues = $("#bar-issues");
  const tone = (n) => (n ? "critical" : "ok");
  issues.innerHTML = `<span class="${tone(count)}">${plural(count, "Issue")}</span><span class="${tone(syncErrors())}">${plural(syncErrors(), "Sync Error")}</span>`;
  $("#bar-last").textContent = ls ? `Last: ${ago(ls.finished_at)}` : "No sync yet";
  const figure = (n, noun, tail = "") => `<strong>${n}</strong> ${n === 1 ? noun : `${noun}s`}${tail}`;
  const ofKind = (kind) => files.filter((f) => f.kind === kind).length;
  $("#bar-stats").innerHTML = `
    <span class="stat-line"><span class="s-games">${figure(state.games.length, "game", `<span class="wide"> tracked</span>`)}</span>
    <span class="s-plats">${figure(byPlatform().length, "platform")}</span></span>
    <span class="stat-line"><span class="s-saves">${figure(ofKind("save"), "save")}</span>
    <span class="s-states">${figure(ofKind("state"), "state")}</span></span>`;
  $("#bar-romm").href = state.romm_url;
  const rommIcon = $("#bar-romm img");
  rommIcon.onerror = () => { rommIcon.onerror = null; rommIcon.src = "isotipo.svg"; };  // RomM unreachable: the bundled copy
  rommIcon.src = asset("assets/isotipo.svg");
}

// Once the hero card has scrolled away the bar becomes the action bar: the issue readout joins it.
// The maintenance panel stays outside the bar (a backdrop-filter inside the sticky/fixed bar cannot blur the page) and is placed beside the button.
let lastAction = false;
function setActionBar(action) {
  lastAction = action;
  const bar = $("#bottombar");
  if (bar.classList.contains("action") === action) return;
  bar.classList.toggle("action", action);
  placeMenuPanel();
}
function placeMenuPanel() {
  const panel = $("#menu-panel");
  panel.removeAttribute("style");
  const button = $("#menu-toggle").getBoundingClientRect();
  Object.assign(panel.style, sheetLayout.matches
    ? { bottom: `${innerHeight - $("#bottombar").getBoundingClientRect().top}px` }
    : { top: `${button.bottom + 10}px`, right: `${document.documentElement.clientWidth - button.right}px` });
}
const STICKY_TOP_PX = 12;  // keep in sync with .bottombar { top } in styles.css
// Land just past the point where the bar sticks, so "Needs you" sits right under it (desktop) without the bar reverting to its resting state,
// or at the top of the page under a bottom bar (mobile).
const PAST_STICK_PX = 16;
$("#bar-issues").addEventListener("click", (e) => {
  e.preventDefault();
  const bar = $("#bottombar");
  const barStyle = getComputedStyle(bar);
  const covered = barStyle.position === "sticky" ? STICKY_TOP_PX + bar.offsetHeight + parseFloat(barStyle.marginBottom) - PAST_STICK_PX : STICKY_TOP_PX;
  scrollTo({ top: $("#needs-you").getBoundingClientRect().top + scrollY - covered, behavior: "smooth" });
});
new IntersectionObserver(([sentinel]) => setActionBar(!sentinel.isIntersecting && sentinel.boundingClientRect.top < STICKY_TOP_PX * 2),
  { rootMargin: `-${STICKY_TOP_PX}px 0px 0px 0px` }).observe($("#bar-sentinel"));

function modal(inner, cls = "") {
  $("#modal-root").innerHTML = `<div class="scrim"><div class="modal ${cls}" role="dialog" aria-modal="true">${inner}</div></div>`;
}

function gameHead(game, subtitle) {
  return `<div class="modal-head">
    ${img("cover", cover(game.cover))}
    <div><h3>${esc(game.name)}</h3>
      <div class="meta">${platIcon(game.platform_slug)}${esc(game.platform_name)}${subtitle}</div></div>
    <button class="pill close">Close</button></div>`;
}

// Tabler Icons outline glyphs (MIT), inlined so they work offline and take currentColor
const glyph = (...paths) => `<svg class="glyph" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths.map((d) => `<path d="${d}" />`).join("")}</svg>`;
const CLOCK = glyph("M3 12a9 9 0 1 0 18 0a9 9 0 0 0 -18 0", "M12 7v5l3 3");
const STATUS_PILLS = { synced: ["ok", "In Sync"], conflict: ["crit", "Conflicts"], differs: ["crit", "Conflicts"], blocked: ["held", "Held"] };

function fileHead(file) {
  return `<div class="issue-head"><span class="kind">${file.kind}${file.core ? ` · ${esc(file.core)}` : ""}</span><span class="fname">${esc(file.file_name)}</span></div>`;
}

const STATUS_ICONS = { crit: glyph("M12 5v8", "M12 18.5v.01"), ok: glyph("M5 12.5l4.5 4.5L19 7.5"), held: glyph("M9 6v12", "M15 6v12") };

function statusRow(game, file, status = file.status) {
  const [tone, label] = status === "synced" && !file.last_synced ? ["held", "Not synced"] : STATUS_PILLS[status];
  const query = new URLSearchParams({ file_name: file.file_name, emulator: file.emulator, v: file.local?.modified_at ?? "" });
  const shot = file.screenshot ? img("shot", `/api/games/${game.rom_id}/screenshot?${query}`, true) : "";
  const when = file.last_synced ? `<span>${shortStamp(file.last_synced)}</span><span class="dim">${ago(file.last_synced)}</span>` : `<span>Never synced</span>`;
  return `<div class="status-row">${shot}
      <div class="status-when">${CLOCK}<div>${when}</div></div>
      <button type="button" class="status-chip ${tone}" data-label="${label}" aria-label="${label}">${STATUS_ICONS[tone]}<span class="chip-label">${label}</span></button>
    </div>`;
}

// on a phone the chip is icon-only; press-and-hold shows its label and a quick tap flashes it (wider screens print the label)
{
  let timer;
  const chipOf = (e) => e.target.closest?.(".status-chip");
  const show = (chip, on) => chip.classList.toggle("show", on);
  document.addEventListener("pointerdown", (e) => {
    const c = chipOf(e);
    if (!c || e.pointerType === "mouse") return;
    clearTimeout(timer);
    if (c.classList.contains("show")) return show(c, false);  // a second tap dismisses the label
    show(c, true);
    const release = () => { timer = setTimeout(() => show(c, false), 1400); };
    c.addEventListener("pointerup", release, { once: true });
    c.addEventListener("pointercancel", release, { once: true });
  });
  document.addEventListener("contextmenu", (e) => { if (chipOf(e)) e.preventDefault(); });  // Android long-press menu
}

function copyLine(copy, newer) {
  return `<span class="copy-when">${stamp(copy.modified_at)} <span class="dim">(${ago(copy.modified_at)})</span>${newer ? ` <span class="newer">Newer</span>` : ""}</span>
    <span class="dim">${esc(copy.device)} · ${kb(copy.size_bytes)}${copy.hash ? ` · <span class="mono">${esc(copy.hash.slice(0, 10))}</span>` : ""}</span>`;
}


function issueSide(key, label, body, button) {
  return `<div class="side compact ${key}"><h4>${label}</h4><div class="copy">${body}</div>${button || ""}</div>`;
}

function keepButton(key, label, attrs, reason) {
  return `<button class="keep" data-keep="${key}" ${attrs} ${reason ? `disabled title="${esc(reason)}"` : ""}>${label}</button>`;
}

function conflictIssue(game, file, firstSeen = false) {
  const localNewer = new Date(file.local.modified_at) > new Date(file.romm.modified_at);
  const attrs = `data-rom="${game.rom_id}" data-file="${esc(file.file_name)}" data-emu="${esc(file.emulator)}" data-kind="${file.kind}"`;
  return `<div class="issue">${fileHead(file)}${statusRow(game, file)}
      <div class="compare ${firstSeen ? "grouped" : ""}">
        ${issueSide("local", "Syncthing", copyLine(file.local, localNewer && !firstSeen), keepButton("local", "Keep Syncthing copy", attrs))}
        ${issueSide("romm", "RomM", copyLine(file.romm, !localNewer && !firstSeen), keepButton("romm", "Keep RomM copy", attrs, state.push_only ? PUSH_ONLY_REASON : ""))}
      </div></div>`;
}

function blockedIssue(game, file) {
  const attrs = `data-rom="${game.rom_id}" data-file="${esc(file.file_name)}" data-emu="${esc(file.emulator)}" data-kind="${file.kind}"`;
  const addButton = `<button class="keep" data-add-local ${attrs} ${state.push_only ? `disabled title="${esc(PUSH_ONLY_REASON)}"` : ""}>Add to Local</button>`;
  return `<div class="issue held">${fileHead(file)}${statusRow(game, file)}
      <div class="why soft">Not downloaded: ${esc(file.note)}</div>
      <div class="compare">
        ${issueSide("local", "Existing file", file.local ? copyLine(file.local, false) : `<span class="dim">none</span>`)}
        ${issueSide("romm", `RomM copy · ${esc(file.emulator || "no emulator")}`, copyLine(file.romm, false), addButton)}
      </div></div>`;
}

// One block per original file: its current copy, then every conflict copy Syncthing kept for it, newest first.
// A "Keep ... current" button that swaps itself for an inline confirm; scope is one original file name, or "" for every file.
const bulkButton = (scope, label) =>
  state.push_only ? "" : `<div class="bulk-slot" data-scope="${esc(scope)}" data-label="${esc(label)}"><button class="pill" data-bulk="ask">${label}</button></div>`;

// One block per original file: its current copy, then every conflict copy Syncthing kept for it, newest first.
function syncthingFile(copies, game) {
  const reason = state.push_only ? PUSH_ONLY_REASON : "";
  const current = { ...copies[0].original, device: "Current file" };
  const rows = [...copies].sort((x, y) => new Date(y.modified_at) - new Date(x.modified_at)).map((c) => `
      <div class="st-copy">
        <div class="copy">${copyLine({ modified_at: c.modified_at, size_bytes: c.size_bytes, device: c.device }, false)}</div>
        <div class="st-actions">
          <button class="pill" data-keep="local" data-st="${esc(c.path)}" ${reason ? `disabled title="${esc(reason)}"` : ""}>Discard</button>
          <button class="pill use" data-keep="romm" data-st="${esc(c.path)}" ${reason ? `disabled title="${esc(reason)}"` : ""}>Use this copy</button>
        </div>
      </div>`).join("");
  const file = game?.files.find((f) => f.file_name === copies[0].file_name);
  return `<div class="issue">
      <div class="issue-head"><span class="kind">syncthing</span><span class="fname">${esc(copies[0].file_name)}</span></div>
      ${file ? statusRow(game, file, "conflict") : ""}
      <div class="st-group">
        <div class="side compact local"><h4>Current file</h4>
          <div class="st-copy first"><div class="copy">${copyLine(current, false)}</div>${bulkButton(copies[0].file_name, "Keep current copy")}</div></div>
        <div class="side compact st"><h4>${plural(copies.length, "conflict copy")}</h4>${rows}</div>
      </div>
    </div>`;
}

function syncthingSection(copies, game) {
  const byFile = new Map();
  for (const c of copies) byFile.set(c.file_name, [...(byFile.get(c.file_name) || []), c]);
  return `<section class="issue-section">
    <div class="sub-head with-action"><span>Syncthing Conflicts</span>${byFile.size > 1 ? bulkButton("", "Keep All Current Copies") : ""}</div>
    <div class="issues">${[...byFile.values()].map((c) => syncthingFile(c, game)).join("")}</div></section>`;
}

function bulkConfirm(copies) {
  const newest = copies.reduce((a, c) => (new Date(c.modified_at) > new Date(a.modified_at) ? c : a));
  return `<div class="menu-confirm">This will delete all ${copies.length} sync-conflict files. The newest is from ${stamp(newest.modified_at)}. They will be backed up.
    <div class="confirm-actions"><button class="no" data-bulk="cancel">No</button><button class="yes" data-bulk="yes">Yes</button></div></div>`;
}

function openGroup(key) {
  const g = needsGroups().find((x) => x.key === key);
  if (!g) return closeModal();
  const head = g.game ? gameHead(g.game, ` · ${plural(g.total, "issue")}`)
    : `<div class="modal-head" style="grid-template-columns:1fr auto"><div><h3>${esc(g.name)}</h3>
        <div class="meta">Folder /${esc(g.folder)} · ${plural(g.total, "issue")}</div></div><button class="pill close">Close</button></div>`;
  const section = (title, items) => (items.length ? `<section class="issue-section"><div class="sub-head">${title}</div><div class="issues">${items.join("")}</div></section>` : "");
  const ambiguous = g.ambiguous ? `<section class="issue-section"><div class="sub-head">Matches more than one RomM game</div>
    <div class="issue held"><div class="why">Not synced. Saves for <strong>${esc(g.ambiguous.name)}</strong> could pair with either game below,
      so nothing is pushed or pulled until one is removed or renamed in RomM.</div>
      <ul class="info-list">${g.ambiguous.roms.map((r) => `<li><strong>${esc(r.fs_name)}</strong> · RomM ID ${r.id}${r.name ? ` · ${esc(r.name)}` : ""}</li>`).join("")}</ul></div></section>` : "";
  modal(head + ambiguous +
    section("Changed on both sides", g.conflicts.map((f) => conflictIssue(g.game, f))) +
    section("Differs, no sync history (a one-time pick, then they sync normally)", g.differs.map((f) => conflictIssue(g.game, f, true))) +
    (g.syncthing.length ? syncthingSection(g.syncthing, g.game) : "") +
    section("Held RomM downloads", g.blocked.map((f) => blockedIssue(g.game, f))) +
    section("No Conflict", (g.game?.files ?? [])
      .filter((f) => !isOpen(f) && f.status !== "blocked" && !g.syncthing.some((c) => c.file_name === f.file_name))
      .map((f) => `<div class="issue">${fileHead(f)}${statusRow(g.game, f)}</div>`)), "wide");
  $(".scrim").onclick = (e) => {
    if (e.target.classList.contains("scrim") || e.target.classList.contains("close")) return closeModal();
    const bulk = e.target.closest("[data-bulk]")?.dataset.bulk;
    const slot = e.target.closest(".bulk-slot");
    const scoped = slot && g.syncthing.filter((c) => !slot.dataset.scope || c.file_name === slot.dataset.scope);
    if (bulk === "ask") slot.innerHTML = bulkConfirm(scoped);
    if (bulk === "cancel") slot.innerHTML = `<button class="pill" data-bulk="ask">${esc(slot.dataset.label)}</button>`;
    if (bulk === "yes") post("/api/syncthing-conflicts/keep-current", { paths: scoped.map((c) => c.path) },
      `Removed ${plural(scoped.length, "sync-conflict file")} (backed up)`, () => openGroup(key));
    if (bulk) return;
    const addButton = e.target.closest("[data-add-local]");
    if (addButton && !addButton.disabled) return addLocalDialog(g, addButton.dataset, () => openGroup(key));
    const btn = e.target.closest("[data-keep]");
    if (!btn || btn.disabled) return;
    const keep = btn.dataset.keep;
    const reopen = () => openGroup(key);
    if (btn.dataset.st) {
      const path = btn.dataset.st;
      const name = path.split("/").pop();
      confirmAction(keep === "local"
        ? "You are choosing <strong>Keep current file</strong>. The conflict copy will be moved to the backup folder."
        : "You are choosing <strong>Keep conflict copy</strong>. This will replace the current file. A backup will be made.",
        () => post("/api/syncthing-conflicts/resolve", { path, keep: keep === "local" ? "original" : "conflict" }, `Resolved ${name}`, reopen));
    } else {
      const { rom, kind, file, emu } = btn.dataset;
      confirmAction(keep === "local"
        ? "You are choosing <strong>Keep Syncthing copy</strong>. This will replace the copy on RomM. A backup will be made."
        : "You are choosing <strong>Keep RomM copy</strong>. This will overwrite the file in the sync folder, and Syncthing will send it to every device. A backup will be made.",
        () => post("/api/conflicts/resolve", { rom_id: Number(rom), kind, file_name: file, emulator: emu, keep },
          `Kept the ${keep === "local" ? "Syncthing" : "RomM"} copy of ${file}`, reopen));
    }
  };
}

const ACTIONS = {
  push: "Pushed to RomM", pull: "Pulled from RomM", conflict: "Conflict detected",
  resolve_local: "Resolved: kept the Syncthing copy", resolve_romm: "Resolved: kept the RomM copy",
  match: "Confirmed identical on both sides",
};

async function openHistory(romId) {
  const game = state.games.find((g) => g.rom_id === romId);
  const events = await (await fetch(`/api/games/${romId}/history`)).json();
  const eventRow = (e) => `<li><span class="when">${stamp(e.ts)} · ${ago(e.ts)}</span>
      <span>${e.success ? ACTIONS[e.action] : `Failed ${e.action.replace("_", " ")}`}
        <span class="detail">${e.success ? `${e.device ? esc(e.device) : ""}${e.backup_path ? ` · previous copy backed up` : ""}` : esc(e.error)}</span></span></li>`;
  const card = (file) => {
    const own = events.filter((e) => e.kind === file.kind && e.file_name === file.file_name && e.emulator === file.emulator);
    const open = isOpen(file) ? `<button class="pill" data-group="${game.rom_id}">Resolve</button>` : "";
    return `<div class="issue">${fileHead(file)}${statusRow(game, file)}
      ${own.length || open ? `<div class="card-foot">${own.length ? `<details class="file-history"><summary>${plural(own.length, "sync event")}</summary>
        <ul class="history">${own.map(eventRow).join("")}</ul></details>` : ""}${open}</div>` : ""}</div>`;
  };
  modal(`${gameHead(game, "")}<div class="issues">${game.files.map(card).join("")}</div>`);
  $(".scrim").onclick = (e) => {
    if (e.target.classList.contains("scrim") || e.target.classList.contains("close")) return closeModal();
    const group = e.target.closest("[data-group]");
    if (group) openGroup(group.dataset.group);
  };
}

// `choices` replaces the default No/Yes buttons: { label, cls, run, reason }, where a reason disables the button.
function confirmAction(message, onYes, choices = [{ label: "No", cls: "no" }, { label: "Yes", cls: "yes", run: onYes }]) {
  const box = document.createElement("div");
  box.className = "scrim";
  box.innerHTML = `<div class="modal confirm" role="alertdialog" aria-modal="true"><p>${message}</p>
    <div class="confirm-actions">${choices.map((c, i) => `<button class="${c.cls}" data-choice="${i}" ${c.reason ? `disabled title="${esc(c.reason)}"` : ""}>${c.label}</button>`).join("")}</div></div>`;
  box.onclick = (e) => {
    e.stopPropagation();
    const choice = e.target.closest("[data-choice]");
    if (choice?.disabled) return;
    if (choice) choices[choice.dataset.choice].run?.();
    if (e.target === box || choice) box.remove();
  };
  $("#modal-root").append(box);
}

function addLocalDialog(g, { rom, kind, file, emu }, reopen) {
  const options = g.blocked.find((f) => f.kind === kind && f.file_name === file && f.emulator === emu).add_local;
  const add = (layout) => () => post("/api/blocked/add-local", { rom_id: Number(rom), kind, file_name: file, emulator: emu, layout }, `Added ${file} to the sync folder`, reopen);
  const choice = (layout, label) => ({ label, cls: "yes", run: add(layout), reason: options[layout] });
  confirmAction(`Add <strong>${esc(file)}</strong> from RomM to the sync folder. Syncthing will send it to every device. Nothing is overwritten.${Object.values(options).filter(Boolean).map((r) => `<br /><span class="dim">Unavailable: ${esc(r)}</span>`).join("")}`, null, [
    { label: "Cancel", cls: "no" },
    choice("content", "Content Dir Only"),
    ...(emu ? [choice("core", "Content Dir + Core")] : []),
  ]);
}

function closeModal() { $("#modal-root").innerHTML = ""; }

async function post(url, body, message, after = closeModal) {
  const response = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!response.ok) return toast((await response.json()).detail);
  await load();
  after();  // the group modal re-opens on what is left, or closes when the game is clear
  toast(message);
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), 3200);
}

function sizeOf(files) {
  const bytes = files.reduce((sum, f) => sum + (f.local?.size_bytes || 0), 0);
  return bytes >= 1073741824 ? `${(bytes / 1073741824).toFixed(2)} GB` : kb(bytes);
}

const ROMM_CHECK_MS = 30 * 60 * 1000;
let configOpen = false;  // survives renderMenu(), which rebuilds the panel after every action
const configValue = (name, value) => (name.endsWith("_SECONDS") ? `${value} s` : name.endsWith("_DAYS") ? `${value} d` : String(value));

const stat = (label, value, cls = "") => `<div class="menu-stat ${cls}"><span>${label}</span><span>${value}</span></div>`;
let rommStatus = { version: "checking…" };  // from /api/romm-status, refreshed on its own so an unreachable RomM never blocks the page
const rommRow = () => (rommStatus.error
  ? stat("RomM Version", `<span class="hazard">⚠ Unreachable</span>`, "error") + `<div class="menu-note">${esc(rommStatus.error)}</div>`
  : stat("RomM Version", esc(rommStatus.version)));

async function checkRomm() {
  rommStatus = await (await fetch("/api/romm-status")).json();
  $("#menu-toggle").classList.toggle("warn", !!rommStatus.error);
  $("#romm-row").innerHTML = rommRow();
}

function renderMenu() {
  const files = state.games.flatMap((g) => g.files.filter((f) => f.local));
  const saves = files.filter((f) => f.kind === "save");
  const states = files.filter((f) => f.kind === "state");
  const b = state.backups;
  const lib = state.romm_library;
  const info = state.server_info;
  const configRows = Object.entries(info.config).map(([name, value]) => stat(`<span class="mono">${name}</span>`, esc(configValue(name, value)))).join("");
  $("#menu-panel").innerHTML = `
    <button class="menu-close" data-action="close" aria-label="Close"><svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18" /></svg></button>
    <div class="menu-label">Server info</div>
    ${stat("RomM_Syncthing_Sync Version", esc(info.sync_version))}
    <div id="romm-row">${rommRow()}</div>
    ${stat("Sync Mode", SYNC_MODE_NAMES[state.sync_mode])}
    <details class="menu-config" ${configOpen ? "open" : ""}>
      <summary class="menu-stat"><span>Sync Configuration</span><span class="chev">▶</span></summary>
      ${configRows}
    </details>
    <div class="menu-label">Server stats</div>
    ${stat(plural(saves.length, "save file"), sizeOf(saves))}
    ${stat(plural(states.length, "state file"), sizeOf(states))}
    ${stat(plural(b.count, "backup"), kb(b.size_bytes))}
    ${stat(`Total tracked files: ${files.length}`, sizeOf(files), "separated total")}
    ${stat(`RomM library: ${lib.roms.toLocaleString()} ROMs`, `${(lib.size_bytes / 2 ** 30).toFixed(0)} GB`, "separated")}
    <div class="menu-label">Maintenance</div>
    <div id="prune-slot"><button class="pill menu-action" data-action="prune">Prune backups</button></div>
    <button class="pill menu-action" data-action="rebuild-library">Rebuild romm_syncthing_sync library</button>
    <button class="pill menu-action" data-action="pull" ${state.push_only ? "disabled" : ""}>Pull changes from RomM</button>
    ${state.push_only ? `<div class="menu-note">Push-only: nothing is written to the sync folders.</div>` : ""}`;
}

async function runAction(button, action) {
  document.querySelectorAll(".menu-action").forEach((b) => (b.disabled = true));
  button.classList.add("busy");
  const poll = ["rebuild-library", "pull"].includes(action) && setInterval(async () => {
    const { done, total } = await (await fetch("/api/actions/rebuild-library/progress")).json();
    if (total) button.style.setProperty("--progress", `${Math.round((100 * done) / total)}%`);
  }, 400);
  const response = await fetch(`/api/actions/${action}`, { method: "POST" });
  clearInterval(poll);
  const body = await response.json();
  await load();
  renderMenu();  // an unchanged state skips render(), which would otherwise re-enable the buttons
  toast(response.ok ? body.message : body.detail);
}

function setMenuOpen(open) {
  $("#menu-panel").hidden = !open;
  $("#menu-toggle").setAttribute("aria-expanded", String(open));
  document.body.classList.toggle("menu-open", open);
  if (open) {
    placeMenuPanel();
    checkRomm();
  }
}
$("#menu-toggle").addEventListener("click", () => setMenuOpen($("#menu-panel").hidden));
$("#menu-panel").addEventListener("toggle", (e) => (configOpen = e.target.open), true);
$("#menu-panel").addEventListener("click", (e) => {
  const button = e.target.closest("button");
  if (!button) return;
  if (button.dataset.action === "prune") {
    const b = state.backups;
    $("#prune-slot").innerHTML = `<div class="menu-confirm">Delete all ${plural(b.count, "backup")} (${kb(b.size_bytes)})? This cannot be undone.
      <div class="confirm-actions"><button class="no">No</button><button class="yes" data-action="prune-backups">Yes</button></div></div>`;
  } else if (button.classList.contains("no")) {
    renderMenu();
  } else if (button.dataset.action === "close") {
    setMenuOpen(false);
  } else if (button.dataset.action) {
    runAction(button, button.dataset.action);
  }
});
const sheetLayout = matchMedia("(max-width: 640px)");
sheetLayout.addEventListener("change", () => setActionBar(lastAction));
for (const type of ["touchmove", "wheel"]) {
  document.addEventListener(type, (e) => {
    if (!sheetLayout.matches) return;
    const card = e.target.closest?.(".modal");
    if (card) {  // a card scrolls only when its content overflows; otherwise the swipe would drag the page behind
      if (card.scrollHeight <= card.clientHeight) e.preventDefault();
    } else if (($("#modal-root").firstChild || document.body.classList.contains("menu-open")) && !e.target.closest("#menu-panel")) e.preventDefault();
  }, { passive: false });
}
document.addEventListener("click", (e) => {
  const path = e.composedPath();  // the clicked button may already be replaced
  if (!path.includes($("#menu-panel")) && !path.includes($("#menu-toggle"))) setMenuOpen(false);
});

function render() {
  lazyBodies.clear();
  for (const chip of [$("#push-only-chip"), $("#bar-push-only")]) {
    chip.hidden = !state.push_only;
    chip.textContent = "Push-only";
    chip.title = PUSH_ONLY_REASON;
  }
  renderHero();
  renderNeedsYou();
  renderRecent();
  renderLocal();
  renderRomm();
  renderUnmatched();
  renderBottom();
  renderMenu();
}

let foldSync;
document.addEventListener("toggle", (e) => {
  if (!e.target.dataset.tree) return;
  treeOpen.set(e.target.dataset.tree, e.target.open);
  if (e.target.open) fillTree(e.target);
  // toggle events arrive one task per <details>, so a fold-all would otherwise run syncFolds once per tree
  foldSync ??= requestAnimationFrame(() => { foldSync = null; syncFolds(); });
}, true);

document.addEventListener("click", (e) => {
  if (e.target.closest(".modal")) return;
  const fold = e.target.closest("[data-fold]");
  if (fold) {
    const open = !fold.classList.contains("open");
    // building a body adds the nested trees, so expanding repeats until none are left closed
    let trees;
    do {
      trees = treesOf(fold.dataset.fold);
      trees.forEach((d) => { if (open) fillTree(d); d.open = open; });
    } while (open && trees.length < treesOf(fold.dataset.fold).length);
    return;
  }
  const hist = e.target.closest("[data-hist]");
  const group = e.target.closest("[data-group]");
  const row = e.target.closest(".file-row.conflict");
  if (hist) {
    e.preventDefault();  // a game name inside a <summary> would otherwise also toggle the tree
    openHistory(Number(hist.dataset.hist));
  } else if (group) {
    openGroup(group.dataset.group);
  } else if (row) {
    openGroup(row.dataset.rom);
  }
});
document.addEventListener("keydown", (e) => e.key === "Escape" && closeModal());

const mark = $(".brand-mark");
// docked in the action bar, the maintenance button shows the mark in place of its tools icon
const barMark = mark.cloneNode(true);
barMark.setAttribute("class", "bar-mark");
barMark.setAttribute("aria-hidden", "true");
barMark.removeAttribute("role");
barMark.querySelector("title").remove();  // it would replace the button's "Maintenance" tooltip
// the button wears a gear in place of the sync loop, so it reads as settings and not as a second logo
const GEAR_LOOP = `<g class="mark-loop"><g class="mark-sync"><path transform="rotate(0 32 32)" d="M20.61 4.5H43.39L42.36 7H21.64Z"/><path transform="rotate(225 32 32)" d="M20.61 4.5H43.39L42.36 7H21.64Z"/><path transform="rotate(270 32 32)" d="M20.61 4.5H43.39L42.36 7H21.64Z"/><path transform="rotate(315 32 32)" d="M20.61 4.5H43.39L42.36 7H21.64Z"/></g><g class="mark-romm"><path transform="rotate(45 32 32)" d="M20.61 4.5H43.39L42.36 7H21.64Z"/><path transform="rotate(90 32 32)" d="M20.61 4.5H43.39L42.36 7H21.64Z"/><path transform="rotate(135 32 32)" d="M20.61 4.5H43.39L42.36 7H21.64Z"/><path transform="rotate(180 32 32)" d="M20.61 4.5H43.39L42.36 7H21.64Z"/></g><g class="mark-tooth"><path transform="rotate(0 32 32)" d="M25.5 5.1L27.75 -1H36.25L38.5 5.1Z"/><path transform="rotate(45 32 32)" d="M25.5 5.1L27.75 -1H36.25L38.5 5.1Z"/><path transform="rotate(90 32 32)" d="M25.5 5.1L27.75 -1H36.25L38.5 5.1Z"/><path transform="rotate(135 32 32)" d="M25.5 5.1L27.75 -1H36.25L38.5 5.1Z"/><path transform="rotate(180 32 32)" d="M25.5 5.1L27.75 -1H36.25L38.5 5.1Z"/><path transform="rotate(225 32 32)" d="M25.5 5.1L27.75 -1H36.25L38.5 5.1Z"/><path transform="rotate(270 32 32)" d="M25.5 5.1L27.75 -1H36.25L38.5 5.1Z"/><path transform="rotate(315 32 32)" d="M25.5 5.1L27.75 -1H36.25L38.5 5.1Z"/></g></g>`;
barMark.querySelector(".mark-loop").outerHTML = GEAR_LOOP;
$("#menu-toggle").append(barMark);
// desktop: the same mark leads the bar as the app's logo and scrolls to the top
const brandMark = mark.cloneNode(true);
brandMark.setAttribute("class", "bar-brand-mark");
brandMark.setAttribute("aria-hidden", "true");
brandMark.removeAttribute("role");
brandMark.querySelector("title").remove();
$("#bar-brand").append(brandMark);
$("#bar-brand").addEventListener("click", (e) => { e.preventDefault(); scrollTo({ top: 0, behavior: "smooth" }); });
// a touch enters on press and leaves on release, so these cover both hover and touch-and-hold
for (const el of [mark, $("#menu-toggle"), $("#bar-brand")]) {
  for (const [type, spinning] of [["pointerenter", true], ["pointerleave", false], ["pointercancel", false]]) {
    el.addEventListener(type, () => el.classList.toggle("spinning", spinning));
  }
}
mark.addEventListener("contextmenu", (e) => e.preventDefault());  // Android long-press menu

// the same sun/moon switch sits in the top row on a phone and in the bar on desktop
const THEME_SWITCH = `<span class="sw-thumb"></span><span class="sw-opt">${glyph("M8 12a4 4 0 1 0 8 0a4 4 0 1 0 -8 0", "M3 12h1m8 -9v1m8 8h1m-9 8v1m-6.4 -15.4l.7 .7m12.1 -.7l-.7 .7m0 11.4l.7 .7m-12.1 -.7l-.7 .7")}</span>
  <span class="sw-opt">${glyph("M12 3c.132 0 .263 0 .393 0a7.5 7.5 0 0 0 7.92 12.446a9 9 0 1 1 -8.313 -12.454z")}</span>`;
const THEME_BUTTONS = [$("#theme-toggle"), $("#bar-theme")];
for (const button of THEME_BUTTONS) button.innerHTML = THEME_SWITCH;
function showTheme() {
  const dark = document.documentElement.dataset.theme === "dark";
  for (const button of THEME_BUTTONS) button.setAttribute("aria-checked", String(dark));
}
for (const button of THEME_BUTTONS) {
  button.addEventListener("click", () => {
    document.documentElement.dataset.theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    showTheme();
  });
}
showTheme();

const LOG_LINES = 500;
// loguru's terminal colors, as SGR code -> CSS class
const ANSI_CLASSES = { 1: "a-bold", 2: "a-dim", 31: "a-red", 32: "a-green", 33: "a-yellow", 34: "a-blue", 35: "a-magenta", 91: "a-red", 94: "a-blue", 95: "a-magenta" };
function ansiToHtml(line) {
  let classes = [];
  return line.split(/\x1b\[([\d;]*)m/).map((part, i) => {
    if (i % 2) {
      classes = Number(part) ? [...classes, ANSI_CLASSES[part]].filter(Boolean) : [];
      return "";
    }
    return classes.length ? `<span class="${classes.join(" ")}">${esc(part)}</span>` : esc(part);
  }).join("");
}
async function loadLogs() {
  const pre = $("#applog-body");
  const atBottom = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 4;
  pre.innerHTML = (await (await fetch("/api/logs")).json()).slice(-LOG_LINES).map(ansiToHtml).join("");
  if (atBottom) pre.scrollTop = pre.scrollHeight;
}
$("#applog").addEventListener("toggle", (e) => e.target.open && loadLogs());

let lastStateText;  // the periodic reload skips render() when the server state did not change
async function load() {
  const stateText = await (await fetch("/api/state")).text();
  if (stateText === lastStateText) {
    renderHero();  // only these two show relative times
    renderBottom();
    return;
  }
  lastStateText = stateText;
  state = JSON.parse(stateText);
  render();
  if ($("#applog").open) loadLogs();
}

load().then(checkRomm);
setInterval(checkRomm, ROMM_CHECK_MS);
const refresh = () => !document.hidden && !$("#modal-root").innerHTML && $("#menu-panel").hidden && load();
setInterval(refresh, 30000);
document.addEventListener("visibilitychange", refresh);
