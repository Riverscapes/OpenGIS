""" Build a BRAT project by segmenting a river network to a specified
    length and then extract the input values required to run the
    BRAT model for each reach segment from various GIS layers.

    Philip Bailey
    30 May 2019

    Returns:
        [type]: [description]
"""
import argparse
import os
import sys
import uuid
import traceback
import datetime
import time
from typing import List, Dict
from osgeo import ogr
from rscommons import GeopackageLayer
from rscommons.vector_ops import copy_feature_class
from rscommons import Logger, initGDALOGRErrors, RSLayer, RSProject, ModelConfig, dotenv
from rscommons.util import parse_metadata
from rscommons.build_network import build_network
from rscommons.database import create_database, SQLiteCon
from rscommons.reach_geometry import reach_geometry
from sqlbrat.utils.vegetation_summary import vegetation_summary
from sqlbrat.utils.conflict_attributes import conflict_attributes
from sqlbrat.__version__ import __version__

Path = str

initGDALOGRErrors()

cfg = ModelConfig('http://xml.riverscapes.xyz/Projects/XSD/V1/BRAT.xsd', __version__)

LayerTypes = {
    'DEM': RSLayer('NED 10m DEM', 'DEM', 'DEM', 'inputs/dem.tif'),
    'SLOPE': RSLayer('Slope Raster', 'SLOPE', 'Raster', 'inputs/slope.tif'),
    'HILLSHADE': RSLayer('DEM Hillshade', 'HILLSHADE', 'Raster', 'inputs/dem_hillshade.tif'),
    'EXVEG': RSLayer('Existing Vegetation', 'EXVEG', 'Raster', 'inputs/existing_veg.tif'),
    'HISTVEG': RSLayer('Historic Vegetation', 'HISTVEG', 'Raster', 'inputs/historic_veg.tif'),
    'INPUTS': RSLayer('Confinement', 'INPUTS', 'Geopackage', 'inputs/inputs.gpkg', {
        'FLOWLINES': RSLayer('Segmented Flowlines', 'FLOWLINES', 'Vector', 'flowlines'),
        'FLOW_AREA': RSLayer('NHD Flow Area', 'FLOW_AREA', 'Vector', 'flowareas'),
        'WATERBODIES': RSLayer('NHD Waterbody', 'WATERBODIES', 'Vector', 'waterbodies'),
        'VALLEY_BOTTOM': RSLayer('Valley Bottom', 'VALLEY_BOTTOM', 'Vector', 'valley_bottom'),
        'ROADS': RSLayer('Roads', 'ROADS', 'Vector', 'roads'),
        'RAIL': RSLayer('Rail', 'RAIL', 'Vector', 'rail'),
        'CANALS': RSLayer('Canals', 'CANALS', 'Vector', 'canals')
    }),
    'INTERMEDIATES': RSLayer('Intermediates', 'INTERMEDIATES', 'Geopackage', 'intermediates/intermediates.gpkg', {}),
    'OUTPUTS': RSLayer('BRAT', 'OUTPUTS', 'Geopackage', 'outputs/brat.gpkg', {
        'BRAT_GEOMETRY': RSLayer('BRAT Geometry', 'BRAT_GEOMETRY', 'Vector', 'ReachGeometry'),
        'BRAT': RSLayer('BRAT', 'BRAT_RESULTS', 'Vector', 'vwReaches')
    })
}


def brat_build(huc: int, flowlines: Path, dem: Path, slope: Path, hillshade: Path,
               existing_veg: Path, historical_veg: Path, output_folder: Path,
               streamside_buffer: float, riparian_buffer: float,
               reach_codes: List[str], canal_codes: List[str], peren_codes: List[str],
               flow_areas: Path, waterbodies: Path, max_waterbody: float,
               valley_bottom: Path, roads: Path, rail: Path, canals: Path, ownership: Path,
               elevation_buffer: float, meta: Dict[str, str]):
    """Build a BRAT project by segmenting a reach network and copying
    all the necessary layers into the resultant BRAT project

    Arguments:
        huc {str} -- Watershed identifier
        flowlines {str} -- Path to the raw, original polyline flowline ShapeFile
        flow_areas {str} -- Path to the polygon ShapeFile that contains large river outlines
        waterbodies {str} -- Path to the polygon ShapeFile containing water bodies
        max_length {float} -- Maximum allowable flow line segment after segmentation
        min_length {float} -- Shortest allowable flow line segment after segmentation
        dem {str} -- Path to the DEM raster for the watershed
        slope {str} -- Path to the slope raster
        hillshade {str} -- Path to the DEM hillshade raster
        existing_veg {str} -- Path to the excisting vegetation raster
        historical_veg {str} -- Path to the historical vegetation raster
        output_folder {str} -- Output folder where the BRAT project will get created
        streamside_buffer {float} -- Streamside vegetation buffer (meters)
        riparian_buffer {float} -- Riparian vegetation buffer (meters)
        intermittent {bool} -- True to keep intermittent streams. False discard them.
        ephemeral {bool} -- True to keep ephemeral streams. False to discard them.
        max_waterbody {float} -- Area (sqm) of largest waterbody to be retained.
        valley_bottom {str} -- Path to valley bottom polygon layer.
        roads {str} -- Path to polyline roads ShapeFile
        rail {str} -- Path to polyline railway ShapeFile
        canals {str} -- Path to polyline canals ShapeFile
        ownership {str} -- Path to land ownership polygon ShapeFile
        elevation_buffer {float} -- Distance to buffer DEM when sampling elevation
        meta (Dict[str,str]): dictionary of riverscapes metadata key: value pairs
    """

    log = Logger("BRAT Build")
    log.info('HUC: {}'.format(huc))
    log.info('EPSG: {}'.format(cfg.OUTPUT_EPSG))

    project, _realization, proj_nodes = create_project(huc, output_folder)

    # Incorporate project metadata to the riverscapes project
    if meta is not None:
        project.add_metadata(meta)

    log.info('Adding input rasters to project')
    _dem_raster_path_node, dem_raster_path = project.add_project_raster(proj_nodes['Inputs'], LayerTypes['DEM'], dem)
    _existing_path_node, prj_existing_path = project.add_project_raster(proj_nodes['Inputs'], LayerTypes['EXVEG'], existing_veg)
    _historic_path_node, prj_historic_path = project.add_project_raster(proj_nodes['Inputs'], LayerTypes['HISTVEG'], historical_veg)
    project.add_project_raster(proj_nodes['Inputs'], LayerTypes['HILLSHADE'], hillshade)
    project.add_project_raster(proj_nodes['Inputs'], LayerTypes['SLOPE'], slope)
    project.add_project_geopackage(proj_nodes['Inputs'], LayerTypes['INPUTS'])
    project.add_project_geopackage(proj_nodes['Outputs'], LayerTypes['OUTPUTS'])

    inputs_gpkg_path = os.path.join(output_folder, LayerTypes['INPUTS'].rel_path)
    intermediates_gpkg_path = os.path.join(output_folder, LayerTypes['INTERMEDIATES'].rel_path)
    outputs_gpkg_path = os.path.join(output_folder, LayerTypes['OUTPUTS'].rel_path)

    # Make sure we're starting with empty/fresh geopackages
    GeopackageLayer.delete(inputs_gpkg_path)
    GeopackageLayer.delete(intermediates_gpkg_path)
    GeopackageLayer.delete(outputs_gpkg_path)

    # Copy all the original vectors to the inputs geopackage. This will ensure on same spatial reference
    source_layers = {
        'FLOWLINES': flowlines,
        'FLOW_AREA': flow_areas,
        'WATERBODIES': waterbodies,
        'VALLEY_BOTTOM': valley_bottom,
        'ROADS': roads,
        'RAIL': rail,
        'CANALS': canals
    }

    input_layers = {}
    for input_key, rslayer in LayerTypes['INPUTS'].sub_layers.items():
        input_layers[input_key] = os.path.join(inputs_gpkg_path, rslayer.rel_path)
        copy_feature_class(source_layers[input_key], input_layers[input_key], cfg.OUTPUT_EPSG)

    # Create the output feature class fields. Only those listed here will get copied from the source
    with GeopackageLayer(outputs_gpkg_path, layer_name=LayerTypes['OUTPUTS'].sub_layers['BRAT_GEOMETRY'].rel_path, delete_dataset=True) as out_lyr:
        out_lyr.create_layer(ogr.wkbMultiLineString, epsg=cfg.OUTPUT_EPSG, options=['FID=ReachID'], fields={
            'WatershedID': ogr.OFTString,
            'FCode': ogr.OFTInteger,
            'TotDASqKm': ogr.OFTReal,
            'GNIS_Name': ogr.OFTString,
            'NHDPlusID': ogr.OFTReal
        })

    metadata = {
        'BRAT_Build_DateTime': datetime.datetime.now().isoformat(),
        'Streamside_Buffer': streamside_buffer,
        'Riparian_Buffer': riparian_buffer,
        'Reach_Codes': reach_codes,
        'Canal_Codes': canal_codes,
        'Max_Waterbody': max_waterbody,
        'Elevation_Buffer': elevation_buffer
    }

    # Execute the SQL to create the lookup tables in the output geopackage
    watershed_name = create_database(huc, outputs_gpkg_path, metadata, cfg.OUTPUT_EPSG, os.path.join(os.path.abspath(os.path.dirname(__file__)), '..', 'database', 'brat_schema.sql'))
    project.add_metadata({'Watershed': watershed_name})

    # Copy the reaches into the output feature class layer, filtering by reach codes
    reach_geometry_path = os.path.join(outputs_gpkg_path, LayerTypes['OUTPUTS'].sub_layers['BRAT_GEOMETRY'].rel_path)
    build_network(input_layers['FLOWLINES'], input_layers['FLOW_AREA'], reach_geometry_path, waterbodies_path=input_layers['WATERBODIES'], epsg=cfg.OUTPUT_EPSG, reach_codes=reach_codes, create_layer=False)

    with SQLiteCon(outputs_gpkg_path) as database:
        # Data preparation SQL statements to handle any weird attributes
        database.curs.execute('INSERT INTO ReachAttributes (ReachID, Orig_DA, iGeo_DA, ReachCode, WatershedID, StreamName) SELECT ReachID, TotDASqKm, TotDASqKm, FCode, WatershedID, GNIS_NAME FROM ReachGeometry')
        database.curs.execute('UPDATE ReachAttributes SET IsPeren = 1 WHERE (ReachCode IN ({}))'.format(','.join(peren_codes)))
        database.curs.execute('UPDATE ReachAttributes SET iGeo_DA = 0 WHERE iGeo_DA IS NULL')

        # Register vwReaches as a feature layer as well as its geometry column
        database.curs.execute("""INSERT INTO gpkg_contents (table_name, data_type, identifier, min_x, min_y, max_x, max_y, srs_id)
            SELECT 'vwReaches', data_type, 'Reaches', min_x, min_y, max_x, max_y, srs_id FROM gpkg_contents WHERE table_name = 'ReachGeometry'""")

        database.curs.execute("""INSERT INTO gpkg_geometry_columns (table_name, column_name, geometry_type_name, srs_id, z, m)
            SELECT 'vwReaches', column_name, geometry_type_name, srs_id, z, m FROM gpkg_geometry_columns WHERE table_name = 'ReachGeometry'""")

        database.conn.commit()

    # Calculate the geophysical properties slope, min and max elevations
    reach_geometry(reach_geometry_path, dem_raster_path, elevation_buffer)

    # Calculate the conflict attributes ready for conservation
    conflict_attributes(outputs_gpkg_path, reach_geometry_path,
                        input_layers['VALLEY_BOTTOM'], input_layers['ROADS'], input_layers['RAIL'], input_layers['CANALS'],
                        ownership, 30, 5, cfg.OUTPUT_EPSG, canal_codes, intermediates_gpkg_path)

    # Calculate the vegetation cell counts for each epoch and buffer
    for label, veg_raster in [('Existing Veg', prj_existing_path), ('Historical Veg', prj_historic_path)]:
        for buffer in [streamside_buffer, riparian_buffer]:
            vegetation_summary(outputs_gpkg_path, '{} {}m'.format(label, buffer), veg_raster, buffer)

    log.info('BRAT build completed successfully.')


def create_project(huc, output_dir):
    """ Create riverscapes project XML

    Args:
        huc (str): Watershed HUC code
        output_dir (str): Full absolute path to output folder

    Returns:
        tuple: (project XML object, realization node, dictionary of other nodes)
    """

    project_name = 'BRAT for HUC {}'.format(huc)
    project = RSProject(cfg, output_dir)
    project.create(project_name, 'BRAT')

    project.add_metadata({
        'HUC{}'.format(len(huc)): str(huc),
        'HUC': str(huc),
        'BRATBuildVersion': cfg.version,
        'BRATBuildTimestamp': str(int(time.time()))
    })

    realizations = project.XMLBuilder.add_sub_element(project.XMLBuilder.root, 'Realizations')
    realization = project.XMLBuilder.add_sub_element(realizations, 'BRAT', None, {
        'id': 'BRAT1',
        'dateCreated': datetime.datetime.now().isoformat(),
        'guid': str(uuid.uuid1()),
        'productVersion': cfg.version
    })

    proj_nodes = {
        'Name': project.XMLBuilder.add_sub_element(realization, 'Name', project_name),
        'Inputs': project.XMLBuilder.add_sub_element(realization, 'Inputs'),
        'Intermediates': project.XMLBuilder.add_sub_element(realization, 'Intermediates'),
        'Outputs': project.XMLBuilder.add_sub_element(realization, 'Outputs')
    }

    project.XMLBuilder.write()
    return project, realization, proj_nodes


def main():
    """ Main BRAT Build routine
    """

    parser = argparse.ArgumentParser(
        description='Build the inputs for an eventual brat_run:',
        # epilog="This is an epilog"
    )
    parser.add_argument('huc', help='huc input', type=str)

    parser.add_argument('dem', help='dem input', type=str)
    parser.add_argument('slope', help='slope input', type=str)
    parser.add_argument('hillshade', help='hillshade input', type=str)

    parser.add_argument('flowlines', help='flowlines input', type=str)
    parser.add_argument('existing_veg', help='existing_veg input', type=str)
    parser.add_argument('historical_veg', help='historical_veg input', type=str)

    parser.add_argument('valley_bottom', help='Valley bottom shapeFile', type=str)
    parser.add_argument('roads', help='Roads shapeFile', type=str)
    parser.add_argument('rail', help='Railways shapefile', type=str)
    parser.add_argument('canals', help='Canals shapefile', type=str)
    parser.add_argument('ownership', help='Ownership shapefile', type=str)

    parser.add_argument('streamside_buffer', help='streamside_buffer input', type=float)
    parser.add_argument('riparian_buffer', help='riparian_buffer input', type=float)
    parser.add_argument('elevation_buffer', help='elevation_buffer input', type=float)

    parser.add_argument('output_folder', help='output_folder input', type=str)

    parser.add_argument('--reach_codes', help='Comma delimited reach codes (FCode) to retain when filtering features. Omitting this option retains all features.', type=str)
    parser.add_argument('--canal_codes', help='Comma delimited reach codes (FCode) representing canals. Omitting this option retains all features.', type=str)
    parser.add_argument('--peren_codes', help='Comma delimited reach codes (FCode) representing perennial features', type=str)
    parser.add_argument('--flow_areas', help='(optional) path to the flow area polygon feature class containing artificial paths', type=str)
    parser.add_argument('--waterbodies', help='(optional) waterbodies input', type=str)
    parser.add_argument('--max_waterbody', help='(optional) maximum size of small waterbody artificial flows to be retained', type=float)

    parser.add_argument('--meta', help='riverscapes project metadata as comma separated key=value pairs', type=str)
    parser.add_argument('--verbose', help='(optional) a little extra logging ', action='store_true', default=False)
    parser.add_argument('--debug', help='(optional) more output about things like memory usage. There is a performance cost', action='store_true', default=False)

    # Substitute patterns for environment varaibles
    args = dotenv.parse_args_env(parser)

    reach_codes = args.reach_codes.split(',') if args.reach_codes else None
    canal_codes = args.canal_codes.split(',') if args.canal_codes else None
    peren_codes = args.peren_codes.split(',') if args.peren_codes else None

    # Initiate the log file
    log = Logger("BRAT Build")
    log.setup(logPath=os.path.join(args.output_folder, "brat_build.log"), verbose=args.verbose)
    log.title('BRAT Build Tool For HUC: {}'.format(args.huc))

    meta = parse_metadata(args.meta)

    try:
        if args.debug is True:
            from rscommons.debug import ThreadRun
            memfile = os.path.join(args.output_folder, 'brat_build_memusage.log')
            retcode, max_obj = ThreadRun(brat_build, memfile,
                                         args.huc, args.flowlines, args.dem, args.slope, args.hillshade,
                                         args.existing_veg, args.historical_veg, args.output_folder,
                                         args.streamside_buffer, args.riparian_buffer,
                                         reach_codes, canal_codes, peren_codes,
                                         args.flow_areas, args.waterbodies, args.max_waterbody,
                                         args.valley_bottom, args.roads, args.rail, args.canals, args.ownership,
                                         args.elevation_buffer,
                                         meta
                                         )
            log.debug('Return code: {}, [Max process usage] {}'.format(retcode, max_obj))
        else:
            brat_build(
                args.huc, args.flowlines, args.dem, args.slope, args.hillshade,
                args.existing_veg, args.historical_veg, args.output_folder,
                args.streamside_buffer, args.riparian_buffer,
                reach_codes, canal_codes, peren_codes,
                args.flow_areas, args.waterbodies, args.max_waterbody,
                args.valley_bottom, args.roads, args.rail, args.canals, args.ownership,
                args.elevation_buffer,
                meta
            )

    except Exception as ex:
        log.error(ex)
        traceback.print_exc(file=sys.stdout)
        sys.exit(1)

    sys.exit(0)


if __name__ == '__main__':
    main()
