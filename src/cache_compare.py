"""Compare cached SaveBackup against fresh local scan to identify changes requiring sync."""
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Tuple
import logging

from .library_classes import Game, SaveFile, SaveState

logger = logging.getLogger(__name__)


class SyncChange:
    """Represents a file change that requires syncing."""

    def __init__(self, game_name: str, change_type: str, file_type: str, slot: int = None):
        """
        Args:
            game_name: Name of the game with changes
            change_type: 'new', 'updated', or 'deleted'
            file_type: 'save' or 'state'
            slot: For state files, the slot number (-1 for autosave, 0+ for numbered slots)
        """
        self.game_name = game_name
        self.change_type = change_type  # 'new', 'updated', 'deleted'
        self.file_type = file_type  # 'save', 'state'
        self.slot = slot

    def __repr__(self):
        return f"SyncChange({self.game_name}, {self.change_type} {self.file_type} slot={self.slot})"


class CacheCompare:
    """Compare cached SaveBackup snapshot against fresh local scan."""

    def __init__(self, cached_backup: 'SaveBackup', fresh_backup: 'SaveBackup'):
        """
        Args:
            cached_backup: Previously cached SaveBackup from last sync
            fresh_backup: Freshly scanned SaveBackup from local folder
        """
        self.cached_backup = cached_backup
        self.fresh_backup = fresh_backup

    def _game_by_name(self, backup: 'SaveBackup', game_name: str) -> Game | None:
        """Find a game in backup by exact name match."""
        for game in backup.games:
            if game.name == game_name:
                return game
        return None

    def _compare_files(self,
                       cached_files: List[SaveFile],
                       fresh_files: List[SaveFile]) -> Tuple[List[SaveFile], List[SaveFile]]:
        """Compare two lists of SaveFile objects.

        Returns:
            (new_or_updated_files, deleted_files)
        """
        new_or_updated = []
        deleted = []

        # Build path -> modified_at map for cached files
        cached_map = {str(sf.path): sf.modified_at for sf in cached_files}
        fresh_map = {str(sf.path): sf.modified_at for sf in fresh_files}

        # Find new or updated files
        for fresh_file in fresh_files:
            fresh_path_str = str(fresh_file.path)
            if fresh_path_str not in cached_map:
                # New file
                new_or_updated.append(fresh_file)
            elif fresh_file.modified_at > cached_map[fresh_path_str]:
                # Updated file
                new_or_updated.append(fresh_file)

        # Find deleted files
        for cached_file in cached_files:
            cached_path_str = str(cached_file.path)
            if cached_path_str not in fresh_map:
                deleted.append(cached_file)

        return new_or_updated, deleted

    def _compare_state_files(self,
                             cached_states: List[SaveState],
                             fresh_states: List[SaveState]) -> Tuple[Dict[int, SaveState], List[int]]:
        """Compare state files, organized by slot number.

        Returns:
            (new_or_updated_by_slot, deleted_slots)
            where new_or_updated_by_slot is {slot: SaveState}
        """
        new_or_updated = {}
        deleted_slots = []

        # Build slot -> SaveState map for cached states
        cached_map = {ss.slot: ss for ss in cached_states}
        fresh_map = {ss.slot: ss for ss in fresh_states}

        # Find new or updated state slots
        for fresh_state in fresh_states:
            slot = fresh_state.slot
            if slot not in cached_map:
                # New state slot
                new_or_updated[slot] = fresh_state
            elif fresh_state.modified_at > cached_map[slot].modified_at:
                # Updated state slot
                new_or_updated[slot] = fresh_state

        # Find deleted state slots
        for cached_state in cached_states:
            slot = cached_state.slot
            if slot not in fresh_map:
                deleted_slots.append(slot)

        return new_or_updated, deleted_slots

    def get_games_to_sync(self) -> Dict[str, Dict]:
        """Compare caches and return games with changes requiring ROMM sync.

        Returns:
            Dictionary mapping game name to sync details:
            {
                "game_name": {
                    "game": Game,  # The fresh Game object
                    "save_changes": {
                        "new_or_updated": [SaveFile],
                        "deleted": [SaveFile]
                    },
                    "state_changes": {
                        "new_or_updated": {slot: SaveState},
                        "deleted_slots": [int]
                    }
                }
            }
        """
        games_to_sync = {}

        # Iterate through fresh games
        for fresh_game in self.fresh_backup.games:
            if not fresh_game.is_matched:
                # Skip unmatched games
                continue

            cached_game = self._game_by_name(self.cached_backup, fresh_game.name)

            if cached_game is None:
                # New game entirely
                logger.debug(f"New game found: {fresh_game.name}")
                games_to_sync[fresh_game.name] = {
                    "game": fresh_game,
                    "save_changes": {
                        "new_or_updated": fresh_game.local_save_files,
                        "deleted": []
                    },
                    "state_changes": {
                        "new_or_updated": {ss.slot: ss for ss in fresh_game.local_state_files},
                        "deleted_slots": []
                    }
                }
                continue

            # Compare save files
            save_new_or_updated, save_deleted = self._compare_files(
                cached_game.local_save_files,
                fresh_game.local_save_files
            )

            # Compare state files by slot
            state_new_or_updated, state_deleted_slots = self._compare_state_files(
                cached_game.local_state_files,
                fresh_game.local_state_files
            )

            # Only add to sync list if there are actual changes
            if save_new_or_updated or save_deleted or state_new_or_updated or state_deleted_slots:
                games_to_sync[fresh_game.name] = {
                    "game": fresh_game,
                    "save_changes": {
                        "new_or_updated": save_new_or_updated,
                        "deleted": save_deleted
                    },
                    "state_changes": {
                        "new_or_updated": state_new_or_updated,
                        "deleted_slots": state_deleted_slots
                    }
                }
                logger.debug(f"Changes detected for game: {fresh_game.name}")

        return games_to_sync

    def summary(self) -> str:
        """Return human-readable summary of changes."""
        games_to_sync = self.get_games_to_sync()

        if not games_to_sync:
            return "No changes detected. Local and cache are in sync."

        summary_lines = [f"Changes detected in {len(games_to_sync)} game(s):"]

        for game_name, changes in games_to_sync.items():
            save_updates = len(changes["save_changes"]["new_or_updated"])
            save_deletes = len(changes["save_changes"]["deleted"])
            state_updates = len(changes["state_changes"]["new_or_updated"])
            state_deletes = len(changes["state_changes"]["deleted_slots"])

            summary_lines.append(
                f"  {game_name}: "
                f"saves({'+' if save_updates > 0 else ''}{save_updates}/{'-' if save_deletes > 0 else ''}{save_deletes}) "
                f"states({'+' if state_updates > 0 else ''}{state_updates}/{'-' if state_deletes > 0 else ''}{state_deletes})"
            )

        return "\n".join(summary_lines)
