import os
import pytest

def pytest_addoption(parser):
    parser.addoption("--api-url", default=os.getenv("PROVENA_API_URL", "http://localhost:5000"))
    parser.addoption("--api-key", default=os.getenv("PROVENA_API_KEY", "prov_sk_test"))

@pytest.fixture(scope="session")
def api_url(request):
    return request.config.getoption("--api-url")

@pytest.fixture(scope="session")
def api_key(request):
    return request.config.getoption("--api-key")
