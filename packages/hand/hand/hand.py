# Name:     Height Above Nearest Drainage (HAND)
#
# Purpose:  Perform HAND
#
# Author:   Matt Reimer
#
# Date:     May 5, 2021
#
# -------------------------------------------------------------------------------
import argparse
import os
import sys
import uuid
import traceback
import datetime
import json
import glob
import sqlite3
import time
from typing import List, Dict

# LEave OSGEO import alone. It is necessary even if it looks unused
from osgeo import gdal
import rasterio
import numpy as np
from scipy import interpolate

from rscommons.util import safe_makedirs, parse_metadata
from rscommons import RSProject, RSLayer, ModelConfig, ProgressBar, Logger, dotenv, initGDALOGRErrors, TempRaster
from rscommons import GeopackageLayer
from rscommons.vector_ops import polygonize, buffer_by_field, copy_feature_class
from rscommons.hand import create_hand_raster

from vbet.vbet_network import vbet_network

from hand.hand_report import HANDReport
from hand.__version__ import __version__

initGDALOGRErrors()

cfg = ModelConfig('http://xml.riverscapes.xyz/Projects/XSD/V1/HAND.xsd', __version__)

LayerTypes = {
    'DEM': RSLayer('DEM', 'DEM', 'Raster', 'inputs/dem.tif'),
    'SLOPE_RASTER': RSLayer('Slope Raster', 'SLOPE_RASTER', 'Raster', 'inputs/slope.tif'),
    'HILLSHADE': RSLayer('DEM Hillshade', 'HILLSHADE', 'Raster', 'inputs/dem_hillshade.tif'),
    'INPUTS': RSLayer('Inputs', 'INPUTS', 'Geopackage', 'inputs/hand_inputs.gpkg', {
        'FLOWLINES': RSLayer('NHD Flowlines', 'FLOWLINES', 'Vector', 'flowlines'),
        'FLOW_AREA': RSLayer('NHD Flow Areas', 'FLOW_AREA', 'Vector', 'flow_areas'),
    }),
    'CHANNEL_BUFFER_RASTER': RSLayer('Channel Buffer Raster', 'CHANNEL_BUFFER_RASTER', 'Raster', 'intermediates/channelbuffer.tif'),
    'FLOW_AREA_RASTER': RSLayer('Flow Area Raster', 'FLOW_AREA_RASTER', 'Raster', 'intermediates/flowarea.tif'),
    'HAND_RASTER': RSLayer('Hand Raster', 'HAND_RASTER', 'Raster', 'intermediates/HAND.tif'),
    'CHANNEL_DISTANCE': RSLayer('Channel Euclidean Distance', 'CHANNEL_DISTANCE', "Raster", "intermediates/ChannelEuclideanDist.tif"),
    'FLOW_AREA_DISTANCE': RSLayer('Flow Area Euclidean Distance', 'FLOW_AREA_DISTANCE', "Raster", "intermediates/FlowAreaEuclideanDist.tif"),
    'NORMALIZED_SLOPE': RSLayer('Normalized Slope', 'NORMALIZED_SLOPE', "Raster", "intermediates/nLoE_Slope.tif"),
    'NORMALIZED_HAND': RSLayer('Normalized HAND', 'NORMALIZED_HAND', "Raster", "intermediates/nLoE_Hand.tif"),
    'REPORT': RSLayer('RSContext Report', 'REPORT', 'HTMLFile', 'outputs/hand.html')
}


def hand(huc, flowlines_orig, flowareas_orig, orig_dem, hillshade, project_folder, reach_codes: List[str], meta: Dict[str, str]):
    """[summary]

    Args:
        huc ([type]): [description]
        flowlines_orig ([type]): [description]
        flowareas_orig ([type]): [description]
        orig_dem ([type]): [description]
        hillshade ([type]): [description]
        project_folder ([type]): [description]
        reach_codes (List[int]): NHD reach codes for features to include in outputs
        meta (Dict[str,str]): dictionary of riverscapes metadata key: value pairs
    """
    log = Logger('HAND')
    log.info('Starting HAND v.{}'.format(cfg.version))

    project, _realization, proj_nodes = create_project(huc, project_folder)

    # Incorporate project metadata to the riverscapes project
    if meta is not None:
        project.add_metadata(meta)

    # Copy the inp
    _proj_dem_node, proj_dem = project.add_project_raster(proj_nodes['Inputs'], LayerTypes['DEM'], orig_dem)
    _hillshade_node, hillshade = project.add_project_raster(proj_nodes['Inputs'], LayerTypes['HILLSHADE'], hillshade)

    # Copy input shapes to a geopackage
    inputs_gpkg_path = os.path.join(project_folder, LayerTypes['INPUTS'].rel_path)
    intermediates_gpkg_path = os.path.join(project_folder, LayerTypes['INTERMEDIATES'].rel_path)

    flowlines_path = os.path.join(inputs_gpkg_path, LayerTypes['INPUTS'].sub_layers['FLOWLINES'].rel_path)
    flowareas_path = os.path.join(inputs_gpkg_path, LayerTypes['INPUTS'].sub_layers['FLOW_AREA'].rel_path)

    # Make sure we're starting with a fresh slate of new geopackages
    GeopackageLayer.delete(inputs_gpkg_path)
    GeopackageLayer.delete(intermediates_gpkg_path)

    copy_feature_class(flowlines_orig, flowlines_path, epsg=cfg.OUTPUT_EPSG)
    copy_feature_class(flowareas_orig, flowareas_path, epsg=cfg.OUTPUT_EPSG)

    project.add_project_geopackage(proj_nodes['Inputs'], LayerTypes['INPUTS'])

    # Create a copy of the flow lines with just the perennial and also connectors inside flow areas
    network_path = os.path.join(intermediates_gpkg_path, LayerTypes['INTERMEDIATES'].sub_layers['HAND_NETWORK'].rel_path)
    vbet_network(flowlines_path, flowareas_path, network_path, cfg.OUTPUT_EPSG, reach_codes)

    ############################
    # The main event:
    ############################

    # Generate HAND from dem and vbet_network
    # TODO make a place for this temporary folder. it can be removed after hand is generated.
    temp_hand_dir = os.path.join(project_folder, "intermediates", "hand_processing")
    safe_makedirs(temp_hand_dir)

    hand_raster = os.path.join(project_folder, LayerTypes['HAND_RASTER'].rel_path)
    create_hand_raster(proj_dem, network_path, temp_hand_dir, hand_raster)

    project.add_project_raster(proj_nodes['Outputs'], LayerTypes['HAND_RASTER'])

    report_path = os.path.join(project.project_dir, LayerTypes['REPORT'].rel_path)
    project.add_report(proj_nodes['Outputs'], LayerTypes['REPORT'], replace=True)

    report = HANDReport(report_path, project)
    report.write()

    log.info('HAND Completed Successfully')


def create_project(huc, output_dir):
    project_name = 'HAND for HUC {}'.format(huc)
    project = RSProject(cfg, output_dir)
    project.create(project_name, 'HAND')

    project.add_metadata({
        'HUC{}'.format(len(huc)): str(huc),
        'HUC': str(huc),
        'HANDVersion': cfg.version,
        'HANDTimestamp': str(int(time.time()))
    })

    realizations = project.XMLBuilder.add_sub_element(project.XMLBuilder.root, 'Realizations')
    realization = project.XMLBuilder.add_sub_element(realizations, 'HAND', None, {
        'id': 'HAND',
        'dateCreated': datetime.datetime.now().isoformat(),
        'guid': str(uuid.uuid1()),
        'productVersion': cfg.version
    })

    project.XMLBuilder.add_sub_element(realization, 'Name', project_name)
    proj_nodes = {
        'Inputs': project.XMLBuilder.add_sub_element(realization, 'Inputs'),
        'Intermediates': project.XMLBuilder.add_sub_element(realization, 'Intermediates'),
        'Outputs': project.XMLBuilder.add_sub_element(realization, 'Outputs')
    }

    # Make sure we have these folders
    proj_dir = os.path.dirname(project.xml_path)
    safe_makedirs(os.path.join(proj_dir, 'inputs'))
    safe_makedirs(os.path.join(proj_dir, 'intermediates'))
    safe_makedirs(os.path.join(proj_dir, 'outputs'))

    project.XMLBuilder.write()
    return project, realization, proj_nodes


def main():

    parser = argparse.ArgumentParser(
        description='Riverscapes HAND Tool',
        # epilog="This is an epilog"
    )
    parser.add_argument('huc', help='NHD flow line ShapeFile path', type=str)
    parser.add_argument('flowlines', help='NHD flow line ShapeFile path', type=str)
    parser.add_argument('flowareas', help='NHD flow areas ShapeFile path', type=str)
    parser.add_argument('slope', help='Slope raster path', type=str)
    parser.add_argument('dem', help='DEM raster path', type=str)
    parser.add_argument('hillshade', help='Hillshade raster path', type=str)
    parser.add_argument('output_dir', help='Folder where output HAND project will be created', type=str)
    parser.add_argument('--reach_codes', help='Comma delimited reach codes (FCode) to retain when filtering features. Omitting this option retains all features.', type=str)
    parser.add_argument('--meta', help='riverscapes project metadata as comma separated key=value pairs', type=str)
    parser.add_argument('--verbose', help='(optional) a little extra logging ', action='store_true', default=False)
    parser.add_argument('--debug', help='Add debug tools for tracing things like memory usage at a performance cost.', action='store_true', default=False)

    args = dotenv.parse_args_env(parser)

    # make sure the output folder exists
    safe_makedirs(args.output_dir)

    # Initiate the log file
    log = Logger('HAND')
    log.setup(logPath=os.path.join(args.output_dir, 'hand.log'), verbose=args.verbose)
    log.title('Riverscapes HAND For HUC: {}'.format(args.huc))

    meta = parse_metadata(args.meta)

    reach_codes = args.reach_codes.split(',') if args.reach_codes else None

    try:
        if args.debug is True:
            from rscommons.debug import ThreadRun
            memfile = os.path.join(args.output_dir, 'hand_mem.log')
            retcode, max_obj = ThreadRun(hand, memfile, args.huc, args.flowlines, args.flowareas, args.dem, args.hillshade, args.output_dir, reach_codes, meta)
            log.debug('Return code: {}, [Max process usage] {}'.format(retcode, max_obj))

        else:
            hand(args.huc, args.flowlines, args.flowareas, args.dem, args.hillshade, args.output_dir, reach_codes, meta)

    except Exception as e:
        log.error(e)
        traceback.print_exc(file=sys.stdout)
        sys.exit(1)

    sys.exit(0)


if __name__ == '__main__':
    main()
