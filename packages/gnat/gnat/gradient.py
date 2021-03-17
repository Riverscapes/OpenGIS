#!/usr/bin/env python3
# Name:     GNAT - Gradient
#
# Purpose:  Calculate Gradient on a segmented network
#
# Author:   Kelly Whitehead
#
# Date:     18 Feb 2021
# -------------------------------------------------------------------------------
from gdal import ogr

from rscommons.reach_geometry import reach_geometry
from rscommons import GeopackageLayer, Logger


def gradient(line_network, name, dem, gnat_database):

    log = Logger("GNAT Gradient")
    log.info(f'Starting gradient')

    with GeopackageLayer(line_network, write=True) as flowlines_lyr:

        # Field management
        field_names = {'Length': f"GNAT_{name}_GradLength",
                       'Gradient': f"GNAT_{name}_Gradient",
                       'MinElevation': f"GNAT_{name}_ElevMin",
                       'MaxElevation': f'GNAT_{name}_ElevMax'}

        for field in field_names.values():
            ix_field = flowlines_lyr.ogr_layer.GetLayerDefn().GetFieldIndex(field)
            if ix_field >= 0:
                flowlines_lyr.ogr_layer.DeleteField(ix_field)
            flowlines_lyr.ogr_layer.CreateField(ogr.FieldDefn(field, ogr.OFTReal))

    reach_geometry(line_network, dem, 100.0, field_names)
