import os
import json
import requests
from datasets import load_dataset


def download_medical_vision_samples(save_dir, num_samples=50):
    print(f"🚀 启动‘定向抓取模式’获取 PubMedVision 影像...")

    try:
        # 1. 加载数据集（仅加载文本信息，速度极快）
        dataset = load_dataset(
            "FreedomIntelligence/PubMedVision",
            "PubMedVision_Alignment_VQA",
            split="train",
            streaming=True
        )

        if not os.path.exists(save_dir):
            os.makedirs(save_dir)

        metadata = []
        count = 0

        # Hugging Face 原始文件的基准 URL
        base_url = "https://huggingface.co/datasets/FreedomIntelligence/PubMedVision/resolve/main/"

        for item in dataset:
            if count >= num_samples:
                break

            # 2. 提取图片路径字符串
            img_rel_path = item['image']
            if isinstance(img_rel_path, list):
                img_rel_path = img_rel_path[0]

            # 如果拿到的已经是 PIL 对象（万一成功了），处理一下
            if not isinstance(img_rel_path, str):
                img_name = f"huatuo_{count}.jpg"
                img_rel_path.save(os.path.join(save_dir, img_name))
            else:
                # 构造下载链接
                full_url = base_url + img_rel_path
                img_name = f"huatuo_{count}.jpg"
                img_path = os.path.join(save_dir, img_name)

                print(f"🌐 正在抓取: {full_url}")

                # 3. 发送请求下载二进制图片
                img_resp = requests.get(full_url, stream=True)
                if img_resp.status_code == 200:
                    with open(img_path, 'wb') as f:
                        f.write(img_resp.content)
                else:
                    print(f"⚠️ 下载失败 (状态码 {img_resp.status_code})，跳过...")
                    continue

            # 4. 提取专家描述
            conversations = item.get('conversations', [])
            description = "医学影像专业分析"
            if conversations:
                for conv in conversations:
                    if conv.get('from') == 'gpt':
                        description = conv.get('value')
                        break

            metadata.append({
                "file_name": img_name,
                "medical_description": f"部位: {item.get('body_part', 'N/A')}。描述: {description}",
                "source": "visual_stream"
            })

            print(f"✅ 已成功保存第 {count + 1} 张图文对")
            count += 1

        with open(os.path.join(save_dir, "image_metadata.json"), "w", encoding="utf-8") as f:
            json.dump(metadata, f, ensure_ascii=False, indent=4)

        print(f"🎊 抓取任务完成！共计 {count} 张。")

    except Exception as e:
        print(f"❌ 运行报错: {e}")


if __name__ == "__main__":
    target_dir = r"D:\Health_system\backend\test\huatuo_samples"
    download_medical_vision_samples(target_dir, num_samples=50)