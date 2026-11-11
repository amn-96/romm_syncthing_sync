let state;

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const asset = (path) => `/romm/${path.replace(/^\//, "")}`;
const icon = (slug) => asset(`assets/platforms/${slug}.svg`);
const cover = (path) => (path ? asset(path) : "");
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
const kb = (b) => (b == null ? "unknown" : b >= 1048576 ? `${(b / 1048576).toFixed(1)} MB` : `${Math.round(b / 1024)} KB`);
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
  const listed = ls ? [...ls.pushed.map((f) => [f, "pushed to RomM"]), ...ls.pulled.map((f) => [f, "pulled from RomM"])] : [];
  $("#hero").innerHTML = `
    <div class="hero ${tone}"><div class="hero-card">
      <h1>${headline}</h1>
      <div class="hero-sub">
        ${ls ? `<span>Last sync <strong>${clock(ls.finished_at)}</strong> (${ago(ls.finished_at)})</span>
          <span>${ls.errors ? `<strong>${ls.errors}</strong> failed` : "no errors"}</span>` : "<span>No sync has finished yet</span>"}
        <span>Mode: ${state.sync_mode === "watch" ? "Incremental" : "Periodic"}</span>
        ${state.push_only ? `<span><strong>Push-only</strong>: nothing is written to the sync folders</span>` : ""}
      </div>
      ${listed.length ? `<details class="hero-toggle">
        <summary class="pill">${plural(listed.length, "change")}</summary>
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
  const sub = g.game ? `<img class="plat-icon" src="${icon(g.game.platform_slug)}" alt="" />${esc(g.game.platform_name)}` : `Folder /${esc(g.folder)}`;
  return `<button class="conflict-card ${g.game ? "" : "plain"}" data-group="${esc(g.key)}">
      ${g.game ? `<img class="cover" src="${cover(g.game.cover)}" alt="" loading="lazy" />` : ""}
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
const tree = (cls, key, hit, inner) => `<details class="tree ${cls} ${hit ? "has-conflict" : ""}" data-tree="${esc(key)}" ${treeOpen.get(key) ?? hit ? "open" : ""}>${inner}</details>`;

function platformSummary(p, count) {
  const hit = p.games.some((g) => g.files.some(isOpen));
  return [hit, `<summary><img class="plat-icon" src="${icon(p.slug)}" alt="" />${esc(p.name)}<span class="n">${count}</span><span class="chev">▶</span></summary>`];
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

function renderLocal() {
  const body = byPlatform().map((p) => {
    const files = p.games.flatMap((g) => g.files.filter((f) => f.local && f.status !== "blocked").map((f) => ({ g, f })));
    const [hit, summary] = platformSummary(p, `${files.length} files`);
    const cores = new Map();
    for (const x of files) {
      const key = x.f.core || "";
      if (!cores.has(key)) cores.set(key, []);
      cores.get(key).push(x);
    }
    const flat = (cores.get("") || []).map(({ g, f }) => fileRow(g, f, "local")).join("");
    const nested = [...cores.entries()].filter(([k]) => k).map(([core, xs]) => {
      const coreHit = xs.some(({ f }) => isOpen(f));
      return tree("sub", `local:${p.slug}/${core}`, coreHit, `<summary><span class="folder-glyph">▤</span>${esc(core)}<span class="n">${xs.length}</span><span class="chev">▶</span></summary>
        <div class="tree-body">${xs.map(({ g, f }) => fileRow(g, f, "local")).join("")}</div>`);
    }).join("");
    return tree("", `local:${p.slug}`, hit, `${summary}
      <div class="tree-body"><div class="folder-glyph" style="padding:4px 10px">/${esc(p.games[0].fs_folder)}</div>${nested}${flat}</div>`);
  }).join("");
  $("#panel-local").innerHTML = `
    <div class="panel-head"><span class="side-badge"></span><h2>Syncthing</h2>${foldPill("local")}<span class="sub">${state.push_only ? "read-only" : "read/write"}</span></div>${body}`;
  syncFolds();
}

function renderRomm() {
  const u = state.user;
  const body = byPlatform().map((p) => {
    const [hit, summary] = platformSummary(p, `${p.games.length} games`);
    const games = p.games.map((g) => {
      const gHit = g.files.some(isOpen);
      const romm = g.files.filter((f) => f.romm);
      return tree("sub", `romm:${p.slug}/${g.rom_id}`, gHit, `<summary><span class="game-line game-link" data-hist="${g.rom_id}"><img class="mini-cover" src="${cover(g.cover)}" alt="" loading="lazy" /><span class="gname">${esc(g.name)}</span></span>
        <span class="n">${romm.length}</span><span class="chev">▶</span></summary>
        <div class="tree-body">${romm.map((f) => fileRow(g, f, "romm")).join("")}</div>`);
    }).join("");
    return tree("", `romm:${p.slug}`, hit, `${summary}<div class="tree-body">${games}</div>`);
  }).join("");
  $("#panel-romm").innerHTML = `
    <div class="panel-head"><span class="side-badge"></span><h2>RomM</h2>${foldPill("romm")}
      <span class="user-chip"><span class="avatar">${esc(u.username[0].toUpperCase())}</span>${esc(u.username)}<span class="role">${esc(u.role)}</span></span>
    </div>${body}`;
  syncFolds();
}

function renderUnmatched() {
  $("#unmatched").hidden = !state.unmatched.length;
  $("#unmatched > summary").textContent = `Unmatched games (${state.unmatched.length})`;
  $("#unmatched-body").innerHTML = state.unmatched.map((u) => `<li><strong>${esc(u.name)}</strong> in ${esc(u.local_platform)} · ${plural(u.files.length, "file")}.
    No RomM game with this file name on this platform. Check platform_mapping.yaml and the ROM's file name.</li>`).join("");
}

function renderBottom() {
  const files = state.games.flatMap((g) => g.files);
  $("#bottombar").innerHTML = `
    <span><strong>${state.games.length}</strong> games tracked</span>
    <span><strong>${files.filter((f) => f.kind === "save").length}</strong> saves</span>
    <span><strong>${files.filter((f) => f.kind === "state").length}</strong> states</span>
    <span><strong>${byPlatform().length}</strong> platforms</span>
    <a class="romm-link" href="${esc(state.romm_url)}" target="_blank" rel="noopener" title="Open RomM">
      <img src="${asset("assets/isotipo.svg")}" alt="RomM" />Open RomM</a>`;
}

function modal(inner, cls = "") {
  $("#modal-root").innerHTML = `<div class="scrim"><div class="modal ${cls}" role="dialog" aria-modal="true">${inner}</div></div>`;
}

function gameHead(game, subtitle) {
  return `<div class="modal-head">
    <img class="cover" src="${cover(game.cover)}" alt="" />
    <div><h3>${esc(game.name)}</h3>
      <div class="meta"><img class="plat-icon" src="${icon(game.platform_slug)}" alt="" />${esc(game.platform_name)}${subtitle}</div></div>
    <button class="pill close">Close</button></div>`;
}

function copyLine(copy, newer) {
  return `<span class="copy-when">${stamp(copy.modified_at)} <span class="dim">(${ago(copy.modified_at)})</span>${newer ? ` <span class="newer">Newer</span>` : ""}</span>
    <span class="dim">${esc(copy.device)} · ${kb(copy.size_bytes)}${copy.hash ? ` · <span class="mono">${esc(copy.hash.slice(0, 10))}</span>` : ""}</span>`;
}

function issueRow(title, tag, left, right) {
  return `<div class="issue">
      <div class="issue-head"><span class="kind">${tag}</span><span class="fname">${esc(title)}</span></div>
      <div class="compare">${left}${right}</div>
    </div>`;
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
  return issueRow(file.file_name, `${file.kind}${file.core ? ` · ${esc(file.core)}` : ""}`,
    issueSide("local", "Syncthing", copyLine(file.local, localNewer && !firstSeen), keepButton("local", "Keep Syncthing copy", attrs)),
    issueSide("romm", "RomM", copyLine(file.romm, !localNewer && !firstSeen), keepButton("romm", "Keep RomM copy", attrs, state.push_only ? PUSH_ONLY_REASON : "")));
}

function blockedIssue(game, file) {
  const attrs = `data-rom="${game.rom_id}" data-file="${esc(file.file_name)}" data-emu="${esc(file.emulator)}" data-kind="${file.kind}"`;
  const addButton = `<button class="keep" data-add-local ${attrs} ${state.push_only ? `disabled title="${esc(PUSH_ONLY_REASON)}"` : ""}>Add to Local</button>`;
  return `<div class="issue held">
      <div class="issue-head"><span class="kind">${file.kind}</span><span class="fname">${esc(file.file_name)}</span></div>
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
function syncthingFile(copies) {
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
  return `<div class="issue">
      <div class="issue-head"><span class="kind">syncthing</span><span class="fname">${esc(copies[0].file_name)}</span></div>
      <div class="side compact local"><h4>Current file</h4>
        <div class="st-copy first"><div class="copy">${copyLine(current, false)}</div>${bulkButton(copies[0].file_name, "Keep current copy")}</div></div>
      <div class="side compact romm"><h4>${plural(copies.length, "conflict copy")}</h4>${rows}</div>
    </div>`;
}

function syncthingSection(copies) {
  const byFile = new Map();
  for (const c of copies) byFile.set(c.file_name, [...(byFile.get(c.file_name) || []), c]);
  return `<section class="issue-section">
    <div class="sub-head with-action"><span>Syncthing Conflicts</span>${byFile.size > 1 ? bulkButton("", "Keep All Current Copies") : ""}</div>
    <div class="issues">${[...byFile.values()].map(syncthingFile).join("")}</div></section>`;
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
    (g.syncthing.length ? syncthingSection(g.syncthing) : "") +
    section("Held RomM downloads", g.blocked.map((f) => blockedIssue(g.game, f))), "wide");
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
  modal(`${gameHead(game, "")}
    ${events.length ? `<ul class="history">${events.map((e) => `<li><span class="when">${stamp(e.ts)}<br />${ago(e.ts)}</span>
      <span>${e.success ? ACTIONS[e.action] : `Failed ${e.action.replace("_", " ")}:`} <strong>${esc(e.file_name)}</strong>
        <div class="detail">${e.success ? `${e.device ? esc(e.device) : ""}${e.backup_path ? ` · previous copy backed up` : ""}` : esc(e.error)}</div></span></li>`).join("")}</ul>`
      : `<div class="empty-ok">No sync history for this game yet.</div>`}`);
  $(".scrim").onclick = (e) => (e.target.classList.contains("scrim") || e.target.classList.contains("close")) && closeModal();
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

function renderMenu() {
  const files = state.games.flatMap((g) => g.files.filter((f) => f.local));
  const saves = files.filter((f) => f.kind === "save");
  const states = files.filter((f) => f.kind === "state");
  const b = state.backups;
  const lib = state.romm_library;
  const stat = (label, value, cls = "") => `<div class="menu-stat ${cls}"><span>${label}</span><span>${value}</span></div>`;
  $("#menu-panel").innerHTML = `
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
  toast(response.ok ? body.message : body.detail);
}

$("#menu-toggle").addEventListener("click", () => {
  const panel = $("#menu-panel");
  panel.hidden = !panel.hidden;
  $("#menu-toggle").setAttribute("aria-expanded", String(!panel.hidden));
});
$("#menu-panel").addEventListener("click", (e) => {
  const button = e.target.closest("button");
  if (!button) return;
  if (button.dataset.action === "prune") {
    const b = state.backups;
    $("#prune-slot").innerHTML = `<div class="menu-confirm">Delete all ${plural(b.count, "backup")} (${kb(b.size_bytes)})? This cannot be undone.
      <div class="confirm-actions"><button class="no">No</button><button class="yes" data-action="prune-backups">Yes</button></div></div>`;
  } else if (button.classList.contains("no")) {
    renderMenu();
  } else if (button.dataset.action) {
    runAction(button, button.dataset.action);
  }
});
document.addEventListener("click", (e) => {
  if (!e.composedPath().includes($(".menu"))) $("#menu-panel").hidden = true;  // the clicked button may already be replaced
});

function render() {
  const chip = $("#push-only-chip");
  chip.hidden = !state.push_only;
  chip.textContent = "Push-only";
  chip.title = PUSH_ONLY_REASON;
  renderHero();
  renderNeedsYou();
  renderLocal();
  renderRomm();
  renderUnmatched();
  renderBottom();
  renderMenu();
}

document.addEventListener("toggle", (e) => {
  if (!e.target.dataset.tree) return;
  treeOpen.set(e.target.dataset.tree, e.target.open);
  syncFolds();
}, true);

document.addEventListener("click", (e) => {
  if (e.target.closest(".modal")) return;
  const fold = e.target.closest("[data-fold]");
  if (fold) {
    treesOf(fold.dataset.fold).forEach((d) => (d.open = !fold.classList.contains("open")));
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

const themeBtn = $("#theme-toggle");
themeBtn.addEventListener("click", () => {
  const dark = document.documentElement.dataset.theme !== "dark";
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  themeBtn.textContent = dark ? "Light" : "Dark";
});

async function loadLogs() {
  const pre = $("#applog-body");
  const atBottom = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 4;
  pre.textContent = (await (await fetch("/api/logs")).json()).join("");
  if (atBottom) pre.scrollTop = pre.scrollHeight;
}
$("#applog").addEventListener("toggle", (e) => e.target.open && loadLogs());

async function load() {
  state = await (await fetch("/api/state")).json();
  render();
  if ($("#applog").open) loadLogs();
}

load();
setInterval(() => !$("#modal-root").innerHTML && $("#menu-panel").hidden && load(), 30000);
