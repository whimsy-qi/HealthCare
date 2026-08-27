import React, { useState, useEffect, useRef, useReducer, useCallback } from 'react';
import { useNavigate } from 'react-router';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { toast } from 'sonner';
import {
  Plus, MessageSquare, BookOpen, User, LogOut,
  Send, Paperclip, X, ChevronRight, Lightbulb,
  Eye, ShieldCheck, Database, Globe, Bot, BrainCircuit,
  ChevronDown, ChevronUp, BarChart3, RefreshCw, Mic,
  ArrowRight, Image as ImageIcon, Stethoscope, Activity,
} from 'lucide-react';
import { MADDxLiveDebate } from '../components/MADDxLiveDebate';
import { RumorLiveDebate } from '../components/RumorLiveDebate';

// ─── Design Tokens ──────────────────────────────────────────────────
const T = {
  // ── Mint Green (Primary #afeebf) ──
  teal50:  '#edfaf2', teal100: '#d4f5df', teal200: '#afeebf',
  teal300: '#7bd49a', teal400: '#4eba78', teal500: '#32a05f',
  teal600: '#228048', teal700: '#166035', teal800: '#0d4224', teal900: '#061e10',
  // ── Warm Neutrals (hint of mint) ──
  slate50:  '#f4fbf6', slate100: '#edf5ef', slate200: '#d8ead9',
  slate300: '#b8ccba', slate400: '#90a892', slate500: '#637065',
  slate600: '#465049', slate700: '#313830', slate800: '#1e2420', slate900: '#0e120f',
  // ── Status ──
  red50: '#fef0f2', red500: '#e06870', red700: '#b84850',
  amber50: '#fef8e6', amber600: '#a88028',
  green50: '#edfaf2', green600: '#228048', green700: '#166035',
};

// Cream & accent palette (inline use)
const C = {
  cream50:  '#fefdf5', cream100: '#faf6e6', cream200: '#f0eac1',  // ← #f0eac1
  cream300: '#e4d68a', cream400: '#ccb85a', cream600: '#7a6c28',
  sky100: '#ddf1fb', sky200: '#b8dff0', sky500: '#5aaad4',        // sky blue
  lav100: '#ece8f8', lav200: '#d4cff5', lav500: '#8878c8',        // lavender
  peach100: '#fdeee2', peach200: '#f5ceb0', peach500: '#d8865a',  // peach
};

// ─── Types ──────────────────────────────────────────────────────────
interface Message {
  role: 'user' | 'ai';
  content: string;
  image?: string;
  isThinking?: boolean;
  thinkingStatus?: string;
  maddxEvents?: unknown[];
  rumorEvents?: unknown[];
  meta_data?: {
    route?: string;
    trace_data?: Record<string, unknown>;
    turn_count?: number;
    current_slots?: Record<string, unknown>;
    is_finished?: boolean;
    options?: string[];
  };
}

interface Session { id: number; title: string; date?: string; }

interface ChatState {
  turnCount: number;
  currentSlots: Record<string, unknown>;
  currentRoute: string;
  options: string[];
  isFinished: boolean;
  currentTraceData: Record<string, unknown> | null;
  activeMessageIndex: number;
  isEvidencePanelVisible: boolean;
  traceStep: number;
  expandedPanels: string[];
}

const initialChatState: ChatState = {
  turnCount: 1, currentSlots: {}, currentRoute: '',
  options: [], isFinished: true, currentTraceData: null,
  activeMessageIndex: -1, isEvidencePanelVisible: false,
  traceStep: 0, expandedPanels: [],
};

function chatReducer(state: ChatState, action: { type: string; payload?: unknown }): ChatState {
  switch (action.type) {
    case 'SET_RESPONSE': {
      const p = action.payload as Partial<ChatState> & { turn_count?: number; route?: string; trace_data?: unknown; messageIndex?: number; is_finished?: boolean };
      return {
        ...state,
        turnCount: p.turn_count ?? state.turnCount,
        currentSlots: (p.currentSlots as Record<string, unknown>) ?? state.currentSlots,
        currentRoute: p.route ?? state.currentRoute,
        options: (p.options as string[]) ?? [],
        isFinished: p.is_finished ?? true,
        currentTraceData: (p.trace_data as Record<string, unknown>) ?? null,
        traceStep: 0,
        isEvidencePanelVisible: p.route !== 'CHITCHAT_OR_REJECT',
        activeMessageIndex: (p.messageIndex as number) ?? -1,
      };
    }
    case 'RESTORE_FROM_HISTORY': {
      const p = action.payload as { route?: string; trace_data?: unknown; options?: string[]; current_slots?: Record<string, unknown>; turn_count?: number; is_finished?: boolean; lastAiIndex?: number };
      return {
        ...state,
        currentRoute: p.route ?? '',
        currentTraceData: (p.trace_data as Record<string, unknown>) ?? null,
        options: p.options ?? [],
        currentSlots: p.current_slots ?? {},
        turnCount: p.turn_count ?? 1,
        isFinished: p.is_finished ?? true,
        isEvidencePanelVisible: !!(p.route && p.route !== 'CHITCHAT_OR_REJECT'),
        activeMessageIndex: p.lastAiIndex ?? -1,
      };
    }
    case 'RESET_SESSION': return { ...initialChatState };
    case 'SET_ACTIVE_MESSAGE': return { ...state, activeMessageIndex: action.payload as number };
    case 'TOGGLE_EVIDENCE_PANEL': return { ...state, isEvidencePanelVisible: !state.isEvidencePanelVisible };
    case 'SET_TRACE_STEP': return { ...state, traceStep: action.payload as number };
    case 'SET_EXPANDED_PANELS': return { ...state, expandedPanels: action.payload as string[] };
    default: return state;
  }
}

const defaultGreeting: Message = {
  role: 'ai',
  content: '你好 ✨\n\n我是你的专属全科数字医生，随时为你服务。\n\n有什么症状描述、体检报告，或想求证的健康知识，随时告诉我吧！',
};

const getBase64 = (file: File): Promise<string> =>
  new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.readAsDataURL(file);
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = reject;
  });

// ─── Evidence Section ────────────────────────────────────────────────
const EvidenceSection: React.FC<{ title: string; icon: React.ReactNode; children: React.ReactNode; color?: string }> =
  ({ title, icon, children, color = T.teal600 }) => {
  const [open, setOpen] = useState(true);
  return (
    <div style={{ marginBottom: 12, border: `1px solid ${T.slate200}`, borderRadius: 10, overflow: 'hidden' }}>
      <button onClick={() => setOpen(!open)} style={{
        width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '10px 14px', background: T.slate50, border: 'none', cursor: 'pointer',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ color }}>{icon}</span>
          <span style={{ fontSize: 12, fontWeight: 700, color: T.slate700, textTransform: 'uppercase', letterSpacing: '0.4px' }}>{title}</span>
        </div>
        {open ? <ChevronUp size={14} color={T.slate400} /> : <ChevronDown size={14} color={T.slate400} />}
      </button>
      {open && <div style={{ padding: '12px 14px', background: 'white' }}>{children}</div>}
    </div>
  );
};

// ─── Sidebar Nav Item ────────────────────────────────────────────────
const NavItem: React.FC<{ icon: React.ReactNode; label: string; active?: boolean; danger?: boolean; onClick?: () => void }> =
  ({ icon, label, active, danger, onClick }) => (
  <button onClick={onClick} title={label} style={{
    width: '100%', display: 'flex', alignItems: 'center', gap: 10, padding: '10px 12px',
    borderRadius: 8, border: 'none', cursor: 'pointer', textAlign: 'left',
    background: active ? 'rgba(175,238,191,0.3)' : 'transparent',
    color: danger ? T.red500 : (active ? T.teal600 : T.slate600),
    transition: 'all 0.15s',
  }}
    onMouseEnter={e => { if (!active) (e.currentTarget as HTMLButtonElement).style.background = danger ? 'rgba(224,104,112,0.1)' : 'rgba(175,238,191,0.18)'; (e.currentTarget as HTMLButtonElement).style.color = danger ? T.red500 : (active ? T.teal600 : T.slate700); }}
    onMouseLeave={e => { (e.currentTarget as HTMLButtonElement).style.background = active ? 'rgba(175,238,191,0.3)' : 'transparent'; (e.currentTarget as HTMLButtonElement).style.color = danger ? T.red500 : (active ? T.teal600 : T.slate600); }}
  >
    {icon}
    <span style={{ fontSize: 13, fontWeight: active ? 600 : 400 }}>{label}</span>
  </button>
);

// ─── Main Component ──────────────────────────────────────────────────
export const ChatPage: React.FC = () => {
  const navigate = useNavigate();

  const [sessionList, setSessionList] = useState<Session[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<number | null>(null);
  const activeSessionRef = useRef<number | null>(null);
  useEffect(() => { activeSessionRef.current = activeSessionId; }, [activeSessionId]);

  const [messages, setMessages] = useState<Message[]>([]);
  const [inputText, setInputText] = useState('');
  const [loadingMap, setLoadingMap] = useState<Record<number, boolean>>({});
  const isCurrentLoading = activeSessionId ? (loadingMap[activeSessionId] ?? false) : false;
  const [isSwitching, setIsSwitching] = useState(false);
  const [selectedImage, setSelectedImage] = useState<string | null>(null);

  const [chatState, dispatch] = useReducer(chatReducer, initialChatState);
  const { options, isFinished, currentTraceData, activeMessageIndex, isEvidencePanelVisible, traceStep } = chatState;

  const [selectedOptions, setSelectedOptions] = useState<string[]>([]);
  const [recommendedQueries, setRecommendedQueries] = useState<string[]>([]);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // ── Init sessions ──
  useEffect(() => {
    const init = async () => {
      const token = localStorage.getItem('access_token');
      if (!token) { navigate('/login'); return; }
      try {
        const res = await fetch('http://localhost:8000/api/sessions', { headers: { 'Authorization': `Bearer ${token}` } });
        if (res.status === 401) throw new Error('Unauthorized');
        const data = await res.json();
        if (data.length > 0) { setSessionList(data); setActiveSessionId(data[0].id); }
        else { await createNewSession(); }
      } catch {
        toast.error('登录失效，请重新登录'); navigate('/login');
      }
    };
    init();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Load messages ──
  useEffect(() => {
    if (!activeSessionId) return;
    const load = async () => {
      setIsSwitching(true);
      const token = localStorage.getItem('access_token');
      try {
        const res = await fetch(`http://localhost:8000/api/sessions/${activeSessionId}/messages`, { headers: { 'Authorization': `Bearer ${token}` } });
        const history = await res.json();
        if (history.length === 0) {
          setMessages([defaultGreeting]);
          fetch('http://localhost:8000/api/recommend_queries').then(r => r.json()).then(d => { if (d.status === 'success') setRecommendedQueries(d.queries); }).catch(() => {});
        } else {
          const normalized = history.map((m: Message) => ({ ...m, image: m.image?.startsWith('/static/') ? `http://localhost:8000${m.image}` : m.image }));
          setMessages(normalized);
        }
        setSelectedOptions([]);
        let lastAiIndex = -1;
        for (let i = history.length - 1; i >= 0; i--) {
          const m = history[i];
          if (m.role === 'ai') {
            lastAiIndex = i;
            if (m.meta_data) dispatch({ type: 'RESTORE_FROM_HISTORY', payload: { ...m.meta_data, lastAiIndex } });
            else dispatch({ type: 'RESET_SESSION' });
            break;
          }
        }
        if (lastAiIndex === -1) dispatch({ type: 'RESET_SESSION' });
      } catch { /* ignore */ }
      finally { setIsSwitching(false); }
    };
    load();
  }, [activeSessionId]);

  useEffect(() => { messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages, options]);

  const createNewSession = useCallback(async () => {
    const token = localStorage.getItem('access_token');
    try {
      const res = await fetch('http://localhost:8000/api/sessions', { method: 'POST', headers: { 'Authorization': `Bearer ${token}` } });
      const newSession = await res.json();
      setSessionList(prev => [{ id: newSession.id, title: newSession.title, date: '刚刚' }, ...prev]);
      setActiveSessionId(newSession.id);
    } catch { toast.error('新建对话失败'); }
  }, []);

  const handleViewTrace = (index: number, msg: Message) => {
    dispatch({ type: 'SET_ACTIVE_MESSAGE', payload: index });
    dispatch({ type: 'RESTORE_FROM_HISTORY', payload: { route: msg.meta_data?.route ?? '', trace_data: msg.meta_data?.trace_data ?? null, options: [], current_slots: {}, turn_count: 1, is_finished: true, lastAiIndex: index } });
    dispatch({ type: 'SET_TRACE_STEP', payload: 0 });
    setTimeout(() => dispatch({ type: 'SET_TRACE_STEP', payload: 1 }), 50);
  };

  const sendMessage = async (textToSend?: string) => {
    const finalQuery = (textToSend || inputText).trim() || (selectedImage ? '请帮我解读这份医疗图片' : '');
    if (!finalQuery || !activeSessionId) return;
    const token = localStorage.getItem('access_token');
    if (!token) { toast.warning('请先登录'); navigate('/login'); return; }

    let imageUrlForMsg = selectedImage;
    let imageUrlForBackend: string | null = null;
    if (selectedImage) {
      try {
        const uploadRes = await fetch('http://localhost:8000/api/upload_image', { method: 'POST', headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` }, body: JSON.stringify({ image_base64: selectedImage, session_id: activeSessionId ? parseInt(String(activeSessionId)) : null }) });
        if (uploadRes.ok) {
          const d = await uploadRes.json();
          imageUrlForBackend = d.file_id || d.storage_key || d.url;
          imageUrlForMsg = d.url?.startsWith('http') ? d.url : (d.url ? `http://localhost:8000${d.url}` : selectedImage);
        }
      } catch { /* ignore */ }
    }

    const newMessages: Message[] = [...messages, { role: 'user', content: finalQuery, image: imageUrlForMsg ?? undefined }];
    setMessages(newMessages);
    setInputText(''); setSelectedImage(null);
    dispatch({ type: 'SET_EXPANDED_PANELS', payload: [] });
    setSelectedOptions([]);
    const sessionIdToLock = activeSessionId;
    setLoadingMap(prev => ({ ...prev, [sessionIdToLock]: true }));
    setRecommendedQueries([]);

    let carriedVisionContext: unknown = null;
    let carriedMedPrecheck: unknown = null;
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i];
      if (m.role === 'ai' && m.meta_data?.trace_data) {
        if (!carriedVisionContext && (m.meta_data.trace_data as Record<string, unknown>).vision_insights) carriedVisionContext = (m.meta_data.trace_data as Record<string, unknown>).vision_insights;
        if (!carriedMedPrecheck && (m.meta_data.trace_data as Record<string, unknown>).med_precheck) carriedMedPrecheck = (m.meta_data.trace_data as Record<string, unknown>).med_precheck;
      }
      if (carriedVisionContext && carriedMedPrecheck) break;
    }

    const payload = {
      session_id: parseInt(String(activeSessionId)),
      query: finalQuery,
      messages_history: newMessages.slice(0, -1).filter(m => m.content !== defaultGreeting.content).map(m => ({ role: m.role === 'ai' ? 'assistant' : 'user', content: m.content })),
      turn_count: chatState.turnCount, current_slots: chatState.currentSlots,
      current_route: chatState.currentRoute,
      image_data: imageUrlForBackend || newMessages[newMessages.length - 1].image,
      vision_context: carriedVisionContext, med_precheck: carriedMedPrecheck,
    };

    setMessages(prev => [...prev, { role: 'ai', isThinking: true, thinkingStatus: '🤔 正在思考...', content: '' }]);

    try {
      const response = await fetch('http://localhost:8000/api/chat', {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` }, body: JSON.stringify(payload),
      });
      if (response.status === 401) { toast.error('登录状态失效，请重新登录'); localStorage.removeItem('access_token'); navigate('/login'); return; }
      if (response.status === 409) {
        const payload = await response.json().catch(() => ({}));
        toast.warning(payload.detail || '该会话正在生成中，请稍后再试');
        setMessages(prev => prev.filter(m => !m.isThinking));
        return;
      }
      if (!response.ok) throw new Error('网络响应异常');

      const reader = response.body!.getReader();
      const decoder = new TextDecoder();
      let sseBuffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        sseBuffer += decoder.decode(value, { stream: true });
        const parts = sseBuffer.split('\n\n');
        sseBuffer = parts.pop() ?? '';

        for (const part of parts) {
          const line = part.trim();
          if (!line.startsWith('data: ')) continue;
          let eventData: Record<string, unknown>;
          try { eventData = JSON.parse(line.slice(6)); } catch { continue; }

          if (eventData.type === 'status') {
            if (activeSessionRef.current === sessionIdToLock) {
              setMessages(prev => {
                const u = [...prev];
                u[u.length - 1] = { ...u[u.length - 1], thinkingStatus: eventData.message as string };
                return u;
              });
            }
          } else if (eventData.type === 'maddx_step') {
            if (activeSessionRef.current === sessionIdToLock) {
              setMessages(prev => {
                const u = [...prev];
                const last = u[u.length - 1];
                u[u.length - 1] = { ...last, maddxEvents: [...(last.maddxEvents ?? []), eventData] };
                return u;
              });
            }
          } else if (eventData.type === 'rumor_step') {
            if (activeSessionRef.current === sessionIdToLock) {
              setMessages(prev => {
                const u = [...prev];
                const last = u[u.length - 1];
                u[u.length - 1] = { ...last, rumorEvents: [...(last.rumorEvents ?? []), eventData] };
                return u;
              });
            }
          } else if (eventData.type === 'done') {
            if (activeSessionRef.current === sessionIdToLock) {
              const finalMsg: Message = {
                role: 'ai', content: ((eventData.answer ?? eventData.content) as string) ?? '',
                meta_data: {
                  route: eventData.route as string,
                  trace_data: eventData.trace_data as Record<string, unknown>,
                  turn_count: eventData.turn_count as number,
                  current_slots: eventData.current_slots as Record<string, unknown>,
                  is_finished: eventData.is_finished as boolean,
                  options: eventData.options as string[],
                  run_id: eventData.run_id as string,
                  state_version: eventData.state_version as number,
                },
              };
              setMessages(prev => {
                const updatedMessages = [...prev.slice(0, -1), finalMsg];
                dispatch({ type: 'SET_RESPONSE', payload: { ...eventData, messageIndex: updatedMessages.length - 1 } });
                return updatedMessages;
              });
            }
            fetch('http://localhost:8000/api/sessions', { headers: { 'Authorization': `Bearer ${token}` } }).then(r => r.json()).then(sessions => setSessionList(sessions)).catch(() => {});
          } else if (eventData.type === 'error') {
            setMessages(prev => [...prev.slice(0, -1), { role: 'ai', content: `❌ ${(eventData.message as string) ?? '服务暂时不可用'}` }]);
          }
        }
      }
    } catch {
      setMessages(prev => {
        const last = prev[prev.length - 1];
        const without = last?.isThinking ? prev.slice(0, -1) : prev;
        return [...without, { role: 'ai', content: '❌ 网络请求失败，请确保后端服务已启动。' }];
      });
    } finally {
      setLoadingMap(prev => ({ ...prev, [sessionIdToLock]: false }));
    }
  };

  const handleImageUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (!['image/jpeg', 'image/png'].includes(file.type)) { toast.error('仅支持 JPG/PNG 格式'); return; }
    if (file.size > 5 * 1024 * 1024) { toast.error('图片须小于 5MB'); return; }
    setSelectedImage(await getBase64(file));
    e.target.value = '';
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  };

  const traceData = currentTraceData as {
    kb_results?: unknown; search_results?: unknown; med_precheck?: { kg_warnings?: string; manual_summary?: string };
    vision_insights?: string; final_answer?: string; route_reason?: string;
    maddx_events?: unknown[]; rumor_events?: unknown[];
  } | null;

  const routeLabel: Record<string, string> = {
    'DIAGNOSIS': '诊断路由',  'DRUG_QUERY': '用药路由',  'KNOWLEDGE': '知识路由',
    'RUMOR': '辟谣路由',      'CHITCHAT_OR_REJECT': '闲聊', 'IMAGE_ANALYSIS': '影像路由',
  };

  return (
    <>
      <style>{`
        .chat-scroll::-webkit-scrollbar { width: 4px; }
        .chat-scroll::-webkit-scrollbar-track { background: transparent; }
        .chat-scroll::-webkit-scrollbar-thumb { background: rgba(148,163,184,0.3); border-radius: 10px; }
        .chat-scroll::-webkit-scrollbar-thumb:hover { background: rgba(50,160,95,0.45); }
        @keyframes spin360 { to { transform: rotate(360deg); } }
        @keyframes thinkPulse { 0%,60%,100% { transform:scale(0.7); opacity:0.4; } 30% { transform:scale(1.3); opacity:1; } }
        @keyframes statusFade { from { opacity:0; transform:translateY(4px); } to { opacity:1; transform:translateY(0); } }
        .think-dot { width:7px; height:7px; border-radius:50%; background:${T.teal500}; display:inline-block; animation:thinkPulse 1.4s ease-in-out infinite; }
        .status-txt { animation:statusFade 0.35s ease-out forwards; }
        .session-item { padding:10px 14px; border-radius:8px; cursor:pointer; transition:background 0.15s; border:1px solid transparent; }
        .session-item:hover { background:rgba(175,238,191,0.18); }
        .session-active { background:rgba(175,238,191,0.38) !important; border-color:rgba(123,212,154,0.5) !important; }
        .chat-input-area { border:none !important; outline:none !important; resize:none; background:transparent; }
        .chat-input-area:focus { outline:none !important; box-shadow:none !important; }
        .md-body p { margin:0 0 8px; line-height:1.75; }
        .md-body p:last-child { margin-bottom:0; }
        .md-body ul,.md-body ol { margin:6px 0; padding-left:22px; }
        .md-body li { margin:3px 0; }
        .md-body strong { color:${T.teal700}; font-weight:700; }
        .md-body h1,.md-body h2,.md-body h3,.md-body h4 { margin:12px 0 6px; color:${T.slate900}; }
        .md-body code { background:${T.slate100}; color:${T.slate800}; padding:1px 5px; border-radius:4px; font-size:13px; }
        .md-body pre { background:${T.slate800}; color:#E2E8F0; padding:12px 16px; border-radius:8px; overflow-x:auto; }
        .md-body blockquote { margin:8px 0; padding:4px 12px; border-left:3px solid ${T.teal400}; background:${T.teal50}; color:${T.slate600}; border-radius:0 6px 6px 0; }
        .md-body table { width:100%; border-collapse:collapse; margin:12px 0; }
        .md-body th { background:${T.teal50}; padding:8px 12px; text-align:left; border-bottom:2px solid ${T.teal200}; font-size:13px; color:${T.slate800}; }
        .md-body td { padding:8px 12px; border-bottom:1px solid ${T.slate200}; font-size:13px; color:${T.slate700}; }
        .md-body tr:nth-child(even) { background:${T.slate50}; }
        .option-btn { padding:8px 16px; border-radius:20px; border:1.5px solid ${T.teal400}; background:transparent; color:${T.teal600}; cursor:pointer; font-size:13px; font-weight:600; transition:all 0.2s; }
        .option-btn:hover,.option-btn.selected { background:${T.teal500}; color:white; border-color:${T.teal500}; }
      `}</style>

      <div style={{ display: 'flex', height: '100vh', width: '100vw', overflow: 'hidden', background: '#f4fbf6' }}>

        {/* ══ Left Sidebar ══ */}
        <div style={{
          width: 252, flexShrink: 0, display: 'flex', flexDirection: 'column',
          background: '#e8f7ee', borderRight: `1px solid #c4e8d0`,
          padding: '20px 12px',
        }}>
          {/* Brand */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '4px 8px', marginBottom: 20 }}>
            <div style={{ width: 32, height: 32, borderRadius: 8, background: `linear-gradient(135deg, ${T.teal400}, ${T.teal600})`, display: 'flex', alignItems: 'center', justifyContent: 'center', boxShadow: '0 4px 10px rgba(50,160,95,0.25)' }}>
              <Activity size={16} color="white" />
            </div>
            <div>
              <div style={{ color: T.slate800, fontSize: 14, fontWeight: 700 }}>TrustMed AI</div>
              <div style={{ color: T.slate500, fontSize: 10, letterSpacing: '0.5px' }}>多智能体医疗系统</div>
            </div>
          </div>

          {/* New Chat */}
          <button onClick={createNewSession} style={{
            width: '100%', height: 40, borderRadius: 8,
            background: `linear-gradient(135deg, ${T.teal400}, ${T.teal600})`,
            color: 'white', border: 'none', cursor: 'pointer',
            display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8,
            fontSize: 13, fontWeight: 600, marginBottom: 16,
            boxShadow: '0 4px 12px rgba(50,160,95,0.28)',
          }}>
            <Plus size={15} /> 新建对话
          </button>

          {/* Sessions */}
          <div style={{ flex: 1, overflowY: 'auto', marginBottom: 8 }} className="chat-scroll">
            <div style={{ fontSize: 10, fontWeight: 700, color: T.slate400, letterSpacing: '0.8px', textTransform: 'uppercase', padding: '4px 8px', marginBottom: 6 }}>
              历史对话
            </div>
            {sessionList.map(s => (
              <div key={s.id}
                className={`session-item${activeSessionId === s.id ? ' session-active' : ''}`}
                onClick={() => setActiveSessionId(s.id)}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <MessageSquare size={13} color={activeSessionId === s.id ? T.teal500 : T.slate400} style={{ flexShrink: 0 }} />
                  <span style={{
                    fontSize: 13, color: activeSessionId === s.id ? T.teal700 : T.slate600,
                    fontWeight: activeSessionId === s.id ? 600 : 400,
                    whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                  }}>{s.title}</span>
                </div>
              </div>
            ))}
          </div>

          {/* Bottom Nav */}
          <div style={{ borderTop: `1px solid ${T.teal200}`, paddingTop: 12, display: 'flex', flexDirection: 'column', gap: 2 }}>
            <NavItem icon={<BookOpen size={16} />} label="健康知识专区" onClick={() => navigate('/knowledge')} />
            <NavItem icon={<User size={16} />} label="我的健康档案" onClick={() => navigate('/profile')} />
            <NavItem icon={<LogOut size={16} />} label="退出登录" danger onClick={() => { localStorage.removeItem('access_token'); localStorage.removeItem('current_username'); navigate('/login'); }} />
          </div>
        </div>

        {/* ══ Main Chat Area ══ */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', background: '#fafdf8', minWidth: 0 }}>
          {/* Header */}
          <div style={{ height: 60, borderBottom: `1px solid ${T.slate200}`, display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 24px', background: 'rgba(255,255,255,0.9)', backdropFilter: 'blur(10px)', flexShrink: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <div style={{ width: 36, height: 36, borderRadius: '50%', background: `linear-gradient(135deg, ${T.teal500}, ${T.teal700})`, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <Bot size={18} color="white" />
              </div>
              <div>
                <div style={{ fontSize: 14, fontWeight: 700, color: T.slate900 }}>AI 全科医生</div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                  <div style={{ width: 6, height: 6, borderRadius: '50%', background: '#22C55E' }} />
                  <span style={{ fontSize: 11, color: T.slate500 }}>在线 · 多智能体就绪</span>
                </div>
              </div>
            </div>
            {isEvidencePanelVisible && (
              <button onClick={() => dispatch({ type: 'TOGGLE_EVIDENCE_PANEL' })} style={{ padding: '6px 14px', borderRadius: 8, border: `1.5px solid ${T.teal500}`, background: T.teal50, color: T.teal600, cursor: 'pointer', fontSize: 12, fontWeight: 700, display: 'flex', alignItems: 'center', gap: 6 }}>
                <Lightbulb size={13} /> 查看溯源依据
              </button>
            )}
            <button
              onClick={() => navigate('/app')}
              style={{
                display: 'flex', alignItems: 'center', gap: 6,
                padding: '7px 13px', borderRadius: 20,
                border: `1.5px solid ${T.teal200}`,
                background: T.teal50,
                color: T.teal600,
                cursor: 'pointer', fontSize: 12, fontWeight: 700,
                transition: 'all 0.18s',
                flexShrink: 0,
              }}
              onMouseEnter={e => { (e.currentTarget as HTMLButtonElement).style.background = T.teal200; (e.currentTarget as HTMLButtonElement).style.borderColor = T.teal400; }}
              onMouseLeave={e => { (e.currentTarget as HTMLButtonElement).style.background = T.teal50; (e.currentTarget as HTMLButtonElement).style.borderColor = T.teal200; }}
            >
              📱 移动版
            </button>
          </div>

          {/* Messages */}
          <div style={{ flex: 1, overflowY: 'auto', padding: '24px', display: 'flex', flexDirection: 'column' }} className="chat-scroll">
            {isSwitching ? (
              <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column', gap: 14 }}>
                <div style={{ width: 28, height: 28, border: `3px solid ${T.slate200}`, borderTopColor: T.teal500, borderRadius: '50%', animation: 'spin360 0.8s linear infinite' }} />
                <span style={{ color: T.slate400, fontSize: 13 }}>切换会话中…</span>
              </div>
            ) : (
              <>
                {messages.map((msg, idx) => (
                  <MessageBubble
                    key={idx} msg={msg} index={idx} messagesLength={messages.length}
                    activeMessageIndex={activeMessageIndex}
                    recommendedQueries={recommendedQueries}
                    onSendMessage={sendMessage} onViewTrace={handleViewTrace}
                    isFinished={isFinished} options={options}
                    selectedOptions={selectedOptions} setSelectedOptions={setSelectedOptions}
                  />
                ))}
                <div ref={messagesEndRef} />
              </>
            )}
          </div>

          {/* Input Bar */}
          <div style={{ padding: '16px 24px 20px', background: 'rgba(255,255,255,0.92)', backdropFilter: 'blur(10px)', borderTop: `1px solid ${T.slate200}`, flexShrink: 0 }}>
            {selectedImage && (
              <div style={{ marginBottom: 10, display: 'flex', alignItems: 'center', gap: 8 }}>
                <div style={{ position: 'relative', display: 'inline-block' }}>
                  <img src={selectedImage} alt="preview" style={{ height: 60, width: 80, objectFit: 'cover', borderRadius: 8, border: `1px solid ${T.slate200}` }} />
                  <button onClick={() => setSelectedImage(null)} style={{ position: 'absolute', top: -6, right: -6, width: 18, height: 18, borderRadius: '50%', background: T.red500, border: 'none', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'white' }}>
                    <X size={10} />
                  </button>
                </div>
                <span style={{ fontSize: 12, color: T.slate500 }}>图片已选择</span>
              </div>
            )}
            <div style={{ display: 'flex', alignItems: 'flex-end', gap: 10, padding: '12px 16px', background: T.slate50, borderRadius: 14, border: `1.5px solid ${T.slate200}`, transition: 'border-color 0.2s' }}
              onFocus={() => {}} // handled by inner textarea
            >
              {/* Image Upload */}
              <input type="file" accept="image/*" ref={fileInputRef} onChange={handleImageUpload} style={{ display: 'none' }} />
              <button onClick={() => fileInputRef.current?.click()} title="上传图片" style={{ background: 'none', border: 'none', cursor: 'pointer', color: T.slate400, padding: '4px', display: 'flex', alignItems: 'center', borderRadius: 6, flexShrink: 0 }}
                onMouseEnter={e => (e.currentTarget as HTMLButtonElement).style.color = T.teal500}
                onMouseLeave={e => (e.currentTarget as HTMLButtonElement).style.color = T.slate400}
              >
                <ImageIcon size={18} />
              </button>

              {/* Textarea */}
              <textarea
                ref={textareaRef}
                className="chat-input-area"
                value={inputText}
                onChange={e => setInputText(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="描述症状，或上传体检报告图片… (Enter 发送)"
                rows={1}
                style={{
                  flex: 1, fontSize: 14, color: T.slate900,
                  lineHeight: 1.6, maxHeight: 120, overflowY: 'auto',
                  fontFamily: 'inherit',
                }}
              />

              {/* Send */}
              <button
                onClick={() => sendMessage()}
                disabled={isCurrentLoading || (!inputText.trim() && !selectedImage)}
                style={{
                  width: 38, height: 38, borderRadius: 10, flexShrink: 0,
                  background: (isCurrentLoading || (!inputText.trim() && !selectedImage)) ? T.slate200 : `linear-gradient(135deg, ${T.teal500}, ${T.teal700})`,
                  color: (isCurrentLoading || (!inputText.trim() && !selectedImage)) ? T.slate400 : 'white',
                  border: 'none', cursor: (isCurrentLoading || (!inputText.trim() && !selectedImage)) ? 'not-allowed' : 'pointer',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  boxShadow: (isCurrentLoading || (!inputText.trim() && !selectedImage)) ? 'none' : '0 4px 12px rgba(101,163,13,0.3)',
                  transition: 'all 0.2s',
                }}
              >
                {isCurrentLoading
                  ? <div style={{ width: 14, height: 14, border: `2px solid ${T.slate400}`, borderTopColor: 'transparent', borderRadius: '50%', animation: 'spin360 0.8s linear infinite' }} />
                  : <Send size={16} />
                }
              </button>
            </div>
            <div style={{ textAlign: 'center', marginTop: 8 }}>
              <span style={{ fontSize: 11, color: T.slate400 }}>AI 建议仅供参考，不能替代专业医生的诊断与治疗</span>
            </div>
          </div>
        </div>

        {/* ══ Right Evidence Panel ══ */}
        {isEvidencePanelVisible && traceData && traceStep > 0 && (
          <div style={{
            width: 340, flexShrink: 0, display: 'flex', flexDirection: 'column',
            background: 'white', borderLeft: `1px solid ${T.slate200}`,
            animation: 'slideInRight 0.4s ease-out',
          }}>
            <style>{`@keyframes slideInRight { from { opacity:0; transform:translateX(20px); } to { opacity:1; transform:translateX(0); } }`}</style>
            <div style={{ padding: '16px 20px', borderBottom: `1px solid ${T.slate200}`, display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <BrainCircuit size={16} color={T.teal600} />
                <span style={{ fontSize: 13, fontWeight: 700, color: T.slate800 }}>多智能体溯源链路</span>
              </div>
              <button onClick={() => dispatch({ type: 'TOGGLE_EVIDENCE_PANEL' })} style={{ background: 'none', border: 'none', cursor: 'pointer', color: T.slate400, display: 'flex' }}>
                <X size={16} />
              </button>
            </div>

            <div style={{ flex: 1, overflowY: 'auto', padding: '16px 16px' }} className="chat-scroll">
              {/* Route Badge */}
              {chatState.currentRoute && (
                <div style={{ marginBottom: 14, padding: '8px 12px', background: T.teal50, border: `1px solid ${T.teal200}`, borderRadius: 8, display: 'flex', alignItems: 'center', gap: 8 }}>
                  <ArrowRight size={13} color={T.teal600} />
                  <span style={{ fontSize: 12, fontWeight: 700, color: T.teal700 }}>
                    {routeLabel[chatState.currentRoute] || chatState.currentRoute}
                  </span>
                </div>
              )}

              {/* MADDx Debate */}
              {Array.isArray(traceData.maddx_events) && traceData.maddx_events.length > 0 && (
                <EvidenceSection title="多智能体辩论" icon={<BrainCircuit size={13} />} color={T.teal600}>
                  <MADDxLiveDebate events={traceData.maddx_events as never[]} isLive={false} />
                </EvidenceSection>
              )}

              {/* Rumor Debate */}
              {Array.isArray(traceData.rumor_events) && traceData.rumor_events.length > 0 && (
                <EvidenceSection title="辟谣加权辩论" icon={<ShieldCheck size={13} />} color={T.red700}>
                  <RumorLiveDebate events={traceData.rumor_events as never[]} isLive={false} />
                </EvidenceSection>
              )}

              {/* Vision */}
              {traceData.vision_insights && (
                <EvidenceSection title="影像特征提取" icon={<Eye size={13} />} color={T.green700}>
                  <div style={{ fontSize: 12, color: T.slate600, lineHeight: 1.7 }}>
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{traceData.vision_insights as string}</ReactMarkdown>
                  </div>
                </EvidenceSection>
              )}

              {/* Med Precheck */}
              {traceData.med_precheck && (
                <EvidenceSection title="用药红线核查" icon={<Stethoscope size={13} />} color={T.amber600}>
                  <div style={{ fontSize: 12, lineHeight: 1.6 }}>
                    {traceData.med_precheck.kg_warnings && (
                      <div style={{ color: T.red700, fontWeight: 600, marginBottom: 6 }}>⚠ {traceData.med_precheck.kg_warnings}</div>
                    )}
                    <div style={{ color: T.slate600 }}>{traceData.med_precheck.manual_summary || '已核查，未发现高危禁忌'}</div>
                  </div>
                </EvidenceSection>
              )}

              {/* KB Results */}
              {traceData.kb_results && (
                <EvidenceSection title="知识库检索" icon={<Database size={13} />} color={T.slate600}>
                  <pre style={{ fontSize: 11, color: T.slate600, whiteSpace: 'pre-wrap', margin: 0, lineHeight: 1.6 }}>
                    {typeof traceData.kb_results === 'string' ? traceData.kb_results : JSON.stringify(traceData.kb_results, null, 2)}
                  </pre>
                </EvidenceSection>
              )}

              {/* Search Results */}
              {traceData.search_results && (
                <EvidenceSection title="联网搜索" icon={<Globe size={13} />} color={T.slate600}>
                  <pre style={{ fontSize: 11, color: T.slate600, whiteSpace: 'pre-wrap', margin: 0, lineHeight: 1.6 }}>
                    {typeof traceData.search_results === 'string' ? traceData.search_results : JSON.stringify(traceData.search_results, null, 2)}
                  </pre>
                </EvidenceSection>
              )}
            </div>
          </div>
        )}
      </div>
    </>
  );
};

// ─── Message Bubble Component ────────────────────────────────────────
interface MsgProps {
  msg: Message; index: number; messagesLength: number;
  activeMessageIndex: number; recommendedQueries: string[];
  onSendMessage: (t: string) => void; onViewTrace: (i: number, m: Message) => void;
  isFinished: boolean; options: string[];
  selectedOptions: string[]; setSelectedOptions: React.Dispatch<React.SetStateAction<string[]>>;
}

const MessageBubble = React.memo<MsgProps>(({
  msg, index, messagesLength, activeMessageIndex, recommendedQueries,
  onSendMessage, onViewTrace, isFinished, options, selectedOptions, setSelectedOptions,
}) => {
  const isUser = msg.role === 'user';
  const isLast = index === messagesLength - 1;

  return (
    <div style={{ display: 'flex', justifyContent: isUser ? 'flex-end' : 'flex-start', marginBottom: 24, alignItems: 'flex-start', gap: 10 }}>
      {/* AI avatar */}
      {!isUser && (
        <div style={{ width: 32, height: 32, borderRadius: '50%', background: `linear-gradient(135deg, ${T.teal500}, ${T.teal700})`, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, marginTop: 2 }}>
          <Bot size={16} color="white" />
        </div>
      )}

      <div style={{
        ...(isUser ? { maxWidth: '72%' } : { width: 'min(calc(100% - 42px), 680px)' }),
        padding: '14px 18px', borderRadius: isUser ? '18px 4px 18px 18px' : '4px 18px 18px 18px',
        background: isUser ? `linear-gradient(135deg, ${T.teal600}, ${T.teal800})` : 'white',
        color: isUser ? 'white' : T.slate900,
        boxShadow: isUser ? '0 4px 16px rgba(13,148,136,0.2)' : '0 2px 12px rgba(15,23,42,0.06)',
        border: isUser ? 'none' : `1px solid ${T.slate200}`,
      }}>
        {/* Role label */}
        <div style={{ fontSize: 11, fontWeight: 700, opacity: 0.55, marginBottom: 6, letterSpacing: '0.3px' }}>
          {isUser ? '🧑‍💻 你' : '👩‍⚕️ AI 全科医生'}
        </div>

        {/* Image */}
        {msg.image && (
          <div style={{ marginBottom: 10 }}>
            <img src={msg.image} alt="upload" style={{ maxWidth: 200, borderRadius: 8, border: '2px solid rgba(255,255,255,0.3)' }} />
          </div>
        )}

        {/* Thinking */}
        {msg.isThinking && (
          <div>
            {Array.isArray(msg.maddxEvents) && msg.maddxEvents.length > 0 && (
              <MADDxLiveDebate events={msg.maddxEvents as never[]} isLive />
            )}
            {Array.isArray(msg.rumorEvents) && msg.rumorEvents.length > 0 && (
              <RumorLiveDebate events={msg.rumorEvents as never[]} isLive />
            )}
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '4px 0' }}>
              <div style={{ display: 'flex', gap: 4 }}>
                {[0, 0.22, 0.44].map((d, i) => (
                  <span key={i} className="think-dot" style={{ animationDelay: `${d}s` }} />
                ))}
              </div>
              <span key={msg.thinkingStatus} className="status-txt" style={{ fontSize: 13, color: T.slate500, fontStyle: 'italic' }}>
                {msg.thinkingStatus || '🤔 正在思考…'}
              </span>
            </div>
          </div>
        )}

        {/* Content */}
        {!msg.isThinking && isUser && (
          <div style={{ lineHeight: 1.75, fontSize: 14 }}>{msg.content}</div>
        )}

        {!msg.isThinking && !isUser && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {/* Debate replays */}
            {Array.isArray(msg.meta_data?.trace_data?.maddx_events) && (msg.meta_data!.trace_data!.maddx_events as unknown[]).length > 0 && (
              <MADDxLiveDebate events={msg.meta_data!.trace_data!.maddx_events as never[]} isLive={false} />
            )}
            {Array.isArray(msg.meta_data?.trace_data?.rumor_events) && (msg.meta_data!.trace_data!.rumor_events as unknown[]).length > 0 && (
              <RumorLiveDebate events={msg.meta_data!.trace_data!.rumor_events as never[]} isLive={false} />
            )}

            {/* Vision card */}
            {msg.meta_data?.trace_data?.vision_insights && (
              <div style={{ background: T.green50, border: `1px solid #BBF7D0`, borderRadius: 10, padding: '10px 12px', fontSize: 13, color: '#166534' }}>
                <div style={{ fontWeight: 700, marginBottom: 4, display: 'flex', alignItems: 'center', gap: 6 }}>
                  <Eye size={13} /> 影像特征提取
                </div>
                <div className="md-body">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.meta_data.trace_data.vision_insights as string}</ReactMarkdown>
                </div>
              </div>
            )}

            {/* Med precheck */}
            {msg.meta_data?.trace_data?.med_precheck && (
              <div style={{ background: T.amber50, border: `1px solid #FDE68A`, borderRadius: 10, padding: '10px 12px', fontSize: 13, color: '#92400E' }}>
                <div style={{ fontWeight: 700, marginBottom: 4, display: 'flex', alignItems: 'center', gap: 6 }}>
                  <Stethoscope size={13} /> 用药红线核查
                </div>
                {(() => {
                  const mp = msg.meta_data.trace_data.med_precheck as { kg_warnings?: string; manual_summary?: string };
                  return mp.kg_warnings || mp.manual_summary ? (
                    <>
                      {mp.kg_warnings && <div style={{ color: T.red700, fontWeight: 600, marginBottom: 4 }}>🚨 {mp.kg_warnings}</div>}
                      {mp.manual_summary && <div style={{ opacity: 0.85, fontSize: 12 }}>{mp.manual_summary}</div>}
                    </>
                  ) : (
                    <div style={{ opacity: 0.85, fontSize: 12 }}>✅ 已核查，未发现高危禁忌，遵医嘱服用即可。</div>
                  );
                })()}
              </div>
            )}

            {/* Main content */}
            <div className="md-body" style={{ lineHeight: 1.8, fontSize: 14 }}>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
            </div>
          </div>
        )}

        {/* Trace anchor */}
        {!isUser && msg.meta_data?.trace_data && Object.keys(msg.meta_data.trace_data).length > 0 && (
          <button onClick={() => onViewTrace(index, msg)} style={{
            marginTop: 10, paddingTop: 8, borderTop: `1px dashed ${T.slate200}`,
            display: 'flex', alignItems: 'center', gap: 6, fontSize: 12,
            color: activeMessageIndex === index ? T.teal600 : T.slate400,
            background: 'none', border: 'none', cursor: 'pointer', fontWeight: activeMessageIndex === index ? 700 : 400,
            transition: 'color 0.2s', width: '100%',
          }}>
            <Lightbulb size={12} />
            {activeMessageIndex === index ? '正在查看此轮溯源依据' : '点击查看此轮推理依据'}
          </button>
        )}

        {/* Recommended queries */}
        {index === 0 && !isUser && recommendedQueries.length > 0 && (
          <div style={{ marginTop: 18 }}>
            <div style={{ fontSize: 12, color: T.slate500, marginBottom: 8, fontWeight: 600 }}>💡 猜你想问：</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {recommendedQueries.map((q, i) => (
                <button key={i} onClick={() => onSendMessage(q)} style={{
                  padding: '10px 14px', background: T.slate50, borderRadius: 10,
                  border: `1px solid ${T.slate200}`, cursor: 'pointer', display: 'flex', alignItems: 'center',
                  boxShadow: '0 1px 4px rgba(0,0,0,0.03)', transition: 'all 0.18s', textAlign: 'left',
                }}
                  onMouseEnter={e => { (e.currentTarget as HTMLButtonElement).style.borderColor = T.teal400; (e.currentTarget as HTMLButtonElement).style.boxShadow = `0 2px 8px rgba(13,148,136,0.12)`; }}
                  onMouseLeave={e => { (e.currentTarget as HTMLButtonElement).style.borderColor = T.slate200; (e.currentTarget as HTMLButtonElement).style.boxShadow = '0 1px 4px rgba(0,0,0,0.03)'; }}
                >
                  <span style={{ width: 22, height: 22, borderRadius: '50%', background: T.teal50, color: T.teal600, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontWeight: 800, fontSize: 11, marginRight: 10, flexShrink: 0 }}>#</span>
                  <span style={{ flex: 1, fontSize: 13, color: T.slate800 }}>{q}</span>
                  <ChevronRight size={13} color={T.slate300} />
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Options */}
        {!isUser && !msg.isThinking && isLast && !isFinished && options.length > 0 && (
          <div style={{ marginTop: 14 }}>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 10 }}>
              {options.map((opt, i) => (
                <button key={i} className={`option-btn${selectedOptions.includes(opt) ? ' selected' : ''}`}
                  onClick={() => setSelectedOptions(selectedOptions.includes(opt) ? selectedOptions.filter(x => x !== opt) : [...selectedOptions, opt])}>
                  {opt}
                </button>
              ))}
            </div>
            {selectedOptions.length > 0 && (
              <button onClick={() => onSendMessage(selectedOptions.join('，'))} style={{ padding: '7px 16px', borderRadius: 8, background: `linear-gradient(135deg, ${T.teal500}, ${T.teal700})`, color: 'white', border: 'none', cursor: 'pointer', fontSize: 13, fontWeight: 700, display: 'flex', alignItems: 'center', gap: 6, boxShadow: '0 4px 10px rgba(13,148,136,0.25)' }}>
                <Send size={13} /> 发送
              </button>
            )}
          </div>
        )}
      </div>

      {/* User avatar */}
      {isUser && (
        <div style={{ width: 32, height: 32, borderRadius: '50%', background: T.slate200, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, marginTop: 2 }}>
          <User size={16} color={T.slate500} />
        </div>
      )}
    </div>
  );
});
