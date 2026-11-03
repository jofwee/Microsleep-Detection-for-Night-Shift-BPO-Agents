import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))  # evalkit
sys.path.insert(0, str(HERE.parents[1] / "client"))  # the client's own code, which the evaluation measures
