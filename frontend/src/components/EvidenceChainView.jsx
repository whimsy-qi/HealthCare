import React, { useMemo, useState } from 'react';
import { Tag, Tooltip, Collapse, Typography } from 'antd';
import {
  ExperimentOutlined, NodeIndexOutlined, FileTextOutlined,
  GlobalOutlined, DatabaseOutlined, UserOutlined, EyeOutlined,
  RightOutlined, DownOutlined
} from '@ant-design/icons';

const { Text } = Typography;

/**
 * EvidenceChainView
 * 输入：trace_data.evidence_chain，统一契约见 backend/core/evidence.py
 * 渲染：① 推理路径  ② 关键事实三元组  ③ 原始来源池
 */

const TYPE_META = {
  kg:      { color: '#7C3AED', icon: <DatabaseOutlined />,  label: '知识图谱' },
  pdf:     { color: '#0891B2', icon: <FileTextOutlined />,  label: '本地文献' },
  web:     { color: '#059669', icon: <GlobalOutlined />,    label: '公网检索' },
  image:   { color: '#DB2777', icon: <EyeOutlined />,       label: '影像识别' },
  profile: { color: '#D97706', icon: <UserOutlined />,      label: '患者档案' },
  legacy:  { color: '#64748B', icon: <FileTextOutlined />,  label: '历史卡片' },
};

const RELATION_COLORS = {
  // 用药
  '禁忌于':   '#DC2626',
  '相互作用': '#EA580C',
  '档案命中': '#7C3AED',
  '不良反应': '#D97706',
  '适应症':   '#059669',
  // 症状/疾病
  '可能提示': '#0891B2',
  '推荐就诊': '#059669',
  '观察特征': '#0EA5E9',
  '诊断方向': '#7C3AED',
  '辩论裁决': '#0F172A',
  // 辟谣
  '声称功效': '#94A3B8',
  '实际事实': '#14B8A6',
  '支持依据': '#16A34A',
  '反驳依据': '#DC2626',
  '命题分类': '#7C3AED',
  '风险等级': '#EA580C',
  '幻觉裁定': '#0F172A',
  // 报告解读（D5）
  '偏高':     '#DC2626',
  '偏低':     '#2563EB',
  '阳性':     '#DC2626',
  '阴性':     '#16A34A',
  '异常':     '#EA580C',
  '参考依据': '#0891B2',
};

function ConfidenceBadge({ value }) {
  if (value == null) return null;
  const v = Number(value);
  const color = v >= 0.85 ? '#16A34A' : v >= 0.6 ? '#D97706' : '#DC2626';
  return (
    <Tooltip title={`证据置信度 ${(v * 100).toFixed(0)}%`}>
      <span style={{
        fontSize: 11, color, border: `1px solid ${color}`, borderRadius: 4,
        padding: '0 4px', marginLeft: 6, fontWeight: 600
      }}>
        {(v * 100).toFixed(0)}%
      </span>
    </Tooltip>
  );
}

function RefBadge({ refId, refMap }) {
  const ref = refMap[refId];
  if (!ref) {
    return (
      <Tag style={{ fontSize: 11, marginRight: 4 }}>
        {String(refId).slice(0, 16)}
      </Tag>
    );
  }
  const meta = TYPE_META[ref.type] || TYPE_META.legacy;
  return (
    <Tooltip title={ref.snippet ? ref.snippet.slice(0, 200) : ref.label}>
      <Tag color={meta.color} style={{ fontSize: 11, marginRight: 4, cursor: 'help' }}>
        {meta.icon} {ref.label?.length > 18 ? ref.label.slice(0, 18) + '…' : ref.label}
      </Tag>
    </Tooltip>
  );
}

function ReasoningPath({ steps, refMap }) {
  if (!steps?.length) return null;
  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: '#475569', marginBottom: 8, display: 'flex', alignItems: 'center', gap: 4 }}>
        <NodeIndexOutlined style={{ color: '#0EA5E9' }} /> 推理路径
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {steps.map((s, i) => (
          <div key={i} style={{
            display: 'flex', alignItems: 'flex-start', gap: 8,
            padding: '6px 8px', background: '#F8FAFC', borderRadius: 6,
            borderLeft: '3px solid #0EA5E9', fontSize: 12
          }}>
            <span style={{
              minWidth: 22, height: 22, borderRadius: '50%',
              background: '#0EA5E9', color: '#fff', fontSize: 11,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontWeight: 700
            }}>{s.step}</span>
            <div style={{ flex: 1, lineHeight: 1.5 }}>
              <div style={{ fontWeight: 600, color: '#0F172A' }}>
                <Text code style={{ fontSize: 11 }}>{s.actor}</Text> · {s.action}
              </div>
              <div style={{ color: '#475569', marginTop: 2 }}>
                <span style={{ opacity: 0.7 }}>输入：</span>{s.input_summary}
              </div>
              <div style={{ color: '#0F172A', marginTop: 2 }}>
                <span style={{ opacity: 0.7 }}>输出：</span>{s.output_summary}
              </div>
              {Array.isArray(s.cited_refs) && s.cited_refs.length > 0 && (
                <div style={{ marginTop: 4, display: 'flex', flexWrap: 'wrap', gap: 2 }}>
                  {s.cited_refs.slice(0, 6).map((rid, j) => (
                    <RefBadge key={j} refId={rid} refMap={refMap} />
                  ))}
                  {s.cited_refs.length > 6 && (
                    <Tag style={{ fontSize: 11 }}>+{s.cited_refs.length - 6}</Tag>
                  )}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function TripleList({ triples, refMap }) {
  if (!triples?.length) return null;
  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: '#475569', marginBottom: 8, display: 'flex', alignItems: 'center', gap: 4 }}>
        <ExperimentOutlined style={{ color: '#7C3AED' }} /> 关键事实（{triples.length}）
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        {triples.map((t, i) => {
          const relColor = RELATION_COLORS[t.relation] || '#64748B';
          return (
            <div key={i} style={{
              display: 'flex', alignItems: 'center', gap: 6, padding: '4px 8px',
              background: '#FAFAF9', borderRadius: 6, fontSize: 12, flexWrap: 'wrap'
            }}>
              <span style={{ fontWeight: 600, color: '#0F172A' }}>{t.head}</span>
              <span style={{
                color: relColor, fontSize: 11, padding: '0 6px',
                border: `1px dashed ${relColor}`, borderRadius: 4
              }}>
                {t.relation}
              </span>
              <span style={{ fontWeight: 600, color: '#0F172A' }}>
                {t.tail}
                {t.tail_type && (
                  <span style={{ marginLeft: 4, fontSize: 11, color: '#94A3B8' }}>
                    ({t.tail_type})
                  </span>
                )}
              </span>
              <ConfidenceBadge value={t.confidence} />
              {t.source_id && <RefBadge refId={t.source_id} refMap={refMap} />}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function RefsPool({ refs }) {
  if (!refs?.length) return null;
  return (
    <Collapse
      ghost
      size="small"
      items={[{
        key: 'refs',
        label: (
          <span style={{ fontSize: 12, fontWeight: 600, color: '#475569' }}>
            <FileTextOutlined style={{ color: '#0891B2' }} /> 原始来源（{refs.length}）
          </span>
        ),
        children: (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {refs.map((r, i) => {
              const meta = TYPE_META[r.type] || TYPE_META.legacy;
              return (
                <div key={i} style={{
                  padding: '6px 8px', background: '#F8FAFC', borderRadius: 6,
                  borderLeft: `3px solid ${meta.color}`, fontSize: 12
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span style={{ color: meta.color }}>{meta.icon}</span>
                    <Text strong style={{ fontSize: 12 }}>{r.label}</Text>
                    <Tag color={meta.color} style={{ fontSize: 10 }}>{meta.label}</Tag>
                  </div>
                  {r.snippet && (
                    <div style={{
                      color: '#64748B', marginTop: 4, lineHeight: 1.5,
                      maxHeight: 60, overflow: 'hidden', textOverflow: 'ellipsis'
                    }}>
                      {r.snippet}
                    </div>
                  )}
                  {r.locator?.url && r.locator.url !== '#' && (
                    <a href={r.locator.url} target="_blank" rel="noreferrer"
                       style={{ fontSize: 11, color: meta.color }}>
                      查看原文 →
                    </a>
                  )}
                </div>
              );
            })}
          </div>
        )
      }]}
    />
  );
}

export default function EvidenceChainView({ chain }) {
  const [expanded, setExpanded] = useState(true);

  const refMap = useMemo(() => {
    const m = {};
    (chain?.refs || []).forEach(r => { if (r.ref_id) m[r.ref_id] = r; });
    return m;
  }, [chain]);

  if (!chain || (!chain.triples?.length && !chain.reasoning_path?.length && !chain.refs?.length)) {
    return null;
  }

  const conf = chain.confidence;
  const confColor = conf >= 0.85 ? '#16A34A' : conf >= 0.6 ? '#D97706' : '#DC2626';

  return (
    <div style={{
      background: 'linear-gradient(180deg, #FEFCE8 0%, #FFFFFF 100%)',
      border: '1px solid #FDE68A', borderRadius: 10, padding: 12,
      boxShadow: '0 1px 3px rgba(0,0,0,0.04)'
    }}>
      <div
        onClick={() => setExpanded(e => !e)}
        style={{
          display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer',
          marginBottom: expanded ? 12 : 0, userSelect: 'none'
        }}
      >
        {expanded ? <DownOutlined style={{ fontSize: 11, color: '#92400E' }} /> :
                    <RightOutlined style={{ fontSize: 11, color: '#92400E' }} />}
        <span style={{ fontWeight: 700, color: '#92400E', fontSize: 13 }}>
          🔍 证据链
        </span>
        {chain.final_claim && (
          <span style={{ color: '#78350F', fontSize: 12, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            · {chain.final_claim}
          </span>
        )}
        {conf != null && (
          <span style={{
            fontSize: 11, color: confColor, fontWeight: 600,
            border: `1px solid ${confColor}`, borderRadius: 4, padding: '0 6px'
          }}>
            置信 {(conf * 100).toFixed(0)}%
          </span>
        )}
      </div>

      {expanded && (
        <>
          <ReasoningPath steps={chain.reasoning_path} refMap={refMap} />
          <TripleList triples={chain.triples} refMap={refMap} />
          <RefsPool refs={chain.refs} />
        </>
      )}
    </div>
  );
}
