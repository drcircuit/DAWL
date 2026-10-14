"""Prepare, train or verify the Hypertrix scratch model."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from dawl_scratch.run import main
if __name__=='__main__':main()
