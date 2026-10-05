"""Ground-truth classification of coordinates against the GSHHG shapefiles.

The coastline archive is read straight from the zip through GDAL's ``/vsizip/``
filesystem, one layer at a time. Each layer is tested with an STRtree
``covers`` query (point ``covered_by`` polygon), and the fixed nesting parity
below is applied layer by layer.
"""

from pathlib import Path

import numpy as np
import pyogrio.raw
import shapely

# GSHHG nesting parity: L1 land, L2 lake (water), L3 island-in-lake (land),
# L4 pond-in-island-in-lake (water); h/L5 is the Antarctic ice sheet (land).
# Applied in this fixed order so later layers override earlier ones.
LAYERS = (
    ("f", 1, "land"),
    ("h", 5, "land"),
    ("f", 2, "water"),
    ("f", 3, "land"),
    ("f", 4, "water"),
)


def classify_points(archive: Path, points: list[tuple[float, float]]) -> list[str]:
    """Return ``"land"``/``"water"`` for each point, in the given order."""
    coordinates = np.array(points, dtype=np.float64).reshape(-1, 2)
    query_points = shapely.points(coordinates[:, 0], coordinates[:, 1])
    labels = np.full(len(points), "water", dtype=object)
    for resolution, level, label in LAYERS:
        member = f"GSHHS_shp/{resolution}/GSHHS_{resolution}_L{level}.shp"
        vsi_path = f"/vsizip/{archive.resolve().as_posix()}/{member}"
        _, _, wkb, _ = pyogrio.raw.read(
            vsi_path, columns=[], read_geometry=True
        )
        geometries = shapely.from_wkb(wkb)
        pairs = shapely.STRtree(geometries).query(
            query_points, predicate="covered_by"
        )
        labels[np.unique(pairs[0])] = label
        del wkb, geometries, pairs
    return labels.tolist()
