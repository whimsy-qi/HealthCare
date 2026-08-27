import React, { useState, useEffect, useRef } from 'react';
import { Typography, Tabs, Card, Tag, Button, Avatar, Modal, Spin, Empty, Space, message, Skeleton, Tooltip } from 'antd';
import {
  MedicineBoxOutlined,
  SafetyCertificateOutlined,
  BookOutlined,
  FireOutlined,
  RightOutlined,
  MessageOutlined,
  EyeOutlined,
  LikeOutlined,
  LikeFilled,
  ClockCircleOutlined,
  StarOutlined,
  ThunderboltOutlined,
  SendOutlined,
  RobotOutlined,
  UserOutlined,
  LinkOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

const { Title, Text } = Typography;

// localStorage key，隔离不同用户（登录时存的是 current_username）
const getLikedKey = () => `liked_articles_${localStorage.getItem('current_username') || 'guest'}`;

const KnowledgeZone = () => {
  const navigate = useNavigate();
  const [articles, setArticles] = useState([]);
  const [loading, setLoading] = useState(false);
  const [activeTab, setActiveTab] = useState('辟谣粉碎机');

  // 🌟 弹窗(Modal)详情相关状态
  const [modalVisible, setModalVisible] = useState(false);
  const [currentArticle, setCurrentArticle] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);

  // 🌟 点赞状态：从 localStorage 恢复，跨页面/刷新持久化
  const [likedArticles, setLikedArticles] = useState(() => {
    try {
      const saved = localStorage.getItem(getLikedKey());
      return saved ? new Set(JSON.parse(saved)) : new Set();
    } catch {
      return new Set();
    }
  });

  // 🌟 实时热点状态
  const [liveArticles, setLiveArticles] = useState([]);
  const [liveLoading, setLiveLoading] = useState(false);
  const [liveFetched, setLiveFetched] = useState(false);

  // 🌟 文章内 AI 问答状态
  const [qaMessages, setQaMessages] = useState([]);   // [{role:'user'|'ai', content:string}]
  const [qaInput, setQaInput] = useState('');
  const [qaLoading, setQaLoading] = useState(false);
  const qaBottomRef = useRef(null);

  // 🌟 个性化推荐状态
  const [isLoggedIn, setIsLoggedIn] = useState(() => !!localStorage.getItem('access_token'));
  const [recommended, setRecommended] = useState([]);
  const [recommendLoading, setRecommendLoading] = useState(false);
  const [recommendFetched, setRecommendFetched] = useState(false); // 请求已完成（无论成功与否）
  const [recommendFallback, setRecommendFallback] = useState(false);
  const [recommendMsg, setRecommendMsg] = useState('');
  const [recommendError, setRecommendError] = useState(false);
  const recommendRef = useRef(null);

  // 🌟 高清医疗图库兜底
  const getFallbackImage = (category) => {
    const fallbacks = {
      '辟谣粉碎机': 'https://images.unsplash.com/photo-1576091160399-112ba8d25d1d?auto=format&fit=crop&q=80&w=800',
      '用药红绿灯': 'https://images.unsplash.com/photo-1584308666744-24d5c474f2ae?auto=format&fit=crop&q=80&w=800',
      '硬核诊疗局': 'https://images.unsplash.com/photo-1551076805-e1869043e560?auto=format&fit=crop&q=80&w=800',
      '时令与养生': 'https://images.unsplash.com/photo-1544367567-0f2fcb009e0b?auto=format&fit=crop&q=80&w=800',
      '实时热点追踪': 'https://images.unsplash.com/photo-1504439468489-c8920d786a2b?auto=format&fit=crop&q=80&w=800'
    };
    return fallbacks[category] || fallbacks['硬核诊疗局'];
  };

  useEffect(() => {
    fetchArticles();
    fetchRecommended();
  }, []);

  const fetchArticles = async () => {
    setLoading(true);
    try {
      const res = await fetch('http://localhost:8000/api/articles');
      if (!res.ok) throw new Error('网络响应异常');
      const data = await res.json();
      setArticles(data);
    } catch (error) {
      console.error("加载文章列表失败:", error);
      message.error("加载健康知识失败，请检查网络或后端服务");
    } finally {
      setLoading(false);
    }
  };

  // 🌟 实时热点：切换到该 Tab 时调用 Tavily 联网抓取 + LLM 重塑 + 万相生成封面
  const fetchLiveArticles = async (forceRefresh = false) => {
    if (liveFetched && !forceRefresh) return; // 同一次会话只拉一次（除非强制刷新）
    setLiveLoading(true);
    try {
      const url = forceRefresh
        ? 'http://localhost:8000/api/articles/hot-realtime?refresh=true'
        : 'http://localhost:8000/api/articles/hot-realtime';
      const res = await fetch(url);
      if (!res.ok) throw new Error(`热点接口异常: ${res.status}`);
      const data = await res.json();
      setLiveArticles(data.articles || []);
      if (data.cached) {
        message.info(`命中缓存（${data.cache_age_min} 分钟前抓取）`);
      } else {
        message.success(`已完成 ${data.articles?.length || 0} 篇文章的 AI 重塑`);
      }
    } catch (err) {
      console.error('[实时热点] 加载失败:', err);
      message.error('实时热点加载失败，请稍后重试');
    } finally {
      setLiveLoading(false);
      setLiveFetched(true);
    }
  };

  // 🌟 文章内 AI 问答：流式 SSE 读取
  const sendArticleQuestion = async () => {
    if (!qaInput.trim() || qaLoading || !currentArticle) return;
    const question = qaInput.trim();
    setQaInput('');
    setQaMessages(prev => [...prev, { role: 'user', content: question }, { role: 'ai', content: '' }]);
    setQaLoading(true);
    try {
      const res = await fetch(`http://localhost:8000/api/articles/${currentArticle.id}/ask`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question }),
      });
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const parts = buf.split('\n\n');
        buf = parts.pop();
        for (const part of parts) {
          if (!part.startsWith('data: ')) continue;
          const evt = JSON.parse(part.slice(6));
          if (evt.type === 'chunk') {
            setQaMessages(prev => {
              const msgs = [...prev];
              msgs[msgs.length - 1] = { role: 'ai', content: msgs[msgs.length - 1].content + evt.content };
              return msgs;
            });
            qaBottomRef.current?.scrollIntoView({ behavior: 'smooth' });
          }
        }
      }
    } catch {
      setQaMessages(prev => {
        const msgs = [...prev];
        msgs[msgs.length - 1] = { role: 'ai', content: '⚠️ AI 问答暂时不可用，请稍后重试。' };
        return msgs;
      });
    } finally {
      setQaLoading(false);
    }
  };

  // 🌟 个性化推荐：携带 token，后端基于健康档案用 LLM 挑选
  const fetchRecommended = async () => {
    const token = localStorage.getItem('access_token');
    console.log('[推荐] token:', token ? '存在' : '不存在');
    if (!token) {
      setIsLoggedIn(false);
      setRecommendFetched(true); // 无 token 也标记完成，触发"未登录"状态渲染
      return;
    }
    setIsLoggedIn(true);
    setRecommendLoading(true);
    setRecommendError(false);
    try {
      const res = await fetch('http://localhost:8000/api/articles/recommended', {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      console.log('[推荐] 响应状态:', res.status);
      // token 过期或失效 → 清除旧 token，回退到"未登录"状态，提示用户重新登录
      if (res.status === 401) {
        localStorage.removeItem('access_token');
        localStorage.removeItem('current_username');
        message.warning('登录已过期，请重新登录后获取专属推荐');
        setIsLoggedIn(false);
        setRecommendFetched(true);
        setRecommendLoading(false);
        return;
      }
      if (!res.ok) throw new Error(`推荐接口异常: ${res.status}`);
      const data = await res.json();
      console.log('[推荐] 返回数据:', data);
      setRecommended(data.articles || []);
      setRecommendFallback(data.fallback || false);
      setRecommendMsg(data.message || '');
    } catch (error) {
      console.error('[推荐] 加载失败:', error);
      setRecommendError(true);
    } finally {
      setRecommendLoading(false);
      setRecommendFetched(true);
    }
  };

  // 获取弹窗文章详情
  const fetchArticleDetail = async (id) => {
    setDetailLoading(true);
    try {
      const res = await fetch(`http://localhost:8000/api/articles/${id}`);
      if (!res.ok) throw new Error('获取详情失败');
      const data = await res.json();
      setCurrentArticle(data);
      setModalVisible(true);
      setArticles(prev => prev.map(item => item.id === id ? { ...item, view_count: item.view_count + 1 } : item));
    } catch (error) {
      console.error("加载文章详情失败:", error);
      message.error("加载文章详情失败");
    } finally {
      setDetailLoading(false);
    }
  };

  // 🌟 点赞：乐观更新 UI + 写入 localStorage + 持久化到后端
  const handleLike = async (id, e) => {
    e.stopPropagation();
    if (likedArticles.has(id)) {
      message.warning("您已经赞过这篇文章啦！");
      return;
    }

    // 乐观更新
    const newSet = new Set(likedArticles).add(id);
    setLikedArticles(newSet);
    localStorage.setItem(getLikedKey(), JSON.stringify([...newSet]));

    setArticles(prev => prev.map(item => item.id === id ? { ...item, likes: item.likes + 1 } : item));
    setRecommended(prev => prev.map(item => item.id === id ? { ...item, likes: item.likes + 1 } : item));
    if (currentArticle?.id === id) {
      setCurrentArticle(prev => ({ ...prev, likes: prev.likes + 1 }));
    }
    message.success("点赞成功！感谢支持 ❤️");

    // 持久化到后端（需要登录）
    try {
      const token = localStorage.getItem('access_token');
      if (token) {
        await fetch(`http://localhost:8000/api/articles/${id}/like`, {
          method: 'POST',
          headers: { 'Authorization': `Bearer ${token}` }
        });
      }
    } catch (error) {
      console.warn("点赞网络异常，本地已记录:", error);
    }
  };

  const getTabIcon = (key) => {
    switch(key) {
      case '辟谣粉碎机': return <SafetyCertificateOutlined />;
      case '用药红绿灯': return <MedicineBoxOutlined />;
      case '硬核诊疗局': return <BookOutlined />;
      case '时令与养生': return <FireOutlined />;
      case '实时热点追踪': return <MessageOutlined />;
      default: return <BookOutlined />;
    }
  };

  const categories = ['辟谣粉碎机', '硬核诊疗局', '用药红绿灯', '时令与养生', '实时热点追踪'];

  // 实时热点 Tab 用 liveArticles，其他 Tab 从数据库文章过滤
  const isLiveTab = activeTab === '实时热点追踪';
  const filteredArticles = isLiveTab ? liveArticles : articles.filter(item => item.category === activeTab);

  const handleTabChange = (tab) => {
    setActiveTab(tab);
    if (tab === '实时热点追踪') fetchLiveArticles();
  };

  return (
    <>
      <style>
        {`
          /* 覆盖 Ant Design Tabs 蓝色，统一改为医疗绿 */
          .ant-tabs-tab.ant-tabs-tab-active .ant-tabs-tab-btn {
            color: #14B8A6 !important;
            text-shadow: 0 0 0.25px #14B8A6 !important;
          }
          .ant-tabs-ink-bar {
            background: #14B8A6 !important;
          }
          .ant-tabs-tab:hover {
            color: #14B8A6 !important;
          }

          /* Markdown 表格样式 */
          .markdown-body table {
            width: 100%;
            border-collapse: collapse;
            margin: 24px 0;
            background-color: #ffffff;
            border-radius: 8px;
            overflow: hidden;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.05);
          }
          .markdown-body th {
            background-color: #F0FDFA; 
            color: #0F172A;
            font-weight: 600;
            text-align: left;
            padding: 14px 16px;
            border-bottom: 2px solid #14B8A6;
          }
          .markdown-body td {
            padding: 14px 16px;
            border-bottom: 1px solid #E2E8F0;
            color: #334155;
            vertical-align: top;
          }
          .markdown-body tr:nth-child(even) {
            background-color: #F8FAFC;
          }
          .markdown-body tr:hover {
            background-color: #F1F5F9;
          }

          /* Q&A 气泡内 Markdown 紧凑排版 */
          .qa-bubble p { margin: 0 0 6px 0; }
          .qa-bubble p:last-child { margin-bottom: 0; }
          .qa-bubble ul, .qa-bubble ol { margin: 6px 0; padding-left: 20px; }
          .qa-bubble li { margin: 2px 0; }
          .qa-bubble strong { color: #0D9488; font-weight: 700; }
          .qa-bubble h1, .qa-bubble h2, .qa-bubble h3, .qa-bubble h4 {
            margin: 8px 0 4px 0; font-size: 14px; font-weight: 700; color: #0F172A;
          }
          .qa-bubble code {
            background: #E2E8F0; color: #0F172A; padding: 1px 5px;
            border-radius: 4px; font-size: 12.5px;
          }
          .qa-bubble blockquote {
            margin: 6px 0; padding: 4px 10px; border-left: 3px solid #14B8A6;
            background: #F0FDFA; color: #475569;
          }
        `}
      </style>

      <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', background: '#F8FAFC' }}>
        
        {/* 顶部导航区 */}
        <div style={{ 
          padding: '24px 40px', 
          background: '#FFFFFF', 
          borderBottom: '1px solid #E2E8F0',
          display: 'flex',
          alignItems: 'center',
          boxShadow: '0 2px 10px rgba(0,0,0,0.02)'
        }}>
          <div 
            onClick={() => navigate('/chat')}
            style={{ 
              width: 40, height: 40, borderRadius: '50%', background: '#F0FDFA', 
              display: 'flex', justifyContent: 'center', alignItems: 'center', 
              marginRight: 20, cursor: 'pointer', color: '#14B8A6',
              transition: 'all 0.3s'
            }}
            onMouseEnter={(e) => { e.currentTarget.style.background = '#14B8A6'; e.currentTarget.style.color = '#FFF'; }}
            onMouseLeave={(e) => { e.currentTarget.style.background = '#F0FDFA'; e.currentTarget.style.color = '#14B8A6'; }}
          >
            <RightOutlined style={{ transform: 'rotate(180deg)', fontSize: '18px' }} />
          </div>
          <div>
            <Title level={3} style={{ margin: 0, color: '#0F172A', fontWeight: 700 }}>健康知识专区</Title>
            <Text type="secondary" style={{ fontSize: '14px' }}>破除医疗迷信，传递硬核科普与前沿健康热点</Text>
          </div>
        </div>

        {/* 主体内容区 */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '32px 40px' }}>
          
          {/* ✨ 个性化推荐区块：始终渲染，内部区分登录/未登录/加载/错误状态 */}
          <div style={{ marginBottom: 40 }}>
              {/* 区块标题 */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 18 }}>
                <div style={{
                  width: 36, height: 36, borderRadius: '50%',
                  background: 'linear-gradient(135deg, #14B8A6, #6366F1)',
                  display: 'flex', alignItems: 'center', justifyContent: 'center'
                }}>
                  <ThunderboltOutlined style={{ color: '#fff', fontSize: 16 }} />
                </div>
                <div>
                  <Title level={5} style={{ margin: 0, color: '#0F172A' }}>
                    {recommendLoading ? '正在为你匹配...' : recommendFallback ? '📈 热门推荐' : '✨ 为你专属推荐'}
                  </Title>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {recommendLoading
                      ? '基于你的健康档案，AI 正在匹配最相关的文章'
                      : recommendFallback
                        ? (recommendMsg || '基于全站热度为你挑选，完善健康档案后可获得个性化推荐')
                        : '基于你的健康档案，由 AI 智能匹配'}
                  </Text>
                </div>
              </div>

              {/* 状态一：未登录 → 引导登录 */}
              {!isLoggedIn && recommendFetched && !recommendLoading && (
                <div
                  onClick={() => navigate('/login')}
                  style={{
                    padding: '16px 20px', background: 'linear-gradient(135deg, #F0FDFA, #EFF6FF)',
                    border: '1px dashed #99F6E4', borderRadius: 12,
                    display: 'flex', alignItems: 'center', gap: 12, cursor: 'pointer'
                  }}
                >
                  <div style={{ fontSize: 28 }}>🔐</div>
                  <div>
                    <div style={{ fontWeight: 600, color: '#0F172A', marginBottom: 2 }}>登录后获取专属推荐</div>
                    <div style={{ fontSize: 13, color: '#64748B' }}>基于你的健康档案，AI 将为你匹配最相关的医学科普文章</div>
                  </div>
                  <Button type="primary" size="small" style={{ marginLeft: 'auto', background: '#14B8A6', border: 'none', borderRadius: 8 }}>
                    立即登录
                  </Button>
                </div>
              )}

              {/* 状态二：加载中 → 骨架屏 */}
              {recommendLoading && (
                <div style={{ display: 'flex', gap: 16 }}>
                  {[1, 2, 3, 4].map(i => (
                    <div key={i} style={{ minWidth: 280, background: '#fff', borderRadius: 14, padding: 20, boxShadow: '0 2px 10px rgba(0,0,0,0.04)' }}>
                      <Skeleton active paragraph={{ rows: 3 }} />
                    </div>
                  ))}
                </div>
              )}

              {/* 状态三：加载失败（需已登录） */}
              {!recommendLoading && recommendFetched && recommendError && isLoggedIn && (
                <div style={{
                  padding: '16px 20px', background: '#FFF7F7', border: '1px dashed #FCA5A5',
                  borderRadius: 12, color: '#DC2626', fontSize: 13,
                  display: 'flex', alignItems: 'center', gap: 8
                }}>
                  <span>⚠️</span>
                  <span>推荐服务暂时不可用，请稍后刷新重试。</span>
                  <Button size="small" type="link" style={{ color: '#14B8A6', padding: 0 }} onClick={fetchRecommended}>重试</Button>
                </div>
              )}

              {/* 状态四：有数据 → 横向滚动卡片 */}
              {!recommendLoading && !recommendError && recommended.length > 0 && (
                <div
                  ref={recommendRef}
                  style={{ display: 'flex', gap: 16, overflowX: 'auto', paddingBottom: 8, scrollbarWidth: 'none' }}
                >
                  {recommended.map((item) => (
                    <div
                      key={item.id}
                      onClick={() => fetchArticleDetail(item.id)}
                      style={{
                        minWidth: 292, maxWidth: 292, cursor: 'pointer',
                        background: '#FFFFFF', borderRadius: 14,
                        padding: '18px 20px',
                        boxShadow: '0 2px 12px rgba(0,0,0,0.05)',
                        border: '1.5px solid #E2E8F0',
                        transition: 'all 0.25s',
                        display: 'flex', flexDirection: 'column', gap: 10,
                        position: 'relative', overflow: 'hidden'
                      }}
                      onMouseEnter={e => {
                        e.currentTarget.style.borderColor = '#14B8A6';
                        e.currentTarget.style.boxShadow = '0 6px 20px rgba(20,184,166,0.15)';
                        e.currentTarget.style.transform = 'translateY(-3px)';
                      }}
                      onMouseLeave={e => {
                        e.currentTarget.style.borderColor = '#E2E8F0';
                        e.currentTarget.style.boxShadow = '0 2px 12px rgba(0,0,0,0.05)';
                        e.currentTarget.style.transform = 'translateY(0)';
                      }}
                    >
                      {/* 顶部渐变装饰条 */}
                      <div style={{
                        position: 'absolute', top: 0, left: 0, right: 0, height: 3,
                        background: 'linear-gradient(90deg, #14B8A6, #6366F1)'
                      }} />
                      <Tag color="cyan" style={{ alignSelf: 'flex-start', border: 'none', fontSize: 11, fontWeight: 600 }}>
                        {item.category}
                      </Tag>
                      <Text strong style={{
                        fontSize: 14, color: '#1E293B', lineHeight: '1.5',
                        display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden'
                      }}>
                        {item.title}
                      </Text>
                      {/* AI 推荐理由气泡 */}
                      {!recommendFallback && item.reason && (
                        <div style={{
                          background: 'linear-gradient(135deg, #F0FDF4, #EFF6FF)',
                          border: '1px solid #D1FAE5', borderRadius: 8,
                          padding: '6px 10px', fontSize: 12, color: '#065F46',
                          display: 'flex', alignItems: 'flex-start', gap: 5
                        }}>
                          <StarOutlined style={{ marginTop: 1, flexShrink: 0 }} />
                          <span>{item.reason}</span>
                        </div>
                      )}
                      {/* 底部统计 */}
                      <div style={{ display: 'flex', gap: 14, marginTop: 'auto' }}>
                        <Text type="secondary" style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 3 }}>
                          <EyeOutlined /> {item.view_count}
                        </Text>
                        <Text
                          style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 3, cursor: 'pointer',
                            color: likedArticles.has(item.id) ? '#14B8A6' : '#94A3B8' }}
                          onClick={e => handleLike(item.id, e)}
                        >
                          {likedArticles.has(item.id) ? <LikeFilled /> : <LikeOutlined />} {item.likes}
                        </Text>
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {/* 状态五：加载完成但空（无文章，且已登录排除未登录情况） */}
              {!recommendLoading && !recommendError && recommendFetched && isLoggedIn && recommended.length === 0 && (
                <div style={{
                  padding: '16px 20px', background: '#F8FAFC', border: '1px dashed #E2E8F0',
                  borderRadius: 12, color: '#94A3B8', fontSize: 13, textAlign: 'center'
                }}>
                  暂无推荐文章，数据库中还没有内容，请先运行 generate_articles.py 生成文章。
                </div>
              )}
          </div>

          {/* 分类 Tabs */}
          <Tabs
            activeKey={activeTab}
            onChange={handleTabChange}
            size="large"
            tabBarStyle={{ marginBottom: 32, borderBottom: '2px solid #E2E8F0' }}
            items={categories.map(cat => ({
              label: (
                <span style={{ fontSize: '16px', fontWeight: activeTab === cat ? 600 : 400, padding: '0 8px' }}>
                  {getTabIcon(cat)} {cat}
                  {cat === '实时热点追踪' && (
                    <span style={{
                      marginLeft: 6, fontSize: 10, fontWeight: 700, color: '#fff',
                      background: '#EF4444', borderRadius: 4, padding: '1px 5px',
                      verticalAlign: 'middle', letterSpacing: 0.5
                    }}>LIVE</span>
                  )}
                </span>
              ),
              key: cat,
            }))}
          />

          {/* 实时热点子标题栏（仅在实时热点 Tab 显示） */}
          {isLiveTab && !liveLoading && liveFetched && (
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
              <Text type="secondary" style={{ fontSize: 13 }}>
                🌐 以下内容由 Tavily 实时联网抓取，点击卡片可跳转原文
              </Text>
              <Button
                size="small" icon={<ReloadOutlined />}
                onClick={() => fetchLiveArticles(true)}
                style={{ borderRadius: 8, color: '#14B8A6', borderColor: '#14B8A6' }}
              >刷新热点</Button>
            </div>
          )}

          {/* 文章瀑布流展示 */}
          {(isLiveTab ? liveLoading : loading) ? (
            <div style={{ display: 'flex', flexDirection: 'column', justifyContent: 'center', alignItems: 'center', height: '400px', gap: 16 }}>
              <Spin size="large" />
              <div style={{ textAlign: 'center' }}>
                <div style={{ fontSize: 16, fontWeight: 600, color: '#14B8A6', marginBottom: 8 }}>
                  {isLiveTab ? '🤖 AI 科普作家正在重塑实时内容...' : '正在为您拉取最新医学科普...'}
                </div>
                {isLiveTab && (
                  <div style={{ fontSize: 13, color: '#94A3B8', lineHeight: '1.8' }}>
                    · 🌐 Tavily 联网抓取最新健康话题<br/>
                    · 🧠 DeepSeek 提取核心观点 + AI 核查打假<br/>
                    · 🎨 万相生成科普封面（首次约 30-60 秒）
                  </div>
                )}
              </div>
            </div>
          ) : filteredArticles.length > 0 ? (
            <div style={{ 
              display: 'grid', 
              gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', 
              gap: '24px' 
            }}>
              {filteredArticles.map((article) => (
                <Card
                  key={article.id}
                  hoverable
                  onClick={() => {
                    if (article.is_live) {
                      // 实时热点：直接用后端已返回的完整内容打开弹窗，不再走 /api/articles/{id} 后端接口
                      setCurrentArticle(article);
                      setModalVisible(true);
                    } else {
                      fetchArticleDetail(article.id);
                    }
                  }}
                  style={{ borderRadius: '16px', overflow: 'hidden', border: 'none', boxShadow: '0 4px 12px rgba(0,0,0,0.05)' }}
                  bodyStyle={{ padding: '20px' }}
                  cover={
                    <div style={{ height: '200px', overflow: 'hidden', position: 'relative' }}>
                      <img
                        alt="cover"
                        src={article.cover_image && article.cover_image.includes('http') ? article.cover_image : getFallbackImage(article.category)}
                        style={{ width: '100%', height: '100%', objectFit: 'cover', transition: 'transform 0.5s' }}
                        onMouseOver={(e) => e.currentTarget.style.transform = 'scale(1.05)'}
                        onMouseOut={(e) => e.currentTarget.style.transform = 'scale(1)'}
                        onError={(e) => { e.target.onerror = null; e.target.src = getFallbackImage(article.category); }}
                      />
                      <div style={{ position: 'absolute', top: 12, left: 12, display: 'flex', gap: 6 }}>
                        <Tag color="cyan" style={{ border: 'none', padding: '4px 8px', borderRadius: '4px', fontWeight: 600 }}>
                          {article.category}
                        </Tag>
                      </div>
                    </div>
                  }
                >
                  <Title level={5} style={{ marginTop: 0, marginBottom: '12px', fontSize: '18px', lineHeight: '1.4', color: '#1E293B', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                    {article.title}
                  </Title>
                  <Text type="secondary" style={{ display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden', marginBottom: '20px', fontSize: '14px', lineHeight: '1.6' }}>
                    {article.summary}
                  </Text>
                  
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderTop: '1px solid #F1F5F9', paddingTop: '16px' }}>
                    {article.is_live ? (
                      /* 实时热点卡片：显示发布时间 + 来源链接 */
                      <>
                        <Text type="secondary" style={{ fontSize: '12px' }}>
                          {article.published_date ? article.published_date.slice(0, 10) : '实时'}
                        </Text>
                        <Text
                          style={{ fontSize: '13px', color: '#14B8A6', display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}
                          onClick={e => { e.stopPropagation(); window.open(article.url, '_blank'); }}
                        >
                          <LinkOutlined /> 查看原文
                        </Text>
                      </>
                    ) : (
                      /* 普通文章卡片：AI 头像 + 阅读数 + 点赞 */
                      <>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                          <Avatar src="https://api.dicebear.com/7.x/notionists/svg?seed=doctor" size="small" style={{ backgroundColor: '#F0FDFA' }} />
                          <Text type="secondary" style={{ fontSize: '12px' }}>AI 极速健康大脑</Text>
                        </div>
                        <div style={{ display: 'flex', gap: '16px' }}>
                          <Text type="secondary" style={{ fontSize: '13px', display: 'flex', alignItems: 'center', gap: '4px' }}>
                            <EyeOutlined /> {article.view_count}
                          </Text>
                          <Text
                            style={{ fontSize: '13px', display: 'flex', alignItems: 'center', gap: '4px', cursor: 'pointer',
                              color: likedArticles.has(article.id) ? '#14B8A6' : '#94A3B8', transition: 'color 0.3s' }}
                            onClick={(e) => handleLike(article.id, e)}
                          >
                            {likedArticles.has(article.id) ? <LikeFilled /> : <LikeOutlined />} {article.likes}
                          </Text>
                        </div>
                      </>
                    )}
                  </div>
                </Card>
              ))}
            </div>
          ) : (
            <div style={{ padding: '60px 0', display: 'flex', justifyContent: 'center' }}>
              <Empty description={<span style={{ color: '#94A3B8' }}>该专区暂无文章发布哦~</span>} />
            </div>
          )}
        </div>
      </div>

      {/* 🌟 文章详情全屏弹窗 (保留极简纯净排版，无顶部大图) */}
      <Modal
        open={modalVisible}
        onCancel={() => { setModalVisible(false); setQaMessages([]); setQaInput(''); }}
        footer={null}
        width={800}
        centered
        destroyOnClose
        closeIcon={<div style={{ width: 32, height: 32, background: '#F1F5F9', borderRadius: '50%', display: 'flex', justifyContent: 'center', alignItems: 'center', color: '#64748B' }}>✖</div>}
        bodyStyle={{ padding: 0, maxHeight: '85vh', overflowY: 'auto' }}
        className="article-detail-modal"
      >
        <style>
          {`
            .article-detail-modal .ant-modal-content {
              border-radius: 20px;
              overflow: hidden;
            }
            .article-detail-modal::-webkit-scrollbar { width: 6px; }
            .article-detail-modal::-webkit-scrollbar-thumb { background: #CBD5E1; border-radius: 10px; }
          `}
        </style>

        {detailLoading || !currentArticle ? (
           <div style={{ height: '500px', display: 'flex', justifyContent: 'center', alignItems: 'center' }}>
             <Spin size="large" tip="正在解码健康真相..." />
           </div>
        ) : (
          <div style={{ padding: '40px', background: '#FFFFFF', borderRadius: '20px' }}>
            <Tag color="cyan" style={{ border: 'none', padding: '4px 10px', borderRadius: '6px', fontWeight: 600, marginBottom: '16px', fontSize: '14px' }}>
              {currentArticle.category}
            </Tag>
            <Title level={2} style={{ color: '#0F172A', marginBottom: 24, fontSize: '28px' }}>
              {currentArticle.title}
            </Title>
            
            <Space style={{ marginBottom: 32, display: 'flex', gap: 24, borderBottom: '1px solid #E2E8F0', paddingBottom: '20px' }}>
              <Space><ClockCircleOutlined style={{ color: '#94A3B8' }} /> <Text type="secondary">{currentArticle.date || '实时'}</Text></Space>
              {currentArticle.is_live ? (
                <>
                  <Tag style={{ border: 'none', fontWeight: 700, background: '#FEE2E2', color: '#DC2626' }}>
                    🔴 实时联网抓取
                  </Tag>
                  {currentArticle.url && (
                    <a href={currentArticle.url} target="_blank" rel="noopener noreferrer"
                       style={{ color: '#14B8A6', fontSize: 13, display: 'flex', alignItems: 'center', gap: 4 }}>
                      <LinkOutlined /> 查看原文
                    </a>
                  )}
                </>
              ) : (
                <>
                  <Space><EyeOutlined style={{ color: '#94A3B8' }} /> <Text type="secondary">{currentArticle.view_count} 次阅读</Text></Space>
                  <Space><LikeFilled style={{ color: '#14B8A6' }} /> <Text type="secondary">{currentArticle.likes} 赞</Text></Space>
                </>
              )}
            </Space>

            <div style={{ fontSize: '16px', lineHeight: '1.9', color: '#334155' }} className="markdown-body">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {currentArticle.content}
              </ReactMarkdown>
            </div>
            
            {/* 📑 免责声明 */}
            <div style={{ marginTop: 60, padding: '24px', background: '#F8FAFC', borderRadius: '12px', borderLeft: '4px solid #14B8A6' }}>
              <Text strong style={{ display: 'block', marginBottom: 8, color: '#0F172A' }}>📑 免责声明与参考文献</Text>
              <Text type="secondary" style={{ fontSize: '13px', lineHeight: '1.6', display: 'block' }}>
                {currentArticle.is_live
                  ? '本内容由 Tavily 实时联网抓取生成。信息仅供参考，具体健康问题请咨询执业医师。'
                  : '本文由"医疗AI引擎"综合国家药典、临床指南及医学核心期刊智能生成。科普内容仅供学习参考，不能替代执业医师的当面诊断。如有明显不适，请立即前往正规医院就诊。'}
              </Text>
              {/* 实时热点不支持点赞（ID 非数据库整数） */}
              {!currentArticle.is_live && (
                <div style={{ display: 'flex', justifyContent: 'center', marginTop: 24 }}>
                  <Button
                    type="primary"
                    size="large"
                    icon={likedArticles.has(currentArticle.id) ? <LikeFilled /> : <LikeOutlined />}
                    onClick={(e) => handleLike(currentArticle.id, e)}
                    style={{
                      borderRadius: '20px', padding: '0 32px',
                      background: likedArticles.has(currentArticle.id) ? '#0D9488' : '#14B8A6',
                      border: 'none', boxShadow: '0 4px 14px rgba(20,184,166,0.3)'
                    }}
                  >
                    {likedArticles.has(currentArticle.id) ? '已赞同本文' : '觉得有用，赞一个'}
                  </Button>
                </div>
              )}
            </div>

            {/* ✨ 文章内 AI 问答区块（置于最底部，免责声明之下） */}
            <div style={{ marginTop: 32, border: '1.5px solid #E2E8F0', borderRadius: 16, overflow: 'hidden' }}>
              {/* 问答区标题栏 */}
              <div style={{
                padding: '14px 20px', background: 'linear-gradient(90deg, #F0FDFA, #ECFDF5)',
                borderBottom: '1px solid #E2E8F0', display: 'flex', alignItems: 'center', gap: 8
              }}>
                <RobotOutlined style={{ color: '#14B8A6', fontSize: 18 }} />
                <span style={{ fontWeight: 700, color: '#0F172A', fontSize: 15 }}>对本文有疑问？问问 AI</span>
                <span style={{ fontSize: 12, color: '#94A3B8', marginLeft: 4 }}>基于文章全文作答</span>
              </div>

              {/* 历史消息列表 */}
              {qaMessages.length > 0 && (
                <div style={{ maxHeight: 320, overflowY: 'auto', padding: '16px 20px', display: 'flex', flexDirection: 'column', gap: 12 }}>
                  {qaMessages.map((msg, idx) => (
                    <div key={idx} style={{ display: 'flex', gap: 10, alignItems: 'flex-start',
                      flexDirection: msg.role === 'user' ? 'row-reverse' : 'row' }}>
                      <div style={{
                        width: 30, height: 30, borderRadius: '50%', flexShrink: 0,
                        background: msg.role === 'user'
                          ? 'linear-gradient(135deg, #0D9488, #14B8A6)'
                          : 'linear-gradient(135deg, #14B8A6, #10B981)',
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                        boxShadow: '0 2px 6px rgba(20,184,166,0.25)'
                      }}>
                        {msg.role === 'user'
                          ? <UserOutlined style={{ color: '#fff', fontSize: 13 }} />
                          : <RobotOutlined style={{ color: '#fff', fontSize: 13 }} />}
                      </div>
                      <div className="qa-bubble" style={{
                        maxWidth: '78%', padding: '10px 14px', borderRadius: 12, fontSize: 14, lineHeight: '1.7',
                        background: msg.role === 'user' ? '#14B8A6' : '#F8FAFC',
                        color: msg.role === 'user' ? '#fff' : '#334155',
                        border: msg.role === 'ai' ? '1px solid #E2E8F0' : 'none',
                        borderTopRightRadius: msg.role === 'user' ? 4 : 12,
                        borderTopLeftRadius: msg.role === 'ai' ? 4 : 12,
                        wordBreak: 'break-word',
                      }}>
                        {msg.role === 'user' ? (
                          msg.content
                        ) : msg.content ? (
                          <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
                        ) : qaLoading && idx === qaMessages.length - 1 ? (
                          <span style={{ color: '#94A3B8' }}>▌ 正在思考...</span>
                        ) : null}
                      </div>
                    </div>
                  ))}
                  <div ref={qaBottomRef} />
                </div>
              )}

              {/* 输入区 */}
              <div style={{ padding: '12px 16px', borderTop: qaMessages.length > 0 ? '1px solid #E2E8F0' : 'none',
                display: 'flex', gap: 10, background: '#FAFAFA' }}>
                <input
                  value={qaInput}
                  onChange={e => setQaInput(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && !e.shiftKey && sendArticleQuestion()}
                  placeholder="输入你对本文的疑问，按 Enter 发送..."
                  disabled={qaLoading}
                  style={{
                    flex: 1, border: '1.5px solid #E2E8F0', borderRadius: 10, padding: '9px 14px',
                    fontSize: 14, outline: 'none', background: '#fff', color: '#334155',
                    transition: 'border-color 0.2s',
                  }}
                  onFocus={e => e.target.style.borderColor = '#14B8A6'}
                  onBlur={e => e.target.style.borderColor = '#E2E8F0'}
                />
                <Button
                  type="primary"
                  icon={<SendOutlined />}
                  loading={qaLoading}
                  onClick={sendArticleQuestion}
                  disabled={!qaInput.trim()}
                  style={{ borderRadius: 10, background: '#14B8A6', border: 'none', height: 40, width: 44, padding: 0 }}
                />
              </div>
            </div>
          </div>
        )}
      </Modal>
    </>
  );
};

export default KnowledgeZone;