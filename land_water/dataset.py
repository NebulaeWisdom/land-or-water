"""Point generation for the Land-or-Water benchmark.

Grid cell centres on the whole Earth, or a seeded subset of them. Every point is
a ``(longitude, latitude)`` pair in degrees. The returned order is a seeded
shuffle, so the same seed always yields the same execution order while the set
of grid cell centres is unchanged.
"""

import random


def generate_points(test: dict) -> list[tuple[float, float]]:
    """Return the point list for one test configuration.

    The full grid has ``int(180 / step)`` latitudes and ``int(360 / step)``
    longitudes; the point for cell ``(row, column)`` is its centre. Before
    returning, the points are shuffled with ``test["seed"]``, so the same seed
    reproduces the same execution order and the full set of grid cell centres
    is unchanged. When ``point_count`` is set, the seeded sample of the grid is
    taken first and then shuffled, so the selected set of cells is unchanged
    while its execution order is random.
    """
    step = test["step_degrees"]
    latitude_count = int(180 / step)
    longitude_count = int(360 / step)
    points = [
        (
            -180 + step / 2 + longitude_index * step,
            -90 + step / 2 + latitude_index * step,
        )
        for latitude_index in range(latitude_count)
        for longitude_index in range(longitude_count)
    ]
    point_count = test["point_count"]
    if point_count is not None:
        indices = random.Random(test["seed"]).sample(range(len(points)), point_count)
        points = [points[index] for index in indices]
    random.Random(test["seed"]).shuffle(points)
    return points
