"""Put the skill's scripts on sys.path so tests can import them by module name."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
