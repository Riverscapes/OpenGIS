# Name:     Valley Bottom Centerlines
#
# Purpose:  Generate centerlines for valley bottoms
#
# Author:   Kelly Whitehead, modified from Matt Reimer
#
# Date:     May 4, 2021
#
# -------------------------------------------------------------------------------

from osgeo import ogr
from shapely.geometry import LineString, Polygon, Point
from shapely.ops import linemerge, split
from shapely.wkb import loads as wkbload

from rscommons import GeopackageLayer, Logger
from rscommons.thiessen.shapes import RiverPoint, densifyShape, GetBufferedBounds, projToShape, splitClockwise
from rscommons.thiessen.vor import NARVoronoi

# from vbet.vbet_network import join_attributes


def vbet_centerline(flowlines, vbet_polygons, out_layer):
    """[summary]

    Args:
        flowlines ([type]): [description]
        vbet_polygons ([type]): [description]
        out_layer ([type]): [description]
    """
    log = Logger('vbet_centerline')
    # fields = ['HydroSeq', 'DnHydroSeq', 'UpHydroSeq']
    # flowlines = os.path.join(os.path.dirname(flowlines), "VAA_Centerlines")
    # flowlines = join_attributes(os.path.dirname(flowlines), "VAA_Centerlines", os.path.basename(flowlines), "NHDPlusFlowlineVAA", 'NHDPlusID', fields, 4326)

    reaches = {}

    with GeopackageLayer(flowlines) as lyr,\
            GeopackageLayer(vbet_polygons, write=True) as lyr_polygons,\
            GeopackageLayer(out_layer, write=True) as lyr_output:

        srs = lyr.ogr_layer.GetSpatialRef()
        lyr_output.create_layer(ogr.wkbLineString, spatial_ref=srs)
        lyr_output.create_field("HydroSeq", field_type=ogr.OFTReal)
        lyr_output_defn = lyr_output.ogr_layer.GetLayerDefn()

        degree_factor = lyr.rough_convert_metres_to_vector_units(1)

        for feat, *_ in lyr.iterate_features():
            reach = {}
            reach['up'] = feat.GetField('UpHydroSeq')
            reach['down'] = feat.GetField('DnHydroSeq')
            geom = feat.GetGeometryRef()
            reach['geom'] = geom.Clone()
            reaches[feat.GetField('HydroSeq')] = reach

        headwaters = {k: v for k, v in reaches.items() if v['up'] == 0 or v['up'] not in reaches}
        processed = []
        unioned_reaches = {}

        for HydroSeq in headwaters:
            unioned_geom = reaches[HydroSeq]['geom']
            HydroSeq_next = reaches[HydroSeq]['down']

            lines = ogr.Geometry(ogr.wkbMultiLineString)

            while HydroSeq_next != 0 and HydroSeq_next in reaches:
                union = unioned_geom.Union(reaches[HydroSeq_next]['geom'])
                unioned_geom = union
                lines.AddGeometry(reaches[HydroSeq_next]['geom'])
                if HydroSeq_next in processed:
                    break
                processed.append(HydroSeq_next)
                HydroSeq_next = reaches[HydroSeq_next]['down']

            unioned_reaches[HydroSeq] = unioned_geom

        counter = 0

        merged_centerline = None

        for HydroSeq, line in unioned_reaches.items():

            counter += 1
            log.info('Processing reach: ({}/{})'.format(counter, len(unioned_reaches)))

            polys = ogr.Geometry(ogr.wkbMultiPolygon)

            for poly_feat, *_ in lyr_polygons.iterate_features():
                poly = poly_feat.GetGeometryRef()

                if poly.IsEmpty():
                    continue

                if poly.Intersects(line):
                    polys.AddGeometry(poly)

            log.debug('Unioning...')
            if not polys.IsValid() or polys.Area() == 0:
                poly_test = polys.Buffer(0)
                if poly_test.IsValid():
                    polys = poly_test
                else:
                    log.warning('Invalid geometry')
                    continue

            # with open(os.path.join(os.path.dirname(os.path.dirname(out_layer)), 'layer_{}.json'.format(counter)), 'w') as fs:
            #     fs.write(polys.ExportToJson())
            if polys.GetGeometryType() == ogr.wkbMultiPolygon:
                poly_union = polys.UnionCascaded()
            else:
                poly_union = polys
            log.debug('Unioning complete')

            if poly_union:
                centerlines, merged_centerline = build_centerline(line, poly_union, 20, dist_factor=degree_factor, existing_centerlines=merged_centerline, up_reach=reaches[HydroSeq]['geom'])

                if centerlines:
                    for centerline in centerlines:
                        feat_out = ogr.Feature(lyr_output_defn)
                        feat_out.SetGeometry(centerline)
                        feat_out.SetField('HydroSeq', HydroSeq)
                        lyr_output.ogr_layer.CreateFeature(feat_out)
                        feat_out = None

    return


def build_centerline(thalweg, bounding_polygon, spacing=None, dist_factor=1, existing_centerlines=None, up_reach=None):
    """[summary]

    Args:
        thalweg ([type]): [description]
        bounding_polygon ([type]): [description]
        spacing ([type], optional): [description]. Defaults to None.
        dist_factor (int, optional): [description]. Defaults to 1.
        existing_centerlines ([type], optional): [description]. Defaults to None.
        up_reach ([type], optional): [description]. Defaults to None.

    Returns:
        [type]: [description]
    """

    log = Logger('build_centerline')
    log.info('Building centerline')
    # Remove Z values
    thalweg.FlattenTo2D()
    bounding_polygon.FlattenTo2D()

    # load the geoms
    g_thalweg_load = wkbload(bytes(thalweg.ExportToWkb()))
    if g_thalweg_load.geometryType() == 'LineString':
        g_thalweg_init = g_thalweg_load
    else:
        g_thalweg_init = linemerge(g_thalweg_load)
    g_polygon = wkbload(bytes(bounding_polygon.ExportToWkb()))

    buffer = (g_polygon.area / g_thalweg_init.length) * 1.5

    processing_extent = g_thalweg_init.buffer(buffer)

    # islands?
    if g_polygon.type == 'Polygon':
        rivershape = g_polygon
    else:
        rivershape = max(g_polygon, key=lambda a: a.area)

    # make sure all thalweg coords are within river polygon
    coords = []
    for coord in g_thalweg_init.coords:
        if Point(coord).within(rivershape):
            coords.append(coord)
    if len(coords) < 2:
        return None, existing_centerlines
    g_thalweg = LineString(coords)

    # Prepare geoms
    if spacing:
        rivershape_smooth = densifyShape(rivershape, spacing * dist_factor)
    else:
        rivershape_smooth = rivershape

    thalwegStart = LineString([g_thalweg.coords[1], g_thalweg.coords[0]])
    thalwegEnd = LineString([g_thalweg.coords[-2], g_thalweg.coords[-1]])

    rivershape_bounds = GetBufferedBounds(rivershape, 5 * dist_factor)

    thalwegStartExt = projToShape(thalwegStart, rivershape_bounds)
    thalwegEndExt = projToShape(thalwegEnd, rivershape_bounds)

    thalweglist = list(g_thalweg.coords)
    thalweglist.insert(0, thalwegStartExt.coords[1])
    thalweglist.append(thalwegEndExt.coords[1])

    newThalweg = LineString(thalweglist)

    bankshapes = splitClockwise(rivershape_bounds, newThalweg)

    points = []

    # Exterior is the shell and there is only ever 1
    for pt in list(rivershape_smooth.exterior.coords):
        g_pt = Point(pt)
        side = 1 if bankshapes[0].contains(g_pt) else -1
        if processing_extent.contains(g_pt):
            points.append(RiverPoint(g_pt, interior=False, side=side))

    for idx, island in enumerate(rivershape_smooth.interiors):
        for pt in list(island.coords):
            g_pt = Point(pt)
            side = 1 if bankshapes[0].contains(g_pt) else -1
            if processing_extent.contains(g_pt):
                points.append(RiverPoint(g_pt, interior=True, side=side, island=idx))

    log.info("Calculating Voronoi Polygons for {} points...".format(len(points)))
    myVorL = NARVoronoi(points)
    log.info("Calculating nearest neighbour for Voronoi polygons ...")
    myVorL.calculate_neighbours()
    centerlines_raw = myVorL.collectCenterLines(Polygon(rivershape.exterior))

    if centerlines_raw.type == 'GeometryCollection':
        return None, existing_centerlines

    centerline_segments = split(centerlines_raw, rivershape)
    centerlines_long = [LineString(segment.coords[1:-1] if len(segment.coords) > 3 else segment.coords) for segment in centerline_segments if segment.interpolate(segment.length / 2).within(rivershape)]

    if existing_centerlines:
        g_existing_centerlines = wkbload(bytes(existing_centerlines.ExportToWkb()))
        l_existing_centerlines = [g_existing_centerlines] if g_existing_centerlines.type == "LineString" else [g for g in g_existing_centerlines]

        up_reach.FlattenTo2D()
        g_up_reach = wkbload(bytes(up_reach.ExportToWkb()))

        centerlines = []

        for line in centerlines_long:
            new_segment = line.difference(g_existing_centerlines)
            if new_segment.type == 'LineString':
                if new_segment.intersects(g_up_reach):
                    centerlines.append(new_segment)
            else:
                for s in new_segment:
                    if s.intersects(g_up_reach):
                        centerlines.append(s)

        centerlines_merged = linemerge(centerlines + l_existing_centerlines)
    else:
        centerlines = centerlines_long
        centerlines_merged = linemerge(centerlines)

    geom_centerlines = [ogr.CreateGeometryFromWkb(centerline.to_wkb()) for centerline in centerlines]
    geom_merged_centerlines = ogr.CreateGeometryFromWkb(centerlines_merged.to_wkb())

    return geom_centerlines, geom_merged_centerlines
