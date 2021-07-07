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
import typing
import glob
import traceback
import uuid
import datetime
import sqlite3
from osgeo import ogr
from osgeo import gdal

from rscommons import Logger, RSProject, RSLayer, ModelConfig, dotenv, initGDALOGRErrors
from rscommons.util import safe_makedirs, safe_remove_dir, parse_metadata
from rscommons import GeopackageLayer
from rscommons.database import load_lookup_data

from gnat.gradient import gradient
from gnat.sinuosity import segment_sinuosity
from gnat.network_structure import build_network_structure
from gnat.zonal_attributes import zonal_intersect, summerize_attributes
from gnat.gnat import GNAT

from gnat.__version__ import __version__

Path = typing.Union[str, bytes, os.PathLike]

initGDALOGRErrors()

cfg = ModelConfig('http://xml.riverscapes.xyz/Projects/XSD/V1/GNAT.xsd', __version__)

LayerTypes = {
    # key: (name, id, tag, relpath)
    'DEM': RSLayer('NED 10m DEM', 'DEM', 'DEM', 'topography/dem.tif')
}


def gnat(huc: int, output_folder: Path, vb_polygons: Path, inputs: dict, epsg: int = None):
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

    # TODO: Create Project
    # TODO: Copy inputs

    intermediates_gpkg = os.path.join(output_folder, 'intermediates', 'gnat_intermediates.gpkg')
    outputs_gpkg = os.path.join(output_folder, 'outputs', 'gnat.gpkg')

    # set up gnat database and run
    gnat_run = GNAT(outputs_gpkg, vb_polygons, inputs)

    # test for measurements based system
    zonal_tabulation = zonal_intersect(gnat_run.riverscapes_features, gnat_run.inputs['flowlines'], epsg=epsg)
    summary = summerize_attributes(zonal_tabulation)

    # Test for summary attributes based system
    # if gnat_run.run_metric['Planform Sinuosity Min']:
    #     planform_sinuosity = os.path.join(intermediates_gpkg, "PlanformSinuosity")
    #     segment_sinuosity(inputs['segmented_flowlines'], planform_sinuosity, "PlanformSinuosity")
    #     summary_fields = ['PlanformSinuosity']  # TODO pull this from db
    #     zonal_tabulation = zonal_intersect(gnat_run.riverscapes_features, planform_sinuosity, summary_fields, epsg)
    #     summarized_outputs = summerize_attributes(zonal_tabulation, summary_fields)
    #     attributes = ['Planform Sinuosity Max', 'Planform Sinuosity Min', 'Planform Sinuosity Mean']
    #     gnat_run.write_gnat_attributes(summarized_outputs, attributes)

    # if gnat_run.run_metric['Convergent Count']:

    #     network_nodes = build_network_structure(gnat_run.inputs['flowlines'], intermediates_gpkg)
    #     summary_fields = ['convergent', 'divergent']
    #     zonal_tabulation = zonal_intersect(gnat_run.riverscapes_features, network_nodes, summary_fields, epsg)
    #     summarized_outputs = summerize_attributes(zonal_tabulation, summary_fields)
    #     attributes = ['Convergent Count', 'Divergent Count']
    #     gnat_run.write_gnat_attributes(summarized_outputs, attributes)

    # Channel Sinuosity

    # Valley Bottom Sinuosity

    # 2 Gradient Attributes

    # Channel Gradient

    # Valley Gradient


def main():
    parser = argparse.ArgumentParser(
        description='GNAT',
        # epilog="This is an epilog"
    )
    parser.add_argument('huc', help='HUC identifier', type=str)
    parser.add_argument('vb_polygons', help='valley bottom polygons (.gpkg/layer_name)', type=str)
    parser.add_argument('output_folder', help='Output folder', type=str)
    parser.add_argument('inputs', help='gnat inputs as comma separated key=value pairs')
    parser.add_argument('--epsg')
    parser.add_argument('--reach_codes', help='Comma delimited reach codes (FCode) to retain when filtering features. Omitting this option retains all features.', type=str)
    parser.add_argument('--meta', help='riverscapes project metadata as comma separated key=value pairs', type=str)
    parser.add_argument('--verbose', help='(optional) a little extra logging ', action='store_true', default=False)
    parser.add_argument('--debug', help="(optional) save intermediate outputs for debugging", action='store_true', default=False)

    args = dotenv.parse_args_env(parser)

    # Initiate the log file
    log = Logger("GNAT")
    log.setup(logPath=os.path.join(args.output_folder, "gnat.log"), verbose=args.verbose)
    log.title('GNAT For HUC: {}'.format(args.huc))

    inputs = parse_metadata(args.inputs)

    try:
        gnat(args.huc, args.output_folder, args.vb_polygons, inputs, args.epsg)

    except Exception as e:
        log.error(e)
        traceback.print_exc(file=sys.stdout)
        sys.exit(1)

    sys.exit(0)


if __name__ == '__main__':
    main()
