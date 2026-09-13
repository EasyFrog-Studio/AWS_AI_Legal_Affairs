"""測試不讀開發者的 .env:先塞假值,模組匯入時的 load_dotenv(override=False) 就不會覆蓋。"""
import os

os.environ.setdefault("AWS_ACCOUNT_ID", "000000000000")
os.environ.setdefault("DEPLOY_ALLOWED_INGRESS", "203.0.113.1/32=test-only")
