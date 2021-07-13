# Name:     ShapeFile helper methods
#
# Purpose:  Miscellaneous routines for common tasks related to ShapeFiles
#
# Author:   Philip Bailey
#
# Date:     30 May 2019
# -------------------------------------------------------------------------------
import os
import sys
import json
import subprocess
import math
from functools import reduce
from osgeo import gdal
from osgeo import ogr
from osgeo import osr
from copy import copy
from shapely.wkb import loads as wkbload
from shapely.ops import unary_union
from shapely.geometry import shape, mapping, Point, MultiPoint, LineString, MultiLineString, GeometryCollection, Polygon, MultiPolygon
from rscommons import Logger, Raster, ProgressBar
from rscommons.util import safe_makedirs, sizeof_fmt, get_obj_size

NO_UI = os.environ.get('NO_UI') is not None

LINE_TYPES = [
    ogr.wkbLineString, ogr.wkbLineString25D, ogr.wkbLineStringM, ogr.wkbLineStringZM,
    ogr.wkbMultiLineString, ogr.wkbMultiLineString25D, ogr.wkbMultiLineStringM, ogr.wkbMultiLineStringZM
]
POLY_TYPES = [
    ogr.wkbPolygon, ogr.wkbPolygon25D, ogr.wkbPolygonM, ogr.wkbPolygonZM,
    ogr.wkbMultiPolygon, ogr.wkbMultiPolygon25D, ogr.wkbMultiPolygonM, ogr.wkbMultiPolygonZM
]


def get_transform_from_epsg(inSpatialRef, epsg):
    """Transform a spatial ref using an epsg code provided

    This is done explicitly and includes a GetAxisMappingStrategy check to
    account for GDAL3's projection differences.

    Args:
        inSpatialRef ([type]): [description]
        epsg ([type]): [description]

    Returns:
        [type]: [description]
    """
    log = Logger('get_transform_from_epsg')
    outSpatialRef = osr.SpatialReference()
    outSpatialRef.ImportFromEPSG(int(epsg))

    # https://github.com/OSGeo/gdal/issues/1546
    outSpatialRef.SetAxisMappingStrategy(inSpatialRef.GetAxisMappingStrategy())

    log.info('Input spatial reference is {0}'.format(inSpatialRef.ExportToProj4()))
    log.info('Output spatial reference is {0}'.format(outSpatialRef.ExportToProj4()))
    transform = osr.CoordinateTransformation(inSpatialRef, outSpatialRef)
    return outSpatialRef, transform


def get_srs_debug(spatial_ref):
    order = spatial_ref.GetAxisMappingStrategy()
    order_str = str(order)
    if order == 0:
        order_str = 'OAMS_TRADITIONAL_GIS_ORDER'
    elif order == 1:
        order_str = 'OAMS_AUTHORITY_COMPLIANT'
    elif order == 2:
        order_str = 'OAMS_CUSTOM'

    return [spatial_ref.ExportToProj4(), order_str]


def delete_shapefile(delete_path):

    driver = ogr.GetDriverByName("ESRI Shapefile")

    if os.path.exists(delete_path):
        driver.DeleteDataSource(delete_path)


def print_geom_size(logger, geom_obj):
    try:
        size_str = sizeof_fmt(get_obj_size(geom_obj.wkb))
        logger.debug('Byte Size of output object: {} Type: {} IsValid: {} Length: {} Area: {}'.format(size_str, geom_obj.type, geom_obj.is_valid, geom_obj.length, geom_obj.area))
    except Exception as e:
        logger.debug(e)
        logger.debug('Byte Size of output object could not be determined')


def create_field(layer, field, field_type=ogr.OFTReal):
    """
    Remove and then re-add a field to a feature class
    :param layer: Feature class that will receive the attribute field
    :param field: Name of the attribute field to be created
    :param log:
    :return: name of the field created (same as function argument)
    """
    log = Logger('Shapefile')

    if not field or len(field) < 1 or len(field) > 10:
        raise Exception('Attempting to create field with invalid field name "{}".'.format(field))

    # Delete output column from network ShapeFile if it exists and then recreate it
    networkDef = layer.GetLayerDefn()
    for fieldidx in range(0, networkDef.GetFieldCount()):
        if networkDef.GetFieldDefn(fieldidx).GetName() == field:
            log.info('Deleting existing output field "{}" in network ShapeFile.'.format(field))
            layer.DeleteField(fieldidx)
            break

    log.info('Creating output field "{}" in network ShapeFile.'.format(field))
    field_def = ogr.FieldDefn(field, field_type)

    if field_type == ogr.OFTReal:
        field_def.SetPrecision(10)
        field_def.SetWidth(18)

    layer.CreateField(field_def)

    return field


def verify_field(layer, field):
    """
    Case insensitive search for field in layer. Throw exception if it doesn't exist
    :param layer: Layer in which to search for field
    :param field: The field name that will be searched for
    :return: The actual field name (with correct case) that exists in layer
    """

    layerDef = layer.GetLayerDefn()
    for i in range(layerDef.GetFieldCount()):
        actual_name = layerDef.GetFieldDefn(i).GetName()
        if field.lower() == actual_name.lower():
            return actual_name

    raise Exception('Missing field {} in {}'.format(field, layer.GetName()))


def get_geometry_union(inpath, epsg, attribute_filter=None):
    """
    TODO: Remove this method and replace all references to the get_geometry_unary_union method below
    Load all features from a ShapeFile and union them together into a single geometry
    :param inpath: Path to a ShapeFile
    :param epsg: Desired output spatial reference
    :return: Single Shapely geometry of all unioned features
    """

    log = Logger('Shapefile')

    driver = ogr.GetDriverByName("ESRI Shapefile")
    data_source = driver.Open(inpath, 0)
    layer = data_source.GetLayer()
    in_spatial_ref = layer.GetSpatialRef()

    if attribute_filter:
        layer.SetAttributeFilter(attribute_filter)

    _out_spatial_ref, transform = get_transform_from_epsg(in_spatial_ref, epsg)

    geom = None
    progbar = ProgressBar(layer.GetFeatureCount(), 50, "Unioning features")
    counter = 0
    for feature in layer:
        counter += 1
        progbar.update(counter)

        new_geom = feature.GetGeometryRef()

        if new_geom is None:
            progbar.erase()  # get around the progressbar
            log.warning('Feature with FID={} has no geometry. Skipping'.format(feature.GetFID()))
            continue

        new_geom.Transform(transform)
        new_shape = wkbload(bytes(new_geom.ExportToWkb()))
        try:
            geom = geom.union(new_shape) if geom else new_shape
        except Exception as e:
            progbar.erase()  # get around the progressbar
            log.warning('Union failed for shape with FID={} and will be ignored'.format(feature.GetFID()))

    progbar.finish()
    data_source = None

    return geom


def get_geometry_unary_union(inpath, epsg):
    """
    Load all features from a ShapeFile and union them together into a single geometry
    :param inpath: Path to a ShapeFile
    :param epsg: Desired output spatial reference
    :return: Single Shapely geometry of all unioned features
    """

    log = Logger('Unary Union')

    driver = ogr.GetDriverByName("ESRI Shapefile")
    data_source = driver.Open(inpath, 0)
    layer = data_source.GetLayer()
    in_spatial_ref = layer.GetSpatialRef()

    out_spatial_ref, transform = get_transform_from_epsg(in_spatial_ref, epsg)

    fcount = layer.GetFeatureCount()
    progbar = ProgressBar(fcount, 50, "Unary Unioning features")
    counter = 0

    def unionize(wkb_lst):
        return unary_union([wkbload(g) for g in wkb_lst]).wkb

    geom_list = []
    for feature in layer:
        counter += 1
        progbar.update(counter)
        new_geom = feature.GetGeometryRef()
        geo_type = new_geom.GetGeometryType()

        # We can't union non-valid shapes but sometimes a buffer by 0 can help
        if not new_geom.IsValid():
            progbar.erase()  # get around the progressbar
            log.warning('Invalid shape with FID={} trying the Buffer0 technique...'.format(feature.GetFID()))
            try:
                new_geom = new_geom.Buffer(0)
                if not new_geom.IsValid():
                    progbar.erase()  # get around the progressbar
                    log.warning('   Still invalid. Skipping this geometry')
                    continue
            except Exception as e:
                progbar.erase()  # get around the progressbar
                log.warning('Exception raised during buffer 0 technique. skipping this file')
                continue

        if new_geom is None:
            progbar.erase()  # get around the progressbar
            log.warning('Feature with FID={} has no geoemtry. Skipping'.format(feature.GetFID()))
        # Filter out zero-length lines
        elif geo_type in LINE_TYPES and new_geom.Length() == 0:
            progbar.erase()  # get around the progressbar
            log.warning('Zero Length for shape with FID={}'.format(feature.GetFID()))
        # Filter out zero-area polys
        elif geo_type in POLY_TYPES and new_geom.Area() == 0:
            progbar.erase()  # get around the progressbar
            log.warning('Zero Area for shape with FID={}'.format(feature.GetFID()))
        else:
            new_geom.Transform(transform)
            geom_list.append(bytes(new_geom.ExportToWkb()))

            # IF we get past a certain size then run the union
            if len(geom_list) >= 500:
                geom_list = [unionize(geom_list)]
        new_geom = None

    log.debug('finished iterating with list of size: {}'.format(len(geom_list)))
    progbar.finish()

    if len(geom_list) > 1:
        log.debug('Starting final union of geom_list of size: {}'.format(len(geom_list)))
        # Do a final union to clean up anything that might still be in the list
        geom_union = wkbload(unionize(geom_list))
    elif len(geom_list) == 0:
        log.warning('No geometry found to union')
        return None
    else:
        log.debug('FINAL Unioning geom_list of size {}'.format(len(geom_list)))
        geom_union = wkbload(geom_list[0])
        log.debug('   done')

    print_geom_size(log, geom_union)
    log.debug('Complete')
    data_source = None
    return geom_union


def get_geometry_unary_union_from_wkt(inpath, to_sr_wkt):
    """
    Load all features from a ShapeFile and union them together into a single geometry
    :param inpath: Path to a ShapeFile
    :param epsg: Desired output spatial reference
    :return: Single Shapely geometry of all unioned features
    """

    log = Logger('Unary Union')

    driver = ogr.GetDriverByName("ESRI Shapefile")
    data_source = driver.Open(inpath, 0)
    layer = data_source.GetLayer()
    in_spatial_ref = layer.GetSpatialRef()

    out_spatial_ref, transform = get_transform_from_wkt(in_spatial_ref, to_sr_wkt)

    fcount = layer.GetFeatureCount()
    progbar = ProgressBar(fcount, 50, "Unary Unioning features")
    counter = 0

    def unionize(wkb_lst):
        return unary_union([wkbload(g) for g in wkb_lst]).wkb

    geom_list = []
    for feature in layer:
        counter += 1
        progbar.update(counter)
        new_geom = feature.GetGeometryRef()
        geo_type = new_geom.GetGeometryType()

        # We can't union non-valid shapes but sometimes a buffer by 0 can help
        if not new_geom.IsValid():
            progbar.erase()  # get around the progressbar
            log.warning('Invalid shape with FID={} trying the Buffer0 technique...'.format(feature.GetFID()))
            try:
                new_geom = new_geom.Buffer(0)
                if not new_geom.IsValid():
                    progbar.erase()  # get around the progressbar
                    log.warning('   Still invalid. Skipping this geometry')
                    continue
            except Exception as e:
                progbar.erase()  # get around the progressbar
                log.warning('Exception raised during buffer 0 technique. skipping this file')
                continue

        if new_geom is None:
            progbar.erase()  # get around the progressbar
            log.warning('Feature with FID={} has no geoemtry. Skipping'.format(feature.GetFID()))
        # Filter out zero-length lines
        elif geo_type in LINE_TYPES and new_geom.Length() == 0:
            progbar.erase()  # get around the progressbar
            log.warning('Zero Length for shape with FID={}'.format(feature.GetFID()))
        # Filter out zero-area polys
        elif geo_type in POLY_TYPES and new_geom.Area() == 0:
            progbar.erase()  # get around the progressbar
            log.warning('Zero Area for shape with FID={}'.format(feature.GetFID()))
        else:
            new_geom.Transform(transform)
            geom_list.append(bytes(new_geom.ExportToWkb()))

            # IF we get past a certain size then run the union
            if len(geom_list) >= 500:
                geom_list = [unionize(geom_list)]
        new_geom = None

    log.debug('finished iterating with list of size: {}'.format(len(geom_list)))
    progbar.finish()

    if len(geom_list) > 1:
        log.debug('Starting final union of geom_list of size: {}'.format(len(geom_list)))
        # Do a final union to clean up anything that might still be in the list
        geom_union = wkbload(unionize(geom_list))
    elif len(geom_list) == 0:
        log.warning('No geometry found to union')
        return None
    else:
        log.debug('FINAL Unioning geom_list of size {}'.format(len(geom_list)))
        geom_union = wkbload(geom_list[0])
        log.debug('   done')

    print_geom_size(log, geom_union)
    log.debug('Complete')
    data_source = None
    return geom_union


def get_transform_from_wkt(inSpatialRef, to_sr_wkt):
    log = Logger('get_transform_from_epsg')
    outSpatialRef = ogr.osr.SpatialReference()
    outSpatialRef.ImportFromWkt(to_sr_wkt)

    # https://github.com/OSGeo/gdal/issues/1546
    outSpatialRef.SetAxisMappingStrategy(inSpatialRef.GetAxisMappingStrategy())

    log.info('Input spatial reference is {0}'.format(inSpatialRef.ExportToProj4()))
    log.info('Output spatial reference is {0}'.format(outSpatialRef.ExportToProj4()))
    transform = ogr.osr.CoordinateTransformation(inSpatialRef, outSpatialRef)
    return outSpatialRef, transform


def create_shapefile(geometry, epsg, outpath):
    """
    Create a new ShapeFile and write the argument geometry to it
    This is meant to be a temporary/development method. Not production code.
    :param geometry: Single geometry
    :param epsg: Output spatial reference
    :param outpath: Path where the ShapeFile will get created
    :return: None
    """

    driver = ogr.GetDriverByName("ESRI Shapefile")
    spatial_ref = osr.SpatialReference()
    spatial_ref.ImportFromEPSG(epsg)
    data_source = driver.CreateDataSource(outpath)
    # TODO: Convert geometry type to function argument
    # TODO: Provide a more meaningful layer name
    layer = data_source.CreateLayer('network', spatial_ref, geom_type=ogr.wkbPolygon)

    # Get the output Layer's Feature Definition
    layer_def = layer.GetLayerDefn()

    # Create output Feature
    feature = ogr.Feature(layer_def)

    # Add new feature to output Layer
    ogrpolyline = ogr.CreateGeometryFromWkb(geometry.wkb)
    feature.SetGeometry(ogrpolyline)
    layer.CreateFeature(feature)

    data_source = None


def merge_geometries(feature_classes, epsg):
    """
    Load all features from multiple feature classes into a single list of geometries
    :param feature_classes:
    :param epsg:
    :return:
    """
    log = Logger('Shapefile')

    driver = ogr.GetDriverByName("ESRI Shapefile")

    union = ogr.Geometry(ogr.wkbMultiLineString)

    fccount = 0
    for fc in feature_classes:
        fccount += 1
        log.info("Merging Geometries for feature class {}/{}".format(fccount, len(feature_classes)))
        data_source = driver.Open(fc, 0)
        layer = data_source.GetLayer()

        in_spatial_ref = layer.GetSpatialRef()

        out_spatial_ref, transform = get_transform_from_epsg(in_spatial_ref, epsg)

        progbar = ProgressBar(layer.GetFeatureCount(), 50, "Merging Geometries")
        counter = 0
        for feature in layer:
            counter += 1
            progbar.update(counter)
            geom = feature.GetGeometryRef()

            if geom is None:
                progbar.erase()  # get around the progressbar
                log.warning('Feature with FID={} has no geoemtry. Skipping'.format(feature.GetFID()))
                continue

            geom.Transform(transform)
            union.AddGeometry(geom)

        progbar.finish()
        data_source = None

    return union


def clip(features, clip, output):
    """
    Clip one ShapeFile with another
    https://gis.stackexchange.com/questions/297268/clipping-shapefile-with-python
    :param features: The feature class that will be clipped
    :param clip: The boundary feature class that will be used to do the clipping
    :param output: The path where the clipped feature class will get created
    :return: None
    """

    callstr = ['ogr2ogr',
               '-clipsrc',
               clip,
               output,
               features]
    proc = subprocess.Popen(callstr, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = proc.communicate()


def copy_feature_class(inpath, epsg, outpath, clip_shape=None, attribute_filter=None):
    """Copy a Shapefile from one location to another

    This method is capable of reprojecting the geometries as they are copied.
    It is also possible to filter features by both attributes and also clip the
    features to another geometryNone

    Arguments:
        inpath {str} -- File path to input Shapefile that will be copied.
        epsg {int} -- Output coordinate system
        outpath {str} -- File path where the output Shapefile will be generated.

    Keyword Arguments:
        clip_shape {shape} -- Shapely polygon geometry in the output EPSG used to clip the input geometries (default: {None})
        attribute_filter {str} -- Attribute filter used to limit the input features that will be copied. (default: {None})
    """

    log = Logger('Shapefile')

    # if os.path.isfile(outpath):
    #     log.info('Skipping copy of feature classes because output file exists.')
    #     return

    driver = ogr.GetDriverByName("ESRI Shapefile")

    inDataSource = driver.Open(inpath, 0)
    inLayer = inDataSource.GetLayer()
    inSpatialRef = inLayer.GetSpatialRef()
    geom_type = inLayer.GetGeomType()

    # Optionally limit which features are copied by using an attribute filter
    if attribute_filter:
        inLayer.SetAttributeFilter(attribute_filter)

    # If there's a clip geometry provided then limit the features copied to
    # those that intersect (partially or entirely) by this clip feature.
    # Note that this makes the subsequent intersection process a lot more
    # performant because the SetSaptialFilter() uses the ShapeFile's spatial
    # index which is much faster than manually checking if all pairs of features intersect.
    clip_geom = None
    if clip_shape:
        clip_geom = ogr.CreateGeometryFromWkb(clip_shape.wkb)
        inLayer.SetSpatialFilter(clip_geom)

    outpath_dir = os.path.dirname(outpath)
    safe_makedirs(outpath_dir)

    # Create the output shapefile
    outSpatialRef, transform = get_transform_from_epsg(inSpatialRef, epsg)

    outDataSource = driver.CreateDataSource(outpath)
    outLayer = outDataSource.CreateLayer('network', outSpatialRef, geom_type=geom_type)
    outLayerDefn = outLayer.GetLayerDefn()

    # Add input Layer Fields to the output Layer if it is the one we want
    inLayerDefn = inLayer.GetLayerDefn()
    for i in range(0, inLayerDefn.GetFieldCount()):
        fieldDefn = inLayerDefn.GetFieldDefn(i)
        outLayer.CreateField(fieldDefn)

    # Get the output Layer's Feature Definition
    outLayerDefn = outLayer.GetLayerDefn()

    progbar = ProgressBar(inLayer.GetFeatureCount(), 50, "Copying features")
    counter = 0
    for feature in inLayer:
        counter += 1
        progbar.update(counter)
        geom = feature.GetGeometryRef()

        if geom is None:
            progbar.erase()  # get around the progressbar
            log.warning('Feature with FID={} has no geometry. Skipping'.format(feature.GetFID()))
            continue

        geom.Transform(transform)

        # if clip_shape:
        #     raw = shape(json.loads(geom.ExportToJson()))
        #     try:
        #         clip = raw.intersection(clip_shape)
        #         geom = ogr.CreateGeometryFromJson(json.dumps(mapping(clip)))
        #     except Exception as e:
        #         progbar.erase()  # get around the progressbar
        #         log.warning('Invalid shape with FID={} cannot be intersected'.format(feature.GetFID()))

        # Create output Feature
        outFeature = ogr.Feature(outLayerDefn)
        outFeature.SetGeometry(geom)

        # Add field values from input Layer
        for i in range(0, outLayerDefn.GetFieldCount()):
            outFeature.SetField(outLayerDefn.GetFieldDefn(i).GetNameRef(), feature.GetField(i))

        outLayer.CreateFeature(outFeature)
        outFeature = None

    progbar.finish()
    inDataSource = None
    outDataSource = None


def merge_feature_classes(feature_classes, epsg, boundary, outpath):

    log = Logger('Shapefile')

    if os.path.isfile(outpath):
        log.info('Skipping merging feature classes because file exists.')
        return

    safe_makedirs(os.path.dirname(outpath))

    log.info('Merging {} feature classes.'.format(len(feature_classes)))

    driver = ogr.GetDriverByName("ESRI Shapefile")

    # Create the output shapefile
    outDataSource = driver.CreateDataSource(outpath)
    outLayer = None
    outSpatialRef = None
    transform = None

    fccount = 0
    for inpath in feature_classes:
        fccount += 1
        log.info("Merging feature class {}/{}".format(fccount, len(feature_classes)))

        inDataSource = driver.Open(inpath, 0)
        inLayer = inDataSource.GetLayer()
        inSpatialRef = inLayer.GetSpatialRef()
        inLayer.SetSpatialFilter(ogr.CreateGeometryFromWkb(boundary.wkb))

        # First input spatial ref sets the SRS for the output file
        outSpatialRefTmp, transform = get_transform_from_epsg(inSpatialRef, epsg)
        if outLayer is None:
            outSpatialRef = outSpatialRefTmp
            outLayer = outDataSource.CreateLayer('network', outSpatialRef, geom_type=ogr.wkbMultiLineString)

        outLayerDefn = outLayer.GetLayerDefn()
        # Transfer fields over
        inLayerDef = inLayer.GetLayerDefn()
        for i in range(inLayerDef.GetFieldCount()):
            inFieldDef = inLayerDef.GetFieldDefn(i)
            # Only create fields if we really don't have them
            # NOTE: THIS ASSUMES ALL FIELDS OF THE SAME NAME HAVE THE SAME TYPE
            if outLayerDefn.GetFieldIndex(inFieldDef.GetName()) == -1:
                outLayer.CreateField(inFieldDef)

        progbar = ProgressBar(inLayer.GetFeatureCount(), 50, "Processing features")

        outLayerDefn = outLayer.GetLayerDefn()

        counter = 0
        for feature in inLayer:
            counter += 1
            progbar.update(counter)

            geom = feature.GetGeometryRef()

            if geom is None:
                progbar.erase()  # get around the progressbar
                log.warning('Feature with FID={} has no geometry. Skipping'.format(feature.GetFID()))
                continue

            geom.Transform(transform)

            # get a Shapely representation of the line
            # featobj = json.loads(geom.ExportToJson())
            # polyline = shape(featobj)

            # if boundary.intersects(polyline):
            # clipped = boundary.intersection(polyline)
            outFeature = ogr.Feature(outLayerDefn)

            for i in range(inLayerDef.GetFieldCount()):
                outFeature.SetField(outLayerDefn.GetFieldDefn(i).GetNameRef(), feature.GetField(i))

            outFeature.SetGeometry(geom)
            outLayer.CreateFeature(outFeature)

            feature = None
            outFeature = None

        progbar.finish()
        inDataSource = None

    outDataSource = None
    log.info('Merge complete.')


def load_attributes(network, id_field, fields):
    """
    Load ShapeFile attributes fields into a dictionary keyed by the id_field
    :param network: Full, absolute path to a ShapeFile
    :param id_field: Field that uniquely identifies each feature
    :param fields: List of fields to load into the dictionary
    :return: Dictionary with id_field as key and each feature as dictionary of values keyed by the field name
    """

    # Get the input network
    driver = ogr.GetDriverByName('ESRI Shapefile')
    dataset = driver.Open(network, 0)
    layer = dataset.GetLayer()

    # Verify that all the fields are present or throw an exception
    [verify_field(layer, field) for field in fields]

    # Only calculate the combined FIS where all the inputs exist
    # [networkLr.SetAttributeFilter('{} is not null'.format(field)) for field in [veg_field, drain_field, hydq2_field, hydlow_field, length_field, slope_field]]
    # layer.SetAttributeFilter("iGeo_Slope > 0 and iGeo_DA > 0")

    driver = ogr.GetDriverByName("ESRI Shapefile")
    data_source = driver.Open(network, 0)
    layer = data_source.GetLayer()
    print('{:,} features in polygon ShapeFile {}'.format(layer.GetFeatureCount(), network))

    feature_values = {}

    progbar = ProgressBar(layer.GetFeatureCount(), 50, "Loading features")
    counter = 0
    for inFeature in layer:
        counter += 1
        progbar.update(counter)

        reach = inFeature.GetField(id_field)
        feature_values[reach] = {}

        for field in fields:
            feature_values[reach][field] = inFeature.GetField(field)

    progbar.finish()
    return feature_values


def load_geometries(feature_class, id_field, epsg=None):
    log = Logger('Shapefile')
    # Get the input network
    driver = ogr.GetDriverByName('ESRI Shapefile')
    dataset = driver.Open(feature_class, 0)
    layer = dataset.GetLayer()
    in_spatial_ref = layer.GetSpatialRef()

    # Determine the transformation if user provides an EPSG
    transform = None
    if epsg:
        out_spatial_ref, transform = get_transform_from_epsg(in_spatial_ref, epsg)

    features = {}

    progbar = ProgressBar(layer.GetFeatureCount(), 50, "Loading features")
    counter = 0
    for inFeature in layer:
        counter += 1
        progbar.update(counter)

        reach = inFeature.GetField(id_field)
        geom = inFeature.GetGeometryRef()

        # Optional coordinate transformation
        if transform:
            geom.Transform(transform)

        new_geom = wkbload(bytes(geom.ExportToWkb()))
        geo_type = new_geom.GetGeometryType()

        if new_geom.is_empty:
            progbar.erase()  # get around the progressbar
            log.warning('Empty feature with FID={} cannot be unioned and will be ignored'.format(inFeature.GetFID()))
        elif not new_geom.is_valid:
            progbar.erase()  # get around the progressbar
            log.warning('Invalid feature with FID={} cannot be unioned and will be ignored'.format(inFeature.GetFID()))
        # Filter out zero-length lines
        elif geo_type in LINE_TYPES and new_geom.Length() == 0:
            progbar.erase()  # get around the progressbar
            log.warning('Zero Length for feature with FID={}'.format(inFeature.GetFID()))
        # Filter out zero-area polys
        elif geo_type in POLY_TYPES and new_geom.Area() == 0:
            progbar.erase()  # get around the progressbar
            log.warning('Zero Area for feature with FID={}'.format(inFeature.GetFID()))
        else:
            features[reach] = new_geom

    progbar.finish()
    dataset = None
    return features


def write_attributes(feature_class, output_values, id_field, fields, field_type=ogr.OFTReal, null_values=None):
    """
    Write field values to a ShapeFile feature class
    :param feature_class: Path to feature class
    :param output_values: Dictionary of values keyed by id_field. Each feature is dictionary keyed by field names
    :param id_field: Unique key identifying each feature in both feature class and output_values dictionary
    :param fields: List of fields in output_values to write to ShapeFile
    :return: None
    """

    log = Logger('ShapeFile')

    driver = ogr.GetDriverByName('ESRI Shapefile')
    dataset = driver.Open(feature_class, 1)
    layer = dataset.GetLayer()

    # Create each field and store the name and index in a list of tuples
    field_indices = [(field, create_field(layer, field, field_type)) for field in fields]

    for feature in layer:
        reach = feature.GetField(id_field)
        if reach not in output_values:
            continue

        # Set all the field values and then store the feature
        for field, idx in field_indices:
            if field in output_values[reach]:
                if not output_values[reach][field]:
                    if null_values:
                        feature.SetField(field, null_values)
                    else:
                        log.warning('Unhandled ShapeFile value for None type')
                        feature.SetField(field, None)
                else:
                    feature.SetField(field, output_values[reach][field])
        layer.SetFeature(feature)

    dataset = None


def verify_spatial_ref(shapefile_path, raster_path):

    driver = ogr.GetDriverByName("ESRI Shapefile")
    dataset = driver.Open(shapefile_path, 1)
    layer = dataset.GetLayer()
    spatial_ref = layer.GetSpatialRef()

    raster = Raster(raster_path)

    ex = None
    if not spatial_ref.IsSame(osr.SpatialReference(wkt=raster.proj)):
        ex = Exception('ShapeFile and raster spatial references do not match.')
        # TODO add more information to the exception

    dataset = None
    raster = None

    if ex:
        raise ex


def network_statistics(label, shapefile_path):

    log = Logger('shapefile')
    log.info('Network ShapeFile Summary: {}'.format(shapefile_path))

    driver = ogr.GetDriverByName("ESRI Shapefile")
    dataset = driver.Open(shapefile_path, 1)
    layer = dataset.GetLayer()

    results = {}
    total_length = 0.0
    min_length = None
    max_length = None
    invalid_features = 0
    no_geometry = 0

    # Delete output column from network ShapeFile if it exists and then recreate it
    networkDef = layer.GetLayerDefn()
    for fieldidx in range(0, networkDef.GetFieldCount()):
        results[networkDef.GetFieldDefn(fieldidx).GetName()] = 0

    progbar = ProgressBar(layer.GetFeatureCount(), 50, "Calculating Stats")
    counter = 0
    for feature in layer:
        counter += 1
        progbar.update(counter)

        geom = feature.GetGeometryRef()

        if geom is None:
            no_geometry += 1
            continue

        shapely_obj = wkbload(bytes(geom.ExportToWkb()))
        length = shapely_obj.length

        if shapely_obj.is_empty or shapely_obj.is_valid is False:
            invalid_features += 1

        total_length += length
        min_length = length if not min_length or min_length > length else min_length
        max_length = length if not max_length or max_length < length else max_length

        for fieldidx in range(0, networkDef.GetFieldCount()):
            field = networkDef.GetFieldDefn(fieldidx).GetName()
            if field not in results:
                results[field] = 0

            results[field] += 0 if feature.GetField(field) else 1

    progbar.finish()

    features = layer.GetFeatureCount()
    results['Feature Count'] = features
    results['Invalid Features'] = invalid_features
    results['Features without geometry'] = no_geometry
    results['Min Length'] = min_length
    results['Max Length'] = max_length
    results['Avg Length'] = (total_length / features) if features > 0 and total_length != 0 else 0.0
    results['Total Length'] = total_length

    for key, value in results.items():
        if value > 0:
            log.info('{}, {} with {:,} NULL values'.format(label, key, value))

    dataset = None
    return results


def _rough_convert_metres_to_shapefile_units(shapefile_path, distance):

    driver = ogr.GetDriverByName("ESRI Shapefile")
    data_source = driver.Open(shapefile_path, 0)
    layer = data_source.GetLayer()
    in_spatial_ref = layer.GetSpatialRef()
    extent = layer.GetExtent()
    data_source = None

    return _rough_convert_metres_to_dataset_units(in_spatial_ref, extent, distance)


def _rough_convert_metres_to_gpkg_units(gpkg_path, distance):

    driver = ogr.GetDriverByName("GPKG")
    data_source = driver.Open(gpkg_path, 0)
    layer = data_source.GetLayer()
    in_spatial_ref = layer.GetSpatialRef()
    extent = layer.GetExtent()
    data_source = None

    return _rough_convert_metres_to_dataset_units(in_spatial_ref, extent, distance)


def _rough_convert_metres_to_raster_units(raster_path, distance):

    ds = gdal.Open(raster_path)
    in_spatial_ref = osr.SpatialReference()
    in_spatial_ref.ImportFromWkt(ds.GetProjectionRef())
    gt = ds.GetGeoTransform()
    extent = (gt[0], gt[0] + gt[1] * ds.RasterXSize, gt[3] + gt[5] * ds.RasterYSize, gt[3])

    return _rough_convert_metres_to_dataset_units(in_spatial_ref, extent, distance)


def _rough_convert_metres_to_dataset_units(in_spatial_ref, extent, distance):
    """DO NOT USE THIS FOR ACCURATE DISTANCES. IT'S GOOD FOR A QUICK CALCULATION
    WHEN DISTANCE PRECISION ISN'T THAT IMPORTANT

    Arguments:
        shapefile_path {[type]} -- [description]
        distance {[type]} -- [description]

    Returns:
        [type] -- [description]
    """
    log = Logger('_rough_convert_metres_to_dataset_units')
    # If the ShapeFile uses a projected coordinate system in meters then simply return the distance.
    # If it's projected but in some other units then throw an exception.
    # If it's in degrees then continue with the code below to convert it to metres.
    if in_spatial_ref.IsProjected() == 1:
        if in_spatial_ref.GetAttrValue('unit').lower() in ['meter', 'metre', 'm']:
            return distance
        else:
            raise Exception('Unhandled projected coordinate system linear units: {}'.format(in_spatial_ref.GetAttrValue('unit')))

    # Get the centroid of the Shapefile spatial extent
    extent_ring = ogr.Geometry(ogr.wkbLinearRing)
    extent_ring.AddPoint(extent[0], extent[2])
    extent_ring.AddPoint(extent[1], extent[2])
    extent_ring.AddPoint(extent[1], extent[3])
    extent_ring.AddPoint(extent[0], extent[3])
    extent_ring.AddPoint(extent[0], extent[2])
    extent_poly = ogr.Geometry(ogr.wkbPolygon)
    extent_poly.AddGeometry(extent_ring)
    extent_centroid = extent_poly.Centroid()

    # Go diagonally on the extent rectangle
    pt1_orig = Point(extent[0], extent[2])
    pt2_orig = Point(extent[1], extent[3])
    orig_dist = pt1_orig.distance(pt2_orig)

    # Determine the UTM zone by locating the centroid of the shapefile extent
    # Then get the transformation required to convert to the Shapefile to this UTM zone
    utm_epsg = get_utm_zone_epsg(extent_centroid.GetX())
    in_spatial_ref.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)

    out_spatial_ref = osr.SpatialReference()
    out_spatial_ref.ImportFromEPSG(int(utm_epsg))
    out_spatial_ref.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)

    log.debug('Original spatial reference is : \n       {0} (AxisMappingStrategy:{1})'.format(*get_srs_debug(in_spatial_ref)))
    log.debug('Transform spatial reference is : \n       {0} (AxisMappingStrategy:{1})'.format(*get_srs_debug(out_spatial_ref)))

    transformFwd = osr.CoordinateTransformation(in_spatial_ref, out_spatial_ref)

    pt1_ogr = ogr.CreateGeometryFromWkb(pt1_orig.wkb)
    pt2_ogr = ogr.CreateGeometryFromWkb(pt2_orig.wkb)
    pt1_ogr.Transform(transformFwd)
    pt2_ogr.Transform(transformFwd)

    pt1_proj = wkbload(bytes(pt1_ogr.ExportToWkb()))
    pt2_proj = wkbload(bytes(pt2_ogr.ExportToWkb()))

    proj_dist = pt1_proj.distance(pt2_proj)

    output_distance = (orig_dist / proj_dist) * distance

    log.info('{}m distance converts to {:.10f} using UTM EPSG {}'.format(distance, output_distance, utm_epsg))

    if output_distance > 360:
        raise Exception('Projection Error: \'{:,}\' is larger than the maximum allowed value'.format(output_distance))

    return output_distance


def get_utm_zone_epsg(longitude):

    zone_number = math.floor((180.0 + longitude) / 6.0)
    epsg = 26901 + zone_number
    return epsg


def feature_class_bounds(shapefile_path):

    driver = ogr.GetDriverByName("ESRI Shapefile")
    data_source = driver.Open(shapefile_path, 0)
    layer = data_source.GetLayer()
    return layer.GetExtent()


def intersect_feature_classes(feature_class1, feature_class2, epsg, out_path, output_geom_type):

    union = get_geometry_unary_union(feature_class1, epsg)
    intersect_geometry_with_feature_class(union, feature_class2, epsg, out_path, output_geom_type)


def intersect_geometry_with_feature_class(geometry, feature_class, epsg, out_path, output_geom_type):

    if output_geom_type not in [ogr.wkbMultiPoint, ogr.wkbMultiLineString]:
        raise Exception('Unsupported ogr type: "{}"'.format(output_geom_type))

    # Remove output shapefile if it already exists
    driver = ogr.GetDriverByName("ESRI Shapefile")
    if os.path.exists(out_path):
        driver.DeleteDataSource(out_path)
    else:
        # Make sure the output folder exists
        safe_makedirs(os.path.dirname(out_path))

    # Create the output shapefile
    data_source = driver.CreateDataSource(out_path)
    spatial_ref = osr.SpatialReference()
    spatial_ref.ImportFromEPSG(epsg)
    layer = data_source.CreateLayer('intersection', spatial_ref, geom_type=output_geom_type)

    geom_union = get_geometry_unary_union(feature_class, epsg)

    # Nothing to do if there were no features in the feature class
    if not geom_union:
        data_source = None
        return

    geom_inter = geometry.intersection(geom_union)

    # Nothing to do if the intersection is empty
    if geom_inter.is_empty:
        data_source = None
        return

    # Single features and collections need to be converted into Multi-features
    if output_geom_type == ogr.wkbMultiPoint and not isinstance(geom_inter, MultiPoint):
        if isinstance(geom_inter, Point):
            geom_inter = MultiPoint([(geom_inter)])

        elif isinstance(geom_inter, LineString):
            # Break this linestring down into vertices as points
            geom_inter = MultiPoint(list(geom_inter.coords))

        elif isinstance(geom_inter, MultiLineString):
            # Break this linestring down into vertices as points
            geom_inter = MultiPoint(reduce(lambda acc, ls: acc + list(ls.coords), list(geom_inter.geoms), []))

        elif isinstance(geom_inter, GeometryCollection):
            geom_inter = MultiPoint([geom for geom in geom_inter.geoms if isinstance(geom, Point)])

    elif output_geom_type == ogr.wkbMultiLineString and not isinstance(geom_inter, MultiLineString):
        if isinstance(geom_inter, LineString):
            geom_inter = MultiLineString([(geom_inter)])
        else:
            raise Exception('Unsupported ogr type: "{}" does not match shapely type of "{}"'.format(output_geom_type, geom_inter.type))

    out_layer_def = layer.GetLayerDefn()
    feature = ogr.Feature(out_layer_def)
    feature.SetGeometry(ogr.CreateGeometryFromWkb(geom_inter.wkb))
    layer.CreateFeature(feature)

    data_source = None


def polygonize(raster_path, band, out_shp_path, epsg):
    # mapping between gdal type and ogr field type
    type_mapping = {
        gdal.GDT_Byte: ogr.OFTInteger,
        gdal.GDT_UInt16: ogr.OFTInteger,
        gdal.GDT_Int16: ogr.OFTInteger,
        gdal.GDT_UInt32: ogr.OFTInteger,
        gdal.GDT_Int32: ogr.OFTInteger,
        gdal.GDT_Float32: ogr.OFTReal,
        gdal.GDT_Float64: ogr.OFTReal,
        gdal.GDT_CInt16: ogr.OFTInteger,
        gdal.GDT_CInt32: ogr.OFTInteger,
        gdal.GDT_CFloat32: ogr.OFTReal,
        gdal.GDT_CFloat64: ogr.OFTReal
    }

    src_ds = gdal.Open(raster_path)
    src_band = src_ds.GetRasterBand(band)
    driver = ogr.GetDriverByName("ESRI Shapefile")
    if os.path.exists(out_shp_path):
        driver.DeleteDataSource(out_shp_path)
    outDataSource = driver.CreateDataSource(out_shp_path)
    out_spatial_ref = osr.SpatialReference()
    out_spatial_ref.ImportFromEPSG(epsg)

    outLayer = outDataSource.CreateLayer("polygonized", out_spatial_ref, geom_type=ogr.wkbPolygon)

    raster_field = ogr.FieldDefn('id', type_mapping[src_band.DataType])
    outLayer.CreateField(raster_field)

    progbar = ProgressBar(100, 50, "Polygonizing raster")

    def poly_progress(progress, msg, data):
        # double dfProgress, char const * pszMessage=None, void * pData=None
        progbar.update(int(progress * 100))

    gdal.Polygonize(src_band, src_ds.GetRasterBand(band), outLayer, 0, [], callback=poly_progress)
    progbar.finish()

    outDataSource.Destroy()
    src_ds = None


def remove_holes(geom, min_hole_area):
    """[summary]

    Arguments:
        geom {[type]} -- shapely geometry of either Polygon or Multipolygon
        min_hole_area {[type]} -- (optional) Minimum area of holes to keep. If ommitted, all holes will be removed

    Raises:
        Exception: [description]

    Returns:
        [type] -- [description]
    """

    def _simpl(geo):
        return Polygon(geo.exterior, list(filter(lambda x: Polygon(x).area > min_hole_area, geo.interiors)))

    if type(geom) == Polygon:
        if min_hole_area is None:
            # Remove all holes if we don't specify a min area
            return Polygon(geom.exterior)
        else:
            return _simpl(geom)
    elif type(geom) == MultiPolygon:
        if min_hole_area is None:
            # Remove all holes if we don't specify a min area
            return MultiPolygon([Polygon(mgeo.exterior) for mgeo in geom.geoms])
        else:
            return MultiPolygon([_simpl(mgeo) for mgeo in geom.geoms])
    else:
        raise Exception('Invalid geometry type used for "remove_holes": {}'.format(type(geom)))


def get_pts(geom):
    """Helper function for counting points

    Arguments:
        geom {[Shapely Shape]} -- [description]

    Returns:
        [int] -- Number
    """
    try:
        if type(geom) == Polygon:
            return len(geom.exterior.coords)
        elif type(geom) == MultiPolygon:
            return reduce(lambda x, y: x + y, [len(g.exterior.coords) for g in geom.geoms])
    except Exception as e:
        pass
    return None


def get_rings(geom):
    """Helper function for counting points

    Arguments:
        geom {[Shapely Shape]} -- [description]

    Returns:
        [int] -- Number
    """
    try:
        if type(geom) == Polygon:
            return len(geom.interiors)
        elif type(geom) == MultiPolygon:
            return reduce(lambda x, y: x + y, [len(g.interiors) for g in geom.geoms])
    except Exception as e:
        pass
    return None


def export_geojson(shapely_geom, props=None):
    new_props = copy(props) if props is not None else {}
    the_dict = {
        "type": "FeatureCollection",
        "features": [
        ]
    }
    if shapely_geom is not None:
        the_dict["features"].append({
            "type": "Feature",
            "properties": new_props,
            "geometry": mapping(shapely_geom),
        })

    return the_dict
