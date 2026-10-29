"""Search Sentinel-2 L2A via Earth Search (AWS Open Data, anonymous)."""
import pystac_client

STAC_URL = "https://earth-search.aws.element84.com/v1"
AOI_BBOX = [39.747125, 47.121225, 39.772875, 47.138775]  # lon/lat
DATETIME = "2024-06-01/2025-09-30"
MAX_CLOUD = 10

catalog = pystac_client.Client.open(STAC_URL)
search = catalog.search(
    collections=["sentinel-2-l2a"],
    bbox=AOI_BBOX,
    datetime=DATETIME,
    query={"eo:cloud_cover": {"lt": MAX_CLOUD}},
    limit=50,
)
items = list(search.items())
print(f"Found {len(items)} items with cloud < {MAX_CLOUD}%")
for item in items[:10]:
    print(f"  {item.id}  |  {item.datetime}  |  cloud {item.properties.get('eo:cloud_cover')}%")
