"""Request and response models: they validate input and are what /docs shows."""
from typing import Literal

from pydantic import BaseModel, Field

# most serious first: a game's status is the worst of its files'
FILE_STATUSES = ("conflict", "differs", "blocked", "synced")
FileStatus = Literal[FILE_STATUSES]
Kind = Literal["save", "state"]


# region requests
class FileKey(BaseModel):
    rom_id: int = Field(description="RomM ROM ID")
    kind: Kind
    file_name: str
    emulator: str = Field(description='RomM emulator of the file, "" for none')


class ResolveRequest(FileKey):
    keep: Literal["local", "romm"]


class AddLocalRequest(FileKey):
    layout: Literal["content", "core"] = Field(description="Platform folder, or the emulator's core folder")


class SyncthingResolveRequest(BaseModel):
    path: str = Field(description="Full path of the .sync-conflict copy")
    keep: Literal["original", "conflict"]


class SyncthingKeepCurrentRequest(BaseModel):
    paths: list[str] = Field(description="Full paths of the .sync-conflict copies to back up and remove")
# endregion


# region responses
class Copy(BaseModel):
    modified_at: str | None
    device: str = Field(description="Syncthing device that last changed the file, or RomM")
    size_bytes: int | None
    hash: str | None = Field(description="md5 of the file's bytes")


class RommCopy(Copy):
    id: int = Field(description="RomM save or state ID")


class TrackedFile(BaseModel):
    kind: Kind
    file_name: str
    emulator: str
    core: str | None
    status: FileStatus
    note: str | None = Field(description="Why a held file was not downloaded")
    last_synced: str | None
    screenshot: bool = Field(description="A state with a screenshot at /api/games/{rom_id}/screenshot")
    add_local: dict[str, str | None] | bool = Field(description="For a held file: layout -> reason it is unavailable (null when free)")
    local: Copy | None = Field(description="The sync-folder copy")
    romm: RommCopy | None = Field(description="RomM's copy")


class SyncthingOriginal(BaseModel):
    path: str
    modified_at: str
    size_bytes: int


class SyncthingConflict(BaseModel):
    path: str = Field(description="Full path of the .sync-conflict copy; send it to /api/syncthing-conflicts/resolve")
    file_name: str = Field(description="Name of the file it conflicts with")
    rom_id: int | None = Field(description="Game the original belongs to, null for a file the sync does not track")
    device: str | None = Field(description="Syncthing device that made the conflict copy")
    modified_at: str
    size_bytes: int
    original: SyncthingOriginal = Field(description="The current file at the original path")


class Game(BaseModel):
    rom_id: int
    name: str
    platform_slug: str
    platform_name: str
    fs_folder: str = Field(description="Platform folder in the sync folder")
    cover: str | None
    sha1: str | None = Field(description="SHA-1 of the ROM file in RomM")
    files: list[TrackedFile]
    syncthing_conflicts: list[SyncthingConflict] = Field(description="Syncthing conflict copies of this game's files, separate from file status")


class GameSummary(BaseModel):
    rom_id: int
    name: str
    platform: str = Field(description="RomM platform slug")
    status: FileStatus = Field(description="The most serious status among the game's files")
    files: dict[str, int] = Field(description="File count per status, statuses with no files left out")
    syncthing_conflicts: int = Field(description="Syncthing conflict copies of this game's files; `status` does not include them")


class LastSync(BaseModel):
    started_at: str
    finished_at: str | None
    trigger: str | None
    checked: int | None
    errors: int | None


class StatusSummary(BaseModel):
    sync_version: str
    sync_mode: str
    push_only: bool
    last_sync: LastSync | None
    games: int = Field(description="Tracked games")
    files: dict[str, int] = Field(description="File count per status")
    syncthing_conflicts: int
    unmatched_games: int = Field(description="Local games with no RomM match")
    ambiguous_games: int = Field(description="Local games matching more than one RomM ROM")
    links: dict[str, str] = Field(description="For each non-zero count above, the route that lists those items")


class Route(BaseModel):
    method: str
    path: str
    summary: str


class HistoryEvent(BaseModel):
    id: int
    ts: str
    rom_id: int
    kind: Kind
    file_name: str
    emulator: str
    action: str = Field(description="push, pull, match, conflict, resolve_local, resolve_romm, romm_echo, delete_romm, ...")
    local_hash: str | None
    local_mtime: str | None
    romm_id: int | None
    romm_hash: str | None
    romm_updated_at: str | None
    device: str | None
    backup_path: str | None
    success: int
    error: str | None


class RommStatus(BaseModel):
    version: str | None = None
    error: str | None = Field(None, description="Why RomM could not be reached")


class Progress(BaseModel):
    done: int
    total: int


class Ok(BaseModel):
    ok: bool


class Message(BaseModel):
    message: str


class ErrorDetail(BaseModel):
    detail: str
# endregion


ERROR_MEANINGS = {404: "No such file, game or conflict", 409: "Push-only mode, or the destination is taken",
                  500: "RomM or the disk failed; the detail says which"}


def errors(*codes: int) -> dict:
    """OpenAPI `responses` for a route's error codes; FastAPI documents 422 (invalid input) on its own."""
    return {code: {"model": ErrorDetail, "description": ERROR_MEANINGS[code]} for code in codes}
