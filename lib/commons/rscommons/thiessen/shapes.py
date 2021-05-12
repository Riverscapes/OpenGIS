import math
import numpy as np
from typing import List, Dict, Any
from osgeo import ogr
from shapely.geometry import Point, Polygon, MultiPolygon, LineString, LinearRing
from shapely.ops import unary_union
from rscommons import Logger, ProgressBar, get_shp_or_gpkg, VectorBase
from rscommons.shapefile import get_transform_from_epsg
# from rscommons.vector_ops import export_geojson

Path = str
Transform = ogr.osr.CoordinateTransformation


class RiverPoint:

    def __init__(self, pt, interior=False, side=None, island=None, properties: Dict[str, Any] = None):
        self.point = pt
        self.side = side
        self.interior = interior
        self.island = island
        self.properties = properties


def get_riverpoints(inpath, epsg, attribute_filter=None):
    """[summary]

    Args:
        inpath ([type]): Path to a ShapeFile
        epsg ([type]):  Desired output spatial reference
        attribute_filter ([type], optional): [description]. Defaults to None.

    Returns:
        [type]: List of RiverPoint objects
    """

    log = Logger('get_riverpoints')
    points = []

    with get_shp_or_gpkg(inpath) as in_lyr:

        _out_spatial_ref, transform = get_transform_from_epsg(in_lyr.spatial_ref, epsg)

        for feat, _counter, progbar in in_lyr.iterate_features('Getting points for use in Thiessen', attribute_filter=attribute_filter):

            new_geom = feat.GetGeometryRef()

            if new_geom is None:
                progbar.erase()  # get around the progressbar
                log.warning('Feature with FID={} has no geometry. Skipping'.format(feat.GetFID()))
                continue

            new_geom.Transform(transform)
            new_shape = VectorBase.ogr2shapely(new_geom)

            if new_shape.type == 'Polygon':
                new_shape = MultiPolygon([new_shape])

            for poly in new_shape:
                # Exterior is the shell and there is only ever 1
                for pt in list(poly.exterior.coords):
                    points.append(RiverPoint(pt, interior=False))

                # Now we consider interiors. NB: Interiors are only qualifying islands in this case
                for idx, island in enumerate(poly.interiors):
                    for pt in list(island.coords):
                        points.append(RiverPoint(pt, interior=True, island=idx))

    return points


def midpoints(in_lines):

    out_points = []
    with get_shp_or_gpkg(in_lines) as in_lyr:
        for feat in in_lyr.iterate_features('Getting Midpoints'):
            geom = feat.GetGeometryRef()
            line = VectorBase.ogr2shapely(geom)
            out_points.append(RiverPoint(line.interpolate(0.5, True)))
            feat = None

    return out_points


def centerline_points(in_lines: Path, distance: float = 0.0, transform: Transform = None, fields=None) -> Dict[int, List[RiverPoint]]:
    """Generates points along each line feature at specified distances from the end as well as quarter and halfway

    Args:
        in_lines (Path): path of shapefile with features
        distance (float, optional): distance from ends to generate points. Defaults to 0.0.
        transform (Transform, optional): coordinate transformation. Defaults to None.

    Returns:
        [type]: [description]
    """
    log = Logger('centerline_points')
    with get_shp_or_gpkg(in_lines) as in_lyr:
        out_group = {}
        ogr_extent = in_lyr.ogr_layer.GetExtent()
        extent = Polygon.from_bounds(ogr_extent[0], ogr_extent[2], ogr_extent[1], ogr_extent[3])

        for feat, _counter, progbar in in_lyr.iterate_features("Centerline points"):

            line = VectorBase.ogr2shapely(feat, transform)

            fid = feat.GetFID()
            out_points = []
            # Attach the FID in case we need it later
            props = {'fid': fid}

            if fields:
                for field in fields:
                    divergence = feat.GetField('Divergence')
                    if divergence == 2:
                        value = feat.GetField('DnLevelPat')
                    else:
                        value = feat.GetField(field)
                    props[field] = str(int(value)) if value else None

            pts = [
                line.interpolate(distance),
                line.interpolate(0.5, True),
                line.interpolate(-distance)]

            total = line.length
            interval = distance / total
            current = interval
            while current < 1.0:
                pts.append(line.interpolate(interval, True))
                current = current + interval

            # pts = [
            #     line.interpolate(distance),
            #     line.interpolate(0.5, True),
            #     line.interpolate(-distance)
            # ]

            if line.project(line.interpolate(0.25, True)) > distance:
                pts.append(line.interpolate(0.25, True))
                pts.append(line.interpolate(-0.25, True))

            for pt in pts:
                # Recall that interpolation can have multiple solutions due to pythagorean theorem
                # Throw away anything that's not inside our bounds
                if not extent.contains(pt):
                    progbar.erase()
                    log.warning('Point {} is outside of extent: {}'.format(pt.coords[0], ogr_extent))
                out_points.append(RiverPoint(pt, properties=props))

            out_group[int(fid)] = out_points
            feat = None
        return out_group


def centerline_vertex_between_distance(in_lines, distance=0.0):

    out_group = []
    with get_shp_or_gpkg(in_lines) as in_lyr:
        for feat, _counter, _progbar in in_lyr.iterate_features("Centerline points between distance"):
            line = VectorBase.ogr2shapely(feat)

            out_points = []
            out_points.append(RiverPoint(line.interpolate(distance)))
            out_points.append(RiverPoint(line.interpolate(-distance)))

            max_distance = line.length - distance

            for vertex in list(line.coords):
                test_dist = line.project(Point(vertex))
                if test_dist > distance and test_dist < max_distance:
                    out_points.append(RiverPoint(Point(vertex)))
            out_group.append(out_points)
            feat = None
    return out_group


def load_geoms(in_lines):
    out = []
    with get_shp_or_gpkg(in_lines) as in_lyr:
        for feat, _counter, _progbar in in_lyr.iterate_features("Loading geometry"):
            shapely_geom = VectorBase.ogr2shapely(feat)
            out.append(shapely_geom)

    return out


def clip_polygons(clip_poly, polys):

    progbar = ProgressBar(len(polys), 50, "Clipping Polygons...")
    counter = 0
    progbar.update(counter)
    out_polys = {}
    for key, poly in polys.items():
        counter += 1
        progbar.update(counter)
        out_polys[key] = clip_poly.intersection(poly.buffer(0))

    progbar.finish()
    return out_polys


def dissolve_by_intersection(lines, polys):

    progbar = ProgressBar(len(polys), 50, "Dissolving Polygons...")
    counter = 0
    progbar.update(counter)
    dissolved_polys = []
    for line in lines:
        counter += 1
        progbar.update(counter)
        intersected = [p for p in polys if line.intersects(p)]
        dissolved_polys.append(unary_union(intersected))

    return dissolved_polys


def dissolve_by_points(groups, polys):

    progbar = ProgressBar(len(groups), 50, "Dissolving Polygons...")
    counter = 0
    progbar.update(counter)
    dissolved_polys = {}

    # Original method
    # for key, group in groups.items():
    #     counter += 1
    #     progbar.update(counter)
    #     intersected = [p for p in polys if any([p.contains(pt.point) for pt in group])]
    #     dissolved_polys[key] = unary_union(intersected)

    # This method gradulally speeds up processing by removing polygons from the list.
    for key, group in groups.items():
        counter += 1
        progbar.update(counter)
        intersected = []
        indexes = []
        for i, p in enumerate(polys):
            if any([p.contains(pt.point) for pt in group]):
                intersected.append(p)
                indexes.append(i)
        dissolved_polys[key] = unary_union(intersected)  # MultiPolygon(intersected) #intersected
        polys = [p for i, p in enumerate(polys) if i not in indexes]

    progbar.finish()
    return dissolved_polys


def densifyShape(shape, spacing):
    """
    Densifies a shape (including
    :param shape:
    :param spacing: the spacing between points
    :return:
    """
    ext = _densifyRing(shape.exterior, spacing)

    if not ext.is_valid:
        ext = ext.buffer(0)

    isls = []
    for isl in shape.interiors:
        isls.append(_densifyRing(isl, spacing))

    if len(isls) == 0:
        return ext
    else:
        return ext.difference(MultiPolygon(isls))


def _densifyRing(ring, spacing):
    """
    Densify this particular ring using _densify segment on each segment
    :param ring:
    :param spacing:
    :return:
    """
    # Densify all the line segments. This return a list with the first point and any linear interpolated points
    densesegments = [_densifySegment(LineString([ring.coords[x - 1], ring.coords[x]]), spacing) for x in range(1, len(ring.coords))]
    # Finally add the very last point to complete the ring
    densesegments.append([ring.coords[-1]])
    poly = Polygon(LinearRing([pt for seg in densesegments for pt in seg]))
    return poly


def _densifySegment(segment, spacing):
    """
    A segment is defined as a LineString with two points in it. Spacing is the point spacing we want.
    :param segment:
    :param spacing:
    :return:
    """
    if segment.length < spacing:
        return [segment.coords[0]]

    pts = []
    for currDist in np.arange(0, segment.length, spacing):
        newpt = segment.interpolate(currDist).coords[0]
        if newpt != segment.coords[-1]:
            pts.append(newpt)
    return pts


def GetBufferedBounds(shape, buffer):
    """[summary]

    Args:
        shape ([type]): [description]
        buffer ([type]): [description]

    Returns:
        [type]: [description]
    """

    newExtent = (shape.bounds[0] - buffer, shape.bounds[1] - buffer, shape.bounds[2] + buffer, shape.bounds[3] + buffer)

    return Polygon([
        (newExtent[0], newExtent[1]),
        (newExtent[2], newExtent[1]),
        (newExtent[2], newExtent[3]),
        (newExtent[0], newExtent[3]),
        (newExtent[0], newExtent[1])
    ])


def getDiag(rect):

    return math.sqrt(math.pow((rect.bounds[3] - rect.bounds[1]), 2) + math.pow((rect.bounds[2] - rect.bounds[0]), 2))


def projToShape(line, poly):

    diag = getDiag(poly)
    longLine = getExtrapoledLine(line, diag)
    return poly.intersection(longLine)


def getExtrapoledLine(line, length):

    p1 = line.coords[0]
    p2 = line.coords[1]

    rise = (p2[1] - p1[1])
    run = (p2[0] - p1[0])

    theta = math.atan2(rise, run)

    newX = p2[0] + length * math.cos(theta)
    newY = p2[1] + length * math.sin(theta)

    return LineString([p2, (newX, newY)])


def splitClockwise(rect, thalweg):
    """
    Work clockwise around a rectangle and create two shapes that represent left and right bank
    We do this by adding 4 corners of the rectangle and 2 endpoints of thalweg to a list and then
    sorting it clockwise using the rectangle centroid.
    Then we traverse the clockwise list and switch between shape1 and shape2 when we hit thalweg start/end points
    finally we inject the entire thalweg line into both shape1 and shape2 between where the start and end points
    of the thalweg intersect the rectangle and instantiate the whole mess as two polygons inside a multipolygon
    which we then return
    :param rect:
    :param thalweg: a thalweg with start and end points that intersects the rectangle
    :return:
    """

    # TODO: This might break if the thalweg is reversed or if the thalweg us weird. Lots of testing necessary
    # The thalweg has two points we care about: the first and last points that should intersect the rectangle
    thalwegStart = thalweg.coords[0]
    thalwegEnd = thalweg.coords[-1]

    coordsorter = list(rect.exterior.coords)
    coordsorter.append(thalwegStart)
    coordsorter.append(thalwegEnd)

    # Sort the points clockwise using the centroid as a center point
    def algo(pt):
        return math.atan2(pt[0] - rect.centroid.coords[0][0], pt[1] - rect.centroid.coords[0][1])
    coordsorter.sort(key=algo)

    # Create shape1 and shape2 which will fill up with points shape#idx is the place where the thalweg
    # Should be injected
    shape1 = []
    shape2 = []
    shape1idx = 0
    shape2idx = 0

    # Our boolean switchers
    firstshape = True
    foundfirst = False
    reverseThalweg = False

    # Calculate shape 1 and shape 2 by traversal
    for idx, pt in enumerate(coordsorter):

        # If we hit the thalweg start note it using the idx vars and floop the firstshape.
        if pt == thalwegStart:
            shape1idx = len(shape1)
            shape2idx = len(shape2)
            firstshape = not firstshape
            foundfirst = True

        # At the endpoint we just floop the firstshape.
        elif pt == thalwegEnd:
            firstshape = not firstshape
            # We found the tail before we found the head. Make a note that it's ass-backwards
            if not foundfirst:
                reverseThalweg = True

        # If this is a rectangle corner we add it to the appropriate shape
        elif firstshape:
            shape1.append(pt)
        elif not firstshape:
            shape2.append(pt)

    # Now inject the entire thalweg into the appropriate area (reversed if necessary)
    if reverseThalweg:
        shape1[shape1idx:shape1idx] = reversed(list(thalweg.coords))
        shape2[shape2idx:shape2idx] = list(thalweg.coords)
    else:
        shape1[shape1idx:shape1idx] = list(thalweg.coords)
        shape2[shape2idx:shape2idx] = reversed(list(thalweg.coords))

    return MultiPolygon([Polygon(shape1), Polygon(shape2)])


def chopCenterlineEnds(line, shape):

    # log = Logger("chopCenterlineEnds")

    # Trim to be inside the river shape.
    centerlineChopped = line
    centerlineIntersection = line.intersection(shape)
    # It it's a multiline string that means it crosses over the channel at some point
    if centerlineIntersection.type == "MultiLineString":
        # log.error("Centerline Crosses Channel Boundary. Continuing...")

        # We need to pick out the start and end points so collect them from all
        # the intersections and then sort them by distance from the start of
        # the original line
        endpts = []
        for segline in centerlineIntersection:
            endpts.append(Point(segline.coords[0]))
            endpts.append(Point(segline.coords[-1]))
        endpts.sort(key=lambda pt: line.project(pt))

        # Get the start and endpoints where the line crosses the rivershape
        startdist = line.project(endpts[0])
        enddist = line.project(endpts[-1])

        pts = []
        lnstr = [line] if line.type == 'LineString' else line
        for geom in lnstr:
            for pt in geom.coords:
                projectpt = geom.project(Point(pt))
                if startdist < projectpt < enddist:
                    pts.append(pt)

        # Add the first point in if it isn't already there
        firstpoint = line.interpolate(startdist).coords[0]
        if pts[0] != firstpoint:
            pts.insert(0, firstpoint)

        # Add the last point in if it isn't already there
        lastpoint = line.interpolate(enddist).coords[0]
        if pts[-1] != lastpoint:
            pts.append(lastpoint)

        centerlineChopped = LineString(pts)
    else:
        # If it's just a linestring then we can safely use it
        centerlineChopped = centerlineIntersection

    return centerlineChopped


def reconnectLine(baseline, separateLine):
    """
    Smoothing can separate the centerline from its alternates. This function
    reconnects them using nearest point projection. Do this before smoothing the
    alternate line
    :param baseline: The main line that does not change
    :param separateLine: The line we want to reconnect
    :return:
    """
    # First find the start and end point
    sepLineStart = Point(separateLine.coords[0])
    sepLineEnd = Point(separateLine.coords[-1])

    # Now find their nearest points on the centerline
    newStart = baseline.interpolate(baseline.project(sepLineStart))
    newEnd = baseline.interpolate(baseline.project(sepLineEnd))

    line = list(separateLine.coords)
    line.insert(0, tuple(newStart.coords[0]))
    line.append(tuple(newEnd.coords[0]))

    return LineString(line)
