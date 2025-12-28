"""
Test script for various state file upload approaches to ROMM API.
Tests different field names and request formats to find the correct API usage.
"""

import requests
from requests.auth import HTTPBasicAuth
from pathlib import Path
import sys
sys.path.insert(0, str(Path.cwd().parent))

from src.romm_api_func import RommUser
from src.config import Config
from src.library_classes import RetroGameServer
from src.sync_operations import SaveBackup

# Setup
url = 'https://emu.local.amnserv.xyz'
me = RommUser(romm_url=url,
              romm_username="akshay",
              romm_password="inagalaxyfarfaraway")

cfg = Config(romm_credentials=me,
             sync_folder="/mnt/d/serverdat/EmuSync/SAVES",
             cache_file=Path.cwd() / "local_games.json",
             log_level="DEBUG")

srv = RetroGameServer.initialize_romm_map(cfg.ROMM_CREDENTIALS)
svs = SaveBackup(cfg.SYNC_FOLDER)
svs.scan_save_folder()
svs.match_to_romm(srv)

test_g = svs.get_matched_games()[7]
test_file = test_g.local_state_files[0]

print(f"Testing file upload: {test_file}")
print(f"File exists: {test_file.exists()}")
print(f"File size: {test_file.stat().st_size} bytes")
print(f"ROM ID: {test_g.romm_id}")
print()

# Test 1: Raw binary body with x-upload-filename header
print("=" * 60)
print("Test 1: Raw binary with x-upload-filename header")
print("=" * 60)
with open(test_file, 'rb') as f:
    file_data = f.read()

headers = {"x-upload-filename": test_file.name}
result = me.post("/api/states",
                 params={"rom_id": test_g.romm_id},
                 data=file_data,
                 headers=headers)
print(f"Response: {result}\n")

# Test 2: Multipart form-data with different field names
print("=" * 60)
print("Test 2: Multipart form-data - trying different field names")
print("=" * 60)
field_names = ['file', 'state_file', 'state', 'upload', 'content']

for field_name in field_names:
    print(f"\nTrying field name: '{field_name}'")
    with open(test_file, 'rb') as f:
        files = {field_name: (test_file.name, f, 'application/octet-stream')}
        result = requests.post(
            f"{me.url}/api/states",
            auth=HTTPBasicAuth(me.user, me.password),
            params={"rom_id": test_g.romm_id},
            files=files
        )

    print(f"Status: {result.status_code}")
    print(f"Response: {result.json()}")

    if result.status_code in [200, 201]:
        print("SUCCESS!")
        break

# Test 3: Multipart with just raw bytes (no filename tuple)
print("\n" + "=" * 60)
print("Test 3: Multipart form-data - raw bytes only")
print("=" * 60)
with open(test_file, 'rb') as f:
    files = {'file': f.read()}
    result = requests.post(
        f"{me.url}/api/states",
        auth=HTTPBasicAuth(me.user, me.password),
        params={"rom_id": test_g.romm_id},
        files=files
    )

print(f"Status: {result.status_code}")
print(f"Response: {result.json()}")

print("\n" + "=" * 60)
print("All tests complete")
print("=" * 60)
