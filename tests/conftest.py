def pytest_addoption(parser):
    parser.addoption("--run-gepa-smoke", action="store_true", default=False,
                     help="Explicitly allow real-provider GEPA smoke test (may incur cost)")
