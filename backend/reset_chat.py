from core.database import engine
from core.models import ChatMessage, ChatSession

print("🚨 清理旧表中...")
ChatMessage.__table__.drop(engine, checkfirst=True)
ChatSession.__table__.drop(engine, checkfirst=True)
print("✅ 旧表已清理，重启 API 服务时将自动创建带有 meta_data 的新表！")