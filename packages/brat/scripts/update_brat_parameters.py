"""Script for exporting riverscapes parameters from Postgres on
    Amazon AWS and writing them to CSV in this repository for use
    in SQLite individual tool databaes.
"""
import csv
import os
import argparse
import psycopg2
from psycopg2.extras import RealDictCursor
from rscommons import dotenv
from rscommons import ProgressBar


relative_path = '../database/data'

tables = [
    # primary tables
    'hydro_params',
    'vegetation_types',
    'statistics',
    'watershed_attributes',
    # intersect tables
    'vegetation_overrides',
    'watershed_hydro_params',
    'watershed_statistics'
]


def update_brat_parameters(host, port, database, user_name, password, csv_dir):
    """ Pull the BRAT parameters from PostGres database and save them to CSV
    files in the BRAT source code folder where they can be used by BRAT Build
    and also commited to git.

    Arguments:
        host {str} -- PostGres host URL or IP address
        port {str} -- PostGres port
        database {str} -- PostGres database name
        user_name {str} -- PostGres user name
        passord {str} -- PostGres password
        csv_dir {[type]} -- [description]

    """

    csv_dir = csv_dir if csv_dir else os.path.dirname(os.path.abspath(__file__))

    watershed_csv = os.path.join(csv_dir, relative_path, 'Watersheds.csv')
    # hydro_params_csv = os.path.join(csv_dir, relative_path, 'HydroParams.csv')
    # veg_type_csv = os.path.join(csv_dir, relative_path, 'VegetationTypes.csv')
    # override_csv = os.path.join(csv_dir, relative_path, 'intersect', 'VegetationOverrides.csv')
    # watershed_hydro_params_csv = os.path.join(csv_dir, relative_path, 'intersect', 'WatershedHydroParams.csv')
    # statistics_csv = os.path.join(csv_dir, relative_path, 'Statistics.csv')
    # watershed_attributes_csv = os.path.join(csv_dir, relative_path, WatershedAttributes.csv')

    conn = psycopg2.connect(host=host, port=port, database=database, user=user_name, password=password)
    curs = conn.cursor(cursor_factory=RealDictCursor)

    # Update watersheds first, because it will attempt to verify the hydrologic equations
    # and abort with errors and before any CSV files are changed.
    update_watersheds(curs, watershed_csv)

    # Now process all the other tables
    for pg_table in tables:
        output_table = snake_to_pascal(pg_table)

        # Get list of column names. Needed because can't determine columns names from empty tables
        curs.execute("""SELECT column_name
            FROM information_schema.columns
            WHERE (table_name = %s)
            AND column_name NOT IN ('created_on', 'updated_on')
            ORDER BY ordinal_position""", [pg_table])
        cols = [row['column_name'] for row in curs.fetchall()]

        # Get a list of the primary key columns for ordering
        curs.execute("""SELECT c.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.constraint_column_usage AS ccu USING (constraint_schema, constraint_name)
            JOIN information_schema.columns AS c ON c.table_schema = tc.constraint_schema
            AND tc.table_name = c.table_name AND ccu.column_name = c.column_name
            WHERE constraint_type = 'PRIMARY KEY' and tc.table_name = %s""", [pg_table])
        keys = [row['column_name'] for row in curs.fetchall()]

        # Load all the data from the table
        curs.execute('SELECT * FROM {} ORDER BY {}'.format(pg_table, ','.join(keys)))
        data = [{col: row[col] for col in row.keys()} for row in curs.fetchall()]

        # write the data to the CSV
        output_csv = os.path.join(csv_dir, relative_path, 'intersect' if len(keys) > 1 else '', '{}.csv'.format(output_table))
        write_values_to_csv(output_csv, cols, data)


def snake_to_pascal(snake_name):

    # Remember to upper case ID fields ending with _id
    temp = snake_name.replace("_", " ").title().replace(" ", "")
    return temp[:-2] + 'ID' if snake_name.endswith('_id') is True else temp


def update_watersheds(curs, watershed_csv):

    # Load all the watersheds from the database in a PREDICTABLE ORDER (so git diff is useful for previewing changes)
    curs.execute("""SELECT * FROM watersheds ORDER BY watershed_id""")
    watersheds = [row for row in curs.fetchall()]


<< << << < HEAD

<< << << < HEAD
== == == =
  # watersheds = [{
  #     'WatershedID': row['watershed_id'],
  #     'Name': row['name'],
  #     'EcoregionID': row['ecoregion_id'],
  #     'MaxDrainage': row['max_drainage'],
  #     'QLow': row['qlow'],
  #     'Q2': row['q2'],
  #     'Notes': row['notes'],
  #     'MetaData': row['metadata'],
  #     'AreaSqKm': row['area_sqkm'],
  #     'States': row['states'].replace(',', '_') if row['states'] else None
  # } for row in curs.fetchall()]
>>>>>> > 163f85b... new postgres tables for watershed statistics

  # Validate the hydrologic equations. The following dictionary will be keyed by python exception concatenated to produce
  # a unique string for each type of error for each equation. These will get printed to the screen for easy cut and paste
  # into a GitHub issue for USU to resolve.
  unique_errors = {}
   for q in ['qlow', 'q2']:
== == == =
    # Validate the hydrologic equations. The following dictionary will be keyed by python exception concatenated to produce
    # a unique string for each type of error for each equation. These will get printed to the screen for easy cut and paste
    # into a GitHub issue for USU to resolve.
    unique_errors = {}
    for q in ['qlow', 'q2']:
>>>>>> > 541db6c... BRAT GeoPackage now contains watershed statistics tables
        progbar = ProgressBar(len(watersheds), 50, 'Verifying {} equations'.format(q))
        counter = 0
        for values in watersheds:
            watershed = values['watershed_id']
            counter += 1
            progbar.update(counter)

            # proceed if the watershed has a hydrologic formula defined
            if not values[q]:
                continue

            # Load the hydrologic parameters for this watershed and substitute a placeholder for drainage area
            curs.execute('SELECT * FROM vw_watershed_hydro_params WHERE watershed_id = %s', [watershed])
            params = {row['name']: row['value'] for row in curs.fetchall()}
            params['DRNAREA'] = 1.0

            try:
                equation = values[q]
                equation = equation.replace('^', '**')
                value = eval(equation, {'__builtins__': None}, params)
                _float_val = float(value)
            except Exception as ex:
                # NoneType is not subscriptable means a watershed parameter is missing.
                exception_id = repr(ex) + values[q]
                if exception_id in unique_errors:
                    unique_errors[exception_id]['watersheds'][watershed] = params
                else:
                    unique_errors[exception_id] = {
                        'watersheds': {watershed: params},
                        'exception': repr(ex),
                        'equation': values[q],
                    }

        progbar.finish()

    if len(unique_errors) > 0:
        for exception_id, values in unique_errors.items():
            print('\n## Hydrologic equation Error\n```')
            print('Equation:', values['equation'])
            print('Exception:', values['exception'])
            print('Watersheds:')
            for watershed, params in values['watersheds'].items():
                print('\t{}:'.format(watershed))
                [print('\t\t{}: {}'.format(key, val)) for key, val in params.items()]
            print('```')
        raise Exception('Aborting due to {} hydrology equation errors'.format(len(unique_errors)))

    cols = list(next(iter(watersheds)).keys())
    del cols[cols.index('updated_on')]
    del cols[cols.index('created_on')]
    del cols[cols.index('geom')]
    write_values_to_csv(watershed_csv, cols, watersheds)


def write_values_to_csv(csv_file, cols, values):

    # cols = list(next(iter(values)).keys())

    # # Remove the date related columns
    # for unwanted_col in ['updated_on', 'created_on']:
    #     if unwanted_col in cols:
    #         del cols[cols.index(unwanted_col)]

    output_cols = [snake_to_pascal(col) for col in cols]

    progBar = ProgressBar(len(values), 50, 'Writing to {}'.format(os.path.basename(csv_file)))
    with open(csv_file, 'w') as file:
        writer = csv.writer(file)
        writer.writerow(output_cols)
        counter = 0
        for vals in values:
            counter += 1
            progBar.update(counter)
            writer.writerow([vals[col] for col in cols])

    progBar.finish()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('host', help='BRAT parameter DB host', type=str)
    parser.add_argument('port', help='BRAT parameter DB port', type=str)
    parser.add_argument('database', help='BRAT parameter database', type=str)
    parser.add_argument('user_name', help='BRAT parameter DB user name', type=str)
    parser.add_argument('password', help='BRAT parameter DB password', type=str)
    parser.add_argument('--csv_dir', help='directory where the csv lives', type=str)
    args = dotenv.parse_args_env(parser)

    try:
        update_brat_parameters(args.host, args.port, args.database, args.user_name, args.password, args.csv_dir)
        print('Processing completed successfully. Review changes using git.')
    except Exception as ex:
        print('Errors occurred:', str(ex))


if __name__ == "__main__":
    main()
