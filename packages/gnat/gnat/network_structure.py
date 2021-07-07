#!/usr/bin/env python3
# Name:     GNAT - Network Structure
#
# Purpose:  Generate network based reach and node attributes on a segmented network
#
# Author:   Kelly Whitehead
#
# Date:     22 Feb 2021
# -------------------------------------------------------------------------------

import sys
import os
import glob
import traceback
import datetime
import json
import argparse

import networkx as nx

from osgeo import ogr
from osgeo import gdal

from rscommons import Logger, ProgressBar, dotenv
from rscommons import GeopackageLayer
from rscommons import database

Path = str


def build_network_structure(line_network: Path, out_gpkg: Path, reach_id_field: str = None):
    """generate network based geometry features

    Args:
        line_network (Path): full flowline network
        out_gpkg (Path): geopackage to save outputs
        reach_id_field (str, optional): custom fid field. Defaults to None.

    Returns:
        [type]: [description]
    """

    log = Logger("GNAT Network Structure")
    log.info(f'Starting network structure')

    with GeopackageLayer(line_network, write=True) as flowlines_lyr, \
            GeopackageLayer(out_gpkg, "NetworkNodes", delete_dataset=False, write=True) as lyr_nodes:

        # Get the reach nodes
        reaches = {}
        up_nodes = {}
        down_nodes = {}

        reach_attributes = {}
        MDG = nx.MultiDiGraph()

        for feat, _counter, _progbar in flowlines_lyr.iterate_features("Loading Flowline Nodes"):

            reach_nodes = {}
            geom = feat.GetGeometryRef()
            r_id = int(feat.GetField(reach_id_field)) if reach_id_field else feat.GetFID()

            pt_start = geom.GetPoint_2D(0)
            pt_end = geom.GetPoint_2D(geom.GetPointCount() - 1)

            up_nodes[r_id] = pt_start
            down_nodes[r_id] = pt_end

            reach_nodes['up'] = pt_start
            reach_nodes['down'] = pt_end

            reaches[r_id] = reach_nodes

            MDG.add_edge(pt_start, pt_end, reach_id=r_id)

        # Find attributes based on node concurrency
        progbar = ProgressBar(len(reaches), 50, "Processing Reach Nodes")
        progcount = 0

        out_nodes = {}

        for reach_id, node in reaches.items():
            progbar.update(progcount)
            attributes = {'convergent_count': 0, 'divergent_count': 0, 'headwater': 0, 'outlet': 0, 'up_reaches': [], 'down_reaches': []}
            for down_id, down_node in down_nodes.items():
                if down_node == node['up']:
                    attributes['up_reaches'].append(down_id)

            for up_id, up_node in up_nodes.items():
                if up_node == node['down']:
                    attributes['down_reaches'].append(up_id)

            attributes['convergent_count'] = len(attributes['up_reaches'])
            if attributes['convergent_count'] > 1:
                if node['up'] not in out_nodes.keys():
                    out_nodes[node['up']] = {'convergent': 1, 'divergent': 0}
                else:
                    out_nodes[node['up']]['convergent'] = out_nodes[node['up']]['convergent'] + 1

            attributes['divergent_count'] = len(attributes['down_reaches'])
            if attributes['divergent_count'] > 1:
                if node['down'] not in out_nodes.keys():
                    out_nodes[node['down']] = {'convergent': 0, 'divergent': 1}
                else:
                    out_nodes[node['down']]['divergent'] = out_nodes[node['down']]['divergent'] + 1

            attributes['headwater'] = 1 if attributes['convergent_count'] == 0 else 0
            # if attributes['headwater'] = 1:
            #     if node['up'] not in out_nodes.keys():
            #             out_nodes[node['up']] = {'convergent': 1, 'divergent': 0}
            #         else:
            #             out_nodes[node['up']]['convergent'] = out_nodes[node['up']]['convergent'] + 1

            attributes['outlet'] = 1 if attributes['divergent_count'] == 0 else 0

            reach_attributes[reach_id] = attributes

            # save nodes with attributes
            progcount += 1

        srs = flowlines_lyr.ogr_layer.GetSpatialRef()
        lyr_nodes.create_layer(ogr.wkbPoint, spatial_ref=srs, fields={'convergent': ogr.OFTInteger, 'divergent': ogr.OFTInteger})

        for node, attributes in out_nodes.items():
            out_feature = ogr.Feature(lyr_nodes.ogr_layer_def)
            geom = ogr.Geometry(ogr.wkbPoint)
            geom.AddPoint(node[0], node[1])
            out_feature.SetGeometry(geom)

            out_feature.SetField('convergent', attributes['convergent'])
            out_feature.SetField('divergent', attributes['divergent'])

            lyr_nodes.ogr_layer.CreateFeature(out_feature)
            out_feature = None

    return os.path.join(out_gpkg, "NetworkNodes")


if __name__ == "__main__":

    parser = argparse.ArgumentParser(description='Network Structure')

    parser.add_argument('huc', help='HUC identifier', type=str)
    parser.add_argument('in_network', help="NHD Flowlines (.shp, .gpkg/layer_name)", type=str)
    parser.add_argument('output_folder', type=str)
    parser.add_argument('--meta', help='riverscapes project metadata as comma separated key=value pairs', type=str)
    parser.add_argument('--verbose', help='(optional) a little extra logging ', action='store_true', default=False)

    args = dotenv.parse_args_env(parser)

    out_gpkg = os.path.join(args.output_folder, "gnat.gpkg")

    build_network_structure(args.in_network, out_gpkg)
