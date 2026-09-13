"""讓 aws_setup/ 下數字開頭的腳本能被測試 import:它們內部用 `from config import ...`,
config.py 只有把 aws_setup/ 本身放進 sys.path 才找得到。"""
import os
import sys
from pathlib import Path

# 測試不讀開發者的 .env:先塞假值,config 匯入時的 load_dotenv(override=False) 就不會覆蓋
os.environ.setdefault("AWS_ACCOUNT_ID", "000000000000")

AWS_SETUP_DIR = Path(__file__).resolve().parents[1]
if str(AWS_SETUP_DIR) not in sys.path:
    sys.path.insert(0, str(AWS_SETUP_DIR))
