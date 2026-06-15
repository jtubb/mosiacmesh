#!/usr/bin/env python3
"""
Isolated pytest runner that doesn't import server.py directly
This avoids the argparse conflict in server.py
"""
import subprocess
import sys
import os
from pathlib import Path

def main():
    """Run pytest directly without importing server.py"""
    
    # Change to project directory
    os.chdir(Path(__file__).parent)
    
    # Build pytest command
    cmd = ['python', '-m', 'pytest']
    
    # Parse our arguments
    if len(sys.argv) > 1:
        if '--js' in sys.argv:
            import glob
            files = sorted(glob.glob('tests/unit/js/test_*.js'))
            if not files:
                print('No JS tests found at tests/unit/js/test_*.js')
                sys.exit(0)
            print(f'Running {len(files)} JS test files via node --test...')
            rc = subprocess.call(['node', '--test'] + files)
            sys.exit(rc)
        elif '--e2e' in sys.argv:
            # PR-4b: Playwright browser-driven integration tests.
            # Requires `npm install` (adds ~150MB chromium download).
            # Run against a live dev server (default localhost:3000;
            # override via MM_BASE_URL env var).
            if not os.path.isdir('node_modules/playwright'):
                print('Playwright not installed. To run e2e tests:')
                print('  npm install')
                print('  npx playwright install chromium')
                print('  python pytest_runner.py --e2e')
                sys.exit(0)
            print('Running tests/e2e/run.js (requires dev server on '
                  + os.environ.get('MM_BASE_URL', 'http://localhost:3000')
                  + ')...')
            rc = subprocess.call(['node', 'tests/e2e/run.js'])
            sys.exit(rc)
        elif '--unit' in sys.argv:
            cmd.extend(['tests/unit'])
        elif '--integration' in sys.argv:
            cmd.extend(['tests/integration'])
        elif '--help' in sys.argv or '-h' in sys.argv:
            print("Usage: python pytest_runner.py [--unit|--integration|--js|--e2e|--coverage]")
            print("  --unit        Run only unit tests")
            print("  --integration Run only integration tests")
            print("  --js          Run Node-based JS unit tests under tests/unit/js/")
            print("  --e2e         Run Playwright browser tests under tests/e2e/")
            print("                (requires `npm install` + dev server running)")
            print("  --coverage    Run with coverage report")
            print("  --verbose     Verbose output")
            return 0
        else:
            # Default - run all tests
            cmd.extend(['tests/unit', 'tests/integration'])
        
        if '--coverage' in sys.argv:
            cmd.extend(['--cov=server', '--cov-report=html', '--cov-report=term'])
            
        if '--verbose' in sys.argv or '-v' in sys.argv:
            cmd.append('-v')
    else:
        # Default - run all tests
        cmd.extend(['tests/unit', 'tests/integration'])
    
    # Add pytest configuration
    cmd.extend(['-c', 'tests/pytest.ini'])
    
    print(f"Running: {' '.join(cmd)}")
    print("=" * 60)
    
    # Run the command
    try:
        result = subprocess.run(cmd, cwd=Path(__file__).parent)
        
        if '--coverage' in sys.argv and result.returncode == 0:
            print(f"\nCoverage report generated in: {Path.cwd()}/htmlcov/index.html")
        
        return result.returncode
        
    except FileNotFoundError:
        print("Error: pytest not found. Installing...")
        try:
            subprocess.run([sys.executable, '-m', 'pip', 'install', 
                          'pytest', 'pytest-asyncio', 'pytest-cov'])
            print("pytest installed. Please run again.")
            return 1
        except Exception as e:
            print(f"Failed to install pytest: {e}")
            return 1
    except Exception as e:
        print(f"Error running tests: {e}")
        return 1

if __name__ == '__main__':
    sys.exit(main())