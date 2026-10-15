"""Repository entry point for the governed training recovery operator."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from dawl_training.recovery_cli import main

if __name__ == '__main__': main()
