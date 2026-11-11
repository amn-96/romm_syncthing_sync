# Sync Strategy

How romm_sync decides whether a save or state is pushed to RomM, pulled into the sync folders, left alone, or held for you. For detailed application configuration, see the [configuration guide](../CONFIGURATION_GUIDE.md); for documention on the database tables see [this doc](SYNC_DB.md).

Terms: 
- **local** is a file in the sync folder (the one Syncthing shares), 
- **RomM** is the copy stored in RomM, 
- **two-way** is the default mode
- **push-only** is when `PUSH_ONLY=true` (in your .env) or any sync folder is not writable (for example mounted `:ro`). Push-only behaves identically to this app before version 2.x.

## 1. The model

A file can exist in three places:

| Place | What it holds |
|---|---|
| Sync folder | The file itself, shared with your devices by Syncthing |
| RomM | One copy per file, with an upload time (`updated_at`) and, for saves, an md5 `content_hash` |
| History DB (`DATA_DIR/romm_sync.db`) | A log of every push, pull, match, resolution, conflict and failed transfer |

**Baseline.** A file's baseline is the newest successful history row of type `push`, `pull`, `match`, `resolve_local` or `resolve_romm`. It records the local and RomM md5 at the last moment both sides were known to be equal or were made equal by a transfer. Each side is "changed" if its current md5 differs from the baseline's:

| Event | When it is written |
|---|---|
| `push` | A local file was uploaded to RomM |
| `pull` | RomM's copy was written to the sync folder (also for files that exist only in RomM) |
| `match` | Both sides were found byte-identical with no matching baseline |
| `resolve_local` / `resolve_romm` | You picked a side in the web UI |
| `conflict` | A file newly became a conflict (informational; not a baseline) |

Failed transfers are logged with `success = 0` and are never a baseline.

**Why md5 and not timestamps.** Syncthing, RetroArch and RomM all set different times, and device clocks drift, so "newer" may not necessarily mean "the one you want". Content hashes say whether a file actually changed since the last sync. RomM uses md5 for saves, so local and RomM hashes compare directly. Timestamps still play one narrow role, described in section 3.

Without a baseline (a new install, a deleted history DB, or a ROM that RomM re-created with a new id), nothing is assumed: identical files are recorded as a `match`, different files are held (see section 3). 

This is deliberately conservative. You may see more frequent sync issues, but in return, errant data loss is less likely.

## 2. Matching

### Local game to RomM game

1. **Platform.** The platform folder name is looked up (case-insensitive) in `platform_mapping.yaml`, which maps a RomM platform slug to a list of folder names. If the folder is not in the mapping, the folder name must equal the RomM slug exactly. See [Platform mapping](../CONFIGURATION_GUIDE.md#platform-mapping).
2. **Game name.** The file name without its extension, with two things stripped: a web-player timestamp (`Game [2025-08-08 00-06-19-138].srm` becomes `Game`) and a ROM extension some cores keep (`Game.gba.sav` becomes `Game`). Saves, states (`.state`, `.state1`, `.state.auto`, melonDS `.ml1`) and their `.state.png` screenshots with the same name form one game.
3. **RomM game.** The name must equal the RomM game's `fs_name` (with or without extension), case-sensitive, within that platform. It is not the display name: RomM's "Castlevania: Symphony of the Night" is matched by its file name "Castlevania - Symphony of the Night (USA)".

| Outcome | What happens |
|---|---|
| No match | Warning in the log; the game is skipped |
| One match | The game syncs |
| Several matches (for example `Foo.zip` and `Foo.nes`) | The game is **held**: nothing is pushed or pulled for it, and it is listed under Needs You with the candidate ROMs. Remove the duplicate in RomM. |

The RomM side is held for the same reason: pulls skip any RomM game whose name is shared by another game on the same platform.

### Which RomM row a file pairs with

| File | Pairs with |
|---|---|
| Save in a core folder (`GBA/mGBA/Game.srm`) | The RomM save with the same file name **and** the same emulator (`mgba`) |
| Flat save (`GBA/Game.srm`) | The RomM save with the same file name and **any** emulator, preferring one with no emulator |
| State, anywhere | The RomM state with the same file name; emulator is ignored. Pushing a state from a core folder re-points the RomM state's emulator to that core. |

Local states are tracked by file name, so `Game.state` and `Game.state1` are separate states. If the same file name exists in several folders of a platform (flat and core, or two cores), only the newest copy is synced.

### Core folders

A subfolder of a platform folder counts as a core folder only if its name is a core RomM's web player (EmulatorJS) knows, plus gpSP. Matching ignores case and punctuation (`mGBA` and `mgba` both become `mgba`). Other subfolders (for example `gc/User`) are ignored. The folder name becomes the save's `emulator` in RomM.

Sorting by core is recommended for two reasons:

- RomM's web player saves arrive with an emulator set. With a core folder the pull has an exact destination; without one the file cannot be placed and is held (section 6).
- A core folder is the only way to tell RomM which emulator a file belongs to, which the web player needs to find it.

## 3. Decision table

Each local file is compared with its paired RomM file. Rules that always hold in two-way mode:

- **Never newer-wins for disagreements.** If both sides differ and history cannot say which is right, the file is held. A timestamp never decides between two different contents.
- **Never overwrite an existing path.** A file that exists only in RomM is downloaded only to a path that is free. If the path is taken by a different file, it is held.
- A pull overwrites the local file without a backup, because it only happens when the local copy is unchanged since the baseline. Anything that loses a real change goes to the backup folder (section 6).

Two-way mode (default):

| Situation | Action |
|---|---|
| Only local | **Push** (add to RomM) |
| Only in RomM | **Pull** to the matching sync folder if the path is free; otherwise **blocked** (held, with a reason) |
| Identical | Nothing; a `match` baseline is recorded if there is none |
| Different, no baseline | **Differs** (held). Pick a side in the web UI. |
| Local changed only | **Push**, provided the local mtime is newer than RomM's `updated_at` (see below) |
| RomM changed only | **Pull** (overwrite local) |
| Both changed | **Conflict** (held). Pick a side in the web UI. |
| Neither changed | Nothing |

Held means romm_sync neither pushes nor pulls that file until you resolve it or the situation changes.

**The mtime gate on pushes.** For "local changed only", the push is still gated on the local file's modified time being newer than RomM's `updated_at`. If a device's clock is behind, a real local change can sit unpushed with no warning. A pulled file gets its mtime set to RomM's `updated_at`, so it is not pushed back. If the content is identical but the local mtime is newer, the same bytes may be uploaded once more; this is harmless and refreshes the baseline.

**RomM's device sync.** For saves RomM holds in a slot, RomM's own sync check (`/api/sync/negotiate`) is consulted too, and can upgrade a verdict to a conflict (RomM says conflict, or disagrees on direction with what the history says). If that request fails (RomM older than 5.x has no device sync), a warning is logged and the history DB alone decides.

### Push-only

Push-only is for read-only sync folders. The app never writes to a sync folder, pull passes and the scheduled pull are skipped, and the web UI refuses any action that writes locally ("Keep RomM copy", Add to Local, Syncthing conflict actions).

| Situation | Push-only behaviour |
|---|---|
| Only local | Push |
| Only in RomM | Nothing (not downloaded, not listed as blocked) |
| Identical | Nothing, except an upload if the local mtime is newer |
| Different (any history) | **Pushed if the local mtime is newer than RomM's `updated_at`** (mtime newer-wins). No hold, no backup. |
| RomM changed only | Nothing; local is not touched |
| Both changed | Same as "different": the newer mtime pushes. The web UI may still show it as a conflict because the history check still runs for display. |

So the "never newer-wins" rule does **not** apply in push-only mode. If you keep a push-only instance next to RomM's web player, saves made in the browser can be overwritten by an older-looking-but-newer-mtime local file. "Keep local copy" in the UI still works in push-only.

### FORCE_PUSH_SYNC

`FORCE_PUSH_SYNC=true` (watch-mode syncs and periodic syncs) skips the checks and the mtime gate: every matched local file is uploaded, overwriting RomM, including files that would be conflicts or differs. `FULL_INITIAL_SYNC=force` does the same for the startup sync only. Force does not skip the pull pass that runs first in a full sync. The history still records the push as a baseline, but nothing backs up RomM's overwritten copy. Leave it off unless you intend to make the sync folders authoritative.

## 4. States

RomM gives states no content hash and no slot, and the negotiate API does not cover them. To compare a state, romm_sync needs RomM's md5, which it gets in this order:

1. An in-memory cache for the running process.
2. The hash recorded for that file with the same RomM `updated_at` (from the baseline, then from the snapshot table).
3. Otherwise **download the state once** and hash it.

A download therefore happens only for a RomM state with an `updated_at` romm_sync has never recorded. That includes every RomM-only state, since the snapshot hashes those too. The cost lands on the first sync after upgrading from a version that did not hash states (or on a fresh install with many states in RomM), and again for any state that changes on RomM. After that, the recorded hash is reused and nothing is downloaded for unchanged states.

Baselines recorded before states were hashed have no RomM hash; for those, "RomM changed" falls back to comparing `updated_at` at whole-second precision (upload responses carry microseconds, later GETs do not).

**Screenshots.** When a state is pushed, the `<state>.png` next to it is uploaded with it, if it exists. A change to the `.png` alone does not trigger a push. Pulling a state from RomM writes only the state: no `.state.png` is created, so RetroArch shows no thumbnail for pulled states. RomM-only screenshots are not synced.

**Slots.** A state's slot (`.state` is 0, `.state3` is 3, `.ml2` is 2, `.state.auto` is -1) is read from its file extension. RomM states have no slot in the API; romm_sync reads it from the RomM file's extension too. States are paired by file name, not slot.

## 5. Triggers

| Trigger | When | What it does |
|---|---|---|
| Startup full sync | On start; `FULL_INITIAL_SYNC` = `true` (default), `false` or `force` | Pull pass, then push pass, over all games |
| Watch (`SYNC_MODE=watch`, default) | Files change in a sync folder | Rescans only the affected games and pushes (and pulls, if only RomM changed for those files) |
| Scheduled pull | Watch mode, two-way only, every `PULL_INTERVAL_SECONDS` (default 1800) | Pull pass only; never pushes. Catches changes made on the RomM side, which no file event can see |
| Periodic (`SYNC_MODE=periodic`) | Every `SYNC_INTERVAL_SECONDS` (default 1800) | A full sync |
| Web UI | "Rebuild library" runs a full sync; "Pull" runs a pull pass (refused in push-only) | As above |

A full sync pulls first so files that exist only in RomM join the library before the push pass.

**Debounce.** In watch mode each file event restarts a timer of `WATCHDOG_DELAY_SECONDS` (default 60). The sync runs only after that long with no further events. Events arriving during a sync are queued and start a new timer when it ends. Ignored: Syncthing and temp files (names containing `sync-conflict`, `syncthing`, `.stfolder`, `.stversions`, `.stignore`, `.DS_Store`, `._`, `~`) and files outside a platform or core folder.

**Locking.** One sync or web action runs at a time. Anything else (a watch sync, a scheduled pull, a Resolve click) waits for the lock.

**Own-write suppression.** When romm_sync writes a file, it writes to a temp file (`~name.romm_sync`), sets the mtime to RomM's `updated_at`, and swaps it in atomically. It remembers that mtime, and a file event for that path is ignored while the mtime is unchanged. So a pull does not trigger a push of itself.

## 6. Needs You

The web UI lists everything romm_sync will not decide on its own.

| Item | Cause | Actions |
|---|---|---|
| **Conflict** | Changed locally and in RomM since the baseline | Keep local copy, Keep RomM copy |
| **Differs, no sync history** | Both sides exist, content differs, no baseline | Keep local copy, Keep RomM copy |
| **Held RomM download** (blocked) | A file only in RomM that cannot be placed | Add to Local |
| **Ambiguous match** | A local name matches several RomM games | None. Remove the duplicate in RomM |
| **Syncthing `.sync-conflict` copy** | Syncthing kept two versions | Keep current, Keep either |
| **Orphaned history** | History for ROMs no longer in RomM | None (informational) |
| **Unmatched game** | A local game with no RomM game of that file name (listed in the collapsed "Unmatched games" section above the log, not as a card) | None (informational). Check `platform_mapping.yaml` and the ROM's file name |
| **Flat + core duplicate** | Same file name flat and in a core folder | None (informational) |

After an upgrade from a version without history, many files can show as "Differs" once; there is no bulk action yet.

### Resolve (conflict and differs)

- **Keep local copy**: the RomM copy is saved to the backup folder (prefixed `romm_`), then the sync-folder file is uploaded to RomM.
- **Keep RomM copy**: the sync-folder file is saved to the backup folder, then replaced by RomM's copy. Not available in push-only.

Both write a `resolve_local` / `resolve_romm` event, which becomes the new baseline.

### Held RomM downloads: Add to Local

A RomM-only file is held when its destination is unavailable:

- `No <core> core folder in <platform>`: the file has an emulator, but no matching core folder exists.
- `<path> already exists and is a different save`: the destination is taken by a different file. (If the existing file is identical, nothing is held.)

The Add to Local choices:

| Choice | Destination |
|---|---|
| Content Dir Only | `<platform>/<file>`, flat |
| Content Dir + Core | `<platform>/<core>/<file>`, using the existing core folder or creating one named after the emulator id (for example `mgba`). Offered only for files with an emulator. |

A choice is disabled when its destination already exists. The file is downloaded and recorded as a `pull`.

### Syncthing conflict copies

Files named like `Game.sync-conflict-20250101-120000-ABCDEFG.srm` are listed when the original file still exists. Syncthing's conflict copies are never synced to RomM. The device name comes from the 7-character ID in the file name (shown as the raw ID if the name is unknown).

- **Keep current**: the conflict copy goes to the backup folder and is deleted. A bulk "keep current" action applies this to a list of copies. 
- **Keep the conflict copy**: the current file goes to the backup folder, then the conflict copy replaces it. Normal sync then compares it with RomM.

These actions do not write a history row; the backup file is the only record.

#### Disclaimer on Syncthing Integration
The syncthing integration is a convenience and I'm not really able to test this outside of my setup. Device ID resolution and relative-path resolution in theory should be reliable, but please make sure that the content in the WebUI makes sense to you before taking action. Again, I am not responsible for data loss and am deliberately leaving all decision up to the user here. For what it's worth, syncthing's 'keep' choices have been pretty reliable for me.

### Backups

Losing copies are written to `DATA_DIR/backups`, which is outside the sync folders so Syncthing does not spread them:

`{UTC YYYYMMDD-HHMMSS}_{rom_id}_{md5[:8]}_{file name}`

- Identical content already backed up for that ROM is not written twice.
- Syncthing conflict backups use `0` as the ROM id.
- Backups older than `LOG_DB_RETENTION_DAYS` (default 90) are removed at startup and every 24 hours, together with history pruning. "Prune backups" in the UI deletes all of them.
- Pulls do not make backups (see section 3).

## 7. Edge cases and limits

- **Changed on both sides while romm_sync was offline.** Handled the same as online: the next run compares both hashes with the baseline, finds both changed, and holds it. Nothing is lost, but nothing happens until you choose. The exception is push-only, where mtime decides.
- **Deleted files.** Deleting a local or RomM file is not synced. A deleted local file that still exists in RomM is pulled back (two-way) as a RomM-only file; a file deleted in RomM is uploaded again from the sync folder, and nothing is ever removed from either side.
- **RomM game deleted and re-added.** The new game has a new ROM id, so old history no longer links to it. The old rows appear as **orphaned history** and are never auto-removed, because pruning keeps each file's newest baseline. The new ROM starts with no baseline: identical files become a `match`, different ones are "Differs".
- **Two RomM rows with the same name.** The game is held as an ambiguous match (section 2) until the duplicate is removed from RomM.
- **Flat and core copy of the same name.** Only the newest scanned copy (by mtime) is synced to RomM. A callout lists flat-plus-core duplicates; moving one is manual. Copies under two different cores are also reduced to the newest, without a callout.
- **Negotiate failure.** Logged as a warning; the history DB decides. Downloads (`/content`) still need RomM 5.x.
- **RomM version.** RomM 5.x or newer is required. Support for older versions is not planned.
- **Syncthing device names.** With `SYNCTHING_URL` and `SYNCTHING_API_KEY` set, "last modified by" is found by asking Syncthing (read-only requests) for the file in every Syncthing folder that covers it, ignoring entries last modified by the server's own Syncthing device, taking the answer of the most specific (deepest) covering folder, and mapping its device ID to the device name. The path prefix Syncthing uses is found by probing the real file, so overlapping folders and container paths are handled. It shows `Syncthing` when Syncthing is unreachable at startup, no folder covers the file, or the only changer was the server itself. RomM-side changes show `RomM`, and resolved ones `web UI`.
- **Failed transfers.** Logged with `success = 0`, visible in the game's history, never a baseline; the game counts as failed for that run.
- **What push-only never does.** It never writes, creates, renames or deletes anything in a sync folder: no pulls, no downloads of RomM-only files, no Syncthing conflict clean-up, no Add to Local, no Keep RomM copy. It still uploads to RomM and writes to its own DB and backup folder.
- **No login.** The web UI has no authentication and can overwrite saves. Keep it on your LAN or behind a reverse proxy with auth.
- **States and negotiate.** RomM's negotiate API covers saves only; states rely on our own hashing and the history DB.
- **Saves uploaded without a slot.** romm_sync does not set a slot when it uploads, so RomM's negotiate only pairs saves that another client put in a slot.
- **Not covered yet:** moving a flat save into a core folder, pulled-state thumbnails, bulk resolve for "Differs", failed transfers in the summary card, running outside Docker without symlinked paths.

## 8. Why did my file ...?

| Symptom | Likely cause |
|---|---|
| Not pushed, game not listed anywhere | Platform folder not mapped to a RomM slug, or the file name does not equal the ROM's file name. See the log for "Could not match". |
| Not pushed, listed in Needs You as ambiguous | The name matches several RomM games. |
| Not pushed, shows "Conflict" | Changed locally and on RomM since the last sync. |
| Not pushed, shows "Differs, no sync history" | First time seen; the two copies already differ. |
| Not pushed, status looks fine | Local content changed but its mtime is not newer than RomM's `updated_at` (clock behind). Touch the file or use Keep local copy. |
| Pushed but RomM already had the same bytes | Local mtime was newer; harmless. |
| Overwritten by an older-looking local file | Push-only mode, or `FORCE_PUSH_SYNC` / `FULL_INITIAL_SYNC=force`. |
| Pulled and replaced my local file | Only RomM changed since the baseline; the local file matched the baseline, so nothing was lost. |
| RomM save never appears locally | No core folder for its emulator (held as "No <core> core folder"), the path is taken by a different file, a held ambiguous name, or push-only. |
| First sync after upgrade is slow | States are hashed by downloading them once (section 4). |
| A pulled state has no thumbnail | Screenshots are pushed with a state but never pulled. |
| Device shows `Syncthing` | Syncthing not configured or unreachable, or the server itself made the last change. |
| Game's history vanished after a RomM re-scan | RomM assigned a new ROM id; the old rows are under orphaned history. |
| Two copies of one save (flat and core) | Only the newest is synced; see the duplicate callout. |
