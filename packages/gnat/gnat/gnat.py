#!/usr/bin/env python3
# Name:     GNAT
#
# Purpose:  Build a GNAT project by downloading and preparing
#           commonly used data layers for several riverscapes tools.
#
# Author:   Kelly Whitehead
#
# Date:     24 Sep 2020
# -------------------------------------------------------------------------------
import argparse
import sys
import os
import glob
import traceback
import uuid
import datetime
import sqlite3
from osgeo import ogr
from osgeo import gdal

from rscommons import Logger, RSProject, RSLayer, ModelConfig, dotenv, initGDALOGRErrors
from rscommons.util import safe_makedirs, safe_remove_dir
from rscommons import GeopackageLayer
from rscommons.database import load_lookup_data

from gnat.gradient import gradient
from gnat.sinuosity import planform_sinuosity

from gnat.__version__ import __version__

initGDALOGRErrors()

cfg = ModelConfig('http://xml.riverscapes.xyz/Projects/XSD/V1/GNAT.xsd', __version__)

LayerTypes = {
    # key: (name, id, tag, relpath)
    'DEM': RSLayer('NED 10m DEM', 'DEM', 'DEM', 'topography/dem.tif')
}


def gnat(huc, output_folder):
    """[summary]

    Args:
        huc ([type]): [description]

    Raises:
        Exception: [description]
        Exception: [description]
        Exception: [description]
        Exception: [description]

    Returns:
        [type]: [description]
    """

    log = Logger("GNAT")
    log.info('GNAT v.{}'.format(cfg.version))

    try:
        int(huc)
    except ValueError:
        raise Exception('Invalid HUC identifier "{}". Must be an integer'.format(huc))

    if not (len(huc) == 4 or len(huc) == 8):
        raise Exception('Invalid HUC identifier. Must be four digit integer')

    safe_makedirs(output_folder)

    # Create Project
    # Copy layers to outputs -  write attributes on these

    # 1 Sinuosity Attributes

    # Planform Sinuosity

    # Channel Sinuosity

    # Valley Bottom Sinuosity

    # 2 Gradient Attributes

    # Channel Gradient

    # Valley Gradient


def gnat_database(gnat_gpkg, in_layer, overwrite_existing=False):
    """generates (if needed) and loads layer to gnat database

    Args:
        gnat_gpkg ([type]): [description]
        in_layer ([type]): [description]

    Returns:
        [type]: [description]
    """

    schema_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'database')

    if overwrite_existing:
        safe_remove_dir(gnat_gpkg)

    if not os.path.exists(gnat_gpkg):
        with GeopackageLayer(in_layer) as lyr_inputs, \
                GeopackageLayer(gnat_gpkg, layer_name='riverscapes', write=True) as lyr_outputs:

            srs = lyr_inputs.ogr_layer.GetSpatialRef()

            lyr_outputs.create_layer(ogr.wkbPolygon, spatial_ref=srs, options=['FID=riverscape_id'], fields={
                'area_sqkm': ogr.OFTReal,
                'river_style_id': ogr.OFTInteger
            })

            for feat, *_ in lyr_inputs.iterate_features("Copying Riverscapes Features"):
                geom = feat.GetGeometryRef()
                fid = feat.GetFID()
                area = geom.GetArea()  # TODO calculate area as sqkm

                out_feature = ogr.Feature(lyr_outputs.ogr_layer_def)
                out_feature.SetGeometry(geom)
                out_feature.SetFID(fid)
                out_feature.SetField('area_sqkm', area)

                lyr_outputs.ogr_layer.CreateFeature(out_feature)
                out_feature = None

        # log.info('Creating database schema at {0}'.format(database))
        qry = open(os.path.join(schema_path, 'gnat_schema.sql'), 'r').read()
        sqlite3.complete_statement(qry)
        conn = sqlite3.connect(gnat_gpkg)
        conn.execute('PRAGMA foreign_keys = ON;')
        curs = conn.cursor()
        curs.executescript(qry)

        load_lookup_data(gnat_gpkg, schema_path)

    out_layer = os.path.join(gnat_gpkg, 'riverscapes')

    return out_layer


def write_gnat_attributes(gnat_gpkg, reaches, attributes, set_null_first=False):

    if len(reaches) < 1:
        return

    conn = sqlite3.connect(gnat_gpkg)
    conn.execute('pragma foreign_keys=ON')
    curs = conn.cursor()

    # Optionally clear all the values in the fields first
    # if set_null_first is True:
    #     [curs.execute(f'UPDATE {table_name} SET {field} = NULL') for field in fields]

    for attribute in attributes:

        sql = f'SELECT attribute_id from attributes where machine_name = ?'
        attribute_id = curs.execute(sql, [attribute]).fetchone()[0]

        fieldname, summary = attribute.split('_')

        sql = f'INSERT INTO riverscape_attributes (riverscape_id, attribute_id, value) VALUES(?,?,?)'
        curs.executemany(sql, [(reach, attribute_id, value[fieldname][summary]) for reach, value in reaches.items() if fieldname in value.keys()])
        conn.commit()


def main():
    parser = argparse.ArgumentParser(
        description='GNAT',
        # epilog="This is an epilog"
    )
    parser.add_argument('huc', help='HUC identifier', type=str)
    parser.add_argument('flowlines', help="NHD Flowlines (.gpkg/layer_name)", type=str)
    parser.add_argument('valley_bottom', help='valley bottom polygon (.gpkg/layer_name)', type=str)
    parser.add_argument('valley_bottom_centerline', help='valley bottom centerline (.gpkg/layer_name)', type=str)
    parser.add_argument('dem', help='DEM raster path', type=str)
    parser.add_argument('output_folder', help='Output folder', type=str)
    parser.add_argument('--reach_codes', help='Comma delimited reach codes (FCode) to retain when filtering features. Omitting this option retains all features.', type=str)
    parser.add_argument('--meta', help='riverscapes project metadata as comma separated key=value pairs', type=str)
    parser.add_argument('--verbose', help='(optional) a little extra logging ', action='store_true', default=False)
    parser.add_argument('--debug', help="(optional) save intermediate outputs for debugging", action='store_true', default=False)

    args = dotenv.parse_args_env(parser)

    # Initiate the log file
    log = Logger("GNAT")
    log.setup(logPath=os.path.join(args.output, "gnat.log"), verbose=args.verbose)
    log.title('GNAT For HUC: {}'.format(args.huc))

    try:
        gnat(args.huc, args.output_folder)

    except Exception as e:
        log.error(e)
        traceback.print_exc(file=sys.stdout)
        sys.exit(1)

    sys.exit(0)


if __name__ == '__main__':
    main()
