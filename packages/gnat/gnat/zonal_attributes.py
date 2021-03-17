""" Tools for summarizing features by polygon zones

        Author: Kelly Whitehead

        Date: Feb 26 2021

    """

import os
import typing
import argparse

import ogr

from gnat.gnat import gnat_database, write_gnat_attributes
from rscommons import GeopackageLayer, get_shp_or_gpkg, dotenv, Logger, ProgressBar, VectorBase

Path = typing.Union[str, bytes, os.PathLike]

geom_attribute_names = {'COUNT': VectorBase.POINT_TYPES,
                        'LENGTH': VectorBase.LINE_TYPES,
                        'AREA': VectorBase.POLY_TYPES}


def zonal_intersect(polygon_zones: Path, attribute_features: Path, summary_fields: list = None, epsg: int = None) -> dict:
    """Find zonal attributes

        Points: count for each class in each field
        Lines: Length for each class in each field
        Polygons: Area for each class in each field

    Args:
        polygon_zones (Path): Zonal polygons to summarize attributes
        attribute_features (Path): features with attributes
        summary_fields (list, optional): list of fields to summarize. Defaults to None.
        epsg (int, optional): epsg to transform featurs

    Returns:
        dict: dictionary of geom ids with attribute counts, lengths or areas
    """

    with GeopackageLayer(os.path.dirname(polygon_zones), layer_name=os.path.basename(polygon_zones)) as lyr_zonal, \
            GeopackageLayer(os.path.dirname(attribute_features), layer_name=os.path.basename(attribute_features)) as lyr_attributes:

        # Initialize outputs
        outputs = {}
        geom_type = lyr_attributes.ogr_geom_type
        geom_attribue_name = next(k for k, value in geom_attribute_names.items() if geom_type in value)

        if epsg:
            _srs, transform_zonal = VectorBase.get_transform_from_epsg(lyr_zonal.spatial_ref, epsg)
            _srs, transform_attrib = VectorBase.get_transform_from_epsg(lyr_attributes.spatial_ref, epsg)

        # Iterate polygon zones
        for feat_zonal, *_ in lyr_zonal.iterate_features("Summarizing attributes by zone"):
            zonal_id = feat_zonal.GetFID()
            geom_zonal = feat_zonal.GetGeometryRef()
            if epsg:
                geom_zonal.Transform(transform_zonal)

            intersected_attributes = {geom_attribue_name: 0, "ZONE_AREA": geom_zonal.GetArea()}

            # Gather Intersected feats and thier attributes
            for feat_attribute, *_ in lyr_attributes.iterate_features():
                geom_attribute = feat_attribute.GetGeometryRef()

                if epsg:
                    geom_attribute.Transform(transform_attrib)

                if geom_zonal.Intersects(geom_attribute):
                    intersected_geom = geom_zonal.Intersection(geom_attribute)
                    if intersected_geom.GetGeometryType() in VectorBase.POINT_TYPES:
                        value = 1
                    if intersected_geom.GetGeometryType() in VectorBase.POLY_TYPES:
                        value = intersected_geom.Area()
                    if intersected_geom.GetGeometryType() in VectorBase.LINE_TYPES:
                        value = intersected_geom.Length()

                    if summary_fields:
                        for field in summary_fields:
                            attribute = feat_attribute.GetField(field)
                            if field in intersected_attributes:
                                intersected_attributes[field][attribute] = intersected_attributes[field][attribute] + value if attribute in intersected_attributes[field] else value
                            else:
                                intersected_attributes[field] = {attribute: value}

                    intersected_attributes[geom_attribue_name] = intersected_attributes[geom_attribue_name] + value

            outputs[zonal_id] = intersected_attributes

    return outputs


def summerize_attributes(reaches: dict, fields: list = None):
    """summerize tabulated zonal counts

    Args:
        reaches (dict): [description]
        fields (list, optional): [description]. Defaults to None.

    Returns:
        dict: summarized attributes
    """

    outputs = {}

    for reach_id, reach in reaches.items():
        output = {}
        for field, field_values in reach.items():
            if field in [*geom_attribute_names.keys(), 'ZONE_AREA']:
                continue
            if fields is None or field in fields:
                output_values = {}
                output_values['MAX'] = max(field_values, key=field_values.get)
                output_values['MIN'] = min(field_values, key=field_values.get)
                output_values['SUM'] = sum(field_values.values())
                if [type(k) for k in field_values.keys()][0] in [int, float]:
                    output_values['WEIGHTED-MEAN'] = sum([k * v for k, v in field_values.items()]) / sum(field_values.values())
                output[field] = output_values
        outputs[reach_id] = output

    return outputs


if __name__ == "__main__":

    parser = argparse.ArgumentParser(description='Zonal Summary Tool')

    parser.add_argument('huc', help='HUC identifier', type=str)
    parser.add_argument('zonal_polygons', help="polygon representing confining boundary (.shp, .gpkg/layer_name)", type=str)
    parser.add_argument('attribute_features', help='feature class with attributes to generate ', type=str)
    parser.add_argument('metrics', type=str)
    parser.add_argument('output_gpkg', type=str)
    parser.add_argument('--epsg', type=int, default=None)

    args = dotenv.parse_args_env(parser)
    metrics = args.metrics.split(',')
    summary_fields = [m.split('_')[0] for m in metrics]

    zones = gnat_database(args.output_gpkg, args.zonal_polygons, True)

    zonal_tabulation = zonal_intersect(zones, args.attribute_features, summary_fields, args.epsg)
    summarized_outputs = summerize_attributes(zonal_tabulation, summary_fields)

    write_gnat_attributes(args.output_gpkg, summarized_outputs, metrics)
