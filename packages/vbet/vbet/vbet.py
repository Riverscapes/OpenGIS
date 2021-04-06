# Name:     Valley Bottom
#
# Purpose:  Perform initial VBET analysis that can be used by the BRAT conservation
#           module
#
# Author:   Matt Reimer
#
# Date:     November 20, 2020
#
# Vectorize polygons from raster
# https://gis.stackexchange.com/questions/187877/how-to-polygonize-raster-to-shapely-polygons
# -------------------------------------------------------------------------------
import argparse
import os
import sys
import uuid
import traceback
import datetime
import json
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
from rscommons.database import load_lookup_data

from vbet.vbet_network import vbet_network, create_drainage_area_zones
from vbet.vbet_report import VBETReport
from vbet.vbet_raster_ops import rasterize, proximity_raster, raster_clean, rasterize_attribute
from vbet.vbet_outputs import threshold, sanitize
from vbet.__version__ import __version__

initGDALOGRErrors()

cfg = ModelConfig('http://xml.riverscapes.xyz/Projects/XSD/V1/VBET.xsd', __version__)

thresh_vals = {"50": 0.5, "60": 0.6, "70": 0.7, "80": 0.8, "90": 0.9, "100": 1}

LayerTypes = {
    'DEM': RSLayer('DEM', 'DEM', 'Raster', 'inputs/dem.tif'),
    'SLOPE_RASTER': RSLayer('Slope Raster', 'SLOPE_RASTER', 'Raster', 'inputs/slope.tif'),
    'HILLSHADE': RSLayer('DEM Hillshade', 'HILLSHADE', 'Raster', 'inputs/dem_hillshade.tif'),
    'INPUTS': RSLayer('Inputs', 'INPUTS', 'Geopackage', 'inputs/vbet_inputs.gpkg', {
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
    'NORMALIZED_CHANNEL_DISTANCE': RSLayer('Normalized Channel Distance', 'NORMALIZED_CHANNEL_DISTANCE', "Raster", "intermediates/nLoE_ChannelDist.tif"),
    'NORMALIZED_FLOWAREA_DISTANCE': RSLayer('Normalized Flow Area Distance', 'NORMALIZED_FLOWAREA_DISTANCE', "Raster", "intermediates/nLoE_FlowAreaDist.tif"),
    'EVIDENCE_TOPO': RSLayer('Topo Evidence', 'EVIDENCE_TOPO', 'Raster', 'intermediates/Topographic_Evidence.tif'),
    'EVIDENCE_CHANNEL': RSLayer('Channel Evidence', 'EVIDENCE_CHANNEL', 'Raster', 'intermediates/Channel_Evidence.tif'),
    'INTERMEDIATES': RSLayer('Intermediates', 'Intermediates', 'Geopackage', 'intermediates/vbet_intermediates.gpkg', {
        'VBET_NETWORK': RSLayer('VBET Network', 'VBET_NETWORK', 'Vector', 'vbet_network'),
        'VBET_NETWORK_BUFFERED': RSLayer('VBET Network Buffer', 'VBET_NETWORK_BUFFERED', 'Vector', 'vbet_network_buffered'),
        # We also add all tht raw thresholded shapes here but they get added dynamically later
    }),
    # Same here. Sub layers are added dynamically later.
    'VBET_EVIDENCE': RSLayer('VBET Evidence Raster', 'VBET_EVIDENCE', 'Raster', 'outputs/VBET_Evidence.tif'),
    'VBET_OUTPUTS': RSLayer('VBET', 'VBET_OUTPUTS', 'Geopackage', 'outputs/vbet.gpkg'),
    'REPORT': RSLayer('RSContext Report', 'REPORT', 'HTMLFile', 'outputs/vbet.html')
}


def vbet(huc, flowlines_orig, flowareas_orig, orig_slope, json_transforms, orig_dem, hillshade, catchments_orig, project_folder, reach_codes: List[str], meta: Dict[str, str]):
    """[summary]

    Args:
        huc ([type]): [description]
        flowlines_orig ([type]): [description]
        flowareas_orig ([type]): [description]
        orig_slope ([type]): [description]
        json_transforms ([type]): [description]
        orig_dem ([type]): [description]
        hillshade ([type]): [description]
        max_hand ([type]): [description]
        min_hole_area_m ([type]): [description]
        project_folder ([type]): [description]
        reach_codes (List[int]): NHD reach codes for features to include in outputs
        meta (Dict[str,str]): dictionary of riverscapes metadata key: value pairs
    """
    log = Logger('VBET')
    log.info('Starting VBET v.{}'.format(cfg.version))

    project, _realization, proj_nodes = create_project(huc, project_folder)

    # Incorporate project metadata to the riverscapes project
    if meta is not None:
        project.add_metadata(meta)

    # Copy the inp
    _proj_slope_node, proj_slope = project.add_project_raster(proj_nodes['Inputs'], LayerTypes['SLOPE_RASTER'], orig_slope)
    _proj_dem_node, proj_dem = project.add_project_raster(proj_nodes['Inputs'], LayerTypes['DEM'], orig_dem)
    _hillshade_node, hillshade = project.add_project_raster(proj_nodes['Inputs'], LayerTypes['HILLSHADE'], hillshade)

    # Copy input shapes to a geopackage
    inputs_gpkg_path = os.path.join(project_folder, LayerTypes['INPUTS'].rel_path)
    intermediates_gpkg_path = os.path.join(project_folder, LayerTypes['INTERMEDIATES'].rel_path)

    flowlines_path = os.path.join(inputs_gpkg_path, LayerTypes['INPUTS'].sub_layers['FLOWLINES'].rel_path)
    flowareas_path = os.path.join(inputs_gpkg_path, LayerTypes['INPUTS'].sub_layers['FLOW_AREA'].rel_path)
    catchments_path = os.path.join(intermediates_gpkg_path, 'Catchments')

    # Make sure we're starting with a fresh slate of new geopackages
    GeopackageLayer.delete(inputs_gpkg_path)
    GeopackageLayer.delete(intermediates_gpkg_path)

    copy_feature_class(flowlines_orig, flowlines_path, epsg=cfg.OUTPUT_EPSG)
    copy_feature_class(flowareas_orig, flowareas_path, epsg=cfg.OUTPUT_EPSG)
    copy_feature_class(catchments_orig, catchments_path, epsg=cfg.OUTPUT_EPSG)

    project.add_project_geopackage(proj_nodes['Inputs'], LayerTypes['INPUTS'])

    # Create a copy of the flow lines with just the perennial and also connectors inside flow areas
    network_path = os.path.join(intermediates_gpkg_path, LayerTypes['INTERMEDIATES'].sub_layers['VBET_NETWORK'].rel_path)
    vbet_network(flowlines_path, flowareas_path, network_path, cfg.OUTPUT_EPSG, reach_codes)

    # Build Transformation Tables
    database_folder = os.path.join(os.path.abspath(os.path.dirname(__file__)), '..', 'database')
    with sqlite3.connect(intermediates_gpkg_path) as conn:
        cursor = conn.cursor()
        with open(os.path.join(database_folder, 'vbet_schema.sql')) as sqlfile:
            sql_commands = sqlfile.read()
            cursor.executescript(sql_commands)
            conn.commit()

    # Load tables
    load_lookup_data(intermediates_gpkg_path, database_folder)

    # Load transforms from table
    # Load configuration from table
    scenario_code = "KW_TESTING"
    vbet_run = load_configuration(scenario_code, intermediates_gpkg_path)

    create_drainage_area_zones(catchments_path, flowlines_path, 'NHDPlusID', 'TotDASqKm', vbet_run['Zones'])

    in_rasters = {}
    if 'Slope' in vbet_run['Inputs']:
        in_rasters['Slope'] = proj_slope

    # Generate HAND from dem and vbet_network
    if 'HAND' in vbet_run['Inputs']:
        temp_hand_dir = os.path.join(project_folder, "intermediates", "hand_processing")
        safe_makedirs(temp_hand_dir)
        hand_raster = os.path.join(project_folder, LayerTypes['HAND_RASTER'].rel_path)
        create_hand_raster(proj_dem, network_path, temp_hand_dir, hand_raster)
        project.add_project_raster(proj_nodes['Intermediates'], LayerTypes['HAND_RASTER'])

        in_rasters['HAND'] = hand_raster

    # Rasterize the channel polygon and write to raster
    if 'Channel' in vbet_run['Inputs']:
        # Get raster resolution as min buffer and apply bankfull width buffer to reaches
        with rasterio.open(proj_slope) as raster:
            t = raster.transform
            min_buffer = (t[0] + abs(t[4])) / 2

        log.info("Buffering Polyine by bankfull width buffers")
        network_path_buffered = os.path.join(intermediates_gpkg_path, LayerTypes['INTERMEDIATES'].sub_layers['VBET_NETWORK_BUFFERED'].rel_path)
        buffer_by_field(network_path, network_path_buffered, "BFwidth", cfg.OUTPUT_EPSG, min_buffer)

        log.info('Writing channel raster using slope as a template')
        channel_buffer_raster = os.path.join(project_folder, LayerTypes['CHANNEL_BUFFER_RASTER'].rel_path)
        rasterize(network_path_buffered, channel_buffer_raster, proj_slope)
        project.add_project_raster(proj_nodes['Intermediates'], LayerTypes['CHANNEL_BUFFER_RASTER'])

        log.info('Generating Channel Proximity raster')
        channel_dist_raster = os.path.join(project_folder, LayerTypes['CHANNEL_DISTANCE'].rel_path)
        proximity_raster(channel_buffer_raster, channel_dist_raster)
        project.add_project_raster(proj_nodes["Intermediates"], LayerTypes['CHANNEL_DISTANCE'])

        in_rasters['Channel'] = channel_dist_raster

    if 'Flow Areas' in vbet_run['Inputs']:
        flow_area_raster = os.path.join(project_folder, LayerTypes['FLOW_AREA_RASTER'].rel_path)
        rasterize(flowareas_path, flow_area_raster, proj_slope)
        project.add_project_raster(proj_nodes['Intermediates'], LayerTypes['FLOW_AREA_RASTER'])
        fa_dist_raster = os.path.join(project_folder, LayerTypes['FLOW_AREA_DISTANCE'].rel_path)
        proximity_raster(flow_area_raster, fa_dist_raster)
        project.add_project_raster(proj_nodes["Intermediates"], LayerTypes['FLOW_AREA_DISTANCE'])

        in_rasters['Flow Areas'] = fa_dist_raster

    for vbet_input in vbet_run['Inputs'] if vbet_input not in ['Slope', 'HAND', 'Channel', 'Flow Areas']:
        'process'

    # Generate da Zone rasters
    for zone in vbet_run['Zones']:
        raster_name = os.path.join(project_folder, 'intermediates', f'da_{zone}_zones.tif')
        rasterize_attribute(catchments_path, raster_name, proj_slope, f'{zone}_Zone')
        in_rasters[f'{zone}_Zone'] = raster_name

    # da_slope_zone_raster = os.path.join(project_folder, 'intermediates', 'da_slope_zones.tif')
    # rasterize_attribute(catchments_path, da_slope_zone_raster, proj_slope, 'SLOPE_Zone')
    # da_hand_zone_raster = os.path.join(project_folder, 'intermediates', 'da_hand_zones.tif')
    # rasterize_attribute(catchments_path, da_hand_zone_raster, proj_slope, 'HAND_Zone')

    slope_transform_raster = os.path.join(project_folder, LayerTypes['NORMALIZED_SLOPE'].rel_path)
    hand_transform_raster = os.path.join(project_folder, LayerTypes['NORMALIZED_HAND'].rel_path)
    chan_dist_transform_raster = os.path.join(project_folder, LayerTypes['NORMALIZED_CHANNEL_DISTANCE'].rel_path)
    fa_dist_transform_raster = os.path.join(project_folder, LayerTypes['NORMALIZED_FLOWAREA_DISTANCE'].rel_path)

    topo_evidence_raster = os.path.join(project_folder, LayerTypes['EVIDENCE_TOPO'].rel_path)
    channel_evidence_raster = os.path.join(project_folder, LayerTypes['EVIDENCE_CHANNEL'].rel_path)
    evidence_raster = os.path.join(project_folder, LayerTypes['VBET_EVIDENCE'].rel_path)

    # Open evidence rasters concurrently. We're looping over windows so this shouldn't affect
    # memory consumption too much
    if in_rasters:
        open_rasters = {name: rasterio.open(raster) for name, raster in in_rasters.items()}

        # with rasterio.open(proj_slope) if proj_slope else None as slp_src, \
        #         rasterio.open(hand_raster) if hand_raster else None as hand_src, \
        #         rasterio.open(channel_dist_raster) as cdist_src, \
        #         rasterio.open(fa_dist_raster) as fadist_src, \
        #         rasterio.open(da_slope_zone_raster) as da_slope_zone_src,\
        #         rasterio.open(da_hand_zone_raster) as da_hand_zone_src:
        # All 3 rasters should have the same extent and properties. They differ only in dtype
        out_meta = open_rasters['Slope'].meta
        # Rasterio can't write back to a VRT so rest the driver and number of bands for the output
        out_meta['driver'] = 'GTiff'
        out_meta['count'] = 1
        out_meta['compress'] = 'deflate'
        # out_meta['dtype'] = rasterio.uint8
        # We use this to buffer the output
        cell_size = abs(open_rasters['Slope'].get_transform()[1])

        with rasterio.open(evidence_raster, 'w', **out_meta) as dest_evidence, \
                rasterio.open(topo_evidence_raster, "w", **out_meta) as dest, \
                rasterio.open(channel_evidence_raster, 'w', **out_meta) as dest_channel, \
                rasterio.open(slope_transform_raster, "w", **out_meta) as slope_ev_out, \
                rasterio.open(hand_transform_raster, 'w', **out_meta) as hand_ev_out, \
                rasterio.open(chan_dist_transform_raster, 'w', **out_meta) as chan_dist_ev_out, \
                rasterio.open(fa_dist_transform_raster, 'w', **out_meta) as fa_dist_ev_out:

            progbar = ProgressBar(len(list(open_rasters['Slope'].block_windows(1))), 50, "Calculating evidence layer")
            counter = 0
            # Again, these rasters should be orthogonal so their windows should also line up
            for _ji, window in open_rasters['Slope'].block_windows(1):
                progbar.update(counter)
                counter += 1
                slope_data = open_rasters['Slope'].read(1, window=window, masked=True)
                hand_data = open_rasters['HAND'].read(1, window=window, masked=True)
                cdist_data = open_rasters['Channel'].read(1, window=window, masked=True)
                fadist_data = open_rasters['Flow Areas'].read(1, window=window, masked=True)
                da_slope_zones_data = open_rasters['Slope_Zone'].read(1, window=window, masked=True)
                da_hand_zones_data = open_rasters['HAND_Zone'].read(1, window=window, masked=True)

                slope_transform_zones = [np.ma.MaskedArray(transform(slope_data.data), mask=slope_data.mask) for transform in vbet_run['Transforms']['Slope']]
                #  np.ma.MaskedArray(transforms["Slope MID"](slope_data.data), mask=slope_data.mask),
                #  np.ma.MaskedArray(transforms["Slope LARGE"](slope_data.data), mask=slope_data.mask)]

                hand_transform_zones = [np.ma.MaskedArray(transform(hand_data.data), mask=hand_data.mask) for transform in vbet_run['Transforms']['HAND']]
                # [np.ma.MaskedArray(transforms["HAND"](hand_data.data), mask=hand_data.mask),
                # np.ma.MaskedArray(transforms["HAND MID"](hand_data.data), mask=hand_data.mask),
                # np.ma.MaskedArray(transforms["HAND LARGE"](hand_data.data), mask=hand_data.mask)]

                slope_transform = np.ma.MaskedArray(np.choose(da_slope_zones_data.data, slope_transform_zones, mode='clip'), mask=slope_data.mask)

                hand_transform = np.ma.MaskedArray(np.choose(da_hand_zones_data.data, hand_transform_zones, mode='clip'), mask=hand_data.mask)

                # slope_transform = np.ma.MaskedArray(transforms["Slope"](slope_data.data), mask=slope_data.mask)
                # hand_transform = np.ma.MaskedArray(transforms["HAND"](hand_data.data), mask=hand_data.mask)
                channel_dist_transform = np.ma.MaskedArray(vbet_run['Transforms']["Channel"][0](cdist_data.data), mask=cdist_data.mask)
                fa_dist_transform = np.ma.MaskedArray(vbet_run['Transforms']["Flow Areas"][0](fadist_data.data), mask=fadist_data.mask)

                fvals_topo = slope_transform * hand_transform
                fvals_channel = np.maximum(channel_dist_transform, fa_dist_transform)
                fvals_evidence = np.maximum(fvals_topo, fvals_channel)

                # Fill the masked values with the appropriate nodata vals
                # Unthresholded in the base band (mostly for debugging)
                dest.write(np.ma.filled(np.float32(fvals_topo), out_meta['nodata']), window=window, indexes=1)

                slope_ev_out.write(slope_transform.astype('float32').filled(out_meta['nodata']), window=window, indexes=1)
                hand_ev_out.write(hand_transform.astype('float32').filled(out_meta['nodata']), window=window, indexes=1)
                chan_dist_ev_out.write(channel_dist_transform.astype('float32').filled(out_meta['nodata']), window=window, indexes=1)
                fa_dist_ev_out.write(fa_dist_transform.astype('float32').filled(out_meta['nodata']), window=window, indexes=1)

                dest_channel.write(np.ma.filled(np.float32(fvals_channel), out_meta['nodata']), window=window, indexes=1)
                dest_evidence.write(np.ma.filled(np.float32(fvals_evidence), out_meta['nodata']), window=window, indexes=1)
            progbar.finish()

        # The remaining rasters get added to the project
        project.add_project_raster(proj_nodes["Intermediates"], LayerTypes['NORMALIZED_SLOPE'])
        project.add_project_raster(proj_nodes["Intermediates"], LayerTypes['NORMALIZED_HAND'])
        project.add_project_raster(proj_nodes["Intermediates"], LayerTypes['NORMALIZED_CHANNEL_DISTANCE'])
        project.add_project_raster(proj_nodes["Intermediates"], LayerTypes['NORMALIZED_FLOWAREA_DISTANCE'])
        project.add_project_raster(proj_nodes['Intermediates'], LayerTypes['EVIDENCE_TOPO'])
        project.add_project_raster(proj_nodes['Intermediates'], LayerTypes['EVIDENCE_CHANNEL'])
        project.add_project_raster(proj_nodes['Outputs'], LayerTypes['VBET_EVIDENCE'])

    # Get the length of a meter (roughly)
    degree_factor = GeopackageLayer.rough_convert_metres_to_raster_units(proj_slope, 1)
    buff_dist = cell_size
    # min_hole_degrees = min_hole_area_m * (degree_factor ** 2)

    # Get the full paths to the geopackages
    intermed_gpkg_path = os.path.join(project_folder, LayerTypes['INTERMEDIATES'].rel_path)
    vbet_path = os.path.join(project_folder, LayerTypes['VBET_OUTPUTS'].rel_path)

    for str_val, thr_val in {"50": 0.5}.items():  # thresh_vals.items():
        plgnize_id = 'THRESH_{}'.format(str_val)
        with TempRaster('vbet_raw_thresh_{}'.format(plgnize_id)) as tmp_raw_thresh, \
                TempRaster('vbet_cleaned_thresh_{}'.format(plgnize_id)) as tmp_cleaned_thresh:

            log.debug('Temporary threshold raster: {}'.format(tmp_raw_thresh.filepath))
            threshold(evidence_raster, thr_val, tmp_raw_thresh.filepath)

            raster_clean(tmp_raw_thresh.filepath, tmp_cleaned_thresh.filepath, buffer_pixels=1)

            plgnize_lyr = RSLayer('Raw Threshold at {}%'.format(str_val), plgnize_id, 'Vector', plgnize_id.lower())
            # Add a project node for this thresholded vector
            LayerTypes['INTERMEDIATES'].add_sub_layer(plgnize_id, plgnize_lyr)

            vbet_id = 'VBET_{}'.format(str_val)
            vbet_lyr = RSLayer('Threshold at {}%'.format(str_val), vbet_id, 'Vector', vbet_id.lower())
            # Add a project node for this thresholded vector
            LayerTypes['VBET_OUTPUTS'].add_sub_layer(vbet_id, vbet_lyr)
            # Now polygonize the raster
            log.info('Polygonizing')
            polygonize(tmp_cleaned_thresh.filepath, 1, '{}/{}'.format(intermed_gpkg_path, plgnize_lyr.rel_path), cfg.OUTPUT_EPSG)
            log.info('Done')

        # Now the final sanitization
        sanitize(
            str_val,
            '{}/{}'.format(intermed_gpkg_path, plgnize_lyr.rel_path),
            '{}/{}'.format(vbet_path, vbet_lyr.rel_path),
            buff_dist,
            network_path
        )
        log.info('Completed thresholding at {}'.format(thr_val))

    # Now add our Geopackages to the project XML
    project.add_project_geopackage(proj_nodes['Intermediates'], LayerTypes['INTERMEDIATES'])
    project.add_project_geopackage(proj_nodes['Outputs'], LayerTypes['VBET_OUTPUTS'])

    report_path = os.path.join(project.project_dir, LayerTypes['REPORT'].rel_path)
    project.add_report(proj_nodes['Outputs'], LayerTypes['REPORT'], replace=True)

    report = VBETReport(report_path, project)
    report.write()

    log.info('VBET Completed Successfully')


def load_transform_functions(json_transforms, database):

    conn = sqlite3.connect(database)
    conn.execute('pragma foreign_keys=ON')
    curs = conn.cursor()

    transform_functions = {}

    # TODO how to handle missing transforms? use defaults?

    for input_name, transform_id in json.loads(json_transforms).items():
        transform_type = curs.execute("""SELECT transform_types.name from transforms INNER JOIN transform_types ON transform_types.type_id = transforms.type_id where transforms.transform_id = ?""", [transform_id]).fetchone()[0]
        values = curs.execute("""SELECT input_value, output_value FROM inflections WHERE transform_id = ? ORDER BY input_value """, [transform_id]).fetchall()

        transform_functions[input_name] = interpolate.interp1d(np.array([v[0] for v in values]), np.array([v[1] for v in values]), kind=transform_type, bounds_error=False, fill_value=0.0)

        if transform_type == "Polynomial":
            # add polynomial function
            transform_functions[input_name] = None

    return transform_functions


def load_configuration(machine_code, database):

    conn = sqlite3.connect(database)
    conn.execute('pragma foreign_keys=ON')
    curs = conn.cursor()

    configuration = {}

    # 1 Get inputs
    inputs = curs.execute(""" SELECT inputs.name, inputs.input_id, scenario_input_id FROM scenarios
                               INNER JOIN scenario_inputs ON scenarios.scenario_id = scenario_inputs.scenario_id
                               INNER JOIN inputs ON scenario_inputs.input_id = inputs.input_id
                               WHERE machine_code = ?;""", [machine_code]).fetchall()

    inputs_dict = {}
    for input_value in inputs:

        zones = curs.execute("""SELECT transform_id, min_da, max_da FROM input_zones WHERE scenario_input_id = ?""", [input_value[2]]).fetchall()

        transform_zones = {}
        for zone in zones:
            transform_zones[zone[0]] = {'min': zone[1], 'max': zone[2]}

        inputs_dict[input_value[0]] = {'input_id': input_value[1], 'transform_zones': transform_zones}

    configuration['Inputs'] = inputs_dict

    transforms_dict = {}
    for input_name, val in configuration['Inputs'].items():
        input_transforms = []
        for i, transform_id in enumerate(val['transform_zones']):
            transform_type = curs.execute("""SELECT transform_types.name from transforms INNER JOIN transform_types ON transform_types.type_id = transforms.type_id where transforms.transform_id = ?""", [transform_id]).fetchone()[0]
            values = curs.execute("""SELECT input_value, output_value FROM inflections WHERE transform_id = ? ORDER BY input_value """, [transform_id]).fetchall()

            if transform_type == "Polynomial":
                # add polynomial function
                transforms_dict[transform_id] = None

            input_transforms.append(interpolate.interp1d(np.array([v[0] for v in values]), np.array([v[1] for v in values]), kind=transform_type, bounds_error=False, fill_value=0.0))
        transforms_dict[input_name] = input_transforms

    configuration['Transforms'] = transforms_dict

    zones_dict = {}
    for input_name, input_zones in configuration["Inputs"].items():

        if len(input_zones['transform_zones']) > 1:
            zone = {}
            for i, zone_values in enumerate(input_zones['transform_zones'].values()):
                zone[i] = zone_values['max']
            zones_dict[input_name] = zone

    configuration['Zones'] = zones_dict

    return configuration


def create_project(huc, output_dir):
    project_name = 'VBET for HUC {}'.format(huc)
    project = RSProject(cfg, output_dir)
    project.create(project_name, 'VBET')

    project.add_metadata({
        'HUC{}'.format(len(huc)): str(huc),
        'HUC': str(huc),
        'VBETVersion': cfg.version,
        'VBETTimestamp': str(int(time.time()))
    })

    realizations = project.XMLBuilder.add_sub_element(project.XMLBuilder.root, 'Realizations')
    realization = project.XMLBuilder.add_sub_element(realizations, 'VBET', None, {
        'id': 'VBET',
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
        description='Riverscapes VBET Tool',
        # epilog="This is an epilog"
    )
    parser.add_argument('huc', help='NHD flow line ShapeFile path', type=str)
    parser.add_argument('flowlines', help='NHD flow line ShapeFile path', type=str)
    parser.add_argument('flowareas', help='NHD flow areas ShapeFile path', type=str)
    parser.add_argument('slope', help='Slope raster path', type=str)
    parser.add_argument('dem', help='DEM raster path', type=str)
    parser.add_argument('hillshade', help='Hillshade raster path', type=str)
    parser.add_argument('catchments', help='NHD Catchment polygons path', type=str)
    parser.add_argument('output_dir', help='Folder where output VBET project will be created', type=str)
    parser.add_argument('--reach_codes', help='Comma delimited reach codes (FCode) to retain when filtering features. Omitting this option retains all features.', type=str)
    # parser.add_argument('--max_slope', help='Maximum slope to be considered', type=float, default=12)
    # parser.add_argument('--max_hand', help='Maximum HAND to be considered', type=float, default=50)
    # parser.add_argument('--min_hole_area', help='Minimum hole retained in valley bottom (sq m)', type=float, default=50000)
    parser.add_argument('--meta', help='riverscapes project metadata as comma separated key=value pairs', type=str)
    parser.add_argument('--verbose', help='(optional) a little extra logging ', action='store_true', default=False)
    parser.add_argument('--debug', help='Add debug tools for tracing things like memory usage at a performance cost.', action='store_true', default=False)

    args = dotenv.parse_args_env(parser)

    # make sure the output folder exists
    safe_makedirs(args.output_dir)

    # Initiate the log file
    log = Logger('VBET')
    log.setup(logPath=os.path.join(args.output_dir, 'vbet.log'), verbose=args.verbose)
    log.title('Riverscapes VBET For HUC: {}'.format(args.huc))

    meta = parse_metadata(args.meta)

    json_transform = json.dumps({"Slope": 1, "HAND": 2, "Channel": 3, "Flow Areas": 4, 'Slope MID': 5, "Slope LARGE": 6, "HAND MID": 7, "HAND LARGE": 8})
    reach_codes = args.reach_codes.split(',') if args.reach_codes else None

    try:
        if args.debug is True:
            from rscommons.debug import ThreadRun
            memfile = os.path.join(args.output_dir, 'vbet_mem.log')
            retcode, max_obj = ThreadRun(vbet, memfile, args.huc, args.flowlines, args.flowareas, args.slope, json_transform, args.dem, args.hillshade, args.catchments, args.output_dir, reach_codes, meta)
            log.debug('Return code: {}, [Max process usage] {}'.format(retcode, max_obj))

        else:
            vbet(args.huc, args.flowlines, args.flowareas, args.slope, json_transform, args.dem, args.hillshade, args.catchments, args.output_dir, reach_codes, meta)

    except Exception as e:
        log.error(e)
        traceback.print_exc(file=sys.stdout)
        sys.exit(1)

    sys.exit(0)


if __name__ == '__main__':
    main()
