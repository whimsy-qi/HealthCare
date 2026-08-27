import os
import time
from dotenv import load_dotenv
import dashscope
from dashvector import Client, Doc

# 1. 环境初始化
load_dotenv()
os.environ['HTTP_PROXY'] = ''
os.environ['HTTPS_PROXY'] = ''
os.environ['ALL_PROXY'] = ''

dashscope.api_key = os.getenv("DASHSCOPE_API_KEY")
dv_client = Client(api_key=os.getenv("DASHVECTOR_API_KEY"), endpoint=os.getenv("DASHVECTOR_ENDPOINT"))
collection = dv_client.get("multimodal_medical_db")


def ingest_images(image_folder):
    print(f"🖼️ 开始处理多模态医学影像数据: {image_folder}")

    # 支持的图片格式
    valid_extensions = ('.png', '.jpg', '.jpeg')
    image_files = [f for f in os.listdir(image_folder) if f.lower().endswith(valid_extensions)]

    if not image_files:
        print("⚠️ 未发现有效的图片文件，请检查路径。")
        return

    for i, file_name in enumerate(image_files):
        file_path = os.path.join(image_folder, file_name)

        # 针对不同类型的图片，手动给出一个语义标签（帮助检索）
        # PM 建议：在实际毕设中，这些标签可以从文件名中提取
        label = "医学影像/临床照片"
        if "化验单" in file_name:
            label = "医学化验单截图"
        elif "药盒" in file_name:
            label = "药品外包装照片"
        elif "皮疹" in file_name:
            label = "临床皮肤症状照片"

        print(f"🚀 正在向量化第 {i + 1}/{len(image_files)} 张: {file_name} ({label})")

        # 核心：调用 Qwen3-VL 进行多模态向量化
        # input 中同时包含图片路径和文字标签，增强语义表达
        for retry in range(3):
            resp = dashscope.MultiModalEmbedding.call(
                model="qwen3-vl-embedding",
                input=[
                    {'image': file_path},
                    {'text': f"这是一张医学相关的图片：{label}"}
                ]
            )

            if resp.status_code == 200:
                vec = resp.output['embeddings'][0]['embedding']
                doc_id = f"img_{int(time.time() * 1000)}_{i}"

                # 入库，标记 source 为 visual_stream
                collection.insert(Doc(
                    id=doc_id,
                    vector=vec,
                    fields={
                        "source": "visual_stream",
                        "content": f"【视觉参考】文件名：{file_name}，类型：{label}",
                        "file_path": file_path  # 记录本地路径，方便前端展示
                    }
                ))
                print(f"✅ 入库成功: {file_name}")
                break
            else:
                print(f"⚠️ 失败重试 {retry + 1}/3: {resp.message}")
                time.sleep(2)

        time.sleep(0.5)


if __name__ == "__main__":
    # 💡 适配你的本地路径：D:\Health_system\backend\test\test_image
    image_dir = r"D:\Health_system\backend\test\test_image"
    ingest_images(image_dir)