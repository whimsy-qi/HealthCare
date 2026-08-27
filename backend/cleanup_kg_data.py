import os
from dotenv import load_dotenv
from dashvector import Client

# 加载配置
load_dotenv()


def cleanup():
    # 1. 初始化客户端
    api_key = os.getenv("DASHVECTOR_API_KEY")
    endpoint = os.getenv("DASHVECTOR_ENDPOINT")

    if not api_key or not endpoint:
        print("❌ 错误：未找到环境变量，请检查 .env 文件")
        return

    dv_client = Client(api_key=api_key, endpoint=endpoint)
    collection = dv_client.get("multimodal_medical_db")

    # 2. 安全确认
    confirm = input("⚠️ 此操作将扫描并删除所有标签为 'medical_kg' 的旧数据。确定吗？(y/n): ")
    if confirm.lower() != 'y':
        print("🚪 操作已取消。")
        return

    print("🔍 正在扫描旧的 medical_kg 数据...")

    # 3. 分批查出所有符合条件的 ID
    # 由于可能数据较多，我们循环查询直到清空
    total_deleted = 0
    while True:
        # 使用 query 配合 filter 找 ID
        # 这里的 vector 传个全 0 即可，因为我们主要靠 filter 过滤
        res = collection.query(
            vector=[0.0] * 2560,
            filter="source = 'medical_kg'",
            topk=100,  # 每次查 100 条
            output_fields=[]  # 只要 ID，不需要其他字段
        )

        if not res or len(res) == 0:
            break

        # 提取 ID 列表
        ids_to_delete = [doc.id for doc in res]

        # 4. 执行删除
        del_res = collection.delete(ids=ids_to_delete)

        if del_res.code == 0:
            total_deleted += len(ids_to_delete)
            print(f"✅ 已清理 {total_deleted} 条记录...")
        else:
            print(f"❌ 删除过程中出错: {del_res.message}")
            break

    print(f"🎊 清理大功告成！累计从云端抹除 {total_deleted} 条旧逻辑数据。")


if __name__ == "__main__":
    cleanup()