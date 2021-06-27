""" Run the BRAT model on an existing BRAT project that was built
    using the brat_build.py script. The model calculates existing
    and historic dam capacities as well as conservation and conflict.

    Philip Bailey
    30 May 2019

    Returns:
        None: None
"""
import os
import sys
import traceback
import argparse
import time
import datetime
from osgeo import ogr
from rscommons import Logger, RSLayer, RSProject, ModelConfig, dotenv
from rscommons.database import update_database, store_metadata, set_reach_fields_null, SQLiteCon
from sqlbrat.utils.vegetation_suitability import vegetation_suitability, output_vegetation_raster
from sqlbrat.utils.vegetation_fis import vegetation_fis
from sqlbrat.utils.combined_fis import combined_fis
from sqlbrat.utils.hydrology import hydrology
from sqlbrat.utils.land_use import land_use
from sqlbrat.utils.conservation import conservation
from sqlbrat.brat_report import BratReport
from sqlbrat.__version__ import __version__

cfg = ModelConfig('http://xml.riverscapes.xyz/Projects/XSD/V1/BRAT.xsd', __version__)

# Dictionary of fields that this process outputs, keyed by ShapeFile data type
output_fields = {
    ogr.OFTInteger: ['RiskID', 'LimitationID', 'OpportunityID'],
    ogr.OFTReal: ['iVeg100EX', 'iVeg_30EX', 'iVeg100HPE', 'iVeg_30HPE', 'iPC_LU',
                  'iPC_VLowLU', 'iPC_LowLU', 'iPC_ModLU', 'iPC_HighLU', 'iHyd_QLow',
                  'iHyd_Q2', 'iHyd_SPLow', 'iHyd_SP2', 'oVC_HPE', 'oVC_EX', 'oCC_HPE',
                  'mCC_HPE_CT', 'oCC_EX', 'mCC_EX_CT', 'mCC_HisDep']
}

LayerTypes = {
    'EXVEG_SUIT': RSLayer('Existing Vegetation', 'EXVEG_SUIT', 'Raster', 'intermediates/existing_veg_suitability.tif'),
    'HISTVEG_SUIT': RSLayer('Historic Vegetation', 'HISTVEG_SUIT', 'Raster', 'intermediates/historic_veg_suitability.tif'),
    'BRAT_RUN_REPORT': RSLayer('BRAT Report', 'BRAT_RUN_REPORT', 'HTMLFile', 'outputs/brat.html')
}

Epochs = [
    # (epoch, prefix, LayerType, OrigId)
    ('Existing', 'EX', 'EXVEG_SUIT', 'EXVEG'),
    ('Historic', 'HPE', 'HISTVEG_SUIT', 'HISTVEG')
]


def brat_run(project_root, csv_dir):
    """Run the BRAT model and calculat dam capacity
    as well as conservation and restoration.

    Arguments:
        database {str} -- Path to existing BRAT SQLite database
        csv_dir {str} -- Path to the directory containing the BRAT lookup CSV data files
        shapefile {str} -- Path to the existing BRAT reach segment ShapeFile
        project_root {str} -- (Optional) path to Riverscapes project directory
    """

    log = Logger('BRAT Run')
    log.info('Starting BRAT run')

    project = RSProject(cfg, project_root)

    project.add_metadata({
        'BRATRunVersion': cfg.version,
        'BRATRunTimestamp': str(int(time.time()))
    })

    realizations = project.XMLBuilder.find('Realizations').findall('BRAT')
    if len(realizations) != 1:
        raise Exception('Could not find a valid realization inside the existing brat project')

    # Fetch some XML nodes we'll need to aleter
    r_node = realizations[0]
    input_node = r_node.find('Inputs')
    intermediate_node = r_node.find('Intermediates')
    outputs_node = r_node.find('Outputs')

    # Get the filepaths for the DB and shapefile
    gpkg_path = os.path.join(project.project_dir, r_node.find('Outputs/Geopackage[@id="OUTPUTS"]/Path').text)

    if not os.path.isfile(gpkg_path):
        raise Exception('BRAT geopackage file missing at {}. You must run Brat Build first.'.format(gpkg_path))

    # Update any of the lookup tables we need
    update_database(gpkg_path, csv_dir)

    # Store the BRAT Run date time to the database (for reporting)
    store_metadata(gpkg_path, 'BRAT_Run_DateTime', datetime.datetime.now().isoformat())

    watershed, max_drainage_area, ecoregion = get_watershed_info(gpkg_path)

    # Set database output columns to NULL before processing (note omission of string lookup fields from view)
    set_reach_fields_null(gpkg_path, output_fields[ogr.OFTReal])
    set_reach_fields_null(gpkg_path, output_fields[ogr.OFTInteger])

    # Calculate the low and high flow using regional discharge equations
    hydrology(gpkg_path, 'Low', watershed)
    hydrology(gpkg_path, '2', watershed)

    # Calculate the vegetation and combined FIS for the existing and historical vegetation epochs
    for epoch, prefix, ltype, orig_id in Epochs:

        # Calculate the vegetation suitability for each buffer
        [vegetation_suitability(gpkg_path, buffer, prefix, ecoregion) for buffer in get_stream_buffers(gpkg_path)]

        # Run the vegetation and then combined FIS for this epoch
        vegetation_fis(gpkg_path, epoch, prefix)
        combined_fis(gpkg_path, epoch, prefix, max_drainage_area)

        orig_raster = os.path.join(project.project_dir, input_node.find('Raster[@id="{}"]/Path'.format(orig_id)).text)
        _veg_suit_raster_node, veg_suit_raster = project.add_project_raster(intermediate_node, LayerTypes[ltype], None, True)
        output_vegetation_raster(gpkg_path, orig_raster, veg_suit_raster, epoch, prefix, ecoregion)

    # Calculate departure from historical conditions
    with SQLiteCon(gpkg_path) as database:
        log.info('Calculating departure from historic conditions')
        database.curs.execute('UPDATE ReachAttributes SET mCC_HisDep = mCC_HPE_CT - mCC_EX_CT WHERE (mCC_EX_CT IS NOT NULL) AND (mCC_HPE_CT IS NOT NULL)')
        database.conn.commit()

    # Land use intesity, conservation and restoration
    land_use(gpkg_path, 100.0)
    conservation(gpkg_path)

    report_path = os.path.join(project.project_dir, LayerTypes['BRAT_RUN_REPORT'].rel_path)
    project.add_report(outputs_node, LayerTypes['BRAT_RUN_REPORT'], replace=True)

    report = BratReport(gpkg_path, report_path, project)
    report.write()

    log.info('BRAT run complete')


def get_watershed_info(gpkg_path):
    """Query a BRAT database and get information about
    the watershed being run. Assumes that all watersheds
    except the one being run have been deleted.

    Arguments:
        database {str} -- Path to the BRAT SQLite database

    Returns:
        [tuple] -- WatershedID, max drainage area, EcoregionID with which
        the watershed is associated.
    """

    with SQLiteCon(gpkg_path) as database:
        # Retrieve watershed information being careful that there might be records for all watersheds (so join to reaches)
        database.curs.execute('SELECT W.WatershedID, W.MaxDrainage, W.EcoregionID FROM Watersheds W INNER JOIN ReachAttributes RA on W.WatershedID = RA.WatershedID LIMIT 1')
        row = database.curs.fetchone()
        watershed = row['WatershedID']
        max_drainage = row['MaxDrainage']
        ecoregion = row['EcoregionID']

    log = Logger('BRAT Run')

    if not watershed:
        raise Exception('Missing watershed in BRAT datatabase {}'.format(database))

    if not max_drainage:
        log.warning('Missing max drainage for watershed {}'.format(watershed))

    if not ecoregion:
        raise Exception('Missing ecoregion for watershed {}'.format(watershed))

    return watershed, max_drainage, ecoregion


def get_stream_buffers(gpkg_path):
    """Get the list of buffers used to sample the vegetation.
    Assumes that the vegetation has already been sample and that
    the streamside and riparian buffers are the only values in
    the ReachVegetation database table.

    Arguments:
        database {str} -- Path to the BRAT database

    Returns:
        [list] -- all discrete vegetation buffers
    """

    with SQLiteCon(gpkg_path) as database:
        database.curs.execute('SELECT Buffer FROM ReachVegetation GROUP BY Buffer')
        return [row['Buffer'] for row in database.curs.fetchall()]


def main():
    """ Main BRAT Run
    """
    parser = argparse.ArgumentParser(
        description='Run brat against a pre-existing sqlite db:',
        # epilog="This is an epilog"
    )
    parser.add_argument('project', help='Riverscapes project folder or project xml file', type=str, default=None)
    parser.add_argument('--csv_dir', help='(optional) directory where we can find updated lookup tables', action='store_true', default=False)
    parser.add_argument('--verbose', help='(optional) a little extra logging ', action='store_true', default=False)
    parser.add_argument('--debug', help='(optional) more output about things like memory usage. There is a performance cost', action='store_true', default=False)

    args = dotenv.parse_args_env(parser)

    if os.path.isfile(args.project):
        logpath = os.path.dirname(args.project)
    elif os.path.isdir(args.project):
        logpath = args.project
    else:
        raise Exception('You must supply a valid path to a riverscapes project')

    log = Logger('BRAT Run')
    log.setup(logPath=os.path.join(logpath, "brat_run.log"), verbose=args.verbose)
    log.title('BRAT Run Tool')

    try:
        if args.debug is True:
            from rscommons.debug import ThreadRun
            memfile = os.path.join(logpath, 'brat_run_memusage.log')
            retcode, max_obj = ThreadRun(brat_run, memfile, args.project, args.csv_dir)
            log.debug('Return code: {}, [Max process usage] {}'.format(retcode, max_obj))
        else:
            brat_run(args.project, args.csv_dir)

    except Exception as e:
        log.error(e)
        traceback.print_exc(file=sys.stdout)
        sys.exit(1)

    sys.exit(0)


if __name__ == '__main__':
    main()
