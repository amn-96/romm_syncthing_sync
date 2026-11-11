"""Conflict detection and transfers between the sync folders and RomM, on top of the history in the Store.

The push decision itself stays in Game._romm_sync (mtime based). In push-only mode this only records and
reports: it never writes to a sync folder. Failed transfers are logged as events with success=0, which are never baselines."""
import hashlib
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import requests
from loguru import logger

from .config import Config
from .cores import normalize_core
from .games_class import Game, SaveFile, SaveState, paired_romm, state_slot
from .syncthing import SyncthingResolver

if TYPE_CHECKING:
    from .store import Store

_md5_cache: dict[tuple[Path, int, int], str] = {}


def file_md5(path: Path) -> str:
    """md5 is the algorithm RomM uses for a save's content_hash."""
    stat = path.stat()
    cache_key = (path, stat.st_mtime_ns, stat.st_size)
    if cache_key not in _md5_cache:
        with open(path, "rb") as f:
            _md5_cache[cache_key] = hashlib.file_digest(f, "md5").hexdigest()
    return _md5_cache[cache_key]


def iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def romm_stamp(updated_at: datetime) -> str:
    # upload responses carry microseconds but later GETs return whole seconds, so compare at second precision
    return updated_at.replace(microsecond=0).isoformat()


def romm_fields(response: dict, uploaded_hash: str) -> dict:
    """Event columns describing the RomM copy right after we uploaded `uploaded_hash`'s bytes (RomM doesn't hash states)."""
    return {"romm_id": response["id"], "romm_hash": uploaded_hash,
            "romm_updated_at": romm_stamp(datetime.fromisoformat(response["updated_at"]))}


def local_copy(kind: str, path: Path, platform_id: int, emulator: str | None, modified_at: datetime) -> SaveFile | SaveState:
    if kind == "save":
        return SaveFile(path=path, platform_id=platform_id, modified_at=modified_at, emulator=emulator)
    screenshot = Path(f"{path}.png")
    return SaveState(path=path, platform_id=platform_id, emulator=emulator, modified_at=modified_at,
                     slot=state_slot(path.name), screenshot=screenshot if screenshot.exists() else None)


class TwoWaySync:
    def __init__(self, store: "Store", cfg: Config, resolver: SyncthingResolver | None = None):
        self.store = store
        self.cfg = cfg
        self.user = cfg.ROMM_CREDENTIALS
        self.resolver = resolver
        self.writable = not cfg.PUSH_ONLY
        self.written: dict[Path, int] = {}  # path -> mtime_ns right after our own write
        self.negotiated: dict[tuple[int, str], dict] = {}  # (rom_id, file_name) -> RomM's operation for the file
        self.romm_hashes: dict[tuple[str, int, str], str] = {}  # (kind, romm file id, romm_stamp) -> md5, for this process
        self.touched_rom_ids: set[int] = set()
        self.session_ids: list[int] = []
        self.run_id: int | None = None

    # region runs
    def begin_run(self, trigger: str) -> None:
        self.run_id = self.store.start_run(trigger)
        self.negotiated.clear()
        self.touched_rom_ids.clear()

    def finish_run(self, checked: int, errors: int) -> None:
        self.store.finish_run(self.run_id, checked, errors)
        for session_id in self.session_ids:
            self.user.complete_sync_session(session_id)
        self.session_ids.clear()
    # endregion

    # region decisions
    def negotiate(self, games: list[Game]) -> None:
        """Ask RomM about every save it holds in a slot; only slotted saves can be paired. States aren't covered by RomM's negotiate."""
        client_saves = [{"rom_id": game.romm_id, "file_name": local.path.name, "slot": romm.slot, "emulator": romm.emulator,
                         "content_hash": file_md5(local.path), "updated_at": local.modified_at.isoformat(),
                         "file_size_bytes": local.path.stat().st_size}
                        for game in games for local in game.local_save_files
                        if (romm := paired_romm(local, game.romm_save_files)) and romm.slot]
        if not client_saves:
            return
        try:
            negotiation = self.user.negotiate_saves(client_saves)
        except requests.HTTPError as error:  # RomM older than 5.x has no device sync, so only the history DB decides conflicts
            logger.warning(f"RomM negotiate unavailable: {error}")
            return
        self.session_ids.append(negotiation["session_id"])
        self.negotiated = {(op["rom_id"], op["file_name"]): op for op in negotiation["operations"]}

    def romm_hash(self, rom_id: int, kind: str, romm: SaveFile | SaveState) -> str:
        """md5 of RomM's copy. RomM hashes saves but not states, so a state's hash comes from what we recorded for the same
        updated_at, and only otherwise from downloading it."""
        if romm.content_hash:
            return romm.content_hash
        stamp = romm_stamp(romm.modified_at)
        cache_key = (kind, romm.id, stamp)
        if cache_key not in self.romm_hashes:
            file_key = (rom_id, kind, romm.path.name, romm.emulator or "")
            recorded_hashes = (row["romm_hash"] for row in (self.store.baseline(*file_key), self.store.library_row(*file_key))
                               if row and row["romm_hash"] and romm_stamp(datetime.fromisoformat(row["romm_updated_at"])) == stamp)
            self.romm_hashes[cache_key] = next(recorded_hashes, None) or hashlib.md5(self.user.download_content(f"{kind}s", romm.id)).hexdigest()
        return self.romm_hashes[cache_key]

    def verdict(self, rom_id: int, kind: str, local: SaveFile | SaveState, romm: SaveFile | SaveState) -> str | None:
        """"conflict" (both sides changed since the last sync), "pull" (only RomM changed), "differs" (different content
        and no sync history to say which side is right) or None."""
        negotiated_action = self.negotiated.get((rom_id, local.path.name), {}).get("action") if kind == "save" else None
        if negotiated_action == "conflict":
            return "conflict"
        local_hash = file_md5(local.path)
        baseline = self.store.baseline(rom_id, kind, local.path.name, local.emulator or "")
        romm_hash = self.romm_hash(rom_id, kind, romm)
        if romm_hash == local_hash:  # same content on both sides: nothing to resolve, and a safe baseline
            if baseline is None or (baseline["local_hash"], baseline["romm_hash"]) != (local_hash, local_hash):
                self.store.add_event("match", rom_id=rom_id, kind=kind, file_name=local.path.name, emulator=local.emulator or "",
                                     local_hash=local_hash, local_mtime=local.modified_at.isoformat(),
                                     romm_id=romm.id, romm_hash=romm_hash, romm_updated_at=romm_stamp(romm.modified_at))
            return None
        if baseline is None:
            return "differs"
        local_changed = local_hash != baseline["local_hash"]
        if baseline["romm_hash"]:
            romm_changed = romm_hash != baseline["romm_hash"]
        else:  # baselines recorded before states were hashed
            romm_changed = romm_stamp(romm.modified_at) != romm_stamp(datetime.fromisoformat(baseline["romm_updated_at"]))
        if (local_changed and romm_changed) or (local_changed and negotiated_action == "download") \
                or (romm_changed and negotiated_action == "upload"):
            return "conflict"
        return "pull" if romm_changed else None
    # endregion

    # region file changes
    @contextmanager
    def logging_failure(self, action: str, **fields):
        try:
            yield
        except Exception as error:
            self.store.add_event(action, success=0, error=f"{type(error).__name__}: {error}", **fields)
            raise

    def push(self, rom_id: int, kind: str, local: SaveFile | SaveState, upload) -> None:
        """Run `upload()` (which returns RomM's JSON) and log the push, or the failure."""
        key = {"rom_id": rom_id, "kind": kind, "file_name": local.path.name, "emulator": local.emulator or ""}
        with self.logging_failure("push", **key):
            response = upload()
        if "id" not in response:
            logger.error(f"RomM rejected {local.path.name}: {response}")
            self.store.add_event("push", success=0, error=f"RomM rejected the upload: {response}", **key)
            return
        self.touched_rom_ids.add(rom_id)
        local_hash = file_md5(local.path)
        self.store.add_event("push", local_hash=local_hash, local_mtime=local.modified_at.isoformat(),
                             device=self.resolver.modified_by(local.path) if self.resolver else None, **key, **romm_fields(response, local_hash))

    def is_own_write(self, path: Path) -> bool:
        try:
            return self.written.get(path) == path.stat().st_mtime_ns
        except FileNotFoundError:
            return False

    def overwrite(self, path: Path, content: bytes, mtime: datetime) -> None:
        """Atomic replace; the mtime is RomM's updated_at so the next sync doesn't push the file back."""
        temp_path = path.with_name(f"~{path.name}.romm_sync")  # '~' is in METADATA_FILE_FILTERS, so neither we nor Syncthing's scans pick it up
        temp_path.write_bytes(content)
        os.utime(temp_path, (mtime.timestamp(), mtime.timestamp()))
        os.replace(temp_path, path)
        self.written[path] = path.stat().st_mtime_ns

    def pull(self, rom_id: int, kind: str, local: SaveFile | SaveState, romm: SaveFile | SaveState) -> None:
        """Write RomM's copy to local.path. No backup: this only runs when the local copy is unchanged since the last sync."""
        key = {"rom_id": rom_id, "kind": kind, "file_name": local.path.name, "emulator": local.emulator or ""}
        with self.logging_failure("pull", **key):
            content = self.user.download_content(f"{kind}s", romm.id)
            local.path.parent.mkdir(parents=True, exist_ok=True)
            self.overwrite(local.path, content, romm.modified_at)
        self.touched_rom_ids.add(rom_id)
        content_hash = hashlib.md5(content).hexdigest()
        self.store.add_event("pull", local_hash=content_hash, local_mtime=romm.modified_at.isoformat(), romm_id=romm.id,
                             romm_hash=content_hash, romm_updated_at=romm.modified_at.isoformat(), device="RomM", **key)
        logger.info(f"Pulled {local.path.name} from RomM")

    def core_folder(self, platform_dir: Path, emulator: str) -> Path | None:
        """The existing folder in `platform_dir` that holds `emulator`'s files."""
        return next((d for d in platform_dir.iterdir() if normalize_core(d.name) == emulator and d.is_dir()), None) if platform_dir.is_dir() else None

    def download_target(self, game: Game, kind: str, romm: SaveFile | SaveState) -> tuple[Path | None, str | None]:
        """Where a file that exists only in RomM goes, or None and the reason it can't be placed (no reason: already there). Never an existing path."""
        platform_dir = self.cfg.sync_dir_for(kind) / game.platform
        folder = platform_dir
        if romm.emulator:
            if not (folder := self.core_folder(platform_dir, romm.emulator)):
                return None, f"No {romm.emulator} core folder in {game.platform}"
        target = folder / romm.path.name
        if target.exists():
            if file_md5(target) == self.romm_hash(game.romm_id, kind, romm):
                return None, None  # the same file is already there, nothing to download
            return None, f"{target.relative_to(platform_dir)} already exists and is a different save"
        return target, None

    def add_local_options(self, kind: str, platform: str, file_name: str, emulator: str) -> dict[str, tuple[Path, str | None]]:
        """Where a held RomM file can be placed by hand: layout -> (path, reason it is unavailable). "content" is the platform
        folder itself; "core" is the emulator's core folder, created when missing, and only offered for a file with an emulator."""
        platform_dir = self.cfg.sync_dir_for(kind) / platform
        paths = {"content": platform_dir / file_name}
        if emulator:
            paths["core"] = (self.core_folder(platform_dir, emulator) or platform_dir / emulator) / file_name
        return {layout: (path, f"{path.relative_to(platform_dir)} already exists" if path.exists() else None) for layout, path in paths.items()}

    def pull_new(self, game: Game, kind: str, romm: SaveFile | SaveState) -> None:
        target, _ = self.download_target(game, kind, romm)
        if target is None:
            return
        local = local_copy(kind, target, game.romm_platform_id, romm.emulator, romm.modified_at)
        self.pull(game.romm_id, kind, local, romm)
        if kind == "save":
            game.local_save_files.append(local)
        else:
            game.local_state_files[local.path.name] = local
    # endregion
