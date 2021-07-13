""" Testing for the vector ops

"""
import unittest
from rscommons import util


class UtilTest(unittest.TestCase):
    """[summary]

    Args:
        unittest ([type]): [description]
    """

    def test_pretty_duration(self):
        """[summary]
        """

        test1 = util.pretty_duration(0)
        self.assertEqual(test1, '0.0 seconds')

        test1 = util.pretty_duration(10)
        self.assertEqual(test1, '10.0 seconds')

        test1 = util.pretty_duration(100)
        self.assertEqual(test1, '1:40 minutes')

        test1 = util.pretty_duration(1000)
        self.assertEqual(test1, '16:40 minutes')

        test1 = util.pretty_duration(10000)
        self.assertEqual(test1, '2:46 hours')

        test1 = util.pretty_duration(100000)
        self.assertEqual(test1, '1 days, 3:46 hours')
