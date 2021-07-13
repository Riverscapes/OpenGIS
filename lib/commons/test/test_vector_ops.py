""" Testing for the vector ops

"""
import unittest
import os
from tempfile import mkdtemp
from osgeo import ogr
from rscommons import vector_ops
from rscommons import Logger, ShapefileLayer, GeopackageLayer, initGDALOGRErrors
from rscommons.util import safe_remove_dir


initGDALOGRErrors()
log = Logger('RSCommons TEST')
log.setup(verbose=True)

datadir = os.path.join(os.path.dirname(__file__), 'data')


class VectorOpsTest(unittest.TestCase):
    """[summary]

    Args:
        unittest ([type]): [description]
    """

    def setUp(self):
        """[summary]
        """
        super(VectorOpsTest, self).setUp()
        self.outdir = mkdtemp()

    def tearDown(self):
        """[summary]
        """
        super(VectorOpsTest, self).tearDown()
        safe_remove_dir(self.outdir)

    def test_copy_feature_class(self):
        """[summary]
        """

        in_path = os.path.join(datadir, 'WBDHU12.shp')
        out_path = os.path.join(self.outdir, 'WBDHU12_copy.gpkg')

        vector_ops.copy_feature_class(in_path, os.path.join(out_path, 'WBDHU12_no_ref'), epsg=4326)

        with ShapefileLayer(in_path) as in_lyr, GeopackageLayer(os.path.join(out_path, 'WBDHU12_no_ref')) as out_lyr:
            numfeats_orig = in_lyr.ogr_layer.GetFeatureCount()
            numfeats1 = out_lyr.ogr_layer.GetFeatureCount()

        vector_ops.copy_feature_class(in_path, os.path.join(out_path, 'WBDHU12_ref'))
        with GeopackageLayer(os.path.join(out_path, 'WBDHU12_no_ref')) as out_lyr:
            numfeats2 = out_lyr.ogr_layer.GetFeatureCount()

        self.assertEqual(numfeats_orig, numfeats1)
        self.assertEqual(numfeats_orig, numfeats2)

    # def test_merge_feature_class(self):
    #     # WARNING: This should merge layers
    #     in_path = os.path.join(datadir, 'WBDHU12.shp')
    #     out_path = os.path.join(self.outdir, 'WBDHU12_merge.gpkg')

    #     clip_shapes = []

    #     with ShapefileLayer(in_path) as in_lyr:
    #         for clip_feat, _counter, _progbar in in_lyr.iterate_features("Gettingshapes"):
    #             shpobj =  GeopackageLayer.ogr2shapely(feature)
    #             clip_shapes.append(shpobj)

    #     with GeopackageLayer(out_path, 'WBDHU12_no_ref', write=True) as out_lyr:
    #         # Get a list of shapes to merge:
    #         merged_total = vector_ops.merge_feature_classes(clip_shapes, MultiPolygon(clip_shapes).boundary, out_lyr, epsg=4326)
    #         self.assertAlmostEqual(merged_total.area, 0.06580, 4)

    #     with ShapefileLayer(in_path) as in_lyr, GeopackageLayer(out_path, 'WBDHU12_ref', write=True) as out_lyr:
    #         vector_ops.copy_feature_class(in_lyr, out_lyr)

    #         numfeats2 = out_lyr.ogr_layer.GetFeatureCount()

    #     self.assertEqual(len(clip_shapes), numfeats1)
    #     self.assertEqual(len(clip_shapes), numfeats2)

    def test_print_geom_size(self):
        """[summary]
        """
        in_path = os.path.join(datadir, 'WBDHU12.shp')

        with ShapefileLayer(in_path) as in_lyr:
            for feature, _counter, progbar in in_lyr.iterate_features("GettingSize"):
                geom = GeopackageLayer.ogr2shapely(feature)
                progbar.erase()
                vector_ops.print_geom_size(log, geom)

    def test_get_geometry_union(self):
        """[summary]
        """
        in_path = os.path.join(datadir, 'WBDHU12.shp')
        # Use this for the clip shape
        clip_path = os.path.join(datadir, 'WBDHU10.shp')

        # This is the whole file unioned
        result_all = vector_ops.get_geometry_union(in_path, 4326)
        # This is one huc12
        result201 = vector_ops.get_geometry_union(in_path, 4326, attribute_filter="HUC12 = '170603040201'")
        result202 = vector_ops.get_geometry_union(in_path, 4326, attribute_filter="HUC12 = '170603040202'")
        result203 = vector_ops.get_geometry_union(in_path, 4326, attribute_filter="HUC12 = '170603040203'")
        result101 = vector_ops.get_geometry_union(in_path, 4326, attribute_filter="HUC12 = '170603040101'")
        result102 = vector_ops.get_geometry_union(in_path, 4326, attribute_filter="HUC12 = '170603040102'")
        result103 = vector_ops.get_geometry_union(in_path, 4326, attribute_filter="HUC12 = '170603040103'")
        # This is every huc12 with the pattern 1706030402%
        result20 = vector_ops.get_geometry_union(in_path, 4326, attribute_filter="HUC12 LIKE '1706030402%'")
        result10 = vector_ops.get_geometry_union(in_path, 4326, attribute_filter="HUC12 LIKE '1706030401%'")

        self.assertAlmostEqual(result_all.area, 0.06580, 4)
        self.assertAlmostEqual(result_all.area,
                               result201.area + result202.area + result203.area + result101.area + result102.area + result103.area,
                               4)

        self.assertAlmostEqual(result10.area, result101.area + result102.area + result103.area, 4)
        self.assertAlmostEqual(result20.area, result201.area + result202.area + result203.area, 4)

        # Build a library of shapes to clip
        clip_shapes = {}
        # Now test with clip_shape enabled
        with ShapefileLayer(clip_path) as clip_lyr:

            for clip_feat, _counter, _progbar in clip_lyr.iterate_features("Gettingshapes"):
                huc10 = clip_feat.GetFieldAsString("HUC10")
                clip_shapes[huc10] = GeopackageLayer.ogr2shapely(clip_feat)

            for huc10, clip_shape in clip_shapes.items():
                debug_path = os.path.join(datadir, 'test_get_geometry_union_{}.gpkg'.format(huc10))
                buffered_clip_shape = clip_shape.buffer(-0.004)
                # Write the clipping shape
                with GeopackageLayer(debug_path, 'CLIP_{}'.format(huc10), write=True) as deb_lyr:
                    deb_lyr.create_layer_from_ref(clip_lyr)
                    out_feature = ogr.Feature(deb_lyr.ogr_layer_def)
                    out_feature.SetGeometry(GeopackageLayer.shapely2ogr(buffered_clip_shape))
                    deb_lyr.ogr_layer.CreateFeature(out_feature)

                # This is every huc12 within a single huc 10 unioned
                result_clipped = vector_ops.get_geometry_union(in_path, clip_shape=buffered_clip_shape)

                with ShapefileLayer(in_path) as in_lyr, GeopackageLayer(debug_path, 'result_{}'.format(huc10), write=True) as deb_lyr:
                    deb_lyr.create(in_lyr.ogr_geom_type, spatial_ref=in_lyr.spatial_ref)
                    out_feature = ogr.Feature(deb_lyr.ogr_layer_def)
                    out_feature.SetGeometry(GeopackageLayer.shapely2ogr(result_clipped))
                    deb_lyr.ogr_layer.CreateFeature(out_feature)

                self.assertAlmostEqual(clip_shape.area, result_clipped.area, 4)

    def test_get_geometry_unary_union(self):
        """[summary]
        """
        in_path = os.path.join(datadir, 'WBDHU12.shp')
        # Use this for the clip shape
        clip_path = os.path.join(datadir, 'WBDHU10.shp')

        # This is the whole file unioned
        result_all = vector_ops.get_geometry_unary_union(in_path, 4326)
        # This is one huc12
        result201 = vector_ops.get_geometry_unary_union(in_path, 4326, attribute_filter="HUC12 = '170603040201'")
        result202 = vector_ops.get_geometry_unary_union(in_path, 4326, attribute_filter="HUC12 = '170603040202'")
        result203 = vector_ops.get_geometry_unary_union(in_path, 4326, attribute_filter="HUC12 = '170603040203'")
        result101 = vector_ops.get_geometry_unary_union(in_path, 4326, attribute_filter="HUC12 = '170603040101'")
        result102 = vector_ops.get_geometry_unary_union(in_path, 4326, attribute_filter="HUC12 = '170603040102'")
        result103 = vector_ops.get_geometry_unary_union(in_path, 4326, attribute_filter="HUC12 = '170603040103'")
        # This is every huc12 with the pattern 1706030402%
        result20 = vector_ops.get_geometry_unary_union(in_path, 4326, attribute_filter="HUC12 LIKE '1706030402%'")
        result10 = vector_ops.get_geometry_unary_union(in_path, 4326, attribute_filter="HUC12 LIKE '1706030401%'")

        self.assertAlmostEqual(result_all.area, 0.06580, 4)
        self.assertAlmostEqual(result_all.area,
                               result201.area + result202.area + result203.area + result101.area + result102.area + result103.area,
                               4)

        self.assertAlmostEqual(result10.area, result101.area + result102.area + result103.area, 4)
        self.assertAlmostEqual(result20.area, result201.area + result202.area + result203.area, 4)

        # Now test with clip_shape enabled
        with ShapefileLayer(clip_path) as clip_lyr:

            # Build a library of shapes to clip
            clip_shapes = {}
            for clip_feat, _counter, _progbar in clip_lyr.iterate_features("Gettingshapes"):
                huc10 = clip_feat.GetFieldAsString("HUC10")
                clip_shapes[huc10] = GeopackageLayer.ogr2shapely(clip_feat)

            for huc10, clip_shape in clip_shapes.items():
                debug_path = os.path.join(datadir, 'test_get_geometry_unary_union_{}.gpkg'.format(huc10))
                buffered_clip_shape = clip_shape.buffer(-0.004)
                # Write the clipping shape
                with GeopackageLayer(debug_path, 'CLIP_{}'.format(huc10), write=True) as deb_lyr:
                    deb_lyr.create_layer_from_ref(clip_lyr)
                    out_feature = ogr.Feature(deb_lyr.ogr_layer_def)
                    out_feature.SetGeometry(GeopackageLayer.shapely2ogr(buffered_clip_shape))
                    deb_lyr.ogr_layer.CreateFeature(out_feature)

                # This is every huc12 within a single huc 10 unioned
                result_clipped = vector_ops.get_geometry_union(in_path, clip_shape=buffered_clip_shape)

                with ShapefileLayer(in_path) as in_lyr, GeopackageLayer(debug_path, 'result_{}'.format(huc10), write=True) as deb_lyr:
                    deb_lyr.create(in_lyr.ogr_geom_type, spatial_ref=in_lyr.spatial_ref)
                    out_feature = ogr.Feature(deb_lyr.ogr_layer_def)
                    out_feature.SetGeometry(GeopackageLayer.shapely2ogr(result_clipped))
                    deb_lyr.ogr_layer.CreateFeature(out_feature)

                self.assertAlmostEqual(clip_shape.area, result_clipped.area, 4)

    def test_load_attributes(self):
        """[summary]
        """
        in_path = os.path.join(datadir, 'WBDHU10.shp')

        values = vector_ops.load_attributes(in_path, 'HUC10', ['GNIS_ID', 'HUC10', 'States', 'AreaSqKm'])

        self.assertEqual(len(values.keys()), 2)
        self.assertEqual(len(values['1706030401']), 4)

    def test_load_geometries(self):
        """[summary]
        """
        in_path = os.path.join(datadir, 'WBDHU10.shp')
        values = vector_ops.load_geometries(in_path, 'HUC10')
        self.assertEqual(len(values.keys()), 2)
        for shp_obj in values.values():
            self.assertGreater(shp_obj.area, 0)
