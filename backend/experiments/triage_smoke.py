"""Triage prompt 边界修正后的快速验证脚本。
跑一组对照样本，打印 primary_intent / sub_intent 是否符合预期。
"""
import sys, asyncio, io
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# Windows 控制台强制 UTF-8，避免 emoji 打印崩
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from agents.triage_agent import triage_query

CASES = [
    # (query, expected_primary, expected_sub)
    ("我头疼",                      "SYMPTOM_ANALYSIS",      "DIAGNOSIS"),
    ("胸痛",                        "SYMPTOM_ANALYSIS",      "DIAGNOSIS"),
    ("最近老咳嗽，已经两周了",       "SYMPTOM_ANALYSIS",      "DIAGNOSIS"),
    ("肚子不舒服",                   "SYMPTOM_ANALYSIS",      "DIAGNOSIS"),
    ("我头疼该吃什么药",             "GENERAL_CONSULTATION",  "TREATMENT"),
    ("摔破了怎么处理",               "GENERAL_CONSULTATION",  "TREATMENT"),
    ("维生素D有什么作用",            "GENERAL_CONSULTATION",  "GENERAL"),
    ("我能吃布洛芬吗",               "MEDICATION_REVIEW",     None),  # sub 不强约束
    ("高血压吃阿司匹林会伤胃吗",      "MEDICATION_REVIEW",     None),
    ("骨头汤补钙是真的吗",           "RUMOR_VERIFICATION",    "FACT_CHECK"),
    ("你好",                         "CHITCHAT_OR_REJECT",    "GREETING"),
]

async def main():
    total, passed = 0, 0
    for q, want_p, want_s in CASES:
        res = await triage_query(q, history=[], has_image=False)
        got_p = res.get("primary_intent")
        got_s = res.get("sub_intent")
        ok_p = got_p == want_p
        ok_s = (want_s is None) or got_s == want_s
        ok = ok_p and ok_s
        mark = "PASS" if ok else "FAIL"
        total += 1
        passed += int(ok)
        print(f"{mark} '{q}'  →  {got_p}/{got_s}  (期望 {want_p}/{want_s or '*'})")
    print(f"\n{passed}/{total} 通过")

if __name__ == "__main__":
    asyncio.run(main())
