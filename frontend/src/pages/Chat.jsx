import React, { useState, useEffect, useRef, useReducer } from 'react';
import { Button, Input, Avatar, Space, Typography, Collapse, Badge, Tooltip, Spin, Upload, message, Tag, Modal, Timeline, Skeleton } from 'antd';
import { 
  PlusOutlined, MenuUnfoldOutlined, 
  MenuFoldOutlined, PictureOutlined, BulbOutlined, SafetyCertificateOutlined, 
  MedicineBoxOutlined, SendOutlined, CloseCircleFilled, 
  UserOutlined, RightOutlined, GlobalOutlined, DatabaseOutlined, BookOutlined, EyeOutlined,
  ApiOutlined, MessageOutlined, ShareAltOutlined,
  AudioOutlined, AppstoreOutlined
} from '@ant-design/icons';
import ReactMarkdown from 'react-markdown';
import { useNavigate } from 'react-router-dom';
import remarkGfm from 'remark-gfm';
import MADDxDebateView from '../components/MADDxDebateView';
import MADDxLiveDebate from '../components/MADDxLiveDebate';
import RumorLiveDebate from '../components/RumorLiveDebate';
import HallucinationGuardCard from '../components/HallucinationGuardCard';
import EvidenceChainView from '../components/EvidenceChainView';
import BlackboardDAGView from '../components/BlackboardDAGView';
import { apiUrl, staticUrl } from '../config/api';

const { Title, Text } = Typography;
const { Panel } = Collapse;
const { TextArea } = Input;

const PALETTE = {
  teal: '#14B8A6',
  tealDeep: '#0F766E',
  tealSoft: '#5EEAD4',
  tealGhost: 'rgba(20, 184, 166, 0.10)',
  yellowGreen: '#afeebf',
  cream: '#f0eac1',
  mint: '#e0f5ee',
  textInk: '#0F172A',
  textSlate: '#334155',
  textMute: '#64748B',
  hairline: 'rgba(15, 118, 110, 0.10)',
  glass: 'rgba(255, 255, 255, 0.72)',
  glassThick: 'rgba(255, 255, 255, 0.85)',
  amber: '#D9F99D',
};

const PAGE_BACKGROUND = `
  radial-gradient(1200px 600px at 0% 0%, rgba(175, 238, 191, 0.55) 0%, transparent 60%),
  radial-gradient(1000px 500px at 100% 0%, rgba(240, 234, 193, 0.55) 0%, transparent 55%),
  radial-gradient(900px 600px at 50% 100%, rgba(224, 245, 238, 0.65) 0%, transparent 55%),
  linear-gradient(135deg, #f7fbf6 0%, #fbf7e8 50%, #effaf4 100%)
`;

const isQuestionMarkText = (value) => {
  const text = String(value || '').trim();
  return !text || /^[?\s]+$/.test(text);
};

const normalizeSessionTitle = (title) => (
  isQuestionMarkText(title) ? '新的健康咨询' : title
);

const normalizeSessionItem = (session = {}) => ({
  ...session,
  title: normalizeSessionTitle(session.title),
});

const isKgConstraintSource = (source = {}) =>
  source.type === 'kg' || source.evidence_role === 'constraint' || source.citation_allowed === false;

const isMilvusRagSource = (source = {}) => {
  if (!source || isKgConstraintSource(source)) return false;
  return Boolean(
    source.rag_trace ||
    source.knowledge_card ||
    source.role === 'evidence' ||
    source.role === 'background'
  );
};

const getLegacySourceSummary = (source = {}) =>
  firstText(source.medical_summary, source.key_takeaway, source.summary, source.snippet, source.content) ||
  '该来源暂未提供摘要。';

const glassSurface = {
  background: PALETTE.glass,
  backdropFilter: 'blur(24px) saturate(160%)',
  WebkitBackdropFilter: 'blur(24px) saturate(160%)',
  border: `1px solid ${PALETTE.hairline}`,
  boxShadow: '0 16px 40px rgba(15, 118, 110, 0.07), 0 2px 8px rgba(15, 118, 110, 0.03)',
};

const glassThickSurface = {
  ...glassSurface,
  background: PALETTE.glassThick,
};

const getMedicalSummaryText = (source = {}) =>
  source.medical_summary || source.key_takeaway || source.summary || '暂未生成摘要。';

const getSummaryStatusLabel = (status) => {
  if (status === 'summarized') return '模型摘要';
  if (status === 'rule_fallback') return '规则摘要';
  return '';
};

const firstText = (...values) =>
  values.find((value) => typeof value === 'string' && value.trim())?.trim() || '';

const getKnowledgeCard = (source = {}) => {
  const card = source.knowledge_card;
  return card && typeof card === 'object' ? card : null;
};

const getKnowledgeCardSummary = (source = {}) => {
  const card = getKnowledgeCard(source);
  return firstText(card?.answer_summary) || '该证据的知识卡暂未生成。';
};

const normalizeLocator = (locator) => {
  if (!locator) return null;
  if (typeof locator === 'string') {
    try {
      return JSON.parse(locator);
    } catch {
      return { raw: locator };
    }
  }
  return locator;
};

const getLocatorLabel = (source = {}) => {
  const locator = normalizeLocator(source.locator) || {};
  const page = source.page_no || locator.page_no || locator.page_start;
  const slide = source.slide_no || locator.slide_no;
  const sectionPath = source.section_path || locator.section_path;
  const section = Array.isArray(sectionPath) ? sectionPath.join(' / ') : sectionPath;
  const parts = [];
  if (page) parts.push(`P${page}`);
  if (slide) parts.push(`Slide ${slide}`);
  if (section) parts.push(section);
  return parts.join(' · ');
};

const buildEvidenceModalData = (source = {}, fallbackTitle = 'Evidence') => {
  const card = getKnowledgeCard(source);
  return {
    title: card?.card_title || source.title || fallbackTitle,
    content: getKnowledgeCardSummary(source),
    keyPoints: Array.isArray(card?.key_points) ? card.key_points.filter(Boolean).slice(0, 3) : [],
    whyRelevant: firstText(card?.why_relevant),
    evidenceLimit: firstText(card?.limits),
    dept: source.source_tier || source.department || source.source_format || 'Milvus',
    locator: source.locator,
    locatorLabel: getLocatorLabel(source),
    sourceFormat: source.source_format,
    sectionType: source.section_type,
    pageNo: source.page_no,
    slideNo: source.slide_no,
    ocrUsed: source.ocr_used,
    parseWarnings: source.parse_warnings,
    status: source.llm_summary_status,
  };
};

const getTraceAuditLog = (trace = {}) => {
  if (Array.isArray(trace.audit_log)) return trace.audit_log;
  if (Array.isArray(trace.agent_audit_log)) return trace.agent_audit_log;
  return [];
};

const toArray = (value) => {
  if (!value) return [];
  if (Array.isArray(value)) return value;
  if (typeof value === 'string') return value.trim() ? [value] : [];
  return [value];
};

const isNonEmptyText = (value) => typeof value === 'string' && value.trim().length > 0;

const getSupplementalSources = (trace = {}) => {
  const supplementalSources = toArray(trace.supplemental?.sources);
  const legacySources = toArray(trace.sources).filter(source => !isKgConstraintSource(source) && !isMilvusRagSource(source));
  return supplementalSources.length > 0 ? supplementalSources : legacySources;
};

const normalizeTraceForPanel = (trace = {}, route = '') => {
  const safeTrace = trace && typeof trace === 'object' ? trace : {};
  const rag = safeTrace.rag && typeof safeTrace.rag === 'object' ? safeTrace.rag : {};
  const ragItems = toArray(rag.items);
  const evidenceItems = ragItems.filter(item => item?.role !== 'background');
  const backgroundItems = ragItems.filter(item => item?.role === 'background');

  const kg = safeTrace.kg && typeof safeTrace.kg === 'object' ? safeTrace.kg : {};
  const kgPaths = toArray(kg.paths);

  const rumor = safeTrace.rumor && typeof safeTrace.rumor === 'object' ? safeTrace.rumor : {};
  const scoutData = toArray(rumor.scout_data).length > 0 ? toArray(rumor.scout_data) : toArray(safeTrace.scout_data);
  const medicalData = toArray(rumor.medical_data).length > 0 ? toArray(rumor.medical_data) : toArray(safeTrace.medical_data);
  const criticReasoning = firstText(rumor.critic_reasoning, safeTrace.critic_reasoning);
  const rumorEvents = toArray(rumor.rumor_events).length > 0 ? toArray(rumor.rumor_events) : toArray(safeTrace.rumor_events);
  const hasRumorPanel = route === 'RUMOR_VERIFICATION' || Boolean(safeTrace.rumor) || scoutData.length > 0 || medicalData.length > 0 || isNonEmptyText(criticReasoning) || rumorEvents.length > 0;

  const supplemental = safeTrace.supplemental && typeof safeTrace.supplemental === 'object' ? safeTrace.supplemental : {};
  const supplementalSources = getSupplementalSources(safeTrace);
  const maddxDebate = supplemental.maddx_debate || safeTrace.maddx_debate;
  const maddxEvents = toArray(supplemental.maddx_events).length > 0 ? toArray(supplemental.maddx_events) : toArray(safeTrace.maddx_events);

  const summary = safeTrace.trace_summary && typeof safeTrace.trace_summary === 'object' ? safeTrace.trace_summary : {};
  const auditLog = getTraceAuditLog(safeTrace);
  const safety = safeTrace.safety_check && typeof safeTrace.safety_check === 'object' ? safeTrace.safety_check : {};
  const safetyStatus = summary.safety_status || (safety.degraded || safety.timeout ? 'degraded' : 'passed');

  return {
    summary: {
      route: summary.route || route || 'UNKNOWN',
      collabMode: summary.collab_mode || safeTrace.collab_mode || '未记录',
      agents: toArray(summary.agents),
      milvusEvidenceCount: Number(summary.milvus_evidence_count ?? rag.evidence_count ?? evidenceItems.length ?? 0),
      kgConstraintCount: Number(summary.kg_constraint_count ?? kgPaths.length ?? 0),
      externalSourceCount: Number(summary.external_source_count ?? scoutData.length ?? 0),
      safetyStatus,
    },
    auditLog,
    scratchpad: toArray(safeTrace.internal_scratchpad),
    rag: {
      items: ragItems,
      evidenceItems,
      evidenceCount: Number(rag.evidence_count ?? evidenceItems.length ?? 0),
      backgroundCount: Number(rag.background_count ?? backgroundItems.length ?? 0),
    },
    kg: {
      paths: kgPaths,
      degraded: Boolean(kg.degraded),
    },
    rumor: {
      scoutData,
      medicalData,
      criticReasoning,
      rumorEvents,
      verdict: firstText(rumor.verdict, safeTrace.verdict),
      riskLevel: firstText(rumor.risk_level, safeTrace.risk_level),
    },
    supplemental: {
      sources: supplementalSources,
      notes: toArray(supplemental.notes),
      maddxDebate,
      maddxEvents,
    },
    hasRumorPanel,
    hasAnyTrace: Boolean(
      auditLog.length ||
      ragItems.length ||
      kgPaths.length ||
      scoutData.length ||
      medicalData.length ||
      isNonEmptyText(criticReasoning) ||
      supplementalSources.length ||
      maddxDebate
    ),
  };
};

const panelCardStyle = {
  backgroundColor: PALETTE.glassThick,
  borderRadius: '12px',
  marginBottom: '16px',
  border: `1px solid ${PALETTE.hairline}`,
};

const TRACE_TONE = {
  deep: '#0F766E',
  leaf: '#16A34A',
  moss: '#4D7C0F',
  lime: '#ECFCCB',
  soft: '#F0FDF4',
  warm: '#FFFBEB',
  border: '#D9E9C6',
  slate: '#64748B',
  ink: '#111827',
};

const traceTagStyle = (tone = 'soft', extra = {}) => {
  const styles = {
    deep: { color: TRACE_TONE.deep, background: '#F0FDF4', border: '#A7D7C5' },
    leaf: { color: TRACE_TONE.leaf, background: '#F0FDF4', border: '#BFE7C5' },
    moss: { color: TRACE_TONE.moss, background: '#FFFBEB', border: TRACE_TONE.border },
    lime: { color: TRACE_TONE.moss, background: TRACE_TONE.lime, border: '#CFE6A3' },
    neutral: { color: TRACE_TONE.slate, background: '#F8FAF5', border: TRACE_TONE.border },
    soft: { color: TRACE_TONE.deep, background: TRACE_TONE.soft, border: TRACE_TONE.border },
  };
  const selected = styles[tone] || styles.soft;
  return {
    margin: 0,
    borderRadius: '999px',
    border: `1px solid ${selected.border}`,
    color: selected.color,
    background: selected.background,
    ...extra,
  };
};

const getAgentTraceColor = (agent) => {
  if (agent === 'Triage') return TRACE_TONE.deep;
  if (agent === 'Medication') return TRACE_TONE.leaf;
  if (agent === 'General') return TRACE_TONE.moss;
  if (agent === 'Symptom') return TRACE_TONE.deep;
  if (agent === 'Rumor') return TRACE_TONE.moss;
  return TRACE_TONE.slate;
};

const EmptyTraceState = ({ text }) => (
  <div style={{ padding: '12px', borderRadius: '10px', border: `1px dashed ${PALETTE.hairline}`, color: PALETTE.textMute, fontSize: '12px', background: 'rgba(255,255,255,0.58)' }}>
    {text}
  </div>
);

const getSafetyColor = (status) => {
  if (status === 'blocked') return 'moss';
  if (status === 'degraded') return 'lime';
  if (status === 'warning') return 'moss';
  return 'leaf';
};

const getBase64 = (file) =>
  new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.readAsDataURL(file);
    reader.onload = () => resolve(reader.result);
    reader.onerror = (error) => reject(error);
  });

const defaultGreeting = { 
  role: 'ai', 
  content: '你好呀 ✨\n\n愿你心间常驻小欢喜。\n\n我是你的专属全科数字医生，有什么症状描述、体检报告，或者想求证的健康小知识，随时发给我吧！' 
};

const initialChatState = {
  turnCount: 1,
  currentSlots: {},
  currentRoute: "",
  options: [],
  isFinished: true,
  currentTraceData: null,
  activeMessageIndex: -1,
  isEvidencePanelVisible: false,
  traceStep: 0,
  expandedPanels: [],
};

function chatReducer(state, action) {
  switch (action.type) {
    case 'SET_RESPONSE':
      return {
        ...state,
        turnCount: action.payload.turn_count,
        currentSlots: action.payload.current_slots,
        currentRoute: action.payload.route,
        options: action.payload.options || [],
        isFinished: action.payload.is_finished,
        currentTraceData: action.payload.trace_data || null,
        traceStep: 0,
        isEvidencePanelVisible: action.payload.route !== 'CHITCHAT_OR_REJECT',
        activeMessageIndex: action.payload.messageIndex,
      };
    case 'RESTORE_FROM_HISTORY':
      return {
        ...state,
        currentRoute: action.payload.route || "",
        currentTraceData: action.payload.trace_data || null,
        options: action.payload.options || [],
        currentSlots: action.payload.current_slots || {},
        turnCount: action.payload.turn_count || 1,
        isFinished: action.payload.is_finished !== undefined ? action.payload.is_finished : true,
        isEvidencePanelVisible: !!(action.payload.route && action.payload.route !== 'CHITCHAT_OR_REJECT'),
        activeMessageIndex: action.payload.lastAiIndex,
      };
    case 'RESET_SESSION':
      return { ...initialChatState };
    case 'CLEAR_TRACE':
      return {
        ...state,
        currentTraceData: null,
        currentRoute: '',
        isEvidencePanelVisible: false,
        traceStep: 0,
        expandedPanels: [],
        activeMessageIndex: -1,
      };
    case 'SET_ACTIVE_MESSAGE':
      return { ...state, activeMessageIndex: action.payload };
    case 'TOGGLE_EVIDENCE_PANEL':
      return { ...state, isEvidencePanelVisible: !state.isEvidencePanelVisible };
    case 'SET_TRACE_STEP':
      return { ...state, traceStep: action.payload };
    case 'SET_EXPANDED_PANELS':
      return { ...state, expandedPanels: action.payload };
    default:
      return state;
  }
}

const MemoizedMessage = React.memo(({ msg, index, activeMessageIndex, recommendedQueries, onSendMessage, onViewTrace, onOpenKbModal, isFinished, options, selectedOptions, setSelectedOptions, messagesLength }) => {
  return (
    <div data-kb-modal-ready={Boolean(onOpenKbModal)} style={{ display: 'flex', justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start', marginBottom: 24 }}>
      <div style={{
        // AI 气泡固定宽度（与辩论卡内容一致）；用户气泡保持 content-size
        ...(msg.role === 'ai' ? { width: '80%' } : { maxWidth: '80%' }),
        padding: '16px 20px', borderRadius: '18px',
        background: msg.role === 'user' ? PALETTE.tealGhost : PALETTE.glassThick,
        backdropFilter: 'blur(18px) saturate(150%)',
        WebkitBackdropFilter: 'blur(18px) saturate(150%)',
        color: msg.role === 'user' ? PALETTE.tealDeep : PALETTE.textInk,
        boxShadow: msg.role === 'ai'
          ? '0 12px 28px rgba(15,118,110,0.07), 0 2px 8px rgba(15,23,42,0.03)'
          : '0 8px 20px rgba(15,118,110,0.08)',
        border: `1px solid ${PALETTE.hairline}`,
        transition: 'all 0.3s',
        boxSizing: 'border-box',
      }}>
        <div style={{ marginBottom: 8, fontSize: '12px', opacity: msg.role === 'user' ? 0.75 : 0.55, fontWeight: 600, letterSpacing: 0.2 }}>
          {msg.role === 'user' ? '🧑‍💻 咨询者' : '👩‍⚕️ 健康管家'}
        </div>

        {msg.image && (
          <div style={{ marginBottom: 12 }}>
            <img src={msg.image} alt="uploaded" style={{ maxWidth: '200px', borderRadius: '8px', border: '2px solid rgba(255,255,255,0.3)' }} />
          </div>
        )}

        {/* 🌟 思考气泡：正在等待后端流式回包时显示 */}
        {msg.isThinking && (
          <div>
            {/* MADDx 辩论实况：过程中逐步填充事件卡片 */}
            {Array.isArray(msg.maddxEvents) && msg.maddxEvents.length > 0 && (
              <MADDxLiveDebate events={msg.maddxEvents} isLive={true} />
            )}
            {/* Rumor 加权辩论实况（D9 CTAEW）：进行中 */}
            {Array.isArray(msg.rumorEvents) && msg.rumorEvents.length > 0 && (
              <RumorLiveDebate events={msg.rumorEvents} isLive={true} />
            )}
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', minWidth: '200px', padding: '4px 0' }}>
              <div style={{ display: 'flex', gap: '5px', alignItems: 'center' }}>
                <span className="thinking-dot" style={{ animationDelay: '0s' }} />
                <span className="thinking-dot" style={{ animationDelay: '0.22s' }} />
                <span className="thinking-dot" style={{ animationDelay: '0.44s' }} />
              </div>
              <span
                key={msg.thinkingStatus}
                className="thinking-status"
                style={{ fontSize: '13px', color: PALETTE.textMute, fontStyle: 'italic', lineHeight: '1.5' }}
              >
                {msg.thinkingStatus || '🤔 正在思考...'}
              </span>
            </div>
            {msg.content && (
              <div style={{ marginTop: 12, lineHeight: '1.8', fontSize: '15px' }} className="markdown-body">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
              </div>
            )}
          </div>
        )}

        {/* 🌟 多模态卡片流式渲染 */}
        {!msg.isThinking && msg.role === 'user' ? (
          <div style={{ lineHeight: '1.8', fontSize: '15px' }} className="markdown-body">
            {msg.content}
          </div>
        ) : !msg.isThinking && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>

            {/* 辩论实录：仅诊断/辟谣意图展示，默认折叠 */}
            {(Array.isArray(msg.meta_data?.trace_data?.maddx_events) && msg.meta_data.trace_data.maddx_events.length > 0) ||
             (Array.isArray(msg.meta_data?.trace_data?.rumor_events) && msg.meta_data.trace_data.rumor_events.length > 0) ? (
              <Collapse ghost style={{ marginBottom: 8 }}
                items={[
                  ...(Array.isArray(msg.meta_data?.trace_data?.maddx_events) && msg.meta_data.trace_data.maddx_events.length > 0 ? [{
                    key: 'debate', label: <span style={{fontSize:12,color: PALETTE.textMute}}>🔬 诊断辩论实录</span>,
                    children: <MADDxLiveDebate events={msg.meta_data.trace_data.maddx_events} isLive={false} />
                  }] : []),
                  ...(Array.isArray(msg.meta_data?.trace_data?.rumor_events) && msg.meta_data.trace_data.rumor_events.length > 0 ? [{
                    key: 'rumor-debate', label: <span style={{fontSize:12,color: PALETTE.textMute}}>⚖️ 辟谣辩论实录</span>,
                    children: <RumorLiveDebate events={msg.meta_data.trace_data.rumor_events} isLive={false} />
                  }] : []),
                ].filter(Boolean)}
              />
            ) : null}

            {/* 1. 渲染视觉智能体初步提取卡片 */}
            {msg.meta_data?.trace_data?.vision_insights && (
              <div style={{ background: PALETTE.tealGhost, border: `1px solid ${PALETTE.hairline}`, padding: '12px', borderRadius: '10px', fontSize: '13px', color: PALETTE.tealDeep }}>
                <div style={{ fontWeight: 'bold', marginBottom: '6px', display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <EyeOutlined style={{ color: PALETTE.tealDeep, fontSize: '15px' }} /> 影像特征提取
                </div>
                <div style={{ lineHeight: '1.6', marginTop: '8px' }}>
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.meta_data.trace_data.vision_insights}</ReactMarkdown>
                </div>
              </div>
            )}

            {/* 2. 渲染用药审查初步排查卡片 */}
            {msg.meta_data?.trace_data?.med_precheck && (
              <div style={{ background: TRACE_TONE.warm, border: `1px solid ${TRACE_TONE.border}`, padding: '12px', borderRadius: '10px', fontSize: '13px', color: TRACE_TONE.moss }}>
                <div style={{ fontWeight: 'bold', marginBottom: '6px', display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <MedicineBoxOutlined style={{ color: TRACE_TONE.deep, fontSize: '15px' }} /> 用药红线核查
                </div>
                {/* 🌟 核心改造：判断是否有实质内容，没有就给兜底文案 */}
                  {msg.meta_data.trace_data.med_precheck.kg_warnings || msg.meta_data.trace_data.med_precheck.manual_summary ? (
                    <>
                      {msg.meta_data.trace_data.med_precheck.kg_warnings && (
                        <div style={{ color: TRACE_TONE.moss, marginBottom: '6px', fontWeight: 600 }}>
                          🚨 {msg.meta_data.trace_data.med_precheck.kg_warnings}
                        </div>
                      )}
                      {msg.meta_data.trace_data.med_precheck.manual_summary && (
                        <div style={{ color: TRACE_TONE.slate, opacity: 0.9, fontSize: '12px', lineHeight: '1.5' }}>
                          {msg.meta_data.trace_data.med_precheck.manual_summary}
                        </div>
                      )}
                    </>
                  ) : (
                    <div style={{ color: TRACE_TONE.slate, opacity: 0.9, fontSize: '12px', lineHeight: '1.5' }}>
                      ✅ 已核查本地药典与互联网知识库，未匹配到关于该药物的特殊禁忌症或高危说明，按常规遵医嘱服用即可。
                    </div>
                  )}
                </div>
              )}

            {/* 3. 渲染主治大夫的正式回复或追问 */}
            <div style={{ lineHeight: '1.8', fontSize: '15px' }} className="markdown-body">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
            </div>
          </div>
        )}

        {/* 🌟 气泡底部溯源锚点：用户可以自由点击切换历史面板 */}
        {msg.role === 'ai' && msg.meta_data?.trace_data && Object.keys(msg.meta_data.trace_data).length > 0 && (
          <div
            style={{
              marginTop: '12px', paddingTop: '8px', borderTop: '1px dashed rgba(203,213,225,0.8)',
              fontSize: '12px', display: 'flex', alignItems: 'center', gap: '6px',
              color: activeMessageIndex === index ? PALETTE.tealDeep : PALETTE.textMute,
              cursor: 'pointer', transition: 'all 0.3s', fontWeight: activeMessageIndex === index ? 600 : 400
            }}
            onClick={() => onViewTrace(index, msg)}
          >
            <BulbOutlined /> {activeMessageIndex === index ? '正在查看此轮溯源依据' : '点击查看此轮依据'}
          </div>
        )}

        {index === 0 && msg.role === 'ai' && recommendedQueries.length > 0 && (
          <div style={{ marginTop: '20px', display: 'flex', flexDirection: 'column', gap: '10px' }}>
            <div style={{ fontSize: '13px', color: PALETTE.textMute, marginBottom: '4px', fontWeight: 600 }}>
              💡 猜你想问：
            </div>
            {recommendedQueries.map((q, i) => (
              <div
                key={i}
                onClick={() => onSendMessage(q)}
                style={{
                  padding: '12px 16px', background: PALETTE.glassThick, borderRadius: '12px',
                  border: `1px solid ${PALETTE.hairline}`, cursor: 'pointer', display: 'flex',
                  alignItems: 'center', boxShadow: '0 4px 14px rgba(15,118,110,0.05)', transition: 'all 0.2s'
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.borderColor = PALETTE.teal;
                  e.currentTarget.style.boxShadow = '0 8px 20px rgba(20,184,166,0.14)';
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.borderColor = PALETTE.hairline;
                  e.currentTarget.style.boxShadow = '0 4px 14px rgba(15,118,110,0.05)';
                }}
              >
                <div style={{
                  width: '24px', height: '24px', borderRadius: '50%', background: PALETTE.tealGhost,
                  color: PALETTE.tealDeep, display: 'flex', alignItems: 'center', justifyContent: 'center',
                  marginRight: '12px', fontWeight: 'bold'
                }}>#</div>
                <Text style={{ flex: 1, color: PALETTE.textInk, fontSize: '14px' }}>{q}</Text>
                <RightOutlined style={{ color: '#CBD5E1', fontSize: '12px' }} />
              </div>
            ))}
          </div>
        )}

        {msg.role === 'ai' && !msg.isThinking && index === messagesLength - 1 && !isFinished && options.length > 0 && (
          <div style={{ marginTop: 16 }}>
            <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginBottom: '12px' }}>
              {options.map((opt, i) => {
                const isSelected = selectedOptions.includes(opt);
                return (
                  <Button
                    key={i}
                    type={isSelected ? "primary" : "default"}
                    ghost={!isSelected}
                    shape="round"
                    onClick={() => {
                      if (isSelected) {
                        setSelectedOptions(selectedOptions.filter(item => item !== opt));
                      } else {
                        setSelectedOptions([...selectedOptions, opt]);
                      }
                    }}
                    style={{
                      borderColor: PALETTE.tealDeep,
                      color: isSelected ? '#fff' : PALETTE.tealDeep,
                      background: isSelected ? PALETTE.tealDeep : 'transparent',
                      transition: 'all 0.3s'
                    }}
                  >
                    {opt}
                  </Button>
                );
              })}
            </div>
            {selectedOptions.length > 0 && (
              <Button
                type="primary"
                size="small"
                icon={<SendOutlined />}
                onClick={() => onSendMessage(selectedOptions.join('，'))}
                style={{ background: PALETTE.tealDeep, border: 'none', borderRadius: '6px', boxShadow: '0 2px 6px rgba(15,118,110,0.24)', marginTop: '4px' }}
              >
                发送
              </Button>
            )}
          </div>
        )}
      </div>
    </div>
  );
});

const Chat = () => {
  const navigate = useNavigate();

  const [sessionList, setSessionList] = useState([]); 
  const [activeSessionId, setActiveSessionId] = useState(null); 
  // 🌟 修复 1：新增一个 Ref 来实时追踪当前用户到底停留在哪个会话
  const activeSessionRef = useRef(activeSessionId);
  useEffect(() => {
    activeSessionRef.current = activeSessionId;
  }, [activeSessionId]);

  const [messages, setMessages] = useState([]);
  const [inputText, setInputText] = useState('');
  const [loadingMap, setLoadingMap] = useState({});
  const isCurrentLoading = loadingMap[activeSessionId] || false;
  const [isSwitching, setIsSwitching] = useState(false); 
  
  const [selectedImage, setSelectedImage] = useState(null); 
  
  const [chatState, dispatch] = useReducer(chatReducer, initialChatState);
  const { turnCount, currentSlots, currentRoute, options, isFinished, currentTraceData, activeMessageIndex, isEvidencePanelVisible, traceStep, expandedPanels } = chatState;
  const panelTrace = normalizeTraceForPanel(currentTraceData, currentRoute);

  const [selectedOptions, setSelectedOptions] = useState([]);

  const [recommendedQueries, setRecommendedQueries] = useState([]);
  const messagesEndRef = useRef(null); 
  const [kbModalVisible, setKbModalVisible] = useState(false); 
  const [kbModalData, setKbModalData] = useState({ title: '', content: '', dept: '' }); 
  const [scoutModalVisible, setScoutModalVisible] = useState(false);
  const [scoutModalData, setScoutModalData] = useState(null);

  useEffect(() => {
    const initSessions = async () => {
      const token = localStorage.getItem('access_token');
      if (!token) {
        navigate('/login');
        return;
      }
      try {
        const res = await fetch(apiUrl('/api/sessions'), {
          headers: { 'Authorization': `Bearer ${token}` }
        });
        if (res.status === 401) throw new Error('Unauthorized');
        
        const data = await res.json();
        const normalizedData = Array.isArray(data) ? data.map(normalizeSessionItem) : [];
        if (normalizedData.length > 0) {
          setSessionList(normalizedData);
          setActiveSessionId(normalizedData[0].id); 
        } else {
          createNewSession();
        }
      } catch (error) {
        console.error('初始化会话失败:', error); 
        message.error('登录失效，请重新登录');
        navigate('/login');
      }
    };
    initSessions();
  }, [navigate]);

  useEffect(() => {
    const loadMessages = async () => {
      if (!activeSessionId) return;
      setIsSwitching(true);
      const token = localStorage.getItem('access_token');
      
      try {
        const res = await fetch(apiUrl(`/api/sessions/${activeSessionId}/messages`), {
          headers: { 'Authorization': `Bearer ${token}` }
        });
        const historyData = await res.json();
        
        if (historyData.length === 0) {
          setMessages([defaultGreeting]);
          fetch(apiUrl('/api/recommend_queries'))
            .then(r => r.json())
            .then(d => {
              if (d.status === 'success') setRecommendedQueries(d.queries);
            })
            .catch(err => console.error("拉取推荐问题失败:", err));
        } else {
          // 补全图片相对路径为完整 URL（数据库存的是 /static/uploads/xxx.jpg）
          const normalized = historyData.map(m => ({
            ...m,
            image: m.image && m.image.startsWith('/static/')
              ? staticUrl(m.image)
              : m.image
          }));
          setMessages(normalized);
        }
        
        setSelectedOptions([]); // 保留这个，清空用户未发送的选中状态

        let restored = false;
        let lastAiIndex = -1;

        if (historyData.length > 0) {
          for (let i = historyData.length - 1; i >= 0; i--) {
            const msg = historyData[i];
            if (msg.role === 'ai') {
              lastAiIndex = i;
              if (msg.meta_data && typeof msg.meta_data === 'object') {
                if (msg.meta_data.route && msg.meta_data.route !== 'CHITCHAT_OR_REJECT') {
                  restored = true;
                }
                // 🌟 核心修复：从数据库恢复中断的症状槽位与选项状态！
                dispatch({
                  type: 'RESTORE_FROM_HISTORY',
                  payload: { ...msg.meta_data, lastAiIndex }
                });
              }
              break;
            }
          }
        }

        if (!restored) {
          dispatch({ type: 'RESET_SESSION' });
        }

      } catch (error) {
        console.error("加载历史记录失败", error);
      } finally {
        setIsSwitching(false);
      }
    };
    loadMessages();
  }, [activeSessionId]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, options, selectedImage]);

  useEffect(() => {
    if (isEvidencePanelVisible && currentTraceData) {
      const nextPanelTrace = normalizeTraceForPanel(currentTraceData, currentRoute);
      const firstKeys = ['summary', 'milvus'];
      if (nextPanelTrace.hasRumorPanel) firstKeys.push('rumor');
      dispatch({ type: 'SET_TRACE_STEP', payload: 1 });
      dispatch({ type: 'SET_EXPANDED_PANELS', payload: firstKeys });

      const timer1 = setTimeout(() => {
        dispatch({ type: 'SET_TRACE_STEP', payload: 2 });
        dispatch({ type: 'SET_EXPANDED_PANELS', payload: firstKeys });
      }, 800);

      const timer2 = setTimeout(() => {
        dispatch({ type: 'SET_TRACE_STEP', payload: 3 });
        dispatch({ type: 'SET_EXPANDED_PANELS', payload: firstKeys });
      }, 2000);

      const timer3 = setTimeout(() => {
        dispatch({ type: 'SET_TRACE_STEP', payload: 4 });
        dispatch({ type: 'SET_EXPANDED_PANELS', payload: firstKeys });
      }, 3500);

      return () => { clearTimeout(timer1); clearTimeout(timer2); clearTimeout(timer3); };
    } else if (!isCurrentLoading) {
      dispatch({ type: 'SET_TRACE_STEP', payload: 0 });
      dispatch({ type: 'SET_EXPANDED_PANELS', payload: [] });
    }
  }, [isEvidencePanelVisible, currentTraceData, currentRoute, isCurrentLoading]);

  const handleImageUpload = async (file) => {
    const isJpgOrPng = file.type === 'image/jpeg' || file.type === 'image/png';
    if (!isJpgOrPng) { message.error('只能上传 JPG/PNG 格式的图片!'); return false; }
    const isLt5M = file.size / 1024 / 1024 < 5;
    if (!isLt5M) { message.error('图片必须小于 5MB!'); return false; }
    const base64 = await getBase64(file);
    setSelectedImage(base64);
    return false; 
  };

  const removeImage = () => {
    setSelectedImage(null);
  };

  const createNewSession = async () => {
    const token = localStorage.getItem('access_token');
    try {
      const res = await fetch(apiUrl('/api/sessions'), {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${token}` }
      });
      const newSession = await res.json();
      setSessionList(prev => [{ id: newSession.id, title: normalizeSessionTitle(newSession.title), date: '刚刚' }, ...prev]);
      setActiveSessionId(newSession.id);
    } catch (error) {
      console.error('新建咨询失败:', error);
      message.error('新建咨询失败');
    }
  };

  const sendMessage = async (textToSend) => {
    const finalQuery = (textToSend || inputText).trim() || (selectedImage ? "请帮我解读这份医疗图片" : "");
    if (!finalQuery || !activeSessionId) return;

    const token = localStorage.getItem('access_token');
    if (!token) {
      message.warning('您尚未登录或登录已过期，请先登录');
      navigate('/login');
      return;
    }

    // 如果有图片，先上传到后端拿到文件路径 URL，不再将 Base64 塞进消息和数据库
    let imageUrlForMsg = selectedImage; // 本地预览仍用 Base64（仅内存中，不入库）
    let imageUrlForBackend = null;
      if (selectedImage) {
        try {
          const uploadRes = await fetch(apiUrl('/api/upload_image'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
            body: JSON.stringify({ image_base64: selectedImage, session_id: activeSessionId ? parseInt(activeSessionId) : null })
          });
          if (uploadRes.ok) {
            const uploadData = await uploadRes.json();
            imageUrlForBackend = uploadData.file_id || uploadData.storage_key || uploadData.url;
            imageUrlForMsg = uploadData.url
              ? (/^https?:\/\//i.test(uploadData.url) ? uploadData.url : staticUrl(uploadData.url))
              : selectedImage;
          }
        } catch (e) {
          console.error('图片上传失败:', e);
      }
    }

    dispatch({ type: 'CLEAR_TRACE' });
    const newMessages = [...messages, { role: 'user', content: finalQuery, image: imageUrlForMsg }];
    setMessages(newMessages);

    setInputText('');
    setSelectedImage(null);
    setSelectedOptions([]);
    const sessionIdToLock = activeSessionId;
    setLoadingMap(prev => ({ ...prev, [sessionIdToLock]: true }));

    setRecommendedQueries([]);

    const reqSessionId = activeSessionId;

    try {
      const historyPayload = newMessages
        .slice(0, -1) 
        .filter(m => m.content !== defaultGreeting.content)
        .map(m => ({
          role: m.role === 'ai' ? 'assistant' : 'user',
          content: m.content
        }));

      // ==========================================
      // 🌟 核心改造 1：倒序遍历历史消息，捞回最近一次的跨模态检查报告
      // ==========================================
      let carriedVisionContext = null;
      let carriedMedPrecheck = null;
      
      for (let i = messages.length - 1; i >= 0; i--) {
        const msg = messages[i];
        if (msg.role === 'ai' && msg.meta_data?.trace_data) {
          if (!carriedVisionContext && msg.meta_data.trace_data.vision_insights) {
            carriedVisionContext = msg.meta_data.trace_data.vision_insights;
          }
          if (!carriedMedPrecheck && msg.meta_data.trace_data.med_precheck) {
            carriedMedPrecheck = msg.meta_data.trace_data.med_precheck;
          }
        }
        // 只要两个都找到了，或者遍历完了，就停止寻找
        if (carriedVisionContext && carriedMedPrecheck) break;
      }

      // ==========================================
      // 🌟 核心改造 2：将捞回的报告装进行囊，作为参数传给后端
      // ==========================================
      const payload = {
        session_id: parseInt(activeSessionId),
        query: finalQuery,
        messages_history: historyPayload,
        turn_count: turnCount,
        current_slots: currentSlots,
        current_route: currentRoute,
        image_data: imageUrlForBackend || newMessages[newMessages.length - 1].image,
        vision_context: carriedVisionContext, // 携带视觉记忆
        med_precheck: carriedMedPrecheck      // 携带用药记忆
      };

      // ==========================================
      // 🌟 SSE 流式接收：立即插入思考气泡，随进度事件更新，done 时替换为正式回复
      // ==========================================

      // 1. 先在消息列表末尾插入思考气泡占位
      setMessages(prev => [...prev, { role: 'ai', isThinking: true, thinkingStatus: '🤔 正在思考...' }]);

      const response = await fetch(apiUrl('/api/chat'), {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify(payload)
      });

      if (response.status === 401) {
        message.error('登录状态已失效，请重新登录');
        localStorage.removeItem('access_token');
        navigate('/login');
        return;
      }
      if (response.status === 409) {
        const payload = await response.json().catch(() => ({}));
        message.warning(payload.detail || '该会话正在生成中，请稍后再试');
        setMessages(prev => prev.filter(m => !m.isThinking));
        return;
      }

      if (!response.ok) throw new Error("网络响应异常");

      // 2. 用 ReadableStream 逐块读取 SSE 数据
      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let sseBuffer = '';
      let streamedAnswer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        sseBuffer += decoder.decode(value, { stream: true });

        // SSE 事件以 "\n\n" 分隔
        const parts = sseBuffer.split('\n\n');
        sseBuffer = parts.pop(); // 末尾不完整的留在缓冲区

        for (const part of parts) {
          const line = part.trim();
          if (!line.startsWith('data: ')) continue;

          let eventData;
          try {
            eventData = JSON.parse(line.slice(6));
          } catch {
            continue;
          }

          if (eventData.type === 'chunk') {
            streamedAnswer += eventData.content || '';
            if (activeSessionRef.current === reqSessionId) {
              setMessages(prev => {
                const updated = [...prev];
                const last = updated[updated.length - 1];
                updated[updated.length - 1] = {
                  ...last,
                  content: streamedAnswer,
                };
                return updated;
              });
            }

          } else if (eventData.type === 'status') {
            // 3. 更新气泡文字，key 变化触发 CSS 淡入动画
            if (activeSessionRef.current === reqSessionId) {
              setMessages(prev => {
                const updated = [...prev];
                updated[updated.length - 1] = {
                  ...updated[updated.length - 1],
                  thinkingStatus: eventData.message
                };
                return updated;
              });
            }

          } else if (eventData.type === 'maddx_step') {
            // 🆕 MADDx 辩论事件：追加到思考气泡的事件流，实时可视化
            if (activeSessionRef.current === reqSessionId) {
              setMessages(prev => {
                const updated = [...prev];
                const last = updated[updated.length - 1];
                const prevEvents = Array.isArray(last.maddxEvents) ? last.maddxEvents : [];
                updated[updated.length - 1] = {
                  ...last,
                  maddxEvents: [...prevEvents, eventData],
                };
                return updated;
              });
            }

          } else if (eventData.type === 'rumor_step') {
            // 🆕 Rumor 加权辩论事件（D9 CTAEW）：追加到 rumorEvents 流
            if (activeSessionRef.current === reqSessionId) {
              setMessages(prev => {
                const updated = [...prev];
                const last = updated[updated.length - 1];
                const prevEvents = Array.isArray(last.rumorEvents) ? last.rumorEvents : [];
                updated[updated.length - 1] = {
                  ...last,
                  rumorEvents: [...prevEvents, eventData],
                };
                return updated;
              });
            }

          } else if (eventData.type === 'hallucination_check') {
            // 🛡️ 幻觉检测员事件（rumor / general / medication 三处共用）
            // 把 message 字段当作 thinking status 推到气泡上做实时反馈
            if (activeSessionRef.current === reqSessionId && eventData.message) {
              setMessages(prev => {
                const updated = [...prev];
                updated[updated.length - 1] = {
                  ...updated[updated.length - 1],
                  thinkingStatus: eventData.message,
                };
                return updated;
              });
            }

          } else if (eventData.type === 'agent_step') {
            // 🌐 统一 agent 步骤事件（medication / report / symptom / general 共用）
            // 1) message 字段更新 thinkingStatus，让用户看到推理过程
            // 2) 同时按 agent 名称归类到 agentEvents[agent]，便于历史回放
            if (activeSessionRef.current === reqSessionId) {
              setMessages(prev => {
                const updated = [...prev];
                const last = updated[updated.length - 1];
                const prevByAgent = last.agentEvents || {};
                const agentName = eventData.agent || 'unknown';
                const list = prevByAgent[agentName] || [];
                updated[updated.length - 1] = {
                  ...last,
                  thinkingStatus: eventData.message || last.thinkingStatus,
                  agentEvents: {
                    ...prevByAgent,
                    [agentName]: [...list, eventData],
                  },
                };
                return updated;
              });
            }

          } else if (eventData.type === 'done') {
            // 4. 用真实回复替换思考气泡
            const newAiMsg = {
              role: 'ai',
              content: eventData.answer,
              meta_data: {
                route: eventData.route,
                trace_data: eventData.trace_data,
                run_id: eventData.run_id,
                state_version: eventData.state_version,
              }
            };

            if (activeSessionRef.current === reqSessionId) {
              let updatedMessages;
              setMessages(prev => {
                // 弹出最后一条思考气泡，换成正式回复
                updatedMessages = [...prev.slice(0, -1), newAiMsg];
                return updatedMessages;
              });

              dispatch({
                type: 'SET_RESPONSE',
                payload: {
                  turn_count: eventData.turn_count,
                  current_slots: eventData.current_slots,
                  route: eventData.route,
                  options: eventData.options || [],
                  is_finished: eventData.is_finished,
                  trace_data: eventData.trace_data || null,
                  messageIndex: updatedMessages ? updatedMessages.length - 1 : -1,
                }
              });
            }

            // 刷新左侧会话列表（放在 if 外，保证无论在哪个会话都能更新摘要）
            fetch(apiUrl('/api/sessions'), { headers: { 'Authorization': `Bearer ${token}` } })
              .then(res => res.json())
              .then(sessions => setSessionList(Array.isArray(sessions) ? sessions.map(normalizeSessionItem) : []));

          } else if (eventData.type === 'error') {
            // 5. 错误时：将气泡替换为错误提示
            setMessages(prev => [
              ...prev.slice(0, -1),
              { role: 'ai', content: `❌ ${eventData.message || '服务暂时不可用，请稍后再试'}` }
            ]);
          }
        }
      }

    } catch (error) {
      console.error("后端请求失败:", error);
      // 清除可能残留的思考气泡，换成错误提示
      setMessages(prev => {
        const last = prev[prev.length - 1];
        const withoutBubble = last?.isThinking ? prev.slice(0, -1) : prev;
        return [...withoutBubble, { role: 'ai', content: '❌ 网络请求失败，请确保 python api_server.py 已启动。' }];
      });
    } finally {
      setLoadingMap(prev => ({ ...prev, [sessionIdToLock]: false }));
    }
  };

  const handleViewTrace = (index, msg) => {
    dispatch({ type: 'SET_ACTIVE_MESSAGE', payload: index });
    dispatch({
      type: 'RESTORE_FROM_HISTORY',
      payload: {
        route: msg.meta_data?.route || "",
        trace_data: msg.meta_data?.trace_data || null,
        options: [],
        current_slots: {},
        turn_count: 1,
        is_finished: true,
        lastAiIndex: index,
      }
    });
    dispatch({ type: 'SET_TRACE_STEP', payload: 0 });
    setTimeout(() => dispatch({ type: 'SET_TRACE_STEP', payload: 1 }), 50);
  };

  const renderTraceSummaryPanel = () => (
    <Panel header={<Space><BulbOutlined style={{ color: PALETTE.tealDeep }} />本轮处理概览</Space>} key="summary" className="panel-anim" style={panelCardStyle}>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px', marginBottom: '10px' }}>
        <div style={{ padding: '10px', borderRadius: '10px', background: PALETTE.tealGhost }}>
          <Text style={{ display: 'block', fontSize: '11px', color: PALETTE.textMute }}>路由</Text>
          <Text strong style={{ fontSize: '12px', color: PALETTE.textInk }}>{panelTrace.summary.route}</Text>
        </div>
        <div style={{ padding: '10px', borderRadius: '10px', background: '#F8FAFC' }}>
          <Text style={{ display: 'block', fontSize: '11px', color: PALETTE.textMute }}>协作模式</Text>
          <Text strong style={{ fontSize: '12px', color: PALETTE.textInk }}>{panelTrace.summary.collabMode}</Text>
        </div>
      </div>
      <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
        <Tag style={traceTagStyle('leaf')}>Milvus {panelTrace.summary.milvusEvidenceCount}</Tag>
        <Tag style={traceTagStyle('moss')}>KG {panelTrace.summary.kgConstraintCount}</Tag>
        <Tag style={traceTagStyle('deep')}>外部 {panelTrace.summary.externalSourceCount}</Tag>
        <Tag style={traceTagStyle(getSafetyColor(panelTrace.summary.safetyStatus))}>安全 {panelTrace.summary.safetyStatus}</Tag>
      </div>
      {panelTrace.summary.agents.length > 0 && (
        <div style={{ marginTop: '10px', display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
          {panelTrace.summary.agents.map(agent => <Tag key={agent} style={traceTagStyle('neutral')}>{agent}</Tag>)}
        </div>
      )}
    </Panel>
  );

  const renderAgentAuditPanel = () => (
    <Panel header={<Space><ApiOutlined style={{ color: TRACE_TONE.deep }} />多智能体协作链路</Space>} key="audit" className="panel-anim" style={panelCardStyle}>
      {panelTrace.auditLog.length > 0 ? (
        <Timeline style={{ marginTop: '8px', marginLeft: '4px' }}>
          {panelTrace.auditLog.map((log, index) => {
            const text = typeof log === 'string' ? log : JSON.stringify(log);
            const match = text.match(/^\[(.*?)\]\s*(.*)/);
            const agent = match ? match[1] : 'System';
            const action = match ? match[2] : text;
            const dotColor = getAgentTraceColor(agent);
            return (
              <Timeline.Item key={index} color={dotColor} style={{ paddingBottom: index === panelTrace.auditLog.length - 1 ? '0' : '18px' }}>
                <Text strong style={{ display: 'block', fontSize: '13px', color: PALETTE.textInk, marginBottom: '4px' }}>{agent} Agent</Text>
                <Text type="secondary" style={{ fontSize: '12px', lineHeight: '1.5' }}>{action}</Text>
              </Timeline.Item>
            );
          })}
        </Timeline>
      ) : (
        <EmptyTraceState text="本轮未产生多智能体审计日志。" />
      )}
      {panelTrace.scratchpad.length > 0 && (
        <div style={{ marginTop: '14px', paddingTop: '14px', borderTop: '1px dashed rgba(148,163,184,0.3)' }}>
          <div style={{ fontSize: '12px', fontWeight: 600, color: PALETTE.textMute, marginBottom: '8px', display: 'flex', alignItems: 'center', gap: '6px' }}>
            <MessageOutlined /> 内部会诊留言板
          </div>
          {panelTrace.scratchpad.map((msg, idx) => (
            <div key={idx} style={{ background: TRACE_TONE.warm, padding: '10px 12px', borderRadius: '8px', marginBottom: '8px', border: `1px solid ${TRACE_TONE.border}`, fontSize: '12px' }}>
              <Text style={{ color: TRACE_TONE.moss }}>{typeof msg === 'string' ? msg : msg.msg}</Text>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );

  const renderMilvusPanel = () => (
    <Panel header={<Space><DatabaseOutlined />主证据：Milvus RAG</Space>} key="milvus" className="panel-anim" style={panelCardStyle}>
      <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginBottom: '10px' }}>
        <Tag style={traceTagStyle('leaf')}>Evidence {panelTrace.rag.evidenceCount}</Tag>
        <Tag style={traceTagStyle('deep')}>Background {panelTrace.rag.backgroundCount}</Tag>
      </div>
      {panelTrace.rag.evidenceItems.length > 0 ? (
        <div className="advanced-scrollbar" style={{ maxHeight: '260px', overflowY: 'auto', paddingRight: '4px' }}>
          {panelTrace.rag.evidenceItems.map((item, index) => (
            <div
              key={item.ref_id || index}
              onClick={() => {
                setKbModalData(buildEvidenceModalData(item, 'Evidence'));
                setKbModalVisible(true);
              }}
              style={{ marginBottom: '10px', padding: '10px', background: '#F0FDF4', borderRadius: '8px', border: '1px solid #BBF7D0', cursor: 'pointer' }}
            >
              <div style={{ display: 'flex', gap: '6px', alignItems: 'center', marginBottom: '6px', flexWrap: 'wrap' }}>
                <Tag style={traceTagStyle('leaf')}>evidence</Tag>
                {item.source_tier && <Tag style={traceTagStyle('deep')}>{item.source_tier}</Tag>}
                {item.scores?.reranker_prob ? <Tag style={traceTagStyle('moss')}>rerank {Number(item.scores.reranker_prob).toFixed(2)}</Tag> : null}
              </div>
              <div style={{ fontWeight: 600, fontSize: '12px', color: PALETTE.textSlate, marginBottom: '4px' }}>{getKnowledgeCard(item)?.card_title || item.title || 'Untitled evidence'}</div>
              <div style={{ fontSize: '12px', color: PALETTE.textMute, lineHeight: '1.5', display: '-webkit-box', WebkitLineClamp: 3, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>{getKnowledgeCardSummary(item)}</div>
              {getLocatorLabel(item) && (
                <div style={{ fontSize: '11px', color: '#0F766E', marginTop: '6px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {getLocatorLabel(item)}
                </div>
              )}
            </div>
          ))}
        </div>
      ) : (
        <EmptyTraceState text="本轮未命中 Milvus 主证据。" />
      )}
    </Panel>
  );

  const renderKgPanel = () => (
    <Panel header={<Space><ShareAltOutlined />KG 约束路径</Space>} key="kg" className="panel-anim" style={panelCardStyle}>
      <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginBottom: '10px' }}>
        <Tag style={traceTagStyle('moss')}>constraint_evidence</Tag>
        <Tag style={traceTagStyle(panelTrace.kg.degraded ? 'lime' : 'leaf')}>{panelTrace.kg.degraded ? 'Degraded' : 'Active'}</Tag>
        <Tag style={traceTagStyle('neutral')}>citation_allowed=false</Tag>
      </div>
      {panelTrace.kg.paths.length > 0 ? (
        <div className="advanced-scrollbar" style={{ maxHeight: '220px', overflowY: 'auto', paddingRight: '4px' }}>
          {panelTrace.kg.paths.map((path, index) => (
            <div key={path.source_id || index} style={{ marginBottom: '10px', padding: '10px', background: TRACE_TONE.warm, borderRadius: '8px', border: `1px solid ${TRACE_TONE.border}` }}>
              <div style={{ display: 'flex', gap: '6px', alignItems: 'center', marginBottom: '6px', flexWrap: 'wrap' }}>
                {path.confidence !== undefined && <Tag style={traceTagStyle('lime')}>score {Number(path.confidence).toFixed(2)}</Tag>}
              </div>
              <div style={{ fontWeight: 600, fontSize: '12px', color: PALETTE.textSlate, marginBottom: '4px' }}>{path.head || 'KG'} → {path.tail || 'related node'}</div>
              <div style={{ fontSize: '12px', color: PALETTE.textMute, lineHeight: '1.5' }}>{path.relation || 'relationship constraint'}</div>
            </div>
          ))}
        </div>
      ) : (
        <EmptyTraceState text="本轮未命中 KG 约束。" />
      )}
    </Panel>
  );

  const renderRumorPanel = () => (
    <Panel header={<Space><SafetyCertificateOutlined style={{ color: TRACE_TONE.moss }} />辟谣证据链</Space>} key="rumor" className="panel-anim" style={panelCardStyle}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
        <div>
          <Text strong style={{ fontSize: '13px', color: PALETTE.textInk }}>舆情线索 Scout</Text>
          <Text type="secondary" style={{ display: 'block', fontSize: '11px', margin: '2px 0 8px' }}>舆情线索，不作为医学结论依据。</Text>
          {panelTrace.rumor.scoutData.length > 0 ? (
            panelTrace.rumor.scoutData.map((source, index) => (
              <div key={index} role="button" tabIndex={0} onClick={() => { setScoutModalData(source); setScoutModalVisible(true); }} style={{ marginBottom: '8px', padding: '10px', background: '#F8FAF5', borderRadius: '10px', border: `1px solid ${TRACE_TONE.border}`, cursor: 'pointer' }}>
                <div style={{ fontWeight: 600, fontSize: '12px', color: TRACE_TONE.deep, marginBottom: '6px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{source.display_title || source.title || '网络来源'}</div>
                <div style={{ display: 'flex', gap: '6px', marginBottom: '6px', flexWrap: 'wrap' }}>
                  {source.platform && <Tag style={traceTagStyle('deep')}>{source.platform}</Tag>}
                  {source.stance && <Tag style={traceTagStyle('leaf')}>{source.stance}</Tag>}
                  <Tag style={traceTagStyle('moss')}>舆情线索</Tag>
                </div>
                <div style={{ fontSize: '12px', color: PALETTE.textMute, lineHeight: '1.5', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>{source.content_overview || source.summary || '暂未生成摘要。'}</div>
              </div>
            ))
          ) : (
            <EmptyTraceState text="本轮未收集到舆情线索。" />
          )}
        </div>

        <div>
          <Text strong style={{ fontSize: '13px', color: PALETTE.textInk }}>权威医学事实 Medical</Text>
          {panelTrace.rumor.medicalData.length > 0 ? (
            <div style={{ marginTop: '8px' }}>
              {panelTrace.rumor.medicalData.map((source, index) => (
                <div key={index} style={{ marginBottom: '8px', padding: '10px', background: source.is_internal ? TRACE_TONE.soft : TRACE_TONE.warm, borderRadius: '10px', border: `1px solid ${TRACE_TONE.border}` }}>
                  <div style={{ display: 'flex', alignItems: 'center', marginBottom: '4px', gap: '6px' }}>
                    {source.is_internal ? <Tag icon={<DatabaseOutlined />} style={traceTagStyle('leaf')}>本地智库</Tag> : <Tag icon={<GlobalOutlined />} style={traceTagStyle('deep')}>权威外网</Tag>}
                    <Text strong style={{ fontSize: '12px', color: PALETTE.textInk, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{source.title || '医学事实'}</Text>
                  </div>
                  <div style={{ fontSize: '12px', color: PALETTE.textMute, lineHeight: '1.5', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>{getMedicalSummaryText(source)}</div>
                </div>
              ))}
            </div>
          ) : (
            <div style={{ marginTop: '8px' }}><EmptyTraceState text="本轮未返回独立的权威医学事实卡。" /></div>
          )}
        </div>

        <div>
          <Text strong style={{ fontSize: '13px', color: PALETTE.textInk }}>交叉辩论 Critic</Text>
          {panelTrace.rumor.criticReasoning ? (
            <div className="markdown-body" style={{ marginTop: '8px', padding: '12px', borderRadius: '10px', background: TRACE_TONE.warm, border: `1px solid ${TRACE_TONE.border}`, color: PALETTE.textSlate, fontSize: '12px', lineHeight: '1.7' }}>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{panelTrace.rumor.criticReasoning}</ReactMarkdown>
            </div>
          ) : (
            <div style={{ marginTop: '8px' }}><EmptyTraceState text="本轮未产生交叉辩论记录。" /></div>
          )}
          {panelTrace.rumor.rumorEvents.length > 0 && (
            <div style={{ marginTop: '10px' }}>
              <RumorLiveDebate events={panelTrace.rumor.rumorEvents} isLive={false} />
            </div>
          )}
        </div>
      </div>
    </Panel>
  );

  const renderSupplementalPanel = () => (
    <Panel header={<Space><BookOutlined />补充证据</Space>} key="supplemental" className="panel-anim" style={panelCardStyle}>
      {panelTrace.supplemental.maddxDebate && (
        <div style={{ marginBottom: '12px' }}>
          <MADDxDebateView dag={panelTrace.supplemental.maddxDebate} />
        </div>
      )}
      {panelTrace.supplemental.maddxEvents.length > 0 && (
        <div style={{ marginBottom: '12px' }}>
          <MADDxLiveDebate events={panelTrace.supplemental.maddxEvents} isLive={false} />
        </div>
      )}
      {panelTrace.supplemental.sources.length > 0 ? (
        <div className="advanced-scrollbar" style={{ maxHeight: '300px', overflowY: 'auto', paddingRight: '4px' }}>
          {panelTrace.supplemental.sources.map((source, index) => (
            <div key={index} style={{ marginBottom: '10px', padding: '10px', background: '#F8FAFC', borderRadius: '10px', border: `1px solid ${PALETTE.hairline}` }}>
              <div style={{ fontWeight: 600, fontSize: '12px', color: PALETTE.textSlate, marginBottom: '5px' }}>{getKnowledgeCard(source)?.card_title || source.title || source.department || '补充来源'}</div>
              <div style={{ fontSize: '12px', color: PALETTE.textMute, lineHeight: '1.5', display: '-webkit-box', WebkitLineClamp: 3, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>{getLegacySourceSummary(source)}</div>
            </div>
          ))}
        </div>
      ) : !panelTrace.supplemental.maddxDebate && panelTrace.supplemental.maddxEvents.length === 0 ? (
        <EmptyTraceState text="本轮未触发补充证据。" />
      ) : null}
    </Panel>
  );

  return (
    <>
      <style>
        {`
          .advanced-scrollbar::-webkit-scrollbar { width: 4px; height: 4px; }
          .advanced-scrollbar::-webkit-scrollbar-track { background: transparent; }
          .advanced-scrollbar::-webkit-scrollbar-thumb {
            background: rgba(148, 163, 184, 0.2);
            border-radius: 10px;
            border-top: 24px solid transparent;    
            border-bottom: 24px solid transparent; 
            background-clip: padding-box;
          }
          .advanced-scrollbar::-webkit-scrollbar-thumb:hover { background: rgba(20, 184, 166, 0.4); }
          
          @keyframes slideInRight {
            from { opacity: 0; transform: translateX(15px); }
            to { opacity: 1; transform: translateX(0); }
          }
          .panel-anim {
            animation: slideInRight 0.5s ease-out forwards;
          }
          .custom-spin .ant-spin-dot-item {
            background-color: ${PALETTE.teal} !important;
          }
          .custom-spin .ant-spin-text {
            color: ${PALETTE.tealDeep} !important;
            font-weight: 500;
          }
          .trustmed-chat .ant-collapse-item {
            background: ${PALETTE.glassThick} !important;
            border: 1px solid ${PALETTE.hairline} !important;
            box-shadow: 0 8px 22px rgba(15, 118, 110, 0.05) !important;
          }
          .trustmed-chat .ant-collapse-header {
            color: ${PALETTE.textInk} !important;
            font-weight: 600;
          }
          .trustmed-chat .ant-collapse-content-box {
            background: transparent;
          }
          .trustmed-chat .ant-skeleton-content .ant-skeleton-title,
          .trustmed-chat .ant-skeleton-content .ant-skeleton-paragraph > li {
            background: linear-gradient(90deg, rgba(20,184,166,0.08), rgba(255,255,255,0.72), rgba(20,184,166,0.08)) !important;
          }
          .ant-modal-content {
            background: ${PALETTE.glassThick} !important;
            backdrop-filter: blur(24px) saturate(160%);
            -webkit-backdrop-filter: blur(24px) saturate(160%);
            border: 1px solid ${PALETTE.hairline};
            box-shadow: 0 24px 60px rgba(15, 118, 110, 0.14), 0 4px 12px rgba(15, 23, 42, 0.06) !important;
          }
          .ant-modal-header {
            background: transparent !important;
          }
          .markdown-body {
            overflow-wrap: anywhere;
          }
          .markdown-body table {
            display: block;
            width: 100%;
            max-width: 100%;
            overflow-x: auto;
            border-collapse: separate;
            border-spacing: 0;
            margin: 12px 0;
            font-size: 14px;
            line-height: 1.6;
          }
          .markdown-body th,
          .markdown-body td {
            border: 1px solid rgba(15, 118, 110, 0.14);
            padding: 9px 12px;
            min-width: 96px;
            vertical-align: top;
            background: rgba(255, 255, 255, 0.76);
          }
          .markdown-body th {
            background: rgba(20, 184, 166, 0.10);
            color: ${PALETTE.textInk};
            font-weight: 700;
            white-space: nowrap;
          }

          /* 🌟 消除底部输入框 TextArea 所有状态下的黑色外框 / 浏览器原生 outline */
          .chat-input-wrap,
          .chat-input-wrap *,
          .chat-input-wrap *:focus,
          .chat-input-wrap *:focus-visible,
          .chat-input-wrap *:focus-within,
          .chat-input-wrap *:hover,
          .chat-input-wrap *:active {
            outline: none !important;
            -webkit-tap-highlight-color: transparent !important;
          }
          .chat-input-wrap textarea,
          .chat-input-wrap .ant-input,
          .chat-input-wrap .ant-input-affix-wrapper,
          .chat-input-wrap .ant-input-textarea,
          .chat-input-wrap .ant-input-borderless {
            border: none !important;
            box-shadow: none !important;
            background: transparent !important;
          }
          .chat-input-wrap textarea:focus,
          .chat-input-wrap textarea:hover,
          .chat-input-wrap textarea:active,
          .chat-input-wrap .ant-input:focus,
          .chat-input-wrap .ant-input:hover,
          .chat-input-wrap .ant-input-affix-wrapper:focus,
          .chat-input-wrap .ant-input-affix-wrapper-focused,
          .chat-input-wrap .ant-input-affix-wrapper:hover {
            border: none !important;
            box-shadow: none !important;
            outline: none !important;
          }

          /* 思考气泡动画 */
          @keyframes thinkingPulse {
            0%, 60%, 100% { transform: scale(0.7); opacity: 0.4; }
            30% { transform: scale(1.3); opacity: 1; }
          }
          .thinking-dot {
            width: 7px; height: 7px;
            border-radius: 50%;
            background: ${PALETTE.teal};
            display: inline-block;
            animation: thinkingPulse 1.4s ease-in-out infinite;
          }
          @keyframes statusFadeIn {
            from { opacity: 0; transform: translateY(4px); }
            to   { opacity: 1; transform: translateY(0); }
          }
          .thinking-status {
            animation: statusFadeIn 0.35s ease-out forwards;
          }
        `}
      </style>
      
      <div className="trustmed-chat" style={{ display: 'flex', height: '100vh', width: '100vw', background: PAGE_BACKGROUND, overflow: 'hidden' }}>
        
        {/* 1. 左侧边栏 */}
        <div style={{ width: '260px', flexShrink: 0, ...glassSurface, borderTop: 'none', borderBottom: 'none', borderLeft: 'none', display: 'flex', flexDirection: 'column', padding: '24px', position: 'relative' }}>
          <div style={{ display: 'flex', alignItems: 'center', marginBottom: '32px', zIndex: 1 }}>
            <MedicineBoxOutlined style={{ fontSize: 28, color: PALETTE.tealDeep, marginRight: 12 }} />
            <Title level={4} style={{ margin: 0, fontWeight: 800, color: PALETTE.textInk, letterSpacing: '-0.2px' }}>TrustMed AI</Title>
          </div>
          
          <Button type="primary" icon={<PlusOutlined />} block onClick={createNewSession} style={{ background: `linear-gradient(135deg, ${PALETTE.teal} 0%, ${PALETTE.tealDeep} 100%)`, border: 'none', borderRadius: '10px', height: '42px', fontSize: '15px', fontWeight: 600, marginBottom: '24px', boxShadow: '0 8px 20px rgba(15,118,110,0.22)', zIndex: 1 }}>
            新建健康咨询
          </Button>

          <div className="advanced-scrollbar" style={{ flex: 1, overflowY: 'auto', marginBottom: '24px', zIndex: 1, paddingRight: '4px' }}>
            {sessionList.map(item => (
              <div 
                key={item.id} 
                onClick={() => setActiveSessionId(item.id)} 
                style={{ 
                  padding: '12px 16px', borderRadius: '10px', 
                  background: activeSessionId === item.id ? PALETTE.glassThick : 'rgba(255,255,255,0.24)', 
                  border: activeSessionId === item.id ? `1px solid ${PALETTE.hairline}` : '1px solid transparent', 
                  marginBottom: '8px', cursor: 'pointer', display: 'flex', alignItems: 'center',
                  transition: 'all 0.2s',
                  boxShadow: activeSessionId === item.id ? '0 6px 16px rgba(15,118,110,0.08)' : 'none'
                }}
              >
                <Text style={{ fontSize: '15px', color: activeSessionId === item.id ? PALETTE.tealDeep : PALETTE.textSlate, fontWeight: activeSessionId === item.id ? 600 : 400, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: '100%' }}>
                  {normalizeSessionTitle(item.title)}
                </Text>
              </div>
            ))}
          </div>

          <div style={{ marginTop: 'auto', display: 'flex', flexDirection: 'column', gap: '10px', zIndex: 1 }}>
            
            {/* 🌟 医疗知识图谱入口卡片 (莫兰迪蓝) */}
            <div 
              onClick={() => navigate('/graph')} 
              style={{
                ...glassThickSurface,
                borderRadius: '12px',
                padding: '12px 14px',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                boxShadow: '0 6px 18px rgba(15,118,110,0.08)',
                transition: 'all 0.3s ease'
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.transform = 'translateY(-2px)';
                e.currentTarget.style.boxShadow = '0 10px 24px rgba(15,118,110,0.14)';
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.transform = 'translateY(0)';
                e.currentTarget.style.boxShadow = '0 6px 18px rgba(15,118,110,0.08)';
              }}
            >
              <div style={{ 
                width: '36px', height: '36px', borderRadius: '8px', 
                background: `linear-gradient(135deg, ${PALETTE.tealSoft} 0%, ${PALETTE.teal} 100%)`, display: 'flex', justifyContent: 'center', alignItems: 'center', marginRight: '12px' 
              }}>
                <ShareAltOutlined style={{ color: '#FFFFFF', fontSize: '20px' }} />
              </div>
              <div style={{ flex: 1 }}>
                <div style={{ color: PALETTE.textInk, fontWeight: 'bold', fontSize: '14px', letterSpacing: '0.5px' }}>医疗知识图谱</div>
                <div style={{ color: PALETTE.tealDeep, fontSize: '12px', marginTop: '2px' }}>探索疾病与药物星系</div>
              </div>
            </div>

            {/* 🌟 健康知识专区 (升级为果冻橙质感) */}
            <div 
              onClick={() => navigate('/knowledge')} 
              style={{
                ...glassThickSurface,
                borderRadius: '12px',
                padding: '12px 14px',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                boxShadow: '0 6px 18px rgba(15,118,110,0.08)',
                transition: 'all 0.3s ease'
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.transform = 'translateY(-2px)';
                e.currentTarget.style.boxShadow = '0 10px 24px rgba(15,118,110,0.14)';
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.transform = 'translateY(0)';
                e.currentTarget.style.boxShadow = '0 6px 18px rgba(15,118,110,0.08)';
              }}
            >
              <div style={{ 
                width: '36px', height: '36px', borderRadius: '8px', 
                background: `linear-gradient(135deg, ${PALETTE.cream} 0%, ${PALETTE.amber} 100%)`, display: 'flex', justifyContent: 'center', alignItems: 'center', marginRight: '12px' 
              }}>
                <BookOutlined style={{ color: '#FFFFFF', fontSize: '20px' }} />
              </div>
              <div style={{ flex: 1 }}>
                <div style={{ color: PALETTE.textInk, fontWeight: 'bold', fontSize: '14px', letterSpacing: '0.5px' }}>健康知识专区</div>
                <div style={{ color: PALETTE.tealDeep, fontSize: '12px', marginTop: '2px' }}>硬核科普与辟谣</div>
              </div>
            </div>

            {/* 🌟 我的数字健康档案 (升级为薄荷青质感) */}
            <div 
              onClick={() => navigate('/profile')} 
              style={{
                ...glassThickSurface,
                borderRadius: '12px',
                padding: '12px 14px',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                boxShadow: '0 6px 18px rgba(15,118,110,0.08)',
                transition: 'all 0.3s ease'
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.transform = 'translateY(-2px)';
                e.currentTarget.style.boxShadow = '0 10px 24px rgba(15,118,110,0.14)';
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.transform = 'translateY(0)';
                e.currentTarget.style.boxShadow = '0 6px 18px rgba(15,118,110,0.08)';
              }}
            >
              <div style={{ 
                width: '36px', height: '36px', borderRadius: '8px', 
                background: `linear-gradient(135deg, ${PALETTE.teal} 0%, ${PALETTE.tealDeep} 100%)`, display: 'flex', justifyContent: 'center', alignItems: 'center', marginRight: '12px' 
              }}>
                <UserOutlined style={{ color: '#FFFFFF', fontSize: '20px' }} />
              </div>
              <div style={{ flex: 1 }}>
                <div style={{ color: PALETTE.textInk, fontWeight: 'bold', fontSize: '14px', letterSpacing: '0.5px' }}>我的数字健康档案</div>
                <div style={{ color: PALETTE.tealDeep, fontSize: '12px', marginTop: '2px' }}>查看与编辑体征</div>
              </div>
            </div>

          </div>
        </div>

        {/* 2. 中部主对话区 */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', background: 'transparent', position: 'relative' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '16px 24px', borderBottom: `1px solid ${PALETTE.hairline}`, background: PALETTE.glassThick, backdropFilter: 'blur(24px) saturate(160%)', WebkitBackdropFilter: 'blur(24px) saturate(160%)', boxShadow: '0 8px 24px rgba(15,118,110,0.05)' }}>
            <Text style={{ fontSize: '16px', color: PALETTE.textInk, fontWeight: 700 }}>
              多模态智能诊疗室
            </Text>
            <Space>
              <Tooltip title={isEvidencePanelVisible ? "收起溯源面板" : "展开溯源面板"}>
                <Button type="text" icon={isEvidencePanelVisible ? <MenuFoldOutlined /> : <MenuUnfoldOutlined />} onClick={() => dispatch({ type: 'TOGGLE_EVIDENCE_PANEL' })} style={{ color: isEvidencePanelVisible ? PALETTE.tealDeep : PALETTE.textMute }} />
              </Tooltip>
            </Space>
          </div>

          <div className="advanced-scrollbar" style={{ flex: 1, padding: '32px 24px', overflowY: 'auto', display: 'flex', justifyContent: 'center' }}>
            <div style={{ width: '100%', maxWidth: '800px' }}>
              {isSwitching ? (
                <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%', minHeight: '300px' }}>
                  <Spin tip="正在同步时空记忆..." size="large" />
                </div>
              ) : (
                messages.map((msg, index) => (
                  <MemoizedMessage
                    key={index}
                    msg={msg}
                    index={index}
                    activeMessageIndex={activeMessageIndex}
                    recommendedQueries={recommendedQueries}
                    onSendMessage={sendMessage}
                    onViewTrace={handleViewTrace}
                    onOpenKbModal={(data) => { setKbModalData(data); setKbModalVisible(true); }}
                    isFinished={isFinished}
                    options={options}
                    selectedOptions={selectedOptions}
                    setSelectedOptions={setSelectedOptions}
                    messagesLength={messages.length}
                  />
                ))
              )}
              
              {/* 🌟 删除冗余 Spin：已有"症状追踪专家正在问诊..."步骤卡片做加载提示 */}
              <div ref={messagesEndRef} />
            </div>
          </div>

          {/* 底部输入区 —— 悬浮白卡 */}
          <div style={{ padding: '20px 40px 28px', background: 'transparent', display: 'flex', justifyContent: 'center' }}>
            <div
              className="chat-input-wrap"
              style={{
                width: '100%', maxWidth: '800px',
                background: PALETTE.glassThick,
                backdropFilter: 'blur(24px) saturate(160%)',
                WebkitBackdropFilter: 'blur(24px) saturate(160%)',
                borderRadius: '20px',
                padding: '14px 16px 10px',
                border: `1px solid ${PALETTE.hairline}`,
                boxShadow: inputText
                  ? '0 0 0 2px rgba(15,118,110,0.12), 0 16px 40px rgba(15,118,110,0.10), 0 2px 8px rgba(15,23,42,0.03)'
                  : '0 16px 40px rgba(15,118,110,0.08), 0 2px 8px rgba(15,23,42,0.03)',
                position: 'relative',
                transition: 'box-shadow 0.25s ease-out',
              }}
            >

              {/* 病历附件预览栈 */}
              {selectedImage && (
                <div style={{ marginBottom: '12px', paddingBottom: '10px', borderBottom: `1px dashed ${PALETTE.hairline}`, display: 'flex', alignItems: 'center', gap: '12px' }}>
                  <Text style={{ fontSize: '12px', color: PALETTE.textMute, fontWeight: 600 }}>📎 问诊附件</Text>
                  <div style={{ position: 'relative', display: 'inline-block' }}>
                    <Badge count={<CloseCircleFilled style={{ color: '#EF4444', fontSize: 16, cursor: 'pointer', background: '#fff', borderRadius: '50%' }} />} onClick={removeImage} offset={[-2, 2]}>
                      <div style={{ padding: '3px', background: '#fff', border: '1px solid #E2E8F0', borderRadius: '8px' }}>
                        <img src={selectedImage} alt="preview" style={{ height: 44, borderRadius: '4px', objectFit: 'cover' }} />
                      </div>
                    </Badge>
                  </div>
                </div>
              )}

              {/* 文本输入 */}
              <TextArea
                value={inputText}
                onChange={e => setInputText(e.target.value)}
                onPressEnter={e => { if (!e.shiftKey) { e.preventDefault(); sendMessage(inputText); } }}
                placeholder="详细描述您的症状，或询问特定药物禁忌（支持附带影像化验单）..."
                autoSize={{ minRows: 1, maxRows: 6 }}
                bordered={false}
                style={{
                  width: '100%', padding: '6px 4px',
                  fontSize: '15px', color: PALETTE.textInk,
                  resize: 'none', background: 'transparent',
                }}
              />

              {/* 底部工具栏：左侧工具 + 右侧字数 & 发送 */}
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: '6px' }}>
                <Space size={2}>
                  <Upload accept="image/*" showUploadList={false} beforeUpload={handleImageUpload}>
                    <Tooltip title="上传症状影像 / 化验单">
                      <Button type="text" icon={<PictureOutlined style={{ fontSize: '17px' }} />}
                        style={{ color: PALETTE.textMute, width: '32px', height: '32px', borderRadius: '8px', background: 'transparent' }} />
                    </Tooltip>
                  </Upload>
                  <Tooltip title="语音输入（即将上线）">
                    <Button type="text" disabled icon={<AudioOutlined style={{ fontSize: '17px' }} />}
                      style={{ color: '#CBD5E1', width: '32px', height: '32px', borderRadius: '8px', background: 'transparent' }} />
                  </Tooltip>
                  <Tooltip title="症状模板（即将上线）">
                    <Button type="text" disabled icon={<AppstoreOutlined style={{ fontSize: '17px' }} />}
                      style={{ color: '#CBD5E1', width: '32px', height: '32px', borderRadius: '8px', background: 'transparent' }} />
                  </Tooltip>
                </Space>

                <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                  <span style={{ fontSize: '11px', color: PALETTE.textMute, fontVariantNumeric: 'tabular-nums' }}>
                    {inputText.length > 0 ? `${inputText.length} 字` : 'Enter 发送 · Shift+Enter 换行'}
                  </span>
                  <Button
                    type="primary"
                    shape="circle"
                    icon={<SendOutlined />}
                    onClick={() => sendMessage(inputText)}
                    disabled={!inputText.trim() && !selectedImage}
                    style={{
                      width: '36px', height: '36px',
                      background: (!inputText.trim() && !selectedImage) ? 'rgba(148,163,184,0.14)' : PALETTE.tealDeep,
                      color: (!inputText.trim() && !selectedImage) ? '#CBD5E1' : '#FFF',
                      border: 'none',
                      boxShadow: (!inputText.trim() && !selectedImage)
                        ? 'none'
                        : '0 4px 12px rgba(20,184,166,0.35)',
                      transition: 'all 0.25s ease-out',
                    }}
                  />
                </div>
              </div>
            </div>
          </div>
        </div> {/* 🌟 修复：补回刚才被不小心覆盖掉的中部聊天区的闭合标签 */}

        {/* 3. 右侧依据面板 */}
        {isEvidencePanelVisible && (
          <div className="advanced-scrollbar" style={{ width: '420px', ...glassSurface, borderTop: 'none', borderRight: 'none', borderBottom: 'none', display: 'flex', flexDirection: 'column', padding: '24px', overflowY: 'auto' }}>
            <div style={{ display: 'flex', alignItems: 'center', marginBottom: '16px' }}>
              <BulbOutlined style={{ fontSize: 24, color: PALETTE.tealDeep, marginRight: 12 }} />
              <Title level={4} style={{ margin: 0, fontWeight: 800, color: PALETTE.textInk }}>可信溯源</Title>
            </div>

            {/* 🌟 等待大模型响应时的缓冲状态栏 */}
            {isCurrentLoading && (
               <div className="panel-anim" style={{ padding: '10px 14px', background: PALETTE.tealGhost, borderRadius: '10px', color: PALETTE.tealDeep, fontSize: '13px', display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '16px', border: `1px solid ${PALETTE.hairline}` }}>
                 <Spin size="small" className="custom-spin" /> <span style={{ fontWeight: 500 }}>正在为最新提问进行分析...</span>
               </div>
            )}

            {/* 🌟 如果是首轮等待，连旧数据都没有，直接上高级骨架屏 */}
            {!currentTraceData && isCurrentLoading ? (
              <div className="panel-anim" style={{ marginTop: '10px' }}>
                <Skeleton active paragraph={{ rows: 4 }} title={{ width: 120 }} />
                <Skeleton active paragraph={{ rows: 3 }} title={{ width: 150 }} style={{ marginTop: '30px' }} />
                <Skeleton active paragraph={{ rows: 5 }} title={{ width: 100 }} style={{ marginTop: '30px' }} />
              </div>
            ) : (
              <Collapse activeKey={expandedPanels} onChange={(keys) => dispatch({ type: 'SET_EXPANDED_PANELS', payload: keys })} ghost expandIconPosition="right">
                {panelTrace.hasAnyTrace ? (
                  <>
                    {renderTraceSummaryPanel()}
                    {renderAgentAuditPanel()}
                    {renderMilvusPanel()}
                    {renderKgPanel()}
                    {panelTrace.hasRumorPanel ? renderRumorPanel() : renderSupplementalPanel()}
                  </>
                ) : (
                  <Panel header={<Space><BulbOutlined />本轮溯源</Space>} key="summary" className="panel-anim" style={panelCardStyle}>
                    <EmptyTraceState text="该轮没有保存溯源数据。" />
                  </Panel>
                )}

              </Collapse>
            )}
          </div>
        )}
      </div>

      {/* 🌟 全局弹窗：专门用于展示本地知识库卡片的原文内容 */}
      <Modal
        title={
          <Space>
            <DatabaseOutlined style={{ color: PALETTE.tealDeep, fontSize: '18px' }} />
            <span style={{ color: PALETTE.textInk, fontWeight: 600 }}>证据摘要阅览</span>
          </Space>
        }
        open={kbModalVisible}
        onCancel={() => setKbModalVisible(false)}
        footer={[
          <Button key="close" type="primary" onClick={() => setKbModalVisible(false)} style={{ background: PALETTE.tealDeep, border: 'none', borderRadius: '8px' }}>
            我知道了
          </Button>
        ]}
        width={500}
        centered
        styles={{ body: { padding: '16px 0' } }}
      >
        <div style={{ padding: '0 8px' }}>
          <div style={{ fontSize: '16px', fontWeight: 600, color: PALETTE.textInk, marginBottom: '12px' }}>
            {kbModalData.title}
          </div>
          <Tag style={traceTagStyle('deep', { marginBottom: '16px' })}>{kbModalData.dept}</Tag>
          {getSummaryStatusLabel(kbModalData.status) && (
            <Tag style={traceTagStyle('moss', { marginBottom: '16px' })}>{getSummaryStatusLabel(kbModalData.status)}</Tag>
          )}
          {kbModalData.sourceFormat && (
            <Tag style={traceTagStyle('neutral', { marginBottom: '16px' })}>{kbModalData.sourceFormat}</Tag>
          )}
          {kbModalData.sectionType && (
            <Tag style={traceTagStyle('leaf', { marginBottom: '16px' })}>{kbModalData.sectionType}</Tag>
          )}
          {kbModalData.ocrUsed && (
            <Tag style={traceTagStyle('lime', { marginBottom: '16px' })}>OCR</Tag>
          )}
          {kbModalData.locatorLabel && (
            <Typography.Paragraph style={{ lineHeight: '1.6', fontSize: '12px', color: '#0F766E', background: '#ECFDF5', padding: '10px 12px', borderRadius: '8px', border: '1px solid #A7F3D0' }}>
              {kbModalData.locatorLabel}
            </Typography.Paragraph>
          )}
          <Typography.Paragraph style={{ lineHeight: '1.8', fontSize: '14px', color: PALETTE.textSlate, background: PALETTE.glassThick, padding: '16px', borderRadius: '10px', border: `1px solid ${PALETTE.hairline}` }}>
            {kbModalData.content}
          </Typography.Paragraph>
          {Array.isArray(kbModalData.keyPoints) && kbModalData.keyPoints.length > 0 && (
            <div style={{ marginBottom: '12px', padding: '12px', borderRadius: '8px', background: '#F0FDF4', border: '1px solid #BBF7D0' }}>
              {kbModalData.keyPoints.map((point, index) => (
                <div key={index} style={{ fontSize: '13px', color: '#166534', lineHeight: '1.8' }}>
                  {index + 1}. {point}
                </div>
              ))}
            </div>
          )}
          {kbModalData.takeaway && (
            <Typography.Paragraph style={{ lineHeight: '1.8', fontSize: '14px', color: '#166534', background: '#F0FDF4', padding: '14px', borderRadius: '8px', border: '1px solid #BBF7D0' }}>
              {kbModalData.takeaway}
            </Typography.Paragraph>
          )}
          {kbModalData.whyRelevant && (
            <Typography.Paragraph style={{ lineHeight: '1.8', fontSize: '13px', color: TRACE_TONE.deep, background: TRACE_TONE.soft, padding: '12px', borderRadius: '8px', border: `1px solid ${TRACE_TONE.border}` }}>
              {kbModalData.whyRelevant}
            </Typography.Paragraph>
          )}
          {kbModalData.evidenceLimit && (
            <Typography.Paragraph style={{ lineHeight: '1.8', fontSize: '13px', color: TRACE_TONE.moss, background: TRACE_TONE.warm, padding: '12px', borderRadius: '8px', border: `1px solid ${TRACE_TONE.border}` }}>
              {kbModalData.evidenceLimit}
            </Typography.Paragraph>
          )}
        </div>
      </Modal>

      <Modal
        title={
          <Space>
            <GlobalOutlined style={{ color: TRACE_TONE.deep, fontSize: '18px' }} />
            <span style={{ color: PALETTE.textInk, fontWeight: 600 }}>网络来源摘要</span>
          </Space>
        }
        open={scoutModalVisible}
        onCancel={() => setScoutModalVisible(false)}
        footer={[
          <Button key="close" onClick={() => setScoutModalVisible(false)} style={{ borderRadius: '8px' }}>
            关闭
          </Button>,
          <Button
            key="open"
            type="primary"
            disabled={!((scoutModalData?.open_url || scoutModalData?.url) && scoutModalData?.open_url_type !== 'none')}
            icon={<GlobalOutlined />}
            onClick={() => {
              const openUrl = scoutModalData?.open_url || scoutModalData?.url;
              if (openUrl && scoutModalData?.open_url_type !== 'none') {
                window.open(openUrl, '_blank', 'noopener,noreferrer');
              }
            }}
            style={{ background: TRACE_TONE.deep, border: 'none', borderRadius: '8px' }}
          >
            {scoutModalData?.open_url_type === 'post'
              ? '打开帖子'
              : scoutModalData?.open_url_type === 'web'
                ? '打开网页'
                : scoutModalData?.open_url_type === 'search'
                  ? '打开搜索结果'
                  : '无可打开来源'}
          </Button>
        ]}
        width={560}
        centered
        styles={{ body: { padding: '16px 0' } }}
      >
        <div style={{ padding: '0 8px' }}>
          <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', marginBottom: '12px' }}>
            {scoutModalData?.platform && <Tag style={traceTagStyle('deep')}>{scoutModalData.platform}</Tag>}
            {scoutModalData?.stance && <Tag style={traceTagStyle('leaf')}>{scoutModalData.stance}</Tag>}
            {scoutModalData?.evidence_type === 'social_opinion' && <Tag style={traceTagStyle('moss')}>舆情证据</Tag>}
            {getSummaryStatusLabel(scoutModalData?.llm_summary_status) && (
              <Tag style={traceTagStyle('lime')}>{getSummaryStatusLabel(scoutModalData?.llm_summary_status)}</Tag>
            )}
          </div>
          <div style={{ fontSize: '16px', fontWeight: 600, color: PALETTE.textInk, marginBottom: '12px', lineHeight: 1.5 }}>
            {scoutModalData?.display_title || scoutModalData?.title || '未命名来源'}
          </div>
          {(scoutModalData?.claim_relation || scoutModalData?.why_relevant) && (
            <Typography.Paragraph style={{ color: TRACE_TONE.deep, background: TRACE_TONE.soft, padding: '10px 12px', borderRadius: '8px', border: `1px solid ${TRACE_TONE.border}`, marginBottom: '12px' }}>
              {scoutModalData.claim_relation || scoutModalData.why_relevant}
            </Typography.Paragraph>
          )}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            <div>
              <Text strong style={{ color: PALETTE.textInk, fontSize: '13px' }}>内容概述</Text>
              <Typography.Paragraph style={{ marginTop: '6px', lineHeight: '1.8', fontSize: '14px', color: PALETTE.textSlate, background: PALETTE.glassThick, padding: '14px', borderRadius: '10px', border: `1px solid ${PALETTE.hairline}`, whiteSpace: 'pre-wrap' }}>
                {scoutModalData?.content_overview || scoutModalData?.summary || '该来源没有返回可展示摘要。'}
              </Typography.Paragraph>
            </div>
            <div>
              <Text strong style={{ color: PALETTE.textInk, fontSize: '13px' }}>从该来源可得出的结论</Text>
              <Typography.Paragraph style={{ marginTop: '6px', lineHeight: '1.8', fontSize: '14px', color: PALETTE.textSlate, background: PALETTE.glassThick, padding: '14px', borderRadius: '10px', border: `1px solid ${PALETTE.hairline}`, whiteSpace: 'pre-wrap' }}>
                {scoutModalData?.post_conclusion || '该来源只能作为网络线索，不能单独决定最终结论。'}
              </Typography.Paragraph>
            </div>
            {scoutModalData?.evidence_limit && (
              <div>
                <Text strong style={{ color: PALETTE.textInk, fontSize: '13px' }}>证据局限</Text>
                <Typography.Paragraph style={{ marginTop: '6px', lineHeight: '1.8', fontSize: '14px', color: TRACE_TONE.moss, background: TRACE_TONE.warm, padding: '14px', borderRadius: '8px', border: `1px solid ${TRACE_TONE.border}`, whiteSpace: 'pre-wrap' }}>
                  {scoutModalData.evidence_limit}
                </Typography.Paragraph>
              </div>
            )}
          </div>
        </div>
      </Modal>
    </>
  );
};

export default Chat;
