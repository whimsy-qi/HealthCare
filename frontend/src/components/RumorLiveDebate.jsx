import React, { useMemo, useState } from 'react';
import { Collapse, Typography, Space, Spin, Tooltip } from 'antd';
import {
  SafetyCertificateOutlined,
  ExperimentOutlined,
  ThunderboltOutlined,
  CheckCircleFilled,
  LoadingOutlined,
  ShareAltOutlined,
  DatabaseOutlined,
  GlobalOutlined,
  CrownOutlined,
  BranchesOutlined,
  TagOutlined,
  AlertOutlined,
} from '@ant-design/icons';

const { Text } = Typography;
const { Panel } = Collapse;

/**
 * Rumor 辩论实况（D9 CTAEW）
 * ==========================
 * 把 rumor_step SSE 事件流渲染成可视化辩论时间线。
 *
 * 配色：TrustMed 黄绿色调性
 *   DEEP_GREEN #0F766E  主行动 / 核心状态
 *   LEAF_GREEN #16A34A  支持证据 / 通过状态
 *   MOSS_GREEN #4D7C0F  风险、路由和裁决强调
 *
 * 顶栏亮点（区别于 MADDx）：
 *   - claim_type 徽章 + 权重三胶囊 (KG/RAG/Web)
 *   - belief 温度计（-1..+1）+ dissent / confidence 环
 *   - verdict 收尾（属实/谣言/误导/尚无定论）
 */

const DEEP_GREEN = '#0F766E';
const LEAF_GREEN = '#16A34A';
const MOSS_GREEN = '#4D7C0F';
const LIGHT_LIME = '#ECFCCB';
const SOFT_GREEN = '#F0FDF4';
const WARM_WHITE = '#FFFBEB';
const GREEN_BORDER = '#D9E9C6';
const TEAL = DEEP_GREEN;
const SLATE = '#64748B';
const INDIGO = MOSS_GREEN;
const BORDER = GREEN_BORDER;
const TEXT_MAIN = '#111827';
const TEXT_SUB = '#64748B';
const TEXT_MUTED = '#94A3B8';

const VERDICT_META = {
  属实:     { color: LEAF_GREEN, bg: SOFT_GREEN, label: '属实' },
  谣言:     { color: MOSS_GREEN, bg: WARM_WHITE, label: '谣言' },
  误导:     { color: MOSS_GREEN, bg: LIGHT_LIME, label: '误导' },
  尚无定论: { color: TEXT_SUB, bg: '#F8FAF5', label: '尚无定论' },
};

// claim_type → 中文简标 + icon 色
const CLAIM_TYPE_LABEL = {
  CAUSAL:        '因果型',
  COMPOSITIONAL: '成分型',
  EFFICACY:      '疗效型',
  DOSAGE:        '剂量型',
  INTERACTION:   '相互作用',
  POPULATION:    '人群禁忌',
  NOVEL_TREND:   '新兴偏方',
  FOLKLORE:      '民俗谚语',
  UNKNOWN:       '未知类型',
};

const SOURCE_META = {
  kg:  { label: 'KG',  color: DEEP_GREEN, icon: <ShareAltOutlined style={{ fontSize: 10 }} /> },
  rag: { label: '指南', color: LEAF_GREEN, icon: <DatabaseOutlined style={{ fontSize: 10 }} /> },
  web: { label: 'Web', color: MOSS_GREEN, icon: <GlobalOutlined style={{ fontSize: 10 }} /> },
};

const TERMINATION_LABEL = {
  NO_VALID_OBJECTIONS: '质疑方无有效反驳',
  NO_NEW_EVIDENCE:     '证据空间耗尽',
  STRONG_CONSENSUS:    '强共识收敛',
  MAX_ROUNDS_REACHED:  '达到最大轮次',
};

const RISK_META = {
  LOW:    { color: LEAF_GREEN, bg: SOFT_GREEN, label: '低风险' },
  MEDIUM: { color: MOSS_GREEN, bg: LIGHT_LIME, label: '中风险' },
  HIGH:   { color: MOSS_GREEN, bg: WARM_WHITE, label: '高风险' },
};

const ROUTE_META = {
  FAST_PATH:  { color: DEEP_GREEN, label: '快速核查', hint: '低风险命题，单 LLM 直答（~2s）' },
  FULL_CTAEW: { color: MOSS_GREEN, label: '深度核查', hint: '高风险命题，完整 CTAEW 加权辩论（~80s）' },
};

const PHASE_META = {
  start:            { title: '谣言验证启动',           color: SLATE,  icon: <ShareAltOutlined /> },
  classify_start:   { title: '识别谣言类型中…',         color: INDIGO, icon: <TagOutlined /> },
  classify_done:    { title: '谣言类型已锁定',           color: INDIGO, icon: <TagOutlined /> },
  risk_routed:      { title: '风险评估完成 · 路由分发',  color: MOSS_GREEN, icon: <AlertOutlined /> },
  weights_resolved: { title: '权重策略已解析',           color: INDIGO, icon: <BranchesOutlined /> },
  advocate_start:   { title: '辩护方 · 开始取证',        color: TEAL,   icon: <ExperimentOutlined /> },
  advocate_done:    { title: '辩护方 · 证据就绪',        color: TEAL,   icon: <ExperimentOutlined /> },
  skeptic_start:    { title: '质疑方 · 开始反驳',        color: SLATE,  icon: <SafetyCertificateOutlined /> },
  skeptic_done:     { title: '质疑方 · 反驳完成',        color: SLATE,  icon: <SafetyCertificateOutlined /> },
  converged:        { title: '辩论收敛',                 color: INDIGO, icon: <CheckCircleFilled /> },
  judge_start:      { title: '终审法官 · 加权裁决中',    color: INDIGO, icon: <CrownOutlined /> },
  done:             { title: '终审完成',                 color: INDIGO, icon: <CrownOutlined /> },
};

// ---------- 顶栏：claim_type 徽章 + 权重胶囊 ----------

const ClaimTypeBadge = ({ claimType, confidence, secondary }) => {
  if (!claimType) return null;
  const label = CLAIM_TYPE_LABEL[claimType] || claimType;
  return (
    <Tooltip title={
      <div style={{ fontSize: 11 }}>
        <div>主类型：<b>{claimType}</b>（置信度 {(confidence * 100).toFixed(0)}%）</div>
        {secondary && <div>次类型：{secondary}</div>}
      </div>
    }>
      <span style={{
        display: 'inline-flex', alignItems: 'center', gap: 4,
        fontSize: 11, padding: '2px 8px', borderRadius: 10,
        border: `1px solid ${INDIGO}`, color: INDIGO,
        fontFamily: 'ui-monospace, Menlo, monospace',
        background: SOFT_GREEN,
      }}>
        <TagOutlined style={{ fontSize: 10 }} />
        {label}
        {typeof confidence === 'number' && (
          <span style={{ color: TEXT_MUTED, marginLeft: 2 }}>
            {(confidence * 100).toFixed(0)}%
          </span>
        )}
      </span>
    </Tooltip>
  );
};

const RiskBadge = ({ baseRisk, finalRisk, upgradeReasons }) => {
  if (!finalRisk) return null;
  const m = RISK_META[finalRisk] || RISK_META.HIGH;
  const upgraded = baseRisk && baseRisk !== finalRisk;
  return (
    <Tooltip title={
      <div style={{ fontSize: 11, lineHeight: 1.6 }}>
        <div>基础风险：<b>{baseRisk}</b></div>
        <div>最终风险：<b>{finalRisk}</b></div>
        {upgraded && upgradeReasons?.length > 0 && (
          <>
            <div style={{ marginTop: 4, color: MOSS_GREEN }}>风险升级原因：</div>
            {upgradeReasons.map((r, i) => (
              <div key={i} style={{ paddingLeft: 8 }}>· {r}</div>
            ))}
          </>
        )}
      </div>
    }>
      <span style={{
        display: 'inline-flex', alignItems: 'center', gap: 4,
        fontSize: 11, padding: '2px 8px', borderRadius: 10,
        border: `1px solid ${m.color}`, color: m.color, background: m.bg,
        fontWeight: 600,
      }}>
        <AlertOutlined style={{ fontSize: 10 }} />
        {m.label}
        {upgraded && <span style={{ fontSize: 9, opacity: 0.7 }}>↑</span>}
      </span>
    </Tooltip>
  );
};

const RouteBadge = ({ route }) => {
  if (!route) return null;
  const m = ROUTE_META[route];
  if (!m) return null;
  return (
    <Tooltip title={m.hint}>
      <span style={{
        display: 'inline-flex', alignItems: 'center',
        fontSize: 10.5, padding: '2px 8px', borderRadius: 10,
        border: `1px solid ${m.color}`, color: m.color,
        background: '#fff', fontWeight: 600,
        fontFamily: 'ui-monospace, Menlo, monospace',
      }}>
        {m.label}
      </span>
    </Tooltip>
  );
};

const WeightPills = ({ weights, budgets }) => {
  if (!weights) return null;
  const sources = ['kg', 'rag', 'web'];
  return (
    <div style={{ display: 'inline-flex', gap: 4, alignItems: 'center' }}>
      {sources.map(src => {
        const w = weights[src] ?? 0;
        const b = budgets?.[src];
        const meta = SOURCE_META[src];
        return (
          <Tooltip
            key={src}
            title={`${meta.label} 权重 ${(w * 100).toFixed(0)}%${b !== undefined ? `，预算 top_k=${b}` : ''}`}
          >
            <span style={{
              display: 'inline-flex', alignItems: 'center', gap: 3,
              fontSize: 10.5, padding: '1px 7px', borderRadius: 9,
              border: `1px solid ${BORDER}`, color: meta.color,
              background: '#fff',
              fontFamily: 'ui-monospace, Menlo, monospace',
              fontVariantNumeric: 'tabular-nums',
            }}>
              {meta.icon}
              {meta.label}·{(w * 100).toFixed(0)}
            </span>
          </Tooltip>
        );
      })}
    </div>
  );
};

// ---------- belief 温度计（-1..+1）----------

const BeliefBar = ({ belief }) => {
  if (typeof belief !== 'number') return null;
  const clamped = Math.max(-1, Math.min(1, belief));
  const pct = (clamped + 1) * 50;   // -1 → 0% / 0 → 50% / +1 → 100%
  const pointer = Math.min(99, Math.max(1, pct));
  const color = clamped >= 0.35 ? LEAF_GREEN : clamped <= -0.35 ? MOSS_GREEN : DEEP_GREEN;
  return (
    <Tooltip title={`belief = ${belief >= 0 ? '+' : ''}${belief.toFixed(3)}（+1=完全支持，-1=完全反驳）`}>
      <div style={{
        display: 'inline-flex', alignItems: 'center', gap: 6,
        fontSize: 10.5, color: TEXT_SUB, fontVariantNumeric: 'tabular-nums',
      }}>
        <span>信念</span>
        <div style={{
          width: 80, height: 6, borderRadius: 3, position: 'relative',
          background: 'linear-gradient(to right, rgba(77,124,15,0.18), #E7EDD8 50%, rgba(22,163,74,0.22))',
        }}>
          {/* 中线 */}
          <div style={{
            position: 'absolute', left: '50%', top: -2, bottom: -2,
            width: 1, background: TEXT_MUTED, opacity: 0.5,
          }} />
          {/* 指针 */}
          <div style={{
            position: 'absolute', left: `${pointer}%`, top: -2, bottom: -2,
            width: 3, background: color, borderRadius: 2,
            transform: 'translateX(-50%)',
            transition: 'left 0.4s ease-out',
          }} />
        </div>
        <span style={{ color, fontWeight: 600, minWidth: 38, textAlign: 'right' }}>
          {belief >= 0 ? '+' : ''}{belief.toFixed(2)}
        </span>
      </div>
    </Tooltip>
  );
};

// ---------- confidence/dissent 小环 ----------

const MiniStat = ({ label, value, hint }) => {
  if (typeof value !== 'number') return null;
  return (
    <Tooltip title={hint || ''}>
      <span style={{
        fontSize: 10.5, color: TEXT_SUB, fontVariantNumeric: 'tabular-nums',
        padding: '1px 6px', border: `1px solid ${BORDER}`, borderRadius: 4,
      }}>
        {label}·{(value * 100).toFixed(0)}%
      </span>
    </Tooltip>
  );
};

// ---------- 单个事件行 ----------

const EventRow = ({ event, isLast, isLive }) => {
  const meta = PHASE_META[event.phase] || { title: event.phase, color: SLATE, icon: <ShareAltOutlined /> };
  const showSpin = isLast && isLive && event.phase.endsWith('_start');

  return (
    <div style={{
      display: 'flex', gap: 10, padding: '10px 0',
      borderBottom: isLast ? 'none' : `1px solid ${BORDER}`,
      animation: 'rumorFadeSlideIn 0.3s ease-out',
    }}>
      <div style={{
        width: 2, background: meta.color, borderRadius: 1,
        flexShrink: 0, alignSelf: 'stretch',
      }} />

      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <span style={{ color: meta.color, fontSize: 13, display: 'flex', alignItems: 'center' }}>
            {showSpin ? <LoadingOutlined spin /> : meta.icon}
          </span>
          <Text strong style={{ fontSize: 12.5, color: meta.color, letterSpacing: 0.2 }}>
            {meta.title}
          </Text>
          {typeof event.round === 'number' && event.round > 0 && (
            <span style={{
              fontSize: 10, color: TEXT_MUTED, padding: '0 6px',
              border: `1px solid ${BORDER}`, borderRadius: 4,
              fontFamily: 'ui-monospace, Menlo, monospace',
            }}>
              R{event.round}
            </span>
          )}

          {/* classify_done → 显示类型徽章 */}
          {event.phase === 'classify_done' && (
            <ClaimTypeBadge
              claimType={event.claim_type}
              confidence={event.confidence}
              secondary={event.secondary}
            />
          )}

          {/* risk_routed → 风险等级 + 路由徽章 */}
          {event.phase === 'risk_routed' && (
            <>
              <RiskBadge
                baseRisk={event.base_risk}
                finalRisk={event.final_risk}
                upgradeReasons={event.upgrade_reasons}
              />
              <RouteBadge route={event.route} />
            </>
          )}

          {/* weights_resolved → 权重胶囊 */}
          {event.phase === 'weights_resolved' && (
            <WeightPills weights={event.weights} budgets={event.budgets} />
          )}

          {/* advocate_done → 证据数 */}
          {event.phase === 'advocate_done' && typeof event.support_count === 'number' && (
            <Text style={{ fontSize: 10.5, color: TEXT_MUTED, fontVariantNumeric: 'tabular-nums' }}>
              支持证据 {event.support_count} 条
            </Text>
          )}

          {/* skeptic_done → 反驳 + objection */}
          {event.phase === 'skeptic_done' && (
            <Text style={{ fontSize: 10.5, color: TEXT_MUTED, fontVariantNumeric: 'tabular-nums' }}>
              反驳 {event.refute_count ?? 0} / objection {event.objection_count ?? 0}
            </Text>
          )}

          {/* converged → 收敛理由 + belief */}
          {event.phase === 'converged' && (
            <>
              <span style={{
                fontSize: 10, color: INDIGO,
                padding: '0 6px', border: `1px solid ${BORDER}`, borderRadius: 4,
                fontFamily: 'ui-monospace, Menlo, monospace',
              }}>
                {TERMINATION_LABEL[event.reason] || event.reason}
              </span>
              {typeof event.belief === 'number' && <BeliefBar belief={event.belief} />}
              <MiniStat label="dissent" value={event.dissent} hint="分歧度：双方命中均衡时偏高" />
            </>
          )}

          {/* done → 最终 verdict + belief + confidence */}
          {event.phase === 'done' && (
            <>
              <VerdictBadge verdict={event.verdict} />
              <BeliefBar belief={event.belief} />
              <MiniStat label="可信度" value={event.confidence} hint="|belief| · (1-dissent) · evidence_sufficiency" />
              {event.termination_reason && (
                <span style={{
                  fontSize: 10, color: TEXT_MUTED,
                  padding: '0 6px', border: `1px solid ${BORDER}`, borderRadius: 4,
                  fontFamily: 'ui-monospace, Menlo, monospace',
                }}>
                  {TERMINATION_LABEL[event.termination_reason] || event.termination_reason}
                </span>
              )}
            </>
          )}
        </div>

        {event.message && (
          <Text style={{ fontSize: 11.5, color: TEXT_SUB, lineHeight: 1.6, display: 'block', marginTop: 2 }}>
            {event.message}
          </Text>
        )}

        {/* advocate_done 的 stance 摘要 */}
        {event.phase === 'advocate_done' && event.stance && (
          <Text style={{ fontSize: 11, color: TEXT_SUB, lineHeight: 1.55, display: 'block', marginTop: 4, paddingLeft: 2 }}>
            <span style={{ color: TEAL, fontWeight: 600 }}>立场：</span>{event.stance}
          </Text>
        )}
      </div>
    </div>
  );
};

const VerdictBadge = ({ verdict }) => {
  const m = VERDICT_META[verdict];
  if (!m) return null;
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center',
      fontSize: 11, padding: '2px 10px', borderRadius: 10,
      border: `1px solid ${m.color}`, color: m.color,
      background: m.bg, fontWeight: 600,
    }}>
      {m.label}
    </span>
  );
};

// ---------- 主组件 ----------

const RumorLiveDebate = ({ events, isLive }) => {
  const [activeKey, setActiveKey] = useState(['rumor']);

  // 合并渲染：起始 start 事件和 classify_start 可以合并，减少噪声
  const visibleEvents = useMemo(() => {
    if (!Array.isArray(events)) return [];
    return events.filter(() => {
      // classify_start 后紧跟 classify_done 时，前者可略；保留以显示"识别中"的 spin
      return true;
    });
  }, [events]);

  // 顶栏汇总：最新 claim_type / weights / verdict
  const summary = useMemo(() => {
    let claimType = null, confidence = null, secondary = null;
    let weights = null, budgets = null;
    let verdict = null, finalBelief = null, finalConf = null;
    let rounds = 0;
    let baseRisk = null, finalRisk = null, route = null, upgradeReasons = null;
    for (const e of events || []) {
      if (e.phase === 'classify_done') {
        claimType = e.claim_type; confidence = e.confidence; secondary = e.secondary;
      } else if (e.phase === 'risk_routed') {
        baseRisk = e.base_risk; finalRisk = e.final_risk; route = e.route;
        upgradeReasons = e.upgrade_reasons;
      } else if (e.phase === 'weights_resolved') {
        weights = e.weights; budgets = e.budgets;
      } else if (e.phase === 'advocate_start' || e.phase === 'advocate_done') {
        if (typeof e.round === 'number') rounds = Math.max(rounds, e.round);
      } else if (e.phase === 'done') {
        verdict = e.verdict; finalBelief = e.belief; finalConf = e.confidence;
      }
    }
    return { claimType, confidence, secondary, weights, budgets, verdict, finalBelief, finalConf, rounds,
             baseRisk, finalRisk, route, upgradeReasons };
  }, [events]);

  if (!visibleEvents.length) return null;

  return (
    <>
      <style>
        {`
          @keyframes rumorFadeSlideIn {
            from { opacity: 0; transform: translateY(-3px); }
            to { opacity: 1; transform: translateY(0); }
          }
          .rumor-live-wrap .ant-collapse-ghost > .ant-collapse-item > .ant-collapse-header {
            padding: 10px 12px !important;
          }
          .rumor-live-wrap .ant-collapse-ghost > .ant-collapse-item > .ant-collapse-content > .ant-collapse-content-box {
            padding: 0 12px 10px 12px !important;
          }
        `}
      </style>
      <div className="rumor-live-wrap" style={{
        width: '100%',
        background: '#fff',
        border: `1px solid ${BORDER}`,
        borderRadius: 10,
        marginBottom: 10,
        boxSizing: 'border-box',
        overflow: 'hidden',
      }}>
        <Collapse activeKey={activeKey} onChange={setActiveKey} ghost>
          <Panel
            key="rumor"
            header={
              <Space size={8} wrap>
                <SafetyCertificateOutlined style={{ color: INDIGO, fontSize: 13 }} />
                <Text strong style={{ fontSize: 13, color: TEXT_MAIN }}>
                  谣言加权辩论实录
                </Text>
                <span style={{
                  fontSize: 10.5, color: TEXT_MUTED,
                  fontVariantNumeric: 'tabular-nums',
                }}>
                  {visibleEvents.length} 步{summary.rounds ? ` · ${summary.rounds} 轮辩论` : ''}{isLive ? ' · 进行中' : ''}
                </span>
                {summary.claimType && (
                  <ClaimTypeBadge
                    claimType={summary.claimType}
                    confidence={summary.confidence}
                    secondary={summary.secondary}
                  />
                )}
                {summary.finalRisk && (
                  <RiskBadge
                    baseRisk={summary.baseRisk}
                    finalRisk={summary.finalRisk}
                    upgradeReasons={summary.upgradeReasons}
                  />
                )}
                {summary.route && <RouteBadge route={summary.route} />}
                {summary.weights && <WeightPills weights={summary.weights} budgets={summary.budgets} />}
                {summary.verdict && <VerdictBadge verdict={summary.verdict} />}
                {typeof summary.finalBelief === 'number' && !isLive && (
                  <BeliefBar belief={summary.finalBelief} />
                )}
                {isLive && (
                  <Spin size="small" indicator={<LoadingOutlined spin style={{ color: INDIGO, fontSize: 12 }} />} />
                )}
              </Space>
            }
          >
            <div>
              {visibleEvents.map((ev, idx) => (
                <EventRow
                  key={idx}
                  event={ev}
                  isLast={idx === visibleEvents.length - 1}
                  isLive={isLive}
                />
              ))}
            </div>
          </Panel>
        </Collapse>
      </div>
    </>
  );
};

export default RumorLiveDebate;
