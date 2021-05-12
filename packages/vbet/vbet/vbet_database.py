import os
import sqlite3
import numpy as np
from scipy import interpolate

from rscommons.database import load_lookup_data


def build_vbet_database(database):

    database_folder = os.path.join(os.path.abspath(os.path.dirname(__file__)), '..', 'database')
    with sqlite3.connect(database) as conn:
        cursor = conn.cursor()
        with open(os.path.join(database_folder, 'vbet_schema.sql')) as sqlfile:
            sql_commands = sqlfile.read()
            cursor.executescript(sql_commands)
            conn.commit()

    # Load tables
    load_lookup_data(database, database_folder)


def load_configuration(machine_code, database):

    conn = sqlite3.connect(database)
    conn.execute('pragma foreign_keys=ON')
    curs = conn.cursor()

    configuration = {}

    # 1 Get inputs
    inputs = curs.execute(""" SELECT inputs.name, inputs.input_id, scenario_input_id FROM scenarios
                               INNER JOIN scenario_inputs ON scenarios.scenario_id = scenario_inputs.scenario_id
                               INNER JOIN inputs ON scenario_inputs.input_id = inputs.input_id
                               WHERE machine_code = ?;""", [machine_code]).fetchall()

    inputs_dict = {}
    for input_value in inputs:

        zones = curs.execute("""SELECT transform_id, min_da, max_da FROM input_zones WHERE scenario_input_id = ?""", [input_value[2]]).fetchall()

        transform_zones = {}
        for zone in zones:
            transform_zones[zone[0]] = {'min': zone[1], 'max': zone[2]}

        inputs_dict[input_value[0]] = {'input_id': input_value[1], 'transform_zones': transform_zones}

    configuration['Inputs'] = inputs_dict

    transforms_dict = {}
    for input_name, val in configuration['Inputs'].items():
        input_transforms = []
        for i, transform_id in enumerate(val['transform_zones']):
            transform_type = curs.execute("""SELECT transform_types.name from transforms INNER JOIN transform_types ON transform_types.type_id = transforms.type_id where transforms.transform_id = ?""", [transform_id]).fetchone()[0]
            values = curs.execute("""SELECT input_value, output_value FROM inflections WHERE transform_id = ? ORDER BY input_value """, [transform_id]).fetchall()

            if transform_type == "Polynomial":
                # add polynomial function
                transforms_dict[transform_id] = None

            input_transforms.append(interpolate.interp1d(np.array([v[0] for v in values]), np.array([v[1] for v in values]), kind=transform_type, bounds_error=False, fill_value=0.0))
        transforms_dict[input_name] = input_transforms

    configuration['Transforms'] = transforms_dict

    zones_dict = {}
    for input_name, input_zones in configuration["Inputs"].items():

        if len(input_zones['transform_zones']) > 1:
            zone = {}
            for i, zone_values in enumerate(input_zones['transform_zones'].values()):
                zone[i] = zone_values['max']
            zones_dict[input_name] = zone

    configuration['Zones'] = zones_dict

    return configuration
