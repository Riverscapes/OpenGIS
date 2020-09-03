# Name:     Segment Network
#
# Purpose:  This script segments a polyline ShapeFile into reaches of
#           user defined length. The script attempts to segment the lines
#           at a desired length but does not split lines if it would
#           result ina line less than the specified minimum length.
#
#           Note that the interval and minimum are in the linear units
#           of the input ShpaeFile (i.e. best used with projected data).
#
# Author:   Philip Bailey
#
# Date:     15 May 2019
# -------------------------------------------------------------------------------
import argparse
import os
import sys
import traceback
from osgeo import ogr, osr
from rscommons import Logger, ProgressBar, initGDALOGRErrors, dotenv
from shapely.geometry import MultiLineString, LineString, Point, shape
from rscommons.shapefile import create_field, get_transform_from_epsg, get_utm_zone_epsg

initGDALOGRErrors()


class SegmentFeature:

    def __init__(self, feature, transform):
        self.name = feature.GetField('GNIS_NAME')
        georef = feature.GetGeometryRef()
        self.fid = feature.GetFID()
        geotype = georef.GetGeometryType()
        self.FCode = feature.GetField('FCode')
        self.TotDASqKm = feature.GetField('TotDASqKm')
        self.NHDPlusID = feature.GetField('NHDPlusID')

        if geotype not in [ogr.wkbLineStringZM, ogr.wkbLineString, ogr.wkbLineString25D, ogr.wkbLineStringM]:
            raise Exception('Multipart geometry in the original ShapeFile')

        pts = []

        pts = georef.GetPoints()

        self.start = ogr.Geometry(ogr.wkbPoint)
        self.start.AddPoint(*pts[0])

        self.end = ogr.Geometry(ogr.wkbPoint)
        self.end.AddPoint(*pts[-1])

        georef.Transform(transform)
        self.length_m = georef.Length()


def segment_network(inpath, outpath, interval, minimum, tolerance=0.1):
    """
    Chop the lines in a polyline feature class at the specified interval unless
    this would create a line less than the minimum in which case the line is not segmented.
    :param inpath: Original network feature class
    :param outpath: Output segmented network feature class
    :param interval: Distance at which to segment each line feature (map units)
    :param minimum: Minimum length below which lines are not segmented (map units)
    :param tolerance: Distance below which points are assumed to coincide (map units)
    :return: None
    """

    log = Logger('Segment Network')

    if os.path.isfile(outpath):
        log.info('Skipping network segmentation because output exists {}'.format(outpath))
        return None

    if interval <= 0:
        log.info('Skipping segmentation.')
    else:
        log.info('Segmenting network to {}m, with minimum feature length of {}m'.format(interval, minimum))
        log.info('Segmenting network from {0}'.format(inpath))

    driver = ogr.GetDriverByName('ESRI Shapefile')

    # Get the input NHD flow lines layer
    inDataSource = driver.Open(inpath, 0)
    inLayer = inDataSource.GetLayer()

    log.info('Input feature count {:,}'.format(inLayer.GetFeatureCount()))

    # Get the closest EPSG possible to calculate length
    in_spatial_ref = inLayer.GetSpatialRef()
    extent_poly = ogr.Geometry(ogr.wkbPolygon)
    extent_centroid = extent_poly.Centroid()
    utm_epsg = get_utm_zone_epsg(extent_centroid.GetX())
    transform_ref, transform = get_transform_from_epsg(in_spatial_ref, utm_epsg)

    # IN order to get accurate lengths we are going to need to project into some coordinate system
    transform_back = osr.CoordinateTransformation(transform_ref, in_spatial_ref)

    # Omit pipelines with FCode 428**
    attfilter = 'FCode < 42800 OR FCode > 42899'
    inLayer.SetAttributeFilter(attfilter)
    log.info('Filtering out pipelines ({})'.format(attfilter))

    # Remove output shapefile if it already exists
    if os.path.exists(outpath):
        driver.DeleteDataSource(outpath)

    # Make sure the output folder exists
    resultsFolder = os.path.dirname(outpath)
    if not os.path.isdir(resultsFolder):
        os.mkdir(resultsFolder)

    # Create the output shapefile
    outDataSource = driver.CreateDataSource(outpath)
    outLayer = outDataSource.CreateLayer('Segmented', inLayer.GetSpatialRef(), geom_type=ogr.wkbMultiLineString)
    create_field(outLayer, 'GNIS_NAME', ogr.OFTString)
    create_field(outLayer, 'FCode', ogr.OFTString)
    create_field(outLayer, 'TotDASqKm', ogr.OFTReal)
    create_field(outLayer, 'NHDPlusID', ogr.OFTReal)
    create_field(outLayer, 'ReachID', ogr.OFTInteger)

    # Get the output Layer's Feature Definition
    outLayerDefn = outLayer.GetLayerDefn()

    # Retrieve all input features keeping track of which ones have GNIS names or not
    namedFeatures = {}
    allFeatures = []
    junctions = []
    log.info('Loading {:,} original features.'.format(inLayer.GetFeatureCount()))

    progbarLoad = ProgressBar(inLayer.GetFeatureCount(), 50, "Loading Network")
    counterLoad = 0

    for inFeature in inLayer:
        counterLoad += 1
        progbarLoad.update(counterLoad)

        # Store relevant items as a tuple:
        # (name, FID, StartPt, EndPt, Length, FCode)
        sFeat = SegmentFeature(inFeature, transform)

        # Add the end points of all lines to a single list
        junctions.extend([sFeat.start, sFeat.end])

        if not sFeat.name or len(sFeat.name) < 1 or interval <= 0:
            # Add features without a GNIS name to list. Also add to list if not segmenting
            allFeatures.append(sFeat)
        else:
            # Build separate lists for each unique GNIS name
            if sFeat.name not in namedFeatures:
                namedFeatures[sFeat.name] = [sFeat]
            else:
                namedFeatures[sFeat.name].append(sFeat)
    progbarLoad.finish()

    # Loop over all features with the same GNIS name.
    # Only merge them if they meet at a junction where no other lines meet.
    log.info('Merging simple features with the same GNIS name...')
    for name, features in namedFeatures.items():
        log.debug('   {} x{}'.format(name.encode('utf-8'), len(features)))
        allFeatures.extend(features)

    log.info('{:,} features after merging. Starting segmentation...'.format(len(allFeatures)))

    # Segment the features at the desired interval
    rid = 0
    log.info('Segmenting Network...')
    progbar = ProgressBar(inLayer.GetFeatureCount(), 50, "Segmenting")
    counter = 0

    for origFeat in allFeatures:
        counter += 1
        progbar.update(counter)

        oldFeat = inLayer.GetFeature(origFeat.fid)
        oldGeom = oldFeat.GetGeometryRef()
        #  Anything that produces reach shorter than the minimum just gets added. Also just add features if not segmenting
        if origFeat.length_m < (interval + minimum) or interval <= 0:
            newOGRFeat = ogr.Feature(outLayerDefn)
            # Set the attributes using the values from the delimited text file
            newOGRFeat.SetField("GNIS_NAME", origFeat.name)
            newOGRFeat.SetField("ReachID", rid)
            newOGRFeat.SetField("FCode", origFeat.FCode)
            newOGRFeat.SetField("TotDASqKm", origFeat.TotDASqKm)
            newOGRFeat.SetField("NHDPlusID", origFeat.NHDPlusID)
            newOGRFeat.SetGeometry(oldGeom)
            outLayer.CreateFeature(newOGRFeat)
            rid += 1
        else:
            # From here on out we use shapely and project to UTM. We'll transform back before writing to disk.
            newGeom = oldGeom.Clone()
            newGeom.Transform(transform)
            remaining = LineString(newGeom.GetPoints())
            while remaining and remaining.length >= (interval + minimum):
                part1shply, part2shply = cut(remaining, interval)
                remaining = part2shply

                newOGRFeat = ogr.Feature(outLayerDefn)
                # Set the attributes using the values from the delimited text file
                newOGRFeat.SetField("GNIS_NAME", origFeat.name)
                newOGRFeat.SetField("ReachID", rid)
                newOGRFeat.SetField("FCode", origFeat.FCode)
                newOGRFeat.SetField("TotDASqKm", origFeat.TotDASqKm)
                newOGRFeat.SetField("NHDPlusID", origFeat.NHDPlusID)
                geo = ogr.CreateGeometryFromWkt(part1shply.wkt)
                geo.Transform(transform_back)
                newOGRFeat.SetGeometry(geo)
                outLayer.CreateFeature(newOGRFeat)
                rid += 1

            # Add any remaining line to outGeometries
            if remaining:
                newOGRFeat = ogr.Feature(outLayerDefn)
                # Set the attributes using the values from the delimited text file
                newOGRFeat.SetField("GNIS_NAME", origFeat.name)
                newOGRFeat.SetField("ReachID", rid)
                newOGRFeat.SetField("FCode", origFeat.FCode)
                newOGRFeat.SetField("TotDASqKm", origFeat.TotDASqKm)
                newOGRFeat.SetField("NHDPlusID", origFeat.NHDPlusID)
                geo = ogr.CreateGeometryFromWkt(remaining.wkt)
                geo.Transform(transform_back)
                newOGRFeat.SetGeometry(geo)
                outLayer.CreateFeature(newOGRFeat)
                rid += 1

    progbarLoad.finish()

    log.info(('{:,} features written to {:}'.format(outLayer.GetFeatureCount(), outpath)))
    log.info('Process completed successfully.')

    inDataSource = None
    outDataSource = None


def cut(line, distance):
    """
    Cuts a line in two at a distance from its starting point
    :param line: line geometry
    :param distance: distance at which to cut the liner
    :return: List where the first item is the first part of the line
    and the second is the remaining part of the line (if there is any)
    """

    if distance <= 0.0 or distance >= line.length:
        return (line)

    for i, p in enumerate(line.coords):
        pd = line.project(Point(p))
        if pd == distance:
            return (
                LineString(line.coords[:i + 1]),
                LineString(line.coords[i:])
            )
        if pd > distance:
            cp = line.interpolate(distance)
            return (
                LineString(line.coords[:i] + [cp]),
                LineString([cp] + line.coords[i:])
            )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('network', help='Input stream network ShapeFile path', type=str)
    parser.add_argument('segmented', help='Output segmented network ShapeFile path', type=str)
    parser.add_argument('interval', help='Interval distance at which to segment the network', type=float)
    parser.add_argument('minimum', help='Minimum feature length in the segmented network', type=float)
    parser.add_argument('--tolerance', help='Tolerance for considering points are coincident', type=float, default=0.1)
    parser.add_argument('--verbose', help='(optional) verbose logging mode', action='store_true', default=False)

    args = dotenv.parse_args_env(parser)

    # Initiate the log file
    logg = Logger("Segment Network")
    logfile = os.path.join(os.path.dirname(args.segmented), "segment_network.log")
    logg.setup(logPath=logfile, verbose=args.verbose)

    if os.path.isfile(args.segmented):
        logg.info('Deleting existing output {}'.format(args.segmented))
        shpDriver = ogr.GetDriverByName("ESRI Shapefile")
        shpDriver.DeleteDataSource(args.segmented)

    try:
        segment_network(args.network, args.segmented, args.interval, args.minimum, args.tolerance)

    except Exception as e:
        logg.error(e)
        traceback.print_exc(file=sys.stdout)
        sys.exit(1)

    sys.exit(0)


if __name__ == '__main__':
    main()
