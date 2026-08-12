from __future__ import annotations
from pathlib import Path
from urllib.request import urlretrieve

IFRA_51_OVERVIEW_URL = 'https://d3t14p1xronwr0.cloudfront.net/docs/Standards-Documentation/ifra-51st-amendment-ifra-standards-overview.xlsx'
IFRA_51_ANNEX_URL = 'https://d3t14p1xronwr0.cloudfront.net/docs/Standards-Documentation/ifra-51st-amendment-annex-on-contributions-from-other-sources.xlsx'

def download_ifra_51(folder: str | Path):
    folder=Path(folder); folder.mkdir(parents=True,exist_ok=True)
    overview=folder/'ifra-51st-amendment-ifra-standards-overview.xlsx'
    annex=folder/'ifra-51st-amendment-annex-on-contributions-from-other-sources.xlsx'
    urlretrieve(IFRA_51_OVERVIEW_URL, overview)
    urlretrieve(IFRA_51_ANNEX_URL, annex)
    return overview, annex
