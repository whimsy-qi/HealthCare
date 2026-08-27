import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Button,
  Card,
  Col,
  Divider,
  Empty,
  Input,
  message,
  Modal,
  Row,
  Skeleton,
  Space,
  Spin,
  Tag,
  Typography,
} from 'antd';
import {
  ArrowLeftOutlined,
  BookOutlined,
  ClockCircleOutlined,
  EyeOutlined,
  FireOutlined,
  HeartOutlined,
  LikeFilled,
  LikeOutlined,
  MedicineBoxOutlined,
  SafetyCertificateOutlined,
  SearchOutlined,
  SendOutlined,
  StarFilled,
  StarOutlined,
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { API_BASE, apiUrl } from '../config/api';

const { Title, Text, Paragraph } = Typography;

const CATEGORIES = [
  { key: '全部', icon: <BookOutlined /> },
  { key: '辟谣粉碎机', icon: <SafetyCertificateOutlined /> },
  { key: '硬核诊疗局', icon: <BookOutlined /> },
  { key: '用药红绿灯', icon: <MedicineBoxOutlined /> },
  { key: '时令与养生', icon: <FireOutlined /> },
  { key: '专家科普', icon: <SafetyCertificateOutlined /> },
];

const getToken = () => localStorage.getItem('access_token');

const authHeaders = () => {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
};

const unwrapArticlePayload = (payload) => payload?.item || payload?.article || payload || {};

const normalizeArticle = (article = {}) => ({
  ...article,
  title: article.title || '',
  summary: article.summary || '',
  content: article.content || article.body || '',
  category: article.category || article.cat || '硬核诊疗局',
  cover_image: article.cover_image && article.cover_image.startsWith('/') ? apiUrl(article.cover_image) : article.cover_image,
  view_count: article.view_count ?? article.views ?? 0,
  likes: article.likes ?? 0,
  tags: Array.isArray(article.tags) ? article.tags : [],
  related_entities: Array.isArray(article.related_entities) ? article.related_entities : [],
  sources: Array.isArray(article.sources) ? article.sources : [],
  reading_time: article.reading_time || 3,
  risk_level: article.risk_level || 'low',
  is_favorited: !!article.is_favorited,
  is_expert: !!article.is_expert,
});

const fallbackImage = (category) => {
  const map = {
    辟谣粉碎机: 'https://images.unsplash.com/photo-1576091160399-112ba8d25d1d?auto=format&fit=crop&q=80&w=900',
    硬核诊疗局: 'https://images.unsplash.com/photo-1551076805-e1869043e560?auto=format&fit=crop&q=80&w=900',
    用药红绿灯: 'https://images.unsplash.com/photo-1584308666744-24d5c474f2ae?auto=format&fit=crop&q=80&w=900',
    时令与养生: 'https://images.unsplash.com/photo-1544367567-0f2fcb009e0b?auto=format&fit=crop&q=80&w=900',
    专家科普: 'https://images.unsplash.com/photo-1576091160550-2173dba999ef?auto=format&fit=crop&q=80&w=900',
  };
  return map[category] || map.硬核诊疗局;
};

const Cover = ({ article, height = 180 }) => {
  const preferredSrc = article.cover_image || article.fallback_cover_image || fallbackImage(article.category);
  const [failedImages, setFailedImages] = useState(new Set());
  const fallbackSrc = fallbackImage(article.category);
  const src = !failedImages.has(preferredSrc) ? preferredSrc : (!failedImages.has(fallbackSrc) ? fallbackSrc : '');
  const imageUnavailable = !src;
  return (
    <div style={{ height, borderRadius: 16, overflow: 'hidden', background: '#eef5dd', position: 'relative' }}>
      {imageUnavailable ? (
        <div style={{
          width: '100%',
          height: '100%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          padding: 24,
          background: 'linear-gradient(135deg, #eff8da, #dceec1)',
          color: '#2f5a2e',
          fontWeight: 800,
          textAlign: 'center',
        }}>
          {article.title || '健康科普'}
        </div>
      ) : (
        <img
          src={src}
          alt={article.title}
          style={{ width: '100%', height: '100%', objectFit: 'cover', objectPosition: 'center center', display: 'block' }}
          onError={() => setFailedImages((prev) => new Set(prev).add(src))}
        />
      )}
      <Tag style={{ position: 'absolute', left: 12, top: 12, border: 'none', borderRadius: 999, fontWeight: 700 }} color="green">
        {article.category || '健康科普'}
      </Tag>
    </div>
  );
};

const ArticleCard = ({ article, onOpen, onLike, onFavorite, liked, favorited, compact = false }) => (
  <Card
    hoverable
    bodyStyle={{ padding: compact ? 10 : 12 }}
    style={{ borderRadius: 16, overflow: 'hidden' }}
    cover={<Cover article={article} height={compact ? 155 : 180} />}
    onClick={() => onOpen(article)}
  >
    <Space size={6} wrap style={{ marginBottom: 7 }}>
      <Tag color="green">{article.category}</Tag>
      <Tag>{article.reading_time} 分钟读完</Tag>
    </Space>
    <Title level={5} style={{ margin: 0, lineHeight: 1.34, fontSize: compact ? 14.5 : 15.5 }}>
      <span style={{ display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
        {article.title}
      </span>
    </Title>
    <Paragraph type="secondary" style={{ marginTop: 7, marginBottom: 0, fontSize: 13, lineHeight: 1.55 }} ellipsis={{ rows: 2 }}>
      {article.summary}
    </Paragraph>
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 10 }}>
      <Space size={12} onClick={(e) => e.stopPropagation()}>
        <Text type="secondary"><EyeOutlined /> {article.view_count}</Text>
        {!article.is_expert && (
          <Button size="small" type="text" icon={liked ? <LikeFilled /> : <LikeOutlined />} onClick={(e) => onLike(article, e)}>
            {article.likes + (liked ? 1 : 0)}
          </Button>
        )}
      </Space>
      {!article.is_expert && (
        <Button
          size="small"
          type="text"
          icon={favorited ? <StarFilled /> : <StarOutlined />}
          onClick={(e) => onFavorite(article, e)}
        />
      )}
    </div>
  </Card>
);

const KnowledgeZone = () => {
  const navigate = useNavigate();
  const [activeTab, setActiveTab] = useState('全部');
  const [query, setQuery] = useState('');
  const [articles, setArticles] = useState([]);
  const [articleTotal, setArticleTotal] = useState(0);
  const [recommended, setRecommended] = useState([]);
  const [expertArticles, setExpertArticles] = useState([]);
  const [loading, setLoading] = useState(false);
  const [expertLoading, setExpertLoading] = useState(false);
  const [selectedArticle, setSelectedArticle] = useState(null);
  const [modalVisible, setModalVisible] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [liked, setLiked] = useState(new Set());
  const [favorited, setFavorited] = useState(new Set());
  const [qaMessages, setQaMessages] = useState([]);
  const [qaInput, setQaInput] = useState('');
  const [qaLoading, setQaLoading] = useState(false);
  const readStartRef = useRef(0);
  const qaBottomRef = useRef(null);

  const markFavorites = useCallback((list) => {
    setFavorited((prev) => {
      const next = new Set(prev);
      list.forEach((item) => {
        if (item.is_favorited) next.add(item.id);
      });
      return next;
    });
  }, []);

  const track = useCallback((payload) => {
    fetch(`${API_BASE}/api/articles/track`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify(payload),
    }).catch(() => {});
  }, []);

  const fetchArticles = useCallback(async () => {
    if (activeTab === '专家科普') return;
    setLoading(true);
    try {
      const params = new URLSearchParams({
        sort: query.trim() ? 'relevance' : 'latest',
        page_size: '30',
      });
      if (activeTab !== '全部') params.set('category', activeTab);
      if (query.trim()) params.set('q', query.trim());
      const res = await fetch(`${API_BASE}/api/articles/search?${params.toString()}`, { headers: authHeaders() });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const list = (data.items || []).map(normalizeArticle);
      setArticles(list);
      setArticleTotal(data.total || list.length);
      markFavorites(list);
      if (query.trim()) track({ event_type: 'search', query: query.trim(), meta_data: { category: activeTab, surface: 'web' } });
    } catch {
      message.error('文章加载失败，请检查后端服务');
      setArticles([]);
      setArticleTotal(0);
    } finally {
      setLoading(false);
    }
  }, [activeTab, query, markFavorites, track]);

  const fetchRecommended = useCallback(async () => {
    if (!getToken()) return;
    try {
      const res = await fetch(`${API_BASE}/api/articles/recommended`, { headers: authHeaders() });
      if (!res.ok) return;
      const data = await res.json();
      const list = (data.articles || []).map(normalizeArticle);
      setRecommended(list);
      markFavorites(list);
    } catch {
      setRecommended([]);
    }
  }, [markFavorites]);

  const fetchExpertArticles = useCallback(async () => {
    setExpertLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/health-articles?limit=50`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const list = (data.items || []).map((item) => normalizeArticle({ ...item, category: '专家科普', is_expert: true }));
      setExpertArticles(list);
    } catch {
      message.error('专家科普加载失败');
      setExpertArticles([]);
    } finally {
      setExpertLoading(false);
    }
  }, []);

  useEffect(() => {
    const t = window.setTimeout(fetchArticles, query.trim() ? 260 : 0);
    return () => window.clearTimeout(t);
  }, [fetchArticles, query]);

  useEffect(() => {
    fetchRecommended();
  }, [fetchRecommended]);

  useEffect(() => {
    if (activeTab === '专家科普' && expertArticles.length === 0) fetchExpertArticles();
  }, [activeTab, expertArticles.length, fetchExpertArticles]);

  const openArticle = async (article) => {
    setDetailLoading(true);
    readStartRef.current = Date.now();
    setQaMessages([]);
    try {
      if (article.is_expert) {
        const res = await fetch(`${API_BASE}/api/health-articles/${article.id}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const payload = await res.json();
        const detail = normalizeArticle({
          ...article,
          ...unwrapArticlePayload(payload),
          category: '专家科普',
          is_expert: true,
        });
        setSelectedArticle(detail);
        setModalVisible(true);
        return;
      }
      const res = await fetch(`${API_BASE}/api/articles/${article.id}`, { headers: authHeaders() });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const detail = normalizeArticle(await res.json());
      setSelectedArticle(detail);
      setModalVisible(true);
      track({ event_type: 'click', article_id: detail.id, meta_data: { surface: 'web' } });
    } catch {
      message.error('文章详情加载失败');
    } finally {
      setDetailLoading(false);
    }
  };

  const closeArticle = () => {
    if (selectedArticle?.id) {
      track({
        event_type: 'read',
        article_id: selectedArticle.id,
        duration_ms: Math.max(0, Date.now() - readStartRef.current),
        meta_data: { surface: 'web_detail' },
      });
    }
    setModalVisible(false);
    setSelectedArticle(null);
  };

  const handleLike = async (article, e) => {
    e.stopPropagation();
    if (liked.has(article.id) || article.is_expert) return;
    setLiked((prev) => new Set(prev).add(article.id));
    setArticles((prev) => prev.map((item) => item.id === article.id ? { ...item, likes: item.likes + 1 } : item));
    setRecommended((prev) => prev.map((item) => item.id === article.id ? { ...item, likes: item.likes + 1 } : item));
    try {
      await fetch(`${API_BASE}/api/articles/${article.id}/like`, { method: 'POST', headers: authHeaders() });
    } catch {
      // Like is optimistic. Tracking failure should not block reading.
    }
  };

  const handleFavorite = async (article, e) => {
    e.stopPropagation();
    if (article.is_expert) return;
    if (!getToken()) {
      message.warning('登录后可以收藏文章');
      return;
    }
    const nextState = !favorited.has(article.id);
    setFavorited((prev) => {
      const next = new Set(prev);
      nextState ? next.add(article.id) : next.delete(article.id);
      return next;
    });
    try {
      await fetch(`${API_BASE}/api/articles/${article.id}/favorite`, {
        method: nextState ? 'POST' : 'DELETE',
        headers: authHeaders(),
      });
      message.success(nextState ? '已收藏' : '已取消收藏');
    } catch {
      message.error('收藏操作失败');
    }
  };

  const sendArticleQuestion = async () => {
    if (!qaInput.trim() || qaLoading || !selectedArticle?.id || selectedArticle.is_expert) return;
    const question = qaInput.trim();
    setQaInput('');
    setQaLoading(true);
    setQaMessages((prev) => [...prev, { role: 'user', content: question }, { role: 'ai', content: '' }]);
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), 45000);
    const replaceLastAiMessage = (content) => {
      setQaMessages((prev) => {
        const next = [...prev];
        next[next.length - 1] = { role: 'ai', content };
        return next;
      });
    };
    try {
      const res = await fetch(`${API_BASE}/api/articles/${selectedArticle.id}/ask`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ question }),
        signal: controller.signal,
      });
      if (!res.ok) throw new Error(`article ask failed: ${res.status}`);
      if (!res.body) throw new Error('empty stream');
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let hasContent = false;
      const handleEvent = (part) => {
        const dataLine = part.split('\n').find((line) => line.startsWith('data: '));
        if (!dataLine) return;
        const evt = JSON.parse(dataLine.slice(6));
        if (evt.type === 'chunk' && evt.content) {
          hasContent = true;
          setQaMessages((prev) => {
            const next = [...prev];
            next[next.length - 1] = { role: 'ai', content: next[next.length - 1].content + evt.content };
            return next;
          });
          qaBottomRef.current?.scrollIntoView({ behavior: 'smooth' });
        }
        if (evt.type === 'error') {
          throw new Error(evt.message || 'article ask stream error');
        }
      };
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split('\n\n');
        buffer = parts.pop() || '';
        for (const part of parts) {
          handleEvent(part);
        }
      }
      const tail = decoder.decode();
      if (tail) buffer += tail;
      if (buffer.trim()) handleEvent(buffer.trim());
      if (!hasContent) {
        replaceLastAiMessage('AI 伴读暂时没有生成有效内容，请换个问题再试。');
      }
      track({ event_type: 'ask', article_id: selectedArticle.id, query: question, meta_data: { surface: 'web_detail' } });
    } catch (error) {
      replaceLastAiMessage(
        error?.name === 'AbortError'
          ? 'AI 伴读响应超时，请稍后重试。'
          : 'AI 伴读暂时不可用，请稍后重试。'
      );
    } finally {
      window.clearTimeout(timeoutId);
      setQaLoading(false);
    }
  };

  const currentList = activeTab === '专家科普' ? expertArticles : articles;
  const currentLoading = loading || expertLoading;

  return (
    <div className="knowledge-zone" style={{
      minHeight: '100vh',
      padding: 32,
      position: 'relative',
      background: `
        radial-gradient(1200px 600px at 0% 0%, rgba(175, 238, 191, 0.55) 0%, transparent 60%),
        radial-gradient(1000px 500px at 100% 0%, rgba(240, 234, 193, 0.55) 0%, transparent 55%),
        radial-gradient(900px 600px at 50% 100%, rgba(224, 245, 238, 0.65) 0%, transparent 55%),
        linear-gradient(135deg, #f7fbf6 0%, #fbf7e8 50%, #effaf4 100%)
      `,
    }}>
      <style>{`
        .knowledge-md table { width: 100%; border-collapse: collapse; margin: 18px 0; }
        .knowledge-md th, .knowledge-md td { border: 1px solid #d5e3c8; padding: 10px 12px; }
        .knowledge-md th { background: #edf6d8; }
        .knowledge-zone .ant-input-search-button, .knowledge-zone .ant-btn-primary { background: #2f5a2e !important; border-color: #2f5a2e !important; }
        .knowledge-zone .ant-input-search-button:hover, .knowledge-zone .ant-btn-primary:hover { background: #3d6b38 !important; border-color: #3d6b38 !important; }
        .knowledge-zone .ant-card-hoverable:hover { border-color: #b8d49a; box-shadow: 0 8px 24px rgba(47, 90, 46, 0.10); }
        .article-ai-search .ant-input-search-button { background: #2f5a2e !important; border-color: #2f5a2e !important; color: #fff !important; }
        .article-ai-search .ant-input-search-button:hover { background: #3d6b38 !important; border-color: #3d6b38 !important; color: #fff !important; }
      `}</style>

      <div style={{ maxWidth: 1480, margin: '0 auto', position: 'relative', zIndex: 1 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 24, flexWrap: 'wrap', marginBottom: 24 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <Button
            type="text"
            icon={<ArrowLeftOutlined style={{ fontSize: 18, color: '#0F766E' }} />}
            onClick={() => navigate('/chat')}
            style={{
              width: 44,
              height: 44,
              borderRadius: '50%',
              background: 'rgba(255,255,255,0.85)',
              backdropFilter: 'blur(12px)',
              boxShadow: '0 4px 12px rgba(15,118,110,0.08)',
              border: '1px solid rgba(15,118,110,0.10)',
              flexShrink: 0,
            }}
          />
          <div>
            <Title level={3} style={{ margin: 0, color: '#0F172A', fontWeight: 800, letterSpacing: '-0.5px' }}>健康知识专区</Title>
            <Text style={{ fontSize: 13, color: '#0F766E', fontWeight: 500, letterSpacing: '0.5px' }}>Health Knowledge Magazine</Text>
          </div>
        </div>
        <div style={{ flex: '1 1 420px', maxWidth: 620 }}>
          <Input.Search
            size="large"
            allowClear
            placeholder="搜索症状、疾病、用药或体检报告"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onSearch={(value) => setQuery(value)}
          />
        </div>
      </div>

      {recommended.length > 0 && activeTab !== '专家科普' && !query.trim() && (
        <Card title="为你推荐" style={{ borderRadius: 18, marginBottom: 18 }} extra={<Text type="secondary">基于健康档案、收藏与阅读行为</Text>}>
          <Row gutter={[16, 16]}>
            {recommended.slice(0, 4).map((article) => (
              <Col xs={24} sm={12} lg={6} key={`rec-${article.id}`}>
                <ArticleCard
                  article={article}
                  compact
                  onOpen={openArticle}
                  onLike={handleLike}
                  onFavorite={handleFavorite}
                  liked={liked.has(article.id)}
                  favorited={favorited.has(article.id)}
                />
              </Col>
            ))}
          </Row>
        </Card>
      )}

      <Card style={{ borderRadius: 18 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap', marginBottom: 18 }}>
          <Space size={10} wrap>
            {CATEGORIES.map((cat) => (
              <Button
                key={cat.key}
                type="default"
                icon={cat.icon}
                onClick={() => setActiveTab(cat.key)}
                style={{
                  borderRadius: 999,
                  background: activeTab === cat.key ? '#2f5a2e' : '#fff',
                  borderColor: activeTab === cat.key ? '#2f5a2e' : '#d5e3c8',
                  color: activeTab === cat.key ? '#fff' : '#2f4a28',
                  boxShadow: activeTab === cat.key ? '0 4px 10px rgba(47,90,46,0.16)' : 'none',
                }}
              >
                {cat.key}
              </Button>
            ))}
          </Space>
        </div>

        <div style={{ marginBottom: 14, color: '#78906a', fontSize: 13 }}>
          {activeTab === '专家科普'
            ? `专家科普：共 ${expertArticles.length} 篇`
            : query.trim()
            ? `搜索“${query.trim()}”：共 ${articleTotal} 篇`
            : activeTab === '全部'
              ? `全部文章：共 ${articleTotal} 篇`
              : `${activeTab}：共 ${articleTotal} 篇`}
        </div>

        {currentLoading && <Skeleton active paragraph={{ rows: 6 }} />}

        {!currentLoading && currentList.length === 0 && (
          <Empty description={query.trim() ? '没有找到匹配文章' : '当前分类暂无文章'}>
            {activeTab !== '全部' && (
              <Button type="primary" onClick={() => setActiveTab('全部')}>查看全部</Button>
            )}
          </Empty>
        )}

        {!currentLoading && currentList.length > 0 && (
          <>
            <Row gutter={[18, 18]}>
              {currentList.map((article) => (
                <Col xs={24} sm={12} lg={6} key={article.id}>
                  <ArticleCard
                    article={article}
                    onOpen={openArticle}
                    onLike={handleLike}
                    onFavorite={handleFavorite}
                    liked={liked.has(article.id)}
                    favorited={favorited.has(article.id)}
                  />
                </Col>
              ))}
            </Row>
          </>
        )}
      </Card>
      </div>

      <Modal
        open={modalVisible}
        onCancel={closeArticle}
        footer={null}
        width={920}
        destroyOnClose
        title={selectedArticle?.title || '文章详情'}
      >
        {detailLoading || !selectedArticle ? (
          <Spin />
        ) : (
          <div style={{ maxWidth: 820, margin: '0 auto' }}>
            <Cover article={selectedArticle} height={300} />
            <Space wrap style={{ margin: '16px 0' }}>
              <Tag color="green">{selectedArticle.category}</Tag>
              <Tag><ClockCircleOutlined /> {selectedArticle.reading_time} 分钟读完</Tag>
              <Tag><EyeOutlined /> {selectedArticle.view_count}</Tag>
              <Tag><HeartOutlined /> {selectedArticle.likes}</Tag>
            </Space>
            <Title level={2}>{selectedArticle.title}</Title>
            <Paragraph type="secondary">{selectedArticle.summary}</Paragraph>
            <Divider />
            <div className="knowledge-md">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{selectedArticle.content || selectedArticle.summary}</ReactMarkdown>
            </div>
            {!selectedArticle.is_expert && (
              <>
                <Divider />
                <div style={{ borderRadius: 16, background: '#f4fae8', border: '1px solid #d5eab4', padding: 16 }}>
                  <Text strong style={{ display: 'block', color: '#2f5a2e', marginBottom: 12 }}>AI 伴读</Text>
                  {qaMessages.length > 0 && (
                    <div style={{ maxHeight: 300, overflowY: 'auto', marginBottom: 12 }}>
                      {qaMessages.map((msg, idx) => (
                        <div key={idx} style={{ marginBottom: 10, textAlign: msg.role === 'user' ? 'right' : 'left' }}>
                          <div style={{
                            display: 'inline-block',
                            maxWidth: '88%',
                            borderRadius: 12,
                            padding: '8px 11px',
                            background: msg.role === 'user' ? '#dff0bd' : '#fff',
                            border: msg.role === 'user' ? '1px solid #c7df95' : '1px solid #e3ecd4',
                            color: '#24381f',
                          }}>
                            <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content || '正在思考...'}</ReactMarkdown>
                          </div>
                        </div>
                      ))}
                      <div ref={qaBottomRef} />
                      </div>
                  )}
                  <Input.Search
                    className="article-ai-search"
                    value={qaInput}
                    onChange={(e) => setQaInput(e.target.value)}
                    onSearch={sendArticleQuestion}
                    enterButton={(
                      <Button
                        type="primary"
                        icon={<SendOutlined />}
                        style={{ background: '#2f5a2e', borderColor: '#2f5a2e', color: '#fff' }}
                      />
                    )}
                    loading={qaLoading}
                    placeholder="针对本文继续提问"
                  />
                  <Text type="secondary" style={{ display: 'block', marginTop: 8, fontSize: 12 }}>AI 回答仅供健康科普参考，不能替代医生诊断。</Text>
                </div>
              </>
            )}
          </div>
        )}
      </Modal>
    </div>
  );
};

export default KnowledgeZone;
