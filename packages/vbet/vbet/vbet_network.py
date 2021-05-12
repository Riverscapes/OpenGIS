# -------------------------------------------------------------------------------
# Name:     Valley Bottom
#
# Purpose:  Perform initial VBET analysis that can be used by the BRAT conservation
#           module
#
# Author:   Philip Bailey
#
# Date:     7 Oct 2019
#
# https://nhd.usgs.gov/userGuide/Robohelpfiles/NHD_User_Guide/Feature_Catalog/Hydrography_Dataset/Complete_FCode_List.htm
# -------------------------------------------------------------------------------
import os
import sqlite3
from typing import List
from osgeo import ogr
from shapely.geometry.base import BaseGeometry
from rscommons import Logger, VectorBase, GeopackageLayer
from rscommons.vector_ops import get_geometry_unary_union
from rscommons import get_shp_or_gpkg


def vbet_network(flow_lines_path: str, flow_areas_path: str, out_path: str, epsg: int = None, fcodes: List[str] = None):

    log = Logger('VBET Network')
    log.info('Generating perennial network')
    fcodes = ["46006"] if fcodes is None else fcodes

    with get_shp_or_gpkg(out_path, write=True) as vbet_net, \
            get_shp_or_gpkg(flow_lines_path) as flow_lines_lyr:

        # Add input Layer Fields to the output Layer if it is the one we want
        vbet_net.create_layer_from_ref(flow_lines_lyr, epsg=epsg)

        # Perennial features
        log.info('Incorporating perennial features')
        fcode_filter = "FCode = " + " or FCode = ".join([f"'{fcode}'" for fcode in fcodes]) if len(fcodes) > 0 else ""  # e.g. "FCode = '46006' or FCode = '55800'"
        fids = include_features(flow_lines_lyr, vbet_net, fcode_filter)

        # Flow area features
        polygon = get_geometry_unary_union(flow_areas_path, epsg=epsg)
        if polygon is not None:
            log.info('Incorporating flow areas.')
            include_features(flow_lines_lyr, vbet_net, "FCode <> '46006'", polygon, excluded_fids=fids)

        fcount = flow_lines_lyr.ogr_layer.GetFeatureCount()

        log.info('VBET network generated with {} features'.format(fcount))


def include_features(source_layer: VectorBase, out_layer: VectorBase, attribute_filter: str = None, clip_shape: BaseGeometry = None, excluded_fids: list = None):

    included_fids = []
    excluded_fids = [] if excluded_fids is None else excluded_fids
    for feature, _counter, _progbar in source_layer.iterate_features('Including Features', write_layers=[out_layer], attribute_filter=attribute_filter, clip_shape=clip_shape):
        out_feature = ogr.Feature(out_layer.ogr_layer_def)

        if feature.GetFID() not in excluded_fids:

            included_fids.append(feature.GetFID())

            # Add field values from input Layer
            for i in range(0, out_layer.ogr_layer_def.GetFieldCount()):
                out_feature.SetField(out_layer.ogr_layer_def.GetFieldDefn(i).GetNameRef(), feature.GetField(i))

            geom = feature.GetGeometryRef()
            out_feature.SetGeometry(geom.Clone())
            out_layer.ogr_layer.CreateFeature(out_feature)

    return included_fids


def create_drainage_area_zones(catchment_layer, flowlines_layer, join_field, copy_field, zones, out_layer):

    # Load drainage area
    with sqlite3.connect(os.path.dirname(flowlines_layer)) as conn:
        cursor = conn.cursor()
        join_data = cursor.execute(f"""SELECT {join_field}, {copy_field} FROM {os.path.basename(flowlines_layer)}""").fetchall()
        data = {int(value[0]): value[1] for value in join_data}

    with GeopackageLayer(os.path.dirname(catchment_layer), layer_name=os.path.basename(catchment_layer)) as lyr_source, \
            GeopackageLayer(os.path.dirname(out_layer), layer_name=os.path.basename(out_layer), write=True) as lyr_destination:

        # Create Output
        srs = lyr_source.spatial_ref
        lyr_destination.create_layer(ogr.wkbPolygon, spatial_ref=srs)
        lyr_destination.create_field(copy_field, field_type=ogr.OFTReal)
        for attribute_type in zones:
            lyr_destination.create_field(f'{attribute_type}_Zone', field_type=ogr.OFTInteger)

        out_layer_defn = lyr_destination.ogr_layer.GetLayerDefn()
        lyr_destination.ogr_layer.StartTransaction()
        # Build Zones
        for feat_source, *_ in lyr_source.iterate_features("Joining attributes"):
            join_id = int(feat_source.GetField(join_field))

            if join_id in data:

                geom = feat_source.GetGeometryRef()
                feat_dest = ogr.Feature(out_layer_defn)
                feat_dest.SetFID(join_id)
                feat_dest.SetGeometry(geom)

                feat_dest.SetField(copy_field, data[join_id])  # Set drainage area
                if data[join_id]:  # if the drainage area is not null

                    out_zones = {}
                    for zone_type, zone_values in zones.items():
                        for i, value in zone_values.items():
                            if value:
                                if data[join_id] < value:
                                    out_zones[zone_type] = i
                                    break
                            else:
                                out_zones[zone_type] = i

                    for zone_field, zone_value in out_zones.items():
                        feat_dest.SetField(f'{zone_field}_Zone', zone_value)

                lyr_destination.ogr_layer.CreateFeature(feat_dest)
        lyr_destination.ogr_layer.CommitTransaction()


def copy_vaa_attributes(destination_layer, vaa_table):

    with sqlite3.connect(os.path.dirname(vaa_table)) as conn_vaa, \
            sqlite3.connect(os.path.dirname(destination_layer)) as conn_dest:
        curs = conn_dest.cursor()

        for line in conn_vaa.iterdump():

            curs.execute(line)

        curs.execute(f"INSERT INTO gpkg_contents (table_name, data_type) VALUES ('{os.path.basename(vaa_table)}', 'attributes');")
        conn_dest.commit()

    return os.path.basename(vaa_table)


def join_attributes(gpkg, name, geom_layer, attribute_layer, join_field, fields, epsg):

    sql = f"CREATE VIEW {name} AS SELECT G.*, {','.join(['A.' + item for item in fields])} FROM {geom_layer} G INNER JOIN {attribute_layer} A ON G.{join_field} = A.{join_field};"

    with sqlite3.connect(gpkg) as conn:

        curs = conn.cursor()
        curs.execute(sql)
        conn.commit()

        curs.execute(f"INSERT INTO gpkg_contents (table_name, identifier, data_type, srs_id) VALUES ('{name}', '{name}', 'features', {epsg});")
        conn.commit()

        curs.execute(f"INSERT INTO gpkg_geometry_columns (table_name, column_name, geometry_type_name, srs_id, z, m) values ('{name}', 'geom', 'LINESTRING', {epsg}, 0, 0);")
        conn.commit()

    return os.path.join(gpkg, name)
