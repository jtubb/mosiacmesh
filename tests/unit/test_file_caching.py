"""
Unit tests for file caching system
"""
import pytest
import os
import tempfile
import time
from unittest.mock import patch, mock_open
from pathlib import Path

# Import server with patches to avoid argparse conflicts
import sys
from pathlib import Path
from unittest.mock import MagicMock

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Mock args before import
import argparse
original_parse_args = argparse.ArgumentParser.parse_args

class MockArgs:
    def __init__(self):
        self.Port = 3000
        self.Verbose = False

def mock_parse_args(self, args=None, namespace=None):
    return MockArgs()

argparse.ArgumentParser.parse_args = mock_parse_args

try:
    import server
finally:
    # Restore original
    argparse.ArgumentParser.parse_args = original_parse_args


class TestFileCaching:
    """Test file caching functionality"""
    
    def test_get_cached_file_new_file(self):
        """Test caching a new file"""
        test_content = b"test file content"
        file_path = "test.txt"
        
        with patch('os.path.exists', return_value=True), \
             patch('os.path.getmtime', return_value=12345), \
             patch('builtins.open', mock_open(read_data=test_content)):
            
            result = server.get_cached_file(file_path)
            
            assert result == test_content
            assert file_path in server.file_cache
            assert server.file_cache[file_path]['content'] == test_content
            assert server.file_cache[file_path]['mtime'] == 12345
            assert server.cache_stats['misses'] == 1

    def test_get_cached_file_cached_file(self):
        """Test retrieving cached file"""
        test_content = b"cached content"
        file_path = "cached.txt"
        
        # Pre-populate cache
        server.file_cache[file_path] = {
            'content': test_content,
            'mtime': 12345
        }
        server.cache_stats['hits'] = 0
        
        with patch('os.path.exists', return_value=True), \
             patch('os.path.getmtime', return_value=12345):
            
            result = server.get_cached_file(file_path)
            
            assert result == test_content
            assert server.cache_stats['hits'] == 1

    def test_get_cached_file_modified_file(self):
        """Test file modified after caching"""
        old_content = b"old content"
        new_content = b"new content"
        file_path = "modified.txt"
        
        # Pre-populate cache with old content
        server.file_cache[file_path] = {
            'content': old_content,
            'mtime': 12345
        }
        
        with patch('os.path.exists', return_value=True), \
             patch('os.path.getmtime', return_value=54321), \
             patch('builtins.open', mock_open(read_data=new_content)):
            
            result = server.get_cached_file(file_path)
            
            assert result == new_content
            assert server.file_cache[file_path]['content'] == new_content
            assert server.file_cache[file_path]['mtime'] == 54321

    def test_get_cached_file_nonexistent(self):
        """Test handling nonexistent file"""
        file_path = "nonexistent.txt"
        
        with patch('os.path.exists', return_value=False):
            result = server.get_cached_file(file_path)
            
            assert result is None
            assert file_path not in server.file_cache

    def test_get_cached_file_read_error(self):
        """Test handling file read errors"""
        file_path = "error.txt"
        
        with patch('os.path.exists', return_value=True), \
             patch('os.path.getmtime', return_value=12345), \
             patch('builtins.open', side_effect=IOError("Read error")):
            
            result = server.get_cached_file(file_path)
            
            assert result is None
            assert file_path not in server.file_cache

    def test_cache_stats_tracking(self):
        """Test cache statistics are properly tracked"""
        server.cache_stats['hits'] = 0
        server.cache_stats['misses'] = 0
        server.file_cache.clear()
        
        file_path = "stats_test.txt"
        test_content = b"stats content"
        
        with patch('os.path.exists', return_value=True), \
             patch('os.path.getmtime', return_value=12345), \
             patch('builtins.open', mock_open(read_data=test_content)):
            
            # First access - should be a miss
            server.get_cached_file(file_path)
            assert server.cache_stats['misses'] == 1
            assert server.cache_stats['hits'] == 0
            
            # Second access - should be a hit
            server.get_cached_file(file_path)
            assert server.cache_stats['misses'] == 1
            assert server.cache_stats['hits'] == 1

    def test_close_file_pool(self):
        """Test file pool cleanup"""
        # Populate cache
        server.file_cache['test1.txt'] = {'content': b'test1', 'mtime': 12345}
        server.file_cache['test2.txt'] = {'content': b'test2', 'mtime': 67890}
        server.cache_stats['hits'] = 5
        server.cache_stats['misses'] = 3
        
        server.close_file_pool()
        
        assert len(server.file_cache) == 0
        assert server.cache_stats['hits'] == 0
        assert server.cache_stats['misses'] == 0


class TestFileOperations:
    """Test file operation utilities"""
    
    def test_secure_filename_basic(self):
        """Test basic filename sanitization"""
        if hasattr(server, 'secure_filename'):
            assert server.secure_filename("test.txt") == "test.txt"
            assert server.secure_filename("../../../etc/passwd") == "etc_passwd"
            assert server.secure_filename("file with spaces.txt") == "file_with_spaces.txt"
    
    def test_get_mime_type(self):
        """Test MIME type detection"""
        if hasattr(server, 'get_mime_type'):
            assert server.get_mime_type("test.html") == "text/html"
            assert server.get_mime_type("test.css") == "text/css"
            assert server.get_mime_type("test.js") == "application/javascript"
            assert server.get_mime_type("test.jpg") == "image/jpeg"
            assert server.get_mime_type("test.unknown") == "application/octet-stream"