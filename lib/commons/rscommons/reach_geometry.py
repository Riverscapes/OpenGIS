""" Calculates several properties of each network polyline:
    Slope, length, min and max elevation.

    Philip Bailey
    23 May 2019
"""
import os
from osgeo import gdal
import rasterio
from shapely.geometry import Point, box
from rscommons import Logger, VectorBase
from rscommons.raster_buffer_stats import raster_buffer_stats2
from rscommons.classes.vector_classes import get_shp_or_gpkg
from rscommons.database import write_db_attributes
from rscommons.classes.vector_base import get_utm_zone_epsg

Path = str

default_field_names = {'Length': 'iGeo_Len', 'Gradient': 'iGeo_Slope', 'MinElevation': 'iGeo_ElMin', 'MaxElevation':'IGeo_ElMax'}

def reach_geometry(flow_lines: Path, dem_path: Path, buffer_distance: float, field_names=default_field_names):
    """ Calculate reach geometry BRAT attributes

    Args:
        flow_lines (Path): [description]
        dem_path (Path): [description]
        buffer_distance (float): [description]
    """

    log = Logger('Reach Geometry')

    # Determine the best projected coordinate system based on the raster
    dataset = gdal.Open(dem_path)
    geo_transform = dataset.GetGeoTransform()
    xcentre = geo_transform[0] + (dataset.RasterXSize * geo_transform[1]) / 2.0
    epsg = get_utm_zone_epsg(xcentre)

    with rasterio.open(dem_path) as raster:
        bounds = raster.bounds
        extent = box(*bounds)

    # Buffer the start and end point of each reach
    line_start_polygons = {}
    line_end_polygons = {}
    reaches = {}
    with get_shp_or_gpkg(flow_lines) as lyr:

        # Transformations from original flow line features to metric EPSG, and to raster spatial reference
        _srs, transform_to_metres = VectorBase.get_transform_from_epsg(lyr.spatial_ref, epsg)
        _srs, transform_to_raster = VectorBase.get_transform_from_raster(lyr.spatial_ref, dem_path)

        # Buffer distance converted to the units of the raster spatial reference
        vector_buffer = VectorBase.rough_convert_metres_to_raster_units(dem_path, buffer_distance)

        for feature, _counter, _progbar in lyr.iterate_features("Processing reaches"):
            reach_id = feature.GetFID()
            geom = feature.GetGeometryRef()
            geom_clone = geom.Clone()

            # Calculate the reach length in the output spatial reference
            if transform_to_metres is not None:
                geom.Transform(transform_to_metres)

            reaches[reach_id] = {field_names['Length']: geom.Length(), field_names['Gradient']: 0.0, field_names['MinElevation']: None, field_names['MaxElevation']: None}

            if transform_to_raster is not None:
                geom_clone.Transform(transform_to_raster)

            # Buffer the ends of the reach polyline in the raster spatial reference
            pt_start = Point(VectorBase.ogr2shapely(geom_clone, transform_to_raster).coords[0])
            pt_end = Point(VectorBase.ogr2shapely(geom_clone, transform_to_raster).coords[-1])
            if extent.contains(pt_start) and extent.contains(pt_end):
                line_start_polygons[reach_id] = pt_start.buffer(vector_buffer)
                line_end_polygons[reach_id] = pt_end.buffer(vector_buffer)

    # Retrieve the mean elevation of start and end of point
    line_start_elevations = raster_buffer_stats2(line_start_polygons, dem_path)
    line_end_elevations = raster_buffer_stats2(line_end_polygons, dem_path)

    for reach_id, data in reaches.items():
        if reach_id in line_start_elevations and reach_id in line_end_elevations:
            sta_data = line_start_elevations[reach_id]
            end_data = line_end_elevations[reach_id]

            data[field_names['MaxElevation']] = _max_ignore_none(sta_data['Maximum'], end_data['Maximum'])
            data[field_names['MinElevation']] = _min_ignore_none(sta_data['Minimum'], end_data['Minimum'])

            if sta_data['Mean'] is not None and end_data['Mean'] is not None and sta_data['Mean'] != end_data['Mean']:
                data[field_names['Gradient']] = abs(sta_data['Mean'] - end_data['Mean']) / data[field_names['Length']]
        else:
            log.warning('{:,} features skipped because one or both ends of polyline not on DEM raster'.format(reach_id))

    write_db_attributes(os.path.dirname(flow_lines), reaches, [field_names['Length'], field_names['MaxElevation'], field_names['MinElevation'], field_names['Gradient']])


def _max_ignore_none(val1: float, val2: float) -> float:

    if val1 is not None:
        if val2 is not None:
            return max(val1, val2)
        else:
            return val1
    else:
        if val2 is not None:
            return val2
        else:
            return None


def _min_ignore_none(val1: float, val2: float) -> float:

    if val1 is not None:
        if val2 is not None:
            return min(val1, val2)
        else:
            return val1
    else:
        if val2 is not None:
            return val2
        else:
            return None
