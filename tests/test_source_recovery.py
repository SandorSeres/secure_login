"""Source importer refuses unsafe/unreviewed archives before writing files."""
import hashlib
from pathlib import Path
import tempfile
import unittest
import zipfile
from scripts.import_spark_candidate import validate, ImportErrorSafe


class SourceRecoveryTests(unittest.TestCase):
    def _archive(self, root, entries):
        archive = Path(root) / "source.zip"
        with zipfile.ZipFile(archive, "w") as z:
            for name, value in entries.items():
                z.writestr(name, value)
        return archive, hashlib.sha256(archive.read_bytes()).hexdigest()

    def test_source_validated_with_hash(self):
        with tempfile.TemporaryDirectory() as d:
            archive, digest = self._archive(d, {"api/app/main.py": "app=1"})
            self.assertEqual(validate(archive, digest),
                             [("api/app/main.py", b"app=1")])
            with self.assertRaises(ImportErrorSafe):
                validate(archive, "0" * 64)

    def test_rejects_traversal_and_embedded_private_key(self):
        with tempfile.TemporaryDirectory() as d:
            for entries in (
                {"../api/app/main.py": "app=1"},
                {"api/app/main.py": "app=1", "api/app/password.py": "-----BEGIN PRIVATE KEY-----"},
                {"api/app/../../etc/passwd": "bad", "api/app/main.py": "app=1"},
            ):
                archive, digest = self._archive(d, entries)
                with self.assertRaises(ImportErrorSafe):
                    validate(archive, digest)

    def test_missing_main_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            archive, digest = self._archive(d, {"api/app/config.py": "x=1"})
            with self.assertRaises(ImportErrorSafe):
                validate(archive, digest)


if __name__ == "__main__":
    unittest.main()
