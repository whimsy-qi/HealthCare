# init_db.py
from core.database import engine, Base
# 必须导入模型，这样 SQLAlchemy 才能扫描到它们
from core.models import User, HealthProfile, HealthCheckinItem, UserHealthCheckin, ChatSession, ChatMessage, Article

print("⏳ 正在向 MySQL 引擎发送建表指令...")
Base.metadata.create_all(bind=engine)
print("✅ 数据库表结构初始化成功！")
