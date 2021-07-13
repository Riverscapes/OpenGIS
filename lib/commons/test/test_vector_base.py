""" Testing for the vector base class

"""
import unittest
import os
from shapely.geometry import LineString
from rscommons import Logger, initGDALOGRErrors, GeopackageLayer, ShapefileLayer
from rscommons.classes.vector_base import VectorBase, VectorBaseException
from rscommons.util import safe_remove_dir


initGDALOGRErrors()
log = Logger('RSCommons TEST')
log.setup(verbose=True)

datadir = os.path.join(os.path.dirname(__file__), 'data')


class VectorBaseTest(unittest.TestCase):

    # def setUp(self):
    #     super(VectorBaseTest, self).setUp()
    #     self.outdir = mkdtemp()

    # def tearDown(self):
    #     super(VectorBaseTest, self).tearDown()
    #     safe_remove_dir(self.outdir)

    def test_path_sorter(self):
        """[summary]
        """

        # Exception Cases
        self.assertRaises(VectorBaseException, lambda: VectorBase.path_sorter(None, None))
        self.assertRaises(VectorBaseException, lambda: VectorBase.path_sorter(None, ''))
        self.assertRaises(VectorBaseException, lambda: VectorBase.path_sorter('', None))
        self.assertRaises(VectorBaseException, lambda: VectorBase.path_sorter('', ''))
        self.assertRaises(VectorBaseException, lambda: VectorBase.path_sorter('   ', '   '))

        # Simple cases first:
        realfile_path = os.path.join(datadir, 'sample.gpkg')
        self.assertEqual(VectorBase.path_sorter('/path/to/file.gpkg', 'layer_name'), ('/path/to/file.gpkg', 'layer_name'))

        # Real file is there
        self.assertEqual(VectorBase.path_sorter(realfile_path), (realfile_path, None))

        # Now we start to get into the detected cases:
        self.assertEqual(VectorBase.path_sorter('/path/to/file.gpkg/layer_name'), ('/path/to/file.gpkg', 'layer_name'))
        self.assertEqual(VectorBase.path_sorter('/path/to/file.gpkg/layer_name\\/LASDASDAS'), ('/path/to/file.gpkg', 'layer_name\\/LASDASDAS'))
        self.assertEqual(VectorBase.path_sorter('/path/file.gpkg/thing.shp/to/file.gpkg/layer_name\\/LASDASDAS'), ('/path/file.gpkg/thing.shp/to/file.gpkg', 'layer_name\\/LASDASDAS'))
        self.assertEqual(VectorBase.path_sorter('/path/to /file.gpkg\\layer_name'), ('/path/to /file.gpkg', 'layer_name'))
        self.assertEqual(VectorBase.path_sorter('D:\\path\\to\\file.gpkg\\layer_name'), ('D:\\path\\to\\file.gpkg', 'layer_name'))

        print('hi')

    def test_ogr2shapely(self):
        """[summary]
        """

        # test bad objects
        self.assertRaises(VectorBaseException, lambda: GeopackageLayer.ogr2shapely("this is not valid"))

        with GeopackageLayer(os.path.join(datadir, 'sample.gpkg', 'WBDHU12')) as gpkg_lyr:
            for feat, _counter, _prog in gpkg_lyr.iterate_features():
                geom = feat.GetGeometryRef()
                shply_obj = GeopackageLayer.ogr2shapely(feat)
                self.assertTrue(shply_obj.is_valid)
                self.assertFalse(shply_obj.has_z)

                self.assertTrue(shply_obj.area > 0)
                self.assertAlmostEqual(geom.Area(), shply_obj.area, 6)

                # Make sure it works with geometries as well as features
                shply_obj = GeopackageLayer.ogr2shapely(geom)
                self.assertTrue(shply_obj.is_valid)
                self.assertFalse(shply_obj.has_z)

                self.assertTrue(shply_obj.area > 0)
                self.assertAlmostEqual(geom.Area(), shply_obj.area, 6)

        with ShapefileLayer(os.path.join(datadir, 'NHDFlowline.shp')) as shp_lyr:
            for feat, _counter, _prog in shp_lyr.iterate_features():
                geom = feat.GetGeometryRef()
                shply_obj = GeopackageLayer.ogr2shapely(feat)
                self.assertTrue(shply_obj.is_valid)
                self.assertFalse(shply_obj.has_z)

                self.assertTrue(shply_obj.length > 0)
                self.assertEqual(geom.Length(), shply_obj.length)

    def test_shapely2ogr(self):
        """[summary]
        """
        linestring = LineString([[0, 0, 0], [0, 1, 2], [1, 2, 3]])
        ogr_obj = GeopackageLayer.shapely2ogr(linestring)
        self.assertTrue(ogr_obj.IsValid())
        self.assertFalse(ogr_obj.Is3D())
        self.assertFalse(ogr_obj.IsMeasured())

        self.assertTrue(ogr_obj.Length() > 0)
        self.assertEqual(ogr_obj.Length(), linestring.length)
