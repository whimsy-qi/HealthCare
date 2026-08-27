"""
深度分析 cold vs warm A/B：
1. 配对级对比（每条改写题在 cold/warm 两轮中的结论是否变化）
2. 翻盘分析（哪些题 cold 答错但 warm 答对了？反之？）
3. 命中数与准确率的相关性
"""
import json
from pathlib import Path
from collections import defaultdict

ROOT = Path('experiments/results/insight_ab')
cold_rows = [json.loads(l) for l in open(ROOT / 'cold.jsonl', encoding='utf-8') if l.strip()]
warm_rows = [json.loads(l) for l in open(ROOT / 'warm.jsonl', encoding='utf-8') if l.strip()]
populate_rows = [json.loads(l) for l in open(ROOT / 'populate.jsonl', encoding='utf-8') if l.strip()]

# ====== 1. Cold vs Warm 配对对比 ======
print('=' * 78)
print('【1】配对级对比：同一改写题 cold vs warm 的结论变化')
print('=' * 78)

cold_by_id = {r['case_id']: r for r in cold_rows if 'error' not in r}
warm_by_id = {r['case_id']: r for r in warm_rows if 'error' not in r}

both_correct = 0; both_wrong = 0
cold_only_correct = 0; warm_only_correct = 0  # 翻盘
diffs = []
for cid in cold_by_id:
    if cid not in warm_by_id: continue
    c = cold_by_id[cid]
    w = warm_by_id[cid]
    cc = c['verdict_loose_correct']; wc = w['verdict_loose_correct']
    if cc and wc: both_correct += 1
    elif not cc and not wc: both_wrong += 1
    elif cc and not wc: cold_only_correct += 1  # warm 反而错了
    else: warm_only_correct += 1                # warm 答对了 cold 没答对
    diffs.append({
        'case_id': cid,
        'claim': c['claim'][:60],
        'cold_verdict': c['pred_verdict'],
        'warm_verdict': w['pred_verdict'],
        'cold_conf': c['pred_confidence'],
        'warm_conf': w['pred_confidence'],
        'cold_correct': cc,
        'warm_correct': wc,
        'cold_hits': c.get('insight_hit_count', 0),
        'warm_hits': w.get('insight_hit_count', 0),
        'gt': c['gt_verdict'],
        'flipped': cc != wc,
    })

n = len(diffs)
print(f'\n样本数: {n}')
print(f'  ✅ 都对：           {both_correct:>2}/{n} ({both_correct/n*100:.0f}%)')
print(f'  ❌ 都错：           {both_wrong:>2}/{n} ({both_wrong/n*100:.0f}%)')
print(f'  🆙 warm 翻盘对：    {warm_only_correct:>2}/{n} ({warm_only_correct/n*100:.0f}%)  ← 见解注入带来的净增益')
print(f'  ⚠️ warm 翻盘错：    {cold_only_correct:>2}/{n} ({cold_only_correct/n*100:.0f}%)  ← 见解注入带来的负面影响')
print(f'  净改善：{warm_only_correct - cold_only_correct:+d} 例')
print(f'  结论稳定率：{(both_correct + both_wrong)/n*100:.0f}%')

# ====== 2. 翻盘案例详情 ======
print('\n' + '=' * 78)
print('【2】翻盘明细：见解库带来的 verdict 改变')
print('=' * 78)
flips = [d for d in diffs if d['flipped']]
flips_to_correct = [d for d in flips if d['warm_correct']]
flips_to_wrong = [d for d in flips if not d['warm_correct']]

print(f'\n--- warm 由错→对 ({len(flips_to_correct)} 例)---')
for d in flips_to_correct:
    print(f"  [{d['case_id']}] (GT={d['gt']})")
    print(f"      cold: {d['cold_verdict']} (conf={d['cold_conf']:.2f}, hits={d['cold_hits']})")
    print(f"      warm: {d['warm_verdict']} (conf={d['warm_conf']:.2f}, hits={d['warm_hits']})  ✅")
    print(f"      claim: {d['claim']}...")

print(f'\n--- warm 由对→错 ({len(flips_to_wrong)} 例)---')
for d in flips_to_wrong:
    print(f"  [{d['case_id']}] (GT={d['gt']})")
    print(f"      cold: {d['cold_verdict']} (conf={d['cold_conf']:.2f})  ✅")
    print(f"      warm: {d['warm_verdict']} (conf={d['warm_conf']:.2f}, hits={d['warm_hits']})")
    print(f"      claim: {d['claim']}...")

# ====== 3. 命中数与准确率相关性 ======
print('\n' + '=' * 78)
print('【3】见解命中数 vs 准确率（仅 warm 阶段）')
print('=' * 78)
hit_buckets = defaultdict(list)
for d in diffs:
    h = d['warm_hits']
    bucket = '0' if h == 0 else '1' if h == 1 else '2+'
    hit_buckets[bucket].append(d['warm_correct'])
for b in ['0', '1', '2+']:
    rows = hit_buckets[b]
    if not rows: continue
    acc = sum(rows) / len(rows)
    print(f'  命中 {b} 条: n={len(rows):>2}  loose_acc={acc*100:.1f}%')

# ====== 4. 弃答率拆分 ======
print('\n' + '=' * 78)
print('【4】弃答率变化：库装满后系统更敢答了吗？')
print('=' * 78)
def abst(rows):
    return sum(1 for r in rows if r['pred_verdict'] == '尚无定论') / len(rows)
cold_abst = abst(list(cold_by_id.values()))
warm_abst = abst(list(warm_by_id.values()))
print(f'  cold 弃答率: {cold_abst*100:.1f}%')
print(f'  warm 弃答率: {warm_abst*100:.1f}%   ({(warm_abst-cold_abst)*100:+.1f}pp)')

# 弃答率→变敢答 后准确率如何？
abst_to_answer_correct = 0
abst_to_answer_wrong = 0
for d in diffs:
    if d['cold_verdict'] == '尚无定论' and d['warm_verdict'] != '尚无定论':
        if d['warm_correct']: abst_to_answer_correct += 1
        else: abst_to_answer_wrong += 1
print(f'\n  cold 弃答 → warm 改答的样本: {abst_to_answer_correct + abst_to_answer_wrong} 例')
print(f'    其中答对: {abst_to_answer_correct}')
print(f'    其中答错: {abst_to_answer_wrong}')
if (abst_to_answer_correct + abst_to_answer_wrong) > 0:
    rescue_acc = abst_to_answer_correct / (abst_to_answer_correct + abst_to_answer_wrong)
    print(f'    "脱敢答"准确率: {rescue_acc*100:.1f}%')
