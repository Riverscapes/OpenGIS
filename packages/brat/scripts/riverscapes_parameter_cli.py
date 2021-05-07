"""Rudimentary command line interface for entering 
    riverscapes parameters into postgres database.

    This initial version really only stores BRAT parameters,
    but could be extended for other riverscapes tools.

    Philip Bailey
    May 2021
    """
import os
import pickle
import inquirer
import psycopg2
from psycopg2.extras import RealDictCursor
from rscommons import dotenv

# File name used to pickle the last used watershed ID
WATERSHED_PICKLE_FILE = 'riverscapes_parameters.pickle'


def watershed_info(cinfo):
    """ Watershed menu for defining hydrologic equations,
    max drainage and watershed hydro parameters
    """

    # Retrieve any stored watershed ID
    watershed_id = ''
    if os.path.isfile(WATERSHED_PICKLE_FILE):
        with open(WATERSHED_PICKLE_FILE, 'rb') as infile:
            watershed_id = pickle.load(infile)

    # Ask the user for the watershed they want to work on
    watershed_id = inquirer.text(message='Watershed ID', default=watershed_id)

    # Pickle the watershed ID for next time
    with open(WATERSHED_PICKLE_FILE, 'wb') as outfile:
        pickle.dump(watershed_id, outfile)

    pgconn = psycopg2.connect(user=cinfo['user'], password=cinfo['word'], host=cinfo['host'], port=cinfo['port'], database=cinfo['pgdb'])
    pgcurs = pgconn.cursor(cursor_factory=RealDictCursor)

    choice = ''
    while not choice.startswith('6'):
        pgcurs.execute('SELECT * FROM watersheds WHERE watershed_id = %s', [watershed_id])
        watershed = pgcurs.fetchone()
        print('\n--- Watershed Info ---')
        print('        HUC8:', watershed['watershed_id'])
        print('   Watershed:', watershed['name'])
        print('      States:', watershed['states'])
        print('        QLow:', watershed['qlow'])
        print('          Q2:', watershed['q2'])
        print('Max Drainage:', watershed['max_drainage'])
        print('     Updated:', watershed['updated_on'].strftime("%d %b %Y, %H:%M:%S"))
        print('\n--- Hydro Params ---')

        pgcurs.execute('SELECT * FROM vw_watershed_hydro_params where watershed_id = %s', [watershed_id])
        for row in pgcurs.fetchall():
            print('{}:'.format(row['name']), row['value'], 'data units:', row['data_units'], 'equation units:', row['equation_units'], 'conversion', row['conversion'])

        choice = inquirer.list_input('What do you want to do?',
                                     choices=[
                                         '1. Q2 equation',
                                         '2. QLow equation',
                                         '3. Max drainage',
                                         '4. Add or edit hydro params for this watershed',
                                         '5. Remove a hydro param for this watershed',
                                         '6. Return to main menu'])

        if choice.startswith('1') or choice.startswith('2'):
            equation_type = 'q2' if choice.startswith('1') else 'qlow'
            update_equation(cinfo, equation_type, watershed_id, watershed[equation_type])

        elif choice.startswith('3'):
            update_max_drainage(cinfo, watershed_id, watershed['max_drainage'])

        elif choice.startswith('4'):
            update_watershed_params(cinfo, watershed_id)

        elif choice.startswith('5'):
            delete_watershed_hydro_param(cinfo, watershed_id)


def update_equation(cinfo, equation_type, watershed_id, existing_value):

    new_equation = inquirer.text(message='{} equation'.format(equation_type), default=existing_value)

    if existing_value is not None and new_equation.lower() == existing_value.lower():
        print('Equation unchanged. No action taken.')
        return

    pgconn = psycopg2.connect(user=cinfo['user'], password=cinfo['word'], host=cinfo['host'], port=cinfo['port'], database=cinfo['pgdb'])
    pgcurs = pgconn.cursor(cursor_factory=RealDictCursor)
    pgcurs.execute('UPDATE watersheds SET {} = %s WHERE watershed_id = %s'.format(equation_type), [new_equation, watershed_id])
    pgconn.commit()


def update_watershed_params(cinfo, watershed_id):

    param_name = inquirer.text(message='Hydro parameter abbreviation')
    if param_name is None or len(param_name) < 1:
        return

    pgconn = psycopg2.connect(user=cinfo['user'], password=cinfo['word'], host=cinfo['host'], port=cinfo['port'], database=cinfo['pgdb'])
    pgcurs = pgconn.cursor(cursor_factory=RealDictCursor)
    pgcurs.execute("""SELECT p.*, w.value
                      FROM hydro_params p
                      LEFT JOIN (select * from watershed_hydro_params where watershed_id = %s) w on p.param_id = w.param_id
                      WHERE (name ilike %s)""", [watershed_id, param_name])
    param = pgcurs.fetchone()
    if param is None:
        print('Hydro Parameter with name "{}" does not exist. Return to main menu to add it.'.format(param_name))
        return

    value = inquirer.text(message='Value for {} for watershed {} ({})'.format(param['name'], watershed_id, param['data_units']), default=param['value'])
    if value is None or len(value) < 1:
        print('No value. No action taken.')
        return

    pgcurs.execute("""INSERT INTO watershed_hydro_params (watershed_id, param_id, value)
                      VALUES (%s, %s, %s)
                      ON CONFLICT ON CONSTRAINT pk_watesrhed_hydro_params
                      DO UPDATE SET value = %s""", [watershed_id, param['param_id'], float(value), float(value)])
    pgconn.commit()


def delete_watershed_hydro_param(cinfo, watershed_id):

    pgconn = psycopg2.connect(user=cinfo['user'], password=cinfo['word'], host=cinfo['host'], port=cinfo['port'], database=cinfo['pgdb'])
    pgcurs = pgconn.cursor(cursor_factory=RealDictCursor)
    pgcurs.execute('SELECT p.param_id, p.name, w.value FROM hydro_params p INNER JOIN watershed_hydro_params w on p.param_id = w.param_id WHERE (watershed_id = %s)', [watershed_id])
    choices = ['{}, {} ({})'.format(row['name'], row['value'], row['param_id']) for row in pgcurs.fetchall()]
    choices.append('Exit')

    param = inquirer.list_input('Which hydro parameter for {} do you want to remove?'.format(watershed_id), choices=choices)
    if param.lower() == 'exit':
        return

    pgcurs.execute('DELETE FROM watershed_hydro_params WHERE watershed_id = %s AND param_id =%s', [watershed_id, int(param[param.index('(') + 1:param.index(')')])])
    pgconn.commit()


def update_max_drainage(cinfo, watershed_id, existing_value):

    new_value = inquirer.text(message='Max draiange (sqkm)', default=existing_value)

    if existing_value is not None and new_value.lower() == existing_value.lower():
        print('Max drainage unchanged. No action taken.')
        return

    pgconn = psycopg2.connect(user=cinfo['user'], password=cinfo['word'], host=cinfo['host'], port=cinfo['port'], database=cinfo['pgdb'])
    pgcurs = pgconn.cursor(cursor_factory=RealDictCursor)
    pgcurs.execute('UPDATE watersheds SET max_drainage = %s WHERE watershed_id = %s', [new_value, watershed_id])
    pgconn.commit()


def hydro_params(cinfo):

    choice = ''
    while not choice.startswith('3'):

        pgconn = psycopg2.connect(user=cinfo['user'], password=cinfo['word'], host=cinfo['host'], port=cinfo['port'], database=cinfo['pgdb'])
        pgcurs = pgconn.cursor(cursor_factory=RealDictCursor)
        pgcurs.execute('SELECT * FROM hydro_params  ORDER BY name')
        [print(row['name'], ', data units:', row['data_units'], ', equation units:', row['equation_units']) for row in pgcurs.fetchall()]

        choice = inquirer.list_input('What do you want to do?', choices=[
            '1. Add Hydro Parameter',
            '2. Delete hydro parameter',
            '3. Exit'])

        if choice.startswith('1'):
            add_hydro_parameter(cinfo)

        if choice.startswith('2'):
            delete_hydro_parameter(cinfo)


def add_hydro_parameter(cinfo):

    responses = inquirer.prompt([
        inquirer.Text('name', message='Parameter name (e.g. ELEV)'),
        inquirer.Text('description', message='Description'),
        inquirer.Text('data_units', message='Data units'),
        inquirer.Text('equation_units', message='Equation units'),
        inquirer.Text('conversion', message='Conversion factor', default='1'),
        inquirer.Text('definition', message='definition')
    ])

    for name, val in responses.items():
        print('{}: {}'.format(name, val))
        if len(val) < 1:
            responses[name] = None

    proceed = inquirer.confirm('Do you want to proceed and save the new hydro parameter?')
    if proceed is not True:
        return

    pgconn = psycopg2.connect(user=cinfo['user'], password=cinfo['word'], host=cinfo['host'], port=cinfo['port'], database=cinfo['pgdb'])
    pgcurs = pgconn.cursor(cursor_factory=RealDictCursor)
    pgcurs.execute('INSERT INTO hydro_params (name, description, data_units, equation_units, conversion, definition) VALUES (%s, %s, %s, %s, %s, %s)',
                   [responses['name'], responses['description'], responses['data_units'], responses['equation_units'], float(responses['conversion']), responses['definition']])
    pgconn.commit()


def delete_hydro_parameter(cinfo):

    pgconn = psycopg2.connect(user=cinfo['user'], password=cinfo['word'], host=cinfo['host'], port=cinfo['port'], database=cinfo['pgdb'])
    pgcurs = pgconn.cursor(cursor_factory=RealDictCursor)
    pgcurs.execute('SELECT * FROM hydro_params ORDER BY name')
    params = ['{} ({})'.format(row['name'], row['param_id']) for row in pgcurs.fetchall()]
    params.append('Exit')

    param = inquirer.list_input('Which hydro parameter do you want to delete?', choices=params)
    if param.lower() == 'exit':
        return

    pgcurs.execute('DELETE FROM hydro_params WHERE param_id =%s', [int(param[param.index('(') + 1:param.index(')')])])
    pgconn.commit()


def main():

    # Don't use argparse because we want this to run from the command line without a bunch of annoying arguments.
    env_vars = dotenv.parse_dotenv(os.path.join(os.path.dirname(os.path.realpath(__file__)), '..', '.env'))
    connection_info = {
        'host': env_vars['POSTGRES_HOST'],
        'port': env_vars['POSTGRES_PORT'],
        'user': env_vars['POSTGRES_USER'],
        'word': env_vars['POSTGRES_PASSWORD'],
        'pgdb': env_vars['POSTGRES_DB']
    }

    choice = ''
    while not choice.startswith('6'):
        choice = inquirer.list_input('What do you want to do?',
                                     choices=[
                                         '1. Review Watershed',
                                         '2. Hydro Parameters',
                                         '6. Exit'])

        if choice.startswith('1'):
            watershed_info(connection_info)

        if choice.startswith('2'):
            hydro_params(connection_info)


if __name__ == '__main__':
    main()
