import os
from pathlib import Path
from typing import Optional, List, Dict
import requests
from requests.auth import HTTPBasicAuth
from loguru import logger


class RommSaves:
    """Handle save file operations for ROMM API (upload/download/manage saves)."""

    def __init__(self, romm_user: 'RommUser'):
        """Initialize with a RommUser instance for API access."""
        self.romm_user = romm_user

    def get(self,
            rom_id: int,
            platform_id: int) -> List[Dict]:
        """Generic wrapper for ROMM API GET requests.
        Args:
            creds: RommUser credentials object
            endpoint: API endpoint (e.g., "/api/states")
            params: Optional query parameters
        Returns:
            Parsed JSON response as a dictionary
        """
        url = f"{self.romm_user.url}/api/saves/"
        params = {
            "rom_id": rom_id,
            "platform_id": platform_id
        }
        response = requests.get(url, auth=HTTPBasicAuth(self.romm_user.user, self.romm_user.password), params=params)
        logger.debug(f"GET {url} - {response.status_code}")
        return response.json()

    def add(self,
            local_filepath: str | Path,
            rom_id: int):
        """Upload a new save file to ROMM.

        Args:
            local_filepath: LOCAL LIBRARY PATH to the save file
            rom_id: ROMM ROM ID
        """
        local_filepath = Path(local_filepath)

        if not local_filepath.exists():
            logger.error(f"Save file not found: {local_filepath}")
            return {"error": f"Save file not found: {local_filepath}"}

        with open(local_filepath, 'rb') as f:
            files = {'saveFile': (local_filepath.name, f)}
            params = {'rom_id': rom_id}

            url = f"{self.romm_user.url}/api/saves/"
            response = requests.post(
                url,
                auth=HTTPBasicAuth(self.romm_user.user, self.romm_user.password),
                params=params,
                files=files
            )
            logger.debug(f"POST {url} - {response.status_code}")
            # return response.json()

    def update(self,
               local_filepath: str | Path,
               rom_id: int,
               id: int) -> Dict:
        """Update an existing save file in ROMM.

        Args:
            local_filepath: LOCAL LIBRARY PATH to the save file
            rom_id: ROMM ROM ID
            id: ROMM Save ID (obtained from get() method)

        Returns:
            Parsed JSON response from ROMM API
        """
        local_filepath = Path(local_filepath)

        if not local_filepath.exists():
            logger.error(f"Save file not found: {local_filepath}")
            return {"error": f"Save file not found: {local_filepath}"}

        with open(local_filepath, 'rb') as f:
            files = {'saveFile': (local_filepath.name, f)}

            url = f"{self.romm_user.url}/api/saves/{id}"
            response = requests.put(
                url,
                auth=HTTPBasicAuth(self.romm_user.user, self.romm_user.password),
                files=files
            )
            logger.debug(f"PUT {url} - {response.status_code}")
            return response.json()


class RommStates:
    """Handle state file operations for ROMM API (upload/download/manage states)."""

    def __init__(self, romm_user: 'RommUser'):
        """Initialize with a RommUser instance for API access."""
        self.romm_user = romm_user

    def get(self,
            rom_id: int,
            platform_id: int) -> List[Dict]:
        """Generic wrapper for ROMM API GET requests.
        Args:
            creds: RommUser credentials object
            endpoint: API endpoint (e.g., "/api/states")
            params: Optional query parameters
        Returns:
            Parsed JSON response as a dictionary
        """
        url = f"{self.romm_user.url}/api/states/"
        params = {
            "rom_id": rom_id,
            "platform_id": platform_id
        }
        response = requests.get(url, auth=HTTPBasicAuth(self.romm_user.user, self.romm_user.password), params=params)
        logger.debug(f"GET {url} - {response.status_code}")

        return response.json()

    def add(self,
            local_filepath: str | Path,
            rom_id: int,
            emulator: str | None = None) -> Dict:
        """Upload a new state file to ROMM.

        Args:
            local_filepath: Path to the state file to upload
            rom_id: ROMM ROM ID

        Returns:
            Parsed JSON response from ROMM API
        """
        local_filepath = Path(local_filepath)

        if not local_filepath.exists():
            logger.error(f"state file not found: {local_filepath}")
            return {"error": f"state file not found: {local_filepath}"}

        with open(local_filepath, 'rb') as f:
            files = {'stateFile': (local_filepath.name, f)}
            params = {'rom_id': rom_id, 'emulator': emulator}

            url = f"{self.romm_user.url}/api/states/"
            response = requests.post(
                url,
                auth=HTTPBasicAuth(self.romm_user.user, self.romm_user.password),
                params=params,
                files=files
            )
            logger.debug(f"POST {url} - {response.status_code}")
            return response.json()

    def update(self,
               local_filepath: str | Path,
               rom_id: int,
               id: int,
               emulator: str | None = None) -> Dict:
        """Update an existing state file in ROMM.

        Args:
            local_filepath: Path to the state file to upload
            rom_id: ROMM ROM ID
            id: ROMM State ID (obtained from get() method)
            emulator: Optional emulator name

        Returns:
            Parsed JSON response from ROMM API
        """
        local_filepath = Path(local_filepath)

        if not local_filepath.exists():
            logger.error(f"state file not found: {local_filepath}")
            return {"error": f"state file not found: {local_filepath}"}

        with open(local_filepath, 'rb') as f:
            files = {'stateFile': (local_filepath.name, f)}

            url = f"{self.romm_user.url}/api/states/{id}"
            response = requests.put(
                url,
                auth=HTTPBasicAuth(self.romm_user.user, self.romm_user.password),
                files=files
            )
            logger.debug(f"PUT {url} - {response.status_code}")
            return response.json()


class RommUser:
    """API settings and HTTP Basic Authentication credentials for a ROMM user.

    The lazy-loaded saves and states properties are used as default factories for SaveState/SaveFile dataclass instantiation for nice Save and State file api request syntax.
    """
    def __init__(self,
                 romm_url: str = os.getenv("ROMM_URL", ""),
                 romm_username: str = os.getenv("ROMM_USERNAME", ""),
                 romm_password: str = os.getenv("ROMM_PASSWORD", ""),
                 romm_base_dir: Path = Path(os.getenv("ROMM_BASE_DIR", "")),
                 romm_container_name: str = os.getenv("ROMM_CONTAINER_NAME", "rommapp"),
                 api_limit: int = int(os.getenv("ROMM_API_LIMIT", "50"))):

        self.url = romm_url
        self.user = romm_username
        self.password = romm_password
        self.romm_base_dir = romm_base_dir
        self.romm_container_name = romm_container_name
        self.api_limit = api_limit
        self._saves = None
        self._states = None

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

    def _get(self,
             endpoint: str,
             params: Optional[Dict] = None) -> Dict:
        """Generic wrapper for ROMM API GET requests.
        Args:
            creds: RommUser credentials object
            endpoint: API endpoint (e.g., "/api/states")
            params: Optional query parameters
        Returns:
            Parsed JSON response as a dictionary
        """
        url = f"{self.url}{endpoint}"
        response = requests.get(url, auth=HTTPBasicAuth(self.user, self.password), params=params)
        logger.debug(f"GET {url} - {response.status_code}")
        return response.json()

    def _post(self,
              endpoint: str,
              params: Optional[Dict] = None,
              data: Optional[bytes] = None,
              headers: Optional[Dict] = None) -> Dict:
        """Generic wrapper for ROMM API POST requests.
        Args:
            endpoint: API endpoint (e.g., "/api/states")
            params: Optional query parameters
            data: Optional binary data (file content) for request body
            headers: Optional custom headers
        Returns:
            Parsed JSON response as a dictionary
        """
        url = f"{self.url}{endpoint}"
        response = requests.post(url,
                                 auth=HTTPBasicAuth(self.user, self.password),
                                 params=params,
                                 data=data,
                                 headers=headers)
        logger.debug(f"POST {url} - {response.status_code}")
        return response.json()

    def _put(self,
             endpoint: str,
             params: Optional[Dict] = None) -> Dict:
        """Generic wrapper for ROMM API PUT requests.
        Args:
            creds: RommUser credentials object
            endpoint: API endpoint (e.g., "/api/states")
            params: Optional query parameters
        Returns:
            Parsed JSON response as a dictionary
        """
        url = f"{self.url}{endpoint}"
        response = requests.put(url, auth=HTTPBasicAuth(self.user, self.password), params=params)
        logger.debug(f"PUT {url} - {response.status_code}")
        return response.json()

    def get_full_library(self) -> dict:
        """Gets the full list of ROM's in the ROMM.app database, with arg support for pagination.

        FUTURE OPTIMIZATION: Cache this result with LibraryCache class
        - Store library JSON with timestamp in cache file
        - Implement TTL (time-to-live) to avoid fetching on every sync
        - Only refresh when cache is stale (configurable, default 1 hour)
        - This method can be expensive with large libraries (many API calls)
        """

        all_items = []
        offset = 0  # start offset at 0.
        # ROMM API default return limit is 50, which is also the default here. This can be controlled with docker env variable
        limit = self.api_limit

        while True:
            params = {"limit": limit, "offset": offset}

            url = f"{self.url}/api/roms/"
            response = requests.get(
                url,
                auth=HTTPBasicAuth(self.user, self.password),
                params=params
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
            data = response.json()

            if not data.get("items"):
                logger.debug("No more items to fetch")
                break

            items_count = len(data["items"])
            all_items.extend(data["items"])
            logger.debug(f"Fetched {items_count} items (total so far: {len(all_items)})")

            if items_count < limit:
                logger.debug("Reached end of records")
                break

            offset += limit

        logger.debug(f"Total items retrieved: {len(all_items)}")
        logger.debug("Response received successfully")

        return {"items": all_items}
