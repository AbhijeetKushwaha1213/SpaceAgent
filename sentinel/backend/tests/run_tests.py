import unittest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.discover(start_dir=os.path.dirname(__file__), pattern="test_phase10_llm.py"))
    suite.addTests(loader.discover(start_dir=os.path.dirname(__file__), pattern="test_phase11_sovereign_llm.py"))
    suite.addTests(loader.discover(start_dir=os.path.dirname(__file__), pattern="test_phase12_evaluation.py"))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
