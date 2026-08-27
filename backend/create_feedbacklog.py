import sys
import os

# 1. 强行把当前脚本所在的根目录加入 Python 环境变量，防止找不到包
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

# 2. 引入正确的包路径 (带有 core.)
from core.database import engine
from core.models import FeedbackLog

# 3. 执行建表
try:
    FeedbackLog.metadata.create_all(engine)
    print("✅ FeedbackLog 表创建成功！")
except Exception as e:
    print(f"❌ 建表失败: {e}")