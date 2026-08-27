"""Put the repo's own tooling on sys.path so tests can import it by module name."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
