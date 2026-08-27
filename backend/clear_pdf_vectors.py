import os
import time
import random
from dotenv import load_dotenv
from dashvector import Client

# 1. 环境初始化
load_dotenv()
DASHVECTOR_API_KEY = os.getenv("DASHVECTOR_API_KEY")
DASHVECTOR_ENDPOINT = os.getenv("DASHVECTOR_ENDPOINT")


def clear_expert_guide_data():
    """
    通过【先过滤查询 ID，再按 ID 删除】的策略，精准清空专家指南数据。
    彻底修复了 DashVectorResponse 的状态校验属性错误。
    """
    print("=" * 50)
    print("🧹 正在启动向量数据库精准清理程序...")
    print("=" * 50)

    try:
        dv_client = Client(
            api_key=DASHVECTOR_API_KEY,
            endpoint=DASHVECTOR_ENDPOINT
        )

        collection_name = "multimodal_medical_db"
        collection = dv_client.get(collection_name)

        if not collection:
            print(f"❌ 错误: 未能找到集合 '{collection_name}'。")
            return

        print(f"🔍 已连接至集合: {collection_name}")
        print(f"🚀 正在扫描 source = 'expert_guide' 的数据...\n")

        total_deleted = 0
        batch_size = 100  # DashVector 单次 query 推荐的最大 topk 之一

        while True:
            # 随机非零向量，破除余弦相似度分母为 0 的数学陷阱
            # 注：如果执行后提示维度不对，请把这里的 1024 改成报错信息里提示的维度（如 1536 或 768）
            dummy_vector = [random.random() for _ in range(2560)]

            resp = collection.query(
                vector=dummy_vector,
                topk=batch_size,
                filter="source = 'expert_guide'",
                include_vector=False  # 不需要返回几千维的向量体，加快速度
            )

            # 🌟 修复：DashVector SDK 原生支持布尔值判断，失败直接拦截
            if not resp:
                print(f"⚠️ DashVector 拒绝了查询，状态码: {getattr(resp, 'code', '未知')}")
                print(f"🔍 详细原因: {getattr(resp, 'message', '未知错误')}")
                print("💡 提示：如果是 Dimension Mismatch（维度不匹配），请根据报错信息修改代码中 dummy_vector 的维度长度。")
                break

            # 提取这批数据的 ID
            # DashVectorResponse 内部包含可迭代的 Doc 对象
            ids_to_delete = [doc.id for doc in resp]

            # 如果查不到数据 ID，说明已经清理干净了
            if not ids_to_delete:
                break

            # 执行按 ID 批量删除
            del_resp = collection.delete(ids=ids_to_delete)

            if del_resp:
                total_deleted += len(ids_to_delete)
                print(f"✅ 成功清理 1 批，本批删除 {len(ids_to_delete)} 条，已累计删除 {total_deleted} 条记录...")
            else:
                print(f"⚠️ 删除该批次时发生异常: {getattr(del_resp, 'message', '未知错误')}")
                break

            time.sleep(0.5)  # 给云端数据库一点喘息时间

        print("=" * 50)
        print(f"🎊 清理任务圆满结束！共计安全删除了 {total_deleted} 条 PDF 知识块。")
        print("💡 现在你可以放心运行全新的 ingest_pdf.py 脚本重新入库了！")

    except Exception as e:
        print(f"❌ 清理过程中发生极其罕见的异常: {e}")


if __name__ == "__main__":
    confirm = input("⚠️  警告：此操作将扫描并删除所有 PDF 专家指南向量数据。确认执行？(y/n): ")
    if confirm.lower() == 'y':
        clear_expert_guide_data()
    else:
        print("🛑 操作已取消。")