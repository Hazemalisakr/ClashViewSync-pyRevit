# -*- coding: utf-8 -*-
"""Master test runner for clashview_sync test suite."""
from __future__ import absolute_import, division, print_function

import os
import sys
import unittest

# Ensure extension lib directory is on sys.path
_LIB_DIR = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        os.path.join(os.path.dirname(__file__), "..", "lib"),
    )
)
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)

import test_models
import test_coordinate_transformer
import test_xml_parser
import test_element_resolver
import test_view_builder


def run_tests():
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    suite.addTests(loader.loadTestsFromModule(test_models))
    suite.addTests(loader.loadTestsFromModule(test_coordinate_transformer))
    suite.addTests(loader.loadTestsFromModule(test_xml_parser))
    suite.addTests(loader.loadTestsFromModule(test_element_resolver))
    suite.addTests(loader.loadTestsFromModule(test_view_builder))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
