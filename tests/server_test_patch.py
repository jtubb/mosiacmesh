"""
Patch for server.py to allow importing during tests
This replaces the argparse args with test-friendly defaults
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Mock argparse before importing server
class MockArgs:
    def __init__(self):
        self.Port = 3000
        self.Verbose = False

# Patch argparse in server module
import argparse
original_parse_args = argparse.ArgumentParser.parse_args

def mock_parse_args(self, args=None, namespace=None):
    if 'server.py' in str(sys.modules.get('__main__', '')):
        return MockArgs()
    else:
        return original_parse_args(self, args, namespace)

argparse.ArgumentParser.parse_args = mock_parse_args

# Now we can safely import server
import server

# Restore original parse_args 
argparse.ArgumentParser.parse_args = original_parse_args

# Export server module
__all__ = ['server']