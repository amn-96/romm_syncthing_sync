"""Settling conflicts: resolve a conflicted `library` row, resolve a Syncthing conflict file, and the backups of the losing copies."""
import hashlib
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from loguru import logger

from .games_class import Game, SaveFile, SaveState
from .library_classes import LibraryScanner
from .two_way_sync import TwoWaySync, file_md5, iso, local_copy, romm_fields


class LayoutUnavailable(Exception):
    """The chosen place for a held RomM file does not exist as an option or is already taken."""


class ConflictResolution:
    def __init__(self, sync: TwoWaySync):
        self.sync = sync
        self.backup_dir = sync.cfg.DATA_DIR / "backups"

    def _backup(self, rom_id: int, file_name: str, content: bytes) -> Path:
        """The md5 prefix keeps same-second backups apart, and identical content already backed up is not written again."""
        content_md5 = hashlib.md5(content).hexdigest()[:8]
        existing = next(self.backup_dir.glob(f"*_{rom_id}_{content_md5}_{file_name}"), None) if self.backup_dir.exists() else None
        if existing:
            return existing
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = self.backup_dir / f"{datetime.now(timezone.utc):%Y%m%d-%H%M%S}_{rom_id}_{content_md5}_{file_name}"
        backup_path.write_bytes(content)
        return backup_path

    def backups(self) -> list[Path]:
        return sorted(self.backup_dir.iterdir()) if self.backup_dir.exists() else []

    def prune_backups(self, retention_days: int | None = None) -> int:
        """Without `retention_days` every backup goes. Age comes from the UTC timestamp at the start of each backup's name."""
        cutoff = f"{datetime.now(timezone.utc) - timedelta(days=retention_days):%Y%m%d-%H%M%S}" if retention_days is not None else "~"
        expired = [path for path in self.backups() if path.name[:15] < cutoff]
        for path in expired:
            path.unlink()
        if expired:
            logger.info(f"Pruned {len(expired)} backup(s)" + (f" older than {retention_days} days" if retention_days is not None else ""))
        return len(expired)

    def _local_file(self, row: dict) -> SaveFile | SaveState:
        path = Path(row["local_path"])
        return local_copy(row["kind"], path, row["platform_id"], row["emulator"] or None,
                                datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc))

    def resolve(self, row: dict, keep: str) -> None:
        """Settle a `library` row in conflict: "local" uploads the sync-folder copy to RomM, "romm" overwrites the sync-folder copy.
        The losing copy is saved in the backup folder first."""
        rom_id, kind, file_name, emulator = row["rom_id"], row["kind"], row["file_name"], row["emulator"]
        local = self._local_file(row)
        with self.sync.logging_failure(f"resolve_{keep}", rom_id=rom_id, kind=kind, file_name=file_name, emulator=emulator):
            if keep == "local":
                backup_path = self._backup(rom_id, f"romm_{file_name}", self.sync.user.download_content(f"{kind}s", row["romm_file_id"]))
                response = getattr(self.sync.user, f"{kind}s").update(obj=local, rom_id=rom_id, id=row["romm_file_id"])
                if "id" not in response:
                    raise RuntimeError(f"RomM rejected the upload: {response}")
                event = {"local_hash": file_md5(local.path), "local_mtime": local.modified_at.isoformat(), **romm_fields(response, file_md5(local.path))}
                row |= {"romm_hash": event["romm_hash"], "romm_updated_at": event["romm_updated_at"], "romm_size": local.path.stat().st_size}
            else:
                romm_updated_at = datetime.fromisoformat(row["romm_updated_at"])
                content = self.sync.user.download_content(f"{kind}s", row["romm_file_id"])
                backup_path = self._backup(rom_id, file_name, local.path.read_bytes())
                self.sync.overwrite(local.path, content, romm_updated_at)
                event = {"local_hash": hashlib.md5(content).hexdigest(), "local_mtime": romm_updated_at.isoformat(), "romm_id": row["romm_file_id"],
                         "romm_hash": row["romm_hash"], "romm_updated_at": row["romm_updated_at"]}
                row |= {"local_hash": event["local_hash"], "local_mtime": event["local_mtime"], "local_size": len(content)}
        self.sync.store.add_event(f"resolve_{keep}", rom_id=rom_id, kind=kind, file_name=file_name, emulator=emulator,
                             device="web UI", backup_path=str(backup_path), **event)
        self.sync.store.update_library_row(row | {"status": "synced"})
        logger.info(f"Resolved {file_name}: kept the {keep} copy (backup: {backup_path.name})")

    def add_to_local(self, row: dict, layout: str) -> None:
        """Write a held ("blocked") RomM file to the sync folder in `layout` ("content" or "core"), never over an existing path."""
        rom_id, kind, file_name, emulator = row["rom_id"], row["kind"], row["file_name"], row["emulator"]
        path, reason = self.sync.add_local_options(kind, row["local_platform"], file_name, emulator).get(layout, (None, "The RomM file has no emulator"))
        if reason:
            raise LayoutUnavailable(reason)
        game = Game(name=row["game_name"], path=path.parent, platform=row["local_platform"], romm_id=rom_id, romm_platform_id=row["platform_id"])
        getattr(game, f"set_romm_{kind}s")(getattr(self.sync.user, f"{kind}s").get(rom_id, row["platform_id"]))
        romm = next(f for f in getattr(game, f"romm_{kind}_files") if f.path.name == file_name and (f.emulator or "") == emulator)
        local = local_copy(kind, path, row["platform_id"], romm.emulator if layout == "core" else None, romm.modified_at)
        self.sync.pull(rom_id, kind, local, romm)
        stat = path.stat()
        self.sync.store.update_library_row(row | {"status": "synced", "note": None, "local_path": str(path), "local_hash": file_md5(path),
                                                  "local_mtime": iso(stat.st_mtime), "local_size": stat.st_size})
        logger.info(f"Added {file_name} to the sync folder as {path.relative_to(self.sync.cfg.sync_dir_for(kind))}")

    def resolve_syncthing_conflict(self, conflict_path: Path, original_path: Path, keep: str) -> None:
        """keep "original" or "conflict" copy; the other one moves to the backup folder. A state's screenshot copy follows the same choice."""
        if screenshot := LibraryScanner.screenshot_conflict(conflict_path, original_path):
            self.resolve_syncthing_conflict(screenshot, Path(f"{original_path}.png"), keep)
        if keep == "original":
            self._backup(0, conflict_path.name, conflict_path.read_bytes())
            conflict_path.unlink()
        else:
            if original_path.exists():  # only a screenshot can be missing: the local state may never have had one
                self._backup(0, original_path.name, original_path.read_bytes())
            os.replace(conflict_path, original_path)
