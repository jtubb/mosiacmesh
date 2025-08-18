#!/usr/bin/env python3
"""
Test runner script for MosaicMesh project
"""
import sys
import os
import subprocess
import argparse
from pathlib import Path

# Add project root to Python path
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))


def run_command(cmd, description):
    """Run a command and handle output"""
    print(f"\n{'='*60}")
    print(f"Running: {description}")
    print(f"Command: {' '.join(cmd)}")
    print('='*60)
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=PROJECT_ROOT)
        
        if result.stdout:
            print("STDOUT:")
            print(result.stdout)
        
        if result.stderr:
            print("STDERR:")
            print(result.stderr)
        
        if result.returncode != 0:
            print(f"Command failed with return code: {result.returncode}")
            return False
        else:
            print("Command completed successfully!")
            return True
            
    except FileNotFoundError:
        print(f"Error: Command not found. Make sure pytest is installed.")
        print("Install with: pip install pytest pytest-asyncio")
        return False
    except Exception as e:
        print(f"Error running command: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description='Run MosaicMesh tests')
    parser.add_argument('--unit', action='store_true', 
                       help='Run only unit tests')
    parser.add_argument('--integration', action='store_true',
                       help='Run only integration tests')
    parser.add_argument('--manual', action='store_true',
                       help='Run manual test tools')
    parser.add_argument('--coverage', action='store_true',
                       help='Run tests with coverage report')
    parser.add_argument('--verbose', '-v', action='store_true',
                       help='Verbose output')
    parser.add_argument('--markers', '-m', type=str,
                       help='Run tests matching given mark expression')
    parser.add_argument('--test-path', type=str,
                       help='Run specific test file or directory')
    
    args = parser.parse_args()
    
    # Check if pytest is available
    try:
        subprocess.run(['pytest', '--version'], 
                      capture_output=True, check=True)
        pytest_cmd = ['pytest']
    except (subprocess.CalledProcessError, FileNotFoundError):
        try:
            subprocess.run([sys.executable, '-m', 'pytest', '--version'], 
                          capture_output=True, check=True)
            pytest_cmd = [sys.executable, '-m', 'pytest']
        except (subprocess.CalledProcessError, FileNotFoundError):
            print("Error: pytest not found. Installing pytest...")
            try:
                subprocess.run([sys.executable, '-m', 'pip', 'install', 
                              'pytest', 'pytest-asyncio', 'pytest-cov'], 
                             check=True)
                print("pytest installed successfully!")
                pytest_cmd = [sys.executable, '-m', 'pytest']
            except subprocess.CalledProcessError:
                print("Failed to install pytest. Please install manually:")
                print("pip install pytest pytest-asyncio pytest-cov")
                return 1
    
    # Build command
    cmd = pytest_cmd.copy()
    
    # Add test paths
    if args.unit:
        cmd.append('tests/unit')
    elif args.integration:
        cmd.append('tests/integration')
    elif args.manual:
        cmd.append('tests/manual')
    elif args.test_path:
        cmd.append(args.test_path)
    else:
        # Run all tests by default
        cmd.extend(['tests/unit', 'tests/integration'])
    
    # Add options
    if args.verbose:
        cmd.append('-v')
    
    if args.markers:
        cmd.extend(['-m', args.markers])
    
    if args.coverage:
        cmd.extend(['--cov=server', '--cov-report=html', '--cov-report=term'])
    
    # Add configuration
    cmd.extend(['-c', 'tests/pytest.ini'])
    
    # Run tests
    success = run_command(cmd, "MosaicMesh Test Suite")
    
    if args.coverage and success:
        print(f"\nCoverage report generated in: {PROJECT_ROOT}/htmlcov/index.html")
    
    # Run manual tests if requested
    if args.manual:
        print("\n" + "="*60)
        print("Manual test tools available in tests/manual/:")
        manual_dir = PROJECT_ROOT / "tests" / "manual"
        if manual_dir.exists():
            for file in manual_dir.iterdir():
                if file.suffix in ['.py', '.html']:
                    print(f"  - {file.name}")
        print("="*60)
    
    return 0 if success else 1


if __name__ == '__main__':
    exit(main())