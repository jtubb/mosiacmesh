#!/usr/bin/env python3
"""
Simple test runner that directly imports and runs pytest
This avoids module import conflicts
"""
import sys
import os
from pathlib import Path

# Add project root to Python path  
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

# Now import pytest and run it directly
try:
    import pytest
except ImportError:
    print("Installing pytest...")
    import subprocess
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', 
                          'pytest', 'pytest-asyncio', 'pytest-cov'])
    import pytest

def main():
    """Run tests with pytest"""
    
    # Default arguments
    args = [
        'tests/unit',
        'tests/integration', 
        '-c', 'tests/pytest.ini',
        '-v'
    ]
    
    # Parse command line
    if len(sys.argv) > 1:
        if '--unit' in sys.argv:
            args = ['tests/unit', '-c', 'tests/pytest.ini', '-v']
        elif '--integration' in sys.argv:
            args = ['tests/integration', '-c', 'tests/pytest.ini', '-v']
        elif '--help' in sys.argv or '-h' in sys.argv:
            print("Usage: python test_runner.py [--unit|--integration|--coverage]")
            print("  --unit        Run only unit tests")
            print("  --integration Run only integration tests")
            print("  --coverage    Run with coverage report")
            return 0
        
        if '--coverage' in sys.argv:
            args.extend(['--cov=server', '--cov-report=html', '--cov-report=term'])
    
    print(f"Running pytest with args: {' '.join(args)}")
    print("=" * 60)
    
    # Run pytest
    exit_code = pytest.main(args)
    
    if '--coverage' in sys.argv and exit_code == 0:
        print(f"\nCoverage report generated in: {PROJECT_ROOT}/htmlcov/index.html")
    
    return exit_code

if __name__ == '__main__':
    sys.exit(main())