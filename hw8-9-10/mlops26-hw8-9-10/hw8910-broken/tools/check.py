import hashlib
from pathlib import Path
import sys
import unittest

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
loader = unittest.TestLoader()
suite = unittest.TestSuite(
    [
        loader.discover(str(PROJECT / "tests")),
        unittest.TestLoader().discover(str(PROJECT / "integration")),
    ]
)
result = unittest.TextTestRunner(verbosity=2).run(suite)
print(
    "PUBLIC_TESTS_SHA256="
    + hashlib.sha256((PROJECT / "tests/test_release.py").read_bytes()).hexdigest()
)
print(
    f"Итог: {result.testsRun}/{result.testsRun}"
    if result.wasSuccessful()
    else "Итог: FAIL"
)
print(
    "Отпечаток выданных тестов: "
    + hashlib.sha256(
        b"test_release.py" + (PROJECT / "tests/test_release.py").read_bytes()
    ).hexdigest()[:12]
)
raise SystemExit(0 if result.wasSuccessful() else 1)
