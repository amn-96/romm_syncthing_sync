import requests
from requests.auth import HTTPBasicAuth
import pandas as pd
import logging

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def get_romm_list(romm_url: str, username: str, passwd: str) -> dict:
    """Gets the full list of ROM's in the ROMM.app database"""
    
    url = f"{romm_url}/api/roms/"
    response = requests.get(url, auth=HTTPBasicAuth(username, passwd))

    logger.debug(f"Status Code: {response.status_code}")
    data = response.json()
    logger.debug(f"Type of response: {type(data)}")
    logger.debug("Response received successfully")

    return data


class RetroGameServer:
    def __init__(self, raw_database, library, by_id, by_platform, games):
        self.raw_database: dict = raw_database
        self.library: pd.DataFrame = library
        self.by_id: dict = by_id
        self.by_platform: dict = by_platform
        self.games: list = games
    
    @classmethod
    def initialize_romm_map(cls,
                            romm_url: str = "https://emu.amnserv.xyz", 
                            username: str = "akshay", 
                            passwd: str = "inagalaxyfarfaraway"):
        """Use this factory method to initialize the sync with a snapshot of the ROMM database"""
        data = get_romm_list(romm_url, username, passwd)

        # represent the library as a dataframe for ease of use
        library = pd.json_normalize(data['items'])

        # build a map of ROM ID : ROM NAME
        id_map = dict(zip(library['id'], library['name']))
        platform_map = library.groupby('platform_display_name')['name'].apply(list).to_dict()
        games_list = library['name'].tolist()

        return cls(raw_database=data, library=library, by_id=id_map, by_platform=platform_map, games=games_list)

    def count(self):
        return len(self.games)
    
    def size_gb(self):
        return round(self.library['fs_size_bytes'].sum() / 1e9, 2)

    def __repr__(self):
        return (f"RetroGameServer(games={self.count()}, "
                f"size_gb={self.size_gb()}, "
                f"platforms={len(self.by_platform)})")

    def summary(self, verbose=True):
        print(f"Games: {self.count()}, Library Size: {self.size_gb()} GB")
        print()
        platforms = list(self.by_platform.items())
        for idx, (platform, games) in enumerate(platforms):
            print(f"┌─ {platform}: {len(games)} game(s)")
            if verbose:
                for game in games:
                    print(f"│  - {game}")
                if idx < len(platforms) - 1:
                    print("│")
                    print("├" + "─" * 40)
                    print("│")


if __name__ == "__main__":
    roms = RetroGameServer.initialize_romm_map()

    logger.info(f"Number of ROM's: {roms.count()}")
    logger.info(f"Library Size: {roms.size_gb()}")
    logger.debug(f"{roms.by_id[34]}")
    logger.debug(f"{roms.by_platform["Game Boy Advance"]}")
