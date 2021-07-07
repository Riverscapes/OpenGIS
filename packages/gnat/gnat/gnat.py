import os
import sqlite3

import ogr

from rscommons import GeopackageLayer
from rscommons.util import safe_makedirs, safe_remove_dir, parse_metadata
from rscommons.database import load_lookup_data


class GNAT:

    def __init__(self, database_path, vb_polygons, inputs):

        # database
        self.database = database_path
        self.inputs = inputs
        self.riverscapes_features = self.create_database(vb_polygons, True)

        self.run_metric = self._check_metrics()

        # # inputs
        # self.flowlines = inputs['flowlines'] if 'flowlines' in inputs else None
        # self.segmented_flowlines = inputs['segmented_flowlines'] if 'segmented_flowlines' in inputs else None
        # self.dem = inputs['dem'] if 'dem' in inputs else None
        # self.flow_regime_polygons = inputs['flow_regime_polygons'] if 'flow_regime_polygons' in inputs else None
        # self.eco_region_polygons = inputs['ecoregion_polygons'] if 'ecoregion_polygons' in inputs else None
        # self.valley_bottom_centerline = inputs['valley_bottom_centerline'] if 'valley_bottom_centerline' in inputs else None

    def _check_metrics(self):

        out_metrics = {}

        with sqlite3.connect(self.database) as conn:
            conn.execute('pragma foreign_keys=ON')
            curs = conn.cursor()
            metrics = curs.execute("""SELECT attribute_id, attribute_name FROM attributes""").fetchall()

            for metric in metrics:
                metric_inputs = curs.execute("""SELECT input_name FROM attributes
                                    INNER JOIN attribute_inputs ON attributes.attribute_id = attribute_inputs.attribute_id
                                    INNER JOIN inputs ON attribute_inputs.input_id = inputs.input_id
                                    WHERE attributes.attribute_id = ?""", [metric[0]]).fetchall()
                out_metrics[metric[1]] = all([metric_input[0] in self.inputs for metric_input in metric_inputs])

        return out_metrics

    def create_database(self, in_layer, overwrite_existing=False):
        """generates (if needed) and loads layer to gnat database

        Args:
            gnat_gpkg ([type]): [description]
            in_layer ([type]): [description]

        Returns:
            [type]: [description]
        """

        schema_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'database')

        if overwrite_existing:
            safe_remove_dir(self.database)

        if not os.path.exists(self.database):
            with GeopackageLayer(in_layer) as lyr_inputs, \
                    GeopackageLayer(self.database, layer_name='riverscapes', write=True) as lyr_outputs:

                srs = lyr_inputs.ogr_layer.GetSpatialRef()

                lyr_outputs.create_layer(ogr.wkbPolygon, spatial_ref=srs, options=['FID=riverscape_id'], fields={
                    'area_sqkm': ogr.OFTReal,
                    'river_style_id': ogr.OFTInteger
                })

                for feat, *_ in lyr_inputs.iterate_features("Copying Riverscapes Features"):
                    geom = feat.GetGeometryRef()
                    fid = feat.GetFID()
                    area = geom.GetArea()  # TODO calculate area as sqkm

                    out_feature = ogr.Feature(lyr_outputs.ogr_layer_def)
                    out_feature.SetGeometry(geom)
                    out_feature.SetFID(fid)
                    out_feature.SetField('area_sqkm', area)

                    lyr_outputs.ogr_layer.CreateFeature(out_feature)
                    out_feature = None

            # log.info('Creating database schema at {0}'.format(database))
            qry = open(os.path.join(schema_path, 'gnat_schema.sql'), 'r').read()
            sqlite3.complete_statement(qry)
            conn = sqlite3.connect(self.database)
            conn.execute('PRAGMA foreign_keys = ON;')
            curs = conn.cursor()
            curs.executescript(qry)

            load_lookup_data(self.database, schema_path)

        out_layer = os.path.join(self.database, 'riverscapes')

        return out_layer

    def write_gnat_attributes(self, attribute_values: dict, attribute_names: list, set_null_first=False):

        if len(attribute_values) < 1:
            return

        conn = sqlite3.connect(self.database)
        conn.execute('pragma foreign_keys=ON')
        curs = conn.cursor()

        # Optionally clear all the values in the fields first
        # if set_null_first is True:
        #     [curs.execute(f'UPDATE {table_name} SET {field} = NULL') for field in fields]

        for attribute in attribute_names:

            sql = f"""SELECT attribute_id, fields.field_name, summary_methods.method_name from attributes
                    INNER JOIN fields ON attributes.field_id = fields.field_id
                    INNER JOIN summary_methods ON attributes.method_id = summary_methods.method_id where attribute_name = ?"""
            attribute_id, fieldname, summary = curs.execute(sql, [attribute]).fetchone()

            sql = f'INSERT INTO riverscape_attributes (riverscape_id, attribute_id, value) VALUES(?,?,?)'
            curs.executemany(sql, [(reach_id, attribute_id, value[fieldname][summary]) for reach_id, value in attribute_values.items() if fieldname in value.keys()])
            conn.commit()
