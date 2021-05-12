""" Name: Generate HAND

    Purpose: Generate network specific HAND raster for vbet using TauDEM

    Author: Kelly Whitehead

    Date: Feb 5 2021
    """

from __future__ import annotations
import os
import time
from typing import List
import subprocess
from osgeo import gdal
from rscommons.util import pretty_duration
from rscommons import Logger, ProgressBar, VectorBase

NCORES = os.environ['TAUDEM_CORES'] if 'TAUDEM_CORES' in os.environ else '2'


def create_hand_raster(dem: str, flowlines: str, working_dir: str, out_hand: str):
    """Generate HAND raster for a watershed

    Args:
        dem (Path): geotiff of dem raster
        flowlines_gpkg (GPKG): geopackage where flowline network is saved
        flowlines_layer (str): layer name in geopackage for flowline network (e.g. vbet_network)
        working_dir (Path): temporary directory to store intermedite files
        out_hand (file): path and name of geotiff hand output

    Returns:
        [type]: [description]
    """
    log = Logger("HAND")
    start_time = time.time()
    log.info(f"Generating HAND for {dem} and {flowlines} using {working_dir}")

    # Format Paths
    path_pitfill = os.path.join(working_dir, "pitfill.tif")
    path_ang = os.path.join(working_dir, "dinfflowdir_ang.tif")
    path_slp = os.path.join(working_dir, "dinfflowdir_slp.tif")
    path_rasterized_flowline = os.path.join(working_dir, "rasterized_flowline.tif")

    # PitRemove
    log.info("Filling DEM pits")
    pitfill_status = run_subprocess(working_dir, ["mpiexec", "-n", NCORES, "pitremove", "-z", dem, "-fel", path_pitfill])
    if pitfill_status != 0 or not os.path.isfile(path_pitfill):
        raise Exception('TauDEM: pitfill failed')

    # Flow Dir
    log.info("Finding flow direction")
    dinfflowdir_status = run_subprocess(working_dir, ["mpiexec", "-n", NCORES, "dinfflowdir", "-fel", path_pitfill, "-ang", path_ang, "-slp", path_slp])
    if dinfflowdir_status != 0 or not os.path.isfile(path_ang):
        raise Exception('TauDEM: dinfflowdir failed')

    # rasterize flowlines
    log.info("Rasterizing flowlines")
    hand_rasterize(flowlines, dem, path_rasterized_flowline)

    # generate hand
    log.info("Generating HAND")
    dinfdistdown_status = run_subprocess(working_dir, ["mpiexec", "-n", NCORES, "dinfdistdown", "-ang", path_ang, "-fel", path_pitfill, "-src", path_rasterized_flowline, "-dd", out_hand, "-m", "ave", "v"])
    if dinfdistdown_status != 0 or not os.path.isfile(out_hand):
        raise Exception('TauDEM: dinfdistdown failed')

    # Fin
    log.info(f"Generated HAND Raster {out_hand}")

    ellapsed_time = time.time() - start_time
    log.info("HAND process complete in {}".format(ellapsed_time))

    return out_hand


def hand_rasterize(in_lyr_path: str, template_dem_path: str, out_raster_path: str):
    # log = Logger('hand_rasterize')
    ds_path, lyr_path = VectorBase.path_sorter(in_lyr_path)

    g = gdal.Open(template_dem_path)
    geo_t = g.GetGeoTransform()
    width, height = g.RasterXSize, g.RasterYSize
    xmin = min(geo_t[0], geo_t[0] + width * geo_t[1])
    xmax = max(geo_t[0], geo_t[0] + width * geo_t[1])
    ymin = min(geo_t[3], geo_t[3] + geo_t[-1] * height)
    ymax = max(geo_t[3], geo_t[3] + geo_t[-1] * height)
    # Close our dataset
    g = None

    progbar = ProgressBar(100, 50, "Rasterizing for HAND")

    def poly_progress(progress, _msg, _data):
        progbar.update(int(progress * 100))

    # https://gdal.org/programs/gdal_rasterize.html
    # https://gdal.org/python/osgeo.gdal-module.html#RasterizeOptions
    gdal.Rasterize(
        out_raster_path,
        ds_path,
        layers=[lyr_path],
        height=height,
        width=width,
        burnValues=1, outputType=gdal.GDT_CFloat32,
        creationOptions=['COMPRESS=LZW'],
        # outputBounds --- assigned output bounds: [minx, miny, maxx, maxy]
        outputBounds=[xmin, ymin, xmax, ymax],
        callback=poly_progress
    )
    progbar.finish()

    # Rasterize the features (roads, rail etc) and calculate a raster of Euclidean distance from these features
    progbar.update(0)


def run_subprocess(cwd: str, cmd: List[str]):

    log = Logger("Subprocess")
    log.info('Running command: {}'.format(' '.join(cmd)))
    start_time = time.time()
    # Realtime logging from subprocess
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd)
    # Here we print the lines in real time but we will also log them afterwords
    # replace '' with b'' for Python 3
    for output in iter(process.stdout.readline, b''):
        for line in output.decode('utf-8').split('\n'):
            if len(line) > 0:
                log.info(line)

    for errout in iter(process.stderr.readline, b''):
        for line in errout.decode('utf-8').split('\n'):
            if len(line) > 0:
                log.error(line)

    retcode = process.poll()
    if retcode is not None and retcode > 0:
        log.error('Process returned with code {}'.format(retcode))

    ellapsed_time = time.time() - start_time
    log.info('Command completed in {}'.format(pretty_duration(ellapsed_time)))

    return retcode
