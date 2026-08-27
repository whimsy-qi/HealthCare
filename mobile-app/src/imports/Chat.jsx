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

const { Title, Text } = Typography;
const { Panel } = Collapse;
const { TextArea } = Input;

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
    <div style={{ display: 'flex', justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start', marginBottom: 24 }}>
      <div style={{
        // AI 气泡固定宽度（与辩论卡内容一致）；用户气泡保持 content-size
        ...(msg.role === 'ai' ? { width: '80%' } : { maxWidth: '80%' }),
        padding: '16px 20px', borderRadius: '18px',
        background: msg.role === 'user' ? '#E6F7F4' : '#FFFFFF',
        color: msg.role === 'user' ? '#0F766E' : '#1F2937',
        boxShadow: msg.role === 'ai'
          ? '0 2px 12px rgba(15,23,42,0.04), 0 1px 3px rgba(15,23,42,0.02)'
          : '0 1px 4px rgba(15,118,110,0.06)',
        border: 'none',
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
                style={{ fontSize: '13px', color: '#64748B', fontStyle: 'italic', lineHeight: '1.5' }}
              >
                {msg.thinkingStatus || '🤔 正在思考...'}
              </span>
            </div>
          </div>
        )}

        {/* 🌟 多模态卡片流式渲染 */}
        {!msg.isThinking && msg.role === 'user' ? (
          <div style={{ lineHeight: '1.8', fontSize: '15px' }} className="markdown-body">
            {msg.content}
          </div>
        ) : !msg.isThinking && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>

            {/* 0. 多智能体辩论实录：已完成的辩论回放，默认展开 */}
            {Array.isArray(msg.meta_data?.trace_data?.maddx_events) && msg.meta_data.trace_data.maddx_events.length > 0 && (
              <MADDxLiveDebate events={msg.meta_data.trace_data.maddx_events} isLive={false} />
            )}
            {/* 0'. 谣言加权辩论回放（D9 CTAEW） */}
            {Array.isArray(msg.meta_data?.trace_data?.rumor_events) && msg.meta_data.trace_data.rumor_events.length > 0 && (
              <RumorLiveDebate events={msg.meta_data.trace_data.rumor_events} isLive={false} />
            )}

            {/* 1. 渲染视觉智能体初步提取卡片 */}
            {msg.meta_data?.trace_data?.vision_insights && (
              <div style={{ background: '#F0FDF4', border: '1px solid #BBF7D0', padding: '12px', borderRadius: '8px', fontSize: '13px', color: '#166534' }}>
                <div style={{ fontWeight: 'bold', marginBottom: '6px', display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <EyeOutlined style={{ color: '#10B981', fontSize: '15px' }} /> 影像特征提取
                </div>
                <div style={{ lineHeight: '1.6', marginTop: '8px' }}>
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.meta_data.trace_data.vision_insights}</ReactMarkdown>
                </div>
              </div>
            )}

            {/* 2. 渲染用药审查初步排查卡片 */}
            {msg.meta_data?.trace_data?.med_precheck && (
              <div style={{ background: '#FFFBEB', border: '1px solid #FDE68A', padding: '12px', borderRadius: '8px', fontSize: '13px', color: '#92400E' }}>
                <div style={{ fontWeight: 'bold', marginBottom: '6px', display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <MedicineBoxOutlined style={{ color: '#F59E0B', fontSize: '15px' }} /> 用药红线核查
                </div>
                {/* 🌟 核心改造：判断是否有实质内容，没有就给兜底文案 */}
                  {msg.meta_data.trace_data.med_precheck.kg_warnings || msg.meta_data.trace_data.med_precheck.manual_summary ? (
                    <>
                      {msg.meta_data.trace_data.med_precheck.kg_warnings && (
                        <div style={{ color: '#DC2626', marginBottom: '6px', fontWeight: 600 }}>
                          🚨 {msg.meta_data.trace_data.med_precheck.kg_warnings}
                        </div>
                      )}
                      {msg.meta_data.trace_data.med_precheck.manual_summary && (
                        <div style={{ color: '#92400E', opacity: 0.85, fontSize: '12px', lineHeight: '1.5' }}>
                          {msg.meta_data.trace_data.med_precheck.manual_summary}
                        </div>
                      )}
                    </>
                  ) : (
                    <div style={{ color: '#92400E', opacity: 0.85, fontSize: '12px', lineHeight: '1.5' }}>
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
              color: activeMessageIndex === index ? '#14B8A6' : '#94A3B8',
              cursor: 'pointer', transition: 'all 0.3s', fontWeight: activeMessageIndex === index ? 600 : 400
            }}
            onClick={() => onViewTrace(index, msg)}
          >
            <BulbOutlined /> {activeMessageIndex === index ? '正在查看此轮溯源依据' : '点击查看此轮依据'}
          </div>
        )}

        {index === 0 && msg.role === 'ai' && recommendedQueries.length > 0 && (
          <div style={{ marginTop: '20px', display: 'flex', flexDirection: 'column', gap: '10px' }}>
            <div style={{ fontSize: '13px', color: '#64748B', marginBottom: '4px', fontWeight: 600 }}>
              💡 猜你想问：
            </div>
            {recommendedQueries.map((q, i) => (
              <div
                key={i}
                onClick={() => onSendMessage(q)}
                style={{
                  padding: '12px 16px', background: '#FFFFFF', borderRadius: '12px',
                  border: '1px solid #E2E8F0', cursor: 'pointer', display: 'flex',
                  alignItems: 'center', boxShadow: '0 2px 4px rgba(0,0,0,0.02)', transition: 'all 0.2s'
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.borderColor = '#14B8A6';
                  e.currentTarget.style.boxShadow = '0 2px 8px rgba(20,184,166,0.15)';
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.borderColor = '#E2E8F0';
                  e.currentTarget.style.boxShadow = '0 2px 4px rgba(0,0,0,0.02)';
                }}
              >
                <div style={{
                  width: '24px', height: '24px', borderRadius: '50%', background: '#F0FDFA',
                  color: '#14B8A6', display: 'flex', alignItems: 'center', justifyContent: 'center',
                  marginRight: '12px', fontWeight: 'bold'
                }}>#</div>
                <Text style={{ flex: 1, color: '#1E293B', fontSize: '14px' }}>{q}</Text>
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
                      borderColor: '#14B8A6',
                      color: isSelected ? '#fff' : '#14B8A6',
                      background: isSelected ? '#14B8A6' : 'transparent',
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
                style={{ background: '#14B8A6', border: 'none', borderRadius: '6px', boxShadow: '0 2px 6px rgba(20,184,166,0.3)', marginTop: '4px' }}
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

  const [selectedOptions, setSelectedOptions] = useState([]);

  const [recommendedQueries, setRecommendedQueries] = useState([]);
  const messagesEndRef = useRef(null); 
  const [kbModalVisible, setKbModalVisible] = useState(false); 
  const [kbModalData, setKbModalData] = useState({ title: '', content: '', dept: '' }); 

  useEffect(() => {
    const initSessions = async () => {
      const token = localStorage.getItem('access_token');
      if (!token) {
        navigate('/login');
        return;
      }
      try {
        const res = await fetch('http://localhost:8000/api/sessions', {
          headers: { 'Authorization': `Bearer ${token}` }
        });
        if (res.status === 401) throw new Error('Unauthorized');
        
        const data = await res.json();
        if (data.length > 0) {
          setSessionList(data);
          setActiveSessionId(data[0].id); 
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
        const res = await fetch(`http://localhost:8000/api/sessions/${activeSessionId}/messages`, {
          headers: { 'Authorization': `Bearer ${token}` }
        });
        const historyData = await res.json();
        
        if (historyData.length === 0) {
          setMessages([defaultGreeting]);
          fetch('http://localhost:8000/api/recommend_queries')
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
              ? `http://localhost:8000${m.image}`
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
      dispatch({ type: 'SET_TRACE_STEP', payload: 1 });
      dispatch({ type: 'SET_EXPANDED_PANELS', payload: ['1', 'audit', 'maddx'] });

      const timer1 = setTimeout(() => {
        dispatch({ type: 'SET_TRACE_STEP', payload: 2 });
        dispatch({ type: 'SET_EXPANDED_PANELS', payload: [...expandedPanels, '1', 'audit', '2', 'kg'] });
      }, 800);

      const timer2 = setTimeout(() => {
        dispatch({ type: 'SET_TRACE_STEP', payload: 3 });
        dispatch({ type: 'SET_EXPANDED_PANELS', payload: [...expandedPanels, '1', 'audit', '2', 'kg', '3'] });
      }, 2000);

      const timer3 = setTimeout(() => {
        dispatch({ type: 'SET_TRACE_STEP', payload: 4 });
        dispatch({ type: 'SET_EXPANDED_PANELS', payload: [...expandedPanels, '1', 'audit', '2', 'kg', '3', '4'] });
      }, 3500);

      return () => { clearTimeout(timer1); clearTimeout(timer2); clearTimeout(timer3); };
    } else if (!isCurrentLoading) {
      dispatch({ type: 'SET_TRACE_STEP', payload: 0 });
      dispatch({ type: 'SET_EXPANDED_PANELS', payload: [] });
    }
  }, [isEvidencePanelVisible, currentTraceData]);

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
      const res = await fetch('http://localhost:8000/api/sessions', {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${token}` }
      });
      const newSession = await res.json();
      setSessionList(prev => [{ id: newSession.id, title: newSession.title, date: '刚刚' }, ...prev]);
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
        const uploadRes = await fetch('http://localhost:8000/api/upload_image', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
          body: JSON.stringify({ image_base64: selectedImage, session_id: activeSessionId ? parseInt(activeSessionId) : null })
        });
        if (uploadRes.ok) {
          const uploadData = await uploadRes.json();
          imageUrlForBackend = uploadData.file_id || uploadData.storage_key || uploadData.url;
          imageUrlForMsg = uploadData.url?.startsWith('http')
            ? uploadData.url
            : (uploadData.url ? `http://localhost:8000${uploadData.url}` : selectedImage);
        }
      } catch (e) {
        console.error('图片上传失败:', e);
      }
    }

    const newMessages = [...messages, { role: 'user', content: finalQuery, image: imageUrlForMsg }];
    setMessages(newMessages);

    setInputText('');
    setSelectedImage(null);
    dispatch({ type: 'SET_EXPANDED_PANELS', payload: [] });
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

      const response = await fetch('http://localhost:8000/api/chat', {
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
      const decoder = new TextDecoder();
      let sseBuffer = '';

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

          if (eventData.type === 'status') {
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
            fetch('http://localhost:8000/api/sessions', { headers: { 'Authorization': `Bearer ${token}` } })
              .then(res => res.json())
              .then(sessions => setSessionList(sessions));

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
            background-color: #14B8A6 !important;
          }
          .custom-spin .ant-spin-text {
            color: #14B8A6 !important;
            font-weight: 500;
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
            background: #14B8A6;
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
      
      <div style={{ display: 'flex', height: '100vh', width: '100vw', background: '#F8FAFC', overflow: 'hidden' }}>
        
        {/* 1. 左侧边栏 */}
        <div style={{ width: '260px', flexShrink: 0, background: 'linear-gradient(135deg, #F0FDFA 0%, #E0F2FE 100%)', borderRight: '1px solid rgba(255, 255, 255, 0.5)', display: 'flex', flexDirection: 'column', padding: '24px', position: 'relative' }}>
          <div style={{ display: 'flex', alignItems: 'center', marginBottom: '32px', zIndex: 1 }}>
            <MedicineBoxOutlined style={{ fontSize: 28, color: '#14B8A6', marginRight: 12 }} />
            <Title level={4} style={{ margin: 0, fontWeight: 700, color: '#0F172A' }}>医疗AI引擎</Title>
          </div>
          
          <Button type="primary" icon={<PlusOutlined />} block onClick={createNewSession} style={{ background: 'linear-gradient(135deg, #14B8A6 0%, #0D9488 100%)', border: 'none', borderRadius: '10px', height: '42px', fontSize: '15px', fontWeight: 600, marginBottom: '24px', boxShadow: '0 4px 12px rgba(20,184,166,0.3)', zIndex: 1 }}>
            新建健康咨询
          </Button>

          <div className="advanced-scrollbar" style={{ flex: 1, overflowY: 'auto', marginBottom: '24px', zIndex: 1, paddingRight: '4px' }}>
            {sessionList.map(item => (
              <div 
                key={item.id} 
                onClick={() => setActiveSessionId(item.id)} 
                style={{ 
                  padding: '12px 16px', borderRadius: '8px', 
                  background: activeSessionId === item.id ? 'rgba(255,255,255,0.7)' : 'transparent', 
                  border: activeSessionId === item.id ? '1px solid rgba(20,184,166,0.2)' : '1px solid transparent', 
                  marginBottom: '8px', cursor: 'pointer', display: 'flex', alignItems: 'center',
                  transition: 'all 0.2s'
                }}
              >
                <Text style={{ fontSize: '15px', color: activeSessionId === item.id ? '#14B8A6' : '#475569', fontWeight: activeSessionId === item.id ? 600 : 400, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: '100%' }}>
                  {item.title}
                </Text>
              </div>
            ))}
          </div>

          <div style={{ marginTop: 'auto', display: 'flex', flexDirection: 'column', gap: '10px', zIndex: 1 }}>
            
            {/* 🌟 医疗知识图谱入口卡片 (莫兰迪蓝) */}
            <div 
              onClick={() => navigate('/graph')} 
              style={{
                background: 'linear-gradient(135deg, rgba(171, 215, 251, 0.15) 0%, rgba(171, 215, 251, 0.4) 100%)', 
                border: '1px solid rgba(171, 215, 251, 0.8)', 
                borderRadius: '12px',
                padding: '12px 14px',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                boxShadow: '0 4px 12px rgba(171, 215, 251, 0.2)',
                transition: 'all 0.3s ease'
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.transform = 'translateY(-2px)';
                e.currentTarget.style.boxShadow = '0 6px 16px rgba(171, 215, 251, 0.4)';
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.transform = 'translateY(0)';
                e.currentTarget.style.boxShadow = '0 4px 12px rgba(171, 215, 251, 0.2)';
              }}
            >
              <div style={{ 
                width: '36px', height: '36px', borderRadius: '8px', 
                background: '#ABD7FB', display: 'flex', justifyContent: 'center', alignItems: 'center', marginRight: '12px' 
              }}>
                <ShareAltOutlined style={{ color: '#FFFFFF', fontSize: '20px' }} />
              </div>
              <div style={{ flex: 1 }}>
                <div style={{ color: '#0369A1', fontWeight: 'bold', fontSize: '14px', letterSpacing: '0.5px' }}>医疗知识图谱</div>
                <div style={{ color: '#38BDF8', fontSize: '12px', marginTop: '2px' }}>探索疾病与药物星系</div>
              </div>
            </div>

            {/* 🌟 健康知识专区 (升级为果冻橙质感) */}
            <div 
              onClick={() => navigate('/knowledge')} 
              style={{
                background: 'linear-gradient(135deg, rgba(251, 191, 36, 0.15) 0%, rgba(251, 191, 36, 0.4) 100%)', 
                border: '1px solid rgba(251, 191, 36, 0.6)', 
                borderRadius: '12px',
                padding: '12px 14px',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                boxShadow: '0 4px 12px rgba(251, 191, 36, 0.15)',
                transition: 'all 0.3s ease'
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.transform = 'translateY(-2px)';
                e.currentTarget.style.boxShadow = '0 6px 16px rgba(251, 191, 36, 0.3)';
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.transform = 'translateY(0)';
                e.currentTarget.style.boxShadow = '0 4px 12px rgba(251, 191, 36, 0.15)';
              }}
            >
              <div style={{ 
                width: '36px', height: '36px', borderRadius: '8px', 
                background: '#FBBF24', display: 'flex', justifyContent: 'center', alignItems: 'center', marginRight: '12px' 
              }}>
                <BookOutlined style={{ color: '#FFFFFF', fontSize: '20px' }} />
              </div>
              <div style={{ flex: 1 }}>
                <div style={{ color: '#B45309', fontWeight: 'bold', fontSize: '14px', letterSpacing: '0.5px' }}>健康知识专区</div>
                <div style={{ color: '#D97706', fontSize: '12px', marginTop: '2px' }}>硬核科普与辟谣</div>
              </div>
            </div>

            {/* 🌟 我的数字健康档案 (升级为薄荷青质感) */}
            <div 
              onClick={() => navigate('/profile')} 
              style={{
                background: 'linear-gradient(135deg, rgba(20, 184, 166, 0.15) 0%, rgba(20, 184, 166, 0.4) 100%)', 
                border: '1px solid rgba(20, 184, 166, 0.5)', 
                borderRadius: '12px',
                padding: '12px 14px',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                boxShadow: '0 4px 12px rgba(20, 184, 166, 0.15)',
                transition: 'all 0.3s ease'
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.transform = 'translateY(-2px)';
                e.currentTarget.style.boxShadow = '0 6px 16px rgba(20, 184, 166, 0.3)';
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.transform = 'translateY(0)';
                e.currentTarget.style.boxShadow = '0 4px 12px rgba(20, 184, 166, 0.15)';
              }}
            >
              <div style={{ 
                width: '36px', height: '36px', borderRadius: '8px', 
                background: '#14B8A6', display: 'flex', justifyContent: 'center', alignItems: 'center', marginRight: '12px' 
              }}>
                <UserOutlined style={{ color: '#FFFFFF', fontSize: '20px' }} />
              </div>
              <div style={{ flex: 1 }}>
                <div style={{ color: '#0F766E', fontWeight: 'bold', fontSize: '14px', letterSpacing: '0.5px' }}>我的数字健康档案</div>
                <div style={{ color: '#0D9488', fontSize: '12px', marginTop: '2px' }}>查看与编辑体征</div>
              </div>
            </div>

          </div>
        </div>

        {/* 2. 中部主对话区 */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', background: '#F8FAFC', position: 'relative' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '16px 24px', borderBottom: '1px solid rgba(148,163,184,0.08)', background: 'rgba(255,255,255,0.6)', backdropFilter: 'blur(8px)' }}>
            <Text style={{ fontSize: '16px', color: '#1E293B', fontWeight: 600 }}>
              多模态智能诊疗室
            </Text>
            <Space>
              <Tooltip title={isEvidencePanelVisible ? "收起溯源面板" : "展开溯源面板"}>
                <Button type="text" icon={isEvidencePanelVisible ? <MenuFoldOutlined /> : <MenuUnfoldOutlined />} onClick={() => dispatch({ type: 'TOGGLE_EVIDENCE_PANEL' })} style={{ color: isEvidencePanelVisible ? '#14B8A6' : '#94A3B8' }} />
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
                background: '#FFFFFF',
                borderRadius: '20px',
                padding: '14px 16px 10px',
                border: 'none',
                boxShadow: inputText
                  ? '0 0 0 2px rgba(20,184,166,0.15), 0 12px 32px rgba(15,23,42,0.08), 0 2px 6px rgba(15,23,42,0.04)'
                  : '0 12px 32px rgba(15,23,42,0.08), 0 2px 6px rgba(15,23,42,0.04)',
                position: 'relative',
                transition: 'box-shadow 0.25s ease-out',
              }}
            >

              {/* 病历附件预览栈 */}
              {selectedImage && (
                <div style={{ marginBottom: '12px', paddingBottom: '10px', borderBottom: '1px dashed #E2E8F0', display: 'flex', alignItems: 'center', gap: '12px' }}>
                  <Text style={{ fontSize: '12px', color: '#64748B', fontWeight: 600 }}>📎 问诊附件</Text>
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
                  fontSize: '15px', color: '#1E293B',
                  resize: 'none', background: 'transparent',
                }}
              />

              {/* 底部工具栏：左侧工具 + 右侧字数 & 发送 */}
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: '6px' }}>
                <Space size={2}>
                  <Upload accept="image/*" showUploadList={false} beforeUpload={handleImageUpload}>
                    <Tooltip title="上传症状影像 / 化验单">
                      <Button type="text" icon={<PictureOutlined style={{ fontSize: '17px' }} />}
                        style={{ color: '#64748B', width: '32px', height: '32px', borderRadius: '8px', background: 'transparent' }} />
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
                  <span style={{ fontSize: '11px', color: '#94A3B8', fontVariantNumeric: 'tabular-nums' }}>
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
                      background: (!inputText.trim() && !selectedImage) ? '#F1F5F9' : '#14B8A6',
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
          <div className="advanced-scrollbar" style={{ width: '420px', background: '#F8FAFC', borderLeft: '1px solid rgba(148,163,184,0.1)', display: 'flex', flexDirection: 'column', padding: '24px', overflowY: 'auto' }}>
            <div style={{ display: 'flex', alignItems: 'center', marginBottom: '16px' }}>
              <BulbOutlined style={{ fontSize: 24, color: '#14B8A6', marginRight: 12 }} />
              <Title level={4} style={{ margin: 0, fontWeight: 700, color: '#0F172A' }}>可信溯源</Title>
            </div>

            {/* 🌟 等待大模型响应时的缓冲状态栏 */}
            {isCurrentLoading && (
               <div className="panel-anim" style={{ padding: '10px 14px', background: 'rgba(20, 184, 166, 0.1)', borderRadius: '8px', color: '#14B8A6', fontSize: '13px', display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '16px', border: '1px solid rgba(20, 184, 166, 0.2)' }}>
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
                
                {traceStep >= 1 && currentTraceData?.audit_log && currentTraceData.audit_log.length > 0 && (
                  <Panel header={<Space><ApiOutlined style={{ color: '#8B5CF6' }} />多智能体协作链路</Space>} key="audit" className="panel-anim" style={{ backgroundColor: '#fff', borderRadius: '10px', marginBottom: '16px', border: '1px solid rgba(226,232,240,0.8)' }}>
                    <Timeline style={{ marginTop: '8px', marginLeft: '4px' }}>
                      {currentTraceData.audit_log.map((log, index) => {
                        const match = log.match(/^\[(.*?)\]\s*(.*)/);
                        const agent = match ? match[1] : 'System';
                        const action = match ? match[2] : log;
                        
                        let dotColor = '#94A3B8';
                        if (agent === 'Triage') dotColor = '#0EA5E9';
                        if (agent === 'Medication') dotColor = '#8B5CF6';
                        if (agent === 'General') dotColor = '#F59E0B';
                        if (agent === 'Symptom') dotColor = '#10B981';
                        if (agent === 'Rumor') dotColor = '#F43F5E';

                        return (
                          <Timeline.Item key={index} color={dotColor} style={{ paddingBottom: index === currentTraceData.audit_log.length - 1 ? '0' : '20px' }}>
                            <div style={{ display: 'flex', flexDirection: 'column' }}>
                              <Text strong style={{ fontSize: '13px', color: '#1E293B', marginBottom: '4px' }}>{agent} Agent</Text>
                              <Text type="secondary" style={{ fontSize: '12px', lineHeight: '1.5' }}>{action}</Text>
                            </div>
                          </Timeline.Item>
                        );
                      })}
                    </Timeline>

                    {currentTraceData?.internal_scratchpad && currentTraceData.internal_scratchpad.length > 0 && (
                      <div style={{ marginTop: '16px', paddingTop: '16px', borderTop: '1px dashed rgba(148,163,184,0.3)' }}>
                        <div style={{ fontSize: '12px', fontWeight: 600, color: '#64748B', marginBottom: '8px', display: 'flex', alignItems: 'center', gap: '6px' }}>
                          <MessageOutlined /> 内部会诊留言板
                        </div>
                        {currentTraceData.internal_scratchpad.map((msg, idx) => (
                          <div key={idx} style={{ 
                            background: 'linear-gradient(135deg, #FFFBEB 0%, #FEF3C7 100%)', 
                            padding: '10px 12px', borderRadius: '8px', marginBottom: '8px', 
                            border: '1px solid rgba(245, 158, 11, 0.2)', fontSize: '12px' 
                          }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '4px', marginBottom: '6px' }}>
                              <Tag color="orange" style={{ margin: 0, fontSize: '10px', lineHeight: '16px', padding: '0 4px' }}>{msg.from.toUpperCase()}</Tag>
                              <RightOutlined style={{ fontSize: '10px', color: '#D97706' }} />
                              <Tag color="orange" style={{ margin: 0, fontSize: '10px', lineHeight: '16px', padding: '0 4px' }}>{msg.to.toUpperCase()}</Tag>
                            </div>
                            <div style={{ color: '#92400E', lineHeight: '1.5' }}>"{msg.msg}"</div>
                          </div>
                        ))}
                      </div>
                    )}
                  </Panel>
                )}

                {/* 🌟 MADDx 多智能体辩论溯源 (D7)：仅在症状诊断且后端启用 MADDx 时出现 */}
                {currentTraceData?.maddx_debate && Array.isArray(currentTraceData.maddx_debate.nodes) && currentTraceData.maddx_debate.nodes.length > 0 && (
                  <Panel
                    header={
                      <Space>
                        <ShareAltOutlined style={{ color: '#8B5CF6' }} />
                        <span style={{ fontWeight: 600 }}>MADDx 多智能体辩论过程</span>
                        <Tag color="purple" style={{ margin: 0, fontSize: 10 }}>核心研究点</Tag>
                      </Space>
                    }
                    key="maddx"
                    className="panel-anim"
                    style={{
                      background: 'linear-gradient(135deg, #FAF5FF 0%, #FFFFFF 100%)',
                      borderRadius: 10, marginBottom: 16,
                      border: '1px solid #DDD6FE',
                    }}
                  >
                    <MADDxDebateView dag={currentTraceData.maddx_debate} />
                  </Panel>
                )}

                {traceStep >= 1 && (
                  <Panel header={<Space><SafetyCertificateOutlined style={{ color: '#14B8A6' }} />引擎诊断链路</Space>} key="1" className="panel-anim" style={{ backgroundColor: '#fff', borderRadius: '10px', marginBottom: '16px', border: '1px solid rgba(226,232,240,0.8)' }}>
                    <Text type="secondary" style={{ fontSize: '12px' }}>
                      当前触发轨道：<Tag color="cyan">{currentRoute || "无"}</Tag><br/>
                      视觉模块：已就绪<br/>
                      检索模块：深度全模态检索已激活
                    </Text>
                  </Panel>
                )}

                {traceStep >= 2 && currentTraceData?.sources && currentTraceData.sources.length > 0 && currentRoute !== 'RUMOR_VERIFICATION' && (
                  <Panel header={<Space>🧠 临床决策依据 </Space>} key="kg" className="panel-anim" style={{ backgroundColor: '#fff', borderRadius: '10px', marginBottom: '16px', border: '1px solid rgba(226,232,240,0.8)' }}>
                    <div className="advanced-scrollbar" style={{ maxHeight: '450px', overflowY: 'auto', paddingRight: '4px' }}>
                      {currentTraceData.sources.map((source, index) => {
                        let cardColor = source.type === 'guide' ? '#FFF1F2' : (source.type === 'kg' ? '#F0FDF4' : '#F0F9FF');
                        let borderColor = source.type === 'guide' ? '#FECDD3' : (source.type === 'kg' ? '#BBF7D0' : '#BAE6FD');
                        let deptColor = source.type === 'guide' ? 'magenta' : (source.type === 'kg' ? 'green' : 'cyan');

                        return (
                          <div key={index} style={{ 
                            marginBottom: '12px', padding: '12px', background: cardColor, borderRadius: '8px', 
                            border: `1px solid ${borderColor}`, transition: 'all 0.3s', boxShadow: '0 2px 4px rgba(0,0,0,0.02)'
                          }}>
                            <div style={{ display: 'flex', alignItems: 'center', marginBottom: '8px', gap: '8px' }}>
                              <Tag color={deptColor} style={{ margin: 0, border: 'none', fontWeight: 600 }}>{source.department || '综合'}</Tag>
                              <div 
                                style={{ fontWeight: 600, fontSize: '13px', color: '#166534', cursor: 'pointer', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', flex: 1, borderBottom: '1px dashed #166534' }}
                                onClick={() => {
                                  setKbModalData({ title: source.title, content: source.content, dept: source.department || '本地文献' });
                                  setKbModalVisible(true);
                                }}
                              >
                                {source.title} <span style={{ fontSize: '10px', color: '#14B8A6', marginLeft: '4px' }}><EyeOutlined /> 原文</span>
                              </div>
                            </div>
                            <div style={{ fontSize: '12px', color: '#475569', lineHeight: '1.6', marginBottom: '8px', display: '-webkit-box', WebkitLineClamp: 4, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                              {source.content}
                            </div>
                            <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                              <Tag icon={<DatabaseOutlined />} bordered={false} color="default" style={{ margin: 0, fontSize: '11px', color: '#64748B', background: 'rgba(255,255,255,0.6)' }}>关联图谱: {source.disease || '通用'}</Tag>
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  </Panel>
                )}
                {/* 🌟 核心新增：为症状诊断轨道专门渲染的 GraphRAG 推理路径面板 */}
                {traceStep >= 3 && !currentTraceData?.scout_data && currentTraceData?.critic_reasoning && (
                  <Panel header={<Space>🧬 图谱多跳推理链路</Space>} key="graph_reasoning" className="panel-anim" style={{ backgroundColor: '#fff', borderRadius: '10px', marginBottom: '16px', border: '1px solid rgba(226,232,240,0.8)' }}>
                    <div style={{ 
                      fontSize: '12px', 
                      color: '#475569', 
                      lineHeight: '1.8',
                      whiteSpace: 'pre-wrap', // 🌟 必须加这个，保留后端传来的换行和缩进，呈现树状图效果
                      background: '#F8FAFC',
                      padding: '12px',
                      borderRadius: '8px',
                      border: '1px dashed #CBD5E1'
                    }}>
                      {currentTraceData.critic_reasoning}
                    </div>
                  </Panel>
                )}

              
                
                {currentRoute === 'RUMOR_VERIFICATION' && currentTraceData && (
                  <>
                    {traceStep >= 2 && (
                      <Panel header={<Space>🕵️‍♂️ 网络舆情侦察 (Scout)</Space>} key="2" className="panel-anim" style={{ backgroundColor: '#fff', borderRadius: '10px', marginBottom: '16px', border: '1px solid rgba(226,232,240,0.8)' }}>
                        <div className="advanced-scrollbar" style={{ maxHeight: '300px', overflowY: 'auto', paddingRight: '8px' }}>
                          {Array.isArray(currentTraceData.scout_data) && currentTraceData.scout_data.length > 0 ? (
                            currentTraceData.scout_data.map((source, index) => (
                              <div key={index} style={{ marginBottom: '12px', padding: '12px', background: '#F8FAFC', borderRadius: '8px', border: '1px solid #E2E8F0' }}>
                                <div style={{ fontWeight: 600, fontSize: '13px', marginBottom: '6px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                                  <a href={source.url} target="_blank" rel="noreferrer" style={{ color: '#0284C7', textDecoration: 'none' }}>🔗 {source.title}</a>
                                </div>
                                <div style={{ fontSize: '12px', color: '#475569', lineHeight: '1.6', display: '-webkit-box', WebkitLineClamp: 3, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                                  {source.content}
                                </div>
                              </div>
                            ))
                          ) : (
                            <Text type="secondary" style={{ fontSize: '12px' }}>{typeof currentTraceData.scout_data === 'string' ? currentTraceData.scout_data : "未检索到相关的网络舆情数据"}</Text>
                          )}
                        </div>
                      </Panel>
                    )}
                    
                    {traceStep >= 3 && (
                      <Panel header={<Space>📚 权威医学事实 (Medical)</Space>} key="3" className="panel-anim" style={{ backgroundColor: '#fff', borderRadius: '10px', marginBottom: '16px', border: '1px solid rgba(226,232,240,0.8)' }}>
                        <div style={{ fontSize: '13px', color: '#1E293B', lineHeight: '1.6', marginBottom: '16px', paddingBottom: '16px', borderBottom: '1px dashed #E2E8F0' }} className="markdown-body">
                          <ReactMarkdown>{currentTraceData.medical_truth_text || "解析中..."}</ReactMarkdown>
                        </div>
                        <div style={{ fontSize: '12px', fontWeight: 600, color: '#64748B', marginBottom: '8px' }}>📑 交叉验证文献引用：</div>
                        <div className="advanced-scrollbar" style={{ maxHeight: '250px', overflowY: 'auto', paddingRight: '8px' }}>
                          {Array.isArray(currentTraceData.medical_data) && currentTraceData.medical_data.length > 0 ? (
                            currentTraceData.medical_data.map((source, index) => (
                              <div key={index} style={{ marginBottom: '10px', padding: '10px', background: source.is_internal ? '#F0FDF4' : '#F0F9FF', borderRadius: '6px', border: '1px solid #E2E8F0' }}>
                                <div style={{ display: 'flex', alignItems: 'center', marginBottom: '4px', gap: '6px' }}>
                                  {source.is_internal ? <Tag color="green" icon={<DatabaseOutlined />} style={{ margin: 0, border: 'none' }}>本地智库</Tag> : <Tag color="blue" icon={<GlobalOutlined />} style={{ margin: 0, border: 'none' }}>权威外网</Tag>}
                                  <div style={{ fontWeight: 600, fontSize: '12px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', flex: 1 }}>
                                    {source.is_internal ? (
                                      <span 
                                        style={{ color: '#166534', cursor: 'pointer', borderBottom: '1px dashed #166534' }}
                                        onClick={() => {
                                          setKbModalData({ title: source.title, content: source.content, dept: source.department || '核心事实' });
                                          setKbModalVisible(true);
                                        }}
                                      >
                                        {source.title} <span style={{ fontSize: '10px', color: '#14B8A6' }}><EyeOutlined /> 阅读原文</span>
                                      </span>
                                    ) : (
                                      <a href={source.url} target="_blank" rel="noreferrer" style={{ color: '#0284C7', textDecoration: 'none' }}>{source.title}</a>
                                    )}
                                  </div>
                                </div>
                                <div style={{ fontSize: '11px', color: '#475569', lineHeight: '1.5', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                                  {source.content}
                                </div>
                              </div>
                            ))
                          ) : (
                            <Text type="secondary" style={{ fontSize: '12px' }}>未匹配到相关的底层文献卡片</Text>
                          )}
                        </div>
                      </Panel>
                    )}
                    
                    {traceStep >= 4 && (
                      <Panel header={<Space>⚖️ 交叉辩驳过程 (Critic)</Space>} key="4" className="panel-anim" style={{ backgroundColor: '#fff', borderRadius: '10px', marginBottom: '16px', border: '1px solid rgba(226,232,240,0.8)' }}>
                        <div style={{ fontSize: '13px', lineHeight: '1.6', color: '#475569' }} className="markdown-body">
                          <ReactMarkdown remarkPlugins={[remarkGfm]}>
                            {currentTraceData.critic_reasoning || "暂无辩驳数据"}
                          </ReactMarkdown>
                        </div>
                      </Panel>
                    )}
                  </>
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
            <DatabaseOutlined style={{ color: '#14B8A6', fontSize: '18px' }} />
            <span style={{ color: '#0F172A', fontWeight: 600 }}>文献原文阅览</span>
          </Space>
        }
        open={kbModalVisible}
        onCancel={() => setKbModalVisible(false)}
        footer={[
          <Button key="close" type="primary" onClick={() => setKbModalVisible(false)} style={{ background: '#14B8A6', border: 'none', borderRadius: '8px' }}>
            我知道了
          </Button>
        ]}
        width={500}
        centered
        styles={{ body: { padding: '16px 0' } }}
      >
        <div style={{ padding: '0 8px' }}>
          <div style={{ fontSize: '16px', fontWeight: 600, color: '#1E293B', marginBottom: '12px' }}>
            {kbModalData.title}
          </div>
          <Tag color="cyan" style={{ marginBottom: '16px', border: 'none' }}>{kbModalData.dept}</Tag>
          <Typography.Paragraph style={{ lineHeight: '1.8', fontSize: '14px', color: '#334155', background: '#F8FAFC', padding: '16px', borderRadius: '8px', border: '1px solid #E2E8F0' }}>
            {kbModalData.content}
          </Typography.Paragraph>
        </div>
      </Modal>
    </>
  );
};

export default Chat;
