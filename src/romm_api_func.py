import os
from pathlib import Path
import math
from typing import Callable, List, Dict, TYPE_CHECKING
import requests
from requests.auth import HTTPBasicAuth
from loguru import logger

if TYPE_CHECKING:
    from src.games_class import SaveFile, SaveState


class RommSaves:
    """Handle save file operations for ROMM API (upload/download/manage saves)."""

    def __init__(self, romm_user: 'RommUser'):
        """Initialize with a RommUser instance for API access."""
        self.romm_user = romm_user

    def get(self,
            rom_id: int | None = None,
            platform_id: int | None = None) -> List[Dict]:
        """GET the user's saves. Without arguments RomM returns all of them in one call."""
        url = f"{self.romm_user.url}/api/saves/"
        params = {key: value for key, value in {"rom_id": rom_id, "platform_id": platform_id}.items() if value is not None}
        response = requests.get(url, params=params, **self.romm_user._auth_kwargs())
        logger.debug(f"GET {url} - {response.status_code}")
        return response.json()

    def add(self,
            obj: 'SaveFile',
            rom_id: int,
            emulator: str | None = None):
        """Upload a new save file to ROMM. Pulls the relevant info from the SaveFile object."""
        local_filepath: Path = obj.path

        if not local_filepath.exists():
            logger.error(f"Save file not found: {local_filepath}")
            return {"error": f"Save file not found: {local_filepath}"}

        with open(local_filepath, 'rb') as f:
            files = {'saveFile': (local_filepath.name, f)}
            params = {"rom_id": rom_id, "emulator": emulator}

            url = f"{self.romm_user.url}/api/saves/"
            response = requests.post(
                url,
                params=params,
                files=files,
                **self.romm_user._auth_kwargs()
            )
            logger.debug(f"POST {url} - {response.status_code}")
            return response.json()

    def update(self,
               obj: 'SaveFile',
               rom_id: int,
               id: int) -> Dict:
        """Update an existing save file in ROMM.

        Args:
            local_filepath: LOCAL LIBRARY PATH to the save file
            id: ROMM Save ID (obtained from get() method). this is ".id" attr for the romm save's instance.

        Returns:
            Parsed JSON response from ROMM API
        """
        local_filepath = obj.path

        if not local_filepath.exists():
            logger.error(f"Save file not found: {local_filepath}")
            return {"error": f"Save file not found: {local_filepath}"}

        with open(local_filepath, 'rb') as f:
            files = {'saveFile': (local_filepath.name, f)}

            url = f"{self.romm_user.url}/api/saves/{id}"
            response = requests.put(
                url,
                files=files,
                **self.romm_user._auth_kwargs()
            )
            logger.debug(f"PUT {url} - {response.status_code}")
            return response.json()


class RommStates:
    """Handle state file operations for ROMM API (upload/download/manage states)."""

    def __init__(self, romm_user: 'RommUser'):
        """Initialize with a RommUser instance for API access."""
        self.romm_user = romm_user

    def get(self,
            rom_id: int | None = None,
            platform_id: int | None = None) -> List[Dict]:
        """GET the user's states. Without arguments RomM returns all of them in one call."""
        url = f"{self.romm_user.url}/api/states/"
        params = {key: value for key, value in {"rom_id": rom_id, "platform_id": platform_id}.items() if value is not None}
        response = requests.get(url, params=params, **self.romm_user._auth_kwargs())
        logger.debug(f"GET {url} - {response.status_code}")

        return response.json()

    def add(self,
            obj: 'SaveState',
            rom_id: int,
            emulator: str | None = None) -> Dict:
        """Upload a new state file to ROMM.

        Args:
            local_filepath: Path to the state file to upload
            rom_id: ROMM ROM ID

        Returns:
            Parsed JSON response from ROMM API
        """
        local_filepath = obj.path
        screenshot = obj.screenshot

        with open(local_filepath, 'rb') as state_f:
            files = {'stateFile': (local_filepath.name, state_f)}

            if screenshot:
                screenshot_f = open(screenshot, 'rb')
                files['screenshotFile'] = (screenshot.name, screenshot_f)
            else:
                screenshot_f = None

            try:
                url = f"{self.romm_user.url}/api/states/"
                response = requests.post(
                    url,
                    params={"rom_id": rom_id, 'emulator': emulator},
                    files=files,
                    **self.romm_user._auth_kwargs()
                )
                logger.debug(f"POST {url} - {response.status_code}")
                return response.json()
            finally:
                if screenshot_f:
                    screenshot_f.close()

    def update(self,
               obj: 'SaveState',
               rom_id: int,
               id: int,
               emulator: str | None = None) -> Dict:
        """Update an existing state file in ROMM.

        Args:
            obj: SaveState object containing path and screenshot
            rom_id: ROMM ROM ID
            id: ROMM State ID (obtained from get() method)
            emulator: Optional emulator name

        Returns:
            Parsed JSON response from ROMM API
        """
        local_filepath = obj.path
        screenshot = obj.screenshot

        with open(local_filepath, 'rb') as state_f:
            files = {'stateFile': (local_filepath.name, state_f)}

            if screenshot:
                screenshot_f = open(screenshot, 'rb')
                files['screenshotFile'] = (screenshot.name, screenshot_f)
            else:
                screenshot_f = None

            try:
                url = f"{self.romm_user.url}/api/states/{id}"
                response = requests.put(
                    url,
                    files=files,
                    **self.romm_user._auth_kwargs()
                )
                logger.debug(f"PUT {url} - {response.status_code}")
                return response.json()
            finally:
                if screenshot_f:
                    screenshot_f.close()


class RommUser:
    """API settings and authentication credentials for a ROMM user.

    The lazy-loaded saves and states properties are used as default factories for SaveState/SaveFile dataclass instantiation for nice Save and State file api request syntax.
    """
    def __init__(self,
                 romm_url: str = os.getenv("ROMM_URL", ""),
                 romm_username: str = os.getenv("ROMM_USERNAME", ""),
                 romm_password: str = os.getenv("ROMM_PASSWORD", ""),
                 romm_base_dir: Path = Path(os.getenv("ROMM_BASE_DIR", "")),
                 romm_container_name: str = os.getenv("ROMM_CONTAINER_NAME", "rommapp"),
                 api_limit: int = int(os.getenv("ROMM_API_LIMIT", "50")),
                 auth_type: str = os.getenv("ROMM_AUTH_TYPE", "http"),
                 romm_api_token: str = os.getenv("ROMM_API_TOKEN", "")):

        self.url = romm_url
        self.user = romm_username
        self.password = romm_password
        self.romm_base_dir = romm_base_dir
        self.romm_container_name = romm_container_name
        self.api_limit = api_limit
        self.auth_type = auth_type
        self.api_token = romm_api_token
        self._saves = None
        self._states = None

    def _auth_kwargs(self) -> dict:
        if self.auth_type == "api":
            return {"headers": {"Authorization": f"Bearer {self.api_token}"}}
        return {"auth": HTTPBasicAuth(self.user, self.password)}

    @property
    def saves(self) -> RommSaves:
        """Lazy-loaded RommSaves instance for handling save file operations."""
        if self._saves is None:
            self._saves = RommSaves(self)
        return self._saves

    @property
    def states(self) -> RommStates:
        """Lazy-loaded RommSaves instance for handling save file operations."""
        if self._states is None:
            self._states = RommStates(self)
        return self._states

    def get_me(self) -> dict:
        response = requests.get(f"{self.url}/api/users/me", timeout=10, **self._auth_kwargs())
        response.raise_for_status()
        return response.json()

    def download_content(self, kind: str, file_id: int) -> bytes:
        """File bytes of a RomM save or state. kind is "saves" or "states"."""
        response = requests.get(f"{self.url}/api/{kind}/{file_id}/content", **self._auth_kwargs())
        response.raise_for_status()
        return response.content

    def negotiate_saves(self, saves: list[dict]) -> dict:
        """Register this app as a RomM device (idempotent) and ask RomM how each client save compares to the server's."""
        device = requests.post(f"{self.url}/api/devices", json={"name": "romm_sync", "client": "romm_sync", "allow_existing": True},
                               **self._auth_kwargs())
        device.raise_for_status()
        response = requests.post(f"{self.url}/api/sync/negotiate", json={"device_id": device.json()["device_id"], "saves": saves},
                                 **self._auth_kwargs())
        response.raise_for_status()
        return response.json()

    def complete_sync_session(self, session_id: int) -> None:
        requests.post(f"{self.url}/api/sync/sessions/{session_id}/complete", json={}, **self._auth_kwargs()).raise_for_status()

    def _validate_library_response(self, response) -> dict:
        """Validate and parse ROMM API library response."""
        try:
            data = response.json()
        except ValueError as e:
            logger.critical(f"Failed to decode JSON response: {e}")
            raise RuntimeError(f"Invalid JSON from ROMM API: {e}")

        if "items" not in data:
            logger.critical(f"Malformed API response: missing 'items' key. Got keys: {list(data.keys())}")
            raise RuntimeError("ROMM API response missing 'items' key")

        if not isinstance(data["items"], list):
            logger.critical(f"Malformed API response: 'items' is not a list, got {type(data['items'])}")
            raise RuntimeError("ROMM API response 'items' is not a list")

        return data

    def get_full_library(self, on_page: Callable[[int], None] | None = None) -> dict:
        """Gets the full list of ROM's in the ROMM.app database, with arg support for pagination.
        `on_page` is called after each page with the total page count."""

        all_items = []
        offset = 0  # start offset at 0.
        # ROMM API default return limit is 50, which is also the default here. This can be controlled with docker env variable
        limit = self.api_limit

        while True:
            params = {"limit": limit, "offset": offset}

            url = f"{self.url}/api/roms/"
            response = requests.get(
                url,
                params=params,
                **self._auth_kwargs()
            )
            logger.debug(f"GET {url} - {response.status_code} (offset: {offset}, limit: {limit})")

            if response.status_code == 400:
                logger.critical(f"Bad request (400): {response.text}")
                raise RuntimeError("ROMM API returned 400 - Bad request")
            elif response.status_code == 404:
                logger.critical(f"Not found (404): {response.text}")
                raise RuntimeError("ROMM API returned 404 - Not found")
            elif response.status_code == 422:
                logger.critical(f"Unprocessable entity (422): {response.text}")
                raise RuntimeError("ROMM API returned 422 - Invalid data")

            response.raise_for_status()  # Raise for any other HTTP errors
            data = self._validate_library_response(response)

            if not data.get("items"):
                logger.debug("No more items to fetch")
                break

            items_count = len(data["items"])
            all_items.extend(data["items"])
            logger.debug(f"Fetched {items_count} items (total so far: {len(all_items)})")
            if on_page:
                on_page(math.ceil(data["total"] / limit))

            if items_count < limit:
                logger.debug("Reached end of records")
                break

            offset += limit

        logger.debug(f"Total items retrieved: {len(all_items)}")

        return {"items": all_items}
