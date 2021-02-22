#!/usr/bin/env python3
# Name:     GNAT - Sinuosity
#
# Purpose:  Calculate Sinuosity on a segmented network
#
# Author:   Kelly Whitehead
#
# Date:     18 Feb 2021
# -------------------------------------------------------------------------------


import sys
import os
import glob
import traceback
import datetime
import json
from math import radians, cos, sin, asin, sqrt

from osgeo import ogr
from osgeo import gdal

from rscommons import Logger, ProgressBar
from rscommons import GeopackageLayer

Path = str


def planform_sinuosity(line_network: Path, out_field_name: str = 'PlanformSinuosity'):
    """Calculate planform sinuosity (segment length / segment endpoint distance) on non projected network

    Args:
        line_network (Path): Path to line network geopackage layer
        out_field_name (str, optional): new or overwritten output field to store sinuosity values. Defaults to 'PlanformSinuosity'.
    """

    log = Logger("GNAT Planform Sinuosity")
    log.info(f'Starting sinuosity')

    with GeopackageLayer(line_network, write=True) as flowlines_lyr:

        # Field management
        for field in [out_field_name, 'GNAT_LengthKM', 'GNAT_SegDistKM']:
            ix_field = flowlines_lyr.ogr_layer.GetLayerDefn().GetFieldIndex(field)
            if ix_field != 0:
                flowlines_lyr.ogr_layer.DeleteField(ix_field)
            flowlines_lyr.ogr_layer.CreateField(ogr.FieldDefn(field, ogr.OFTReal))

        # Calculate Planform Sinuosity for each feature
        for feat, _counter, _progbar in flowlines_lyr.iterate_features("Calculating Sinuosity"):

            geom = feat.GetGeometryRef()

            # Segment Distance
            pt_start = geom.GetPoint_2D(0)
            pt_end = geom.GetPoint_2D(geom.GetPointCount() - 1)
            segment_dist = haversine(pt_start[0], pt_start[1], pt_end[0], pt_end[1])

            # Total length of all segments
            i = 0
            length = 0
            while i < geom.GetPointCount() - 1:
                p1 = geom.GetPoint(i)
                i += 1
                p2 = geom.GetPoint(i)
                length = length + haversine(p1[0], p1[1], p2[0], p2[1])

            # Planform Sinuosity
            sinuosity = length / segment_dist

            # Write outupt
            feat.SetField(out_field_name, sinuosity)
            feat.SetField('GNAT_LengthKM', length)
            feat.SetField('GNAT_SegDistKM', segment_dist)
            flowlines_lyr.ogr_layer.SetFeature(feat)


def haversine(lon1, lat1, lon2, lat2):
    """
    Calculate the great circle distance between two points
    on the earth (specified in decimal degrees)
    """
    # convert decimal degrees to radians
    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])

    # haversine formula
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat / 2)**2 + cos(lat1) * cos(lat2) * sin(dlon / 2)**2
    c = 2 * asin(sqrt(a))
    r = 6371  # Radius of earth in kilometers. Use 3956 for miles
    return c * r


if __name__ == "__main__":

    planform_sinuosity(r"D:\NAR_Data\Data\gnat\hydrology.gpkg\network")
