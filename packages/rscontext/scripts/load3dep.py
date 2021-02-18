"""Testing script for downloading 3dep dems
    """

import os
import numpy as np
import py3dep
import rasterio
from rscommons import VectorBase, get_shp_or_gpkg


def download_topo_products(geom_boundary, resolution, out_folder, name=None):

    datasets = py3dep.get_map(["DEM", "Hillshade Gray", "Slope Degrees"], geom_boundary, resolution=resolution, geo_crs="epsg:4326")

    for dataset, d_name in zip([datasets.elevation, datasets.hillshade_gray, datasets.slope_degrees], ["dem", "hillshade_grey", "slope"]):

        out_file = os.path.join(out_folder, f"{d_name}_{name}.tif")

        # Not sure about when to flip and move rasters.
        out_data = np.flip(dataset.values, 0) if resolution == 7 else dataset.values

        with rasterio.open(out_file, "w",
                           driver="GTiff",
                           height=dataset.values.shape[0],
                           width=dataset.values.shape[1],
                           count=1,
                           dtype=dataset.dtype,
                           crs=dataset.crs,
                           transform=dataset.transform,
                           nodata=-3.40282306074e+38,
                           compress="LZW", predictor="3") as out_raster:

            out_raster.write(out_data, 1)

    return
