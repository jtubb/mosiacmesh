# MosaicMesh Test Suite

Comprehensive test suite for the MosaicMesh distributed display discovery system.

## Test Structure

```
tests/
├── __init__.py              # Test package initialization
├── conftest.py             # pytest configuration and fixtures
├── pytest.ini             # pytest settings
├── README.md               # This file
│
├── unit/                   # Unit tests for individual components
│   ├── __init__.py
│   ├── test_client_management.py     # Client object and lifecycle tests
│   ├── test_file_caching.py          # File caching system tests
│   ├── test_api_endpoints.py         # REST API endpoint tests
│   ├── test_image_processing.py      # ArUco/OpenCV functionality tests
│   └── test_websocket_handlers.py    # WebSocket message handler tests
│
├── integration/            # Integration tests for full system
│   ├── __init__.py
│   ├── test_discovery_system.py      # Complete discovery workflow tests
│   └── test_websocket.py            # WebSocket integration tests (moved)
│
└── manual/                 # Manual testing tools and demos
    ├── __init__.py
    ├── keep_alive_test.py           # Connection persistence testing (moved)
    └── test-client.html            # Demo client for manual testing (moved)
```

## Running Tests

### Quick Start

```bash
# Run all unit tests (recommended)
python pytest_runner.py --unit

# Run with coverage report
python pytest_runner.py --unit --coverage

# Run integration tests (some may fail - see known issues)
python pytest_runner.py --integration

# Run with verbose output
python pytest_runner.py --unit --verbose

# Alternative: Run directly with pytest
python -m pytest tests/unit/test_client_management.py -v
```

**Windows Users**: You can also use `run_tests.bat --unit` for a native Windows experience.

### Using pytest directly

```bash
# Install test dependencies
pip install -r requirements-test.txt

# Run all tests
pytest tests/

# Run specific test categories
pytest tests/unit/                    # Unit tests only
pytest tests/integration/             # Integration tests only

# Run tests with markers
pytest -m "not slow"                  # Skip slow tests
pytest -m "api"                       # Run only API tests
pytest -m "websocket"                 # Run only WebSocket tests

# Run with coverage
pytest --cov=server --cov-report=html tests/

# Run specific test file
pytest tests/unit/test_client_management.py

# Run specific test function
pytest tests/unit/test_client_management.py::TestClientClass::test_client_initialization
```

## Test Status

### ✅ Working Tests
- **Client Management** (`test_client_management.py`) - All 12 tests passing
  - Client object initialization and validation  
  - Auto-configuration logic for different device types
  - Client migration for backward compatibility
  - Discovery data formatting and retrieval

### 🔄 Tests Under Development  
- **File Caching** (`test_file_caching.py`) - Some functions need implementation
- **API Endpoints** (`test_api_endpoints.py`) - API functions need to be created
- **WebSocket Handlers** (`test_websocket_handlers.py`) - Handler functions need implementation
- **Image Processing** (`test_image_processing.py`) - ArUco functionality may not be fully implemented
- **Integration Tests** (`test_discovery_system.py`) - Depends on API implementation

## Test Categories

### Unit Tests

Test individual functions and classes in isolation:

- **Client Management** (`test_client_management.py`)
  - Client object initialization and validation
  - Auto-configuration logic for different device types
  - Client migration for backward compatibility
  - Discovery data formatting and retrieval

- **File Caching** (`test_file_caching.py`)
  - File caching with modification time tracking
  - Cache hit/miss statistics
  - Error handling for missing or corrupted files
  - Cache cleanup and memory management

- **API Endpoints** (`test_api_endpoints.py`)
  - REST API discovery endpoints (/api/discovery/*)
  - JSON request/response handling
  - Error responses for invalid requests
  - Device configuration via API

- **Image Processing** (`test_image_processing.py`)
  - ArUco marker detection and processing
  - Image format conversions and resizing
  - Camera calibration matrix calculations
  - Perspective correction algorithms

- **WebSocket Handlers** (`test_websocket_handlers.py`)
  - WebSocket message parsing and validation
  - Client connection/disconnection handling
  - Real-time status updates and broadcasting
  - Heartbeat and keep-alive mechanisms

### Integration Tests

Test complete system workflows:

- **Discovery System** (`test_discovery_system.py`)
  - Full device discovery and auto-configuration workflow
  - Multi-device discovery scenarios
  - Real-time status tracking and updates
  - API integration with WebSocket events

- **WebSocket Integration** (`test_websocket.py`)
  - End-to-end WebSocket communication
  - Multi-client scenarios and broadcasting
  - Connection persistence and recovery

### Manual Tests

Tools for manual testing and debugging:

- **Keep Alive Test** (`keep_alive_test.py`)
  - Connection persistence testing
  - Network reliability simulation
  - Performance monitoring

- **Test Client** (`test-client.html`)
  - Interactive demo client for manual testing
  - Real-time status visualization
  - Device capability demonstration

## Test Fixtures

The test suite includes comprehensive fixtures in `conftest.py`:

- `mock_settings`: Mock Settings object with sample displays
- `mock_client`: Mock Client object with realistic test data
- `mock_websocket_session`: Mock WebSocket session for handler testing
- `sample_discovery_data`: Sample discovery API response data
- `temp_settings_file`: Temporary settings file for persistence testing
- `reset_global_state`: Automatic cleanup between tests

## Test Markers

Tests are marked for selective running:

- `@pytest.mark.slow` - Tests that take longer to run
- `@pytest.mark.integration` - Integration tests
- `@pytest.mark.unit` - Unit tests
- `@pytest.mark.websocket` - WebSocket-related tests
- `@pytest.mark.api` - API endpoint tests
- `@pytest.mark.discovery` - Discovery system tests

## Dependencies

Test dependencies are defined in `requirements-test.txt`:

- `pytest` - Core testing framework
- `pytest-asyncio` - Async/await test support
- `pytest-cov` - Coverage reporting
- `pytest-html` - HTML test reports
- `aiohttp-pytest` - aiohttp integration testing

## Coverage

The test suite aims for high code coverage:

```bash
# Generate coverage report
python run_tests.py --coverage

# View HTML coverage report
open htmlcov/index.html
```

Coverage targets:
- Unit tests: >90% line coverage
- Integration tests: >80% workflow coverage
- Critical paths: 100% coverage (authentication, data persistence)

## Continuous Integration

Tests are designed to run in CI environments:

- All tests should pass on Windows, macOS, and Linux
- No external dependencies required for core tests
- Deterministic test results (no flaky tests)
- Fast execution (unit tests <30s, integration tests <2m)

## Writing New Tests

When adding new functionality:

1. **Write unit tests first** for new functions/classes
2. **Add integration tests** for new workflows
3. **Update fixtures** if new mock data is needed
4. **Add appropriate markers** for test categorization
5. **Update documentation** if test structure changes

### Example Unit Test

```python
def test_new_functionality(mock_settings, mock_client):
    \"\"\"Test description\"\"\"
    # Arrange
    server.settings = mock_settings
    
    # Act
    result = server.new_function(mock_client)
    
    # Assert
    assert result.expected_property == expected_value
```

### Example Integration Test

```python
@pytest.mark.asyncio
async def test_new_workflow(self):
    \"\"\"Test complete workflow\"\"\"
    # Setup test data
    # ... 
    
    # Execute workflow steps
    # ...
    
    # Verify end-to-end results
    assert final_state.is_expected()
```

## Troubleshooting

Common issues and solutions:

**Import Errors**: Make sure the project root is in Python path
```bash
export PYTHONPATH="${PYTHONPATH}:$(pwd)"
```

**Argparse Conflicts**: The test files include patches to avoid conflicts with server.py's argument parser. If you see argparse errors, use the provided test runners (`pytest_runner.py` or `run_tests.bat`).

**AsyncIO Errors**: Use `@pytest.mark.asyncio` for async test functions

**Fixture Not Found**: Check fixture is defined in `conftest.py` or local test file

**Tests Hang**: Check for infinite loops or missing await statements in async tests

For more help, see the [pytest documentation](https://docs.pytest.org/) or open an issue in the project repository.