import os
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict
import json
import requests
from requests.auth import HTTPBasicAuth
import pandas as pd
import logging

logger = logging.getLogger(__name__)


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
        logger.debug(f"GET /api/saves - Status Code: {response.status_code}")
        return response.json()

    def add(self,
            save_path: str | Path,
            rom_id: int):
        """Upload a new save file to ROMM.

        Args:
            save_path: Path to the save file to upload
            rom_id: ROMM ROM ID
        """
        save_path = Path(save_path)

        if not save_path.exists():
            logger.error(f"Save file not found: {save_path}")
            return {"error": f"Save file not found: {save_path}"}

        with open(save_path, 'rb') as f:
            files = {'saveFile': (save_path.name, f)}
            params = {'rom_id': rom_id}

            url = f"{self.romm_user.url}/api/saves/"
            response = requests.post(
                url,
                auth=HTTPBasicAuth(self.romm_user.user, self.romm_user.password),
                params=params,
                files=files
            )
            logger.debug(f"POST /api/saves/ - Status Code: {response.status_code}")
            # return response.json()

    def update(self,
                 save_path: str | Path,
                 rom_id: int) -> Dict:
        """Update an existing save file in ROMM.

        Args:
            save_path: Path to the save file to upload
            rom_id: ROMM ROM ID

        Returns:
            Parsed JSON response from ROMM API
        """
        save_path = Path(save_path)

        if not save_path.exists():
            logger.error(f"Save file not found: {save_path}")
            return {"error": f"Save file not found: {save_path}"}

        with open(save_path, 'rb') as f:
            files = {'saveFile': (save_path.name, f)}
            params = {'rom_id': rom_id}

            url = f"{self.romm_user.url}/api/saves/"
            response = requests.put(
                url,
                auth=HTTPBasicAuth(self.romm_user.user, self.romm_user.password),
                params=params,
                files=files
            )
            logger.debug(f"PUT /api/saves/ - Status Code: {response.status_code}")
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
        logger.debug(f"GET /api/states - Status Code: {response.status_code}")


        return response.json()

    def add(self,
                  state_path: str | Path,
                  rom_id: int,
                  emulator: Optional[str] = None) -> Dict:
        """Upload a new state file to ROMM.

        Args:
            state_path: Path to the state file to upload
            rom_id: ROMM ROM ID

        Returns:
            Parsed JSON response from ROMM API
        """
        state_path = Path(state_path)

        if not state_path.exists():
            logger.error(f"state file not found: {state_path}")
            return {"error": f"state file not found: {state_path}"}

        with open(state_path, 'rb') as f:
            files = {'stateFile': (state_path.name, f)}
            params = {'rom_id': rom_id, 'emulator': emulator}

            url = f"{self.romm_user.url}/api/states/"
            response = requests.post(
                url,
                auth=HTTPBasicAuth(self.romm_user.user, self.romm_user.password),
                params=params,
                files=files
            )
            logger.debug(f"POST /api/states/ - Status Code: {response.status_code}")
            return response.json()

    def update(self,
                 state_path: str | Path,
                 rom_id: int,
                 emulator: str) -> Dict:
        """Update an existing state file in ROMM.

        Args:
            state_path: Path to the state file to upload
            rom_id: ROMM ROM ID

        Returns:
            Parsed JSON response from ROMM API
        """
        state_path = Path(state_path)

        if not state_path.exists():
            logger.error(f"state file not found: {state_path}")
            return {"error": f"state file not found: {state_path}"}

        with open(state_path, 'rb') as f:
            files = {'stateFile': (state_path.name, f)}
            params = {'rom_id': rom_id, 'emulator': emulator}

            url = f"{self.romm_user.url}/api/states/"
            response = requests.put(
                url,
                auth=HTTPBasicAuth(self.romm_user.user, self.romm_user.password),
                params=params,
                files=files
            )
            logger.debug(f"PUT /api/states/ - Status Code: {response.status_code}")
            return response.json()

class RommUser:
    """API settings and HTTP Basic Authentication credentials for a ROMM user"""
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
        logger.debug(f"Status Code: {response.status_code}")
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
        logger.debug(f"Status Code: {response.status_code}")
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
        logger.debug(f"Status Code: {response.status_code}")
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

            data = self._get(endpoint="/api/roms/", params=params)
            logger.debug(f"Offset: {offset}, Limit: {limit}")

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
