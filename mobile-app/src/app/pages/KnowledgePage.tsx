import React, { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { toast } from 'sonner';
import {
  ArrowLeft, ShieldCheck, Pill, BookOpen, Leaf, Radio,
  Eye, Heart, HeartOff, RefreshCw, ExternalLink, Clock,
  Star, Zap, X, ChevronRight, Send, Bot, User,
  Sparkles, TrendingUp,
} from 'lucide-react';

// ─── Design Tokens ──────────────────────────────────────────────────
const T = {
  teal50:  '#edfaf2', teal100: '#d4f5df', teal200: '#afeebf',
  teal300: '#7bd49a', teal400: '#4eba78', teal500: '#32a05f',
  teal600: '#228048', teal700: '#166035', teal800: '#0d4224', teal900: '#061e10',
  slate50:  '#f4fbf6', slate100: '#edf5ef', slate200: '#d8ead9',
  slate300: '#b8ccba', slate400: '#90a892', slate500: '#637065',
  slate600: '#465049', slate700: '#313830', slate800: '#1e2420', slate900: '#0e120f',
  red50: '#fef0f2', red500: '#e06870', red700: '#b84850',
  amber50: '#fef8e6', amber500: '#d4a840', amber600: '#a88028',
  green50: '#edfaf2', green600: '#228048',
  // Cream accent for cards
  cream50: '#fefdf5', cream100: '#faf6e6', cream200: '#f0eac1',
  sky100: '#ddf1fb', sky200: '#b8dff0',
  lav100: '#ece8f8', lav200: '#d4cff5',
};

// ─── Types ──────────────────────────────────────────────────────────
interface Article {
  id: number; title: string; summary?: string; content?: string;
  category: string; cover_image?: string; view_count: number;
  likes: number; date?: string; published_date?: string;
  is_live?: boolean; url?: string; reason?: string;
}

const getLikedKey = () => `liked_articles_${localStorage.getItem('current_username') || 'guest'}`;

const CATEGORIES = ['辟谣粉碎机', '硬核诊疗局', '用药红绿灯', '时令与养生', '实时热点追踪'];

const CAT_ICONS: Record<string, React.ReactNode> = {
  '辟谣粉碎机': <ShieldCheck size={15} />,
  '用药红绿灯': <Pill size={15} />,
  '硬核诊疗局':  <BookOpen size={15} />,
  '时令与养生': <Leaf size={15} />,
  '实时热点追踪': <Radio size={15} />,
};

const FALLBACK_IMGS: Record<string, string> = {
  '辟谣粉碎机': 'https://images.unsplash.com/photo-1576091160399-112ba8d25d1d?auto=format&fit=crop&q=80&w=800',
  '用药红绿灯': 'https://images.unsplash.com/photo-1584308666744-24d5c474f2ae?auto=format&fit=crop&q=80&w=800',
  '硬核诊疗局': 'https://images.unsplash.com/photo-1551076805-e1869043e560?auto=format&fit=crop&q=80&w=800',
  '时令与养生': 'https://images.unsplash.com/photo-1544367567-0f2fcb009e0b?auto=format&fit=crop&q=80&w=800',
  '实时热点追踪': 'https://images.unsplash.com/photo-1504439468489-c8920d786a2b?auto=format&fit=crop&q=80&w=800',
};

const getFallback = (cat: string) => FALLBACK_IMGS[cat] || FALLBACK_IMGS['硬核诊疗局'];

// ─── Article Card ────────────────────────────────────────────────────
const ArticleCard: React.FC<{
  article: Article; liked: boolean;
  onOpen: () => void; onLike: (e: React.MouseEvent) => void;
}> = ({ article, liked, onOpen, onLike }) => (
  <div onClick={onOpen} style={{
    background: 'white', borderRadius: 16, overflow: 'hidden',
    border: `1px solid ${T.slate200}`,
    boxShadow: '0 2px 8px rgba(0,0,0,0.04)',
    cursor: 'pointer', transition: 'all 0.22s',
    display: 'flex', flexDirection: 'column',
  }}
    onMouseEnter={e => { (e.currentTarget as HTMLDivElement).style.boxShadow = `0 8px 24px rgba(77,110,77,0.12)`; (e.currentTarget as HTMLDivElement).style.borderColor = T.teal300; (e.currentTarget as HTMLDivElement).style.transform = 'translateY(-3px)'; }}
    onMouseLeave={e => { (e.currentTarget as HTMLDivElement).style.boxShadow = '0 2px 8px rgba(0,0,0,0.04)'; (e.currentTarget as HTMLDivElement).style.borderColor = T.slate200; (e.currentTarget as HTMLDivElement).style.transform = 'none'; }}
  >
    {/* Cover */}
    <div style={{ height: 190, overflow: 'hidden', position: 'relative', background: T.slate100 }}>
      <img
        src={article.cover_image?.includes('http') ? article.cover_image : getFallback(article.category)}
        alt={article.title}
        style={{ width: '100%', height: '100%', objectFit: 'cover', transition: 'transform 0.4s' }}
        onMouseOver={e => (e.currentTarget.style.transform = 'scale(1.05)')}
        onMouseOut={e => (e.currentTarget.style.transform = 'none')}
        onError={e => { e.currentTarget.src = getFallback(article.category); }}
      />
      {/* Category badge */}
      <div style={{ position: 'absolute', top: 12, left: 12 }}>
        <span style={{ fontSize: 11, fontWeight: 700, padding: '3px 9px', borderRadius: 6, background: 'rgba(255,255,255,0.92)', color: T.teal700, display: 'flex', alignItems: 'center', gap: 4 }}>
          {CAT_ICONS[article.category]} {article.category}
        </span>
      </div>
      {article.is_live && (
        <div style={{ position: 'absolute', top: 12, right: 12 }}>
          <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 4, background: T.red500, color: 'white', letterSpacing: '0.5px' }}>LIVE</span>
        </div>
      )}
    </div>

    {/* Body */}
    <div style={{ flex: 1, padding: '18px 20px 16px', display: 'flex', flexDirection: 'column' }}>
      <h3 style={{ margin: '0 0 10px', fontSize: 16, fontWeight: 700, color: T.slate900, lineHeight: 1.45, display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
        {article.title}
      </h3>
      <p style={{ margin: '0 0 16px', fontSize: 13, color: T.slate500, lineHeight: 1.6, display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden', flex: 1 }}>
        {article.summary}
      </p>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderTop: `1px solid ${T.slate100}`, paddingTop: 12 }}>
        {article.is_live ? (
          <>
            <span style={{ fontSize: 12, color: T.slate400 }}>{article.published_date?.slice(0, 10) || '实时'}</span>
            <span onClick={e => { e.stopPropagation(); window.open(article.url, '_blank'); }}
              style={{ fontSize: 12, color: T.teal600, display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer', fontWeight: 600 }}>
              <ExternalLink size={12} /> 查看原文
            </span>
          </>
        ) : (
          <>
            <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
              <span style={{ fontSize: 12, color: T.slate400, display: 'flex', alignItems: 'center', gap: 4 }}>
                <Eye size={12} /> {article.view_count}
              </span>
              <button onClick={onLike} style={{
                display: 'flex', alignItems: 'center', gap: 4, fontSize: 12,
                color: liked ? T.red500 : T.slate400, background: 'none', border: 'none',
                cursor: 'pointer', fontWeight: liked ? 700 : 400, transition: 'color 0.2s', padding: 0,
              }}>
                {liked ? <Heart size={12} fill={T.red500} /> : <HeartOff size={12} />} {article.likes}
              </button>
            </div>
            <span style={{ fontSize: 11, color: T.slate400 }}>AI 健康大脑</span>
          </>
        )}
      </div>
    </div>
  </div>
);

// ─── Article Modal ────────────────────────────────────────────────────
const ArticleModal: React.FC<{
  article: Article | null; onClose: () => void;
  liked: boolean; onLike: (e: React.MouseEvent) => void;
}> = ({ article, onClose, liked, onLike }) => {
  const [qaMessages, setQaMessages] = useState<{ role: string; content: string }[]>([]);
  const [qaInput, setQaInput] = useState('');
  const [qaLoading, setQaLoading] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => { setQaMessages([]); setQaInput(''); }, [article?.id]);
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [qaMessages]);

  const sendQuestion = async () => {
    if (!qaInput.trim() || qaLoading || !article) return;
    const q = qaInput.trim(); setQaInput('');
    setQaMessages(prev => [...prev, { role: 'user', content: q }, { role: 'ai', content: '' }]);
    setQaLoading(true);
    try {
      const res = await fetch(`http://localhost:8000/api/articles/${article.id}/ask`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question: q }),
      });
      const reader = res.body!.getReader(); const decoder = new TextDecoder(); let buf = '';
      while (true) {
        const { done, value } = await reader.read(); if (done) break;
        buf += decoder.decode(value, { stream: true });
        const parts = buf.split('\n\n'); buf = parts.pop() ?? '';
        for (const part of parts) {
          if (!part.startsWith('data: ')) continue;
          const evt = JSON.parse(part.slice(6));
          if (evt.type === 'chunk') {
            setQaMessages(prev => { const m = [...prev]; m[m.length - 1] = { role: 'ai', content: m[m.length - 1].content + evt.content }; return m; });
            bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
          }
        }
      }
    } catch { setQaMessages(prev => { const m = [...prev]; m[m.length - 1] = { role: 'ai', content: '⚠️ AI 问答暂时不可用' }; return m; }); }
    finally { setQaLoading(false); }
  };

  if (!article) return null;

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 1000, background: 'rgba(15,23,42,0.55)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }} onClick={onClose}>
      <div style={{ background: 'white', borderRadius: 20, width: '100%', maxWidth: 820, maxHeight: '90vh', display: 'flex', flexDirection: 'column', overflow: 'hidden' }} onClick={e => e.stopPropagation()}>
        {/* Modal Header */}
        <div style={{ padding: '20px 28px', borderBottom: `1px solid ${T.slate200}`, display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0 }}>
          <span style={{ fontSize: 11, fontWeight: 700, padding: '3px 9px', borderRadius: 6, background: T.teal50, color: T.teal700, border: `1px solid ${T.teal200}` }}>
            {article.category}
          </span>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <button onClick={onLike} style={{ display: 'flex', alignItems: 'center', gap: 5, padding: '6px 12px', borderRadius: 8, border: `1px solid ${T.slate200}`, background: liked ? T.red50 : 'white', color: liked ? T.red500 : T.slate500, cursor: 'pointer', fontSize: 12, fontWeight: 700 }}>
              {liked ? <Heart size={13} fill={T.red500} /> : <HeartOff size={13} />} {article.likes}
            </button>
            <button onClick={onClose} style={{ width: 32, height: 32, borderRadius: '50%', background: T.slate100, border: 'none', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', color: T.slate600 }}>
              <X size={15} />
            </button>
          </div>
        </div>

        {/* Scrollable Content */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '28px' }}>
          <h2 style={{ margin: '0 0 16px', fontSize: 24, fontWeight: 800, color: T.slate900, lineHeight: 1.35 }}>{article.title}</h2>
          <div style={{ display: 'flex', gap: 20, marginBottom: 24, paddingBottom: 20, borderBottom: `1px solid ${T.slate200}`, alignItems: 'center' }}>
            <span style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 12, color: T.slate400 }}>
              <Clock size={13} /> {article.date || article.published_date || '实时'}
            </span>
            {article.is_live && (
              <span style={{ fontSize: 11, fontWeight: 700, padding: '2px 8px', borderRadius: 4, background: T.red50, color: T.red700, border: `1px solid #FECACA` }}>🔴 实时联网抓取</span>
            )}
            {article.is_live && article.url && (
              <a href={article.url} target="_blank" rel="noopener noreferrer" style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 12, color: T.teal600, fontWeight: 600, textDecoration: 'none' }}>
                <ExternalLink size={12} /> 原文链接
              </a>
            )}
            <span style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 12, color: T.slate400, marginLeft: 'auto' }}>
              <Eye size={13} /> {article.view_count} 次阅读
            </span>
          </div>

          {/* Content */}
          <div style={{ fontSize: 15, color: T.slate700, lineHeight: 1.85 }} className="modal-md">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{article.content || article.summary || ''}</ReactMarkdown>
          </div>

          {/* Q&A Section */}
          <div style={{ marginTop: 32, paddingTop: 24, borderTop: `1px solid ${T.slate200}` }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
              <div style={{ width: 28, height: 28, borderRadius: 8, background: `linear-gradient(135deg, ${T.teal500}, ${T.teal700})`, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <Bot size={14} color="white" />
              </div>
              <span style={{ fontSize: 14, fontWeight: 700, color: T.slate900 }}>就本文提问 AI</span>
            </div>

            {/* Messages */}
            {qaMessages.length > 0 && (
              <div style={{ marginBottom: 14, display: 'flex', flexDirection: 'column', gap: 12, maxHeight: 280, overflowY: 'auto', padding: '12px', background: T.slate50, borderRadius: 12 }}>
                {qaMessages.map((m, i) => (
                  <div key={i} style={{ display: 'flex', justifyContent: m.role === 'user' ? 'flex-end' : 'flex-start', gap: 8, alignItems: 'flex-start' }}>
                    {m.role === 'ai' && <div style={{ width: 24, height: 24, borderRadius: '50%', background: `linear-gradient(135deg, ${T.teal500}, ${T.teal700})`, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}><Bot size={12} color="white" /></div>}
                    <div style={{
                      maxWidth: '80%', padding: '9px 14px', borderRadius: m.role === 'user' ? '16px 4px 16px 16px' : '4px 16px 16px 16px',
                      background: m.role === 'user' ? `linear-gradient(135deg, ${T.teal600}, ${T.teal800})` : 'white',
                      color: m.role === 'user' ? 'white' : T.slate800,
                      border: m.role === 'ai' ? `1px solid ${T.slate200}` : 'none',
                      fontSize: 13, lineHeight: 1.6,
                    }}>
                      {m.role === 'ai' && !m.content ? (
                        <div style={{ display: 'flex', gap: 4 }}>
                          {[0, 0.2, 0.4].map((d, j) => <div key={j} style={{ width: 5, height: 5, borderRadius: '50%', background: T.teal400, animation: 'thinkPulse 1.4s ease-in-out infinite', animationDelay: `${d}s` }} />)}
                        </div>
                      ) : (
                        <div className="qa-md"><ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown></div>
                      )}
                    </div>
                    {m.role === 'user' && <div style={{ width: 24, height: 24, borderRadius: '50%', background: T.slate200, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}><User size={12} color={T.slate500} /></div>}
                  </div>
                ))}
                <div ref={bottomRef} />
              </div>
            )}

            {/* Input */}
            <div style={{ display: 'flex', gap: 8 }}>
              <input
                value={qaInput} onChange={e => setQaInput(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendQuestion(); } }}
                placeholder="就本文内容提问…"
                style={{ flex: 1, height: 42, padding: '0 14px', borderRadius: 10, border: `1.5px solid ${T.slate200}`, fontSize: 14, color: T.slate900, outline: 'none', background: T.slate50, transition: 'border-color 0.2s' }}
                onFocus={e => e.target.style.borderColor = T.teal400}
                onBlur={e => e.target.style.borderColor = T.slate200}
              />
              <button onClick={sendQuestion} disabled={qaLoading || !qaInput.trim()} style={{
                width: 42, height: 42, borderRadius: 10, border: 'none',
                background: (qaLoading || !qaInput.trim()) ? T.slate200 : `linear-gradient(135deg, ${T.teal500}, ${T.teal700})`,
                color: (qaLoading || !qaInput.trim()) ? T.slate400 : 'white',
                cursor: (qaLoading || !qaInput.trim()) ? 'not-allowed' : 'pointer',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}>
                <Send size={15} />
              </button>
            </div>
          </div>
        </div>
      </div>

      <style>{`
        .modal-md p { margin:0 0 10px; }
        .modal-md p:last-child { margin-bottom:0; }
        .modal-md ul,.modal-md ol { margin:8px 0; padding-left:24px; }
        .modal-md li { margin:4px 0; }
        .modal-md strong { color:${T.teal700}; font-weight:700; }
        .modal-md h1,.modal-md h2,.modal-md h3 { margin:16px 0 8px; color:${T.slate900}; }
        .modal-md code { background:${T.slate100}; padding:1px 5px; border-radius:4px; font-size:13px; }
        .modal-md blockquote { margin:10px 0; padding:6px 14px; border-left:3px solid ${T.teal400}; background:${T.teal50}; border-radius:0 6px 6px 0; color:${T.slate600}; }
        .modal-md table { width:100%; border-collapse:collapse; margin:12px 0; }
        .modal-md th { background:${T.teal50}; padding:8px 12px; text-align:left; border-bottom:2px solid ${T.teal200}; font-size:13px; }
        .modal-md td { padding:8px 12px; border-bottom:1px solid ${T.slate200}; font-size:13px; color:${T.slate700}; }
        .qa-md p { margin:0; }
        @keyframes thinkPulse { 0%,60%,100% { transform:scale(0.7); opacity:0.4; } 30% { transform:scale(1.3); opacity:1; } }
      `}</style>
    </div>
  );
};

// ─── Skeleton Card ────────────────────────────────────────────────────
const SkeletonCard: React.FC = () => (
  <div style={{ background: 'white', borderRadius: 16, overflow: 'hidden', border: `1px solid ${T.slate200}` }}>
    <div style={{ height: 190, background: T.slate100, animation: 'shimmer 1.5s infinite linear', backgroundSize: '200% 100%' }} />
    <div style={{ padding: '18px 20px' }}>
      <div style={{ height: 16, background: T.slate200, borderRadius: 8, marginBottom: 10 }} />
      <div style={{ height: 12, background: T.slate100, borderRadius: 8, marginBottom: 6 }} />
      <div style={{ height: 12, background: T.slate100, borderRadius: 8, width: '70%' }} />
    </div>
  </div>
);

// ─── Main Component ──────────────────────────────────────────────────
export const KnowledgePage: React.FC = () => {
  const navigate = useNavigate();
  const [articles, setArticles] = useState<Article[]>([]);
  const [loading, setLoading] = useState(false);
  const [activeTab, setActiveTab] = useState('辟谣粉碎机');
  const [modalArticle, setModalArticle] = useState<Article | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [likedArticles, setLikedArticles] = useState<Set<number>>(() => {
    try { const s = localStorage.getItem(getLikedKey()); return s ? new Set(JSON.parse(s)) : new Set(); } catch { return new Set(); }
  });
  const [liveArticles, setLiveArticles] = useState<Article[]>([]);
  const [liveLoading, setLiveLoading] = useState(false);
  const [liveFetched, setLiveFetched] = useState(false);
  const [isLoggedIn, setIsLoggedIn] = useState(() => !!localStorage.getItem('access_token'));
  const [recommended, setRecommended] = useState<Article[]>([]);
  const [recommendLoading, setRecommendLoading] = useState(false);
  const [recommendFetched, setRecommendFetched] = useState(false);
  const [recommendFallback, setRecommendFallback] = useState(false);
  const [recommendMsg, setRecommendMsg] = useState('');
  const [recommendError, setRecommendError] = useState(false);
  const recommendRef = useRef<HTMLDivElement>(null);

  useEffect(() => { fetchArticles(); fetchRecommended(); }, []);

  const fetchArticles = async () => {
    setLoading(true);
    try {
      const res = await fetch('http://localhost:8000/api/articles');
      if (!res.ok) throw new Error();
      setArticles(await res.json());
    } catch { toast.error('加载文章失败，请检查后端服务'); }
    finally { setLoading(false); }
  };

  const fetchLiveArticles = async (forceRefresh = false) => {
    if (liveFetched && !forceRefresh) return;
    setLiveLoading(true);
    try {
      const url = `http://localhost:8000/api/articles/hot-realtime${forceRefresh ? '?refresh=true' : ''}`;
      const res = await fetch(url);
      if (!res.ok) throw new Error();
      const data = await res.json();
      setLiveArticles(data.articles || []);
      if (data.cached) toast.info(`命中缓存（${data.cache_age_min} 分钟前抓取）`);
      else toast.success(`已完成 ${data.articles?.length ?? 0} 篇 AI 重塑`);
    } catch { toast.error('实时热点加载失败'); }
    finally { setLiveLoading(false); setLiveFetched(true); }
  };

  const fetchRecommended = async () => {
    const token = localStorage.getItem('access_token');
    if (!token) { setIsLoggedIn(false); setRecommendFetched(true); return; }
    setIsLoggedIn(true); setRecommendLoading(true); setRecommendError(false);
    try {
      const res = await fetch('http://localhost:8000/api/articles/recommended', { headers: { 'Authorization': `Bearer ${token}` } });
      if (res.status === 401) { localStorage.removeItem('access_token'); localStorage.removeItem('current_username'); setIsLoggedIn(false); setRecommendFetched(true); setRecommendLoading(false); return; }
      if (!res.ok) throw new Error();
      const data = await res.json();
      setRecommended(data.articles || []); setRecommendFallback(data.fallback || false); setRecommendMsg(data.message || '');
    } catch { setRecommendError(true); }
    finally { setRecommendLoading(false); setRecommendFetched(true); }
  };

  const fetchArticleDetail = async (id: number) => {
    setDetailLoading(true);
    try {
      const res = await fetch(`http://localhost:8000/api/articles/${id}`);
      if (!res.ok) throw new Error();
      const data = await res.json();
      setModalArticle(data);
      setArticles(prev => prev.map(a => a.id === id ? { ...a, view_count: a.view_count + 1 } : a));
    } catch { toast.error('加载文章详情失败'); }
    finally { setDetailLoading(false); }
  };

  const handleLike = async (id: number, e: React.MouseEvent) => {
    e.stopPropagation();
    if (likedArticles.has(id)) { toast.warning('您已赞过这篇文章！'); return; }
    const newSet = new Set(likedArticles).add(id);
    setLikedArticles(newSet);
    localStorage.setItem(getLikedKey(), JSON.stringify([...newSet]));
    setArticles(prev => prev.map(a => a.id === id ? { ...a, likes: a.likes + 1 } : a));
    setRecommended(prev => prev.map(a => a.id === id ? { ...a, likes: a.likes + 1 } : a));
    if (modalArticle?.id === id) setModalArticle(prev => prev ? { ...prev, likes: prev.likes + 1 } : null);
    toast.success('点赞成功！❤️');
    try {
      const token = localStorage.getItem('access_token');
      if (token) await fetch(`http://localhost:8000/api/articles/${id}/like`, { method: 'POST', headers: { 'Authorization': `Bearer ${token}` } });
    } catch { /* silent */ }
  };

  const handleTabChange = (tab: string) => {
    setActiveTab(tab);
    if (tab === '实时热点追踪') fetchLiveArticles();
  };

  const isLiveTab = activeTab === '实时热点追踪';
  const filteredArticles = isLiveTab ? liveArticles : articles.filter(a => a.category === activeTab);

  return (
    <>
      <div style={{ minHeight: '100vh', background: '#f4fbf6', fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif' }}>

        {/* ── Header ── */}
        <div style={{ background: 'rgba(255,255,255,0.9)', backdropFilter: 'blur(10px)', borderBottom: `1px solid ${T.slate200}`, position: 'sticky', top: 0, zIndex: 10 }}>
          <div style={{ maxWidth: 1200, margin: '0 auto', padding: '16px 32px', display: 'flex', alignItems: 'center', gap: 16 }}>
            <button onClick={() => navigate('/chat')} style={{ width: 38, height: 38, borderRadius: 10, background: T.slate100, border: 'none', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', color: T.slate600 }}>
              <ArrowLeft size={18} />
            </button>
            <div>
              <div style={{ fontSize: 17, fontWeight: 800, color: T.slate900 }}>健康知识专区</div>
              <div style={{ fontSize: 12, color: T.slate500 }}>破除医疗迷信，传递硬核科普与前沿健康热点</div>
            </div>
          </div>
        </div>

        <div style={{ maxWidth: 1200, margin: '0 auto', padding: '28px 32px' }}>

          {/* ── Personalized Recommendation ── */}
          <div style={{ marginBottom: 36 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16 }}>
              <div style={{ width: 32, height: 32, borderRadius: 10, background: `linear-gradient(135deg, ${T.teal500}, ${T.teal700})`, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <Sparkles size={15} color="white" />
              </div>
              <div>
                <div style={{ fontSize: 15, fontWeight: 700, color: T.slate900 }}>
                  {recommendLoading ? '正在为你匹配…' : recommendFallback ? '热门推荐' : '为你专属推荐'}
                </div>
                <div style={{ fontSize: 12, color: T.slate500 }}>
                  {recommendLoading ? '基于健康档案，AI 正在匹配相关文章' : recommendFallback ? (recommendMsg || '完善健康档案后可获得个性化推荐') : '基于你的健康档案，AI 智能匹配'}
                </div>
              </div>
            </div>

            {/* Not logged in */}
            {!isLoggedIn && recommendFetched && !recommendLoading && (
              <div onClick={() => navigate('/login')} style={{ padding: '14px 20px', background: T.teal50, border: `1px dashed ${T.teal200}`, borderRadius: 12, display: 'flex', alignItems: 'center', gap: 14, cursor: 'pointer' }}>
                <span style={{ fontSize: 24 }}>🔐</span>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 14, fontWeight: 700, color: T.slate900, marginBottom: 2 }}>登录后获取专属推荐</div>
                  <div style={{ fontSize: 12, color: T.slate500 }}>AI 将基于您的健康档案匹配最相关的医学科普文章</div>
                </div>
                <button style={{ padding: '7px 16px', borderRadius: 8, background: `linear-gradient(135deg, ${T.teal500}, ${T.teal700})`, color: 'white', border: 'none', cursor: 'pointer', fontSize: 12, fontWeight: 700 }}>立即登录</button>
              </div>
            )}

            {/* Loading skeletons */}
            {recommendLoading && (
              <div style={{ display: 'flex', gap: 14, overflow: 'hidden' }}>
                {[1, 2, 3, 4].map(i => (
                  <div key={i} style={{ minWidth: 280, background: 'white', borderRadius: 14, padding: 18, border: `1px solid ${T.slate200}` }}>
                    <div style={{ height: 10, background: T.slate200, borderRadius: 6, marginBottom: 10 }} />
                    <div style={{ height: 12, background: T.slate100, borderRadius: 6, marginBottom: 8, width: '85%' }} />
                    <div style={{ height: 12, background: T.slate100, borderRadius: 6, width: '65%' }} />
                  </div>
                ))}
              </div>
            )}

            {/* Error */}
            {!recommendLoading && recommendFetched && recommendError && isLoggedIn && (
              <div style={{ padding: '14px 18px', background: '#FEF2F2', border: '1px dashed #FECACA', borderRadius: 12, display: 'flex', alignItems: 'center', gap: 10, fontSize: 13, color: T.red700 }}>
                <span>⚠️ 推荐服务暂时不可用</span>
                <button onClick={fetchRecommended} style={{ color: T.teal600, background: 'none', border: 'none', cursor: 'pointer', fontWeight: 700, fontSize: 13 }}>重试</button>
              </div>
            )}

            {/* Recommendation cards */}
            {!recommendLoading && !recommendError && recommended.length > 0 && (
              <div ref={recommendRef} style={{ display: 'flex', gap: 14, overflowX: 'auto', paddingBottom: 6, scrollbarWidth: 'none' }}>
                {recommended.map(item => (
                  <div key={item.id} onClick={() => fetchArticleDetail(item.id)} style={{
                    minWidth: 280, maxWidth: 280, cursor: 'pointer', background: 'white',
                    borderRadius: 14, padding: '16px 18px', border: `1px solid ${T.slate200}`,
                    boxShadow: '0 2px 8px rgba(0,0,0,0.04)', transition: 'all 0.2s',
                    display: 'flex', flexDirection: 'column', gap: 8, position: 'relative', overflow: 'hidden',
                  }}
                    onMouseEnter={e => { (e.currentTarget as HTMLDivElement).style.borderColor = T.teal400; (e.currentTarget as HTMLDivElement).style.boxShadow = `0 6px 18px rgba(77,110,77,0.1)`; (e.currentTarget as HTMLDivElement).style.transform = 'translateY(-2px)'; }}
                    onMouseLeave={e => { (e.currentTarget as HTMLDivElement).style.borderColor = T.slate200; (e.currentTarget as HTMLDivElement).style.boxShadow = '0 2px 8px rgba(0,0,0,0.04)'; (e.currentTarget as HTMLDivElement).style.transform = 'none'; }}
                  >
                    {/* Top accent bar */}
                    <div style={{ position: 'absolute', top: 0, left: 0, right: 0, height: 3, background: `linear-gradient(90deg, ${T.teal500}, ${T.teal700})` }} />
                    <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 5, background: T.teal50, color: T.teal700, border: `1px solid ${T.teal100}`, alignSelf: 'flex-start', marginTop: 4 }}>{item.category}</span>
                    <div style={{ fontSize: 13, fontWeight: 700, color: T.slate900, lineHeight: 1.5, display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>{item.title}</div>
                    {!recommendFallback && item.reason && (
                      <div style={{ background: T.teal50, border: `1px solid ${T.teal100}`, borderRadius: 8, padding: '5px 8px', fontSize: 11, color: T.teal700, display: 'flex', alignItems: 'flex-start', gap: 5 }}>
                        <Star size={10} style={{ marginTop: 1, flexShrink: 0 }} /> <span>{item.reason}</span>
                      </div>
                    )}
                    <div style={{ display: 'flex', gap: 12, marginTop: 'auto' }}>
                      <span style={{ fontSize: 11, color: T.slate400, display: 'flex', alignItems: 'center', gap: 3 }}><Eye size={11} /> {item.view_count}</span>
                      <span onClick={e => { e.stopPropagation(); handleLike(item.id, e); }} style={{ fontSize: 11, color: likedArticles.has(item.id) ? T.red500 : T.slate400, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 3, fontWeight: likedArticles.has(item.id) ? 700 : 400 }}>
                        {likedArticles.has(item.id) ? <Heart size={11} fill={T.red500} /> : <HeartOff size={11} />} {item.likes}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* ── Category Tabs ── */}
          <div style={{ borderBottom: `2px solid ${T.slate200}`, marginBottom: 28, display: 'flex', gap: 0 }}>
            {CATEGORIES.map(cat => (
              <button key={cat} onClick={() => handleTabChange(cat)} style={{
                padding: '10px 18px', border: 'none', background: 'none', cursor: 'pointer',
                fontSize: 14, fontWeight: activeTab === cat ? 700 : 500,
                color: activeTab === cat ? T.teal600 : T.slate500,
                borderBottom: `2px solid ${activeTab === cat ? T.teal600 : 'transparent'}`,
                marginBottom: -2, transition: 'all 0.18s',
                display: 'flex', alignItems: 'center', gap: 6,
              }}>
                <span style={{ color: activeTab === cat ? T.teal600 : T.slate400 }}>{CAT_ICONS[cat]}</span>
                {cat}
                {cat === '实时热点追踪' && (
                  <span style={{ fontSize: 9, fontWeight: 800, padding: '1px 5px', borderRadius: 4, background: T.red500, color: 'white', letterSpacing: '0.5px' }}>LIVE</span>
                )}
              </button>
            ))}
          </div>

          {/* Live tab controls */}
          {isLiveTab && !liveLoading && liveFetched && (
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
              <span style={{ fontSize: 13, color: T.slate500, display: 'flex', alignItems: 'center', gap: 6 }}>
                <TrendingUp size={13} /> 以下内容由 Tavily 实时联网抓取，AI 科普重塑
              </span>
              <button onClick={() => fetchLiveArticles(true)} style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 14px', borderRadius: 8, border: `1.5px solid ${T.teal500}`, background: T.teal50, color: T.teal700, cursor: 'pointer', fontSize: 12, fontWeight: 700 }}>
                <RefreshCw size={12} /> 刷新热点
              </button>
            </div>
          )}

          {/* Articles Grid */}
          {(isLiveTab ? liveLoading : loading) ? (
            <div>
              {isLiveTab && (
                <div style={{ textAlign: 'center', padding: '32px 0', marginBottom: 24 }}>
                  <div style={{ fontSize: 15, fontWeight: 700, color: T.teal600, marginBottom: 10 }}>🤖 AI 科普作家正在重塑实时内容…</div>
                  <div style={{ fontSize: 13, color: T.slate400, lineHeight: 2 }}>
                    · 🌐 Tavily 联网抓取最新健康话题<br />
                    · 🧠 DeepSeek 提取核心观点 + AI 核查<br />
                    · 🎨 万相生成科普封面（首次约 30-60 秒）
                  </div>
                </div>
              )}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 22 }}>
                {[1, 2, 3, 4, 5, 6].map(i => <SkeletonCard key={i} />)}
              </div>
            </div>
          ) : filteredArticles.length > 0 ? (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 22 }}>
              {filteredArticles.map(article => (
                <ArticleCard
                  key={article.id} article={article}
                  liked={likedArticles.has(article.id)}
                  onOpen={() => {
                    if (article.is_live) { setModalArticle(article); }
                    else { fetchArticleDetail(article.id); }
                  }}
                  onLike={e => handleLike(article.id, e)}
                />
              ))}
            </div>
          ) : (
            <div style={{ textAlign: 'center', padding: '64px 0', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12 }}>
              <BookOpen size={40} color={T.slate300} />
              <div style={{ fontSize: 16, fontWeight: 600, color: T.slate400 }}>该专区暂无文章</div>
              <div style={{ fontSize: 13, color: T.slate400 }}>先运行 generate_articles.py 生成文章内容</div>
            </div>
          )}
        </div>
      </div>

      {/* Detail Modal */}
      {detailLoading && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 1000, background: 'rgba(15,23,42,0.55)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <div style={{ background: 'white', borderRadius: 16, padding: '40px 60px', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 14 }}>
            <div style={{ width: 36, height: 36, border: `3px solid ${T.slate200}`, borderTopColor: T.teal500, borderRadius: '50%', animation: 'spin360 0.8s linear infinite' }} />
            <span style={{ color: T.slate600, fontSize: 14 }}>正在解码健康真相…</span>
          </div>
        </div>
      )}
      <ArticleModal
        article={modalArticle} onClose={() => setModalArticle(null)}
        liked={modalArticle ? likedArticles.has(modalArticle.id) : false}
        onLike={e => modalArticle && handleLike(modalArticle.id, e)}
      />
      <style>{`
        @keyframes spin360 { to { transform:rotate(360deg); } }
        @keyframes shimmer { 0% { background-position:200% 0; } 100% { background-position:-200% 0; } }
      `}</style>
    </>
  );
};