import json
import os
from collections import Counter


def analyze_medical_json(file_path):
    dept_counts = Counter()
    total_records = 0

    # 检查文件是否存在
    if not os.path.exists(file_path):
        print(f"❌ 错误：在路径 [{file_path}] 未找到文件。")
        print("💡 请确认文件是否真的在 scripts 文件夹下，或者文件名大小写是否完全一致。")
        return

    print(f"🔍 正在读取并分析文件: {file_path} ...")

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    total_records += 1
                    # 提取 cure_department 字段
                    depts = data.get('cure_department', [])
                    if not depts:
                        dept_counts["未分类"] += 1
                    for dept in depts:
                        dept_counts[dept] += 1
                except json.JSONDecodeError:
                    print(f"⚠️ 第 {line_num} 行 JSON 格式损坏，已跳过。")
                    continue
    except Exception as e:
        print(f"❌ 读取文件时发生错误: {e}")
        return

    # 按数量降序排序
    sorted_depts = sorted(dept_counts.items(), key=lambda x: x[1], reverse=True)

    print("\n" + "=" * 50)
    print(f"📊 医学知识库科室分布报告")
    print(f"📂 路径: {file_path}")
    print(f"📈 总计疾病条数: {total_records}")
    print("=" * 50)
    print(f"{'科室名称':<15} | {'疾病条数':<10}")
    print("-" * 30)

    for dept, count in sorted_depts:
        print(f"{dept:<15} | {count:<10}")

    print("=" * 50)
    print("💡 建议：记录下排名前几位的科室名称，填入入库脚本的 TARGET_DEPTS 中。")


if __name__ == "__main__":
    # 🌟 使用 r'' 确保 Windows 路径被正确解析
    target_file = r'D:\Health_system\backend\scripts\medical.json'
    analyze_medical_json(target_file)