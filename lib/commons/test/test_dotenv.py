""" Testing for the vector ops

"""
import unittest
from rscommons.dotenv import replace_env_varts


class DotEnvTest(unittest.TestCase):
    """[summary]

    Args:
        unittest ([type]): [description]
    """

    def test_patterns(self):
        """[summary]
        """
        env = {
            "DATA_ROOT": 'FOUND1',
            "SECOND": 'FOUND2',
            "WINDOWS": '//\\\\/\\\"'
        }
        pattern = r'{env:([^}]+)}'

        new_str = replace_env_varts(pattern, "input:HUC_NO_REPLACE", env)
        self.assertEqual(new_str, "input:HUC_NO_REPLACE")

        new_str = replace_env_varts(pattern, "{env:WINDOWS}/blah/{env:WINDOWS}/blah/blah", env)
        self.assertEqual(new_str, "/\"/blah/\"/blah/blah")

        new_str = replace_env_varts(pattern, "{env:DATA_ROOT}/blah/{env:SECOND}/blah/blah", env)
        self.assertEqual(new_str, "FOUND1/blah/FOUND2/blah/blah")

        new_str = replace_env_varts(pattern, '"{env:DATA_ROOT}/{env:SECOND}/blah,/${input:SECOND}/blah/blah"', env)
        self.assertEqual(new_str, '"FOUND1/FOUND2/blah,/${input:SECOND}/blah/blah"')
