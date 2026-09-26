from io import BytesIO

import numpy as np
from PIL import Image

from rainpulse_algo.multiband.product import geographic_composite_preview


def test_map_preview_preserves_grid_orientation_missing_and_cell_edges():
    values = np.array([[10, np.nan], [40, 60]], dtype=np.float32)
    metadata = {"width": 2, "height": 2, "west_m": 118, "south_m": 25,
                "spacing_m": 0.01, "row_order": "south_to_north",
                "crs": "EPSG:4326", "grid_id": "test"}
    geometry, encoded = geographic_composite_preview(values, metadata)
    assert np.allclose(geometry["bounds"], [118, 25, 118.02, 25.02])
    image = np.asarray(Image.open(BytesIO(encoded)))
    assert image[192, 192, 3] == 0
    assert image[64, 192, 3] == 255
    assert not np.array_equal(image[64, 64], image[192, 64])
    assert np.isnan(values[0, 1])
