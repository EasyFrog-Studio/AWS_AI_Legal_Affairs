"""讓 test_scoring.py 從任何工作目錄都 import 得到 scoring(倉庫根跑 pytest 時會掃到這裡)。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
