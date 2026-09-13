"""讓 aws_setup/ 下數字開頭的腳本能被測試 import:它們內部用 `from config import ...`,
config.py 只有把 aws_setup/ 本身放進 sys.path 才找得到。"""
import sys
from pathlib import Path

AWS_SETUP_DIR = Path(__file__).resolve().parents[1]
if str(AWS_SETUP_DIR) not in sys.path:
    sys.path.insert(0, str(AWS_SETUP_DIR))
