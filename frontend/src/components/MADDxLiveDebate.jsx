import React, { useState, useMemo } from 'react';
import { Collapse, Typography, Space, Spin, Tooltip } from 'antd';
import {
  ExperimentOutlined,
  SafetyCertificateOutlined,
  ThunderboltOutlined,
  CrownOutlined,
  CheckCircleFilled,
  LoadingOutlined,
  ShareAltOutlined,
  SearchOutlined,
  DatabaseOutlined,
  GlobalOutlined,
} from '@ant-design/icons';

const { Text } = Typography;
const { Panel } = Collapse;

/**
 * MADDx 辩论实况 —— 极简 3 色方案
 *   主色 deep green #0F766E  提出方 (Proposer / Defender)
 *   冷灰 slate      #64748B  质疑方 (Critic)
 *   苔绿 moss       #4D7C0F  裁决方 (Moderator)
 *
 * 统一规则：
 *   - 白底，不用彩色背景块
 *   - 角色靠左侧 2px 细色条 + 图标颜色 + 标题字色 识别
 *   - 取消 Badge / Tag 的彩色填充，改用 ghost 灰标
 *   - 置信度用 1px 灰底 + teal 细进度条，不再用橙/绿 Tag
 */

const TEAL = '#0F766E';
const SLATE = '#64748B';
const INDIGO = '#4D7C0F';
const LEAF_GREEN = '#16A34A';
const BORDER = '#D9E9C6';
const TEXT_MAIN = '#111827';
const TEXT_SUB = '#64748B';
const TEXT_MUTED = '#94A3B8';

const PHASE_META = {
  start:           { title: '启动会诊',               color: SLATE,  icon: <ShareAltOutlined /> },
  proposer_start:  { title: '住院医师 · 初步鉴别',    color: TEAL,   icon: <ExperimentOutlined /> },
  proposer_done:   { title: '住院医师 · 候选诊断',    color: TEAL,   icon: <ExperimentOutlined /> },
  critic_start:    { title: '主治医师 · 审查质疑',    color: SLATE,  icon: <SafetyCertificateOutlined /> },
  critic_done:     { title: '主治医师 · 质疑意见',    color: SLATE,  icon: <SafetyCertificateOutlined /> },
  defender_start:  { title: '住院医师 · 据理反驳',    color: TEAL,   icon: <ThunderboltOutlined /> },
  defender_done:   { title: '住院医师 · 修正后候选',  color: TEAL,   icon: <ThunderboltOutlined /> },
  converged:       { title: '辩论收敛',               color: INDIGO, icon: <CheckCircleFilled /> },
  moderator_start: { title: '科主任 · 综合裁决中',    color: INDIGO, icon: <CrownOutlined /> },
  moderator_done:  { title: '科主任 · 已出会诊意见',  color: INDIGO, icon: <CrownOutlined /> },
  // D8 工具事件（不独立成行，被合并渲染到相应 agent 步骤里）
  tool_call:       { title: '工具调用',               color: SLATE,  icon: <SearchOutlined /> },
  tool_result:     { title: '工具返回',               color: SLATE,  icon: <SearchOutlined /> },
};

// D8: 工具名 → 图标 + 显示名映射
const TOOL_META = {
  kg_query:   { label: 'KG', icon: <ShareAltOutlined style={{ fontSize: 10 }} />, color: TEAL },
  rag_search: { label: 'RAG', icon: <DatabaseOutlined style={{ fontSize: 10 }} />, color: LEAF_GREEN },
  web_search: { label: 'Web', icon: <GlobalOutlined style={{ fontSize: 10 }} />, color: INDIGO },
};

// ---------- 候选诊断：单行 + 细进度条 ----------

const CandidateRows = ({ candidates }) => {
  if (!Array.isArray(candidates) || !candidates.length) return null;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 8 }}>
      {candidates.map((c, i) => {
        const conf = typeof c.confidence === 'number' ? c.confidence : 0;
        return (
          <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{
              width: 18, fontSize: 11, color: TEXT_MUTED, fontVariantNumeric: 'tabular-nums',
              flexShrink: 0,
            }}>
              {i + 1}.
            </span>
            <Text style={{ fontSize: 12, color: TEXT_MAIN, minWidth: 90, flexShrink: 0 }}>
              {c.disease || c.name || '—'}
            </Text>
            <div style={{
              flex: 1, height: 4, background: '#F1F5F9',
              borderRadius: 2, overflow: 'hidden', minWidth: 40,
            }}>
              <div style={{
                width: `${Math.max(0, Math.min(1, conf)) * 100}%`,
                height: '100%', background: TEAL,
                transition: 'width 0.4s ease-out',
              }} />
            </div>
            <span style={{
              fontSize: 11, color: TEXT_SUB, fontVariantNumeric: 'tabular-nums',
              minWidth: 32, textAlign: 'right', flexShrink: 0,
            }}>
              {(conf * 100).toFixed(0)}%
            </span>
          </div>
        );
      })}
    </div>
  );
};

// ---------- D8 工具调用徽章（合并在 agent 步骤行内展示） ----------

const ToolBadges = ({ toolEvents }) => {
  if (!Array.isArray(toolEvents) || toolEvents.length === 0) return null;

  // 按 call_ref 聚合：配对 tool_call 与其对应的 tool_result
  const calls = new Map();
  toolEvents.forEach(e => {
    const ref = e.call_ref;
    if (ref === undefined || ref === null) return;
    if (!calls.has(ref)) calls.set(ref, { ref, tool: e.tool, args: null, hit_count: null, cached: false, preview: [] });
    const slot = calls.get(ref);
    if (e.phase === 'tool_call') {
      slot.args = e.args;
    } else if (e.phase === 'tool_result') {
      slot.hit_count = e.hit_count ?? 0;
      slot.cached = !!e.cached;
      slot.preview = e.preview || [];
    }
  });

  const sortedCalls = Array.from(calls.values());
  if (!sortedCalls.length) return null;

  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
      {sortedCalls.map((c, idx) => {
        const meta = TOOL_META[c.tool] || { label: c.tool || '?', color: SLATE, icon: <SearchOutlined style={{ fontSize: 10 }} /> };
        const pending = c.hit_count === null;
        const miss = !pending && c.hit_count === 0;
        const tipBody = (
          <div style={{ maxWidth: 260, fontSize: 11 }}>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>
              {meta.label} · {pending ? '查询中…' : `${c.hit_count} 命中${c.cached ? '（缓存）' : ''}`}
            </div>
            {c.args && (
              <div style={{ color: TEXT_SUB, marginBottom: 4, fontFamily: 'ui-monospace, Menlo, monospace', wordBreak: 'break-all' }}>
                {JSON.stringify(c.args)}
              </div>
            )}
            {c.preview.length > 0 && (
              <ul style={{ margin: 0, paddingLeft: 14, color: TEXT_SUB }}>
                {c.preview.map((p, i) => (
                  <li key={i} style={{ lineHeight: 1.5 }}>{p}</li>
                ))}
              </ul>
            )}
          </div>
        );
        return (
          <Tooltip key={idx} title={tipBody} placement="top" overlayStyle={{ maxWidth: 280 }}>
            <span style={{
              display: 'inline-flex', alignItems: 'center', gap: 4,
              fontSize: 10.5, padding: '2px 7px', borderRadius: 10,
              border: `1px solid ${miss ? '#D9E9C6' : 'rgba(217,233,198,0.9)'}`,
              background: miss ? '#FFFBEB' : '#fff',
              color: miss ? INDIGO : meta.color,
              fontFamily: 'ui-monospace, Menlo, monospace',
              cursor: 'help',
            }}>
              {meta.icon}
              <span>{meta.label}</span>
              {pending ? (
                <LoadingOutlined spin style={{ fontSize: 9, marginLeft: 2 }} />
              ) : (
                <span style={{ color: miss ? INDIGO : TEXT_MUTED, marginLeft: 2 }}>
                  {c.hit_count}{c.cached ? '·缓存' : ''}
                </span>
              )}
            </span>
          </Tooltip>
        );
      })}
    </div>
  );
};

// ---------- 质疑意见：引用式纵线 + 幽灵灰标 ----------

const ObjectionRows = ({ objections }) => {
  if (!Array.isArray(objections)) return null;
  if (!objections.length) {
    return (
      <Text style={{ fontSize: 12, color: TEXT_SUB, marginTop: 6, display: 'block' }}>
        无有效反驳，达成共识
      </Text>
    );
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 8 }}>
      {objections.map((o, i) => (
        <div key={i} style={{
          borderLeft: `2px solid ${BORDER}`,
          paddingLeft: 10,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 2 }}>
            <Text style={{ fontSize: 12, fontWeight: 600, color: TEXT_MAIN }}>
              {o.target_disease}
            </Text>
            {o.type && (
              <span style={{
                fontSize: 10, color: TEXT_MUTED, padding: '1px 6px',
                border: `1px solid ${BORDER}`, borderRadius: 4,
                fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
              }}>
                {o.type}
              </span>
            )}
          </div>
          <Text style={{ fontSize: 12, color: TEXT_SUB, lineHeight: 1.6 }}>
            {o.reason || o.argument}
          </Text>
        </div>
      ))}
    </div>
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
      animation: 'fadeSlideIn 0.3s ease-out',
    }}>
      {/* 左侧 2px 细色条 */}
      <div style={{
        width: 2, background: meta.color, borderRadius: 1,
        flexShrink: 0, alignSelf: 'stretch',
      }} />

      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
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
              fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
            }}>
              R{event.round}
            </span>
          )}
          {event.evidence_summary && (
            <Text style={{ fontSize: 10.5, color: TEXT_MUTED, fontVariantNumeric: 'tabular-nums' }}>
              KG·{event.evidence_summary.kg_count} / RAG·{event.evidence_summary.rag_count}
            </Text>
          )}
        </div>

        {event.message && (
          <Text style={{ fontSize: 11.5, color: TEXT_SUB, lineHeight: 1.6, display: 'block', marginTop: 2 }}>
            {event.message}
          </Text>
        )}
        {event.candidates && <CandidateRows candidates={event.candidates} />}
        {event.objections !== undefined && <ObjectionRows objections={event.objections} />}
        {event.toolEvents && event.toolEvents.length > 0 && <ToolBadges toolEvents={event.toolEvents} />}
        {event.phase === 'converged' && event.reason && (
          <span style={{
            fontSize: 10, color: INDIGO, marginTop: 4, display: 'inline-block',
            padding: '1px 6px', border: `1px solid ${BORDER}`, borderRadius: 4,
            fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
          }}>
            {event.reason}
          </span>
        )}
      </div>
    </div>
  );
};

// ---------- 主组件 ----------

const MADDxLiveDebate = ({ events, isLive }) => {
  const [activeKey, setActiveKey] = useState(['debate']);

  const visibleEvents = useMemo(() => {
    if (!Array.isArray(events)) return [];
    // D8: 把 tool_call / tool_result 附加到所属 agent 的 *_start 步骤上，
    // 不独立占行——避免辩论时间线被工具调用噪声淹没。
    // 配对规则：tool_event.agent + round 对应 {agent}_start 的最近一条。
    const agentStartIndexByKey = new Map();  // `${agent}|${round}` → index
    const out = [];
    events.forEach(e => {
      if (e.phase === 'tool_call' || e.phase === 'tool_result') {
        const key = `${e.agent}|${e.round ?? 0}`;
        const targetIdx = agentStartIndexByKey.get(key);
        if (targetIdx !== undefined) {
          const target = out[targetIdx];
          target.toolEvents = target.toolEvents || [];
          target.toolEvents.push(e);
        }
        return;  // 工具事件不独立入列
      }
      if (e.phase === 'moderator_done') return;   // 沿用旧规则

      // 规范化：只有 _start 事件作为"工具调用归属宿主"
      if (e.phase.endsWith('_start')) {
        const agentName = e.phase.replace('_start', '');
        const key = `${agentName}|${e.round ?? 0}`;
        agentStartIndexByKey.set(key, out.length);
      }
      out.push({ ...e });
    });
    return out;
  }, [events]);

  // D8: 汇总工具调用统计（用于顶栏显示）
  const toolStats = useMemo(() => {
    if (!Array.isArray(events)) return null;
    let calls = 0, hits = 0;
    events.forEach(e => {
      if (e.phase === 'tool_result') {
        calls += 1;
        hits += (e.hit_count || 0);
      }
    });
    return calls > 0 ? { calls, hits } : null;
  }, [events]);

  if (!visibleEvents.length) return null;

  return (
    <>
      <style>
        {`
          @keyframes fadeSlideIn {
            from { opacity: 0; transform: translateY(-3px); }
            to { opacity: 1; transform: translateY(0); }
          }
          .maddx-live-wrap .ant-collapse-ghost > .ant-collapse-item > .ant-collapse-header {
            padding: 10px 12px !important;
          }
          .maddx-live-wrap .ant-collapse-ghost > .ant-collapse-item > .ant-collapse-content > .ant-collapse-content-box {
            padding: 0 12px 10px 12px !important;
          }
        `}
      </style>
      <div className="maddx-live-wrap" style={{
        width: '100%',
        background: '#fff',
        border: `1px solid ${BORDER}`,
        borderRadius: 10,
        marginBottom: 10,
        boxSizing: 'border-box',
        overflow: 'hidden',
      }}>
        <Collapse
          activeKey={activeKey}
          onChange={setActiveKey}
          ghost
        >
          <Panel
            key="debate"
            header={
              <Space size={8}>
                <ShareAltOutlined style={{ color: INDIGO, fontSize: 13 }} />
                <Text strong style={{ fontSize: 13, color: TEXT_MAIN }}>
                  多智能体会诊实录
                </Text>
                <span style={{
                  fontSize: 10.5, color: TEXT_MUTED,
                  fontVariantNumeric: 'tabular-nums',
                }}>
                  {visibleEvents.length} 步{toolStats ? ` · 🔎${toolStats.calls} / ✓${toolStats.hits}` : ''}{isLive ? ' · 进行中' : ''}
                </span>
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

export default MADDxLiveDebate;
