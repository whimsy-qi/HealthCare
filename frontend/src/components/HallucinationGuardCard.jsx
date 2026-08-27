import React, { useState } from 'react';
import { CheckCircleFilled, WarningFilled, CloseCircleFilled, SafetyCertificateFilled, RightOutlined, DownOutlined } from '@ant-design/icons';

/**
 * 🛡️ HallucinationGuardCard
 * --------------------------------------------------------------
 * 渲染"幻觉检测员"对当前回答做的 claim 级证据对齐结果。
 *
 * 设计原则：
 *  - PASS：极简单行徽章（不打扰用户、但表明系统有在工作）
 *  - WARN/REGENERATE：显眼的黄色卡片，可展开查看 per-claim 细节
 *  - ABSTAIN：红色边框警告卡片，表明系统主动弃答
 *  - 永远展示风险维度（domain_risk）+ 检测到的 claims 总数 + 命中证据条数
 *
 * 这是【可信度】可视化的核心面板 —— 答辩时最直观能讲的视觉证据。
 */

const ACTION_META = {
  PASS: {
    color: '#10B981', bg: 'rgba(16, 185, 129, 0.08)', border: 'rgba(16, 185, 129, 0.30)',
    icon: <CheckCircleFilled />, label: '证据对齐通过', tone: 'good',
  },
  WARN: {
    color: '#D97706', bg: 'rgba(245, 158, 11, 0.10)', border: 'rgba(245, 158, 11, 0.40)',
    icon: <WarningFilled />, label: '可信度提示', tone: 'warn',
  },
  REGENERATE: {
    color: '#D97706', bg: 'rgba(245, 158, 11, 0.10)', border: 'rgba(245, 158, 11, 0.40)',
    icon: <WarningFilled />, label: '建议复核', tone: 'warn',
  },
  ABSTAIN: {
    color: '#DC2626', bg: 'rgba(220, 38, 38, 0.08)', border: 'rgba(220, 38, 38, 0.40)',
    icon: <CloseCircleFilled />, label: '系统主动弃答', tone: 'danger',
  },
};

const VERDICT_META = {
  SUPPORTED:    { label: '✅ 证据支持',     color: '#10B981' },
  PARTIAL:      { label: '🟡 部分支持',     color: '#D97706' },
  UNSUPPORTED:  { label: '⚪ 证据未提及',   color: '#64748B' },
  CONTRADICTED: { label: '🔴 与证据矛盾',   color: '#DC2626' },
};

const RISK_META = {
  LOW:    { label: 'LOW',    color: '#10B981' },
  MEDIUM: { label: 'MEDIUM', color: '#D97706' },
  HIGH:   { label: 'HIGH',   color: '#DC2626' },
};

const HallucinationGuardCard = ({ report }) => {
  const [expanded, setExpanded] = useState(false);

  if (!report || typeof report !== 'object') return null;

  const action = (report.action || 'PASS').toUpperCase();
  const meta = ACTION_META[action] || ACTION_META.PASS;
  const stats = report.stats || {};
  const claims = Array.isArray(report.claims) ? report.claims : [];
  const score = typeof report.hallucination_score === 'number' ? report.hallucination_score : 0;
  const confidence = typeof report.confidence === 'number'
    ? report.confidence
    : Math.max(0, 1 - score);

  // ============= PASS：极简单行徽章 =============
  if (action === 'PASS') {
    return (
      <div style={{
        display: 'inline-flex', alignItems: 'center', gap: 8,
        background: meta.bg, border: `1px solid ${meta.border}`,
        color: meta.color, padding: '6px 12px', borderRadius: 999,
        fontSize: 12, fontWeight: 600, alignSelf: 'flex-start',
      }}>
        <SafetyCertificateFilled style={{ fontSize: 13 }} />
        🛡️ 幻觉检测员：{meta.label}
        <span style={{ opacity: 0.7, fontWeight: 500, marginLeft: 4 }}>
          {stats.n_claims || 0} 条声明 · 可信度 {Math.round(confidence * 100)}%
        </span>
      </div>
    );
  }

  // ============= WARN / REGENERATE / ABSTAIN：可展开卡片 =============
  return (
    <div style={{
      background: meta.bg, border: `1px solid ${meta.border}`,
      borderRadius: 12, padding: '14px 16px', fontSize: 13,
      transition: 'all 0.2s ease',
    }}>
      {/* 标题行 */}
      <div
        onClick={() => setExpanded(v => !v)}
        style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          cursor: 'pointer', userSelect: 'none',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ color: meta.color, fontSize: 16 }}>{meta.icon}</span>
          <span style={{ fontWeight: 700, color: meta.color, fontSize: 14 }}>
            🛡️ 幻觉检测员 · {meta.label}
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, color: meta.color, fontSize: 12, fontWeight: 600 }}>
          <span>幻觉分 {score.toFixed(2)}</span>
          <span style={{ opacity: 0.5 }}>|</span>
          <span>可信度 {Math.round(confidence * 100)}%</span>
          {expanded ? <DownOutlined style={{ fontSize: 10 }} /> : <RightOutlined style={{ fontSize: 10 }} />}
        </div>
      </div>

      {/* 摘要 */}
      {report.summary && (
        <div style={{ marginTop: 10, color: '#334155', lineHeight: 1.6 }}>
          {report.summary}
        </div>
      )}

      {/* 统计条：声明分布 */}
      {claims.length > 0 && (
        <div style={{
          marginTop: 12, display: 'flex', gap: 8, flexWrap: 'wrap', fontSize: 11,
        }}>
          {[
            ['SUPPORTED',    stats.n_supported,    '#10B981'],
            ['PARTIAL',      stats.n_partial,      '#D97706'],
            ['UNSUPPORTED',  stats.n_unsupported,  '#64748B'],
            ['CONTRADICTED', stats.n_contradicted, '#DC2626'],
          ].filter(([, n]) => (n || 0) > 0).map(([k, n, c]) => (
            <span key={k} style={{
              background: '#fff', color: c, border: `1px solid ${c}33`,
              padding: '2px 8px', borderRadius: 999, fontWeight: 600,
            }}>
              {VERDICT_META[k]?.label || k} · {n}
            </span>
          ))}
        </div>
      )}

      {/* 展开详情：per-claim */}
      {expanded && claims.length > 0 && (
        <div style={{ marginTop: 14, borderTop: `1px dashed ${meta.border}`, paddingTop: 12 }}>
          <div style={{ fontSize: 12, color: '#64748B', fontWeight: 600, marginBottom: 8 }}>
            🔍 逐条核查明细（共 {claims.length} 条）：
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {claims.map((c, idx) => {
              const vm = VERDICT_META[c.verdict] || VERDICT_META.UNSUPPORTED;
              const rm = RISK_META[c.risk] || RISK_META.MEDIUM;
              return (
                <div key={idx} style={{
                  background: '#fff', borderRadius: 10,
                  border: `1px solid ${vm.color}33`,
                  padding: '10px 12px', fontSize: 12, lineHeight: 1.55,
                }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6, gap: 8, flexWrap: 'wrap' }}>
                    <span style={{ fontWeight: 600, color: vm.color }}>{vm.label}</span>
                    <span style={{
                      fontSize: 10, fontWeight: 700, color: rm.color,
                      background: `${rm.color}14`, border: `1px solid ${rm.color}55`,
                      padding: '1px 8px', borderRadius: 4, letterSpacing: 0.5,
                    }}>
                      RISK · {rm.label}
                    </span>
                  </div>
                  <div style={{ color: '#0F172A', marginBottom: 4 }}>{c.claim}</div>
                  {c.unsupported_span && (
                    <div style={{
                      background: '#FEF2F2', border: '1px solid #FECACA',
                      color: '#991B1B', padding: '6px 10px', borderRadius: 6,
                      fontSize: 11, marginTop: 6,
                    }}>
                      ⚠️ 不被支持的片段：「{c.unsupported_span}」
                    </div>
                  )}
                  {c.rationale && (
                    <div style={{ color: '#64748B', fontSize: 11, marginTop: 4 }}>
                      💬 {c.rationale}
                    </div>
                  )}
                  {Array.isArray(c.matched_source_idx) && c.matched_source_idx.length > 0 && (
                    <div style={{ color: '#64748B', fontSize: 11, marginTop: 2 }}>
                      📎 命中证据 #{c.matched_source_idx.join(', #')}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* ABSTAIN 特别警示 */}
      {action === 'ABSTAIN' && (
        <div style={{
          marginTop: 12, padding: '10px 12px',
          background: '#FEF2F2', border: '1px dashed #FCA5A5',
          borderRadius: 8, color: '#991B1B', fontSize: 12, lineHeight: 1.6,
        }}>
          📜 <b>主动弃答机制</b>：本次提问触发了医疗安全红线，
          系统宁可不答也不愿冒险输出可能误导的医疗建议。
          请咨询专业医生或补充更多上下文后重新提问。
        </div>
      )}
    </div>
  );
};

export default HallucinationGuardCard;
