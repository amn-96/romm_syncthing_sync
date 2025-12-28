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

class RommUser:
    """API settings and HTTP Basic Authentication credentials for a ROMM user"""
    def __init__(self,
                 romm_url: str = os.getenv("ROMM_URL", ""),
                 romm_username: str = os.getenv("ROMM_USERNAME", ""),
                 romm_password: str = os.getenv("ROMM_PASSWORD", ""),
                 romm_base_dir: str | Path = os.getenv("ROMM_BASE_DIR", ""),
                 romm_container_name: str = os.getenv("ROMM_CONTAINER_NAME", "rommapp"),
                 api_limit: int = int(os.getenv("ROMM_API_LIMIT", "50"))):

        self.url = romm_url
        self.user = romm_username
        self.password = romm_password
        self.romm_base_dir = romm_base_dir if romm_base_dir else None
        self.romm_container_name = romm_container_name
        self.api_limit = api_limit

    def get(self,
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

    def post(self,
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
    
    def put(self,
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
        """Gets the full list of ROM's in the ROMM.app database, with arg support for pagination."""

        all_items = []
        offset = 0  # start offset at 0. 
        # ROMM API default return limit is 50, which is also the default here. This can be controlled with docker env variable
        limit = self.api_limit

        while True:
            params = {"limit": limit, "offset": offset}

            data = self.get(endpoint="/api/roms/", params=params)
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
