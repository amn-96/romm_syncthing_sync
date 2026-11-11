"""Name the Syncthing device that last modified a file (GET requests only).

Syncthing reports folder paths as its own container sees them, and folders overlap (a parent folder plus per-platform
folders), so the root of each of our sync dirs is probed against the real file, trying the most voted candidates first."""
from collections import Counter
from pathlib import Path, PurePosixPath

import requests
from loguru import logger


def vote_root(folder_paths: list[PurePosixPath], platform_names: set[str]) -> list[PurePosixPath]:
    """Every folder parent, ranked by how many folders it holds that are named like one of our platform dirs."""
    votes = Counter(path.parent for path in folder_paths if path.name in platform_names)
    return sorted(dict.fromkeys(path.parent for path in folder_paths), key=lambda parent: -votes[parent])


def covering_folders(folders: list[dict], file_path: PurePosixPath) -> list[dict]:
    return [folder for folder in folders if file_path.is_relative_to(folder["path"])]


class SyncthingResolver:
    def __init__(self, url: str, api_key: str, sync_dirs: list[Path]):
        self._url = url
        self._headers = {"X-API-Key": api_key}
        self.folders = [{**folder, "path": PurePosixPath(folder["path"])} for folder in self._get("/rest/config/folders")]
        self.device_names = {device["deviceID"][:7]: device["name"] for device in self._get("/rest/config/devices")}
        self.my_id = self._get("/rest/system/status")["myID"][:7]
        folder_paths = [folder["path"] for folder in self.folders]
        self._candidates = {sync_dir: vote_root(folder_paths, {d.name for d in sync_dir.iterdir() if d.is_dir()}) for sync_dir in sync_dirs}
        self.roots: dict[Path, PurePosixPath] = {}
        self._cache: dict[tuple[Path, int], str] = {}
        logger.info(f"Syncthing root candidates: {[(str(k), [str(c) for c in v[:3]]) for k, v in self._candidates.items()]}")

    def _get(self, endpoint: str, **params) -> dict | list:
        response = requests.get(f"{self._url}{endpoint}", headers=self._headers, params=params, timeout=10)
        response.raise_for_status()
        return response.json()

    def modified_by(self, file_path: Path) -> str:
        """Device name that last changed the file, or "Syncthing" when it can't be told."""
        cache_key = (file_path, file_path.stat().st_mtime_ns)
        if cache_key not in self._cache:
            self._cache[cache_key] = self._lookup(file_path)
        return self._cache[cache_key]

    def _lookup(self, file_path: Path) -> str:
        sync_dir = next(d for d in self._candidates if file_path.is_relative_to(d))
        relative = PurePosixPath(file_path.relative_to(sync_dir))
        for root in [self.roots[sync_dir]] if sync_dir in self.roots else self._candidates[sync_dir]:
            answers = self._db_answers(root / relative)
            if answers:
                self.roots[sync_dir] = root
                foreign = [(folder, modified_by) for folder, modified_by in answers if modified_by != self.my_id]
                # Parent folders can report relayed IDs that are no device, so only the most specific folder is trusted.
                deepest = max(foreign, key=lambda answer: len(answer[0]["path"].parts), default=None)
                return self.device_names.get(deepest[1], "Syncthing") if deepest else "Syncthing"
        return "Syncthing"

    def _db_answers(self, st_path: PurePosixPath) -> list[tuple[dict, str]]:
        """(folder, modifiedBy) for each covering folder whose database knows the file."""
        answers = []
        for folder in covering_folders(self.folders, st_path):
            response = requests.get(f"{self._url}/rest/db/file", headers=self._headers, timeout=10,
                                    params={"folder": folder["id"], "file": str(st_path.relative_to(folder["path"]))})
            if response.ok:
                answers.append((folder, response.json()["global"]["modifiedBy"]))
        return answers
