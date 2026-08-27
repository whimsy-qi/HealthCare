import React, { useMemo, useState } from 'react';
import { Timeline, Tag, Typography, Space, Collapse, Tooltip, Modal, Badge } from 'antd';
import {
  ExperimentOutlined,
  SafetyCertificateOutlined,
  ThunderboltOutlined,
  CrownOutlined,
  EyeOutlined,
  BranchesOutlined,
  AimOutlined,
} from '@ant-design/icons';

const { Text, Paragraph } = Typography;

/**
 * MADDx 多智能体辩论可视化
 * 数据来源：trace_data.maddx_debate = { nodes: [...], edges: [...] }
 * 每个 node 形如 { id, label, agent, ts, preview, value }
 *
 * 视觉设计目标（答辩"杀手锏"）：
 *  - 时序流：Proposer → Critic → Defender → Critic → Defender → Moderator
 *  - 每个 agent 用独特配色 + 图标，让导师一眼看出角色
 *  - 候选列表以诊断卡片形式展开，objections 以"质疑-分类-证据"结构化
 *  - 终止原因高亮展示
 */

// ========= 角色外观映射 =========
const AGENT_META = {
  perception: { name: '感知层', color: '#64748B', icon: <AimOutlined />, bg: '#F1F5F9', border: '#CBD5E1' },
  proposer:   { name: '住院医师 (Proposer)', color: '#0EA5E9', icon: <ExperimentOutlined />, bg: '#F0F9FF', border: '#BAE6FD' },
  critic:     { name: '主治医师 (Critic)',   color: '#F43F5E', icon: <SafetyCertificateOutlined />, bg: '#FFF1F2', border: '#FECDD3' },
  defender:   { name: '住院医师 (Defender)', color: '#10B981', icon: <ThunderboltOutlined />, bg: '#F0FDF4', border: '#BBF7D0' },
  moderator:  { name: '内科科主任 (Moderator)', color: '#8B5CF6', icon: <CrownOutlined />, bg: '#FAF5FF', border: '#DDD6FE' },
};

// Proposer 在首轮叫 Proposer，后续被复用作 Defender —— 根据在 DAG 中的位置判断
function resolveAgent(node, idx, candidateDxCount) {
  const raw = node.agent || '';
  if (raw === 'proposer') {
    // 第一条 candidate_dx 是 Proposer，之后的都是 Defender
    if (node.label === 'candidate_dx') {
      const posAmongCandidates = candidateDxCount.consumed++;
      return posAmongCandidates === 0 ? AGENT_META.proposer : AGENT_META.defender;
    }
    return AGENT_META.proposer;
  }
  return AGENT_META[raw] || { name: raw || '未知', color: '#94A3B8', icon: <BranchesOutlined />, bg: '#F8FAFC', border: '#E2E8F0' };
}

// ========= 结构化值渲染 =========

const CandidateCards = ({ candidates }) => {
  if (!Array.isArray(candidates) || candidates.length === 0) {
    return <Text type="secondary" style={{ fontSize: 12 }}>（无候选）</Text>;
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      {candidates.map((c, i) => (
        <div key={i} style={{
          background: '#fff', border: '1px solid #E2E8F0', borderRadius: 8,
          padding: '8px 10px', display: 'flex', alignItems: 'center', gap: 8,
        }}>
          <Badge count={i + 1} style={{ backgroundColor: i === 0 ? '#14B8A6' : '#94A3B8' }} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: '#0F172A' }}>{c.disease || c.name || '—'}</div>
            {c.reasoning && (
              <div style={{ fontSize: 11, color: '#64748B', lineHeight: 1.5, marginTop: 2 }}>
                {c.reasoning}
              </div>
            )}
          </div>
          {typeof c.confidence === 'number' && (
            <Tag color={c.confidence >= 0.7 ? 'green' : c.confidence >= 0.4 ? 'orange' : 'default'} style={{ margin: 0, fontSize: 11 }}>
              {(c.confidence * 100).toFixed(0)}%
            </Tag>
          )}
        </div>
      ))}
    </div>
  );
};

const OBJECTION_TYPE_COLOR = {
  EVIDENCE_MISMATCH: 'red',
  EPIDEMIOLOGY_MISMATCH: 'volcano',
  MECHANISM_CONFLICT: 'orange',
  MISSING_DIFFERENTIAL: 'gold',
  CONFIDENCE_OVERSTATED: 'purple',
};

const ObjectionList = ({ objections }) => {
  if (!Array.isArray(objections) || objections.length === 0) {
    return (
      <div style={{
        background: '#F0FDF4', border: '1px solid #BBF7D0', borderRadius: 8,
        padding: '8px 10px', fontSize: 12, color: '#166534',
      }}>
        ✅ 无有效反驳（已达成共识）
      </div>
    );
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      {objections.map((o, i) => (
        <div key={i} style={{
          background: '#fff', border: '1px solid #FECDD3', borderRadius: 8,
          padding: '8px 10px',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
            <Tag color={OBJECTION_TYPE_COLOR[o.type] || 'red'} style={{ margin: 0, fontSize: 10 }}>
              {o.type || 'OBJECTION'}
            </Tag>
            <Text style={{ fontSize: 12, fontWeight: 600, color: '#991B1B' }}>
              针对：{o.target_disease || '—'}
            </Text>
          </div>
          <div style={{ fontSize: 12, color: '#475569', lineHeight: 1.5 }}>{o.reason || o.argument}</div>
          {Array.isArray(o.evidence_refs) && o.evidence_refs.length > 0 && (
            <div style={{ marginTop: 4, display: 'flex', flexWrap: 'wrap', gap: 4 }}>
              {o.evidence_refs.map((ref, j) => (
                <Tag key={j} color="default" style={{ margin: 0, fontSize: 10, fontFamily: 'monospace' }}>{ref}</Tag>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
};

const FinalDiagnosisCard = ({ report }) => {
  if (!report) return null;
  return (
    <div style={{
      background: 'linear-gradient(135deg, #FAF5FF 0%, #EDE9FE 100%)',
      border: '1px solid #DDD6FE', borderRadius: 10, padding: '12px 14px',
    }}>
      <div style={{ fontSize: 13, fontWeight: 700, color: '#6D28D9', marginBottom: 6 }}>
        🩺 最终诊断：{report.primary_dx}
        {typeof report.confidence === 'number' && (
          <Tag color="purple" style={{ marginLeft: 8, fontSize: 11 }}>
            {(report.confidence * 100).toFixed(0)}%
          </Tag>
        )}
      </div>
      {Array.isArray(report.differential_dx) && report.differential_dx.length > 0 && (
        <div style={{ fontSize: 12, color: '#4C1D95', marginBottom: 6 }}>
          <strong>鉴别诊断：</strong>{report.differential_dx.join(' / ')}
        </div>
      )}
      {Array.isArray(report.supporting_evidence) && report.supporting_evidence.length > 0 && (
        <div style={{ fontSize: 12, color: '#4C1D95', marginBottom: 4 }}>
          <strong>支持证据：</strong>
          <ul style={{ margin: '2px 0 0 0', paddingLeft: 18 }}>
            {report.supporting_evidence.slice(0, 4).map((e, i) => <li key={i}>{e}</li>)}
          </ul>
        </div>
      )}
      {Array.isArray(report.recommended_tests) && report.recommended_tests.length > 0 && (
        <div style={{ fontSize: 12, color: '#4C1D95' }}>
          <strong>建议检查：</strong>{report.recommended_tests.join('、')}
        </div>
      )}
      <div style={{ marginTop: 8, paddingTop: 8, borderTop: '1px dashed #C4B5FD', fontSize: 11, color: '#7C3AED' }}>
        终止原因：<Tag color="geekblue" style={{ margin: 0, fontSize: 10 }}>{report.termination_reason}</Tag>
        {' '}· 辩论轮数：{report.rounds_used}
      </div>
    </div>
  );
};

// ========= 主组件 =========

const MADDxDebateView = ({ dag }) => {
  const [rawModal, setRawModal] = useState({ open: false, node: null });

  const { timeline, stats } = useMemo(() => {
    if (!dag || !Array.isArray(dag.nodes) || dag.nodes.length === 0) {
      return { timeline: [], stats: null };
    }
    const sorted = [...dag.nodes].sort((a, b) => a.id - b.id);
    const counter = { consumed: 0 };

    const rows = sorted.map((n, idx) => {
      const meta = resolveAgent(n, idx, counter);
      return { node: n, meta };
    });

    // 统计信息
    const finalNode = sorted.find(n => n.label === 'final_diagnosis');
    const candidateRounds = sorted.filter(n => n.label === 'candidate_dx').length;
    const objectionRounds = sorted.filter(n => n.label === 'objections').length;

    return {
      timeline: rows,
      stats: {
        totalSteps: sorted.length,
        candidateRounds,
        objectionRounds,
        terminationReason: finalNode?.value?.termination_reason,
        roundsUsed: finalNode?.value?.rounds_used,
      },
    };
  }, [dag]);

  if (timeline.length === 0) {
    return <Text type="secondary" style={{ fontSize: 12 }}>暂无辩论溯源数据</Text>;
  }

  const renderValueCard = (node) => {
    const v = node.value;
    const label = node.label;

    if (label === 'symptoms') {
      return (
        <div style={{ fontSize: 12, color: '#475569' }}>
          <strong>症状输入：</strong>{Array.isArray(v) ? v.map(s => s.name).join('；') : String(v)}
        </div>
      );
    }
    if (label === 'patient_profile') {
      const keys = v && typeof v === 'object' ? Object.keys(v) : [];
      return (
        <div style={{ fontSize: 12, color: '#475569' }}>
          <strong>患者档案：</strong>
          {keys.length === 0 ? '未提供' : keys.map(k => `${k}=${v[k]}`).join('；')}
        </div>
      );
    }
    if (label === 'candidate_dx') {
      return <CandidateCards candidates={v} />;
    }
    if (label === 'objections') {
      return <ObjectionList objections={v} />;
    }
    if (label === 'final_diagnosis') {
      return <FinalDiagnosisCard report={v} />;
    }
    return (
      <Text style={{ fontSize: 12, color: '#64748B' }}>{node.preview}</Text>
    );
  };

  return (
    <div>
      {/* 顶部统计条 */}
      {stats && (
        <div style={{
          display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 14, padding: '10px 12px',
          background: 'linear-gradient(135deg, #EFF6FF 0%, #F5F3FF 100%)',
          border: '1px solid #DDD6FE', borderRadius: 10,
        }}>
          <Tag color="blue" style={{ margin: 0 }}>🧬 共 {stats.totalSteps} 步黑板事件</Tag>
          <Tag color="cyan" style={{ margin: 0 }}>📝 候选迭代 {stats.candidateRounds} 版</Tag>
          <Tag color="volcano" style={{ margin: 0 }}>⚖️ 质疑轮次 {stats.objectionRounds}</Tag>
          {stats.terminationReason && (
            <Tooltip title="辩论终止原因">
              <Tag color="purple" style={{ margin: 0 }}>🏁 {stats.terminationReason}</Tag>
            </Tooltip>
          )}
          {typeof stats.roundsUsed === 'number' && (
            <Tag color="geekblue" style={{ margin: 0 }}>🔁 完成 {stats.roundsUsed} 轮辩论</Tag>
          )}
        </div>
      )}

      {/* 时间线 */}
      <Timeline style={{ marginLeft: 4 }}>
        {timeline.map(({ node, meta }, idx) => (
          <Timeline.Item
            key={node.id}
            color={meta.color}
            dot={<span style={{
              display: 'inline-flex', width: 22, height: 22, borderRadius: '50%',
              background: meta.bg, border: `2px solid ${meta.color}`, color: meta.color,
              alignItems: 'center', justifyContent: 'center', fontSize: 11,
            }}>{meta.icon}</span>}
            style={{ paddingBottom: idx === timeline.length - 1 ? 0 : 18 }}
          >
            <div style={{
              background: meta.bg, border: `1px solid ${meta.border}`, borderRadius: 10,
              padding: '10px 12px',
            }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
                <Space size={6}>
                  <Text strong style={{ fontSize: 13, color: meta.color }}>{meta.name}</Text>
                  <Tag color="default" style={{ margin: 0, fontSize: 10, fontFamily: 'monospace' }}>
                    v{node.id} · {node.label}
                  </Tag>
                </Space>
                <Tooltip title="查看原始 JSON">
                  <EyeOutlined
                    style={{ color: '#94A3B8', cursor: 'pointer', fontSize: 13 }}
                    onClick={() => setRawModal({ open: true, node })}
                  />
                </Tooltip>
              </div>
              {renderValueCard(node, meta)}
            </div>
          </Timeline.Item>
        ))}
      </Timeline>

      <Modal
        title={`黑板原始数据 v${rawModal.node?.id} · ${rawModal.node?.label}`}
        open={rawModal.open}
        onCancel={() => setRawModal({ open: false, node: null })}
        footer={null}
        width={640}
      >
        <pre style={{
          background: '#0F172A', color: '#E2E8F0', padding: 12, borderRadius: 8,
          fontSize: 11, lineHeight: 1.6, maxHeight: 480, overflow: 'auto',
        }}>
          {rawModal.node ? JSON.stringify(rawModal.node.value, null, 2) : ''}
        </pre>
      </Modal>
    </div>
  );
};

export default MADDxDebateView;
