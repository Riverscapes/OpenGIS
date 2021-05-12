#!/usr/bin/env python3
# Name:     Riverscapes Context
#
# Purpose:  Build a riverscapes context project by downloading and preparing
#           commonly used data layers for several riverscapes tools.
#
# Author:   Philip Bailey
#
# Date:     9 Sep 2019
# -------------------------------------------------------------------------------
import argparse
import sys
import os
import glob
import json
import traceback
import uuid
import datetime
import time
from typing import Dict
from osgeo import ogr

from rscommons import Logger, RSProject, RSLayer, ModelConfig, dotenv, initGDALOGRErrors, Timer
from rscommons.util import safe_makedirs, safe_remove_dir, parse_metadata, pretty_duration
from rscommons.clean_nhd_data import clean_nhd_data
from rscommons.clean_ntd_data import clean_ntd_data
from rscommons.raster_warp import raster_warp, raster_vrt_stitch
from rscommons.download_dem import download_dem, verify_areas
from rscommons.science_base import download_shapefile_collection, get_ntd_urls, us_states
from rscommons.geographic_raster import gdal_dem_geographic
from rscommons.download_hand import download_hand
from rscommons.raster_buffer_stats import raster_buffer_stats2
from rscommons.vector_ops import get_geometry_unary_union, copy_feature_class, buffer_by_field
from rscommons.prism import calculate_bankfull_width

from rscontext.rs_segmentation import rs_segmentation
from rscontext.flow_accumulation import flow_accumulation, flow_accum_to_drainage_area
from rscontext.clip_ownership import clip_ownership
from rscontext.filter_ecoregions import filter_ecoregions
from rscontext.rs_context_report import RSContextReport
from rscontext.vegetation import clip_vegetation
from rscontext.bankfull import bankfull_buffer, bankfull_nhd_area
from rscontext.__version__ import __version__

initGDALOGRErrors()

cfg = ModelConfig('http://xml.riverscapes.xyz/Projects/XSD/V1/RSContext.xsd', __version__)

# These are the Prism BIL types we expect
PrismTypes = ['PPT', 'TMEAN', 'TMIN', 'TMAX', 'TDMEAN', 'VPDMIN', 'VPDMAX']

LayerTypes = {
    # key: (name, id, tag, relpath)
    'DEM': RSLayer('NED 10m DEM', 'DEM', 'DEM', 'topography/dem.tif'),
    'FA': RSLayer('Flow Accumulation', 'FA', 'Raster', 'topography/flow_accum.tif'),
    'DA': RSLayer('Drainage Area', 'DA', 'Raster', 'topography/drainarea_sqkm.tif'),
    'HILLSHADE': RSLayer('DEM Hillshade', 'HILLSHADE', 'Raster', 'topography/dem_hillshade.tif'),
    'SLOPE': RSLayer('Slope', 'SLOPE', 'Raster', 'topography/slope.tif'),
    'HAND': RSLayer('Height above nearest drainage', 'HAND', 'Raster', 'topography/hand.tif'),
    # Veg Layers
    'EXVEG': RSLayer('Existing Vegetation', 'EXVEG', 'Raster', 'vegetation/existing_veg.tif'),
    'HISTVEG': RSLayer('Historic Vegetation', 'HISTVEG', 'Raster', 'vegetation/historic_veg.tif'),
    # Inputs

    'OWNERSHIP': RSLayer('Ownership', 'Ownership', 'Vector', 'ownership/ownership.shp'),
    'FAIR_MARKET': RSLayer('Fair Market Value', 'FAIRMARKETVALUE', 'Raster', 'ownership/fair_market_value.tif'),
    'ECOREGIONS': RSLayer('Ecoregions', 'Ecoregions', 'Vector', 'inputs/ecoregions.shp'),

    # NHD Geopackage Layers
    'HYDROLOGY': RSLayer('Hydrology', 'NHD', 'Geopackage', 'hydrology/hydrology.gpkg', {
        'NETWORK': RSLayer('NHD Flowlines', 'NETWORK', 'Vector', 'network'),
        'BUFFEREDCLIP100': RSLayer('Buffered Clip Shape 100m', 'BUFFERED_CLIP100', 'Vector', 'buffered_clip100m'),
        'BUFFEREDCLIP500': RSLayer('Buffered Clip Shape 500m', 'BUFFERED_CLIP500', 'Vector', 'buffered_clip500m'),
        'NETWORK300M': RSLayer('NHD Flowlines Segmented 300m', 'NETWORK300M', 'Vector', 'network_300m'),
        'NETWORK300M_INTERSECTION': RSLayer('NHD Flowlines intersected with road, rail and ownership', 'NETWORK300M_INTERSECTION', 'Vector', 'network_intersected'),
        'NETWORK300M_CROSSINGS': RSLayer('NHD Flowlines intersected with road, rail and ownership, segmented to 300m', 'NETWORK300MCROSSINGS', 'Vector', 'network_intersected_300m'),
        'BANKFULL_CHANNEL': RSLayer('Estimated Bankfull Channel', 'BANKFULL_CHANNEL', 'Vector', 'bankfull'),
        # 'COMPOSITE_CHANNEL_AREA': RSLayer('Bankfull and NHD Area', 'COMPOSITE_CHANNEL_AREA', 'Vector', 'bankfull_nhd_area')
    }),

    # Prism Layers
    'PPT': RSLayer('Precipitation', 'Precip', 'Raster', 'climate/precipitation.tif'),
    'TMEAN': RSLayer('Mean Temperature', 'MeanTemp', 'Raster', 'climate/mean_temp.tif'),
    'TMIN': RSLayer('Minimum Temperature', 'MinTemp', 'Raster', 'climate/min_temp.tif'),
    'TMAX': RSLayer('Maximum Temperature', 'MaxTemp', 'Raster', 'climate/max_temp.tif'),
    'TDMEAN': RSLayer('Mean Dew Point Temperature', 'MeanDew', 'Raster', 'climate/mean_dew_temp.tif'),
    'VPDMIN': RSLayer('Minimum Vapor Pressure Deficit', 'MinVap', 'Raster', 'climate/min_vapor_pressure.tif'),
    'VPDMAX': RSLayer('Maximum Vapor Pressure Deficit', 'MaxVap', 'Raster', 'climate/max_vapor_pressure.tif'),
    'REPORT': RSLayer('RSContext Report', 'REPORT', 'HTMLFile', 'rs_context.html')
}

SEGMENTATION = {
    'Max': 300,
    'Min': 50
}


def rs_context(huc, existing_veg, historic_veg, ownership, fair_market, ecoregions, prism_folder, output_folder, download_folder, scratch_dir, parallel, force_download, meta: Dict[str, str]):
    """

    Download riverscapes context layers for the specified HUC and organize them as a Riverscapes project

    :param huc: Eight, 10 or 12 digit HUC identification number
    :param existing_veg: Path to the existing vegetation conditions raster
    :param historic_veg: Path to the historical vegetation conditions raster
    :param ownership: Path to the national land ownership Shapefile
    :param output_folder: Output location for the riverscapes context project
    :param download_folder: Temporary folder where downloads are cached. This can be shared between rs_context processes
    :param force_download: If false then downloads can be skipped if the files already exist
    :param prism_folder: folder containing PRISM rasters in *.bil format
    :param meta (Dict[str,str]): dictionary of riverscapes metadata key: value pairs
    :return:
    """
    rsc_timer = time.time()
    log = Logger("RS Context")
    log.info('Starting RSContext v.{}'.format(cfg.version))

    try:
        int(huc)
    except ValueError:
        raise Exception('Invalid HUC identifier "{}". Must be an integer'.format(huc))

    if not (len(huc) in [4, 8, 10, 12]):
        raise Exception('Invalid HUC identifier. Must be 4, 8, 10 or 12 digit integer')

    safe_makedirs(output_folder)
    safe_makedirs(download_folder)

    # We need a temporary folder for slope rasters, Stitching inputs, intermeditary products, etc.
    scratch_dem_folder = os.path.join(scratch_dir, 'rs_context', huc)
    safe_makedirs(scratch_dem_folder)

    project, realization = create_project(huc, output_folder)
    hydrology_gpkg_path = os.path.join(output_folder, LayerTypes['HYDROLOGY'].rel_path)

    dem_node, dem_raster = project.add_project_raster(realization, LayerTypes['DEM'])
    _node, hill_raster = project.add_project_raster(realization, LayerTypes['HILLSHADE'])
    _node, flow_accum = project.add_project_raster(realization, LayerTypes['FA'])
    _node, drain_area = project.add_project_raster(realization, LayerTypes['DA'])
    hand_node, hand_raster = project.add_project_raster(realization, LayerTypes['HAND'])
    _node, slope_raster = project.add_project_raster(realization, LayerTypes['SLOPE'])
    _node, existing_clip = project.add_project_raster(realization, LayerTypes['EXVEG'])
    _node, historic_clip = project.add_project_raster(realization, LayerTypes['HISTVEG'])
    _node, fair_market_clip = project.add_project_raster(realization, LayerTypes['FAIR_MARKET'])

    # Download the four digit NHD archive containing the flow lines and watershed boundaries
    log.info('Processing NHD')

    # Incorporate project metadata to the riverscapes project
    if meta is not None:
        project.add_metadata(meta)

    nhd_download_folder = os.path.join(download_folder, 'nhd', huc[:4])
    nhd_unzip_folder = os.path.join(scratch_dir, 'nhd', huc[:4])

    nhd, db_path, huc_name, nhd_url = clean_nhd_data(huc, nhd_download_folder, nhd_unzip_folder, os.path.join(output_folder, 'hydrology'), cfg.OUTPUT_EPSG, False)

    # Clean up the unzipped files. We won't need them again
    if parallel:
        safe_remove_dir(nhd_unzip_folder)
    project.add_metadata({'Watershed': huc_name})
    boundary = 'WBDHU{}'.format(len(huc))

    # For coarser rasters than the DEM we need to buffer our clip polygon to include enough pixels
    # This shouldn't be too much more data because these are usually integer rasters that are much lower res.
    buffered_clip_path100 = os.path.join(hydrology_gpkg_path, LayerTypes['HYDROLOGY'].sub_layers['BUFFEREDCLIP100'].rel_path)
    copy_feature_class(nhd[boundary], buffered_clip_path100, epsg=cfg.OUTPUT_EPSG, buffer=100)

    buffered_clip_path500 = os.path.join(hydrology_gpkg_path, LayerTypes['HYDROLOGY'].sub_layers['BUFFEREDCLIP500'].rel_path)
    copy_feature_class(nhd[boundary], buffered_clip_path500, epsg=cfg.OUTPUT_EPSG, buffer=500)

    # PRISM climate rasters
    mean_annual_precip = None
    bil_files = glob.glob(os.path.join(prism_folder, '**', '*.bil'))
    if (len(bil_files) == 0):
        raise Exception('Could not find any .bil files in the prism folder')
    for ptype in PrismTypes:
        try:
            # Next should always be guarded
            source_raster_path = next(x for x in bil_files if ptype.lower() in os.path.basename(x).lower())
        except StopIteration:
            raise Exception('Could not find .bil file corresponding to "{}"'.format(ptype))
        _node, project_raster_path = project.add_project_raster(realization, LayerTypes[ptype])
        raster_warp(source_raster_path, project_raster_path, cfg.OUTPUT_EPSG, buffered_clip_path500, {"cutlineBlend": 1})

        # Use the mean annual precipitation to calculate bankfull width
        if ptype.lower() == 'ppt':
            polygon = get_geometry_unary_union(nhd[boundary], epsg=cfg.OUTPUT_EPSG)
            mean_annual_precip = raster_buffer_stats2({1: polygon}, project_raster_path)[1]['Mean']
            log.info('Mean annual precipitation for HUC {} is {} mm'.format(huc, mean_annual_precip))
            project.add_metadata({'mean_annual_precipitation_mm': str(mean_annual_precip)})

            calculate_bankfull_width(nhd['NHDFlowline'], mean_annual_precip)

    # Add the DB record to the Project XML
    db_lyr = RSLayer('NHD Tables', 'NHDTABLES', 'SQLiteDB', os.path.relpath(db_path, output_folder))
    sqlite_el = project.add_dataset(realization, db_path, db_lyr, 'SQLiteDB')
    project.add_metadata({'origin_url': nhd_url}, sqlite_el)

    # Add any results to project XML
    for name, file_path in nhd.items():
        lyr_obj = RSLayer(name, name, 'Vector', os.path.relpath(file_path, output_folder))
        vector_nod, _fpath = project.add_project_vector(realization, lyr_obj)
        project.add_metadata({'origin_url': nhd_url}, vector_nod)

    states = get_nhd_states(nhd[boundary])

    # Download the NTD archive containing roads and rail
    log.info('Processing NTD')
    ntd_raw = {}
    ntd_unzip_folders = []
    ntd_urls = get_ntd_urls(states)
    for state, ntd_url in ntd_urls.items():
        ntd_download_folder = os.path.join(download_folder, 'ntd', state.lower())
        ntd_unzip_folder = os.path.join(scratch_dir, 'ntd', state.lower(), 'unzipped')  # a little awkward but I need a folder for this and this was the best name I could find
        ntd_raw[state] = download_shapefile_collection(ntd_url, ntd_download_folder, ntd_unzip_folder, force_download)
        ntd_unzip_folders.append(ntd_unzip_folder)

    ntd_clean = clean_ntd_data(ntd_raw, nhd['NHDFlowline'], nhd[boundary], os.path.join(output_folder, 'transportation'), cfg.OUTPUT_EPSG)

    # clean up the NTD Unzip folder. We won't need it again
    if parallel:
        for unzip_path in ntd_unzip_folders:
            safe_remove_dir(unzip_path)

    # Write transportation layers to project file
    log.info('Write transportation layers to project file')

    # Add any results to project XML
    for name, file_path in ntd_clean.items():
        lyr_obj = RSLayer(name, name, 'Vector', os.path.relpath(file_path, output_folder))
        ntd_node, _fpath = project.add_project_vector(realization, lyr_obj)
        project.add_metadata({**ntd_urls}, ntd_node)

    # Download the HAND raster
    huc6 = huc[0:6]
    hand_download_folder = os.path.join(download_folder, 'hand')
    _hpath, hand_url = download_hand(huc6, cfg.OUTPUT_EPSG, hand_download_folder, nhd[boundary], hand_raster, warp_options={"cutlineBlend": 1})
    project.add_metadata({'origin_url': hand_url}, hand_node)

    # download contributing DEM rasters, mosaic and reproject into compressed GeoTIF
    ned_download_folder = os.path.join(download_folder, 'ned')
    ned_unzip_folder = os.path.join(scratch_dir, 'ned')
    dem_rasters, urls = download_dem(nhd[boundary], cfg.OUTPUT_EPSG, 0.01, ned_download_folder, ned_unzip_folder, force_download)

    need_dem_rebuild = force_download or not os.path.exists(dem_raster)
    if need_dem_rebuild:
        raster_vrt_stitch(dem_rasters, dem_raster, cfg.OUTPUT_EPSG, clip=nhd[boundary], warp_options={"cutlineBlend": 1})
        verify_areas(dem_raster, nhd[boundary])

    # Calculate slope rasters seperately and then stitch them
    slope_parts = []
    hillshade_parts = []

    need_slope_build = need_dem_rebuild or not os.path.isfile(slope_raster)
    need_hs_build = need_dem_rebuild or not os.path.isfile(hill_raster)

    project.add_metadata({
        'num_rasters': str(len(urls)),
        'origin_urls': json.dumps(urls)
    }, dem_node)

    for dem_r in dem_rasters:
        slope_part_path = os.path.join(scratch_dem_folder, 'SLOPE__' + os.path.basename(dem_r).split('.')[0] + '.tif')
        hs_part_path = os.path.join(scratch_dem_folder, 'HS__' + os.path.basename(dem_r).split('.')[0] + '.tif')
        slope_parts.append(slope_part_path)
        hillshade_parts.append(hs_part_path)

        if force_download or need_dem_rebuild or not os.path.exists(slope_part_path):
            gdal_dem_geographic(dem_r, slope_part_path, 'slope')
            need_slope_build = True

        if force_download or need_dem_rebuild or not os.path.exists(hs_part_path):
            gdal_dem_geographic(dem_r, hs_part_path, 'hillshade')
            need_hs_build = True

    if need_slope_build:
        raster_vrt_stitch(slope_parts, slope_raster, cfg.OUTPUT_EPSG, clip=nhd[boundary], clean=parallel, warp_options={"cutlineBlend": 1})
        verify_areas(slope_raster, nhd[boundary])
    else:
        log.info('Skipping slope build because nothing has changed.')

    if need_hs_build:
        raster_vrt_stitch(hillshade_parts, hill_raster, cfg.OUTPUT_EPSG, clip=nhd[boundary], clean=parallel, warp_options={"cutlineBlend": 1})
        verify_areas(hill_raster, nhd[boundary])
    else:
        log.info('Skipping hillshade build because nothing has changed.')

    # Remove the unzipped rasters. We won't need them anymore
    if parallel:
        safe_remove_dir(ned_unzip_folder)

    # Calculate flow accumulation raster based on the DEM
    log.info('Running flow accumulation and converting to drainage area.')
    flow_accumulation(dem_raster, flow_accum, dinfinity=False, pitfill=True)
    flow_accum_to_drainage_area(flow_accum, drain_area)

    # Clip and re-project the existing and historic vegetation
    log.info('Processing existing and historic vegetation rasters.')
    clip_vegetation(buffered_clip_path100, existing_veg, existing_clip, historic_veg, historic_clip, cfg.OUTPUT_EPSG)

    log.info('Process the Fair Market Value Raster.')
    raster_warp(fair_market, fair_market_clip, cfg.OUTPUT_EPSG, clip=buffered_clip_path500, warp_options={"cutlineBlend": 1})

    # Clip the landownership Shapefile to a 10km buffer around the watershed boundary
    own_path = os.path.join(output_folder, LayerTypes['OWNERSHIP'].rel_path)
    project.add_dataset(realization, own_path, LayerTypes['OWNERSHIP'], 'Vector')
    clip_ownership(nhd[boundary], ownership, own_path, cfg.OUTPUT_EPSG, 10000)

    #######################################################
    # Segmentation
    #######################################################

    # For now let's just make a copy of the NHD FLowlines
    tmr = Timer()
    rs_segmentation(
        nhd['NHDFlowline'],
        ntd_clean['Roads'],
        ntd_clean['Rail'],
        own_path,
        hydrology_gpkg_path,
        SEGMENTATION['Max'],
        SEGMENTATION['Min'],
        huc
    )
    log.debug('Segmentation done in {:.1f} seconds'.format(tmr.ellapsed()))
    project.add_project_geopackage(realization, LayerTypes['HYDROLOGY'])

    # Add Bankfull Buffer Polygons
    bankfull_path = os.path.join(hydrology_gpkg_path, LayerTypes['HYDROLOGY'].sub_layers['BANKFULL_CHANNEL'].rel_path)
    bankfull_buffer(os.path.join(hydrology_gpkg_path, LayerTypes['HYDROLOGY'].sub_layers['NETWORK'].rel_path), cfg.OUTPUT_EPSG, bankfull_path, )

    # TODO Add nhd/bankfull union when merge feature classes in vector.ops works with Geopackage layers
    # bankfull_nhd_path = os.path.join(hydrology_gpkg_path, LayerTypes['HYDROLOGY'].sub_layers['COMPOSITE_CHANNEL_AREA'].rel_path)
    # clip_path = os.path.join(hydrology_gpkg_path, LayerTypes['HYDROLOGY'].sub_layers['BUFFEREDCLIP500'].rel_path)
    # bankfull_nhd_area(bankfull_path, nhd['NHDArea'], clip_path, cfg.OUTPUT_EPSG, hydrology_gpkg_path, LayerTypes['HYDROLOGY'].sub_layers['COMPOSITE_CHANNEL_AREA'].rel_path)

    # Filter the ecoregions Shapefile to only include attributes that intersect with our HUC
    eco_path = os.path.join(output_folder, 'ecoregions', 'ecoregions.shp')
    project.add_dataset(realization, eco_path, LayerTypes['ECOREGIONS'], 'Vector')
    filter_ecoregions(nhd[boundary], ecoregions, eco_path, cfg.OUTPUT_EPSG, 10000)

    report_path = os.path.join(project.project_dir, LayerTypes['REPORT'].rel_path)
    project.add_report(realization, LayerTypes['REPORT'], replace=True)

    ellapsed_time = time.time() - rsc_timer
    project.add_metadata({"ProcTimeS": "{:.2f}".format(ellapsed_time)})
    project.add_metadata({"ProcTimeHuman": pretty_duration(ellapsed_time)})

    report = RSContextReport(report_path, project, output_folder)
    report.write()

    log.info('Process completed successfully.')
    return {
        'DEM': dem_raster,
        'Slope': slope_raster,
        'ExistingVeg': existing_veg,
        'HistoricVeg': historic_veg,
        'NHD': nhd
    }


def create_project(huc, output_dir):

    project_name = 'Riverscapes Context for HUC {}'.format(huc)
    project = RSProject(cfg, output_dir)
    project.create(project_name, 'RSContext')

    project.add_metadata({'HUC{}'.format(len(huc)): str(huc)})
    project.add_metadata({'HUC': str(huc)})

    realizations = project.XMLBuilder.add_sub_element(project.XMLBuilder.root, 'Realizations')
    realization = project.XMLBuilder.add_sub_element(realizations, 'RSContext', None, {
        'id': 'RSContext1',
        'dateCreated': datetime.datetime.now().isoformat(),
        'guid': str(uuid.uuid4()),
        'productVersion': cfg.version
    })
    project.XMLBuilder.add_sub_element(realization, 'Name', project_name)

    project.XMLBuilder.write()
    return project, realization


def get_nhd_states(inpath):
    """
    Gets the list of US States that an NHD HUC encompasses

    This relies on the watershed boundary ShapeFile having a column called
    'States' that stores a comma separated list of state abbreviations
    such as 'OR,WA'. A dcitionary is used to retrieve the full names.
    :param inpath: Path to the watershed boundary ShapeFile
    :return: List of full US state names that the watershed touches (.e.g. Oregon)
    """
    log = Logger('RS Context')

    driver = ogr.GetDriverByName("ESRI Shapefile")
    data_source = driver.Open(inpath, 0)
    layer = data_source.GetLayer()
    states = []
    for feature in layer:
        value = feature.GetField('States')
        [states.append(us_states[acronym]) for acronym in value.split(',')]

    data_source = None

    if 'Canada' in states:
        if len(states) == 1:
            log.error('HUC is entirely within Canada. No DEMs will be available.')
        else:
            log.warning('HUC is partially in Canada. Certain data will only be available for US portion.')

    log.info('HUC intersects {} state(s): {}'.format(len(states), ', '.join(states)))
    return list(dict.fromkeys(states))


def main():
    parser = argparse.ArgumentParser(
        description='Riverscapes Context Tool',
        # epilog="This is an epilog"
    )
    parser.add_argument('huc', help='HUC identifier', type=str)
    parser.add_argument('existing', help='National existing vegetation raster', type=str)
    parser.add_argument('historic', help='National historic vegetation raster', type=str)
    parser.add_argument('ownership', help='National land ownership shapefile', type=str)
    parser.add_argument('fairmarket', help='National fair market value raster', type=str)
    parser.add_argument('ecoregions', help='National EcoRegions shapefile', type=str)
    parser.add_argument('prism', help='Folder containing PRISM rasters in BIL format', type=str)
    parser.add_argument('output', help='Path to the output folder', type=str)
    parser.add_argument('download', help='Temporary folder for downloading data. Different HUCs may share this', type=str)
    parser.add_argument('--force', help='(optional) download existing files ', action='store_true', default=False)
    parser.add_argument('--parallel', help='(optional) for running multiple instances of this at the same time', action='store_true', default=False)
    parser.add_argument('--temp_folder', help='(optional) cache folder for downloading files ', type=str)
    parser.add_argument('--meta', help='riverscapes project metadata as comma separated key=value pairs', type=str)
    parser.add_argument('--verbose', help='(optional) a little extra logging ', action='store_true', default=False)
    parser.add_argument('--debug', help='(optional) more output about things like memory usage. There is a performance cost', action='store_true', default=False)

    args = dotenv.parse_args_env(parser)

    # Initiate the log file
    log = Logger("RS Context")
    log.setup(logPath=os.path.join(args.output, "rs_context.log"), verbose=args.verbose)
    log.title('Riverscapes Context For HUC: {}'.format(args.huc))

    log.info('HUC: {}'.format(args.huc))
    log.info('EPSG: {}'.format(cfg.OUTPUT_EPSG))
    log.info('Existing veg: {}'.format(args.existing))
    log.info('Historical veg: {}'.format(args.historic))
    log.info('Ownership: {}'.format(args.ownership))
    log.info('Fair Market Value Raster: {}'.format(args.fairmarket))
    log.info('Output folder: {}'.format(args.output))
    log.info('Download folder: {}'.format(args.download))
    log.info('Force download: {}'.format(args.force))

    # This is a general place for unzipping downloaded files and other temporary work.
    # We use GUIDS to make it specific to a particular run of the tool to avoid unzip collisions
    parallel_code = "-" + str(uuid.uuid4()) if args.parallel is True else ""
    scratch_dir = args.temp_folder if args.temp_folder else os.path.join(args.download, 'scratch', 'rs_context{}'.format(parallel_code))
    safe_makedirs(scratch_dir)

    meta = parse_metadata(args.meta)

    try:

        if args.debug is True:
            from rscommons.debug import ThreadRun
            memfile = os.path.join(args.output, 'rs_context_memusage.log')
            retcode, max_obj = ThreadRun(rs_context, memfile, args.huc, args.existing, args.historic, args.ownership, args.fairmarket, args.ecoregions, args.prism, args.output, args.download, scratch_dir, args.parallel, args.force, meta)
            log.debug('Return code: {}, [Max process usage] {}'.format(retcode, max_obj))
        else:
            rs_context(args.huc, args.existing, args.historic, args.ownership, args.fairmarket, args.ecoregions, args.prism, args.output, args.download, scratch_dir, args.parallel, args.force, meta)

    except Exception as e:
        log.error(e)
        traceback.print_exc(file=sys.stdout)
        # Cleaning up the scratch folder is essential
        safe_remove_dir(scratch_dir)
        sys.exit(1)

    # Cleaning up the scratch folder is essential
    safe_remove_dir(scratch_dir)
    sys.exit(0)


if __name__ == '__main__':
    main()
