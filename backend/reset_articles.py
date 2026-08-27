from core.database import engine
from core.models import Article

print("🚨 正在连接 MySQL 数据库...")
# 这行代码的威力在于：它只会精准删除 articles 这一张表，绝不会误伤 users 和 chat_sessions
Article.__table__.drop(engine)
print("✅ 砰！articles 表已成功从物理层面抹除！")