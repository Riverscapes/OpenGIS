"""
NARVoronoi Module
"""
# pylint: disable=no-member
from typing import List
import numpy as np
from scipy.spatial.qhull import QhullError
from scipy.spatial import Voronoi
from shapely.geometry import Point, MultiPoint, LineString, Polygon, MultiPolygon
from shapely.ops import unary_union, linemerge
from rscommons import Logger, ProgressBar
from rscommons.thiessen.shapes import RiverPoint


class NARVoronoi:
    """
    The purpose of this class is to load a shapefile and calculate the voronoi
    shapes from it.
    """

    def __init__(self, points: List[RiverPoint]):
        """
        The init method is where all the Voronoi magic happens.
        :param points:
        """
        # The centroid is what we're going to use to shift all the coords around
        # NOTE: We drop the z coord here
        multipoint = MultiPoint([x.point.coords[0][0:2] for x in points])
        self.points = points
        self.polys = None
        self.centroid = multipoint.centroid.coords[0]
        self.log = Logger('NARVoronoi')
        self.log.info('initializing...')

        # Give us a numpy array that is easy to work with then subtract the centroid
        # centering our object around the origin so that the QHull method works properly
        adjpoints = np.array(multipoint)
        adjpoints = adjpoints - self.centroid

        try:
            self.log.info('Creating Voronoi')
            self._vor = Voronoi(adjpoints)
        except QhullError as e:
            self.log.error("Something went wrong with QHull", e)
        except ValueError as e:
            self.log.error("Invalid array specified", e)

        # bake in region adjacency (I have no idea why it's not in by default)
        self.region_neighbour = []

        # Transform everything back to where it was (with some minor floating point rounding problems)
        # Note that we will use the following and NOT anything from inside _vor
        # (which is shifted to the origin)
        self.log.info("Transform everything back")
        self.vertices = self._vor.vertices + self.centroid
        self.ridge_points = self._vor.ridge_points
        self.ridge_vertices = self._vor.ridge_vertices
        self.regions = self._vor.regions
        self.point_region = self._vor.point_region

    def calculate_neighbours(self):
        self.region_neighbour = []
        # Find which regions are next to which other regions
        progbar = ProgressBar(len(self._vor.regions), 50, "baking in region adjacency")
        counter = 0
        for idx, reg in enumerate(self._vor.regions):
            counter += 1
            progbar.update(counter)
            adj = []
            for idy, reg2 in enumerate(self._vor.regions):
                # Adjacent if we have two matching vertices (neighbours share a wall)
                if idx != idy and len(set(reg) - (set(reg) - set(reg2))) >= 2:
                    adj.append(idy)
            self.region_neighbour.append(adj)
        progbar.finish()

    def collectCenterLines(self, rivershape, flipIsland=None):
        """

        :param flipIsland: The id of the island to reassign to a different side. 
                            Useful when calculating alternate
                            centerlines around islands.
        :return: LineString (Valid) or MultiLineString (invalid)
        """

        # The first loop here asigns each polygon to either left or right side of the c
        # hannel based on the self.point object we passed in earlier.
        regions = []
        if len(self.region_neighbour) == 0:
            self.log.warning('Neighbours are empty. Have you run calculate_neighbours() before collectCenterLines?')
        for idx, reg in enumerate(self.region_neighbour):
            # obj will have everything we need to know.
            obj = {
                "id": idx,
                "side": 1,
                "adjacents": reg
            }
            lookupregion = np.where(self._vor.point_region == idx)
            if len(lookupregion[0]) > 0:
                ptidx = lookupregion[0][0]
                point = self.points[int(ptidx)]
                if flipIsland is not None and point.island == flipIsland:
                    obj["side"] = point.side * -1
                else:
                    obj["side"] = point.side
            regions.append(obj)

        # The second loop goes over each region's neighbours and if a neighbour has a different side
        # Then we must be on opposite sides of a centerline and so try and find two points representing a wall between
        # These regions that we will add to our centerline
        centerlines = []
        for region in regions:
            for nidx in region['adjacents']:
                neighbour = regions[nidx]
                if neighbour['side'] != region['side']:

                    # Get the two shared points these two regions should have
                    # NOTE: set(A) - (set(A) - set(B)) is a great pattern
                    sharedpts = set(self.regions[region['id']]) - (set(self.regions[region['id']]) - set(self.regions[nidx]))

                    # Add this point to the list if it is unique
                    if -1 not in sharedpts:
                        lineseg = []
                        for e in sharedpts:
                            lineseg.append(self.vertices[e])
                        if len(lineseg) == 2:
                            centerlines.append(LineString(lineseg))

        merged = linemerge(unary_union(centerlines))

        # Sometimes what we get back is a ring. This is a tricky case because shapely may not choose the start and
        # endpoint correctly. So we have to cycle through and choose new ones
        if merged.is_closed:
            # Get the index of the farthest point outside the geometry
            candidates = []
            for ind, coord in enumerate(merged.coords):
                if not rivershape.contains(Point(coord)):
                    candidates.append((ind, rivershape.distance(Point(coord))))
            candidates.sort(key=lambda tup: tup[1])
            outsideind = candidates[-1][0]

            # Loop through the points with this transform to move the start/end point
            newline = []
            maxl = len(merged.coords)
            # Add the first point in
            newline.append(merged.coords[outsideind - 1])
            # Now add every other point
            for idx in range(0, maxl):
                t = idx + outsideind
                if t >= maxl:
                    t = t - maxl
                newline.append(merged.coords[t])

            merged = LineString(newline)

        return merged

    def createshapes(self):
        """
        Simple helper function to make polygons out of the untransformed (i.e. original) Voronoi vertices.
        We use this mainly for visualization
        :return:
        """
        polys = []
        for region in self.regions:
            if len(region) >= 3:
                regionVerts = [self.vertices[ptidx] for ptidx in region if ptidx >= 0]
                if len(regionVerts) >= 3:
                    polys.append(Polygon(regionVerts))
        self.polys = MultiPolygon(polys)

    def dissolve_by_property(self, property_name):
        """Group polygons by a property

        Args:
            property_name ([type]): [description]
        """

        poly_groups = {}

        progbar1 = ProgressBar(len(self.point_region), 50, "Grouping Polygons...")
        counter = 0
        progbar1.update(counter)

        for pt_id in range(len(self.point_region)):
            region_id = self.point_region[pt_id]
            fid = self.points[pt_id].properties[property_name]

            counter += 1
            progbar1.update(counter)

            region = self.regions[region_id]
            if len(region) >= 3:
                region_verts = [self.vertices[ptidx] for ptidx in region if ptidx >= 0]
                if len(region_verts) >= 3:
                    poly = Polygon(region_verts)
                    if fid not in poly_groups:
                        poly_groups[fid] = []
                    poly_groups[fid].append(poly)

        progbar1.finish()

        progbar2 = ProgressBar(len(poly_groups.values()), 50, "Dissolving...")
        counter = 0
        progbar2.update(counter)

        dissolved = {}
        for fid, group in poly_groups.items():
            dissolved[fid] = unary_union(group)

        return dissolved
