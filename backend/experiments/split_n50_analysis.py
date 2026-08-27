"""
把 N=50 R10 ablation 详情拆成两组：原 25 + 改写 25，单独评估。
看改写版是否单独拉低了准确率。
"""
import json
from pathlib import Path
from collections import defaultdict

DETAILS = Path('experiments/results/rumor_n50_full/rumor_ablation_details.jsonl')

rows = []
with open(DETAILS, encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if line:
            rows.append(json.loads(line))

# 按 case_id 分组：xxx_v 是改写版，否则是原版
orig = [r for r in rows if 'error' not in r and not r['case_id'].endswith('_v')]
para = [r for r in rows if 'error' not in r and r['case_id'].endswith('_v')]

def agg(group):
    n = len(group)
    if n == 0: return {}
    return {
        'n': n,
        'strict_acc': round(sum(r['verdict_strict_correct'] for r in group) / n, 3),
        'loose_acc':  round(sum(r['verdict_loose_correct'] for r in group) / n, 3),
        'sign_acc':   round(sum(r['belief_sign_correct'] for r in group) / n, 3),
        'avg_conf':   round(sum(r['pred_confidence'] for r in group) / n, 3),
        'avg_lat':    round(sum(r['latency_sec'] for r in group) / n, 2),
        'avg_rounds': round(sum(r['rounds_used'] for r in group) / n, 2),
        'avg_tools':  round(sum(r['tool_calls'] for r in group) / n, 2),
        'abstain':    round(sum(1 for r in group if r['pred_verdict'] == '尚无定论') / n, 3),
        'avg_halluc_score': None,  # not in row
    }

orig_a = agg(orig)
para_a = agg(para)

print('=' * 78)
print('N=50 拆分：原 25 (orig) vs 改写 25 (para)')
print('=' * 78)
metrics = [
    ('n', '样本数'),
    ('strict_acc', '严格准确率'),
    ('loose_acc',  '宽松准确率'),
    ('sign_acc',   'belief 符号正确率'),
    ('avg_conf',   '平均置信度'),
    ('abstain',    '弃答率'),
    ('avg_rounds', '平均轮次'),
    ('avg_tools',  '平均工具调用'),
    ('avg_lat',    '平均延迟(秒)'),
]
print(f'{"指标":<22}{"原版":>12}{"改写版":>12}{"Δ":>14}')
print('-' * 78)
for k, name in metrics:
    o = orig_a.get(k); p = para_a.get(k)
    if isinstance(o, float) and isinstance(p, float):
        if k in ('strict_acc', 'loose_acc', 'sign_acc', 'abstain'):
            print(f'{name:<22}{o*100:>10.1f}% {p*100:>10.1f}% {(p-o)*100:>+12.1f}pp')
        else:
            print(f'{name:<22}{o:>12.3f}{p:>12.3f}{p-o:>+14.3f}')
    else:
        print(f'{name:<22}{str(o):>12}{str(p):>12}')

print()
print('=' * 78)
print('对照分析（原版逐条 vs 同一基础的改写版）')
print('=' * 78)
# 按 base case_id 配对（去掉 _v）
by_base = defaultdict(dict)
for r in orig:
    by_base[r['case_id']]['orig'] = r
for r in para:
    base = r['case_id'][:-2]  # 去掉 _v
    by_base[base]['para'] = r

n_pairs = 0
n_both_correct = 0
n_orig_only = 0
n_para_only = 0
n_neither = 0
for base, pair in by_base.items():
    if 'orig' in pair and 'para' in pair:
        n_pairs += 1
        oc = pair['orig']['verdict_loose_correct']
        pc = pair['para']['verdict_loose_correct']
        if oc and pc: n_both_correct += 1
        elif oc and not pc: n_orig_only += 1
        elif not oc and pc: n_para_only += 1
        else: n_neither += 1

print(f'配对样本：{n_pairs}')
print(f'  原+改 都对：    {n_both_correct} / {n_pairs}  ({n_both_correct/n_pairs*100:.0f}%)')
print(f'  仅原版对：       {n_orig_only}  (改写后变错)')
print(f'  仅改写版对：     {n_para_only}  (改写后反而对)')
print(f'  原+改 都错：    {n_neither}')
print()
robustness = (n_both_correct + n_neither) / n_pairs
print(f'改写鲁棒性（结论一致率）：{robustness*100:.1f}%')
