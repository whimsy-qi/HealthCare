import os
from dotenv import load_dotenv
from dashvector import Client

load_dotenv()
client = Client(api_key=os.getenv("DASHVECTOR_API_KEY"), endpoint=os.getenv("DASHVECTOR_ENDPOINT"))
collection = client.get("multimodal_medical_db")

# 1. 查看统计
stats = collection.stats()
print(f"📊 统计接口返回: {stats.output}")

# 2. 尝试真正的查询
res = collection.query(vector=[0.1]*1024, topk=1)

# 打印完整的响应结构，看看里面到底是什么
print(f"📡 查询响应内容: {res}")

if res and len(res) > 0:
    print(f"💡 确认发现数据！ID 为: {res[0].id}")
    print(f"📝 内容预览: {res[0].fields.get('content', '无内容')[:50]}")
else:
    print("⚠️ 结论：库里确实没有有效 Doc 数据。")