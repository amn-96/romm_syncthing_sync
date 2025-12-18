# ROMM Sync TODO

## 1. API vs Direct File Transfer Strategy
- **Research**: Determine how ROMM's API endpoints for updating saves works and whether it's a better option than a direct file transfer.
- **Test**: Compare performance and reliability of both approaches
  - API route: HTTP multipart upload to ROMM endpoints
  - Direct route: Mount ROMM's save directory and write files directly (rsync-like approach)
- **Decision**: Choose based on reliability and transparency. I want to know what files are being changed.
- **Implementation**: Add abstraction layer in sync_operations.py to support both methods

## 2. File Modified and Matching Operations
- **File change detection**: Use pathlib.Path.stat().st_mtime to track file modifications
  - Compare local_last_modified against last sync timestamp (already in Game.needs_local_sync())
  - Consider using file hashing (MD5/SHA1) to detect actual content changes vs timestamp-only changes
  - Suggestion: Add optional hash field to Game class for content verification

- **Matching operations**: Implement actual file transfer logic
  - Create sync plan: iterate through matched games with needs_local_sync()=True
  - Build transfer list with source (local_save_files) and destination (ROMM rom_id path)
  - Possible solutions:
    - Use pathlib.Path.write_bytes() / read_bytes() for direct file copying
    - Use shutil.copy2() for preserving timestamps
    - Use rsync subprocess for batch operations on large save sets
  - Handle conflicts: newer local vs newer remote (favor local per current design)

## 3. Consolidate SaveBackup and Game Classes
- **Observation**: SaveBackup is a container that mainly iterates and filters Game objects
- **Consider merging**: Game could handle its own matching/filtering logic more directly
- **Possible approach**:
  - Move SaveBackup methods (needs_sync, get_matched_games, etc.) to Game as instance methods
  - Keep SaveBackup as a lightweight list manager (catalog initialization, JSON serialization)
  - Or: Replace SaveBackup entirely with a simple GameCatalog class that holds List[Game] + helper methods
  - Alternative: Make Game.scan() a classmethod that returns List[Game] - remove SaveBackup completely
- **Decision point**: Depends on whether we want per-game filtering or batch operations

## Notes
- All files in sync folder are treated as save files (no extension filtering)
- Game names derived from filename stems (no directory nesting)
- Platform names from parent directory (matches ROMM platform_display_name)
- Scale: ~100-200 local games, realistically 10-50 needing sync at a time
