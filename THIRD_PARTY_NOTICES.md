# Third-party notices

## GSHHG 2.3.7 — land/water ground-truth data

`Land or Water?` derives every ground-truth label from the GSHHG coastline
shapefiles (`GSHHS_shp/f/GSHHS_f_L1..L4`, `GSHHS_shp/h/GSHHS_h_L5`).

- Upstream: Global Self-consistent, Hierarchical, High-resolution Geography
  Database, version 2.3.7, released 2017-06-15.
- Authors / copyright holders: Paul Wessel (SOEST, University of Hawai'i) and
  Walter H. F. Smith (NOAA Geosciences Lab, National Ocean Service).
- Project page: <https://www.soest.hawaii.edu/pwessel/gshhg/>
- Distribution source: <https://github.com/GenericMappingTools/gshhg-gmt>
- Original distribution: `gshhg-shp-2.3.7.zip` (149 MB, not committed).
- Runtime data shipped here: `assets/gshhg-land-water-2.3.7.zip`, a trimmed
  archive containing the five complete shapefile layers used for ground truth
  (`GSHHS_f_L1..L4`, `GSHHS_h_L5`, each `.shp`/`.shx`/`.dbf`/`.prj`) plus the
  upstream `README.TXT`/`SHAPEFILES.TXT`/`GSHHS_shp/README.TXT`/`LICENSE.TXT`/
  `COPYING.LESSERv3`, the GPLv3 text (`COPYINGv3`) and a `SUBSET_NOTICE.TXT`.
  It is the upstream GSHHG 2.3.7 data unchanged (no simplification or raster
  approximation); it is regenerable from the original archive with
  `tools/build_truth_archive.py`.

### License

GSHHG 2.3.7 is distributed under the **GNU Lesser General Public License,
version 3 or later (LGPL-3.0-or-later)**.

- Bundled `LICENSE.TXT`: "As of GSHHG 2.2.2, GSHHG is distributed under the
  GNU Lesser General Public License (LGPL) version 3 or later."
- Upstream `README`: "The project is distributed under the GNU Lesser General
  Public License"; the 2.2.2 release note says "released under the lesser GNU
  License, v3 or any earlier version".
- Project page: "Starting with version 2.2.2, GSHHG has been released under the
  GNU Lesser General Public License."

LGPLv3 incorporates the terms of GPLv3, so both license texts are redistributed
here:

- `licenses/gshhg/LICENSE.TXT` — GSHHG copyright/permission notice (verbatim
  from the 2.3.7 archive).
- `licenses/gshhg/COPYING.LESSERv3` — LGPLv3 (verbatim from the 2.3.7 archive).
- `licenses/gshhg/COPYINGv3` — GPLv3 (<https://www.gnu.org/licenses/gpl-3.0.txt>).

### Additional permission for the data

The bundled `LICENSE.TXT` also grants permission to use, copy, modify and
distribute the data and its documentation for any purpose, provided that the
copyright notice and this permission notice appear in all copies and in
supporting documentation, and that the name GSHHG is not used in advertising or
publicity without specific prior written permission. GSHHG may be included in a
bundled distribution of software for which a fee may be charged (no more than a
reasonable redistribution fee). The source databases (WVS, WDBII, Atlas of the
Cryosphere) are public domain.

### Obligations when redistributing data

The committed `assets/gshhg-land-water-2.3.7.zip` already redistributes GSHHG
data, so the LGPL obligations below apply to every release that includes it. If
further GSHHG data is shipped — more extracted shapefile layers, converted
geometry (GeoJSON/Parquet/…), or a derived raster/mask — it is likewise a
redistribution or a modified version of GSHHG and must:

1. accompany the distribution with the GSHHG copyright/permission notice
   (`LICENSE.TXT`) and the LGPLv3 and GPLv3 texts, and give prominent notice
   that GSHHG is used and is covered by the LGPL;
2. convey the derived data under LGPL-3.0-or-later (the same terms as the
   original data);
3. not use the name GSHHG in advertising or publicity without prior written
   permission;
4. cite Wessel, P., and W. H. F. Smith (1996), *A global, self-consistent,
   hierarchical, high-resolution shoreline database*, J. Geophys. Res.,
   101(B4), 8741–8743.

## Project code

The original project source code is licensed under the MIT License; see
`LICENSE` (Copyright (c) 2026 NebulaeWisdom). The GSHHG data is separate and
stays under LGPL-3.0-or-later as described above; it is not covered by the
project's MIT license.

## Python dependencies

PyYAML, openai, numpy, pyogrio, shapely, nicegui and plotly are installed from
PyPI under their own licenses and are not redistributed by this repository.
