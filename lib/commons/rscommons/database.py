'''
Database Methods
'''
from __future__ import annotations
import os
import glob
import csv
from typing import Dict
import sqlite3
from osgeo import ogr, osr
from rscommons import Logger, VectorBase


class SQLiteCon():
    """This is just a loose mapping class to allow us to use Python's 'with' keyword.

    Raises:
        VectorBaseException: Various
    """
    log = Logger('SQLite')

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.conn = None
        self.curs = None

    def __enter__(self) -> SQLiteCon:
        """Behaviour on open when using the "with VectorBase():" Syntax
        """

        self.conn = sqlite3.connect(self.filepath)

        # turn on foreign key constraints. Does not happen by default
        self.conn.execute('PRAGMA foreign_keys = ON;')

        self.conn.row_factory = dict_factory
        self.curs = self.conn.cursor()
        return self

    def __exit__(self, _type, _value, _traceback):
        """Behaviour on close when using the "with VectorBase():" Syntax
        """
        self.curs.close()
        self.conn.close()
        self.curs = None
        self.conn = None


def create_database(huc: str, db_path: str, metadata: Dict[str, str], epsg: int, schema_path: str, delete: bool = False):
    """[summary]

    Args:
        huc (str): [description]
        db_path (str): [description]
        metadata (Dict[str, str]): [description]
        epsg (int): [description]
        schema_path (str): [description]
        delete (bool, optional): [description]. Defaults to False.

    Raises:
        Exception: [description]

    Returns:
        [type]: [description]
    """

    # We need to create a projection for this DB
    db_srs = osr.SpatialReference()
    db_srs.ImportFromEPSG(int(epsg))
    metadata['gdal_srs_proj4'] = db_srs.ExportToProj4()
    metadata['gdal_srs_axis_mapping_strategy'] = osr.OAMS_TRADITIONAL_GIS_ORDER

    if not os.path.isfile(schema_path):
        raise Exception('Unable to find database schema file at {}'.format(schema_path))

    log = Logger('Database')
    if os.path.isfile(db_path) and delete is True:
        log.info('Removing existing SQLite database at {0}'.format(db_path))
        os.remove(db_path)

    log.info('Creating database schema at {0}'.format(db_path))
    qry = open(schema_path, 'r').read()
    sqlite3.complete_statement(qry)
    conn = sqlite3.connect(db_path)
    conn.execute('PRAGMA foreign_keys = ON;')
    curs = conn.cursor()
    curs.executescript(qry)

    load_lookup_data(db_path, os.path.dirname(schema_path))

    # Keep only the designated watershed
    curs.execute('DELETE FROM Watersheds WHERE WatershedID <> ?', [huc])

    # Retrieve the name of the watershed so it can be stored in riverscapes project
    curs.execute('SELECT Name FROM Watersheds WHERE WatershedID = ?', [huc])
    row = curs.fetchone()
    watershed_name = row[0] if row else None

    conn.commit()
    conn.execute("VACUUM")

    # Write the metadata to the database
    if metadata:
        [store_metadata(db_path, key, value) for key, value in metadata.items()]

    return watershed_name


def update_database(db_path, csv_path):
    """ Update the lookup tables from CSV files in a path

    Arguments:
        db_path {[type]} -- [description]
        csv_path {[type]} -- [description]

    Raises:
        Exception: [description]

    Returns:
        [type] -- [description]
    """

    log = Logger('DatabaseUpdate')

    csv_path = csv_path if csv_path else os.path.join(os.path.abspath(os.path.dirname(__file__)), '..', '..', 'database', 'data')

    if not os.path.isfile(db_path):
        raise Exception('No existing db found at path: {}'.format(db_path))

    log.info('Updating SQLite database at {0}'.format(db_path))

    conn = sqlite3.connect(db_path)
    conn.execute('PRAGMA foreign_keys = ON;')
    conn.row_factory = dict_factory
    curs = conn.cursor()

    try:
        huc = conn.execute('SELECT WatershedID FROM vwReaches GROUP BY WatershedID').fetchall()[0]['WatershedID']
    except Exception as e:
        log.error('Error retrieving HUC from DB')
        raise e

    # Load lookup table data into the database
    load_lookup_data(db_path, csv_path)

    # Updated the database will reload ALL watersheds. Keep only the designated watershed for this run
    curs.execute('DELETE FROM Watersheds WHERE WatershedID <> ?', [huc])

    conn.commit()
    conn.execute("VACUUM")

    return db_path


def load_lookup_data(db_path, csv_dir):
    """Load the database lookup data from CSV files.
    This gets called both during database creation during BRAT build,
    but also during refresh of lookup data at the start of BRAT Run so that
    the database has the latest hydrologic equations and other BRAT parameters

    Args:
        db_path (str): Full path to SQLite database
        csv_dir (str): Full path to the root folder containing CSV lookup files
    """

    conn = sqlite3.connect(db_path)
    conn.row_factory = dict_factory
    curs = conn.cursor()

    log = Logger('Database')

    # Load lookup table data into the database
    dir_search = os.path.join(csv_dir, 'data', '**','*.csv')
    for file_name in glob.glob(dir_search, recursive=True):
        table_name = os.path.splitext(os.path.basename(file_name))[0]
        with open(file_name, mode='r') as csvfile:
            d = csv.DictReader(csvfile)
            sql = 'INSERT OR REPLACE INTO {0} ({1}) VALUES ({2})'.format(table_name, ','.join(d.fieldnames), ','.join('?' * len(d.fieldnames)))

            to_db = [[i[col] for col in d.fieldnames] for i in d]
            curs.executemany(sql, to_db)
            log.info('{:,} records loaded into {} lookup data table'.format(curs.rowcount, table_name))

    conn.commit()


def get_db_srs(database):
    meta = get_metadata(database)
    dbRef = osr.SpatialReference()
    dbRef.ImportFromProj4(meta['gdal_srs_proj4'])
    dbRef.SetAxisMappingStrategy(int(meta['gdal_srs_axis_mapping_strategy']))
    return dbRef


def load_geometries(database, target_srs=None, where_clause=None):

    transform = None
    if target_srs:
        db_srs = get_db_srs(database)
        # https://github.com/OSGeo/gdal/issues/1546
        target_srs.SetAxisMappingStrategy(db_srs.GetAxisMappingStrategy())
        transform = osr.CoordinateTransformation(db_srs, target_srs)

    conn = sqlite3.connect(database)
    curs = conn.execute('SELECT ReachID, Geometry FROM Reaches {}'.format('WHERE {}'.format(where_clause) if where_clause else ''))
    reaches = {}

    for row in curs.fetchall():
        geom = ogr.CreateGeometryFromJson(row[1])
        if transform:
            geom.Transform(transform)
        reaches[row[0]] = VectorBase.ogr2shapely(geom)
    return reaches


def load_attributes(database, fields, where_clause=None):

    conn = sqlite3.connect(database)
    conn.row_factory = dict_factory
    curs = conn.execute('SELECT ReachID, {} FROM vwReaches {}'.format(','.join(fields), 'WHERE {}'.format(where_clause) if where_clause else ''))
    reaches = {}
    for row in curs.fetchall():
        reaches[row['ReachID']] = {}
        for field in fields:
            reaches[row['ReachID']][field] = row[field]

    return reaches


def write_db_attributes(database, reaches, fields, set_null_first=True, summarize=True):

    if len(reaches) < 1:
        return

    conn = sqlite3.connect(database)
    conn.execute('pragma foreign_keys=ON')
    curs = conn.cursor()

    # Optionally clear all the values in the fields first
    if set_null_first is True:
        [curs.execute('UPDATE ReachAttributes SET {} = NULL'.format(field)) for field in fields]

    results = []
    for reachid, values in reaches.items():
        results.append([values[field] if field in values else None for field in fields])
        results[-1].append(reachid)

    sql = 'UPDATE ReachAttributes SET {} WHERE ReachID = ?'.format(','.join(['{}=?'.format(field) for field in fields]))
    curs.executemany(sql, results)
    conn.commit()

    if summarize is True:
        [summarize_reaches(database, field) for field in fields]


def summarize_reaches(database, field):

    log = Logger('Database')
    conn = sqlite3.connect(database)
    curs = conn.cursor()

    curs.execute('SELECT Max({0}), Min({0}), Avg({0}), Count({0}) FROM ReachAttributes WHERE ({0} IS NOT NULL)'.format(field))
    row = curs.fetchone()
    if row and row[3] > 0:
        msg = '{}, max: {:.2f}, min: {:.2f}, avg: {:.2f}'.format(field, row[0], row[1], row[2])
    else:
        msg = "0 non null values"

    curs.execute('SELECT Count(*) FROM ReachAttributes WHERE {0} IS NULL'.format(field))
    row = curs.fetchone()
    msg += ', nulls: {:,}'.format(row[0])

    log.info(msg)


def set_reach_fields_null(database, fields):

    log = Logger('Database')
    log.info('Setting {} reach fields to NULL'.format(len(fields)))
    conn = sqlite3.connect(database)
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute('UPDATE ReachAttributes SET {}'.format(','.join(['{} = NULL'.format(field) for field in fields])))
    conn.commit()
    conn.close()


def execute_query(database, sql, message='Executing database SQL query'):

    log = Logger('Database')
    log.info(message)

    conn = sqlite3.connect(database)
    conn.execute('pragma foreign_keys=ON')
    curs = conn.cursor()
    curs.execute(sql)
    conn.commit()
    log.info('{:,} records affected.'.format(curs.rowcount))


def dict_factory(cursor, row):
    d = {}
    for idx, col in enumerate(cursor.description):
        d[col[0]] = row[idx]
    return d


def get_metadata(database):
    log = Logger('Database')
    log.debug('Retrieving metadata')

    conn = sqlite3.connect(database)
    curs = conn.cursor()
    curs.execute('SELECT KeyInfo, ValueInfo FROM MetaData')
    meta = {}
    for row in curs.fetchall():
        meta[row[0]] = row[1]
    return meta


def store_metadata(database, key, value):

    log = Logger('Database')
    log.info('Storing metadata {} = {}'.format(key, value))

    formatted_value = value
    if isinstance(value, list):
        formatted_value = ', '.join(value)

    conn = sqlite3.connect(database)
    curs = conn.cursor()
    curs.execute('INSERT OR REPLACE INTO MetaData (KeyInfo, ValueInfo) VALUES (?, ?)', [key, formatted_value])
    conn.commit()
