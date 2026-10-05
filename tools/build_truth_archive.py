"""Build the release ground-truth archive from the official GSHHG shapefile zip.

The subset keeps the exact member paths the classifier reads, so
``land_water.truth.classify_points`` works against it unchanged: for each layer
in ``truth.LAYERS`` it copies that layer's ``.shp``/``.shx``/``.dbf``/``.prj``
members byte-for-byte from the official archive, then adds the upstream
documentation/licence files and the GPLv3 text its LGPL "or later" option needs.

Inputs come from the root ``project.yaml``:

- ``paths.gshhg_source_archive`` -- the downloaded official ``gshhg-shp-2.3.7.zip``.
- ``paths.gshhg_archive``        -- the packaged subset to write.

Run with the project venv: ``.venv\\Scripts\\python.exe tools/build_truth_archive.py``.
"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import yaml

from land_water.truth import LAYERS

COMPONENTS = ("shp", "shx", "dbf", "prj")

# Upstream members kept verbatim in the subset, at the same paths.
UPSTREAM_MEMBERS = (
    "README.TXT",
    "SHAPEFILES.TXT",
    "GSHHS_shp/README.TXT",
    "LICENSE.TXT",
    "COPYING.LESSERv3",
)

NOTICE_MEMBER = "SUBSET_NOTICE.TXT"
NOTICE_TEXT = """\
This archive is a subset of the official GSHHG 2.3.7 shapefile distribution
(gshhg-shp-2.3.7.zip), repackaged for the "Land or Water?" benchmark. It holds
only the members this project's ground-truth classifier reads, copied
byte-for-byte from the official archive:

  GSHHS_shp/f/GSHHS_f_L1.*   GSHHS_shp/h/GSHHS_h_L5.*   GSHHS_shp/f/GSHHS_f_L2.*
  GSHHS_shp/f/GSHHS_f_L3.*   GSHHS_shp/f/GSHHS_f_L4.*   (.shp .shx .dbf .prj each)

together with the upstream README.TXT, SHAPEFILES.TXT, GSHHS_shp/README.TXT,
LICENSE.TXT and COPYING.LESSERv3, plus the GNU GPLv3 text (COPYINGv3) covering
LGPL's "version 3 or later" option. No member bytes, geometry or attributes were
modified; only this packaging note and COPYINGv3 are added.

GSHHG is (c) Paul Wessel and Walter H. F. Smith, distributed under the GNU
Lesser General Public License version 3 or later; see LICENSE.TXT,
COPYING.LESSERv3 and COPYINGv3. The complete official archive is at
https://github.com/GenericMappingTools/gshhg-gmt/releases/download/2.3.7/gshhg-shp-2.3.7.zip
"""

_CHUNK = 1 << 20


def member_names() -> list[str]:
    """Every upstream member the subset contains, at its original path."""
    names = list(UPSTREAM_MEMBERS)
    for resolution, level, _ in LAYERS:
        stem = f"GSHHS_shp/{resolution}/GSHHS_{resolution}_L{level}"
        names.extend(f"{stem}.{component}" for component in COMPONENTS)
    return names


def build(source_archive: Path, output_archive: Path, notices_dir: Path) -> None:
    """Write the subset to ``output_archive``, streaming members from ``source_archive``."""
    output_archive.parent.mkdir(parents=True, exist_ok=True)
    with (
        zipfile.ZipFile(source_archive) as source,
        zipfile.ZipFile(
            output_archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as destination,
    ):
        for name in member_names():
            with (
                source.open(name) as reader,
                destination.open(name, "w") as writer,
            ):
                shutil.copyfileobj(reader, writer, _CHUNK)
        with (
            (notices_dir / "COPYINGv3").open("rb") as reader,
            destination.open("COPYINGv3", "w") as writer,
        ):
            shutil.copyfileobj(reader, writer, _CHUNK)
        destination.writestr(NOTICE_MEMBER, NOTICE_TEXT)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    with (root / "project.yaml").open("r", encoding="utf-8") as handle:
        paths = yaml.safe_load(handle)["paths"]
    build(
        root / paths["gshhg_source_archive"],
        root / paths["gshhg_archive"],
        root / "licenses" / "gshhg",
    )
    print(f"wrote {paths['gshhg_archive']}")


if __name__ == "__main__":
    main()
