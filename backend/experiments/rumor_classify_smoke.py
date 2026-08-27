"""Claim Classifier 边界样本对照测试。"""
import sys, asyncio, io
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from agents.rumor.claim_classifier import classify_claim

CASES = [
    # (claim, expected_primary)  —— expected 里 None 表示任意
    ("微波炉加热食物会致癌",                "CAUSAL"),
    ("苹果核含有氰化物会中毒",              "COMPOSITIONAL"),
    ("不粘锅的特氟龙涂层致癌",              "COMPOSITIONAL"),
    ("维生素C能预防感冒",                   "EFFICACY"),
    ("喝盐水可以治疗新冠",                  "EFFICACY"),
    ("一天要喝够8杯水才健康",               "DOSAGE"),
    ("每天吃10粒维C才能防感冒",             "DOSAGE"),
    ("头孢配酒立即死亡",                    "INTERACTION"),
    ("柚子不能和降压药一起吃",              "INTERACTION"),
    ("孕妇不能吃海鲜",                      "POPULATION"),
    ("糖尿病人不能吃任何水果",              "POPULATION"),
    ("疫情期间喝板蓝根能预防新冠",          "NOVEL_TREND"),
    ("某明星同款抗癌神方",                  "NOVEL_TREND"),
    ("发烧要捂汗才能退烧",                  "FOLKLORE"),
    ("骨头汤能补钙",                        "FOLKLORE"),
]

async def main():
    passed = 0
    for claim, want in CASES:
        res = await classify_claim(claim)
        ok = res.primary == want
        mark = "PASS" if ok else "FAIL"
        passed += int(ok)
        sec = f" / {res.secondary}({res.secondary_confidence:.2f})" if res.secondary else ""
        print(f"{mark} '{claim}' → {res.primary}({res.primary_confidence:.2f}){sec}  "
              f"(期望 {want})  [{res.reasoning}]")
    print(f"\n{passed}/{len(CASES)} 通过")

if __name__ == "__main__":
    asyncio.run(main())
