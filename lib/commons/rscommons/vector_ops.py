# Name:     Vector Operations Methods
#
# Purpose:  Miscellaneous routines for common tasks related to Vector  files
#           All methods should take and return VectorBase derivative objects
#
# Author:   Matt Reimer
#
# Date:     Nov 16, 2020
# -------------------------------------------------------------------------------
from copy import copy
from typing import List
from functools import reduce
from osgeo import ogr, gdal, osr
from shapely.ops import unary_union
from shapely.geometry.base import BaseGeometry
from shapely.geometry import mapping, Point, MultiPoint, LineString, MultiLineString, GeometryCollection, Polygon, MultiPolygon
from rscommons import Logger, ProgressBar, get_shp_or_gpkg, Timer, VectorBase
from rscommons.util import sizeof_fmt, get_obj_size
from rscommons.classes.vector_base import VectorBaseException


def print_geom_size(logger: Logger, geom_obj: BaseGeometry):
    try:
        size_str = sizeof_fmt(get_obj_size(geom_obj.wkb))
        logger.debug('Byte Size of output object: {} Type: {} IsValid: {} Length: {} Area: {}'.format(size_str, geom_obj.type, geom_obj.is_valid, geom_obj.length, geom_obj.area))
    except Exception as e:
        logger.debug(e)
        logger.debug('Byte Size of output object could not be determined')


def get_geometry_union(in_layer_path: str, epsg: int = None,
                       attribute_filter: str = None,
                       clip_shape: BaseGeometry = None,
                       clip_rect: List[float] = None
                       ) -> BaseGeometry:
    """[summary]

    Args:
        in_layer_path (str): [description]
        epsg (int, optional): [description]. Defaults to None.
        attribute_filter (str, optional): [description]. Defaults to None.
        clip_shape (BaseGeometry, optional): [description]. Defaults to None.
        clip_rect (List[double minx, double miny, double maxx, double maxy)]): Iterate over a subset by clipping to a Shapely-ish geometry. Defaults to None.

    Returns:
        BaseGeometry: [description]
    """

    log = Logger('get_geometry_union')

    with get_shp_or_gpkg(in_layer_path) as in_layer:

        transform = None
        if epsg:
            _outref, transform = VectorBase.get_transform_from_epsg(in_layer.spatial_ref, epsg)

        geom = None

        for feature, _counter, progbar in in_layer.iterate_features("Getting geometry union", attribute_filter=attribute_filter, clip_shape=clip_shape, clip_rect=clip_rect):
            if feature.GetGeometryRef() is None:
                progbar.erase()  # get around the progressbar
                log.warning('Feature with FID={} has no geometry. Skipping'.format(feature.GetFID()))
                continue

            new_shape = VectorBase.ogr2shapely(feature, transform=transform)
            try:
                geom = geom.union(new_shape) if geom is not None else new_shape
            except Exception:
                progbar.erase()  # get around the progressbar
                log.warning('Union failed for shape with FID={} and will be ignored'.format(feature.GetFID()))

    return geom


def get_geometry_unary_union(in_layer_path: str, epsg: int = None, spatial_ref: osr.SpatialReference = None,
                             attribute_filter: str = None,
                             clip_shape: BaseGeometry = None,
                             clip_rect: List[float] = None
                             ) -> BaseGeometry:
    """Load all features from a ShapeFile and union them together into a single geometry

    Args:
        in_layer_path (str): path to layer
        epsg (int, optional): EPSG to project to. Defaults to None.
        spatial_ref (osr.SpatialReference, optional): Spatial Ref to project to. Defaults to None.
        attribute_filter (str, optional): Filter to a set of attributes. Defaults to None.
        clip_shape (BaseGeometry, optional): Clip to a specified shape. Defaults to None.
        clip_rect (List[double minx, double miny, double maxx, double maxy)]): Iterate over a subset by clipping to a Shapely-ish geometry. Defaults to None.

    Raises:
        VectorBaseException: [description]

    Returns:
        BaseGeometry: [description]
    """
    log = Logger('get_geometry_unary_union')

    if epsg is not None and spatial_ref is not None:
        raise VectorBaseException('Specify either an EPSG or a spatial_ref. Not both')

    with get_shp_or_gpkg(in_layer_path) as in_layer:
        transform = None
        if epsg is not None:
            _outref, transform = VectorBase.get_transform_from_epsg(in_layer.spatial_ref, epsg)
        elif spatial_ref is not None:
            transform = in_layer.get_transform(in_layer.spatial_ref, spatial_ref)

        geom_list = []

        for feature, _counter, progbar in in_layer.iterate_features("Unary Unioning features", attribute_filter=attribute_filter, clip_shape=clip_shape, clip_rect=clip_rect):
            new_geom = feature.GetGeometryRef()
            geo_type = new_geom.GetGeometryType()

            # We can't union non-valid shapes but sometimes a buffer by 0 can help
            if not new_geom.IsValid():
                progbar.erase()  # get around the progressbar
                log.warning('Invalid shape with FID={} trying the Buffer0 technique...'.format(feature.GetFID()))
                try:
                    new_geom = new_geom.Buffer(0)
                    if not new_geom.IsValid():
                        log.warning('   Still invalid. Skipping this geometry')
                        continue
                except Exception:
                    log.warning('Exception raised during buffer 0 technique. skipping this file')
                    continue

            if new_geom is None:
                progbar.erase()  # get around the progressbar
                log.warning('Feature with FID={} has no geoemtry. Skipping'.format(feature.GetFID()))
            # Filter out zero-length lines
            elif geo_type in VectorBase.LINE_TYPES and new_geom.Length() == 0:
                progbar.erase()  # get around the progressbar
                log.warning('Zero Length for shape with FID={}'.format(feature.GetFID()))
            # Filter out zero-area polys
            elif geo_type in VectorBase.POLY_TYPES and new_geom.Area() == 0:
                progbar.erase()  # get around the progressbar
                log.warning('Zero Area for shape with FID={}'.format(feature.GetFID()))
            else:
                geom_list.append(VectorBase.ogr2shapely(new_geom, transform))

                # IF we get past a certain size then run the union
                if len(geom_list) >= 500:
                    geom_list = [unary_union(geom_list)]
            new_geom = None

    log.debug('finished iterating with list of size: {}'.format(len(geom_list)))

    if len(geom_list) > 1:
        log.debug('Starting final union of geom_list of size: {}'.format(len(geom_list)))
        # Do a final union to clean up anything that might still be in the list
        geom_union = unary_union(geom_list)
    elif len(geom_list) == 0:
        log.warning('No geometry found to union')
        return None
    else:
        log.debug('FINAL Unioning geom_list of size {}'.format(len(geom_list)))
        geom_union = geom_list[0]
        log.debug('   done')

    print_geom_size(log, geom_union)
    log.debug('Complete')
    # Return a shapely object
    return geom_union


def copy_feature_class(in_layer_path: str, out_layer_path: str,
                       epsg: int = None,
                       attribute_filter: str = None,
                       clip_shape: BaseGeometry = None,
                       clip_rect: List[float] = None,
                       buffer: float = 0,
                       ) -> None:
    """Copy a Shapefile from one location to another

    This method is capable of reprojecting the geometries as they are copied.
    It is also possible to filter features by both attributes and also clip the
    features to another geometryNone

    Args:
        in_layer (str): Input layer path
        epsg ([type]): EPSG Code to use for the transformation
        out_layer (str): Output layer path
        attribute_filter (str, optional): [description]. Defaults to None.
        clip_shape (BaseGeometry, optional): [description]. Defaults to None.
        clip_rect (List[double minx, double miny, double maxx, double maxy)]): Iterate over a subset by clipping to a Shapely-ish geometry. Defaults to None.
        buffer (float): Buffer the output features (in meters).
    """

    log = Logger('copy_feature_class')

    # NOTE: open the outlayer first so that write gets the dataset open priority
    with get_shp_or_gpkg(out_layer_path, write=True) as out_layer, \
            get_shp_or_gpkg(in_layer_path) as in_layer:

        # Add input Layer Fields to the output Layer if it is the one we want
        out_layer.create_layer_from_ref(in_layer, epsg=epsg)

        transform = VectorBase.get_transform(in_layer.spatial_ref, out_layer.spatial_ref)

        buffer_convert = 0
        if buffer != 0:
            buffer_convert = in_layer.rough_convert_metres_to_vector_units(buffer)

        # This is the callback method that will be run on each feature
        for feature, _counter, progbar in in_layer.iterate_features("Copying features", write_layers=[out_layer], clip_shape=clip_shape, clip_rect=clip_rect, attribute_filter=attribute_filter):
            geom = feature.GetGeometryRef()

            if geom is None:
                progbar.erase()  # get around the progressbar
                log.warning('Feature with FID={} has no geometry. Skipping'.format(feature.GetFID()))
                continue
            if geom.GetGeometryType() in VectorBase.LINE_TYPES:
                if geom.Length() == 0.0:
                    progbar.erase()  # get around the progressbar
                    log.warning('Feature with FID={} has no Length. Skipping'.format(feature.GetFID()))
                    continue

            # Buffer the shape if we need to
            if buffer_convert != 0:
                geom = geom.Buffer(buffer_convert)

            geom.Transform(transform)

            # Create output Feature
            out_feature = ogr.Feature(out_layer.ogr_layer_def)
            out_feature.SetGeometry(geom)

            # Add field values from input Layer
            for i in range(0, out_layer.ogr_layer_def.GetFieldCount()):
                out_feature.SetField(out_layer.ogr_layer_def.GetFieldDefn(i).GetNameRef(), feature.GetField(i))

            out_layer.ogr_layer.CreateFeature(out_feature)
            out_feature = None


def merge_feature_classes(feature_class_paths: List[str], out_layer_path: str, boundary: BaseGeometry = None):
    """[summary]

    Args:
        feature_class_paths (List[str]): [description]
        boundary (BaseGeometry): [description]
        out_layer_path (str): [description]
    """
    log = Logger('merge_feature_classes')
    log.info('Merging {} feature classes.'.format(len(feature_class_paths)))

    with get_shp_or_gpkg(out_layer_path, write=True) as out_layer:
        fccount = 0
        for in_layer_path in feature_class_paths:
            fccount += 1
            log.info("Merging feature class {}/{}".format(fccount, len(feature_class_paths)))

            with get_shp_or_gpkg(in_layer_path) as in_layer:
                if boundary is not None:
                    in_layer.SetSpatialFilter(VectorBase.shapely2ogr(boundary))

                # First input spatial ref sets the SRS for the output file
                if fccount == 1:
                    # transform = in_layer.get_transform(out_layer)
                    out_layer.create_layer_from_ref(in_layer)

                for i in range(in_layer.ogr_layer_def.GetFieldCount()):
                    in_field_def = in_layer.ogr_layer_def.GetFieldDefn(i)
                    # Only create fields if we really don't have them
                    # NOTE: THIS ASSUMES ALL FIELDS OF THE SAME NAME HAVE THE SAME TYPE
                    if out_layer.ogr_layer_def.GetFieldIndex(in_field_def.GetName()) == -1:
                        out_layer.ogr_layer.CreateField(in_field_def)

                log.info('Processing feature: {}/{}'.format(fccount, len(feature_class_paths)))

                for feature, _counter, progbar in in_layer.iterate_features('Processing feature'):
                    geom = feature.GetGeometryRef()

                    if geom is None:
                        progbar.erase()  # get around the progressbar
                        log.warning('Feature with FID={} has no geometry. Skipping'.format(feature.GetFID()))
                        continue

                    # geom.Transform(transform)
                    out_feature = ogr.Feature(out_layer.ogr_layer_def)

                    for i in range(in_layer.ogr_layer_def.GetFieldCount()):
                        out_feature.SetField(out_layer.ogr_layer_def.GetFieldDefn(i).GetNameRef(), feature.GetField(i))

                    out_feature.SetGeometry(geom)
                    out_layer.ogr_layer.CreateFeature(out_feature)

    log.info('Merge complete.')
    return fccount


def collect_feature_class(feature_class_path: str,
                          attribute_filter: str = None,
                          clip_shape: BaseGeometry = None,
                          clip_rect: List[float] = None
                          ) -> ogr.Geometry:
    """Collect simple types into Multi types. Does not use Shapely

    Args:
        feature_class_path (str): [description]
        attribute_filter (str, optional): Attribute Query like "HUC = 17060104". Defaults to None.
        clip_shape (BaseGeometry, optional): Iterate over a subset by clipping to a Shapely-ish geometry. Defaults to None.
        clip_rect (List[double minx, double miny, double maxx, double maxy)]): Iterate over a subset by clipping to a Shapely-ish geometry. Defaults to None.

    Raises:
        Exception: [description]

    Returns:
        ogr.Geometry: [description]
    """
    log = Logger('collect_feature_class')
    log.info('Collecting {} feature class.'.format(len(feature_class_path)))

    with get_shp_or_gpkg(feature_class_path) as in_lyr:
        in_geom_type = in_lyr.ogr_layer.GetGeomType()
        output_geom_type = None
        for tp, varr in VectorBase.MULTI_TYPES.items():
            if in_geom_type in varr:
                output_geom_type = tp
                break
        if output_geom_type is None:
            raise Exception('collect_feature_class: Type "{}" not supported'.format(ogr.GeometryTypeToName(in_geom_type)))

        new_geom = ogr.Geometry(output_geom_type)
        for feat, _counter, _progbar in in_lyr.iterate_features('Collecting Geometry', attribute_filter=attribute_filter, clip_rect=clip_rect, clip_shape=clip_shape):
            geom = feat.GetGeometryRef()

            if geom.IsValid() and not geom.IsEmpty():
                if geom.IsMeasured() > 0 or geom.Is3D() > 0:
                    geom.FlattenTo2D()

                # Do the flatten first to speed up the potential transform
                if geom.GetGeometryType() in VectorBase.MULTI_TYPES.keys():
                    sub_geoms = list(geom)
                else:
                    sub_geoms = [geom]
                for subg in sub_geoms:
                    new_geom.AddGeometry(subg)

    log.info('Collect complete.')
    return new_geom


def load_attributes(in_layer_path: str, id_field: str, fields: list) -> dict:
    """
    Load ShapeFile attributes fields into a dictionary keyed by the id_field
    :param network: Full, absolute path to a ShapeFile
    :param id_field: Field that uniquely identifies each feature
    :param fields: List of fields to load into the dictionary
    :return: Dictionary with id_field as key and each feature as dictionary of values keyed by the field name
    """

    # Verify that all the fields are present or throw an exception
    with get_shp_or_gpkg(in_layer_path) as in_layer:
        [in_layer.verify_field(field) for field in fields]

        # Only calculate the combined FIS where all the inputs exist
        # [networkLr.SetAttributeFilter('{} is not null'.format(field)) for field in [veg_field, drain_field, hydq2_field, hydlow_field, length_field, slope_field]]
        # layer.SetAttributeFilter("iGeo_Slope > 0 and iGeo_DA > 0")

        print('{:,} features in polygon ShapeFile {}'.format(in_layer.ogr_layer.GetFeatureCount(), in_layer.filepath))

        feature_values = {}

        for feature, _counter, _progbar in in_layer.iterate_features("loading attributes"):
            reach = feature.GetField(id_field)
            feature_values[reach] = {}

            for field in fields:
                feature_values[reach][field] = feature.GetField(field)

    return feature_values


def load_geometries(in_layer_path: str, id_field: str = None, epsg: int = None, spatial_ref: osr.SpatialReference = None) -> dict:
    """[summary]

    Args:
        in_layer_path (str): [description]
        id_field (str, optional): [description]. Defaults to None.
        epsg (int, optional): [description]. Defaults to None.
        spatial_ref (osr.SpatialReference, optional): [description]. Defaults to None.

    Raises:
        VectorBaseException: [description]

    Returns:
        dict: [description]
    """
    log = Logger('load_geometries')

    if epsg is not None and spatial_ref is not None:
        raise VectorBaseException('Specify either an EPSG or a spatial_ref. Not both')

    with get_shp_or_gpkg(in_layer_path) as in_layer:
        # Determine the transformation if user provides an EPSG
        transform = None
        if epsg is not None:
            _outref, transform = VectorBase.get_transform_from_epsg(in_layer.spatial_ref, epsg)
        elif spatial_ref is not None:
            transform = in_layer.get_transform(in_layer.spatial_ref, spatial_ref)

        features = {}

        for feature, _counter, progbar in in_layer.iterate_features("Loading features"):

            if id_field is None:
                reach = feature.GetFID()
            else:
                reach = feature.GetField(id_field)

            geom = feature.GetGeometryRef()
            geo_type = geom.GetGeometryType()

            new_geom = VectorBase.ogr2shapely(geom, transform=transform)

            if new_geom.is_empty:
                progbar.erase()  # get around the progressbar
                log.warning('Empty feature with FID={} cannot be unioned and will be ignored'.format(feature.GetFID()))
            elif not new_geom.is_valid:
                progbar.erase()  # get around the progressbar
                log.warning('Invalid feature with FID={} cannot be unioned and will be ignored'.format(feature.GetFID()))
            # Filter out zero-length lines
            elif geo_type in VectorBase.LINE_TYPES and new_geom.length == 0:
                progbar.erase()  # get around the progressbar
                log.warning('Zero Length for feature with FID={}'.format(feature.GetFID()))
            # Filter out zero-area polys
            elif geo_type in VectorBase.POLY_TYPES and new_geom.area == 0:
                progbar.erase()  # get around the progressbar
                log.warning('Zero Area for feature with FID={}'.format(feature.GetFID()))
            else:
                features[reach] = new_geom

    return features


def write_attributes(in_layer_path: str, output_values: dict, id_field: str, fields, field_type=ogr.OFTReal, null_values=None):
    """
    Write field values to a feature class
    :param feature_class: Path to feature class
    :param output_values: Dictionary of values keyed by id_field. Each feature is dictionary keyed by field names
    :param id_field: Unique key identifying each feature in both feature class and output_values dictionary
    :param fields: List of fields in output_values to write to
    :return: None
    """

    log = Logger('write_attributes')

    with get_shp_or_gpkg(in_layer_path, write=True) as in_layer:
        # Create each field and store the name and index in a list of tuples
        field_indices = [(field, in_layer.create_field(field, field_type)) for field in fields]  # TODO different field types

        for feature, _counter, _progbar in in_layer.iterate_features("Writing Attributes", write_layers=[in_layer]):
            reach = feature.GetField(id_field)  # TODO Error when id_field is same as FID field .GetFID() seems to work instead
            if reach not in output_values:
                continue

            # Set all the field values and then store the feature
            for field, _idx in field_indices:
                if field in output_values[reach]:
                    if not output_values[reach][field]:
                        if null_values:
                            feature.SetField(field, null_values)
                        else:
                            log.warning('Unhandled feature class value for None type')
                            feature.SetField(field, None)
                    else:
                        feature.SetField(field, output_values[reach][field])
            in_layer.ogr_layer.SetFeature(feature)


def network_statistics(label: str, vector_layer_path: str):

    log = Logger('network_statistics')
    log.info('Network ShapeFile Summary: {}'.format(vector_layer_path))

    results = {}
    total_length = 0.0
    min_length = None
    max_length = None
    invalid_features = 0
    no_geometry = 0

    with get_shp_or_gpkg(vector_layer_path) as vector_layer:

        # Delete output column from network ShapeFile if it exists and then recreate it
        for fieldidx in range(0, vector_layer.ogr_layer_def.GetFieldCount()):
            results[vector_layer.ogr_layer_def.GetFieldDefn(fieldidx).GetName()] = 0

        for feature, _counter, _progbar in vector_layer.iterate_features("Calculating Stats"):
            geom = feature.GetGeometryRef()

            if geom is None:
                no_geometry += 1
                return

            shapely_obj = VectorBase.ogr2shapely(geom)
            length = shapely_obj.length

            if shapely_obj.is_empty or shapely_obj.is_valid is False:
                invalid_features += 1

            total_length += length
            min_length = length if not min_length or min_length > length else min_length
            max_length = length if not max_length or max_length < length else max_length

            for fieldidx in range(0, vector_layer.ogr_layer_def.GetFieldCount()):
                field = vector_layer.ogr_layer_def.GetFieldDefn(fieldidx).GetName()
                if field not in results:
                    results[field] = 0

                results[field] += 0 if feature.GetField(field) else 1

        features = vector_layer.ogr_layer.GetFeatureCount()
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

    return results


def feature_class_bounds(shapefile_path):

    driver = ogr.GetDriverByName("ESRI Shapefile")
    data_source = driver.Open(shapefile_path, 0)
    layer = data_source.GetLayer()
    return layer.GetExtent()


def intersect_feature_classes(feature_class_path1: str, feature_class_path2: str,
                              output_geom_type: int,
                              epsg: int = None,
                              attribute_filter: str = None,
                              ) -> BaseGeometry:
    """Mostly just a wrapper for intersect_geometry_with_feature_class that does a union first

    Args:
        feature_class_path1 (str): [description]
        feature_class_path2 (str): [description]
        epsg (int, optional): [description]. Defaults to None.
        attribute_filter (str, optional): [description]. Defaults to None.

    Returns:
        BaseGeometry: [description]
    """

    union = get_geometry_unary_union(feature_class_path1, epsg)
    return intersect_geometry_with_feature_class(union, feature_class_path2, output_geom_type,
                                                 epsg=epsg,
                                                 attribute_filter=attribute_filter,
                                                 )


def intersect_geometry_with_feature_class(geometry: BaseGeometry, in_layer_path: str,
                                          output_geom_type: int,
                                          epsg: int = None,
                                          attribute_filter: str = None,
                                          ) -> BaseGeometry:
    """[summary]

    Args:
        geometry (BaseGeometry): [description]
        in_layer_path (str): [description]
        out_layer_path (str): [description]
        output_geom_type (int): [description]
        epsg (int, optional): [description]. Defaults to None.
        attribute_filter (str, optional): [description]. Defaults to None.

    Raises:
        VectorBaseException: [description]
        VectorBaseException: [description]

    Returns:
        BaseGeometry: [description]
    """
    log = Logger('intersect_geometry_with_feature_class')
    if output_geom_type not in [ogr.wkbMultiPoint, ogr.wkbMultiLineString, ogr.wkbMultiPolygon]:
        raise VectorBaseException('Unsupported ogr type for geometry intersection: "{}"'.format(output_geom_type))

    log.debug('Intersection with feature class: Performing unary union on input: {}'.format(in_layer_path))
    geom_union = get_geometry_unary_union(in_layer_path, epsg=epsg, attribute_filter=attribute_filter, clip_shape=geometry)

    # Nothing to do if there were no features in the feature class
    if not geom_union:
        return

    log.debug('Finding intersections (may take a few minutes)...')
    tmr = Timer()
    geom_inter = geometry.intersection(geom_union)
    log.debug('Intersection done in {:.1f} seconds'.format(tmr.ellapsed()))

    # Nothing to do if the intersection is empty
    if geom_inter.is_empty:
        return

    # Single features and collections need to be converted into Multi-features
    if output_geom_type == ogr.wkbMultiPoint and not isinstance(geom_inter, MultiPoint):
        if isinstance(geom_inter, Point):
            geom_inter = MultiPoint([(geom_inter)])

        elif isinstance(geom_inter, LineString):
            # Break this linestring down into vertices as points
            geom_inter = MultiPoint([geom_inter.coords[0], geom_inter.coords[-1]])

        elif isinstance(geom_inter, MultiLineString):
            # Break this linestring down into vertices as points
            geom_inter = MultiPoint(reduce(lambda acc, ls: acc + [ls.coords[0], ls.coords[-1]], list(geom_inter.geoms), []))
        elif isinstance(geom_inter, GeometryCollection):
            geom_inter = MultiPoint([geom for geom in geom_inter.geoms if isinstance(geom, Point)])

    elif output_geom_type == ogr.wkbMultiLineString and not isinstance(geom_inter, MultiLineString):
        if isinstance(geom_inter, LineString):
            geom_inter = MultiLineString([(geom_inter)])
        else:
            raise VectorBaseException('Unsupported ogr type: "{}" does not match shapely type of "{}"'.format(output_geom_type, geom_inter.type))

    return geom_inter


def buffer_by_field(in_layer_path: str, out_layer_path, field: str, epsg: int = None, min_buffer=0.0, centered=False) -> None:
    """generate buffered polygons by value in field

    Args:
        flowlines (str): feature class of line features to buffer
        field (str): field with buffer value
        epsg (int): output srs
        min_buffer: use this buffer value for field values that are less than this

    Returns:
        geometry: unioned polygon geometry of buffered lines
    """
    log = Logger('buffer_by_field')

    with get_shp_or_gpkg(out_layer_path, write=True) as out_layer, get_shp_or_gpkg(in_layer_path) as in_layer:
        conversion = in_layer.rough_convert_metres_to_vector_units(1)

        # Add input Layer Fields to the output Layer if it is the one we want
        out_layer.create_layer(ogr.wkbPolygon, epsg=epsg, fields=in_layer.get_fields())
        # out_layer.create_field(f'{field}_actual_buffer', ogr.OFTReal)

        transform = VectorBase.get_transform(in_layer.spatial_ref, out_layer.spatial_ref)

        factor = 0.5 if centered else 1.0
        min_buffer_converted = min_buffer * conversion * factor if min_buffer else 0.0

        for feature, _counter, progbar in in_layer.iterate_features('Buffering features', write_layers=[out_layer]):
            geom = feature.GetGeometryRef()

            if geom is None:
                progbar.erase()  # get around the progressbar
                log.warning('Feature with FID={} has no geometry. Skipping'.format(feature.GetFID()))
                continue

            buffer_dist = feature.GetField(field) * conversion * factor if field is not None else 0.0
            geom.Transform(transform)
            buffer_value = buffer_dist if buffer_dist > min_buffer_converted else min_buffer_converted
            geom_buffer = geom.Buffer(buffer_value)

            # Create output Feature
            out_feature = ogr.Feature(out_layer.ogr_layer_def)
            out_feature.SetGeometry(geom_buffer)
            # out_feature.SetField(f'{field}_actual_buffer', buffer_value)

            # Add field values from input Layer
            for i in range(0, out_layer.ogr_layer_def.GetFieldCount()):
                out_feature.SetField(out_layer.ogr_layer_def.GetFieldDefn(i).GetNameRef(), feature.GetField(i))

            out_layer.ogr_layer.CreateFeature(out_feature)
            out_feature = None


def polygonize(raster_path: str, band: int, out_layer_path: str, epsg: int = None):
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
    with get_shp_or_gpkg(out_layer_path, write=True) as out_layer:
        out_layer.create_layer(ogr.wkbPolygon, epsg=epsg)

        src_ds = gdal.Open(raster_path)
        src_band = src_ds.GetRasterBand(band)

        out_layer.create_field('id', field_type=type_mapping[src_band.DataType])

        progbar = ProgressBar(100, 50, "Polygonizing raster")

        def poly_progress(progress, _msg, _data):
            # double dfProgress, char const * pszMessage=None, void * pData=None
            progbar.update(int(progress * 100))

        gdal.Polygonize(src_band, src_ds.GetRasterBand(band), out_layer.ogr_layer, 0, [], callback=poly_progress)
        progbar.finish()

    src_ds = None


def remove_holes(geom: BaseGeometry, min_hole_area: float) -> BaseGeometry:
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
        raise VectorBaseException('Invalid geometry type used for "remove_holes": {}'.format(type(geom)))


def get_num_pts(geom: BaseGeometry) -> int:
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


def get_num_rings(geom: BaseGeometry) -> int:
    """Helper function for counting rings

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


def export_geojson(geom: BaseGeometry, props=None):
    new_props = copy(props) if props is not None else {}
    the_dict = {
        "type": "FeatureCollection",
        "features": [
        ]
    }
    if geom is not None:
        the_dict["features"].append({
            "type": "Feature",
            "properties": new_props,
            "geometry": mapping(geom),
        })

    return the_dict


def dissolve_feature_class(in_layer_path, out_layer_path, epsg):

    out_geom = get_geometry_unary_union(in_layer_path)

    with get_shp_or_gpkg(out_layer_path, write=True) as out_layer, \
            get_shp_or_gpkg(in_layer_path) as in_layer:

        # Add input Layer Fields to the output Layer if it is the one we want
        out_layer.create_layer_from_ref(in_layer, epsg=epsg)
        out_layer.create_feature(out_geom)


def remove_holes_feature_class(in_layer_path, out_layer_path, min_hole_area=None):

    with get_shp_or_gpkg(out_layer_path, write=True) as out_layer, \
            get_shp_or_gpkg(in_layer_path) as in_layer:
        # Add input Layer Fields to the output Layer if it is the one we want
        out_layer.create_layer_from_ref(in_layer)
        out_layer_defn = out_layer.ogr_layer.GetLayerDefn()
        for feat, _counter, progbar in in_layer.iterate_features():
            geom = feat.GetGeometryRef()
            s_geom = VectorBase.ogr2shapely(geom)
            out_s_geom = remove_holes(s_geom, min_hole_area)
            out_geom = VectorBase.shapely2ogr(out_s_geom)
            out_feat = ogr.Feature(out_layer_defn)
            out_feat.SetGeometry(out_geom)
            for i in range(0, out_layer.ogr_layer_def.GetFieldCount()):
                out_feat.SetField(out_layer.ogr_layer_def.GetFieldDefn(i).GetNameRef(), feat.GetField(i))
            out_layer.ogr_layer.CreateFeature(out_feat)


def intersection(layer_path1, layer_path2, out_layer_path, epsg):

    log = Logger('feature_class_intersection')
    with get_shp_or_gpkg(out_layer_path, write=True) as out_layer, \
            get_shp_or_gpkg(layer_path1) as layer1, \
            get_shp_or_gpkg(layer_path2) as layer2:

        out_layer.create_layer_from_ref(layer2, epsg=epsg)
        out_layer_defn = out_layer.ogr_layer.GetLayerDefn()

        # layer1.ogr_layer.GetGeomType()
        # create an empty geometry of the same type
        union1 = ogr.Geometry(3)
        # union all the geometrical features of layer 1
        for feat, _counter, progbar in layer1.iterate_features():
            geom = feat.GetGeometryRef()
            union1 = union1.Union(geom)
        for feat, _counter, progbar in layer2.iterate_features():
            geom = feat.GetGeometryRef()
            intersection = union1.Intersection(geom)
            if intersection.IsValid():
                # out_layer.create_feature(intersection)
                out_feat = ogr.Feature(out_layer_defn)
                out_feat.SetGeometry(intersection)
                for i in range(0, out_layer.ogr_layer_def.GetFieldCount()):
                    out_feat.SetField(out_layer.ogr_layer_def.GetFieldDefn(i).GetNameRef(), feat.GetField(i))

                out_layer.ogr_layer.CreateFeature(out_feat)
