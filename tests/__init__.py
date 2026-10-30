import logging

# Expected failures (refusals in tests) are logged as warnings; keep the test output clean.
logging.getLogger("prcopilot").setLevel(logging.ERROR)
