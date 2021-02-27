""" Script for summarsing BRAT attributes for a watershed
and storing them in PostGres database.
"""
import argparse
import sqlite3
import psycopg2
from psycopg2.extras import RealDictCursor
from rscommons import dotenv
from rscommons import ProgressBar


def update_watershed_parameters(host, port, database, user_name, password, brat_db):
    """ Summarize the BRAT attributes for all watersheds in a BRAT database and
    insert them into Postgres BRAT database.

    Arguments:
        host {str} -- PostGres host URL or IP address
        port {str} -- PostGres port
        database {str} -- PostGres database name
        user_name {str} -- PostGres user name
        passord {str} -- PostGres password
        brat_db {atr} -- path to BRAT SQLite GeoPackage database
    """

    pg_conn = psycopg2.connect(host=host, port=port, database=database, user=user_name, password=password)
    pg_curs = pg_conn.cursor(cursor_factory=RealDictCursor)

    # Get list of BRAT attributes to summarize
    pg_curs.execute('SELECT * FROM watershed_attributes')
    atts = {row['attribute_id']: row['column_name'] for row in pg_curs.fetchall()}

    # Get list of statistics to summarize
    pg_curs.execute('SELECT * FROM statistics')
    stats = {row['statistic_id']: row['statistic_name'] for row in pg_curs.fetchall()}

    gpkg_conn = sqlite3.connect(brat_db)
    gpkg_curs = gpkg_conn.cursor()

    progbar = ProgressBar(len(atts), 50, 'BRAT Attributes')
    counter = 0
    for att_id, att_col in atts.items():
        gpkg_curs.execute("""SELECT WatershedID, Min({0}), Max({0}), Avg({0}), Sum({0}), Count({0})
        FROM ReachAttributes WHERE {0} IS NOT NULL GROUP BY WatershedID""".format(att_col))

        for row in gpkg_curs.fetchall():
            print('Uploading statistics for {}'.format(row[0]))
            pg_curs.execute("""INSERT INTO watershed_statistics (
                watershed_id, min_val, max_val, avg_val, sum_val, count_val, attribute_id)
                VALUES (%s, %s, %s, %s, %s, %s)""", [row[i] for i in range(0, 5)] + att_id)

        counter += 1
        progbar.update(counter)

    progbar.finish()
    pg_conn.commit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('host', help='BRAT parameter DB host', type=str)
    parser.add_argument('port', help='BRAT parameter DB port', type=str)
    parser.add_argument('database', help='BRAT parameter database', type=str)
    parser.add_argument('user_name', help='BRAT parameter DB user name', type=str)
    parser.add_argument('password', help='BRAT parameter DB password', type=str)
    parser.add_argument('brat_db', help='Path to BRAT SQLite GeoPackage database', type=str)
    args = dotenv.parse_args_env(parser)

    try:
        update_watershed_parameters(args.host, args.port, args.database, args.user_name, args.password, args.brat_db)
        print('Processing completed successfully. Run update_brat_parameters to pull values into git repo.')
    except Exception as ex:
        print('Errors occurred:', str(ex))


if __name__ == "__main__":
    main()
