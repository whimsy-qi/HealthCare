import React, { useState, useRef, useEffect, useCallback } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  Home, MessageCircle, BookOpen, User, Bell,
  Send, Activity, Heart, Moon, ChevronRight,
  Brain, Bot, Sparkles,
  Footprints, ArrowLeft, TrendingUp,
  Settings, LogOut, Edit3, X, Search, Eye, EyeOff,
  HeartOff, Leaf, Pill, Star, Zap, Lock,
  CheckCircle, Flame, BarChart3, Clock,
  GitBranch, History, AlertTriangle, Lightbulb, ThumbsUp,
  ShieldCheck, Paperclip, Plus, RefreshCw, Trash2,
  Droplets, Dumbbell, Smile, Apple, Scale, Thermometer,
  Utensils, Salad, Banana, Carrot, Cherry, Bed, Bike,
  CigaretteOff, HeartPulse, Wind, ClipboardPlus, Syringe,
  Bandage, BicepsFlexed, GlassWater, HandHeart, CalendarCheck,
  Accessibility, CircleGauge, Target, Stethoscope, type LucideIcon,
} from 'lucide-react';
import { api, type SSEEvent } from '../lib/api';

// ─── Design Tokens — Fresh Mint System ────────────────────────────
const T = {
  // ── Fresh mint greens ─────────────────────────────
  g50:  '#F3FAEF',
  g100: '#E7F6D4',
  g200: '#CFF2D8',
  g300: '#A7E3C2',
  g400: '#7BCFA6',
  g500: '#4FB58B',
  g600: '#2F9B7F',
  g700: '#1F6F5B',
  g800: '#123C34',
  g900: '#10201A',
  // ── Aliases (backward compat) ─────────────────────
  mint50: '#F3FAEF', mint100: '#E7F6D4', mint200: '#CFF2D8',
  mint300: '#DDF7DF', mint400: '#A7E3C2', mint500: '#4FB58B',
  mint600: '#2F9B7F', mint700: '#1F6F5B', mint800: '#123C34',
  sky50: '#F1FAF7', sky100: '#DDF7EF', sky200: '#C5ECDD',
  sky300: '#A7E3D5', sky500: '#55BFA8', sky600: '#2F9B8F',
  lav100: '#F1F2FA', lav200: '#DDE1F5', lav500: '#8896D7',
  rose50: '#FFF4F5', rose100: '#FFE3E6', rose200: '#F8C8D0', rose500: '#D9788C',
  cream50: '#F3FAEF', cream100: '#E7F6D4',
  cream200: '#CFF2D8', cream300: '#A7E3C2',
  cream600: '#4FB58B', cream700: '#1F6F5B',
  slate50: '#F7FBF8', slate100: '#EEF7EF',
  slate200: '#DCEADE', slate300: '#BFD3C4',
  slate400: '#6F8A7A', slate500: '#536A5F',
  slate600: '#34483E', slate700: '#22372F',
  slate800: '#142820', slate900: '#10201A',
  // ── Error (login / alerts only) ───────────────────
  red50: '#fdf3f3', red200: '#f0c0c0', red500: '#c86060', red700: '#a04040',
};

// ─── Types ─────────────────────────────────────────────────────────
type Tab = 'home' | 'chat' | 'knowledge' | 'profile';
type ChatMsg = {
  id: number;
  role: 'user' | 'ai';
  content: string;
  time: string;
  image?: string;        // 用户上传的图片 URL（http://... 或 data:image/...）
  status?: string;       // 流式过程提示（"🤔 正在思考..."）
  route?: string;        // 命中的路由（SYMPTOM / RUMOR / GENERAL...）
  halluc?: any;          // 后端 hallucination_check 报告
  insightHits?: number;  // 命中相似历史案例数（rumor_step.insight_hit）
  loading?: boolean;     // AI 占位气泡仍在等待
  // 🆕 多轮追问：当 isFinished=false 且 options 非空 → 底部弹"快速选择"卡
  options?: string[];
  isFinished?: boolean;
  // 🆕 证据链（trace_data.evidence_chain）
  evidenceChain?: any;
  traceData?: any;
  aiImages?: string[];
};

// File → base64 dataURL（用于上传 + 即时预览）
const fileToBase64 = (file: File): Promise<string> =>
  new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.readAsDataURL(file);
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = reject;
  });

// 🆕 把后端发来的 phase ID 转成友好中文，避免 "round_2_critic_evidence_check" 之类长 ID
// 直接展示溢出对话框。无映射时把下划线/驼峰转空格、首字母大写并截短。
const PHASE_ZH: Record<string, string> = {
  // MADDx
  'init': '初始化', 'doctors_init': '专科医生集结', 'round_start': '轮次开始',
  'doctor_propose': '专科提议', 'doctor_evidence': '调取证据',
  'critic_review': '批判审视', 'critic_check': '证据核验',
  'consensus_check': '共识检查', 'consensus_reached': '达成共识',
  'final_synthesis': '综合诊断', 'done': '完成',
  // Rumor
  'classify': '命题分类', 'route': '风险路由', 'fast_path': '快速核查',
  'advocate_search': '辩护方取证', 'skeptic_search': '质疑方取证',
  'judge_weight': '加权裁决', 'halluc_check': '幻觉复核',
  'insight_hit': '命中相似案例',
};
function friendlyPhase(phase?: string): string {
  if (!phase) return '处理中';
  if (PHASE_ZH[phase]) return PHASE_ZH[phase];
  // 兜底：把 underscore_case 转成空格分词，最多展示 12 字符
  const t = phase.replace(/[_-]+/g, ' ').trim();
  return (t.length > 12 ? t.slice(0, 12) + '…' : t) || '处理中';
}

// ─── Static Data ───────────────────────────────────────────────────
const QUICK_ACTIONS = [
  { icon: <Bot size={22} />, label: 'AI 问诊', sub: '智能多科会诊', color: T.mint500, bg: T.mint50, border: T.mint200, tab: 'chat' as Tab },
  { icon: <BookOpen size={22} />, label: '知识库', sub: '健康科普', color: T.sky500, bg: T.sky100, border: T.sky200, tab: 'knowledge' as Tab },
  { icon: <GitBranch size={22} />, label: '知识图谱', sub: '疾病关系探索', color: T.lav500, bg: T.lav100, border: T.lav200, tab: 'knowledge' as Tab, graph: true },
  { icon: <BarChart3 size={22} />, label: '健康档案', sub: '全维分析', color: T.rose500, bg: T.rose50, border: T.rose100, tab: 'profile' as Tab },
];
const HEALTH_METRICS = [
  { icon: <Footprints size={14} />, label: '今日步数', value: '6,240', unit: '步', color: T.mint500, bg: T.mint50 },
  { icon: <Moon size={14} />, label: '睡眠质量', value: '7.5', unit: 'h', color: T.sky500, bg: T.sky100 },
  { icon: <Heart size={14} />, label: '静息心率', value: '72', unit: 'bpm', color: T.red500, bg: T.red50 },
  { icon: <Flame size={14} />, label: '热量消耗', value: '1,840', unit: 'kcal', color: T.rose500, bg: T.rose50 },
];
const TIPS = [
  '每天步行 30 分钟可降低心血管疾病风险约 35%，尝试分段完成更易坚持。',
  '深度睡眠阶段大脑会清除代谢废物，保持规律作息有助提升记忆与免疫力。',
  '每天摄入足量膳食纤维（25~30g）有助调节血糖，减少炎症反应。',
];
const RECENT_CHATS = [
  { id: 1, title: '头痛发烧咨询', preview: '建议补充水分并休息，若体温超过 38.5℃…', time: '10:24', unread: 2 },
  { id: 2, title: '血压控制建议', preview: 'DASH 饮食模式可有效降低收缩压约 11mmHg…', time: '昨天', unread: 0 },
  { id: 3, title: '膝关节疼痛分析', preview: '多智能体诊断结论：轻度劳损，建议…', time: '周一', unread: 0 },
];
const EXPERT_CATEGORY = '专家科普';
const CATEGORIES = ['辟谣粉碎机', '硬核诊疗局', '用药红绿灯', '时令与养生', '实时热点追踪', EXPERT_CATEGORY];
const CAT_COLORS = [
  { bg: T.mint50, border: T.mint200, icon: T.mint600, text: T.mint700 },
  { bg: T.sky100, border: T.sky200, icon: T.sky500, text: '#2a6080' },
  { bg: T.rose50, border: T.rose100, icon: T.rose500, text: '#8a3040' },
  { bg: T.lav100, border: T.lav200, icon: T.lav500, text: '#4a3a90' },
  { bg: T.cream100, border: T.cream200, icon: T.g600, text: T.g700 },
  { bg: T.mint50, border: T.mint200, icon: T.mint600, text: T.mint700 },
];
const CAT_ICONS: Record<string, React.ReactNode> = {
  '辟谣粉碎机': <CheckCircle size={14} />, '硬核诊疗局': <Activity size={14} />,
  '用药红绿灯': <Pill size={14} />, '时令与养生': <Leaf size={14} />,
  '实时热点追踪': <TrendingUp size={14} />,
  [EXPERT_CATEGORY]: <BookOpen size={14} />,
};
const MOCK_ARTICLES = [
  { id: 1, cat: '辟谣粉碎机', title: '"空腹喝咖啡会损伤胃黏膜"是真的吗？', summary: '流行说法称空腹咖啡有害，但科学证据并不支持此说法…', views: 3241, likes: 187 },
  { id: 2, cat: '硬核诊疗局', title: '高血压患者的饮食红绿灯指南', summary: 'DASH 饮食模式经大规模临床试验证实，可有效降低收缩压…', views: 5820, likes: 312 },
  { id: 3, cat: '用药红绿灯', title: '布洛芬与对乙酰氨基酚的使用区别', summary: '两者都可退热止痛，但适应症、禁忌人群有明显差别…', views: 4150, likes: 256 },
  { id: 4, cat: '时令与养生', title: '春季养肝护脾：中医时令调养指南', summary: '春季五行属木，对应肝脏，适合舒肝解郁的食补方式…', views: 2780, likes: 145 },
];

const ARTICLE_FALLBACK_IMAGES: Record<string, string> = {
  '辟谣粉碎机': 'https://images.unsplash.com/photo-1576091160399-112ba8d25d1d?auto=format&fit=crop&q=80&w=800',
  '硬核诊疗局': 'https://images.unsplash.com/photo-1551076805-e1869043e560?auto=format&fit=crop&q=80&w=800',
  '用药红绿灯': 'https://images.unsplash.com/photo-1584308666744-24d5c474f2ae?auto=format&fit=crop&q=80&w=800',
  '时令与养生': 'https://images.unsplash.com/photo-1544367567-0f2fcb009e0b?auto=format&fit=crop&q=80&w=800',
  '实时热点追踪': 'https://images.unsplash.com/photo-1504439468489-c8920d786a2b?auto=format&fit=crop&q=80&w=800',
  [EXPERT_CATEGORY]: 'https://images.unsplash.com/photo-1581093458791-9f3c3c07b8f2?auto=format&fit=crop&q=80&w=800',
};

const getArticleFallbackImage = (category?: string) => ARTICLE_FALLBACK_IMAGES[category || ''] || ARTICLE_FALLBACK_IMAGES['硬核诊疗局'];
const normalizeArticleCoverUrl = (value: any) => {
  const url = String(value || '').trim();
  if (!url) return '';
  if (/^(https?:|data:|blob:)/i.test(url)) return url;
  if (url.startsWith('/')) return `${api.API_BASE}${url}`;
  return url;
};
const isBrokenText = (value: any) => {
  const text = String(value ?? '').trim();
  return !text || /^[?\s]+$/.test(text) || /[�]{1,}|闂|鈧|锟|绋|熼|婵/.test(text);
};
const normalizeBrokenText = (value: any, fallback: string) => isBrokenText(value) ? fallback : String(value).trim();
const isExpertArticle = (article: any) =>
  article?.cat === EXPERT_CATEGORY || article?.category === EXPERT_CATEGORY || article?.source_type === 'expert_article';

const categoryIndex = (cat?: string) => Math.max(0, CATEGORIES.indexOf(cat || ''));
const FlatArticleCover: React.FC<{ category?: string; title?: string; height?: number }> = ({ category = '硬核诊疗局', title = '', height = 150 }) => {
  const idx = categoryIndex(category);
  const c = CAT_COLORS[idx % CAT_COLORS.length];
  const Icon = category === '用药红绿灯' ? Pill : category === '时令与养生' ? Leaf : category === '辟谣粉碎机' ? ShieldCheck : category === '实时热点追踪' ? TrendingUp : category === EXPERT_CATEGORY ? BookOpen : Activity;
  return (
    <div style={{
      height, position: 'relative', overflow: 'hidden', borderRadius: 14,
      background: `linear-gradient(135deg, ${c.bg}, ${c.border})`,
      border: `1px solid ${c.border}`,
    }}>
      <div style={{ position: 'absolute', inset: 0, opacity: 0.55, background: 'radial-gradient(circle at 18% 22%, rgba(255,255,255,0.8), transparent 28%), radial-gradient(circle at 82% 70%, rgba(255,255,255,0.5), transparent 32%)' }} />
      <div style={{ position: 'absolute', left: 18, top: 18, width: 58, height: 58, borderRadius: 18, background: 'rgba(255,255,255,0.72)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: c.icon }}>
        <Icon size={30} strokeWidth={1.8} />
      </div>
      <svg viewBox="0 0 320 160" style={{ position: 'absolute', right: -4, bottom: 0, width: '78%', height: '100%' }} aria-hidden="true">
        <g fill="none" stroke={c.icon} strokeWidth="4" strokeLinecap="round" strokeLinejoin="round" opacity="0.6">
          <path d="M78 118 C98 88, 128 82, 154 104 C179 126, 213 118, 234 84" />
          <path d="M108 78 C116 61, 139 58, 150 75 C163 55, 197 57, 203 83 C211 118, 166 130, 152 144 C138 130, 94 115, 108 78Z" fill="rgba(255,255,255,0.45)" />
          <path d="M238 48 h34 M255 31 v34" />
          <path d="M48 132 h38 M54 112 h26" />
        </g>
      </svg>
      <div style={{ position: 'absolute', left: 18, right: 18, bottom: 14 }}>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, padding: '3px 8px', borderRadius: 999, background: 'rgba(255,255,255,0.82)', color: c.text, fontSize: 10, fontWeight: 800 }}>
          {CAT_ICONS[category] || <BookOpen size={12} />} {category}
        </span>
        {title && <div style={{ marginTop: 8, color: T.slate900, fontSize: 13, fontWeight: 900, maxWidth: '68%', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{title}</div>}
      </div>
    </div>
  );
};

const ArticleCover: React.FC<{ article: any; height?: number }> = ({ article, height = 150 }) => {
  const [failed, setFailed] = useState(false);
  const category = article?.cat || article?.category;
  const cover = failed ? '' : normalizeArticleCoverUrl(article?.cover_image || article?.coverImage || article?.cover_url || article?.cover);
  const categoryFallback = getArticleFallbackImage(category);
  const stableFallback = normalizeArticleCoverUrl(article?.fallback_cover_image) || categoryFallback;
  const imageSrc = cover || stableFallback;
  if (failed) {
    return <FlatArticleCover category={category} title={article?.title} height={height} />;
  }
  return (
    <div style={{ height, borderRadius: 16, overflow: 'hidden', position: 'relative', background: T.mint50 }}>
      <img
        src={imageSrc}
        alt={article?.title || 'article cover'}
        onError={(e) => {
          setFailed(true);
        }}
        style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'cover' }}
      />
    </div>
  );
};

const normalizeKnowledgeArticle = (a: any, fallbackCat = '硬核诊疗局') => ({
  ...a,
  cat: normalizeBrokenText(a?.cat || a?.category || fallbackCat, fallbackCat),
  title: normalizeBrokenText(a?.title, '未命名文章'),
  summary: normalizeBrokenText(a?.summary || a?.content?.slice?.(0, 90), '暂无摘要'),
  cover_image: normalizeArticleCoverUrl(a?.cover_image || a?.coverImage || a?.cover_url || a?.cover),
  views: a?.views ?? a?.view_count ?? 0,
  likes: a?.likes ?? 0,
  tags: Array.isArray(a?.tags) ? a.tags : [],
  related_entities: Array.isArray(a?.related_entities) ? a.related_entities : [],
  reading_time: a?.reading_time || 3,
  risk_level: a?.risk_level || 'low',
  is_favorited: !!a?.is_favorited,
  source_type: a?.source_type,
});

const safeErrorMessage = (error: any, fallback = '加载失败') => {
  const raw = error?.message || error?.detail || error;
  if (!raw) return fallback;
  if (typeof raw === 'string') return raw;
  try {
    return JSON.stringify(raw);
  } catch {
    return fallback;
  }
};

const PROFILE_MENU: Array<{ icon: React.ReactNode; label: string; sub: string; color: string; action?: 'edit_profile' }> = [
  { icon: <Edit3 size={18} />,    label: '编辑健康档案', sub: '完善信息以获得精准建议', color: T.mint600, action: 'edit_profile' },
  { icon: <Activity size={18} />, label: '健康数据总览', sub: '查看历史趋势',         color: T.mint500 },
  { icon: <GitBranch size={18} />,label: '隐私与安全',   sub: '数据保护设置',         color: T.sky500 },
  { icon: <Bell size={18} />,     label: '提醒与通知',   sub: '健康打卡提醒',         color: T.lav500 },
  { icon: <Settings size={18} />, label: '应用设置',     sub: '主题、语言',            color: T.slate500 },
];

const MOBILE_PROFILE_OPTIONS = {
  gender: ['男', '女', '其他'],
  diet: ['均衡饮食', '素食为主', '高蛋白', '低碳水', '其他'],
  sleep: ['规律且充足', '偶尔熬夜/失眠', '经常熬夜/失眠'],
  exercise: ['每周3次以上', '每周1-2次', '偶尔运动', '几乎不运动'],
  smoking: ['不吸烟', '偶尔吸烟', '长期吸烟'],
  drinking: ['不饮酒', '偶尔饮酒', '经常饮酒'],
  menstrual_volume: ['正常', '偏少', '偏多', '不规律'],
  dysmenorrhea: ['无', '轻度', '中度', '重度'],
  obstetric_status: ['无', '备孕中', '怀孕中', '哺乳期'],
};

const COMMON_PROFILE_DISEASES = ['高血压', '糖尿病', '冠心病', '脑卒中', '慢性肾病', '哮喘', '甲状腺疾病', '高脂血症', '脂肪肝', '骨质疏松'];
const COMMON_PROFILE_ALLERGIES = ['青霉素', '头孢类', '磺胺类', '阿司匹林', '海鲜', '坚果', '花粉', '尘螨', '乳制品', '鸡蛋'];
const COMMON_PROFILE_VACCINES = ['乙肝', '甲肝', '流感', 'HPV', '新冠', '肺炎', '带状疱疹'];

const PROFILE_EDIT_SECTIONS = [
  { key: 'basic', label: '基础' },
  { key: 'life', label: '生活' },
  { key: 'female', label: '女性' },
  { key: 'disease', label: '病史' },
  { key: 'allergy', label: '过敏' },
  { key: 'vaccine', label: '疫苗' },
  { key: 'surgery', label: '手术' },
];

const splitProfileText = (value: any): string[] => {
  if (Array.isArray(value)) return value.map(String).map(s => s.trim()).filter(Boolean);
  return String(value || '').split(/[、,，\s]+/).map(s => s.trim()).filter(Boolean);
};

const uniqueProfileValues = (values: any[]): string[] =>
  Array.from(new Set(values.flatMap(splitProfileText))).filter(Boolean);

const buildMobileProfileDraft = (profile: any = {}) => {
  const pastDiseasesCustom = splitProfileText(profile?.past_diseases_custom);
  const allergiesCustom = splitProfileText(profile?.allergies_custom);
  const legacyDiseases = splitProfileText(profile?.diseases);
  const legacyAllergies = splitProfileText(profile?.allergies);
  const normalizeSurgeries = Array.isArray(profile?.surgeries)
    ? profile.surgeries.map((s: any) => ({
        name: typeof s === 'string' ? s : String(s?.name || ''),
        date: typeof s === 'string' ? '' : String(s?.date || ''),
      })).filter((s: any) => s.name || s.date)
    : [];

  return {
    age: profile?.age ?? '',
    height: profile?.height ?? '',
    weight: profile?.weight ?? '',
    gender: profile?.gender ?? '',
    diet: profile?.diet ?? '',
    sleep: profile?.sleep ?? '',
    exercise: profile?.exercise ?? '',
    smoking: profile?.smoking ?? '',
    drinking: profile?.drinking ?? '',
    past_diseases_common: splitProfileText(profile?.past_diseases_common),
    past_diseases_custom: pastDiseasesCustom.length ? pastDiseasesCustom : legacyDiseases,
    allergies_common: splitProfileText(profile?.allergies_common),
    allergies_custom: allergiesCustom.length ? allergiesCustom : legacyAllergies,
    vaccines_common: splitProfileText(profile?.vaccines_common),
    vaccines_custom: splitProfileText(profile?.vaccines_custom),
    surgeries: normalizeSurgeries,
    menstrual_volume: profile?.menstrual_volume ?? '',
    dysmenorrhea: profile?.dysmenorrhea ?? '',
    menstrual_cycle: profile?.menstrual_cycle ?? '',
    obstetric_status: profile?.obstetric_status ?? '',
    due_date: profile?.due_date ?? '',
    lactation_start_date: profile?.lactation_start_date ?? '',
  };
};

// ─── StatusBar ─────────────────────────────────────────────────────
const StatusBar: React.FC = () => {
  const now = new Date();
  const t = `${now.getHours().toString().padStart(2, '0')}:${now.getMinutes().toString().padStart(2, '0')}`;
  return (
    <div style={{ height: 44, display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 24px', flexShrink: 0, position: 'relative', zIndex: 5 }}>
      <span style={{ fontSize: 15, fontWeight: 700, color: T.slate800, letterSpacing: '-0.3px' }}>{t}</span>
      {/* Dynamic Island */}
      <div style={{ position: 'absolute', left: '50%', top: 10, transform: 'translateX(-50%)', width: 120, height: 34, background: '#111', borderRadius: 20 }} />
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        {/* Signal bars */}
        <svg width={16} height={12} viewBox="0 0 16 12">
          {[0,1,2,3].map((i) => <rect key={i} x={i*4} y={12-(i+1)*3} width={3} height={(i+1)*3} rx={1} fill={i < 3 ? T.slate800 : T.slate300} />)}
        </svg>
        {/* WiFi */}
        <svg width={16} height={12} viewBox="0 0 16 12"><path d="M8 10a1 1 0 100 2 1 1 0 000-2zm0-4a5 5 0 00-3.54 1.46l1.41 1.41A3 3 0 018 8a3 3 0 012.13.87l1.41-1.41A5 5 0 008 6zm0-4a9 9 0 00-6.36 2.64l1.41 1.41A7 7 0 018 4a7 7 0 014.95 2.05l1.41-1.41A9 9 0 008 2z" fill={T.slate800} /></svg>
        {/* Battery */}
        <div style={{ width: 24, height: 12, border: `1.5px solid ${T.slate600}`, borderRadius: 3, padding: '1px 1px', display: 'flex', alignItems: 'center', position: 'relative' }}>
          <div style={{ width: '85%', height: '100%', background: T.mint500, borderRadius: 1.5 }} />
          <div style={{ position: 'absolute', right: -3, top: '50%', transform: 'translateY(-50%)', width: 2, height: 5, background: T.slate600, borderRadius: '0 1px 1px 0' }} />
        </div>
      </div>
    </div>
  );
};

// ─── HomeScreen ────────────────────────────────────────────────────
type CheckinItem = {
  code: string;
  name: string;
  icon: string;
  icon_bg?: string;
  category?: string;
  created_at?: string | null;
  week_count: number;
  month_count: number;
  done_today: boolean;
  last31: Array<{ date: string; done: boolean }>;
  is_custom?: boolean;
};

const DEFAULT_CHECKIN_ICON = 'activity';
const DEFAULT_CHECKIN_ICON_BG = '#E7F6D4';
const CHECKIN_ICON_OPTIONS: Array<{ key: string; label: string; Icon: LucideIcon }> = [
  { key: 'activity', label: '活动', Icon: Activity },
  { key: 'walk', label: '散步', Icon: Footprints },
  { key: 'run', label: '跑步', Icon: Zap },
  { key: 'water', label: '喝水', Icon: Droplets },
  { key: 'glass-water', label: '饮水', Icon: GlassWater },
  { key: 'pill', label: '用药', Icon: Pill },
  { key: 'syringe', label: '注射', Icon: Syringe },
  { key: 'sleep', label: '睡眠', Icon: Bed },
  { key: 'moon', label: '夜间', Icon: Moon },
  { key: 'mood', label: '心情', Icon: Smile },
  { key: 'meditation', label: '冥想', Icon: Brain },
  { key: 'breath', label: '呼吸', Icon: Wind },
  { key: 'read', label: '阅读', Icon: BookOpen },
  { key: 'target', label: '目标', Icon: Target },
  { key: 'heart', label: '心率', Icon: HeartPulse },
  { key: 'blood-pressure', label: '血压', Icon: Activity },
  { key: 'weight', label: '体重', Icon: Scale },
  { key: 'temperature', label: '体温', Icon: Thermometer },
  { key: 'meal', label: '饮食', Icon: Utensils },
  { key: 'salad', label: '轻食', Icon: Salad },
  { key: 'apple', label: '水果', Icon: Apple },
  { key: 'banana', label: '香蕉', Icon: Banana },
  { key: 'carrot', label: '蔬菜', Icon: Carrot },
  { key: 'cherry', label: '零食', Icon: Cherry },
  { key: 'fitness', label: '健身', Icon: Dumbbell },
  { key: 'strength', label: '力量', Icon: BicepsFlexed },
  { key: 'bike', label: '骑行', Icon: Bike },
  { key: 'rehab', label: '康复', Icon: HandHeart },
  { key: 'bandage', label: '护理', Icon: Bandage },
  { key: 'checkup', label: '复诊', Icon: ClipboardPlus },
  { key: 'calendar', label: '预约', Icon: CalendarCheck },
  { key: 'posture', label: '体态', Icon: Accessibility },
  { key: 'metric', label: '指标', Icon: CircleGauge },
  { key: 'quit-smoking', label: '戒烟', Icon: CigaretteOff },
  { key: 'care', label: '关怀', Icon: Stethoscope },
];
const CHECKIN_ICON_BG_OPTIONS = ['#E7F6D4', '#DBEAFE', '#FEF3C7', '#FDE2E4', '#EDE9FE', '#CCFBF1', '#FFEDD5', '#E2EAD7'];
const CHECKIN_ICON_MAP = new Map(CHECKIN_ICON_OPTIONS.map(option => [option.key, option]));
const CHECKIN_NAME_FALLBACK: Record<string, string> = {
  exercise: '运动',
  water: '喝水',
  medicine: '按时用药',
  sleep: '睡眠',
  mood: '心情记录',
};

const toISODate = (d: Date) => {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
};
const addDays = (d: Date, days: number) => {
  const next = new Date(d);
  next.setDate(next.getDate() + days);
  return next;
};
const monthStart = (d = new Date()) => new Date(d.getFullYear(), d.getMonth(), 1);
const yearStart = (d = new Date()) => new Date(d.getFullYear(), 0, 1);
const weekStart = (d = new Date()) => {
  const next = new Date(d);
  const day = next.getDay() || 7;
  next.setDate(next.getDate() - day + 1);
  return new Date(next.getFullYear(), next.getMonth(), next.getDate());
};
const buildLast31Window = (doneToday = false) => {
  const today = new Date();
  const start = addDays(today, -30);
  return Array.from({ length: 31 }, (_, index) => {
    const date = toISODate(addDays(start, index));
    return { date, done: index === 30 ? doneToday : false };
  });
};
const normalizeLast31 = (raw: any, doneToday: boolean) => {
  const values = Array.isArray(raw?.last31) && raw.last31.length > 0
    ? raw.last31
    : buildLast31Window(doneToday);
  const normalized = values.slice(-31).map((d: any) => ({
    date: String(d?.date || ''),
    done: Boolean(d?.done),
  }));
  if (normalized.length < 31) {
    return [...buildLast31Window(doneToday).slice(0, 31 - normalized.length), ...normalized];
  }
  return normalized;
};
const normalizeCheckinItem = (raw: any): CheckinItem => ({
  code: String(raw.code),
  name: normalizeBrokenText(raw.name, CHECKIN_NAME_FALLBACK[String(raw.code)] || '健康打卡'),
  icon: String(raw.icon || DEFAULT_CHECKIN_ICON),
  icon_bg: String(raw.icon_bg || DEFAULT_CHECKIN_ICON_BG),
  category: raw.category ? String(raw.category) : 'custom',
  created_at: raw.created_at ?? null,
  week_count: Number(raw.week_count || 0),
  month_count: Number(raw.month_count || 0),
  done_today: Boolean(raw.done_today ?? raw.done),
  last31: normalizeLast31(raw, Boolean(raw.done_today ?? raw.done)),
  is_custom: Boolean(raw.is_custom),
});

const CheckinIconBadge: React.FC<{ iconKey?: string; bg?: string; size?: number; iconSize?: number }> = ({
  iconKey,
  bg = DEFAULT_CHECKIN_ICON_BG,
  size = 44,
  iconSize,
}) => {
  const option = CHECKIN_ICON_MAP.get(iconKey || '') || CHECKIN_ICON_MAP.get(DEFAULT_CHECKIN_ICON)!;
  const Icon = option.Icon;
  return (
    <div style={{ width: size, height: size, borderRadius: '50%', background: bg, display: 'flex', alignItems: 'center', justifyContent: 'center', color: T.mint600, flexShrink: 0 }}>
      <Icon size={iconSize ?? Math.round(size * 0.48)} strokeWidth={2.2} />
    </div>
  );
};

const HomeScreen: React.FC<{ onTabChange: (t: Tab) => void; onChatOpen: (title: string) => void; onGraphOpen: () => void }> = ({ onTabChange, onChatOpen, onGraphOpen }) => {
  const username = localStorage.getItem('current_username') || '用户';
  const hour = new Date().getHours();
  const greeting = hour < 6 ? '夜深了' : hour < 12 ? '早上好' : hour < 18 ? '下午好' : '晚上好';
  const tip = TIPS[new Date().getDate() % TIPS.length];

  const [checkinList, setCheckinList] = useState<CheckinItem[]>([]);
  const [checkinsLoading, setCheckinsLoading] = useState(false);
  const [actionItem, setActionItem] = useState<CheckinItem | null>(null);
  const [deleteConfirmItem, setDeleteConfirmItem] = useState<CheckinItem | null>(null);
  const [actionLoading, setActionLoading] = useState(false);
  const [actionError, setActionError] = useState('');
  const [editingItem, setEditingItem] = useState<CheckinItem | 'new' | null>(null);
  const [recordingItem, setRecordingItem] = useState<CheckinItem | null>(null);
  const [detailItem, setDetailItem] = useState<CheckinItem | null>(null);

  // 后端 dashboard 数据
  const [dashboard, setDashboard] = useState<any>(null);
  useEffect(() => {
    if (!localStorage.getItem('access_token')) return;
    api.getDashboard().then(setDashboard).catch(() => { /* 失败回退 mock */ });
    refreshCheckins();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 后端打卡列表 → 前端结构
  const refreshCheckins = useCallback(async () => {
    setCheckinsLoading(true);
    try {
      const d: any = await api.getCheckinItems();
      setCheckinList(Array.isArray(d?.items) ? d.items.map(normalizeCheckinItem) : []);
    } catch (e) {
      console.warn('刷新打卡项失败', e);
    } finally {
      setCheckinsLoading(false);
    }
  }, []);

  // 健康评分（用于 ring 中央数字）
  const healthScore: number = dashboard?.health_score ?? 87;

  // 把后端 metrics 映射到 HEALTH_METRICS 同结构
  const liveMetrics = (() => {
    const m = dashboard?.metrics || [];
    const get = (k: string) => m.find((x: any) => x?.key === k)?.value;
    if (!dashboard) return HEALTH_METRICS;
    return [
      { ...HEALTH_METRICS[0], value: String(get('steps') ?? HEALTH_METRICS[0].value) },
      { ...HEALTH_METRICS[1], value: String(get('sleep') ?? HEALTH_METRICS[1].value) },
      { ...HEALTH_METRICS[2], value: String(get('heart_rate') ?? HEALTH_METRICS[2].value) },
      { ...HEALTH_METRICS[3], value: String(get('calories') ?? HEALTH_METRICS[3].value) },
    ];
  })();

  // 删除自定义项
  const onDeleteCustom = useCallback(async (item: CheckinItem) => {
    if (actionLoading) return;
    setActionLoading(true);
    setActionError('');
    try {
      await api.deleteCheckinItem(item.code);
      await refreshCheckins();
      if (detailItem?.code === item.code) setDetailItem(null);
      setDeleteConfirmItem(null);
      setActionItem(null);
    } catch (e: any) {
      setActionError(`删除失败：${e?.message || '请稍后重试'}`);
    } finally {
      setActionLoading(false);
    }
  }, [actionLoading, detailItem?.code, refreshCheckins]);

  // 保存（新增/编辑）— 由 modal 调用
  const onSaveItem = useCallback(async (payload: { name: string; icon: string; icon_bg: string }, editCode?: string) => {
    try {
      const finalPayload = { ...payload, category: 'custom', points: 0 };
      editCode
        ? await api.updateCheckinItem(editCode, finalPayload)
        : await api.createCheckinItem(finalPayload);
      await refreshCheckins();
      setEditingItem(null);
    } catch (e: any) {
      alert(`保存失败：${e?.message || '请稍后重试'}`);
    }
  }, [refreshCheckins]);

  const markCheckinDoneLocally = useCallback((itemCode: string) => {
    const todayIso = toISODate(new Date());
    setCheckinList(prev => prev.map(item => {
      if (item.code !== itemCode) return item;
      const alreadyDone = item.done_today;
      return {
        ...item,
        done_today: true,
        week_count: item.week_count + (alreadyDone ? 0 : 1),
        month_count: item.month_count + (alreadyDone ? 0 : 1),
        last31: normalizeLast31(
          {
            last31: item.last31.map(day => (
              day.date === todayIso ? { ...day, done: true } : day
            )),
          },
          true,
        ),
      };
    }));
  }, []);

  const handleCheckinSaved = useCallback(async (itemCode: string, updatedItem?: any) => {
    if (updatedItem) {
      const normalized = normalizeCheckinItem(updatedItem);
      setCheckinList(prev => prev.map(item => (item.code === itemCode ? normalized : item)));
    } else {
      markCheckinDoneLocally(itemCode);
    }
    setRecordingItem(null);
    try {
      await refreshCheckins();
    } catch {
      // refreshCheckins already preserves the optimistic state on failure.
    }
  }, [markCheckinDoneLocally, refreshCheckins]);

  return (
    <div style={{ flex: 1, overflowY: 'auto', background: T.cream50 }} className="mobile-scroll">
      {/* ── Header ── */}
      <div style={{ padding: '8px 20px 16px', background: 'white' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div>
            <div style={{ fontSize: 13, color: T.slate400, fontWeight: 500 }}>{greeting} 👋</div>
            <div style={{ fontSize: 20, fontWeight: 800, color: T.slate900, letterSpacing: '-0.3px' }}>{username}</div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <button style={{ width: 38, height: 38, borderRadius: '50%', background: T.slate100, border: 'none', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', position: 'relative' }}>
              <Bell size={17} color={T.slate600} />
              <div style={{ position: 'absolute', top: 8, right: 9, width: 7, height: 7, borderRadius: '50%', background: T.g600, border: '1.5px solid white' }} />
            </button>
            <div style={{ width: 38, height: 38, borderRadius: '50%', background: 'linear-gradient(135deg, #EAF7B6, #8DDBA8)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: T.g800, fontSize: 15, fontWeight: 800 }}>
              {username.charAt(0).toUpperCase()}
            </div>
          </div>
        </div>
      </div>

      {/* ── Health Score Banner ── */}
      <div style={{ margin: '12px 16px', borderRadius: 22, overflow: 'hidden', boxShadow: '0 6px 24px rgba(90,112,72,0.18)' }}>
        <div style={{ background: 'linear-gradient(135deg, #F2F8B8 0%, #D8F3A6 38%, #A9E8B6 72%, #76D0A3 100%)', padding: '20px 22px 16px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
            <div>
              <div style={{ fontSize: 12, color: 'rgba(16,32,26,0.62)', fontWeight: 700, marginBottom: 4 }}>今日健康评分</div>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
                <span style={{ fontSize: 52, fontWeight: 900, color: T.g900, lineHeight: 1, letterSpacing: '-2px' }}>{healthScore}</span>
                <span style={{ fontSize: 18, color: 'rgba(16,32,26,0.52)', fontWeight: 700 }}>/100</span>
              </div>
              <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                {['血压正常', '睡眠良好', '心率健康'].map(t => (
                  <span key={t} style={{ fontSize: 10, padding: '2px 8px', borderRadius: 20, background: 'rgba(255,255,255,0.58)', color: T.g700, fontWeight: 700 }}>{t}</span>
                ))}
              </div>
            </div>
            {/* Circular progress */}
            <div style={{ position: 'relative', width: 72, height: 72 }}>
              <svg width={72} height={72} style={{ transform: 'rotate(-90deg)' }}>
                <circle cx={36} cy={36} r={28} fill="none" stroke="rgba(31,111,91,0.14)" strokeWidth={6} />
                <circle cx={36} cy={36} r={28} fill="none" stroke="#26A878" strokeWidth={6}
                  strokeDasharray={2 * Math.PI * 28} strokeDashoffset={2 * Math.PI * 28 * 0.13} strokeLinecap="round" />
              </svg>
              <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <Activity size={22} color={T.g700} />
              </div>
            </div>
          </div>
        </div>
        {/* Metrics Row */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', background: 'white' }}>
          {liveMetrics.map((m, i) => (
            <div key={i} style={{ padding: '12px 0', textAlign: 'center', borderRight: i < 3 ? `1px solid ${T.slate200}` : 'none' }}>
              <div style={{ width: 28, height: 28, borderRadius: 8, background: m.bg, margin: '0 auto 5px', display: 'flex', alignItems: 'center', justifyContent: 'center', color: m.color }}>{m.icon}</div>
              <div style={{ fontSize: 14, fontWeight: 800, color: T.slate900 }}>{m.value}</div>
              <div style={{ fontSize: 9, color: T.slate400, fontWeight: 600 }}>{m.unit}</div>
            </div>
          ))}
        </div>
      </div>

      {/* ── Quick Actions ── */}
      <div style={{ padding: '0 16px', marginBottom: 14 }}>
        <div style={{ fontSize: 14, fontWeight: 700, color: T.slate800, marginBottom: 10 }}>快捷功能</div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
          {QUICK_ACTIONS.map((a, i) => (
            <button key={i} onClick={() => (a as any).graph ? onGraphOpen() : onTabChange(a.tab)} style={{
              background: 'white', border: `1px solid ${a.border}`, borderRadius: 16,
              padding: '16px 14px', textAlign: 'left', cursor: 'pointer',
              display: 'flex', alignItems: 'center', gap: 12,
              boxShadow: '0 2px 8px rgba(0,0,0,0.04)',
              transition: 'all 0.18s',
            }}>
              <div style={{ width: 40, height: 40, borderRadius: 12, background: a.bg, display: 'flex', alignItems: 'center', justifyContent: 'center', color: a.color, flexShrink: 0 }}>
                {a.icon}
              </div>
              <div>
                <div style={{ fontSize: 13, fontWeight: 700, color: T.slate900 }}>{a.label}</div>
                <div style={{ fontSize: 11, color: T.slate400, marginTop: 1 }}>{a.sub}</div>
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* ── Today's AI Tip ── */}
      <div style={{ margin: '0 16px 14px' }}>
        <div style={{ background: `linear-gradient(135deg, ${T.cream50}, ${T.cream100})`, border: `1px solid ${T.cream200}`, borderRadius: 16, padding: '14px 16px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
            <div style={{ width: 26, height: 26, borderRadius: 8, background: T.cream200, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <Sparkles size={13} color={T.cream600} />
            </div>
            <span style={{ fontSize: 12, fontWeight: 700, color: T.cream700 }}>今日 AI 健康贴士</span>
          </div>
          <p style={{ margin: 0, fontSize: 13, color: T.slate700, lineHeight: 1.7 }}>{tip}</p>
        </div>
      </div>

      {/* ── 今日健康打卡（支持自定义增/删/改）── */}
      <div style={{ padding: '0 12px 20px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
          <div style={{ fontSize: 14, fontWeight: 700, color: T.slate800 }}>今日健康打卡</div>
          <button onClick={() => setEditingItem('new')} style={{ width: 30, height: 30, borderRadius: '50%', border: `1.5px solid ${T.slate500}`, background: 'white', color: T.slate900, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <Plus size={17} />
          </button>
        </div>

        {checkinsLoading && (
          <div style={{ padding: 18, borderRadius: 18, background: 'white', border: `1px solid ${T.slate200}`, color: T.slate400, fontSize: 13, textAlign: 'center' }}>正在加载打卡项…</div>
        )}

        {!checkinsLoading && checkinList.length === 0 && (
          <button onClick={() => setEditingItem('new')} style={{ width: '100%', minHeight: 84, borderRadius: 14, border: `1.5px dashed ${T.slate300}`, background: 'rgba(255,255,255,0.62)', cursor: 'pointer', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 6, color: T.slate500 }}>
            <div style={{ width: 36, height: 36, borderRadius: '50%', border: `1.5px dashed ${T.slate300}`, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <Plus size={19} />
            </div>
            <div style={{ fontSize: 13, fontWeight: 800, color: T.slate700 }}>添加第一个打卡项</div>
            <div style={{ fontSize: 10, color: T.slate400 }}>从散步、喝水、睡眠记录开始</div>
          </button>
        )}

        {!checkinsLoading && checkinList.length > 0 && (
          <div style={{ display: 'grid', gap: 8 }}>
            {checkinList.map(h => {
              const accent = h.icon_bg || DEFAULT_CHECKIN_ICON_BG;
              const cardBackground = h.done_today
                ? `linear-gradient(135deg, ${accent}55, rgba(255,255,255,0.96))`
                : 'white';
              const cardBorder = h.done_today ? accent : T.slate200;
              return (
                <div key={h.code} style={{ position: 'relative' }}>
                  <div onClick={() => setDetailItem(h)} role="button" tabIndex={0} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') setDetailItem(h); }} style={{
                    width: '100%', border: `1px solid ${cardBorder}`, borderRadius: 14, background: cardBackground,
                    padding: '12px 12px 10px', cursor: 'pointer', textAlign: 'left',
                    boxShadow: h.done_today ? '0 8px 24px rgba(47,155,127,0.12)' : '0 4px 18px rgba(15,28,8,0.05)',
                  }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                      <CheckinIconBadge iconKey={h.icon} bg={h.icon_bg} size={44} />
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontSize: 15, lineHeight: 1.18, fontWeight: 800, color: T.slate900, marginBottom: 3, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{h.name}</div>
                        <div style={{ fontSize: 12, color: T.slate400, fontWeight: 700 }}>本月 {h.month_count} 次&nbsp;&nbsp; 本周 {h.week_count} 次</div>
                      </div>
                      <button
                        onClick={e => { e.stopPropagation(); setRecordingItem(h); }}
                        style={{ width: 44, height: 44, borderRadius: '50%', border: `2px solid ${h.done_today ? accent : T.slate200}`, background: h.done_today ? accent : 'white', boxShadow: h.done_today ? '0 6px 14px rgba(61,82,48,0.18)' : 'inset 0 1px 4px rgba(0,0,0,0.08)', color: T.slate700, display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', flexShrink: 0 }}
                        title="记录今日打卡"
                      >
                        {h.done_today && <CheckCircle size={23} />}
                      </button>
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(31, 1fr)', gap: 3, marginTop: 10 }}>
                      {h.last31.slice(-31).map((d, i) => (
                        <span key={`${d.date}-${i}`} style={{ width: 5, height: 5, borderRadius: '50%', background: d.done ? accent : T.slate200, display: 'block' }} />
                      ))}
                    </div>
                  </div>
                <button
                  onClick={e => { e.stopPropagation(); setActionError(''); setActionItem(h); }}
                  title={h.is_custom ? '编辑/删除' : '更多操作'}
                  style={{
                    position: 'absolute', top: 4, right: 3, width: 24, height: 24,
                    borderRadius: '50%', border: `1px solid ${T.slate200}`, background: 'rgba(255,255,255,0.92)',
                    cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center',
                    color: T.slate500, fontSize: 15, fontWeight: 900, lineHeight: 1, zIndex: 2,
                    boxShadow: '0 2px 6px rgba(15,28,8,0.08)',
                  }}
                >⋯</button>
              </div>
            );
            })}
          </div>
        )}
      </div>

      {/* 🆕 新增/编辑 modal */}
      {editingItem !== null && (
        <CheckinEditorModal
          initial={editingItem === 'new' ? null : editingItem}
          onClose={() => setEditingItem(null)}
          onSave={(p, code) => onSaveItem(p, code)}
        />
      )}
      {recordingItem && (
        <CheckinRecordSheet
          item={recordingItem}
          onClose={() => setRecordingItem(null)}
          onSaved={handleCheckinSaved}
        />
      )}
      {detailItem && (
        <CheckinDetailSheet
          item={detailItem}
          onClose={() => setDetailItem(null)}
        />
      )}
      {actionItem && !deleteConfirmItem && (
        <div
          onClick={() => setActionItem(null)}
          style={{ position: 'absolute', inset: 0, zIndex: 34, background: 'rgba(15,24,32,0.28)', display: 'flex', alignItems: 'flex-end' }}
        >
          <div
            onClick={e => e.stopPropagation()}
            style={{ width: '100%', background: 'white', borderRadius: '22px 22px 0 0', padding: '12px 16px 18px', boxShadow: '0 -14px 42px rgba(15,28,8,0.18)', animation: 'slideUpFade 0.22s cubic-bezier(0.32,0.72,0,1)' }}
          >
            <div style={{ width: 42, height: 4, borderRadius: 99, background: T.slate200, margin: '0 auto 12px' }} />
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14 }}>
              <CheckinIconBadge iconKey={actionItem.icon} bg={actionItem.icon_bg} size={40} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 15, fontWeight: 900, color: T.slate900, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{actionItem.name}</div>
                <div style={{ fontSize: 11, color: T.slate500, marginTop: 2, fontWeight: 700 }}>管理这个健康打卡项</div>
              </div>
              <button onClick={() => setActionItem(null)} style={{ width: 30, height: 30, border: 'none', borderRadius: 10, background: T.slate100, color: T.slate600, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <X size={14} />
              </button>
            </div>
            <div style={{ display: 'grid', gap: 8 }}>
              <button
                onClick={() => { setRecordingItem(actionItem); setActionItem(null); }}
                style={{ width: '100%', height: 44, borderRadius: 14, border: `1px solid ${T.slate200}`, background: T.slate50, color: T.slate800, display: 'flex', alignItems: 'center', gap: 10, padding: '0 14px', fontSize: 13, fontWeight: 900 }}
              >
                <CalendarCheck size={16} color={T.slate600} />
                记录今日打卡
              </button>
              <button
                onClick={() => { setDetailItem(actionItem); setActionItem(null); }}
                style={{ width: '100%', height: 44, borderRadius: 14, border: `1px solid ${T.slate200}`, background: 'white', color: T.slate800, display: 'flex', alignItems: 'center', gap: 10, padding: '0 14px', fontSize: 13, fontWeight: 900 }}
              >
                <History size={16} color={T.slate600} />
                查看打卡记录
              </button>
              {actionItem.is_custom ? (
                <>
                  <button
                    onClick={() => { setEditingItem(actionItem); setActionItem(null); }}
                    style={{ width: '100%', height: 44, borderRadius: 14, border: `1px solid ${T.slate200}`, background: T.slate50, color: T.slate800, display: 'flex', alignItems: 'center', gap: 10, padding: '0 14px', fontSize: 13, fontWeight: 900 }}
                  >
                    <Edit3 size={16} color={T.slate600} />
                    编辑打卡项
                  </button>
                  <button
                    onClick={() => { setActionError(''); setDeleteConfirmItem(actionItem); }}
                    style={{ width: '100%', height: 44, borderRadius: 14, border: `1px solid ${T.red200}`, background: T.red50, color: T.red700, display: 'flex', alignItems: 'center', gap: 10, padding: '0 14px', fontSize: 13, fontWeight: 900 }}
                  >
                    <Trash2 size={16} />
                    删除打卡项
                  </button>
                </>
              ) : (
                <div style={{ borderRadius: 14, border: `1px solid ${T.slate200}`, background: T.slate50, color: T.slate500, padding: '10px 12px', fontSize: 12, lineHeight: 1.6, fontWeight: 750 }}>
                  系统默认打卡项不可编辑或删除，可以继续记录和查看历史。
                </div>
              )}
              <button
                onClick={() => setActionItem(null)}
                style={{ width: '100%', height: 42, borderRadius: 14, border: `1px solid ${T.slate200}`, background: 'white', color: T.slate600, fontSize: 13, fontWeight: 850 }}
              >
                取消
              </button>
            </div>
          </div>
        </div>
      )}
      {deleteConfirmItem && (
        <div
          onClick={() => !actionLoading && setDeleteConfirmItem(null)}
          style={{ position: 'absolute', inset: 0, zIndex: 36, background: 'rgba(15,24,32,0.38)', display: 'flex', alignItems: 'flex-end' }}
        >
          <div
            onClick={e => e.stopPropagation()}
            style={{ width: '100%', background: 'white', borderRadius: '22px 22px 0 0', padding: '14px 16px 18px', boxShadow: '0 -14px 42px rgba(15,28,8,0.2)', animation: 'slideUpFade 0.22s cubic-bezier(0.32,0.72,0,1)' }}
          >
            <div style={{ width: 42, height: 4, borderRadius: 99, background: T.slate200, margin: '0 auto 14px' }} />
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
              <div style={{ width: 38, height: 38, borderRadius: 13, background: T.red50, color: T.red700, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                <AlertTriangle size={18} />
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 16, fontWeight: 950, color: T.slate900 }}>删除打卡项？</div>
                <div style={{ marginTop: 6, fontSize: 12, lineHeight: 1.6, color: T.slate600 }}>
                  将删除「{deleteConfirmItem.name}」及其历史打卡记录，此操作不可撤销。
                </div>
              </div>
            </div>
            {actionError && (
              <div style={{ marginTop: 12, borderRadius: 12, background: T.red50, border: `1px solid ${T.red200}`, color: T.red700, padding: '8px 10px', fontSize: 12, fontWeight: 750 }}>
                {actionError}
              </div>
            )}
            <div style={{ display: 'flex', gap: 10, marginTop: 16 }}>
              <button
                disabled={actionLoading}
                onClick={() => setDeleteConfirmItem(null)}
                style={{ flex: 1, height: 42, borderRadius: 14, border: `1px solid ${T.slate200}`, background: 'white', color: T.slate700, fontSize: 13, fontWeight: 900, opacity: actionLoading ? 0.6 : 1 }}
              >
                取消
              </button>
              <button
                disabled={actionLoading}
                onClick={() => onDeleteCustom(deleteConfirmItem)}
                style={{ flex: 1, height: 42, borderRadius: 14, border: 'none', background: actionLoading ? T.slate300 : T.red500, color: 'white', fontSize: 13, fontWeight: 900 }}
              >
                {actionLoading ? '删除中...' : '确认删除'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

// ─── 打卡项 编辑/新增 modal ────────────────────────────────────────
const CheckinEditorModal: React.FC<{
  initial: CheckinItem | null;
  onClose: () => void;
  onSave: (payload: { name: string; icon: string; icon_bg: string }, editCode?: string) => void;
}> = ({ initial, onClose, onSave }) => {
  const [name, setName] = useState(initial?.name || '');
  const [icon, setIcon] = useState(CHECKIN_ICON_MAP.has(initial?.icon || '') ? (initial?.icon || DEFAULT_CHECKIN_ICON) : DEFAULT_CHECKIN_ICON);
  const [iconBg, setIconBg] = useState(initial?.icon_bg || DEFAULT_CHECKIN_ICON_BG);
  const [composing, setComposing] = useState(false);
  const [saving, setSaving] = useState(false);

  const valid = name.trim().length > 0 && name.trim().length <= 30;

  const handleSave = async () => {
    if (!valid || saving) return;
    setSaving(true);
    try {
      await onSave({ name: name.trim(), icon, icon_bg: iconBg }, initial?.code);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div onClick={onClose} style={{
      position: 'absolute', inset: 0, background: 'rgba(15,24,32,0.45)',
      zIndex: 30, display: 'flex', alignItems: 'flex-end',
      animation: 'slideUpFade 0.18s cubic-bezier(0.32,0.72,0,1)',
    }}>
      <div onClick={e => e.stopPropagation()} style={{
        width: '100%', background: 'white', borderRadius: '20px 20px 0 0',
        padding: '18px 20px 22px', maxHeight: '80%', overflowY: 'auto',
        animation: 'slideUpFade 0.28s cubic-bezier(0.32,0.72,0,1)',
      }} className="checkin-editor-scroll">
        {/* 拖拽手柄 */}
        <div style={{ width: 36, height: 4, background: T.slate200, borderRadius: 2, margin: '0 auto 14px' }} />
        <div style={{ fontSize: 16, fontWeight: 800, color: T.slate900, marginBottom: 14 }}>
          {initial ? '编辑打卡项' : '新增打卡项'}
        </div>

        {/* 名称 */}
        <div style={{ marginBottom: 14 }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: T.slate600, marginBottom: 6 }}>
            名称 <span style={{ color: T.red500 }}>*</span>
          </div>
          <input
            value={name}
            onChange={e => setName(e.target.value)}
            onCompositionStart={() => setComposing(true)}
            onCompositionEnd={() => setComposing(false)}
            onKeyDown={e => { if (e.key === 'Enter' && !composing && valid) { e.preventDefault(); handleSave(); } }}
            placeholder="例如：每日测血压"
            maxLength={30}
            autoFocus
            style={{
              width: '100%', padding: '10px 12px', borderRadius: 10,
              border: `1.5px solid ${T.slate200}`, fontSize: 13, color: T.slate800,
              outline: 'none', boxSizing: 'border-box',
            }}
          />
          <div style={{ fontSize: 10, color: T.slate400, textAlign: 'right', marginTop: 3 }}>{name.length}/30</div>
        </div>

        {/* 图标选择 */}
        <div style={{ marginBottom: 14 }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: T.slate600, marginBottom: 6 }}>图标</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: 7 }}>
            {CHECKIN_ICON_OPTIONS.map(({ key, label, Icon }) => (
              <button key={key} type="button" onClick={() => setIcon(key)} title={label} style={{
                aspectRatio: '1', borderRadius: 12,
                background: icon === key ? iconBg : T.slate50,
                border: `1.5px solid ${icon === key ? T.mint500 : T.slate200}`,
                cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center',
                color: icon === key ? T.mint700 : T.slate500,
                transition: 'all 0.15s',
              }}>
                <Icon size={19} strokeWidth={2.1} />
              </button>
            ))}
          </div>
        </div>

        <div style={{ marginBottom: 16 }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: T.slate600, marginBottom: 6 }}>底色</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(8, 1fr)', gap: 7 }}>
            {CHECKIN_ICON_BG_OPTIONS.map(bg => (
              <button key={bg} type="button" onClick={() => setIconBg(bg)} style={{
                aspectRatio: '1', borderRadius: 11, border: `1.5px solid ${iconBg === bg ? T.mint600 : T.slate200}`,
                background: bg, cursor: 'pointer', boxShadow: iconBg === bg ? '0 0 0 2px rgba(171,200,155,0.18)' : 'none',
                display: 'flex', alignItems: 'center', justifyContent: 'center', color: T.slate700,
              }}>
                {iconBg === bg && <CheckCircle size={16} strokeWidth={2.2} />}
              </button>
            ))}
          </div>
          <div style={{ marginTop: 10, display: 'flex', alignItems: 'center', gap: 10, color: T.slate500, fontSize: 12, fontWeight: 700 }}>
            <CheckinIconBadge iconKey={icon} bg={iconBg} size={38} />
            预览图标样式
          </div>
        </div>

        {/* 按钮 */}
        <div style={{ display: 'flex', gap: 10 }}>
          <button onClick={onClose} disabled={saving} style={{
            flex: 1, padding: '12px', borderRadius: 12, border: `1.5px solid ${T.slate200}`,
            background: 'white', color: T.slate600, fontSize: 14, fontWeight: 600,
            cursor: saving ? 'not-allowed' : 'pointer', opacity: saving ? 0.5 : 1,
          }}>取消</button>
          <button onClick={handleSave} disabled={!valid || saving} style={{
            flex: 1.4, padding: '12px', borderRadius: 12, border: 'none',
            background: (!valid || saving) ? T.slate200 : 'linear-gradient(135deg, #5EC99D, #2F9B7F)',
            color: (!valid || saving) ? T.slate400 : 'white',
            fontSize: 14, fontWeight: 700,
            cursor: (!valid || saving) ? 'not-allowed' : 'pointer',
            display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
            boxShadow: (!valid || saving) ? 'none' : '0 4px 12px rgba(50,160,95,0.25)',
          }}>
            {saving ? '保存中…' : (initial ? '保存修改' : '创建打卡项')}
          </button>
        </div>
      </div>
    </div>
  );
};

// ─── 今日打卡记录面板 ────────────────────────────────────────────────
const CheckinRecordSheet: React.FC<{
  item: CheckinItem;
  onClose: () => void;
  onSaved: (itemCode: string, updatedItem?: any) => void | Promise<void>;
}> = ({ item, onClose, onSaved }) => {
  const [note, setNote] = useState('');
  const [images, setImages] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const fileRef = useRef<HTMLInputElement | null>(null);
  const now = new Date();
  const checkinDate = toISODate(now);

  const handlePick = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []).slice(0, 3 - images.length);
    e.target.value = '';
    if (!files.length) return;
    const next = await Promise.all(files.map(fileToBase64));
    setImages(prev => [...prev, ...next].slice(0, 3));
  };

  const handleSave = async () => {
    if (saving) return;
    setSaving(true);
    setError('');
    try {
      const uploaded: string[] = [];
      for (const img of images) {
        if (img.startsWith('data:')) {
          const res: any = await api.uploadImage(img);
          uploaded.push(res?.url || res?.image_url || img);
        } else {
          uploaded.push(img);
        }
      }
      const saved: any = await api.saveCheckin({
        item_code: item.code,
        status: 'done',
        checkin_date: checkinDate,
        value_json: {
          note: note.trim(),
          images: uploaded,
          recorded_at: new Date().toISOString(),
        },
      });
      await onSaved(item.code, saved?.item);
    } catch (e: any) {
      setError(e?.message || '保存失败，请稍后重试');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div onClick={() => { if (!saving) onClose(); }} style={{ position: 'absolute', inset: 0, zIndex: 35, background: 'rgba(15,24,32,0.28)', display: 'flex', alignItems: 'flex-end' }}>
      <div onClick={e => e.stopPropagation()} style={{ width: '100%', background: 'white', borderRadius: '22px 22px 0 0', padding: '10px 20px 24px', boxShadow: '0 -10px 30px rgba(0,0,0,0.12)' }}>
        <div style={{ width: 42, height: 5, borderRadius: 99, background: T.slate200, margin: '2px auto 14px' }} />
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 22 }}>
          <button onClick={onClose} disabled={saving} style={{ border: 'none', background: 'transparent', color: T.slate400, fontSize: 18, fontWeight: 700, cursor: saving ? 'not-allowed' : 'pointer', opacity: saving ? 0.55 : 1 }}>取消</button>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
            <CheckinIconBadge iconKey={item.icon} bg={item.icon_bg} size={34} />
            <div style={{ fontSize: 20, fontWeight: 900, color: T.slate900, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.name}</div>
          </div>
          <button onClick={handleSave} disabled={saving} style={{ width: 42, height: 42, borderRadius: '50%', border: `2px solid ${T.slate900}`, background: 'white', color: T.slate900, display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: saving ? 'not-allowed' : 'pointer', opacity: saving ? 0.55 : 1 }}>
            {saving ? <RefreshCw size={22} style={{ animation: 'spin 0.9s linear infinite' }} /> : <CheckCircle size={28} />}
          </button>
        </div>

        {error && (
          <div style={{ marginTop: -10, marginBottom: 14, borderRadius: 12, background: T.red50, border: `1px solid ${T.red200}`, color: T.red700, padding: '9px 11px', fontSize: 12, fontWeight: 800, lineHeight: 1.5 }}>
            {error}
          </div>
        )}

        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 22 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, color: T.slate400, fontSize: 17, fontWeight: 800 }}>
            <Clock size={22} color={T.slate700} />
            时间
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <span style={{ padding: '9px 12px', borderRadius: 10, background: T.slate50, color: T.slate900, fontSize: 16, fontWeight: 700 }}>{now.getFullYear()} 年 {now.getMonth() + 1}月{now.getDate()}日</span>
            <span style={{ padding: '9px 12px', borderRadius: 10, background: T.slate50, color: T.slate900, fontSize: 16, fontWeight: 700 }}>{String(now.getHours()).padStart(2, '0')}:{String(now.getMinutes()).padStart(2, '0')}</span>
          </div>
        </div>

        <textarea
          value={note}
          onChange={e => setNote(e.target.value.slice(0, 240))}
          placeholder="写下你的感想、心得或今天的收获..."
          style={{ width: '100%', minHeight: 116, resize: 'none', border: 'none', outline: 'none', borderRadius: 14, background: T.slate50, padding: 14, fontSize: 15, lineHeight: 1.6, color: T.slate800, boxSizing: 'border-box', marginBottom: 10 }}
        />
        <div style={{ fontSize: 13, color: T.slate400, marginBottom: 8 }}>{images.length}/3</div>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          {images.map((img, i) => (
            <div key={i} style={{ position: 'relative', width: 74, height: 74, borderRadius: 12, overflow: 'hidden', border: `1px solid ${T.slate200}` }}>
              <img src={img} alt={`checkin-${i + 1}`} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
              <button onClick={() => setImages(prev => prev.filter((_, idx) => idx !== i))} style={{ position: 'absolute', top: 4, right: 4, width: 20, height: 20, borderRadius: '50%', border: 'none', background: 'rgba(0,0,0,0.55)', color: 'white', display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer' }}><X size={13} /></button>
            </div>
          ))}
          {images.length < 3 && (
            <button onClick={() => fileRef.current?.click()} style={{ width: 74, height: 74, borderRadius: 12, border: `1.5px dashed ${T.slate300}`, background: 'white', color: T.slate400, cursor: 'pointer', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 4 }}>
              <Plus size={22} />
              <span style={{ fontSize: 12, fontWeight: 700 }}>添加图片</span>
            </button>
          )}
          <input ref={fileRef} type="file" accept="image/*" multiple onChange={handlePick} style={{ display: 'none' }} />
        </div>
      </div>
    </div>
  );
};

// ─── 打卡详情页 ────────────────────────────────────────────────────
const CheckinDetailSheet: React.FC<{
  item: CheckinItem;
  onClose: () => void;
}> = ({ item, onClose }) => {
  const [stats, setStats] = useState<any>(null);
  const [records, setRecords] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const today = new Date();
  const doneSet = new Set(records.filter(r => r.status === 'done').map(r => String(r.date)));
  const stat = stats?.stats || {};

  useEffect(() => {
    let alive = true;
    (async () => {
      setLoading(true);
      try {
        const from = toISODate(yearStart(today));
        const to = toISODate(today);
        const [s, h]: any[] = await Promise.all([
          api.getCheckinStats(item.code),
          api.getCheckinHistory(item.code, from, to),
        ]);
        if (!alive) return;
        setStats(s);
        setRecords(Array.isArray(h?.records) ? h.records : []);
      } catch {
        if (alive) {
          setStats(null);
          setRecords([]);
        }
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [item.code]);

  const weekDays = Array.from({ length: 7 }, (_, i) => addDays(weekStart(today), i));
  const monthFirst = monthStart(today);
  const monthDays = new Date(today.getFullYear(), today.getMonth() + 1, 0).getDate();
  const monthCells = Array.from({ length: monthDays }, (_, i) => addDays(monthFirst, i));
  const yearFirst = yearStart(today);
  const yearLast = new Date(today.getFullYear(), 11, 31);
  const yearHeatmapStart = weekStart(yearFirst);
  const yearHeatmapEnd = addDays(weekStart(yearLast), 6);
  const yearHeatmapTotal = Math.round((yearHeatmapEnd.getTime() - yearHeatmapStart.getTime()) / 86400000) + 1;
  const yearHeatmapColumns = Math.ceil(yearHeatmapTotal / 7);
  const yearCells = Array.from({ length: yearHeatmapTotal }, (_, i) => {
    const date = addDays(yearHeatmapStart, i);
    return {
      date,
      iso: toISODate(date),
      valid: date.getFullYear() === today.getFullYear() && date <= today,
      inYear: date.getFullYear() === today.getFullYear(),
    };
  });
  const yearMonthLabels = Array.from({ length: 12 }, (_, month) => ({
    label: `${month + 1}月`,
    column: Math.floor((new Date(today.getFullYear(), month, 1).getTime() - yearHeatmapStart.getTime()) / 86400000 / 7),
  }));
  const detailAccent = item.icon_bg || DEFAULT_CHECKIN_ICON_BG;

  const metric = [
    ['总次数', stat.total_count ?? 0],
    ['总天数', stat.total_days ?? 0],
    ['最佳连续', stat.best_streak ?? 0],
    ['本月打卡', stat.month_count ?? item.month_count],
    ['本周打卡', stat.week_count ?? item.week_count],
    ['当前连续', stat.current_streak ?? 0],
  ];

  const RecordCard: React.FC<{ title: string; children: React.ReactNode; count: number }> = ({ title, children, count }) => (
    <div style={{ background: 'white', borderRadius: 14, padding: '13px 13px 12px', marginBottom: 12 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 7, marginBottom: 13 }}>
        <div style={{ fontSize: 14, fontWeight: 800, color: T.slate900, flexShrink: 0 }}>{title}</div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 5, color: T.slate500, fontSize: 11, fontWeight: 800, minWidth: 0 }}>
          <button style={{ width: 22, height: 22, borderRadius: 8, border: 'none', background: T.slate50, color: T.slate700, fontSize: 15, lineHeight: 1 }}>‹</button>
          {title === '周打卡' ? `${toISODate(weekDays[0]).slice(5).replace('-', '/')} - ${toISODate(weekDays[6]).slice(5).replace('-', '/')}` : title === '月打卡' ? `${today.getFullYear()} 年 ${today.getMonth() + 1}月` : `${today.getFullYear()} 年`}
          <button style={{ width: 22, height: 22, borderRadius: 8, border: 'none', background: T.slate50, color: T.slate700, fontSize: 15, lineHeight: 1 }}>›</button>
        </div>
      </div>
      {children}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 13, fontSize: 11, fontWeight: 800 }}>
        <span style={{ color: '#35b66d', display: 'flex', alignItems: 'center', gap: 5 }}><CheckCircle size={14} />{title === '周打卡' ? '本周' : title === '月打卡' ? '本月' : '本年'} {count} 次</span>
        <span style={{ color: '#ff9500', display: 'flex', alignItems: 'center', gap: 5 }}><Flame size={14} />{stat.current_streak ?? 0} 天连击</span>
      </div>
    </div>
  );

  return (
    <div style={{ position: 'absolute', inset: 0, zIndex: 4, background: T.slate50, overflowY: 'auto', overflowX: 'hidden', boxSizing: 'border-box' }} className="mobile-scroll">
      <div style={{ height: 76, background: T.slate50, display: 'flex', alignItems: 'flex-end', justifyContent: 'flex-end', padding: '0 16px 10px', boxSizing: 'border-box' }}>
          <button onClick={onClose} style={{ border: 'none', background: 'transparent', color: T.slate900, fontSize: 15, fontWeight: 850, cursor: 'pointer', padding: '8px 4px', lineHeight: 1 }}>
            完成
          </button>
      </div>
      <div style={{ padding: '4px 12px 30px' }}>
        <div style={{ background: 'white', borderRadius: 16, padding: 14, marginBottom: 12 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <CheckinIconBadge iconKey={item.icon} bg={item.icon_bg} size={52} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 18, fontWeight: 850, color: T.slate900, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.name}</div>
              <div style={{ display: 'flex', gap: 5, marginTop: 6, flexWrap: 'wrap' }}>
                <span style={{ padding: '2px 7px', borderRadius: 7, background: '#d8ebff', color: '#1d7be8', fontSize: 10, fontWeight: 800 }}>经典打卡</span>
                <span style={{ padding: '2px 7px', borderRadius: 7, background: T.slate100, color: T.slate900, fontSize: 10, fontWeight: 800 }}>每天 1 次</span>
              </div>
            </div>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 13, color: T.slate500, fontSize: 12, fontWeight: 800 }}>
            <span><History size={14} style={{ verticalAlign: -2, marginRight: 5 }} />创建于 {item.created_at ? item.created_at.slice(0, 10) : toISODate(today)}</span>
            <span style={{ color: '#35b66d' }}>● 活跃</span>
          </div>
        </div>

        <div style={{ background: 'white', borderRadius: 14, padding: 14, marginBottom: 12 }}>
          <div style={{ fontSize: 14, fontWeight: 800, color: T.slate900, marginBottom: 14 }}>统计概览</div>
          {loading ? <div style={{ color: T.slate400, fontSize: 13 }}>正在加载记录…</div> : (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', rowGap: 17 }}>
              {metric.map(([label, value], i) => (
                <div key={String(label)} style={{ textAlign: 'center', borderRight: i % 3 !== 2 ? `1px solid ${T.slate200}` : 'none' }}>
                  <div style={{ fontSize: 25, fontWeight: 900, color: T.slate900, lineHeight: 1 }}>{String(value)}</div>
                  <div style={{ fontSize: 11, color: T.slate500, fontWeight: 900, marginTop: 7 }}>{label}</div>
                </div>
              ))}
            </div>
          )}
        </div>

        <RecordCard title="周打卡" count={stat.week_count ?? item.week_count}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7,1fr)', textAlign: 'center', gap: 6 }}>
            {'一二三四五六日'.split('').map((w, i) => <div key={w} style={{ color: T.slate500, fontSize: 11, fontWeight: 900 }}>{w}<br /><span style={{ color: i === today.getDay() - 1 ? T.slate900 : T.slate500 }}>{weekDays[i].getDate()}</span></div>)}
            {weekDays.map(d => <div key={toISODate(d)} style={{ margin: '7px auto 0', width: 20, height: 20, borderRadius: '50%', background: doneSet.has(toISODate(d)) ? detailAccent : T.slate100 }} />)}
          </div>
        </RecordCard>

        <RecordCard title="月打卡" count={stat.month_count ?? item.month_count}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7,1fr)', gap: 7, textAlign: 'center' }}>
            {monthCells.map(d => <div key={toISODate(d)} style={{ color: doneSet.has(toISODate(d)) ? T.slate900 : T.slate300, fontSize: 12, fontWeight: 800 }}>{d.getDate()}<div style={{ width: 11, height: 11, borderRadius: '50%', background: doneSet.has(toISODate(d)) ? detailAccent : 'transparent', margin: '4px auto 0' }} /></div>)}
          </div>
        </RecordCard>

        <RecordCard title="年度总览" count={stat.year_count ?? records.length}>
          <div className="mobile-scroll" style={{ overflowX: 'auto', overflowY: 'hidden', paddingBottom: 2 }}>
            <div style={{ width: 24 + yearHeatmapColumns * 15, minWidth: '100%' }}>
              <div style={{ display: 'flex', alignItems: 'center', marginBottom: 7 }}>
                <div style={{ width: 24, flexShrink: 0 }} />
                <div style={{ display: 'grid', gridTemplateColumns: `repeat(${yearHeatmapColumns}, 12px)`, columnGap: 3, flexShrink: 0 }}>
                  {yearMonthLabels.map(m => (
                    <div key={m.label} style={{ gridColumn: `${m.column + 1} / span 4`, color: T.slate500, fontSize: 11, fontWeight: 800, whiteSpace: 'nowrap' }}>
                      {m.label}
                    </div>
                  ))}
                </div>
              </div>
              <div style={{ display: 'flex', alignItems: 'flex-start' }}>
                <div style={{ width: 24, flexShrink: 0, display: 'grid', gridTemplateRows: 'repeat(7, 12px)', rowGap: 3, color: T.slate400, fontSize: 11, fontWeight: 800, lineHeight: '12px' }}>
                  {'一二三四五六日'.split('').map(w => <span key={w}>{w}</span>)}
                </div>
                <div style={{ display: 'grid', gridTemplateRows: 'repeat(7, 12px)', gridAutoFlow: 'column', gridAutoColumns: '12px', gap: '3px 3px', flexShrink: 0 }}>
                  {yearCells.map((d, i) => (
                    <span
                      key={`${d.iso}-${i}`}
                      title={d.inYear ? d.iso : ''}
                      style={{
                        width: 12,
                        height: 12,
                        borderRadius: 3,
                        background: d.valid && doneSet.has(d.iso) ? detailAccent : d.inYear ? 'rgba(15,28,8,0.06)' : 'transparent',
                        display: 'block',
                      }}
                    />
                  ))}
                </div>
              </div>
            </div>
          </div>
        </RecordCard>
      </div>
    </div>
  );
};

// ─── EvidenceChainInline ───────────────────────────────────────────
// 移动端紧凑版证据链：默认折叠成"🔍 证据链 · final_claim · 置信 87%"，
// 点开后展示推理路径条 + refs 列表（按 type 分桶颜色）。
const EVIDENCE_TYPE_META: Record<string, { color: string; bg: string; label: string }> = {
  kg:      { color: '#7C3AED', bg: '#f1ebfa', label: 'KG' },
  pdf:     { color: '#0891B2', bg: '#e0f2f7', label: '指南' },
  web:     { color: '#059669', bg: '#dcfce7', label: '公网' },
  image:   { color: '#DB2777', bg: '#fce8f3', label: '影像' },
  profile: { color: '#D97706', bg: '#fef3c7', label: '档案' },
};
function formatActor(actor?: string): string {
  if (!actor) return 'agent';
  // "general.search_local_guidelines" → "本地指南"
  const tail = actor.split('.').pop() || actor;
  const map: Record<string, string> = {
    search_local_guidelines: '本地指南',
    search_medical_graph: '知识图谱',
    search_public_internet: '公网检索',
    synthesis: '综合推演',
    fallback: '熔断兜底',
    rumor_advocate: '辩护方',
    rumor_skeptic: '质疑方',
    rumor_judge: '终审',
    'hallucination_guard': '幻觉守门',
    rumor: '辟谣',
    risk_router: '风险路由',
    fast_path: '快速核查',
    advocate: '辩护方',
    skeptic: '质疑方',
    judge: '终审',
    med_extractor: '药物抽取',
    med_pharmacist: '药师审查',
    med_reviewer: '综合审查',
  };
  return map[tail] || tail.replace(/_/g, ' ');
}

const EvidenceChainInline: React.FC<{ chain: any }> = ({ chain }) => {
  const [open, setOpen] = useState(false);
  if (!chain) return null;
  const refs: any[] = Array.isArray(chain.refs) ? chain.refs : [];
  const path: any[] = Array.isArray(chain.reasoning_path) ? chain.reasoning_path : [];
  const triples: any[] = Array.isArray(chain.triples) ? chain.triples : [];
  if (refs.length === 0 && path.length === 0 && triples.length === 0) return null;

  const conf = typeof chain.confidence === 'number' ? chain.confidence : null;
  const confColor = conf == null ? T.slate500 : conf >= 0.85 ? T.mint600 : conf >= 0.6 ? '#a88028' : '#b84850';
  const claim = (chain.final_claim || '').slice(0, 80);

  return (
    <div style={{
      marginTop: 6, paddingLeft: 4, width: '100%',
      maxWidth: '100%', minWidth: 0, overflow: 'hidden',
    }}>
      <button onClick={() => setOpen(o => !o)} style={{
        width: '100%', padding: '7px 11px', borderRadius: 10,
        background: 'linear-gradient(135deg, #fefdf5 0%, #faf6e6 100%)',
        border: `1px solid #f0eac1`, cursor: 'pointer',
        display: 'flex', alignItems: 'center', gap: 6, textAlign: 'left',
        color: '#7a6c28', fontSize: 11.5, fontWeight: 700,
      }}>
        <span>🔍 证据链</span>
        {claim && (
          <span style={{ fontWeight: 500, color: '#a88028', flex: 1, minWidth: 0,
            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            · {claim}
          </span>
        )}
        <span style={{ fontSize: 10, fontWeight: 700, color: confColor,
          padding: '1px 6px', borderRadius: 6, border: `1px solid ${confColor}40` }}>
          {conf == null ? `${refs.length} refs` : `${(conf * 100).toFixed(0)}%`}
        </span>
        <span style={{ fontSize: 10, color: '#a88028' }}>{open ? '▾' : '▸'}</span>
      </button>

      {open && (
        <div style={{
          marginTop: 5, padding: '10px 12px', borderRadius: 10,
          background: 'white', border: `1px solid ${T.slate200}`,
          fontSize: 11.5, color: T.slate600, lineHeight: 1.6,
        }}>
          {/* 推理路径条 */}
          {path.length > 0 && (
            <div style={{ marginBottom: refs.length || triples.length ? 10 : 0 }}>
              <div style={{ fontSize: 10, color: T.slate400, fontWeight: 700, marginBottom: 5, letterSpacing: 0.3 }}>
                推理路径
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {path.slice(0, 6).map((s: any, i: number) => (
                  <div key={i} style={{ display: 'flex', gap: 7, alignItems: 'flex-start' }}>
                    <div style={{
                      width: 18, height: 18, borderRadius: '50%',
                      background: T.mint100, color: T.mint700,
                      fontSize: 10, fontWeight: 800,
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      flexShrink: 0, marginTop: 1,
                    }}>{s.step ?? i + 1}</div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontWeight: 700, color: T.slate800, fontSize: 11.5 }}>
                        <span style={{
                          fontSize: 10, color: T.mint600,
                          padding: '0 5px', borderRadius: 4,
                          background: T.mint50, marginRight: 5, fontWeight: 700,
                        }}>{formatActor(s.actor)}</span>
                        {s.action || ''}
                      </div>
                      {s.output_summary && (
                        <div style={{ color: T.slate500, fontSize: 11, marginTop: 2,
                          wordBreak: 'break-word', overflowWrap: 'anywhere' }}>
                          {String(s.output_summary).slice(0, 120)}
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* 三元组 */}
          {triples.length > 0 && (
            <div style={{ marginBottom: refs.length ? 10 : 0 }}>
              <div style={{ fontSize: 10, color: T.slate400, fontWeight: 700, marginBottom: 5, letterSpacing: 0.3 }}>
                关键事实
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                {triples.slice(0, 5).map((t: any, i: number) => (
                  <div key={i} style={{ fontSize: 11.5, color: T.slate700,
                    wordBreak: 'break-word', overflowWrap: 'anywhere' }}>
                    <strong>{t.head}</strong>
                    <span style={{ margin: '0 5px', color: T.mint600, fontWeight: 700 }}>—{t.relation}—</span>
                    <span>{t.tail}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* refs 列表 */}
          {refs.length > 0 && (
            <div>
              <div style={{ fontSize: 10, color: T.slate400, fontWeight: 700, marginBottom: 5, letterSpacing: 0.3 }}>
                来源（{refs.length}）
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                {refs.slice(0, 8).map((r: any, i: number) => {
                  const meta = EVIDENCE_TYPE_META[r.type] || { color: T.slate600, bg: T.slate100, label: r.type || 'src' };
                  const url = r.locator?.url;
                  const inner = (
                    <div style={{ display: 'flex', gap: 5, alignItems: 'flex-start',
                      minWidth: 0, padding: '5px 7px', borderRadius: 7,
                      background: meta.bg, border: `1px solid ${meta.color}33` }}>
                      <span style={{
                        fontSize: 9, color: meta.color, fontWeight: 700,
                        padding: '1px 5px', borderRadius: 4, background: 'white',
                        flexShrink: 0,
                      }}>{meta.label}</span>
                      <span style={{ flex: 1, minWidth: 0, fontSize: 11,
                        color: T.slate700, fontWeight: 600,
                        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {r.label || r.ref_id}
                      </span>
                    </div>
                  );
                  return url
                    ? <a key={i} href={url} target="_blank" rel="noreferrer" style={{ textDecoration: 'none' }}>{inner}</a>
                    : <div key={i}>{inner}</div>;
                })}
                {refs.length > 8 && (
                  <div style={{ fontSize: 10, color: T.slate400, fontStyle: 'italic' }}>
                    …还有 {refs.length - 8} 条来源
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

const makeWelcomeMessage = (time: string): ChatMsg => ({
  id: 0,
  role: 'ai',
  content: '您好！我是 TrustMed 多智能体医疗助手 🌿\n\n我集成了**内科、中医、营养、心理、骨科、急诊**六大专科 AI，可以为您提供可信的医疗健康咨询。\n\n请告诉我您的问题或症状，我会为您进行全面分析。',
  time,
});

const toAbsoluteMediaUrl = (url?: string | null) => {
  if (!url) return undefined;
  if (url.startsWith('http://') || url.startsWith('https://') || url.startsWith('data:')) return url;
  return `${api.API_BASE}${url}`;
};

const normalizeChatImages = (value: any): string[] => (
  Array.isArray(value) ? value.map((v) => toAbsoluteMediaUrl(String(v))).filter(Boolean) as string[] : []
);

const isHighRiskChatMessage = (msg: ChatMsg | null) => {
  if (!msg) return false;
  const text = `${msg.content || ''} ${msg.route || ''}`.toLowerCase();
  return /120|急诊|立即就医|马上就医|胸痛|呼吸困难|昏迷|大出血|过敏性休克|emergency|urgent/.test(text);
};

const extractSystemNotice = (content: string) => {
  const notices: Array<{ severity: 'info' | 'warn' | 'high'; text: string }> = [];
  const kept: string[] = [];
  String(content || '').split('\n').forEach((line) => {
    const raw = line.trim();
    const normalized = raw.replace(/^>\s*/, '').replace(/[*_`]/g, '').trim();
    const isCredibility = /可信度提示|事实声明|证据交叉|依据不足|无证据|全科检索超时|稍后再试/.test(normalized);
    if (!isCredibility) {
      kept.push(line);
      return;
    }
    if (/全科检索超时|稍后再试/.test(normalized)) {
      notices.push({ severity: 'warn', text: '检索超时，建议稍后重试或补充更具体的问题。' });
    } else if (/0\s*条|无证据|依据不足/.test(normalized)) {
      notices.push({ severity: 'warn', text: '依据不足，关键决策请以专业医生意见为准。' });
    } else {
      notices.push({ severity: 'info', text: normalized.replace(/^[🟡⚠️✅\s]+/, '') });
    }
  });
  const unique = notices.filter((n, i, arr) => arr.findIndex(x => x.text === n.text) === i);
  return { content: kept.join('\n').trim(), notices: unique };
};

const getMessageRiskLevel = (msg: ChatMsg | null): 'normal' | 'warn' | 'high' => {
  if (!msg) return 'normal';
  if (isHighRiskChatMessage(msg)) return 'high';
  const action = msg.halluc?.action || msg.traceData?.hallucination_check?.action;
  if (action === 'WARN' || action === 'REJECT') return 'warn';
  if (extractSystemNotice(msg.content).notices.some(n => n.severity !== 'info')) return 'warn';
  return 'normal';
};

const compactStatusText = (status?: string) => {
  if (!status) return '正在综合研判';
  const text = status
    .replace(/\p{Extended_Pictographic}/gu, '')
    .replace(/[\u200d\ufe0f]/g, '')
    .replace(/[^\p{L}\p{N}\s]/gu, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  if (/检索|知识库|图谱|公网/.test(text)) return '正在检索知识库';
  if (/安全|校验|复核|幻觉/.test(text)) return '正在进行安全校验';
  if (/会话|重新连接/.test(text)) return '正在重新连接';
  if (/辩论|多智能体|专科|综合|分析|症状|研判|思考|医生|全科/.test(text)) return '正在综合研判';
  return '正在综合研判';
};

const MobileTrustSheet: React.FC<{ msg: ChatMsg; onClose: () => void }> = ({ msg, onClose }) => {
  const trace = msg.traceData || {};
  const chain = msg.evidenceChain || trace.evidence_chain || {};
  const refs: any[] = Array.isArray(chain.refs) ? chain.refs : Array.isArray(trace.sources) ? trace.sources : [];
  const path: any[] = Array.isArray(chain.reasoning_path)
    ? chain.reasoning_path
    : Array.isArray(trace.agent_events)
      ? trace.agent_events
      : Array.isArray(trace.maddx_events)
        ? trace.maddx_events
        : [];
  const triples: any[] = Array.isArray(chain.triples) ? chain.triples : [];
  const highRisk = isHighRiskChatMessage(msg);
  const hallucAction = msg.halluc?.action || trace.hallucination_check?.action;
  const safetyText = highRisk
    ? '回答涉及潜在高风险症状或处置边界，请优先线下就医或拨打 120。'
    : hallucAction === 'WARN'
      ? '部分依据需要结合医生判断，建议不要单独作为诊断结论。'
      : hallucAction === 'REJECT'
        ? '系统标记该回答需要复核，请谨慎参考。'
        : '未检测到明显高风险边界，仍不能替代医生诊断。';

  return (
    <div style={{ position: 'absolute', inset: 0, zIndex: 28, display: 'flex', alignItems: 'flex-end' }}>
      <div onClick={onClose} style={{ position: 'absolute', inset: 0, background: 'rgba(15,28,8,0.38)', backdropFilter: 'blur(2px)' }} />
      <div style={{
        position: 'relative', width: '100%', maxHeight: '78%', overflowY: 'auto',
        background: 'white', borderRadius: '24px 24px 0 0', padding: '12px 16px 20px',
        boxShadow: '0 -18px 48px rgba(15,28,8,0.22)', animation: 'slideUpFade 0.22s cubic-bezier(0.32,0.72,0,1)',
      }} className="mobile-scroll">
        <div style={{ width: 42, height: 4, borderRadius: 999, background: T.g200, margin: '0 auto 14px' }} />
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, marginBottom: 12 }}>
          <div>
            <div style={{ fontSize: 17, fontWeight: 900, color: T.g900 }}>可信依据</div>
            <div style={{ fontSize: 11.5, color: T.g500, marginTop: 2 }}>来源、安全边界与生成过程摘要</div>
          </div>
          <button onClick={onClose} style={{ width: 34, height: 34, borderRadius: 12, border: `1px solid ${T.g200}`, background: T.g50, color: T.g700, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <X size={16} />
          </button>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div style={{ padding: 12, borderRadius: 14, background: highRisk ? '#fff7ed' : T.g50, border: `1px solid ${highRisk ? '#fed7aa' : T.g200}` }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 13, fontWeight: 850, color: highRisk ? '#9a3412' : T.g800 }}>
              <AlertTriangle size={15} /> 安全边界
            </div>
            <div style={{ marginTop: 6, fontSize: 12.5, lineHeight: 1.65, color: highRisk ? '#9a3412' : T.g600 }}>{safetyText}</div>
          </div>

          <div style={{ padding: 12, borderRadius: 14, background: 'white', border: `1px solid ${T.g200}` }}>
            <div style={{ fontSize: 13, fontWeight: 850, color: T.g800, marginBottom: 8 }}>参考依据</div>
            {refs.length > 0 ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
                {refs.slice(0, 8).map((r: any, i: number) => {
                  const meta = EVIDENCE_TYPE_META[r.type] || { color: T.g600, bg: T.g50, label: r.type || '来源' };
                  const label = r.label || r.title || r.ref_id || `来源 ${i + 1}`;
                  const url = r.locator?.url || r.url;
                  const row = (
                    <div style={{ display: 'flex', gap: 7, alignItems: 'center', minWidth: 0, padding: '7px 8px', borderRadius: 10, background: meta.bg, border: `1px solid ${meta.color}33` }}>
                      <span style={{ fontSize: 10, color: meta.color, fontWeight: 850, padding: '2px 6px', borderRadius: 6, background: 'white', flexShrink: 0 }}>{meta.label}</span>
                      <span style={{ flex: 1, minWidth: 0, fontSize: 12, color: T.g700, fontWeight: 700, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{label}</span>
                    </div>
                  );
                  return url ? <a key={i} href={url} target="_blank" rel="noreferrer" style={{ textDecoration: 'none' }}>{row}</a> : <div key={i}>{row}</div>;
                })}
              </div>
            ) : (
              <div style={{ fontSize: 12.5, color: T.g500, lineHeight: 1.6 }}>本轮回答主要基于会话上下文、健康档案与后端医学知识流程生成，未返回可展示的外部来源。</div>
            )}
          </div>

          <div style={{ padding: 12, borderRadius: 14, background: T.g50, border: `1px solid ${T.g200}` }}>
            <div style={{ fontSize: 13, fontWeight: 850, color: T.g800, marginBottom: 8 }}>生成过程摘要</div>
            {path.length > 0 ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
                {path.slice(0, 5).map((step: any, i: number) => (
                  <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
                    <span style={{ width: 20, height: 20, borderRadius: '50%', background: 'white', border: `1px solid ${T.g200}`, color: T.g700, fontSize: 10, fontWeight: 900, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>{i + 1}</span>
                    <div style={{ minWidth: 0, flex: 1, fontSize: 12, color: T.g600, lineHeight: 1.55 }}>
                      <strong style={{ color: T.g800 }}>{formatActor(step.actor || step.agent || step.phase)}</strong>
                      {step.action ? ` · ${step.action}` : ''}
                      {step.output_summary ? <div style={{ color: T.g500 }}>{String(step.output_summary).slice(0, 120)}</div> : null}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div style={{ fontSize: 12.5, color: T.g500, lineHeight: 1.6 }}>已结合您的问题、可用健康档案和医学知识流程进行分析；原始调试链路已隐藏。</div>
            )}
            {triples.length > 0 && (
              <div style={{ marginTop: 10, display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {triples.slice(0, 6).map((t: any, i: number) => (
                  <span key={i} style={{ fontSize: 10.5, color: T.g700, background: 'white', border: `1px solid ${T.g200}`, borderRadius: 999, padding: '3px 8px' }}>
                    {t.head} · {t.relation} · {t.tail}
                  </span>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

// ─── ChatScreen ────────────────────────────────────────────────────
const CHAT_HISTORY_LIST = [
  { id: 1, title: '头痛发烧咨询', time: '今天 10:24', preview: '建议补充水分并休息，若体温超过 38.5℃…' },
  { id: 2, title: '血压控制建议', time: '昨天', preview: 'DASH 饮食模式可有效降低收缩压约 11mmHg…' },
  { id: 3, title: '膝关节疼痛分析', time: '周一', preview: '多智能体诊断：轻度劳损，建议低强度康复…' },
  { id: 4, title: '失眠调理方案', time: '上周五', preview: '建议规律作息，睡前一小时避免蓝光刺激…' },
  { id: 5, title: '过敏症状询问', time: '上周三', preview: '初步判断为季节性过敏，建议查过敏原…' },
  { id: 6, title: '消化不良问题', time: '上周一', preview: '可能与饮食结构相关，建议少量多餐…' },
];

const ChatScreen: React.FC<{ initialTitle?: string; onBack: () => void }> = ({ initialTitle, onBack }) => {
  const nowHHMM = () => new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
  const [messages, setMessages] = useState<ChatMsg[]>([makeWelcomeMessage(nowHHMM())]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [trustMsg, setTrustMsg] = useState<ChatMsg | null>(null);
  const [composing, setComposing] = useState(false); // IME 中文输入法保护
  // 图片上传相关
  const [selectedImage, setSelectedImage] = useState<string | null>(null); // base64 dataURL
  const [imageUploading, setImageUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  // 会话级状态
  const [sessionId, setSessionId] = useState<number>(() => {
    const raw = localStorage.getItem('mobile_session_id');
    const n = raw ? Number(raw) : NaN;
    return Number.isFinite(n) && n > 0 ? n : 0;
  });
  const [historyList, setHistoryList] = useState<Array<{ id: number; title: string; time: string; preview: string }>>([]);
  // 多轮追问状态（与 PC 端 ChatPage 一致）
  const turnCountRef = useRef(0);
  const slotsRef = useRef<Record<string, any>>({});
  const routeRef = useRef('');
  const abortRef = useRef<{ abort: () => void } | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const didRestoreSessionRef = useRef(false);
  const chatTableDragRef = useRef({ active: false, startX: 0, scrollLeft: 0 });
  const optionDragRef = useRef({ active: false, startX: 0, scrollLeft: 0, moved: false });
  const CHIPS = ['头痛发烧怎么办', '血压偏高注意事项', '帮我辟谣一条消息', '分析我的体检报告'];

  const startChatTableDrag = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    chatTableDragRef.current = { active: true, startX: e.clientX, scrollLeft: e.currentTarget.scrollLeft };
    e.currentTarget.classList.add('dragging');
  }, []);
  const moveChatTableDrag = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    if (!chatTableDragRef.current.active) return;
    e.currentTarget.scrollLeft = chatTableDragRef.current.scrollLeft - (e.clientX - chatTableDragRef.current.startX);
    e.preventDefault();
  }, []);
  const endChatTableDrag = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    chatTableDragRef.current.active = false;
    e.currentTarget.classList.remove('dragging');
  }, []);
  const chatMarkdownComponents = {
    table: ({ children, ...props }: any) => (
      <div
        className="mobile-table-scroll"
        onMouseDown={startChatTableDrag}
        onMouseMove={moveChatTableDrag}
        onMouseUp={endChatTableDrag}
        onMouseLeave={endChatTableDrag}
      >
        <table {...props}>{children}</table>
      </div>
    ),
  };

  const startOptionDrag = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    optionDragRef.current = { active: true, startX: e.clientX, scrollLeft: e.currentTarget.scrollLeft, moved: false };
    e.currentTarget.style.cursor = 'grabbing';
  }, []);

  const moveOptionDrag = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    const drag = optionDragRef.current;
    if (!drag.active) return;
    const delta = e.clientX - drag.startX;
    if (Math.abs(delta) > 4) drag.moved = true;
    e.currentTarget.scrollLeft = drag.scrollLeft - delta;
    e.preventDefault();
  }, []);

  const endOptionDrag = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    optionDragRef.current.active = false;
    e.currentTarget.style.cursor = 'grab';
  }, []);

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages]);
  useEffect(() => { if (initialTitle) setInput(initialTitle); }, [initialTitle]);

  const normalizeSessions = useCallback((sessions: any[]) => (
    (sessions || []).slice(0, 30).map((s: any) => ({
      id: Number(s.id),
      title: s.title || `会话 #${s.id}`,
      time: s.date || s.last_message_time || s.created_at || '',
      preview: s.last_message_preview || s.preview || '',
    })).filter((s: any) => Number.isFinite(s.id) && s.id > 0)
  ), []);

  const refreshHistory = useCallback(async () => {
    const sessions = await api.getSessions();
    const normalized = normalizeSessions(sessions || []);
    setHistoryList(normalized);
    return normalized;
  }, [normalizeSessions]);

  // 切换会话：清状态 + 重新加载消息
  const switchSession = useCallback(async (sid: number) => {
    abortRef.current?.abort();
    setSessionId(sid);
    localStorage.setItem('mobile_session_id', String(sid));
    turnCountRef.current = 0; slotsRef.current = {}; routeRef.current = '';
    setShowHistory(false);
    try {
      const msgs = await api.getSessionMessages(sid);
      // 🆕 改进恢复：含 image / options / evidenceChain / isFinished
      // 把 backend 的图片相对路径拼成绝对 URL，直接可显
      const restored: ChatMsg[] = (msgs || []).map((m: any, i: number) => {
        const meta = m.meta_data || {};
        const trace = meta.trace_data || {};
        return {
          id: i + 1,
          role: m.role === 'user' ? 'user' : 'ai',
          content: m.content || '',
          time: m.created_at ? new Date(m.created_at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : nowHHMM(),
          image: toAbsoluteMediaUrl(m.image || m.image_url),
          route: meta.route,
          halluc: trace?.hallucination_check,
          options: Array.isArray(meta.options) ? meta.options : [],
          isFinished: meta.is_finished !== false,
          evidenceChain: trace?.evidence_chain,
          traceData: trace,
          aiImages: normalizeChatImages(meta.response_images || trace.response_images),
        };
      });
      // 恢复多轮状态：取最后一条 AI 消息
      const lastAi = [...(msgs || [])].reverse().find((m: any) => m.role !== 'user');
      if (lastAi?.meta_data) {
        turnCountRef.current = lastAi.meta_data.turn_count || 0;
        slotsRef.current = lastAi.meta_data.current_slots || {};
        routeRef.current = lastAi.meta_data.route || '';
      }
      setMessages(restored.length ? restored : [
        { id: 0, role: 'ai', content: `会话 #${sid} 暂无历史消息，开始新的对话吧。`, time: nowHHMM() },
      ]);
    } catch (e: any) {
      if (/404|403|不存在|not found|forbidden/i.test(String(e?.message || ''))) {
        localStorage.removeItem('mobile_session_id');
        setSessionId(0);
      }
      setMessages([{ id: 0, role: 'ai', content: `⚠️ 会话恢复失败：${e?.message || '网络错误'}`, time: nowHHMM() }]);
    }
  }, []);

  useEffect(() => {
    if (!localStorage.getItem('access_token') || didRestoreSessionRef.current) return;
    didRestoreSessionRef.current = true;
    (async () => {
      try {
        const sessions = await refreshHistory();
        const stored = Number(localStorage.getItem('mobile_session_id') || 0);
        const storedExists = sessions.some((s: any) => s.id === stored);
        const target = storedExists ? stored : sessions[0]?.id;
        if (target) {
          await switchSession(target);
        } else {
          setMessages([makeWelcomeMessage(nowHHMM())]);
        }
      } catch {
        setMessages([makeWelcomeMessage(nowHHMM())]);
      }
    })();
  }, [refreshHistory, switchSession]);

  const send = useCallback(async (text?: string) => {
    const q0 = (text ?? input).trim();
    // 用户带图但不输入文字：自动注入默认 prompt（与 PC 端一致）
    const q = q0 || (selectedImage ? '请帮我解读这份医疗图片' : '');
    if (!q || loading) return;
    if (!localStorage.getItem('access_token')) {
      setMessages(prev => [...prev, { id: Date.now(), role: 'ai', content: '⚠️ 请先登录后再使用聊天功能。', time: nowHHMM() }]);
      return;
    }
    setInput('');
    setLoading(true);

    // 1) 确保 session_id 存在（首条消息自动建会话）
    let sid = sessionId;
    if (!sid || sid <= 0) {
      try {
        const created: any = await api.createSession();
        sid = Number(created?.id ?? created?.session_id ?? 0);
        if (sid > 0) {
          setSessionId(sid);
          localStorage.setItem('mobile_session_id', String(sid));
        }
      } catch (e: any) {
        setMessages(prev => [...prev, { id: Date.now(), role: 'ai', content: `⚠️ 创建会话失败：${e?.message || '请检查后端服务'}`, time: nowHHMM() }]);
        setLoading(false);
        return;
      }
    }

    // 2) 如有图，先上传 → 拿到后端可消费的 file_id
    let imageDataForBackend: string | number | null = null;       // 传给 ChatRequest.image_data 的字段
    let imageUrlForUserBubble: string | null = selectedImage; // 用户气泡里展示的本地 dataURL
    const pendingImage = selectedImage;
    if (pendingImage) {
      setImageUploading(true);
      try {
        const up: any = await api.uploadImage(pendingImage, sid);
        if (up?.file_id || up?.url) {
          imageDataForBackend = up.file_id || up.storage_key || up.url;
          imageUrlForUserBubble = up.url
            ? (up.url.startsWith('http') ? up.url : `${api.API_BASE}${up.url}`)
            : selectedImage;
        }
      } catch (e: any) {
        setMessages(prev => [...prev, { id: Date.now(), role: 'ai', content: `⚠️ 图片上传失败：${e?.message || '网络错误'}`, time: nowHHMM() }]);
        setImageUploading(false);
        setLoading(false);
        return;
      } finally {
        setImageUploading(false);
      }
      setSelectedImage(null); // 上传完清空预览卡
    }

    // 3) 用户气泡 + AI 占位气泡（用户气泡含图片）
    const userId = Date.now();
    const aiId = userId + 1;
    setMessages(prev => [
      ...prev,
      { id: userId, role: 'user', content: q, time: nowHHMM(), image: imageUrlForUserBubble || undefined },
      { id: aiId, role: 'ai', content: '', time: nowHHMM(), loading: true, status: '正在综合研判' },
    ]);

    const runStream = async (streamSid: number, allowStaleRetry: boolean): Promise<void> => {
      let staleSession = false;
      const ctrl = api.sendChatStream(
        {
          query: q,
          session_id: streamSid,
          turn_count: turnCountRef.current,
          current_slots: slotsRef.current,
          current_route: routeRef.current,
          image_data: imageDataForBackend,
        },
        (evt: SSEEvent) => {
          setMessages(prev => {
            const m = [...prev];
            const idx = m.findIndex(x => x.id === aiId);
            if (idx < 0) return m;
            switch (evt.type) {
              case 'status':
                m[idx] = { ...m[idx], status: evt.message || '正在分析症状…' };
                break;
              case 'maddx_step':
                m[idx] = { ...m[idx], status: `正在组织多专科意见 · ${friendlyPhase(evt.phase)}` };
                break;
              case 'rumor_step': {
                if ((evt as any).phase === 'insight_hit') {
                  m[idx] = { ...m[idx], insightHits: (evt as any).n_hits || 1 };
                } else {
                  m[idx] = { ...m[idx], status: `正在核查健康信息 · ${friendlyPhase((evt as any).phase)}` };
                }
                break;
              }
              case 'hallucination_check':
                m[idx] = { ...m[idx], halluc: (evt as any).report, status: '正在进行安全校验…' };
                break;
              case 'done': {
                const d = evt;
                const traceData = d.trace_data || {};
                m[idx] = {
                  ...m[idx],
                  content: d.answer || '（未返回正文）',
                  loading: false,
                  status: undefined,
                  route: d.route,
                  halluc: traceData?.hallucination_check ?? m[idx].halluc,
                  options: Array.isArray(d.options) ? d.options : [],
                  isFinished: d.is_finished !== false,
                  evidenceChain: (traceData as any)?.evidence_chain ?? undefined,
                  traceData,
                  aiImages: normalizeChatImages(d.images),
                };
                turnCountRef.current = d.turn_count ?? turnCountRef.current;
                slotsRef.current = d.current_slots ?? slotsRef.current;
                routeRef.current = d.route ?? routeRef.current;
                break;
              }
              case 'error': {
                if ((evt as any).status === 404 && allowStaleRetry) {
                  staleSession = true;
                  m[idx] = { ...m[idx], content: '', loading: true, status: '会话已失效，正在重新连接…' };
                } else {
                  m[idx] = { ...m[idx], content: `❌ ${evt.message || '服务暂时不可用'}`, loading: false, status: undefined };
                }
                break;
              }
            }
            return m;
          });
        },
      );
      abortRef.current = ctrl;
      await ctrl.done;
      if (staleSession && allowStaleRetry) {
        localStorage.removeItem('mobile_session_id');
        turnCountRef.current = 0;
        slotsRef.current = {};
        routeRef.current = '';
        const created: any = await api.createSession();
        const newSid = Number(created?.id ?? created?.session_id ?? 0);
        if (!newSid) throw new Error('创建新会话失败');
        setSessionId(newSid);
        localStorage.setItem('mobile_session_id', String(newSid));
        await runStream(newSid, false);
      }
    };

    try {
      await runStream(sid, true);
      refreshHistory().catch(() => {});
    } catch (e: any) {
      setMessages(prev => prev.map(m => m.id === aiId ? {
        ...m,
        content: `❌ ${e?.message || '服务暂时不可用'}`,
        loading: false,
        status: undefined,
      } : m));
    } finally {
      setLoading(false);
      abortRef.current = null;
    }
  }, [input, loading, sessionId, selectedImage, refreshHistory]);

  // 卸载时中止飞行中的 SSE
  useEffect(() => { return () => abortRef.current?.abort(); }, []);

  // 图片选择 → 校验类型/大小 → 转 base64 dataURL（不立刻上传，发送时再上传）
  const handleImagePick = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = ''; // 允许同名文件重选
    if (!file) return;
    if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) {
      setMessages(prev => [...prev, { id: Date.now(), role: 'ai', content: '⚠️ 仅支持 JPG / PNG / WebP 格式', time: nowHHMM() }]);
      return;
    }
    if (file.size > 5 * 1024 * 1024) {
      setMessages(prev => [...prev, { id: Date.now(), role: 'ai', content: '⚠️ 图片须小于 5MB', time: nowHHMM() }]);
      return;
    }
    try {
      const b64 = await fileToBase64(file);
      setSelectedImage(b64);
    } catch {
      setMessages(prev => [...prev, { id: Date.now(), role: 'ai', content: '⚠️ 读取图片失败', time: nowHHMM() }]);
    }
  }, []);

  const renderAssistantMessageStack = (m: ChatMsg) => {
    const parsed = extractSystemNotice(m.content);
    const risk = getMessageRiskLevel(m);
    const showTrust = !m.loading && (m.evidenceChain || m.traceData || m.halluc || m.route || parsed.notices.length > 0);
    const trustText = risk === 'high' ? '高风险提示 · 查看' : risk === 'warn' ? '需复核 · 查看' : '依据与安全 · 查看';
    const hasOptions = !m.loading && m.isFinished === false && (m.options?.length ?? 0) > 0;

    return (
      <div style={{ flex: 1, minWidth: 0, maxWidth: 'calc(100% - 38px)', display: 'flex', flexDirection: 'column', alignItems: 'stretch' }}>
        {m.image && (
          <a href={m.image} target="_blank" rel="noreferrer" style={{
            display: 'inline-block', marginBottom: 7, borderRadius: 14,
            overflow: 'hidden', border: `1px solid ${T.slate200}`,
            width: 180, maxHeight: 180, lineHeight: 0,
            boxShadow: '0 2px 8px rgba(0,0,0,0.08)',
          }}>
            <img src={m.image} alt="上传图片" style={{ display: 'block', width: '100%', maxHeight: 180, objectFit: 'cover' }} />
          </a>
        )}

        <div className={m.content ? 'mobile-ai-md' : ''} style={{
          padding: m.content ? '12px 14px' : '10px 13px',
          borderRadius: 18,
          background: 'rgba(255,255,255,0.96)',
          color: T.slate800,
          fontSize: 14,
          lineHeight: 1.65,
          border: `1px solid ${T.slate200}`,
          boxShadow: '0 3px 10px rgba(15,28,8,0.06)',
          wordBreak: 'break-word',
          overflowWrap: 'anywhere',
          minWidth: 0,
          width: '100%',
          boxSizing: 'border-box',
        }}>
          {!m.content ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: 9, minWidth: 0 }}>
              <div style={{ display: 'flex', gap: 4, alignItems: 'center', flexShrink: 0 }}>
                {[0, 0.2, 0.4].map((d, i) => (
                  <div key={i} style={{ width: 5, height: 5, borderRadius: '50%', background: T.mint400, animation: 'mobilePulse 1.4s ease-in-out infinite', animationDelay: `${d}s` }} />
                ))}
              </div>
              <div style={{ fontSize: 12.5, color: T.g600, fontWeight: 700, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {compactStatusText(m.status)}
              </div>
            </div>
          ) : (
            <>
              {parsed.notices.map((notice, i) => (
                <div key={`${notice.text}-${i}`} style={{
                  marginBottom: 9,
                  padding: '8px 10px',
                  borderRadius: 11,
                  background: notice.severity === 'warn' ? '#fff7ed' : T.g50,
                  border: `1px solid ${notice.severity === 'warn' ? '#fed7aa' : T.g200}`,
                  color: notice.severity === 'warn' ? '#9a3412' : T.g700,
                  fontSize: 12.5,
                  fontWeight: 700,
                  lineHeight: 1.55,
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: 6,
                }}>
                  <AlertTriangle size={14} style={{ marginTop: 2, flexShrink: 0 }} />
                  <span>{notice.text}</span>
                </div>
              ))}
              <ReactMarkdown remarkPlugins={[remarkGfm]} components={chatMarkdownComponents}>
                {parsed.content || m.content}
              </ReactMarkdown>
            </>
          )}
        </div>

        {!m.loading && (m.aiImages?.length ?? 0) > 0 && (
          <div style={{ display: 'flex', gap: 8, overflowX: 'auto', width: '100%', marginTop: 8, paddingBottom: 2 }} className="mobile-scroll">
            {m.aiImages!.map((img, i) => (
              <a key={`${img}-${i}`} href={img} target="_blank" rel="noreferrer" style={{ flex: '0 0 auto', width: 132, height: 96, borderRadius: 14, overflow: 'hidden', border: `1px solid ${T.g200}`, background: T.g50 }}>
                <img src={img} alt={`AI 生成图片 ${i + 1}`} style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }} />
              </a>
            ))}
          </div>
        )}

        {showTrust && (
          <button onClick={() => setTrustMsg(m)} style={{
            marginTop: 7,
            width: '100%',
            border: `1px solid ${risk === 'normal' ? T.g200 : '#fed7aa'}`,
            background: risk === 'normal' ? T.g50 : '#fff7ed',
            color: risk === 'normal' ? T.g700 : '#9a3412',
            borderRadius: 12,
            padding: '8px 10px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 8,
            fontSize: 12,
            fontWeight: 850,
            cursor: 'pointer',
          }}>
            <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <ShieldCheck size={14} />
              {trustText}
            </span>
            <ChevronRight size={14} />
          </button>
        )}

        {hasOptions && (
          <div style={{ marginTop: 8 }}>
            <div style={{ fontSize: 11, color: T.slate400, fontWeight: 700, marginBottom: 6, display: 'flex', alignItems: 'center', gap: 5 }}>
              <Sparkles size={11} color={T.mint500} /> 继续追问
            </div>
            <div
              style={{
                display: 'flex',
                gap: 6,
                overflowX: 'auto',
                padding: '0 18px 2px 0',
                cursor: 'grab',
                touchAction: 'pan-x',
                WebkitOverflowScrolling: 'touch',
                userSelect: 'none',
              }}
              className="mobile-scroll"
              onMouseDown={startOptionDrag}
              onMouseMove={moveOptionDrag}
              onMouseUp={endOptionDrag}
              onMouseLeave={endOptionDrag}
            >
              {m.options!.map((opt, i) => (
                <button key={i} onClick={(e) => {
                  if (optionDragRef.current.moved) {
                    e.preventDefault();
                    e.stopPropagation();
                    return;
                  }
                  send(opt);
                }} disabled={loading} style={{
                  flex: '0 0 auto',
                  padding: '7px 12px',
                  borderRadius: 16,
                  fontSize: 12,
                  fontWeight: 750,
                  background: loading ? T.slate100 : T.mint50,
                  color: loading ? T.slate400 : T.mint700,
                  border: `1.5px solid ${T.mint200}`,
                  cursor: loading ? 'not-allowed' : 'pointer',
                }}>{opt}</button>
              ))}
            </div>
          </div>
        )}

        <div style={{ fontSize: 10.5, color: T.slate400, marginTop: 4, paddingLeft: 2 }}>{m.time}</div>
      </div>
    );
  };

  const renderUserMessageStack = (m: ChatMsg) => (
    <div style={{ maxWidth: '82%', minWidth: 0, display: 'flex', flexDirection: 'column', alignItems: 'flex-end' }}>
      {m.image && (
        <a href={m.image} target="_blank" rel="noreferrer" style={{
          display: 'inline-block', marginBottom: 6, borderRadius: 14,
          overflow: 'hidden', border: `1px solid ${T.slate200}`,
          maxWidth: 200, maxHeight: 200, lineHeight: 0,
          boxShadow: '0 2px 8px rgba(0,0,0,0.08)',
        }}>
          <img src={m.image} alt="上传图片" style={{ display: 'block', maxWidth: '100%', maxHeight: 200, objectFit: 'cover' }} />
        </a>
      )}
      <div style={{
        padding: '11px 14px',
        borderRadius: '18px 6px 18px 18px',
        background: 'linear-gradient(135deg, #7BCFA6, #4FB58B)',
        color: 'white',
        fontSize: 14,
        lineHeight: 1.55,
        boxShadow: '0 3px 10px rgba(90,112,72,0.2)',
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-word',
        overflowWrap: 'anywhere',
      }}>{m.content}</div>
      <div style={{ fontSize: 10.5, color: T.slate400, marginTop: 4, paddingRight: 2 }}>{m.time}</div>
    </div>
  );

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', background: T.cream50, minHeight: 0, position: 'relative' }}>
      {/* History Sidebar */}
      {showHistory && (
        <>
          <div onClick={() => setShowHistory(false)} style={{ position: 'absolute', inset: 0, background: 'rgba(15,24,32,0.35)', zIndex: 20, backdropFilter: 'blur(2px)' }} />
          <div style={{ position: 'absolute', top: 0, left: 0, bottom: 0, width: '80%', maxWidth: 300, background: 'white', zIndex: 21, boxShadow: '4px 0 24px rgba(0,0,0,0.12)', display: 'flex', flexDirection: 'column', animation: 'slideInLeft 0.28s cubic-bezier(0.32,0.72,0,1)' }}>
            <div style={{ padding: '16px 18px 12px', borderBottom: `1px solid ${T.slate200}`, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
                <div style={{ width: 32, height: 32, borderRadius: 9, background: T.mint50, border: `1px solid ${T.mint200}`, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <History size={15} color={T.mint600} />
                </div>
                <span style={{ fontSize: 15, fontWeight: 800, color: T.slate900 }}>历史对话</span>
              </div>
              <button onClick={() => setShowHistory(false)} style={{ width: 30, height: 30, borderRadius: 8, background: T.slate100, border: 'none', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', color: T.slate500 }}>
                <X size={14} />
              </button>
            </div>
            <div style={{ flex: 1, overflowY: 'auto', padding: '10px 12px' }} className="mobile-scroll">
              <div style={{ fontSize: 11, color: T.slate400, fontWeight: 600, marginBottom: 8, paddingLeft: 4 }}>最近 30 天</div>
              {(historyList.length ? historyList : CHAT_HISTORY_LIST).map((h: any) => (
                <button key={h.id} onClick={() => {
                  // 真实会话：切到该 session_id 并加载消息；mock 数据（无后端会话）回退到填入输入框
                  // 🐞 之前用 typeof === 'number' 判断有 BUG（后端返 string id），改为用 historyList.length 判断
                  if (historyList.length > 0) {
                    switchSession(Number(h.id));
                  } else {
                    setInput(h.title); setShowHistory(false);
                  }
                }} style={{
                  width: '100%', padding: '12px 12px', borderRadius: 12, border: `1px solid ${T.slate200}`, background: 'white',
                  cursor: 'pointer', textAlign: 'left', marginBottom: 7, transition: 'all 0.18s',
                }}
                  onMouseEnter={e => (e.currentTarget.style.borderColor = T.mint300)}
                  onMouseLeave={e => (e.currentTarget.style.borderColor = T.slate200)}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 4 }}>
                    <span style={{ fontSize: 13, fontWeight: 700, color: T.slate900 }}>{h.title}</span>
                    <span style={{ fontSize: 10, color: T.slate400, flexShrink: 0, marginLeft: 8 }}>{h.time}</span>
                  </div>
                  <div style={{ fontSize: 11.5, color: T.slate500, lineHeight: 1.5, display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>{h.preview}</div>
                </button>
              ))}
            </div>
            <div style={{ padding: '12px 14px', borderTop: `1px solid ${T.slate100}` }}>
              <button onClick={async () => {
                abortRef.current?.abort();
                turnCountRef.current = 0; slotsRef.current = {}; routeRef.current = '';
                try {
                  const created: any = await api.createSession();
                  const newSid = Number(created?.id ?? created?.session_id ?? 0);
                  if (newSid > 0) {
                    setSessionId(newSid);
                    localStorage.setItem('mobile_session_id', String(newSid));
                    refreshHistory().catch(() => {});
                  }
                } catch { /* 静默：fallback 走旧 sid */ }
                setMessages([makeWelcomeMessage(nowHHMM())]);
                setShowHistory(false);
              }} style={{
                width: '100%', padding: '10px', borderRadius: 11, border: `1.5px solid ${T.mint200}`, background: T.mint50,
                color: T.mint700, cursor: 'pointer', fontSize: 13, fontWeight: 700, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 7,
              }}>
                <Bot size={14} /> 开始新对话
              </button>
            </div>
          </div>
        </>
      )}
      {trustMsg && <MobileTrustSheet msg={trustMsg} onClose={() => setTrustMsg(null)} />}

      {/* Header */}
      <div style={{ background: 'white', borderBottom: `1px solid ${T.slate200}`, padding: '12px 16px', display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0 }}>
        <button onClick={onBack} style={{ width: 34, height: 34, borderRadius: 10, background: T.slate100, border: 'none', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', color: T.slate600 }}>
          <ArrowLeft size={16} />
        </button>
        <div style={{ width: 36, height: 36, borderRadius: 10, background: 'linear-gradient(135deg, #A7E3C2, #4FB58B)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <Bot size={18} color="white" />
        </div>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 14, fontWeight: 700, color: T.slate900 }}>TrustMed AI</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 11, color: T.slate400 }}>
            <div style={{ width: 6, height: 6, borderRadius: '50%', background: T.mint500, boxShadow: `0 0 0 2px ${T.mint100}` }} />
            六大专科在线
          </div>
        </div>
        <button onClick={() => setShowHistory(true)} style={{ width: 34, height: 34, borderRadius: 10, background: T.slate100, border: 'none', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', color: T.slate500 }}>
          <History size={16} />
        </button>
        <div style={{ fontSize: 10, padding: '3px 9px', borderRadius: 8, background: T.mint50, color: T.mint700, fontWeight: 700, border: `1px solid ${T.mint200}` }}>可信溯源</div>
      </div>

      {/* Messages */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '14px 14px 4px' }} className="mobile-scroll">
        {/* Quick chips — show only at start */}
        {messages.length <= 1 && (
          <div style={{ marginBottom: 14 }}>
            <div style={{ fontSize: 11, color: T.slate400, fontWeight: 600, marginBottom: 8, textAlign: 'center' }}>快速提问</div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7, justifyContent: 'center' }}>
              {CHIPS.map(c => (
                <button key={c} onClick={() => send(c)} style={{
                  padding: '6px 13px', borderRadius: 20, fontSize: 12, fontWeight: 600,
                  background: 'white', border: `1.5px solid ${T.mint200}`, color: T.mint700, cursor: 'pointer',
                }}>{c}</button>
              ))}
            </div>
          </div>
        )}

        {messages.map((m) => (
          <div key={m.id} style={{
            display: 'flex',
            justifyContent: m.role === 'user' ? 'flex-end' : 'flex-start',
            gap: 8,
            marginBottom: 14,
            alignItems: 'flex-start',
            minWidth: 0,
          }}>
            {m.role === 'ai' && (
              <div style={{ width: 30, height: 30, borderRadius: 10, background: 'linear-gradient(135deg, #A7E3C2, #4FB58B)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, boxShadow: '0 3px 8px rgba(47,155,127,0.18)' }}>
                <Bot size={14} color="white" />
              </div>
            )}
            {m.role === 'ai' ? renderAssistantMessageStack(m) : renderUserMessageStack(m)}
            {m.role === 'user' && (
              <div style={{ width: 30, height: 30, borderRadius: '50%', background: T.slate200, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, fontSize: 13, fontWeight: 800, color: T.slate600, marginTop: 2 }}>
                {(localStorage.getItem('current_username') || 'U').charAt(0).toUpperCase()}
              </div>
            )}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div style={{ padding: '10px 14px 12px', background: 'white', borderTop: `1px solid ${T.slate100}`, flexShrink: 0 }}>
        {/* 图片预览卡（选中后展示） */}
        {selectedImage && (
          <div style={{
            display: 'flex', alignItems: 'center', gap: 10, padding: '8px 10px',
            background: T.mint50, border: `1px solid ${T.mint200}`, borderRadius: 12, marginBottom: 8,
          }}>
            <img src={selectedImage} alt="preview" style={{ width: 44, height: 44, objectFit: 'cover', borderRadius: 8, border: `1px solid ${T.mint200}` }} />
            <div style={{ flex: 1, fontSize: 12, color: T.mint700, fontWeight: 600 }}>
              {imageUploading ? '🔄 上传中…' : '图片已就绪，点发送进行 AI 解读'}
            </div>
            <button
              onClick={() => setSelectedImage(null)}
              disabled={imageUploading}
              style={{
                width: 26, height: 26, borderRadius: '50%', border: 'none',
                background: imageUploading ? T.slate200 : T.mint100,
                cursor: imageUploading ? 'not-allowed' : 'pointer',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                color: T.mint700,
              }}
            >
              <X size={13} />
            </button>
          </div>
        )}
        <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end' }}>
          {/* 隐藏的文件 input — 由 paperclip 触发 */}
          <input type="file" accept="image/jpeg,image/png,image/webp"
                 ref={fileInputRef} onChange={handleImagePick} style={{ display: 'none' }} />
          {/* Paperclip 按钮：触发文件选择 */}
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={loading || imageUploading || !!selectedImage}
            title={selectedImage ? '已选择图片' : '上传医学图片/化验单'}
            style={{
              width: 42, height: 42, borderRadius: '50%',
              border: `1.5px solid ${T.slate200}`,
              background: selectedImage ? T.mint50 : 'white',
              color: selectedImage ? T.mint600 : T.slate500,
              cursor: (loading || imageUploading || !!selectedImage) ? 'not-allowed' : 'pointer',
              display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
              transition: 'all 0.2s',
            }}
          >
            <Paperclip size={16} />
          </button>
          <div style={{ flex: 1, background: T.slate50, border: `1.5px solid ${T.slate200}`, borderRadius: 18, padding: '10px 14px', minHeight: 42, display: 'flex', alignItems: 'center' }}>
            <input value={input}
              onChange={e => setInput(e.target.value)}
              onCompositionStart={() => setComposing(true)}
              onCompositionEnd={() => setComposing(false)}
              onKeyDown={e => { if (e.key === 'Enter' && !composing) { e.preventDefault(); send(); } }}
              placeholder={selectedImage ? '可补充症状描述（可选）…' : '描述您的症状或问题…'}
              style={{ border: 'none', background: 'none', outline: 'none', fontSize: 14, color: T.slate900, width: '100%' }} />
          </div>
          <button onClick={() => send()}
            disabled={(!input.trim() && !selectedImage) || loading || imageUploading}
            style={{
              width: 42, height: 42, borderRadius: '50%', border: 'none',
              cursor: ((input.trim() || selectedImage) && !loading && !imageUploading) ? 'pointer' : 'not-allowed',
              background: ((input.trim() || selectedImage) && !loading && !imageUploading)
                ? 'linear-gradient(135deg, #5EC99D, #2F9B7F)' : T.slate200,
              color: ((input.trim() || selectedImage) && !loading && !imageUploading) ? 'white' : T.slate400,
              display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
              boxShadow: ((input.trim() || selectedImage) && !loading && !imageUploading) ? '0 4px 12px rgba(90,112,72,0.25)' : 'none',
              transition: 'all 0.2s',
            }}>
            <Send size={16} />
          </button>
        </div>
        <div style={{ fontSize: 10, color: T.slate300, textAlign: 'center', marginTop: 6 }}>AI 仅供参考，急症请拨打 120</div>
      </div>
    </div>
  );
};

// ─── KnowledgeScreen ───────────────────────────────────────────────
const MobileArticleDetail: React.FC<{
  article: any;
  liked: boolean;
  favorited: boolean;
  onClose: () => void;
  onLike: (id: number) => void;
  onFavorite: (article: any) => void;
}> = ({ article, liked, favorited, onClose, onLike, onFavorite }) => {
  const expertArticle = isExpertArticle(article);
  const [qaOpen, setQaOpen] = useState(false);
  const [qaInput, setQaInput] = useState('');
  const [qaLoading, setQaLoading] = useState(false);
  const [qaMessages, setQaMessages] = useState<Array<{ role: 'user' | 'ai'; content: string }>>([]);
  const tableDragRef = useRef({ active: false, startX: 0, scrollLeft: 0 });
  const readStartRef = useRef(Date.now());
  const content = article?.detail_error
    ? `> 正文加载失败，当前仅显示摘要。请稍后重试。\n\n${article?.summary || '暂无正文内容。'}`
    : article?.content || article?.summary || '暂无正文内容。';
  useEffect(() => {
    readStartRef.current = Date.now();
    return () => {
      if (typeof article?.id === 'number' && !article?.is_live && !expertArticle) {
        api.trackArticle({
          event_type: 'read',
          article_id: article.id,
          duration_ms: Math.max(0, Date.now() - readStartRef.current),
          meta_data: { source: 'mobile_detail' },
        }).catch(() => {});
      }
    };
  }, [article?.id, article?.is_live, expertArticle]);
  const startTableDrag = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    tableDragRef.current = {
      active: true,
      startX: e.clientX,
      scrollLeft: e.currentTarget.scrollLeft,
    };
    e.currentTarget.classList.add('dragging');
  }, []);
  const moveTableDrag = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    if (!tableDragRef.current.active) return;
    const delta = e.clientX - tableDragRef.current.startX;
    e.currentTarget.scrollLeft = tableDragRef.current.scrollLeft - delta;
    e.preventDefault();
  }, []);
  const endTableDrag = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    tableDragRef.current.active = false;
    e.currentTarget.classList.remove('dragging');
  }, []);
  const markdownComponents = {
    table: ({ children, ...props }: any) => (
      <div
        className="mobile-table-scroll"
        onMouseDown={startTableDrag}
        onMouseMove={moveTableDrag}
        onMouseUp={endTableDrag}
        onMouseLeave={endTableDrag}
      >
        <table {...props}>{children}</table>
      </div>
    ),
  };
  const sendQuestion = async () => {
    const q = qaInput.trim();
    if (!q || qaLoading || !article?.id || String(article.id).startsWith('mock') || expertArticle) return;
    setQaInput('');
    setQaLoading(true);
    setQaMessages(prev => [...prev, { role: 'user', content: q }, { role: 'ai', content: '' }]);
    try {
      const res = await fetch(`${api.API_BASE}/api/articles/${article.id}/ask`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: q }),
      });
      if (!res.body) throw new Error('empty stream');
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const parts = buf.split('\n\n');
        buf = parts.pop() ?? '';
        for (const part of parts) {
          if (!part.startsWith('data: ')) continue;
          const evt = JSON.parse(part.slice(6));
          if (evt.type === 'chunk') {
            setQaMessages(prev => {
              const next = [...prev];
              next[next.length - 1] = { role: 'ai', content: next[next.length - 1].content + evt.content };
              return next;
            });
          }
        }
      }
    } catch {
      setQaMessages(prev => {
        const next = [...prev];
        next[next.length - 1] = { role: 'ai', content: 'AI 问答暂时不可用，请稍后重试。' };
        return next;
      });
    } finally {
      setQaLoading(false);
    }
  };

  return (
    <div style={{ position: 'absolute', inset: 0, zIndex: 8, background: T.cream50, display: 'flex', flexDirection: 'column' }}>
      <div style={{ height: 56, background: 'white', display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', padding: '8px 14px 8px', borderBottom: `1px solid ${T.slate200}`, flexShrink: 0, boxSizing: 'border-box' }}>
        <button onClick={onClose} style={{ width: 32, height: 32, borderRadius: 10, border: 'none', background: T.slate100, color: T.slate700, display: 'flex', alignItems: 'center', justifyContent: 'center' }}><ArrowLeft size={17} /></button>
        <div style={{ fontSize: 14, fontWeight: 850, color: T.slate900 }}>文章详情</div>
        {expertArticle ? (
          <div style={{ width: 32, height: 32 }} />
        ) : (
          <button onClick={() => onFavorite(article)} style={{ width: 32, height: 32, borderRadius: 10, border: 'none', background: favorited ? T.mint100 : T.slate100, color: favorited ? T.mint700 : T.slate500, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            {favorited ? <Star size={16} fill={T.mint500} /> : <Star size={16} />}
          </button>
        )}
      </div>
      <div style={{ flex: 1, overflowY: 'auto', padding: '14px 14px 18px' }} className="mobile-scroll">
        <ArticleCover article={article} height={200} />
        <div style={{ marginTop: 14, background: 'white', borderRadius: 16, padding: 16, border: `1px solid ${T.slate200}` }}>
          <div style={{ fontSize: 19, fontWeight: 900, color: T.slate900, lineHeight: 1.35 }}>{article.title}</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 10 }}>
            <span style={{ fontSize: 10, fontWeight: 800, color: T.g600, background: T.g100, borderRadius: 999, padding: '3px 8px' }}>{article.cat || article.category}</span>
            <span style={{ fontSize: 10, fontWeight: 800, color: T.slate500, background: T.slate100, borderRadius: 999, padding: '3px 8px' }}>{article.reading_time || 3} 分钟读完</span>
            {(article.tags || []).slice(0, 3).map((tag: string) => (
              <span key={tag} style={{ fontSize: 10, fontWeight: 700, color: T.slate500, background: T.slate100, borderRadius: 999, padding: '3px 8px' }}>{tag}</span>
            ))}
          </div>
          <div className="mobile-ai-md" style={{ marginTop: 14, color: T.slate700, fontSize: 13.5, lineHeight: 1.75 }}>
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>{content}</ReactMarkdown>
          </div>
        </div>
        {!expertArticle && <div style={{ marginTop: 12, background: 'white', borderRadius: 16, padding: 14, border: `1px solid ${T.slate200}` }}>
          <button onClick={() => setQaOpen(o => !o)} style={{ width: '100%', border: 'none', background: T.mint50, color: T.mint700, borderRadius: 12, padding: '10px 12px', fontSize: 13, fontWeight: 850, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6 }}>
            <Bot size={15} /> 就本文向 AI 提问
          </button>
          {qaOpen && (
            <div style={{ marginTop: 12 }}>
              {qaMessages.length > 0 && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8, maxHeight: 220, overflowY: 'auto', marginBottom: 10 }} className="mobile-scroll">
                  {qaMessages.map((m, i) => (
                    <div key={i} style={{ alignSelf: m.role === 'user' ? 'flex-end' : 'flex-start', maxWidth: '86%', borderRadius: m.role === 'user' ? '14px 4px 14px 14px' : '4px 14px 14px 14px', background: m.role === 'user' ? T.mint600 : T.slate50, color: m.role === 'user' ? 'white' : T.slate800, padding: '8px 10px', fontSize: 12.5, lineHeight: 1.6 }}>
                      {m.role === 'ai' && !m.content ? '正在思考…' : <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown>}
                    </div>
                  ))}
                </div>
              )}
              <div style={{ display: 'flex', gap: 8 }}>
                <input value={qaInput} onChange={e => setQaInput(e.target.value)} placeholder="问一个延伸问题…" style={{ flex: 1, minWidth: 0, border: `1px solid ${T.slate200}`, background: T.slate50, borderRadius: 12, padding: '0 12px', fontSize: 13, outline: 'none' }} />
                <button onClick={sendQuestion} disabled={!qaInput.trim() || qaLoading} style={{ width: 40, height: 40, borderRadius: 12, border: 'none', background: (!qaInput.trim() || qaLoading) ? T.slate200 : T.mint600, color: 'white', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><Send size={15} /></button>
              </div>
              <div style={{ marginTop: 8, color: T.slate400, fontSize: 10 }}>AI 仅供健康科普参考，不能替代医生诊断。</div>
            </div>
          )}
        </div>}
      </div>
    </div>
  );
};

const KnowledgeScreen: React.FC = () => {
  const [activeTab, setActiveTab] = useState('辟谣粉碎机');
  const [liked, setLiked] = useState<Set<number>>(new Set());
  const [favorited, setFavorited] = useState<Set<number>>(new Set());
  const [articles, setArticles] = useState<any[] | null>(null);
  const [hotArticles, setHotArticles] = useState<any[] | null>(null);
  const [recommendedArticles, setRecommendedArticles] = useState<any[]>([]);
  const [selectedArticle, setSelectedArticle] = useState<any | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [loadErr, setLoadErr] = useState<string>('');
  // 🆕 搜索折叠状态 + 输入
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchComposing, setSearchComposing] = useState(false);
  const searchInputRef = useRef<HTMLInputElement | null>(null);
  const tabScrollerRef = useRef<HTMLDivElement | null>(null);
  const tabDragRef = useRef({ active: false, startX: 0, scrollLeft: 0, moved: false });
  const [tabDragging, setTabDragging] = useState(false);
  const recommendScrollerRef = useRef<HTMLDivElement | null>(null);
  const recommendDragRef = useRef({ active: false, startX: 0, scrollLeft: 0, moved: false });
  const [recommendDragging, setRecommendDragging] = useState(false);

  const syncFavoriteState = useCallback((list: any[]) => {
    setFavorited(prev => {
      const next = new Set(prev);
      list.forEach((item: any) => {
        if (typeof item.id === 'number' && item.is_favorited) next.add(item.id);
      });
      return next;
    });
  }, []);

  // 拉取真实文章；支持后端搜索/分类过滤，失败回退 MOCK_ARTICLES
  useEffect(() => {
    if (activeTab === '实时热点追踪') return;
    const t = window.setTimeout(() => {
      if (activeTab === EXPERT_CATEGORY) {
        api.getHealthArticles({ q: searchQuery.trim() || undefined, limit: 50 })
          .then((res: any) => {
            const rawList = Array.isArray(res) ? res : (res?.items || []);
            const list = rawList.map((a: any) => normalizeKnowledgeArticle({
              ...a,
              cat: EXPERT_CATEGORY,
              category: EXPERT_CATEGORY,
              source_type: 'expert_article',
            }, EXPERT_CATEGORY));
            setArticles(list);
            setLoadErr('');
          })
          .catch(e => {
            setLoadErr(safeErrorMessage(e));
            setArticles([]);
          });
        return;
      }
      const params = {
        q: searchQuery.trim() || undefined,
        category: activeTab,
        sort: searchQuery.trim() ? 'relevance' : 'latest',
        page_size: 50,
      };
      api.searchArticles(params)
        .then((res: any) => {
          const list = (Array.isArray(res) ? res : res?.items || []).map((a: any) => normalizeKnowledgeArticle(a, activeTab));
          syncFavoriteState(list);
          setArticles(list);
          setLoadErr('');
          if (searchQuery.trim()) {
            api.trackArticle({ event_type: 'search', query: searchQuery.trim(), meta_data: { category: activeTab } }).catch(() => {});
          }
        })
        .catch(e => {
          setLoadErr(safeErrorMessage(e));
          setArticles(MOCK_ARTICLES.map(a => normalizeKnowledgeArticle(a)));
        });
    }, searchQuery.trim() ? 260 : 0);
    return () => window.clearTimeout(t);
  }, [activeTab, searchQuery, syncFavoriteState]);

  useEffect(() => {
    api.getRecommendedArticles()
      .then((d: any) => {
        const list = (d?.articles || []).map((a: any) => normalizeKnowledgeArticle(a));
        syncFavoriteState(list);
        setRecommendedArticles(list);
      })
      .catch(() => setRecommendedArticles([]));
  }, [syncFavoriteState]);

  useEffect(() => {
    if (activeTab !== '实时热点追踪' || hotArticles) return;
    api.getHotArticles()
      .then((d: any) => setHotArticles((d?.articles || []).map((a: any) => normalizeKnowledgeArticle(a, '实时热点追踪'))))
      .catch(() => setHotArticles([]));
  }, [activeTab, hotArticles]);

  // 后端已经负责搜索/分类过滤；只有接口失败回退 mock 时再做本地过滤。
  const sourceList = activeTab === '实时热点追踪'
    ? (hotArticles ?? [])
    : (articles ?? MOCK_ARTICLES);
  const _q = searchQuery.trim().toLowerCase();
  const filtered = sourceList.filter(a => {
    if (loadErr && activeTab !== '实时热点追踪' && a.cat !== activeTab) return false;
    if (!_q) return true;
    return (
      String(a.title || '').toLowerCase().includes(_q) ||
      String(a.summary || '').toLowerCase().includes(_q)
    );
  });

  useEffect(() => {
    const el = tabScrollerRef.current?.querySelector(`[data-cat="${activeTab}"]`) as HTMLElement | null;
    el?.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' });
  }, [activeTab]);

  // 搜索栏展开后 autofocus
  useEffect(() => {
    if (searchOpen) {
      const t = setTimeout(() => searchInputRef.current?.focus(), 150);
      return () => clearTimeout(t);
    }
  }, [searchOpen]);

  const onLike = useCallback((id: number) => {
    if ((articles || []).some(a => a.id === id && isExpertArticle(a))) return;
    setLiked(s => {
      const n = new Set(s);
      const willLike = !n.has(id);
      willLike ? n.add(id) : n.delete(id);
      return n;
    });
    // 真实文章才同步后端
    if ((articles || []).some(a => a.id === id)) {
      api.likeArticle(id).catch(() => { /* 静默失败：UI 已更新 */ });
    }
  }, [articles]);

  const onFavorite = useCallback((article: any) => {
    const id = article?.id;
    if (typeof id !== 'number' || article?.is_live || isExpertArticle(article)) return;
    const nextFavorited = !favorited.has(id);
    setFavorited(prev => {
      const next = new Set(prev);
      nextFavorited ? next.add(id) : next.delete(id);
      return next;
    });
    const patch = (item: any) => item.id === id ? { ...item, is_favorited: nextFavorited } : item;
    setArticles(prev => prev?.map(patch) || prev);
    setRecommendedArticles(prev => prev.map(patch));
    setSelectedArticle(prev => prev?.id === id ? { ...prev, is_favorited: nextFavorited } : prev);
    const req = nextFavorited ? api.favoriteArticle(id) : api.unfavoriteArticle(id);
    req.catch(() => {
      setFavorited(prev => {
        const rollback = new Set(prev);
        nextFavorited ? rollback.delete(id) : rollback.add(id);
        return rollback;
      });
      setArticles(prev => prev?.map((item: any) => item.id === id ? { ...item, is_favorited: !nextFavorited } : item) || prev);
      setRecommendedArticles(prev => prev.map((item: any) => item.id === id ? { ...item, is_favorited: !nextFavorited } : item));
      setSelectedArticle(prev => prev?.id === id ? { ...prev, is_favorited: !nextFavorited } : prev);
    });
  }, [favorited]);

  const startTabDrag = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    if (e.button !== 0 || !tabScrollerRef.current) return;
    tabDragRef.current = {
      active: true,
      startX: e.clientX,
      scrollLeft: tabScrollerRef.current.scrollLeft,
      moved: false,
    };
    setTabDragging(true);
  }, []);

  const moveTabDrag = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    const drag = tabDragRef.current;
    const el = tabScrollerRef.current;
    if (!drag.active || !el) return;
    const delta = e.clientX - drag.startX;
    if (Math.abs(delta) > 4) drag.moved = true;
    el.scrollLeft = drag.scrollLeft - delta;
    if (drag.moved) e.preventDefault();
  }, []);

  const endTabDrag = useCallback(() => {
    if (!tabDragRef.current.active) return;
    tabDragRef.current.active = false;
    setTabDragging(false);
    window.setTimeout(() => {
      tabDragRef.current.moved = false;
    }, 0);
  }, []);

  const startRecommendDrag = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    if (e.button !== 0 || !recommendScrollerRef.current) return;
    recommendDragRef.current = {
      active: true,
      startX: e.clientX,
      scrollLeft: recommendScrollerRef.current.scrollLeft,
      moved: false,
    };
    setRecommendDragging(true);
  }, []);

  const moveRecommendDrag = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    const drag = recommendDragRef.current;
    const el = recommendScrollerRef.current;
    if (!drag.active || !el) return;
    const delta = e.clientX - drag.startX;
    if (Math.abs(delta) > 5) drag.moved = true;
    el.scrollLeft = drag.scrollLeft - delta;
    if (drag.moved) e.preventDefault();
  }, []);

  const endRecommendDrag = useCallback(() => {
    if (!recommendDragRef.current.active) return;
    recommendDragRef.current.active = false;
    setRecommendDragging(false);
    window.setTimeout(() => {
      recommendDragRef.current.moved = false;
    }, 0);
  }, []);

  const openArticle = useCallback(async (a: any) => {
    if (a?.is_live || typeof a.id !== 'number' || String(a.id).startsWith('mock')) {
      setSelectedArticle(a);
      return;
    }
    setDetailLoading(true);
    try {
      const expert = isExpertArticle(a);
      const payload = expert ? await api.getHealthArticleDetail(a.id) : await api.getArticleDetail(a.id);
      const detail = payload?.item || payload;
      if (!expert) {
        api.trackArticle({ event_type: 'click', article_id: a.id, meta_data: { source: 'mobile_knowledge' } }).catch(() => {});
      }
      setSelectedArticle({
        ...a,
        ...normalizeKnowledgeArticle({
          ...detail,
          cat: expert ? EXPERT_CATEGORY : (detail.category || a.cat),
          category: expert ? EXPERT_CATEGORY : (detail.category || a.cat),
          source_type: expert ? 'expert_article' : detail.source_type,
        }, a.cat),
        cat: expert ? EXPERT_CATEGORY : (detail.category || a.cat),
        summary: detail.summary || a.summary,
        views: detail.view_count ?? a.views,
        likes: detail.likes ?? a.likes,
      });
      if (!expert) {
        setArticles(prev => prev?.map(item => item.id === a.id ? { ...item, view_count: (item.view_count || 0) + 1, views: (item.views || item.view_count || 0) + 1 } : item) || prev);
        setRecommendedArticles(prev => prev.map(item => item.id === a.id ? { ...item, view_count: (item.view_count || 0) + 1, views: (item.views || item.view_count || 0) + 1 } : item));
      }
    } catch {
      setSelectedArticle({ ...a, detail_error: true });
    } finally {
      setDetailLoading(false);
    }
  }, []);

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', background: T.cream50, minHeight: 0 }}>
      {/* Header — title 左 + 搜索图标右；点击图标在下方展开输入栏 */}
      <div style={{ background: 'white', padding: '12px 16px', borderBottom: `1px solid ${T.slate200}`, flexShrink: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
          <div style={{ fontSize: 17, fontWeight: 800, color: T.slate900 }}>健康知识</div>
          <button
            onClick={() => setSearchOpen(o => !o)}
            title={searchOpen ? '关闭搜索' : '搜索文章'}
            style={{
              width: 34, height: 34, borderRadius: 10,
              background: searchOpen ? T.mint50 : T.slate100,
              border: `1px solid ${searchOpen ? T.mint200 : 'transparent'}`,
              color: searchOpen ? T.mint700 : T.slate500,
              cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center',
              transition: 'all 0.18s',
            }}
          >
            <Search size={16} />
          </button>
        </div>
        {/* 折叠搜索栏 */}
        {searchOpen && (
          <div style={{
            marginTop: 10, display: 'flex', alignItems: 'center', gap: 10,
            background: T.slate100, borderRadius: 12, padding: '8px 14px',
            animation: 'slideUpFade 0.22s cubic-bezier(0.32,0.72,0,1)',
          }}>
            <Search size={15} color={T.slate400} />
            <input
              ref={searchInputRef}
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              onCompositionStart={() => setSearchComposing(true)}
              onCompositionEnd={() => setSearchComposing(false)}
              placeholder="搜索医疗科普文章…"
              style={{
                flex: 1, border: 'none', background: 'none', outline: 'none',
                fontSize: 13, color: T.slate900, minWidth: 0,
              }}
            />
            {searchQuery && (
              <button
                onClick={() => setSearchQuery('')}
                style={{
                  width: 22, height: 22, borderRadius: '50%', background: T.slate200,
                  border: 'none', cursor: 'pointer', color: T.slate500,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                }}
              >
                <X size={11} />
              </button>
            )}
          </div>
        )}
      </div>

      {/* Category Tabs */}
      <div style={{ background: 'white', borderBottom: `1px solid ${T.slate200}`, padding: '10px 0', flexShrink: 0 }}>
        <div
          ref={tabScrollerRef}
          className="mobile-scroll"
          onMouseDown={startTabDrag}
          onMouseMove={moveTabDrag}
          onMouseUp={endTabDrag}
          onMouseLeave={endTabDrag}
          style={{
            display: 'flex', gap: 8, overflowX: 'auto', overflowY: 'hidden',
            scrollbarWidth: 'none', WebkitOverflowScrolling: 'touch' as any,
            padding: '0 40px 0 14px', touchAction: 'pan-x', cursor: tabDragging ? 'grabbing' : 'grab',
            userSelect: tabDragging ? 'none' : 'auto',
          }}
        >
          {CATEGORIES.map((cat, i) => {
            const c = CAT_COLORS[i % CAT_COLORS.length];
            const active = activeTab === cat;
            return (
              <button key={cat} data-cat={cat} onClick={(e) => {
                if (tabDragRef.current.moved) {
                  e.preventDefault();
                  e.stopPropagation();
                  return;
                }
                setActiveTab(cat);
              }} style={{
                display: 'flex', alignItems: 'center', gap: 6, padding: '7px 13px', borderRadius: 20, flexShrink: 0,
                border: `1.5px solid ${active ? c.border : T.slate200}`,
                background: active ? c.bg : 'white',
                color: active ? c.text : T.slate500,
                fontSize: 12, fontWeight: active ? 700 : 500, cursor: 'pointer', transition: 'all 0.18s',
              }}>
                <span style={{ color: active ? c.icon : T.slate400 }}>{CAT_ICONS[cat]}</span>
                {cat}
              </button>
            );
          })}
        </div>
      </div>

      {/* Articles — 单列知识流 */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '14px 12px' }} className="mobile-scroll">
        {activeTab !== '实时热点追踪' && !searchQuery.trim() && recommendedArticles.length > 0 && (
          <div style={{ marginBottom: 14 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
              <div style={{ fontSize: 13, fontWeight: 900, color: T.slate900 }}>为你推荐</div>
              <div style={{ fontSize: 10, color: T.slate500 }}>基于档案与阅读偏好</div>
            </div>
            <div
              ref={recommendScrollerRef}
              className="mobile-scroll"
              onMouseDown={startRecommendDrag}
              onMouseMove={moveRecommendDrag}
              onMouseUp={endRecommendDrag}
              onMouseLeave={endRecommendDrag}
              style={{
                display: 'flex', gap: 10, overflowX: 'auto', overflowY: 'hidden', paddingBottom: 2,
                WebkitOverflowScrolling: 'touch' as any, touchAction: 'pan-x',
                cursor: recommendDragging ? 'grabbing' : 'grab',
                userSelect: recommendDragging ? 'none' : 'auto',
              }}
            >
              {recommendedArticles.slice(0, 5).map((item) => (
                <button
                  key={`rec-${item.id}`}
                  onClick={(e) => {
                    if (recommendDragRef.current.moved) {
                      e.preventDefault();
                      e.stopPropagation();
                      return;
                    }
                    openArticle(item);
                  }}
                  style={{ width: 220, flexShrink: 0, textAlign: 'left', border: `1px solid ${T.slate200}`, borderRadius: 16, background: 'white', padding: 9, boxShadow: '0 4px 14px rgba(15,28,8,0.04)', cursor: recommendDragging ? 'grabbing' : 'pointer' }}
                >
                  <ArticleCover article={item} height={92} />
                  <div style={{ marginTop: 8, fontSize: 13, fontWeight: 850, color: T.slate900, lineHeight: 1.35, display: '-webkit-box', WebkitLineClamp: 2 as any, WebkitBoxOrient: 'vertical' as any, overflow: 'hidden' }}>{item.title}</div>
                  <div style={{ marginTop: 6, display: 'flex', gap: 5, alignItems: 'center', color: T.slate500, fontSize: 10 }}>
                    <Clock size={10} /> {item.reading_time || 3} 分钟
                    {(item.tags || []).slice(0, 1).map((tag: string) => <span key={tag}>· {tag}</span>)}
                  </div>
                </button>
              ))}
            </div>
          </div>
        )}
        {/* 加载/失败/空状态 */}
        {((activeTab === '实时热点追踪' && hotArticles === null) || (activeTab !== '实时热点追踪' && articles === null)) && !loadErr && (
          <div style={{ textAlign: 'center', padding: '40px 16px', color: T.slate400, fontSize: 12 }}>
            🤖 正在加载文章…
          </div>
        )}
        {loadErr && (
          <div style={{ marginBottom: 10, border: `1px solid ${T.red200}`, background: T.red50, color: T.red700, borderRadius: 12, padding: '8px 10px', fontSize: 11.5, lineHeight: 1.45 }}>
            加载失败：{loadErr}{activeTab === EXPERT_CATEGORY ? '。请确认 C 端后端和管理端文章服务已启动。' : '。已显示本地示例。'}
          </div>
        )}
        {articles !== null && filtered.length === 0 && (
          <div style={{ textAlign: 'center', padding: '40px 16px', color: T.slate400, fontSize: 12 }}>
            {searchQuery ? `没有匹配「${searchQuery}」的文章` : '该分类暂无文章'}
          </div>
        )}
        {filtered.length > 0 && (
          <div style={{
            display: 'grid', gridTemplateColumns: '1fr', gap: 12,
            alignItems: 'start',
          }}>
            {filtered.map((a, idx) => {
              const catColor = CAT_COLORS[CATEGORIES.indexOf(a.cat) % CAT_COLORS.length] || CAT_COLORS[0];
              const isFeatured = idx === 0 && !searchQuery;  // 顶部第一张高亮"精选"
              const expertArticle = isExpertArticle(a);
              return (
                <div key={a.id} style={{
                  background: 'white',
                  border: `1px solid ${T.slate200}`,
                  borderRadius: 18, overflow: 'hidden',
                  boxShadow: '0 4px 16px rgba(15,28,8,0.05)',
                  display: 'flex', flexDirection: 'column',
                  cursor: 'pointer',
                }} onClick={() => openArticle(a)}>
                  <div style={{ padding: 10, paddingBottom: 0, position: 'relative' }}>
                    <ArticleCover article={a} height={150} />
                    {isFeatured && (
                      <span style={{
                        position: 'absolute', top: 6, left: 6,
                        fontSize: 9, padding: '1px 7px', borderRadius: 6,
                        background: 'rgba(255,255,255,0.9)', color: T.g700, fontWeight: 800,
                      }}>🔥 精选</span>
                    )}
                  </div>
                  {/* 标题 + 摘要 */}
                  <div style={{ padding: '12px 14px 13px', flex: 1, display: 'flex', flexDirection: 'column' }}>
                    <div style={{
                      fontSize: 15, fontWeight: 850, color: T.slate900,
                      lineHeight: 1.45, marginBottom: 6,
                      display: '-webkit-box', WebkitLineClamp: 2 as any, WebkitBoxOrient: 'vertical' as any,
                      overflow: 'hidden',
                    }}>{a.title}</div>
                    {a.summary && (
                      <div style={{
                        fontSize: 12, color: T.slate500, lineHeight: 1.55,
                        display: '-webkit-box', WebkitLineClamp: 3 as any, WebkitBoxOrient: 'vertical' as any,
                        overflow: 'hidden', marginBottom: 6,
                      }}>{a.summary}</div>
                    )}
                    <div style={{
                      marginTop: 'auto', display: 'flex',
                      alignItems: 'center', justifyContent: 'space-between',
                      gap: 6, paddingTop: 4,
                    }}>
                      <span style={{ fontSize: 10, color: T.slate400,
                        display: 'inline-flex', alignItems: 'center', gap: 3 }}>
                        <Eye size={9} /> {a.views.toLocaleString()}
                      </span>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                        <span style={{ fontSize: 10, color: T.slate400, display: 'inline-flex', alignItems: 'center', gap: 3 }}>
                          <Clock size={9} /> {a.reading_time || 3} 分钟
                        </span>
                        {!expertArticle && (
                          <button
                            onClick={e => { e.stopPropagation(); onFavorite(a); }}
                            style={{
                              fontSize: 10, background: 'none', border: 'none', cursor: 'pointer',
                              color: favorited.has(a.id) ? T.g600 : T.slate400,
                              display: 'inline-flex', alignItems: 'center', gap: 3,
                              fontWeight: favorited.has(a.id) ? 800 : 500, padding: 0,
                            }}
                          >
                            {favorited.has(a.id)
                              ? <Star size={11} fill={T.g300} color={T.g300} />
                              : <Star size={11} />}
                          </button>
                        )}
                      </div>
                    </div>
                    {Array.isArray(a.tags) && a.tags.length > 0 && (
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5, marginTop: 8 }}>
                        {a.tags.slice(0, 3).map((tag: string) => (
                          <span key={tag} style={{ fontSize: 9.5, color: T.slate500, background: T.slate100, borderRadius: 999, padding: '2px 7px', fontWeight: 700 }}>{tag}</span>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
      {detailLoading && (
        <div style={{ position: 'absolute', inset: 0, background: 'rgba(234,244,204,0.72)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: T.slate600, fontSize: 13, fontWeight: 800 }}>
          正在打开文章…
        </div>
      )}
      {selectedArticle && (
        <MobileArticleDetail
          article={selectedArticle}
          liked={typeof selectedArticle.id === 'number' && liked.has(selectedArticle.id)}
          favorited={typeof selectedArticle.id === 'number' && favorited.has(selectedArticle.id)}
          onClose={() => setSelectedArticle(null)}
          onLike={onLike}
          onFavorite={onFavorite}
        />
      )}
    </div>
  );
};

// ─── MobileGraphScreen ─────────────────────────────────────────────
const GRAPH_NODE_COLOR: Record<string, string> = {
  Disease: '#8FAE9B',
  Symptom: '#9DBCCA',
  Drug: '#D7A2A6',
  Department: '#B8A6C9',
  Food: '#D8BE8F',
  Check: '#A8B7C8',
  Cure: '#A5B99A',
  Producer: '#B5BDAE',
};
const GRAPH_NODE_FILL: Record<string, string> = {
  Disease: 'rgba(143,174,155,0.72)',
  Symptom: 'rgba(157,188,202,0.72)',
  Drug: 'rgba(215,162,166,0.72)',
  Department: 'rgba(184,166,201,0.72)',
  Food: 'rgba(216,190,143,0.72)',
  Check: 'rgba(168,183,200,0.72)',
  Cure: 'rgba(165,185,154,0.72)',
  Producer: 'rgba(181,189,174,0.72)',
};
const GRAPH_LABEL_CN: Record<string, string> = {
  Disease: '疾病', Symptom: '症状', Drug: '药物', Department: '科室',
  Food: '食物', Check: '检查', Cure: '疗法', Producer: '厂商',
};
const GRAPH_REL_CN: Record<string, string> = {
  HAS_SYMPTOM: '症状表现', TREATS: '治疗', CONTRAINDICATED_FOR: '禁用',
  BELONGS_TO: '所属科室', DO_EAT: '宜吃', NOT_EAT: '忌吃',
  RECOMMAND_EAT: '推荐饮食', COMMON_DRUG: '常用药物',
  RECOMMAND_DRUG: '推荐用药', NEED_CHECK: '相关检查',
  ACOMPANY_WITH: '并发', CURE_WAY: '疗法',
};
const GRAPH_TYPE_PRIORITY: Record<string, number> = {
  Symptom: 1, Drug: 2, Check: 3, Cure: 4, Food: 5, Department: 6, Disease: 7, Producer: 8,
};
const GRAPH_TYPE_LABELS = ['Symptom', 'Drug', 'Check', 'Cure', 'Food', 'Department', 'Disease', 'Producer'];

const MobileGraphScreen: React.FC<{ onClose: () => void }> = ({ onClose }) => {
  const graphRef = useRef<any>(null);
  const graphContainerRef = useRef<HTMLDivElement | null>(null);
  const canvasWrapRef = useRef<HTMLDivElement | null>(null);
  const selectedGraphNodeRef = useRef<string>('');
  const [popular, setPopular] = useState<{ diseases?: any[]; drugs?: any[] }>({});
  const [keyword, setKeyword] = useState('');
  const [mainType, setMainType] = useState('全部');
  const [targetTypes, setTargetTypes] = useState<string[]>([]);
  const [targetPickerOpen, setTargetPickerOpen] = useState(false);
  const [depth, setDepth] = useState(1);
  const [maxNodes, setMaxNodes] = useState(12);
  const [graphData, setGraphData] = useState<{ nodes: any[]; links: any[] }>({ nodes: [], links: [] });
  const [allGraphData, setAllGraphData] = useState<{ nodes: any[]; links: any[] }>({ nodes: [], links: [] });
  const [graphMode, setGraphMode] = useState<'graph' | 'list'>('graph');
  const [loading, setLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState('');
  const [meta, setMeta] = useState<any>({});
  const [selectedNode, setSelectedNode] = useState<any | null>(null);
  const [explainMd, setExplainMd] = useState('');
  const [explainLoading, setExplainLoading] = useState(false);
  const [canvasSize, setCanvasSize] = useState({ w: 360, h: 360 });

  useEffect(() => {
    api.graphPopular(8).then(setPopular).catch(() => setPopular({}));
  }, []);

  useEffect(() => {
    const update = () => {
      const rect = canvasWrapRef.current?.getBoundingClientRect();
      if (!rect) return;
      setCanvasSize({ w: Math.max(320, rect.width), h: Math.max(300, rect.height) });
    };
    update();
    window.addEventListener('resize', update);
    const t = window.setTimeout(update, 80);
    return () => { window.removeEventListener('resize', update); window.clearTimeout(t); };
  }, [graphData.nodes.length]);

  const degreeMap = React.useMemo(() => {
    const deg: Record<string, number> = {};
    graphData.links.forEach((l: any) => {
      const s = typeof l.source === 'object' ? l.source.id : l.source;
      const t = typeof l.target === 'object' ? l.target.id : l.target;
      deg[s] = (deg[s] || 0) + 1;
      deg[t] = (deg[t] || 0) + 1;
    });
    return deg;
  }, [graphData.links]);
  const centerId = React.useMemo(() => {
    let best = '';
    let bestDeg = -1;
    graphData.nodes.forEach((n: any) => {
      const d = degreeMap[n.id] || 0;
      if ((n.name === keyword || String(n.name || '').includes(keyword)) && d > bestDeg) {
        best = n.id; bestDeg = d;
      }
    });
    return best;
  }, [graphData.nodes, degreeMap, keyword]);

  const allDegreeMap = React.useMemo(() => {
    const deg: Record<string, number> = {};
    allGraphData.links.forEach((l: any) => {
      deg[l.source] = (deg[l.source] || 0) + 1;
      deg[l.target] = (deg[l.target] || 0) + 1;
    });
    return deg;
  }, [allGraphData.links]);

  const groupedAllNodes = React.useMemo(() => {
    const center = graphData.nodes.find((n: any) => n.id === centerId);
    const grouped: Record<string, any[]> = {};
    allGraphData.nodes
      .filter((n: any) => n.id !== center?.id)
      .forEach((n: any) => {
        const label = n.label || 'Unknown';
        if (!grouped[label]) grouped[label] = [];
        grouped[label].push(n);
      });
    Object.keys(grouped).forEach(label => {
      grouped[label].sort((a: any, b: any) => (allDegreeMap[b.id] || 0) - (allDegreeMap[a.id] || 0));
    });
    return grouped;
  }, [allGraphData.nodes, allDegreeMap, graphData.nodes, centerId]);

  const recenterGraph = useCallback(() => {
    graphRef.current?.fitCenter?.({ duration: 260 });
  }, []);

  const doSearch = useCallback(async (raw?: string) => {
    const kw = (raw ?? keyword).trim();
    if (!kw || loading) return;
    setKeyword(kw);
    setLoading(true);
    setErrorMsg('');
    setSelectedNode(null);
    setExplainMd('');
    setGraphMode('graph');
    selectedGraphNodeRef.current = '';
    try {
      const target_types = targetTypes.length ? targetTypes.join(',') : '全部';
      const r: any = await api.graphSearch({ keyword: kw, main_type: mainType, target_types, depth, max_nodes: maxNodes });
      if (r?.status !== 'success') {
        setErrorMsg('图谱查询失败');
        return;
      }
      const nodes = (r.data?.nodes || []).map((n: any) => ({
        id: String(n.id), name: String(n.name || ''), label: String(n.label || 'Unknown'),
      }));
      const rawLinks = (r.data?.links || [])
        .map((l: any) => ({ source: String(l.source), target: String(l.target), relationship: l.relationship }))
      const rawDegree: Record<string, number> = {};
      rawLinks.forEach((l: any) => {
        rawDegree[l.source] = (rawDegree[l.source] || 0) + 1;
        rawDegree[l.target] = (rawDegree[l.target] || 0) + 1;
      });
      let center = nodes.find((n: any) => n.name === kw) || nodes.find((n: any) => String(n.name || '').includes(kw));
      if (!center) center = [...nodes].sort((a: any, b: any) => (rawDegree[b.id] || 0) - (rawDegree[a.id] || 0))[0];
      const allValid = new Set(nodes.map((n: any) => n.id));
      const allLinks = rawLinks.filter((l: any) => allValid.has(l.source) && allValid.has(l.target));
      setAllGraphData({ nodes, links: allLinks });
      const centerNeighborIds = new Set<string>();
      if (center) {
        rawLinks.forEach((l: any) => {
          if (l.source === center.id) centerNeighborIds.add(l.target);
          if (l.target === center.id) centerNeighborIds.add(l.source);
        });
      }
      const candidates = nodes
        .filter((n: any) => n.id !== center?.id)
        .filter((n: any) => !center || centerNeighborIds.has(n.id))
        .sort((a: any, b: any) => {
          const ap = GRAPH_TYPE_PRIORITY[a.label] || 99;
          const bp = GRAPH_TYPE_PRIORITY[b.label] || 99;
          if (ap !== bp) return ap - bp;
          return (rawDegree[b.id] || 0) - (rawDegree[a.id] || 0);
        });
      const fallback = nodes
        .filter((n: any) => n.id !== center?.id && !centerNeighborIds.has(n.id))
        .sort((a: any, b: any) => (rawDegree[b.id] || 0) - (rawDegree[a.id] || 0));
      const limitedNodes = [
        ...(center ? [center] : []),
        ...[...candidates, ...fallback].slice(0, Math.max(0, maxNodes - (center ? 1 : 0))),
      ];
      const valid = new Set(limitedNodes.map((n: any) => n.id));
      const links = rawLinks.filter((l: any) => valid.has(l.source) && valid.has(l.target));
      setGraphData({ nodes: limitedNodes, links });
      setMeta({
        normalized: r.normalized_from && r.actual_keyword && r.normalized_from !== r.actual_keyword
          ? { from: r.normalized_from, to: r.actual_keyword, hint: r.norm_hint }
          : null,
        truncated: Boolean(r.truncated || nodes.length > limitedNodes.length || (r.original_count && r.original_count > limitedNodes.length)),
        original: r.original_count || nodes.length,
        shown: limitedNodes.length,
      });
      if (!nodes.length) setErrorMsg(`图谱中暂无与「${kw}」相关的节点`);
    } catch (e: any) {
      setErrorMsg(e?.message || '图谱服务暂不可用');
    } finally {
      setLoading(false);
    }
  }, [keyword, mainType, targetTypes, depth, maxNodes, loading]);

  useEffect(() => {
    if (!keyword.trim() || !graphData.nodes.length || loading) return;
    const t = window.setTimeout(() => doSearch(keyword), 80);
    return () => window.clearTimeout(t);
  }, [mainType, targetTypes, depth, maxNodes]);

  const openNode = useCallback(async (node: any) => {
    setSelectedNode({ id: node.id, name: node.name, label: node.label });
    setExplainMd('');
    setExplainLoading(true);
    try {
      const r: any = await api.graphExplain(node.name, node.label);
      setExplainMd(r?.explanation || '暂无节点解读。');
    } catch (e: any) {
      setExplainMd(`> 暂无解读：${e?.message || '请稍后重试'}`);
    } finally {
      setExplainLoading(false);
    }
  }, []);

  const buildGraphNodeStyle = useCallback((node: any, selectedId = '') => {
    const degree = degreeMap[node.id] || node.degree || 0;
    const isCenter = centerId === node.id;
    const isSelected = selectedId === node.id;
    const size = isCenter ? 34 : isSelected ? 24 : maxNodes <= 12 ? 18 : maxNodes <= 24 ? 15 : 13;
    const showLabel = isCenter || isSelected;
    return {
      size,
      fill: GRAPH_NODE_FILL[node.label] || 'rgba(181,189,174,0.68)',
      stroke: '#ffffff',
      lineWidth: isCenter || isSelected ? 2.6 : 1.5,
      shadowColor: GRAPH_NODE_COLOR[node.label] || '#AEB8AA',
      shadowBlur: isCenter || isSelected ? 14 : 3,
      halo: isCenter || isSelected,
      haloFill: GRAPH_NODE_FILL[node.label] || 'rgba(181,189,174,0.36)',
      haloStroke: GRAPH_NODE_COLOR[node.label] || '#AEB8AA',
      haloLineWidth: isCenter ? 12 : 9,
      label: showLabel,
      labelText: showLabel ? String(node.name || '').slice(0, isCenter ? 10 : 6) : '',
      labelFill: T.slate700,
      labelFontSize: isCenter ? 12 : 8.5,
      labelFontWeight: isCenter ? 900 : 800,
      labelPlacement: 'bottom',
      labelOffsetY: 3,
    };
  }, [degreeMap, centerId, maxNodes]);

  const layoutGraphNodes = useCallback((nodes: any[]) => {
    const w = canvasSize.w || 360;
    const h = canvasSize.h || 390;
    const centerX = w * 0.5;
    const centerY = h * 0.58;
    const center = nodes.find((n: any) => n.id === centerId) || nodes[0];
    const others = nodes.filter((n: any) => n.id !== center?.id);
    const lanes = [
      { labels: ['Symptom', 'Disease'], start: -158, end: -66, radius: Math.min(w, h) * 0.33 },
      { labels: ['Drug', 'Check', 'Cure'], start: -36, end: 58, radius: Math.min(w, h) * 0.37 },
      { labels: ['Food', 'Department', 'Producer'], start: 88, end: 178, radius: Math.min(w, h) * 0.34 },
    ];
    const assigned = new Map<string, { x: number; y: number }>();
    lanes.forEach(lane => {
      const group = others.filter((n: any) => lane.labels.includes(n.label));
      group.forEach((n: any, i: number) => {
        const t = group.length === 1 ? 0.5 : i / (group.length - 1);
        const angle = (lane.start + (lane.end - lane.start) * t) * Math.PI / 180;
        const jitter = (i % 2 ? 1 : -1) * Math.min(18, group.length * 1.6);
        assigned.set(n.id, {
          x: centerX + Math.cos(angle) * (lane.radius + jitter),
          y: centerY + Math.sin(angle) * (lane.radius + jitter * 0.4),
        });
      });
    });
    const unassigned = others.filter((n: any) => !assigned.has(n.id));
    unassigned.forEach((n: any, i: number) => {
      const angle = (-130 + (260 * (i + 1)) / (unassigned.length + 1)) * Math.PI / 180;
      assigned.set(n.id, {
        x: centerX + Math.cos(angle) * Math.min(w, h) * 0.39,
        y: centerY + Math.sin(angle) * Math.min(w, h) * 0.35,
      });
    });
    return nodes.map((n: any) => {
      const p = n.id === center?.id ? { x: centerX, y: centerY } : assigned.get(n.id) || { x: centerX, y: centerY };
      return { ...n, x: p.x, y: p.y };
    });
  }, [canvasSize.w, canvasSize.h, centerId]);

  const highlightGraphNode = useCallback((id: string) => {
    const graph = graphRef.current;
    if (!graph) return;
    selectedGraphNodeRef.current = id;
    const nodes = graphData.nodes.map((n: any) => ({
      id: n.id,
      style: buildGraphNodeStyle(n, id),
    }));
    graph.updateNodeData(nodes);
    const edges = graphData.links.map((l: any, i: number) => {
      const source = typeof l.source === 'object' ? l.source.id : l.source;
      const target = typeof l.target === 'object' ? l.target.id : l.target;
      const active = source === id || target === id;
      return {
        id: `e-${i}-${source}-${target}`,
        style: {
          stroke: active ? 'rgba(88,112,72,0.46)' : 'rgba(116,135,109,0.10)',
          lineWidth: active ? 1.15 : 0.55,
          opacity: active ? 0.88 : 0.36,
        },
      };
    });
    graph.updateEdgeData?.(edges);
    graph.draw?.();
  }, [graphData.nodes, graphData.links, buildGraphNodeStyle]);

  useEffect(() => {
    const container = graphContainerRef.current;
    if (!container || !graphData.nodes.length) {
      graphRef.current?.destroy?.();
      graphRef.current = null;
      return;
    }

    graphRef.current?.destroy?.();
    let disposed = false;
    let graph: any = null;
    const positionedNodes = layoutGraphNodes(graphData.nodes);
    const graphNodes = positionedNodes.map((n: any) => {
      const degree = degreeMap[n.id] || 0;
      return {
        id: n.id,
        data: { ...n, degree },
        style: { ...buildGraphNodeStyle({ ...n, degree }), x: n.x, y: n.y },
      };
    });
    const graphEdges = graphData.links.map((l: any, i: number) => ({
      id: `e-${i}-${l.source}-${l.target}`,
      source: String(l.source),
      target: String(l.target),
      data: { relationship: l.relationship },
      style: {
        stroke: 'rgba(116,135,109,0.15)',
        lineWidth: graphData.nodes.length > 50 ? 0.45 : 0.65,
        opacity: 0.58,
      },
    }));

    import('@antv/g6').then(({ Graph, NodeEvent }) => {
      if (disposed || !graphContainerRef.current) return;
      graph = new Graph({
        container,
        width: canvasSize.w,
        height: canvasSize.h,
        autoResize: true,
        background: '#fbfff1',
        data: { nodes: graphNodes, edges: graphEdges },
        node: { type: 'circle' },
        edge: { type: 'line' },
        layout: {
          type: 'preset',
        } as any,
        behaviors: [
          'drag-canvas',
          'zoom-canvas',
          'drag-element',
        ],
      } as any);

      graph.on(NodeEvent.CLICK, (evt: any) => {
        const id = evt?.target?.id || evt?.target?.attributes?.id || evt?.target?.get?.('id');
        if (!id) return;
        const datum = graph.getNodeData(id);
        const node = (datum as any)?.data || datum;
        highlightGraphNode(String(id));
        openNode(node);
        graph.focusElement(id, { duration: 260 });
      });

      graph.render().then(() => {
        graph.fitCenter({ duration: 320 });
      }).catch(() => undefined);
      graphRef.current = graph;
    }).catch(() => setErrorMsg('图谱引擎加载失败，请刷新后重试'));

    return () => {
      disposed = true;
      graph?.destroy?.();
      if (graphRef.current === graph) graphRef.current = null;
    };
  }, [graphData, degreeMap, canvasSize.w, canvasSize.h, maxNodes, openNode, highlightGraphNode, buildGraphNodeStyle, layoutGraphNodes]);

  const neighbors = selectedNode ? graphData.links.reduce((acc: any[], l: any) => {
    const s = typeof l.source === 'object' ? l.source : graphData.nodes.find(n => n.id === l.source);
    const t = typeof l.target === 'object' ? l.target : graphData.nodes.find(n => n.id === l.target);
    if (s?.id === selectedNode.id && t) acc.push({ node: t, rel: l.relationship });
    if (t?.id === selectedNode.id && s) acc.push({ node: s, rel: l.relationship });
    return acc;
  }, []).slice(0, 8) : [];

  const typeChips = [
    { value: '全部', label: '全部' },
    { value: 'Disease', label: '疾病' },
    { value: 'Symptom', label: '症状' },
    { value: 'Drug', label: '药物' },
    { value: 'Department', label: '科室' },
    { value: 'Food', label: '食物' },
    { value: 'Check', label: '检查' },
    { value: 'Producer', label: '厂商' },
    { value: 'Cure', label: '疗法' },
  ];
  const targetOptions = typeChips.filter(c => c.value !== '全部');
  const targetSummary = targetTypes.length
    ? targetTypes.map(v => typeChips.find(c => c.value === v)?.label || v).join('、')
    : '全部关联';
  const toggleTargetType = (value: string) => {
    setTargetTypes(prev => prev.includes(value) ? prev.filter(v => v !== value) : [...prev, value]);
  };
  const openVisibleNode = (node: any) => {
    highlightGraphNode(node.id);
    openNode(node);
    graphRef.current?.focusElement?.(node.id, { duration: 220 });
  };

  return (
    <div style={{ position: 'absolute', left: 0, right: 0, top: 44, bottom: 0, zIndex: 7, background: T.cream50, display: 'flex', flexDirection: 'column' }}>
      <div style={{ height: 54, background: 'white', borderBottom: `1px solid ${T.slate200}`, padding: '0 14px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0 }}>
        <button onClick={onClose} style={{ width: 34, height: 34, border: 'none', borderRadius: 12, background: T.g100, color: T.g700, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <ArrowLeft size={17} />
        </button>
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontSize: 15, fontWeight: 900, color: T.slate900 }}>医疗知识图谱</div>
          <div style={{ fontSize: 10, color: T.slate400, marginTop: 1 }}>关系拓扑 · AI 解读</div>
        </div>
        <div style={{ minWidth: 54, textAlign: 'right', fontSize: 10, color: T.g600, fontWeight: 800 }}>
          {graphData.nodes.length ? `${meta.shown || graphData.nodes.length}/${meta.original || graphData.nodes.length}点` : ''}
        </div>
      </div>

      <div style={{ padding: '10px 12px', background: 'white', borderBottom: `1px solid ${T.slate200}`, flexShrink: 0 }}>
        <div style={{ display: 'flex', gap: 8 }}>
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 8, background: T.g50, border: `1px solid ${T.g200}`, borderRadius: 14, padding: '0 10px' }}>
            <Search size={15} color={T.g500} />
            <input
              value={keyword}
              onChange={e => setKeyword(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') doSearch(); }}
              placeholder="疾病/症状/药物..."
              style={{ flex: 1, minWidth: 0, height: 38, border: 'none', outline: 'none', background: 'transparent', color: T.slate900, fontSize: 13 }}
            />
            {keyword && <button onClick={() => setKeyword('')} style={{ border: 'none', background: 'transparent', color: T.slate400, padding: 0 }}><X size={13} /></button>}
          </div>
          <button onClick={() => doSearch()} disabled={!keyword.trim() || loading} style={{ width: 46, border: 'none', borderRadius: 14, background: loading || !keyword.trim() ? T.slate200 : T.g600, color: 'white', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            {loading ? <RefreshCw size={15} className="rfg-spin" /> : <Search size={15} />}
          </button>
        </div>
        <div style={{ marginTop: 9, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 7 }}>
          <select value={mainType} onChange={e => setMainType(e.target.value)} style={{ minWidth: 0, height: 32, borderRadius: 12, border: `1px solid ${T.g200}`, background: T.g50, color: T.g700, fontSize: 11, fontWeight: 900, padding: '0 8px', outline: 'none' }}>
            {typeChips.map(c => <option key={c.value} value={c.value}>中心·{c.label}</option>)}
          </select>
          <button onClick={() => setTargetPickerOpen(true)} style={{ minWidth: 0, height: 32, borderRadius: 12, border: `1px solid ${T.g200}`, background: T.g50, color: T.g700, fontSize: 11, fontWeight: 900, padding: '0 8px', outline: 'none', textAlign: 'left', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
            关联·{targetSummary}
          </button>
        </div>
        <div style={{ marginTop: 7, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 7 }}>
          <select value={depth} onChange={e => setDepth(Number(e.target.value))} style={{ minWidth: 0, height: 32, borderRadius: 12, border: `1px solid ${T.g200}`, background: T.g50, color: T.g700, fontSize: 11, fontWeight: 900, padding: '0 8px', outline: 'none' }}>
            <option value={1}>1 跳</option>
            <option value={2}>2 跳</option>
          </select>
          <select value={maxNodes} onChange={e => setMaxNodes(Number(e.target.value))} style={{ minWidth: 0, height: 32, borderRadius: 12, border: `1px solid ${T.g200}`, background: T.g50, color: T.g700, fontSize: 11, fontWeight: 900, padding: '0 8px', outline: 'none' }}>
            <option value={12}>12 点</option>
            <option value={24}>24 点</option>
            <option value={40}>40 点</option>
          </select>
        </div>
      </div>

      <div style={{ flex: 1, overflowY: 'auto', padding: 12 }} className="mobile-scroll">
        {(meta.normalized || meta.truncated || errorMsg) && (
          <div style={{ marginBottom: 10, borderRadius: 12, padding: '8px 10px', background: errorMsg ? T.red50 : T.g100, color: errorMsg ? T.red700 : T.g700, fontSize: 11, lineHeight: 1.5, fontWeight: 700 }}>
            {errorMsg || (meta.truncated ? `已按重要性展示 ${meta.shown}/${meta.original} 个关键节点` : '') || (meta.normalized ? `已将「${meta.normalized.from}」匹配为「${meta.normalized.to}」` : '')}
          </div>
        )}

        {!graphData.nodes.length && !loading && (
          <div style={{ background: 'white', border: `1px solid ${T.g200}`, borderRadius: 18, padding: 16, marginBottom: 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
              <div style={{ width: 42, height: 42, borderRadius: 14, background: T.g100, color: T.g700, display: 'flex', alignItems: 'center', justifyContent: 'center' }}><GitBranch size={20} /></div>
              <div>
                <div style={{ color: T.slate900, fontSize: 15, fontWeight: 900 }}>探索医疗关系</div>
                <div style={{ color: T.slate500, fontSize: 11, marginTop: 2 }}>从热门疾病或药物开始</div>
              </div>
            </div>
            <div style={{ color: T.slate500, fontSize: 11, fontWeight: 800, marginBottom: 8 }}>热门疾病</div>
            <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap', marginBottom: 12 }}>
              {(popular.diseases || []).slice(0, 8).map((p: any) => (
                <button key={`d-${p.name}`} onClick={() => doSearch(p.name)} style={{ border: `1px solid ${T.g200}`, background: T.g50, color: T.g700, borderRadius: 999, padding: '6px 10px', fontSize: 11, fontWeight: 800 }}>{p.name}</button>
              ))}
            </div>
            <div style={{ color: T.slate500, fontSize: 11, fontWeight: 800, marginBottom: 8 }}>热门药物</div>
            <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap' }}>
              {(popular.drugs || []).slice(0, 6).map((p: any) => (
                <button key={`m-${p.name}`} onClick={() => doSearch(p.name)} style={{ border: `1px solid ${T.g200}`, background: 'white', color: T.g600, borderRadius: 999, padding: '6px 10px', fontSize: 11, fontWeight: 800 }}>{p.name}</button>
              ))}
            </div>
          </div>
        )}

        {graphData.nodes.length > 0 && (
          <div style={{ background: 'rgba(251,255,241,0.92)', border: `1px solid rgba(194,213,180,0.78)`, borderRadius: 20, overflow: 'hidden', height: 412, position: 'relative', boxShadow: '0 18px 44px rgba(88,112,72,0.10)' }}>
            <div ref={canvasWrapRef} style={{ position: 'absolute', inset: 0 }}>
              <div ref={graphContainerRef} style={{ width: '100%', height: '100%', touchAction: 'none' }} />
            </div>
            {meta.original > meta.shown && (
              <button onClick={() => setGraphMode('list')} style={{ position: 'absolute', left: 12, top: 12, border: 'none', borderRadius: 999, padding: '6px 10px', background: 'rgba(217,234,159,0.92)', color: T.g700, fontSize: 10, fontWeight: 900 }}>
                展开更多 {meta.original - meta.shown}
              </button>
            )}
            {loading && <div style={{ position: 'absolute', inset: 0, background: 'rgba(255,255,255,0.72)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: T.g700, fontSize: 12, fontWeight: 900 }}>正在生成图谱...</div>}
            <div style={{ position: 'absolute', right: 10, bottom: 10, display: 'flex', gap: 6 }}>
              <button onClick={recenterGraph} style={{ width: 34, height: 34, border: 'none', borderRadius: 12, background: T.g100, color: T.g700, display: 'flex', alignItems: 'center', justifyContent: 'center' }}><Target size={15} /></button>
              <button onClick={() => graphRef.current?.zoomBy?.(1.18, { duration: 180 })} style={{ width: 34, height: 34, border: 'none', borderRadius: 12, background: T.g100, color: T.g700, display: 'flex', alignItems: 'center', justifyContent: 'center' }}><Plus size={15} /></button>
              <button onClick={() => { graphRef.current?.destroy?.(); graphRef.current = null; selectedGraphNodeRef.current = ''; setGraphData({ nodes: [], links: [] }); setAllGraphData({ nodes: [], links: [] }); setSelectedNode(null); setErrorMsg(''); setMeta({}); }} style={{ width: 34, height: 34, border: 'none', borderRadius: 12, background: T.g100, color: T.g700, display: 'flex', alignItems: 'center', justifyContent: 'center' }}><X size={15} /></button>
            </div>
          </div>
        )}

        {graphData.nodes.length > 0 && (
          <div style={{ marginTop: 10, background: 'white', border: `1px solid ${T.g200}`, borderRadius: 16, padding: 10 }}>
            <div style={{ color: T.slate500, fontSize: 10, fontWeight: 900, marginBottom: 7 }}>可见节点</div>
            <div className="mobile-scroll" style={{ display: 'flex', gap: 6, overflowX: 'auto', paddingBottom: 8, marginBottom: 8 }}>
              {graphData.nodes.map((n: any) => {
                const active = selectedNode?.id === n.id;
                return (
                  <button
                    key={n.id}
                    onClick={() => openVisibleNode(n)}
                    style={{
                      flex: '0 0 auto',
                      border: `1px solid ${active ? GRAPH_NODE_COLOR[n.label] || T.g300 : T.g200}`,
                      background: active ? GRAPH_NODE_FILL[n.label] || T.g100 : 'white',
                      color: T.g700,
                      borderRadius: 999,
                      padding: '5px 8px',
                      maxWidth: 120,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                      fontSize: 10,
                      fontWeight: 850,
                    }}
                  >
                    {n.name}
                  </button>
                );
              })}
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <button onClick={() => setGraphMode('graph')} style={{ flex: 1, height: 30, borderRadius: 12, border: `1px solid ${graphMode === 'graph' ? T.g300 : T.g200}`, background: graphMode === 'graph' ? T.g100 : 'white', color: T.g700, fontSize: 11, fontWeight: 900 }}>图谱</button>
              <button onClick={() => setGraphMode('list')} style={{ flex: 1, height: 30, borderRadius: 12, border: `1px solid ${graphMode === 'list' ? T.g300 : T.g200}`, background: graphMode === 'list' ? T.g100 : 'white', color: T.g700, fontSize: 11, fontWeight: 900 }}>列表</button>
            </div>
            {graphMode === 'list' && (
              <div style={{ marginTop: 10, display: 'grid', gap: 10 }}>
                {GRAPH_TYPE_LABELS.filter(label => groupedAllNodes[label]?.length).map(label => (
                  <div key={label}>
                    <div style={{ color: T.slate500, fontSize: 10, fontWeight: 900, marginBottom: 6 }}>
                      {GRAPH_LABEL_CN[label] || label} · {groupedAllNodes[label].length}
                    </div>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                      {groupedAllNodes[label].slice(0, 12).map((n: any) => (
                        <button key={n.id} onClick={() => doSearch(n.name)} style={{ border: `1px solid ${T.g200}`, background: GRAPH_NODE_FILL[n.label] || T.g50, color: T.g700, borderRadius: 999, padding: '5px 8px', fontSize: 10, fontWeight: 800 }}>
                          {n.name}
                        </button>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {targetPickerOpen && (
        <div style={{ position: 'absolute', left: 0, right: 0, top: 0, bottom: 0, zIndex: 4, background: 'rgba(15,28,8,0.18)', display: 'flex', alignItems: 'flex-end' }} onClick={() => setTargetPickerOpen(false)}>
          <div onClick={e => e.stopPropagation()} style={{ width: '100%', background: 'white', borderRadius: '22px 22px 0 0', padding: '10px 16px 18px', boxShadow: '0 -14px 42px rgba(15,28,8,0.18)' }}>
            <div style={{ width: 44, height: 4, borderRadius: 99, background: T.g200, margin: '0 auto 12px' }} />
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
              <div>
                <div style={{ color: T.slate900, fontSize: 15, fontWeight: 950 }}>关联节点类型</div>
                <div style={{ color: T.slate500, fontSize: 10, marginTop: 2 }}>可多选，未选择表示全部关联</div>
              </div>
              <button onClick={() => setTargetPickerOpen(false)} style={{ width: 30, height: 30, border: 'none', borderRadius: 10, background: T.g100, color: T.g700 }}><X size={14} /></button>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
              <button onClick={() => setTargetTypes([])} style={{ height: 36, borderRadius: 12, border: `1px solid ${targetTypes.length === 0 ? T.g300 : T.g200}`, background: targetTypes.length === 0 ? T.g100 : 'white', color: T.g700, fontSize: 11, fontWeight: 900 }}>
                全部关联
              </button>
              {targetOptions.map(c => {
                const active = targetTypes.includes(c.value);
                return (
                  <button key={c.value} onClick={() => toggleTargetType(c.value)} style={{ height: 36, borderRadius: 12, border: `1px solid ${active ? GRAPH_NODE_COLOR[c.value] || T.g300 : T.g200}`, background: active ? GRAPH_NODE_FILL[c.value] || T.g100 : 'white', color: T.g700, fontSize: 11, fontWeight: 900, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6 }}>
                    <span style={{ width: 8, height: 8, borderRadius: 99, background: GRAPH_NODE_COLOR[c.value] || T.g300 }} />
                    {c.label}
                  </button>
                );
              })}
            </div>
            <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>
              <button onClick={() => setTargetTypes([])} style={{ flex: 1, height: 38, border: `1px solid ${T.g200}`, borderRadius: 14, background: 'white', color: T.g700, fontSize: 12, fontWeight: 900 }}>清空</button>
              <button onClick={() => setTargetPickerOpen(false)} style={{ flex: 1, height: 38, border: 'none', borderRadius: 14, background: T.g600, color: 'white', fontSize: 12, fontWeight: 900 }}>完成</button>
            </div>
          </div>
        </div>
      )}

      {selectedNode && (
        <div style={{ position: 'absolute', left: 0, right: 0, bottom: 0, zIndex: 3, background: 'rgba(15,28,8,0.16)' }} onClick={() => setSelectedNode(null)}>
          <div onClick={e => e.stopPropagation()} style={{ maxHeight: '54vh', overflowY: 'auto', background: 'white', borderRadius: '22px 22px 0 0', padding: '10px 16px 18px', boxShadow: '0 -14px 42px rgba(15,28,8,0.18)' }} className="mobile-scroll">
            <div style={{ width: 44, height: 4, borderRadius: 99, background: T.g200, margin: '0 auto 12px' }} />
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'flex-start' }}>
              <div>
                <div style={{ color: T.slate900, fontSize: 18, fontWeight: 950 }}>{selectedNode.name}</div>
                <div style={{ marginTop: 4, color: GRAPH_NODE_COLOR[selectedNode.label] || T.g600, fontSize: 11, fontWeight: 900 }}>{GRAPH_LABEL_CN[selectedNode.label] || selectedNode.label}</div>
              </div>
              <button onClick={() => setSelectedNode(null)} style={{ border: 'none', background: T.g100, color: T.g700, width: 30, height: 30, borderRadius: 10 }}><X size={14} /></button>
            </div>
            {neighbors.length > 0 && (
              <div style={{ marginTop: 12 }}>
                <div style={{ color: T.slate500, fontSize: 11, fontWeight: 900, marginBottom: 7 }}>相关节点</div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                  {neighbors.map((n, i) => (
                    <button key={`${n.node.id}-${i}`} onClick={() => { setSelectedNode(null); doSearch(n.node.name); }} style={{ border: `1px solid ${T.g200}`, background: T.g50, color: T.g700, borderRadius: 999, padding: '5px 8px', fontSize: 10, fontWeight: 800 }}>
                      {GRAPH_REL_CN[n.rel] || n.rel || '关联'} · {n.node.name}
                    </button>
                  ))}
                </div>
              </div>
            )}
            <div style={{ marginTop: 12, background: T.g50, border: `1px solid ${T.g200}`, borderRadius: 14, padding: 12 }}>
              {explainLoading ? (
                <div style={{ color: T.g600, fontSize: 12, fontWeight: 900, display: 'flex', alignItems: 'center', gap: 6 }}><RefreshCw size={14} className="rfg-spin" /> AI 正在解读...</div>
              ) : (
                <div className="mobile-ai-md" style={{ color: T.slate700, fontSize: 12.5, lineHeight: 1.7 }}>
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{explainMd || '暂无解读。'}</ReactMarkdown>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

// ─── ProfileScreen ─────────────────────────────────────────────────
const ProfileScreen: React.FC<{ onLogout: () => void; setupMode?: boolean; onSetupComplete?: () => void }> = ({ onLogout, setupMode = false, onSetupComplete }) => {
  const username = localStorage.getItem('current_username') || '用户';

  // 后端档案 + dashboard + AI 洞察
  const [dashboard, setDashboard] = useState<any>(null);
  const [profile, setProfile] = useState<any>({});
  const [insights, setInsights] = useState<any>(null);
  const [insightsLoading, setInsightsLoading] = useState(false);
  const [editOpen, setEditOpen] = useState(setupMode);
  const [draftProfile, setDraftProfile] = useState<any>(() => buildMobileProfileDraft({}));
  const [savingProfile, setSavingProfile] = useState(false);
  const [profileError, setProfileError] = useState('');
  const [activeProfileSection, setActiveProfileSection] = useState('basic');
  const [profileOptionSheet, setProfileOptionSheet] = useState<null | {
    field: string;
    title: string;
    options: string[];
  }>(null);
  const [profileDetailSheet, setProfileDetailSheet] = useState<any>(null);
  const [insightDetailSheet, setInsightDetailSheet] = useState<any>(null);
  const profileTabDragRef = useRef({ down: false, startX: 0, scrollLeft: 0, moved: false });
  const insightDragRef = useRef({ down: false, startX: 0, scrollLeft: 0, moved: false });
  const setupDraftReadyRef = useRef(false);

  const loadProfile = useCallback(() => {
    return api.getProfile().then((d: any) => {
      let parsed = d?.profile_data;
      if (typeof parsed === 'string') {
        try { parsed = JSON.parse(parsed); } catch { parsed = {}; }
      }
      const nextProfile = parsed || {};
      setProfile(nextProfile);
      return nextProfile;
    });
  }, []);

  useEffect(() => {
    if (!localStorage.getItem('access_token')) return;
    api.getDashboard().then(setDashboard).catch(() => {});
    loadProfile().catch(() => {});
    setInsightsLoading(true);
    api.getInsights()
      .then(setInsights)
      .catch(() => {})
      .finally(() => setInsightsLoading(false));
  }, [loadProfile]);

  const openProfileEditor = (initialSection = 'basic') => {
    setDraftProfile(buildMobileProfileDraft(profile));
    setProfileError('');
    setActiveProfileSection(initialSection);
    setProfileDetailSheet(null);
    setEditOpen(true);
  };

  useEffect(() => {
    if (!setupMode || setupDraftReadyRef.current) return;
    setupDraftReadyRef.current = true;
    setDraftProfile(buildMobileProfileDraft(profile));
    setProfileError('');
    setActiveProfileSection('basic');
    setEditOpen(true);
  }, [setupMode, profile]);

  const saveProfileDraft = async () => {
    setProfileError('');
    const pastDiseasesCommon = splitProfileText(draftProfile.past_diseases_common);
    const pastDiseasesCustom = splitProfileText(draftProfile.past_diseases_custom);
    const allergiesCommon = splitProfileText(draftProfile.allergies_common);
    const allergiesCustom = splitProfileText(draftProfile.allergies_custom);
    const vaccinesCommon = splitProfileText(draftProfile.vaccines_common);
    const vaccinesCustom = splitProfileText(draftProfile.vaccines_custom);
    const surgeries = Array.isArray(draftProfile.surgeries)
      ? draftProfile.surgeries.map((s: any) => ({
          name: String(s?.name || '').trim(),
          date: String(s?.date || '').trim(),
        })).filter((s: any) => s.name)
      : [];
    const nextProfile = {
      ...profile,
      ...draftProfile,
      age: draftProfile.age === '' ? '' : Number(draftProfile.age),
      height: draftProfile.height === '' ? '' : Number(draftProfile.height),
      weight: draftProfile.weight === '' ? '' : Number(draftProfile.weight),
      past_diseases_common: pastDiseasesCommon,
      past_diseases_custom: pastDiseasesCustom,
      allergies_common: allergiesCommon,
      allergies_custom: allergiesCustom,
      vaccines_common: vaccinesCommon,
      vaccines_custom: vaccinesCustom,
      surgeries,
      diseases: uniqueProfileValues([...pastDiseasesCommon, ...pastDiseasesCustom]),
      allergies: uniqueProfileValues([...allergiesCommon, ...allergiesCustom]),
    };
    if (nextProfile.age && (nextProfile.age < 0 || nextProfile.age > 130)) {
      setProfileError('年龄需在 0-130 岁之间');
      return;
    }
    if (nextProfile.height && (nextProfile.height < 40 || nextProfile.height > 260)) {
      setProfileError('身高需在 40-260 cm 之间');
      return;
    }
    if (nextProfile.weight && (nextProfile.weight < 2 || nextProfile.weight > 350)) {
      setProfileError('体重需在 2-350 kg 之间');
      return;
    }
    setSavingProfile(true);
    try {
      await api.saveProfile(nextProfile);
      setProfile(nextProfile);
      setEditOpen(false);
      if (setupMode) {
        onSetupComplete?.();
        return;
      }
      api.getDashboard().then(setDashboard).catch(() => {});
      api.getInsights().then(setInsights).catch(() => {});
    } catch (err: any) {
      setProfileError(err?.message || '保存失败，请稍后重试');
    } finally {
      setSavingProfile(false);
    }
  };

  useEffect(() => {
    if (activeProfileSection === 'female' && draftProfile.gender !== '女') {
      setActiveProfileSection('basic');
    }
  }, [activeProfileSection, draftProfile.gender]);

  const setDraftField = (key: string, value: any) => {
    setDraftProfile((p: any) => ({ ...p, [key]: value }));
  };

  const toggleDraftArrayValue = (key: string, value: string) => {
    setDraftProfile((p: any) => {
      const list = splitProfileText(p[key]);
      return {
        ...p,
        [key]: list.includes(value) ? list.filter(item => item !== value) : [...list, value],
      };
    });
  };

  const updateSurgery = (index: number, patch: any) => {
    setDraftProfile((p: any) => {
      const list = Array.isArray(p.surgeries) ? [...p.surgeries] : [];
      list[index] = { ...(list[index] || { name: '', date: '' }), ...patch };
      return { ...p, surgeries: list };
    });
  };

  const removeSurgery = (index: number) => {
    setDraftProfile((p: any) => ({
      ...p,
      surgeries: (Array.isArray(p.surgeries) ? p.surgeries : []).filter((_: any, i: number) => i !== index),
    }));
  };

  const dragProfileTabs = {
    onMouseDown: (e: React.MouseEvent<HTMLDivElement>) => {
      const el = e.currentTarget;
      profileTabDragRef.current = { down: true, startX: e.clientX, scrollLeft: el.scrollLeft, moved: false };
    },
    onMouseMove: (e: React.MouseEvent<HTMLDivElement>) => {
      const drag = profileTabDragRef.current;
      if (!drag.down) return;
      const delta = e.clientX - drag.startX;
      if (Math.abs(delta) > 4) drag.moved = true;
      e.currentTarget.scrollLeft = drag.scrollLeft - delta;
    },
    onMouseUp: () => { profileTabDragRef.current.down = false; },
    onMouseLeave: () => { profileTabDragRef.current.down = false; },
  };

  const dragInsights = {
    onMouseDown: (e: React.MouseEvent<HTMLDivElement>) => {
      const el = e.currentTarget;
      insightDragRef.current = { down: true, startX: e.clientX, scrollLeft: el.scrollLeft, moved: false };
    },
    onMouseMove: (e: React.MouseEvent<HTMLDivElement>) => {
      const drag = insightDragRef.current;
      if (!drag.down) return;
      const delta = e.clientX - drag.startX;
      if (Math.abs(delta) > 4) drag.moved = true;
      e.currentTarget.scrollLeft = drag.scrollLeft - delta;
    },
    onMouseUp: () => { insightDragRef.current.down = false; },
    onMouseLeave: () => { insightDragRef.current.down = false; },
  };

  // BMI 实时计算
  const heightCm = Number(profile?.height) || 0;
  const weightKg = Number(profile?.weight) || 0;
  const bmi = (heightCm > 0 && weightKg > 0)
    ? (weightKg / Math.pow(heightCm / 100, 2)).toFixed(1)
    : '--';

  // 4 个统计卡的数据
  const dashMetric = (k: string, fb: any) =>
    (dashboard?.metrics || []).find((x: any) => x?.key === k)?.value ?? fb;

  const STATS = [
    { label: '问诊次数', value: String(dashMetric('sessions', '0')), icon: <MessageCircle size={16} />, color: T.mint500, bg: T.mint50 },
    { label: '收藏文章', value: String(dashMetric('liked_articles', '0')), icon: <Star size={16} />, color: T.sky500, bg: T.sky50 },
    { label: '健康天数', value: String(dashMetric('streak', '0')), icon: <CheckCircle size={16} />, color: T.sky500, bg: T.sky100 },
    { label: '综合评分', value: String(dashboard?.health_score ?? '--'), icon: <Activity size={16} />, color: T.rose500, bg: T.rose50 },
  ];
  const HEALTH_DATA = [
    { label: '年龄', value: profile?.age ? `${profile.age} 岁` : '未填写', icon: <User size={15} /> },
    { label: '身高', value: heightCm ? `${heightCm} cm` : '未填写', icon: <Zap size={15} /> },
    { label: '体重', value: weightKg ? `${weightKg} kg` : '未填写', icon: <Activity size={15} /> },
    { label: 'BMI', value: bmi, icon: <TrendingUp size={15} /> },
  ];
  const profileDiseases = uniqueProfileValues([
    ...splitProfileText(profile?.past_diseases_common),
    ...splitProfileText(profile?.past_diseases_custom),
    ...splitProfileText(profile?.diseases),
  ]);
  const profileAllergies = uniqueProfileValues([
    ...splitProfileText(profile?.allergies_common),
    ...splitProfileText(profile?.allergies_custom),
    ...splitProfileText(profile?.allergies),
  ]);
  const profileVaccines = uniqueProfileValues([
    ...splitProfileText(profile?.vaccines_common),
    ...splitProfileText(profile?.vaccines_custom),
  ]);
  const profileSurgeries = Array.isArray(profile?.surgeries) ? profile.surgeries.filter((s: any) => s?.name || typeof s === 'string') : [];
  const shortValue = (value: any, fallback = '未填写') => {
    const text = Array.isArray(value) ? value.filter(Boolean).join('、') : String(value || '').trim();
    return text || fallback;
  };
  const countLabel = (count: number, unit = '项') => count > 0 ? `${count}${unit}` : '无记录';
  const summaryGroups = [
    {
      key: 'basic',
      title: '身体基础',
      sub: `${profile?.gender || '性别未填'} · ${profile?.age ? `${profile.age}岁` : '年龄未填'} · BMI ${bmi}`,
      icon: <User size={16} />,
      color: T.mint500,
      editSection: 'basic',
      items: [
        { label: '性别', value: shortValue(profile?.gender) },
        { label: '年龄', value: profile?.age ? `${profile.age} 岁` : '未填写' },
        { label: '身高', value: heightCm ? `${heightCm} cm` : '未填写' },
        { label: '体重', value: weightKg ? `${weightKg} kg` : '未填写' },
        { label: 'BMI', value: bmi },
      ],
    },
    {
      key: 'life',
      title: '生活方式',
      sub: `${profile?.sleep || '睡眠未填'} · ${profile?.exercise || '运动未填'}`,
      icon: <Leaf size={16} />,
      color: T.sky500,
      editSection: 'life',
      items: [
        { label: '饮食偏好', value: shortValue(profile?.diet) },
        { label: '运动习惯', value: shortValue(profile?.exercise) },
        { label: '睡眠情况', value: shortValue(profile?.sleep) },
        { label: '吸烟史', value: shortValue(profile?.smoking) },
        { label: '饮酒史', value: shortValue(profile?.drinking) },
      ],
    },
    {
      key: 'risk',
      title: '风险档案',
      sub: `${countLabel(profileDiseases.length, '项病史')} · ${countLabel(profileAllergies.length, '项过敏')}`,
      icon: <ShieldCheck size={16} />,
      color: T.rose500,
      editSection: 'disease',
      items: [
        { label: '慢病/既往病史', value: profileDiseases.length ? profileDiseases.join('、') : '无记录' },
        { label: '过敏史', value: profileAllergies.length ? profileAllergies.join('、') : '无记录' },
        { label: '疫苗记录', value: profileVaccines.length ? profileVaccines.join('、') : '无记录' },
        { label: '手术记录', value: profileSurgeries.length ? profileSurgeries.map((s: any) => typeof s === 'string' ? s : `${s.name}${s.date ? `（${s.date}）` : ''}`).join('、') : '无记录' },
      ],
    },
    ...(profile?.gender === '女' ? [{
      key: 'female',
      title: '女性健康',
      sub: `${profile?.menstrual_volume || '月经量未填'} · ${profile?.obstetric_status || '孕产状态未填'}`,
      icon: <HeartPulse size={16} />,
      color: T.lav500,
      editSection: 'female',
      items: [
        { label: '月经量', value: shortValue(profile?.menstrual_volume) },
        { label: '痛经情况', value: shortValue(profile?.dysmenorrhea) },
        { label: '月经周期', value: profile?.menstrual_cycle ? `${profile.menstrual_cycle} 天` : '未填写' },
        { label: '孕产状态', value: shortValue(profile?.obstetric_status) },
        { label: '预产期', value: shortValue(profile?.due_date) },
        { label: '哺乳开始日期', value: shortValue(profile?.lactation_start_date) },
      ],
    }] : []),
  ];

  // 🐞 后端实际返回结构：{status, insights: [{title, content, tags}, ...]}
  // 之前误以为是 {comprehensive_assessment, risk_warning, action_plan}，导致永远加载不出。
  const insightCards: Array<{ title: string; content: string; tags: string[] }> = (() => {
    const raw = insights?.insights;
    if (!Array.isArray(raw)) return [];
    return raw.slice(0, 3).map((it: any) => ({
      title: String(it?.title ?? '').trim(),
      content: String(it?.content ?? '').trim(),
      tags: Array.isArray(it?.tags) ? it.tags.map(String) : [],
    }));
  })();
  // 三张卡片用不同 accent color 区分（综合 / 风险 / 行动）
  const INSIGHT_ACCENTS = [T.mint400, T.sky500, T.lav500];
  const stripInsightHtml = (html: string) => String(html || '')
    .replace(/<br\s*\/?>/gi, ' ')
    .replace(/<[^>]+>/g, '')
    .replace(/&nbsp;/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  const insightPreview = (content: string) => {
    const text = stripInsightHtml(content);
    return text.length > 42 ? `${text.slice(0, 42)}…` : (text || '点击查看完整洞察');
  };
  const visibleProfileSections = PROFILE_EDIT_SECTIONS.filter(s => s.key !== 'female' || draftProfile.gender === '女');
  const activeProfileIndex = Math.max(0, visibleProfileSections.findIndex(s => s.key === activeProfileSection));
  const isLastProfileSection = activeProfileIndex >= visibleProfileSections.length - 1;
  const goNextProfileSection = () => {
    const next = visibleProfileSections[Math.min(activeProfileIndex + 1, visibleProfileSections.length - 1)];
    if (next && next.key !== activeProfileSection) setActiveProfileSection(next.key);
  };
  const fieldShell: React.CSSProperties = {
    width: '100%', minWidth: 0, boxSizing: 'border-box',
    border: `1px solid ${T.slate200}`, outline: 'none', background: T.slate50,
    borderRadius: 14, color: T.slate900, fontSize: 14, fontWeight: 800,
  };
  const renderFieldLabel = (label: string, sub?: string) => (
    <div style={{ marginBottom: 7 }}>
      <div style={{ fontSize: 11, fontWeight: 800, color: T.slate600 }}>{label}</div>
      {sub && <div style={{ fontSize: 10.5, color: T.slate400, marginTop: 1 }}>{sub}</div>}
    </div>
  );
  const renderNumberField = (key: string, label: string, unit: string, placeholder: string) => (
    <label style={{ display: 'block', marginBottom: 11, minWidth: 0 }}>
      {renderFieldLabel(label)}
      <div style={{ ...fieldShell, display: 'flex', alignItems: 'center', padding: '0 12px', overflow: 'hidden' }}>
        <input
          type="number"
          inputMode="numeric"
          value={draftProfile[key] ?? ''}
          placeholder={placeholder}
          onChange={e => setDraftField(key, e.target.value)}
          style={{ flex: 1, minWidth: 0, height: 44, border: 'none', outline: 'none', background: 'transparent', color: T.slate900, fontSize: 14, fontWeight: 900 }}
        />
        <span style={{ flexShrink: 0, fontSize: 11, color: T.slate400, fontWeight: 900 }}>{unit}</span>
      </div>
    </label>
  );
  const renderOptionField = (key: string, label: string, options: string[], placeholder = '请选择') => (
    <button
      type="button"
      onClick={() => setProfileOptionSheet({ field: key, title: label, options })}
      style={{ ...fieldShell, height: 48, marginBottom: 11, padding: '0 13px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', cursor: 'pointer', textAlign: 'left' }}
    >
      <span style={{ color: draftProfile[key] ? T.slate900 : T.slate400 }}>{draftProfile[key] || placeholder}</span>
      <ChevronRight size={16} color={T.slate400} />
    </button>
  );
  const renderTextInput = (key: string, label: string, placeholder: string, type = 'text') => (
    <label style={{ display: 'block', marginBottom: 11, minWidth: 0 }}>
      {renderFieldLabel(label)}
      <input
        type={type}
        value={draftProfile[key] ?? ''}
        placeholder={placeholder}
        onChange={e => setDraftField(key, e.target.value)}
        style={{ ...fieldShell, height: 44, padding: '0 13px' }}
      />
    </label>
  );
  const renderChipGroup = (key: string, label: string, options: string[], sub?: string) => {
    const selected = splitProfileText(draftProfile[key]);
    return (
      <div style={{ marginBottom: 12 }}>
        {renderFieldLabel(label, sub)}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
          {options.map(option => {
            const active = selected.includes(option);
            return (
              <button
                key={option}
                type="button"
                onClick={() => toggleDraftArrayValue(key, option)}
                style={{
                  border: `1px solid ${active ? T.mint500 : T.slate200}`,
                  background: active ? T.mint100 : 'white',
                  color: active ? T.mint700 : T.slate600,
                  borderRadius: 999,
                  padding: '8px 11px',
                  fontSize: 12,
                  fontWeight: 800,
                  cursor: 'pointer',
                }}
              >
                {active ? '✓ ' : ''}{option}
              </button>
            );
          })}
        </div>
      </div>
    );
  };
  const renderCustomArrayInput = (key: string, label: string, placeholder: string) => (
    <label style={{ display: 'block', marginBottom: 11, minWidth: 0 }}>
      {renderFieldLabel(label)}
      <input
        value={splitProfileText(draftProfile[key]).join('、')}
        placeholder={placeholder}
        onChange={e => setDraftField(key, splitProfileText(e.target.value))}
        style={{ ...fieldShell, height: 44, padding: '0 13px', fontWeight: 700 }}
      />
    </label>
  );

  return (
    <div style={{ flex: 1, overflowY: 'auto', background: T.cream50 }} className="mobile-scroll">
      {/* Profile Header */}
      <div style={{ background: 'linear-gradient(160deg, #F2F8B8 0%, #D8F3A6 42%, #A9E8B6 76%, #76D0A3 100%)', padding: '20px 20px 28px', position: 'relative', overflow: 'hidden' }}>
        <div style={{ position: 'absolute', top: -30, right: -30, width: 150, height: 150, borderRadius: '50%', background: 'rgba(255,255,255,0.08)' }} />
        <div style={{ position: 'absolute', bottom: -20, left: -20, width: 100, height: 100, borderRadius: '50%', background: 'rgba(255,255,255,0.06)' }} />
        <div style={{ display: 'flex', alignItems: 'center', gap: 16, position: 'relative' }}>
          <div style={{ width: 72, height: 72, borderRadius: 22, background: 'rgba(255,255,255,0.48)', border: '2px solid rgba(255,255,255,0.62)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: T.g800, fontSize: 28, fontWeight: 900 }}>
            {username.charAt(0).toUpperCase()}
          </div>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 22, fontWeight: 800, color: T.g900, letterSpacing: '-0.3px' }}>{username}</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 4 }}>
              <span style={{ fontSize: 11, padding: '2px 9px', borderRadius: 8, background: 'rgba(255,255,255,0.48)', color: T.g700, fontWeight: 800 }}>🌱 健康新手</span>
              <span style={{ fontSize: 11, color: 'rgba(18,60,52,0.66)', fontWeight: 700 }}>连续打卡 14 天</span>
            </div>
          </div>
          <button
            onClick={() => openProfileEditor()}
            title="编辑健康档案"
            style={{ width: 34, height: 34, borderRadius: 10, background: 'rgba(255,255,255,0.48)', border: 'none', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
          >
            <Edit3 size={15} color={T.g700} />
          </button>
        </div>

        {/* Stats Row */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 8, marginTop: 20, background: 'rgba(255,255,255,0.28)', borderRadius: 16, padding: '12px 8px', position: 'relative' }}>
          {STATS.map(s => (
            <div key={s.label} style={{ textAlign: 'center' }}>
              <div style={{ fontSize: 20, fontWeight: 900, color: T.g900 }}>{s.value}</div>
              <div style={{ fontSize: 10, color: 'rgba(18,60,52,0.66)', marginTop: 1, fontWeight: 700 }}>{s.label}</div>
            </div>
          ))}
        </div>
      </div>

      <div style={{ padding: '14px 16px' }}>
        {/* Health Summary */}
        <div style={{ background: 'white', borderRadius: 16, padding: '14px', marginBottom: 14, border: `1px solid ${T.cream200}` }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
            <div style={{ fontSize: 13, fontWeight: 900, color: T.slate900 }}>健康摘要</div>
            <button onClick={() => openProfileEditor('basic')} style={{ border: 'none', background: '#F7FBEA', color: T.mint700, borderRadius: 999, padding: '5px 9px', fontSize: 10.5, fontWeight: 900, cursor: 'pointer' }}>
              编辑
            </button>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 9, marginBottom: 10 }}>
            {HEALTH_DATA.map(d => (
              <div key={d.label} style={{ background: '#F7FBEA', borderRadius: 12, padding: '10px 11px', border: `1px solid ${T.cream200}` }}>
                <div style={{ fontSize: 10.5, color: T.slate400, fontWeight: 700, marginBottom: 3, display: 'flex', alignItems: 'center', gap: 4 }}>
                  <span style={{ color: T.mint400 }}>{d.icon}</span>{d.label}
                </div>
                <div style={{ fontSize: 16, fontWeight: 900, color: T.slate900 }}>{d.value}</div>
              </div>
            ))}
          </div>
          <div style={{ display: 'grid', gap: 9 }}>
            {summaryGroups.map(group => (
              <button
                key={group.key}
                type="button"
                onClick={() => setProfileDetailSheet(group)}
                style={{
                  width: '100%',
                  border: `1px solid ${T.cream200}`,
                  background: 'linear-gradient(135deg, rgba(247,251,234,0.82), rgba(255,255,255,0.96))',
                  borderRadius: 14,
                  padding: '11px 12px',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 10,
                  textAlign: 'left',
                  cursor: 'pointer',
                }}
              >
                <div style={{ width: 34, height: 34, borderRadius: 12, background: `${group.color}20`, color: group.color, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                  {group.icon}
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 13, fontWeight: 900, color: T.slate900 }}>{group.title}</div>
                  <div style={{ fontSize: 11, color: T.slate500, marginTop: 2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{group.sub}</div>
                </div>
                <ChevronRight size={15} color={T.slate300} />
              </button>
            ))}
          </div>
        </div>

        {/* AI Health Insights */}
        <div style={{ background: '#EAF7C7', borderRadius: 16, border: `1px solid ${T.cream200}`, overflow: 'hidden', marginBottom: 14 }}>
          <div style={{ padding: '13px 14px 9px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <div style={{ width: 28, height: 28, borderRadius: 10, background: T.cream200, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <Brain size={14} color={T.mint500} />
              </div>
              <span style={{ fontSize: 13, fontWeight: 900, color: T.slate900 }}>AI 健康洞察</span>
            </div>
            <span style={{ fontSize: 10, color: T.slate400 }}>横滑查看</span>
          </div>
          {insightsLoading && (
            <div style={{ padding: '4px 14px 14px', color: T.slate500, fontSize: 12 }}>
              AI 正在分析您的健康档案…
            </div>
          )}
          {!insightsLoading && insightCards.length === 0 && (
            <div style={{ margin: '0 14px 14px', padding: '14px 12px', borderRadius: 14, background: 'rgba(255,255,255,0.62)', color: T.slate400, fontSize: 12, lineHeight: 1.6, textAlign: 'center' }}>
              完善健康档案后生成洞察
            </div>
          )}
          {!insightsLoading && insightCards.length > 0 && (
            <div
              {...dragInsights}
              className="mobile-scroll"
              style={{ display: 'flex', gap: 10, overflowX: 'auto', padding: '0 14px 14px', cursor: 'grab' }}
            >
              {insightCards.map((card, i) => {
                const accent = INSIGHT_ACCENTS[i % INSIGHT_ACCENTS.length];
                return (
                  <button
                    key={i}
                    type="button"
                    onClick={() => { if (!insightDragRef.current.moved) setInsightDetailSheet({ ...card, accent }); }}
                    style={{
                      flex: '0 0 220px',
                      height: 108,
                      border: `1px solid ${accent}35`,
                      borderLeft: `3px solid ${accent}`,
                      background: 'rgba(255,255,255,0.82)',
                      borderRadius: 15,
                      padding: '11px 12px',
                      textAlign: 'left',
                      cursor: 'pointer',
                      display: 'flex',
                      flexDirection: 'column',
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 6 }}>
                      <Sparkles size={14} color={accent} />
                      <span style={{ flex: 1, minWidth: 0, fontSize: 12.5, fontWeight: 900, color: T.slate900, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                        {card.title || `洞察 ${i + 1}`}
                      </span>
                    </div>
                    <div style={{ fontSize: 11.5, lineHeight: 1.55, color: T.slate600, display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                      {insightPreview(card.content)}
                    </div>
                    {card.tags.length > 0 && (
                      <div style={{ display: 'flex', gap: 5, marginTop: 'auto', overflow: 'hidden' }}>
                        {card.tags.slice(0, 2).map((tag, j) => (
                          <span key={j} style={{ flex: '0 0 auto', fontSize: 9.5, padding: '2px 7px', borderRadius: 8, background: `${accent}18`, color: accent, fontWeight: 800 }}>
                            {tag}
                          </span>
                        ))}
                      </div>
                    )}
                  </button>
                );
              })}
            </div>
          )}
        </div>

        {/* Menu */}
        <div style={{ background: 'white', borderRadius: 16, overflow: 'hidden', border: `1px solid ${T.cream200}`, marginBottom: 14 }}>
          {PROFILE_MENU.map((item, i) => (
            <button
              key={i}
              onClick={() => { if (item.action === 'edit_profile') openProfileEditor(); }}
              disabled={!item.action}
              style={{
                width: '100%', padding: '14px 16px', background: 'none', border: 'none',
                cursor: item.action ? 'pointer' : 'default', opacity: item.action ? 1 : 0.55,
                display: 'flex', alignItems: 'center', gap: 12, textAlign: 'left',
                borderBottom: i < PROFILE_MENU.length - 1 ? `1px solid ${T.cream100}` : 'none',
              }}>
              <div style={{ width: 36, height: 36, borderRadius: 10, background: T.cream50, display: 'flex', alignItems: 'center', justifyContent: 'center', color: item.color, flexShrink: 0 }}>
                {item.icon}
              </div>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: T.slate900 }}>{item.label}</div>
                <div style={{ fontSize: 11, color: T.slate400, marginTop: 1 }}>{item.sub}</div>
              </div>
              <ChevronRight size={15} color={T.slate300} />
            </button>
          ))}
        </div>

        {/* Logout — small & subtle */}
        <button onClick={onLogout} style={{
          display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
          margin: '4px auto 24px', padding: '7px 20px', borderRadius: 20,
          border: `1px solid ${T.slate300}`, background: 'transparent',
          color: T.slate500, cursor: 'pointer', fontSize: 12.5, fontWeight: 600,
        }}>
          <LogOut size={13} /> 退出登录
        </button>
      </div>

      {profileDetailSheet && (
        <div onClick={() => setProfileDetailSheet(null)} style={{ position: 'absolute', inset: 0, zIndex: 70, background: 'rgba(15,28,8,0.28)', display: 'flex', alignItems: 'flex-end' }}>
          <div onClick={e => e.stopPropagation()} style={{ width: '100%', maxHeight: '72vh', overflowY: 'auto', background: 'white', borderRadius: '24px 24px 0 0', padding: '10px 18px 20px', boxShadow: '0 -18px 46px rgba(15,28,8,0.18)' }} className="mobile-scroll">
            <div style={{ width: 38, height: 4, borderRadius: 4, background: T.slate200, margin: '0 auto 16px' }} />
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14 }}>
              <div style={{ width: 38, height: 38, borderRadius: 13, background: `${profileDetailSheet.color}20`, color: profileDetailSheet.color, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                {profileDetailSheet.icon}
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 17, fontWeight: 900, color: T.slate900 }}>{profileDetailSheet.title}</div>
                <div style={{ fontSize: 11.5, color: T.slate400, marginTop: 2 }}>{profileDetailSheet.sub}</div>
              </div>
              <button onClick={() => setProfileDetailSheet(null)} style={{ width: 34, height: 34, borderRadius: 12, border: `1px solid ${T.slate200}`, background: T.slate50, color: T.slate500, display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer' }}>
                <X size={16} />
              </button>
            </div>
            <div style={{ display: 'grid', gap: 8, marginBottom: 14 }}>
              {(profileDetailSheet.items || []).map((item: any) => (
                <div key={item.label} style={{ display: 'grid', gridTemplateColumns: '86px minmax(0, 1fr)', gap: 10, alignItems: 'start', padding: '10px 11px', borderRadius: 13, background: T.cream50, border: `1px solid ${T.cream200}` }}>
                  <div style={{ fontSize: 11, fontWeight: 800, color: T.slate400 }}>{item.label}</div>
                  <div style={{ fontSize: 12.5, fontWeight: 800, color: T.slate700, lineHeight: 1.55, wordBreak: 'break-word' }}>{item.value}</div>
                </div>
              ))}
            </div>
            <button onClick={() => openProfileEditor(profileDetailSheet.editSection || 'basic')} style={{ width: '100%', height: 46, borderRadius: 14, border: 'none', background: 'linear-gradient(135deg, #5EC99D, #2F9B7F)', color: 'white', fontSize: 14, fontWeight: 900, cursor: 'pointer' }}>
              编辑档案
            </button>
          </div>
        </div>
      )}

      {insightDetailSheet && (
        <div onClick={() => setInsightDetailSheet(null)} style={{ position: 'absolute', inset: 0, zIndex: 70, background: 'rgba(15,28,8,0.28)', display: 'flex', alignItems: 'flex-end' }}>
          <div onClick={e => e.stopPropagation()} style={{ width: '100%', maxHeight: '72vh', overflowY: 'auto', background: 'white', borderRadius: '24px 24px 0 0', padding: '10px 18px 20px', boxShadow: '0 -18px 46px rgba(15,28,8,0.18)' }} className="mobile-scroll">
            <div style={{ width: 38, height: 4, borderRadius: 4, background: T.slate200, margin: '0 auto 16px' }} />
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
              <div style={{ width: 38, height: 38, borderRadius: 13, background: `${insightDetailSheet.accent || T.mint500}20`, color: insightDetailSheet.accent || T.mint500, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <Sparkles size={17} />
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 17, fontWeight: 900, color: T.slate900 }}>{insightDetailSheet.title || 'AI 健康洞察'}</div>
                <div style={{ fontSize: 11.5, color: T.slate400, marginTop: 2 }}>基于健康档案生成</div>
              </div>
              <button onClick={() => setInsightDetailSheet(null)} style={{ width: 34, height: 34, borderRadius: 12, border: `1px solid ${T.slate200}`, background: T.slate50, color: T.slate500, display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer' }}>
                <X size={16} />
              </button>
            </div>
            <div style={{ padding: '12px 13px', borderRadius: 15, background: T.cream50, border: `1px solid ${T.cream200}`, fontSize: 13, lineHeight: 1.8, color: T.slate700 }} dangerouslySetInnerHTML={{ __html: insightDetailSheet.content || '暂无内容' }} />
            {Array.isArray(insightDetailSheet.tags) && insightDetailSheet.tags.length > 0 && (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 12 }}>
                {insightDetailSheet.tags.map((tag: string, i: number) => (
                  <span key={i} style={{ fontSize: 10.5, padding: '3px 8px', borderRadius: 9, background: `${insightDetailSheet.accent || T.mint500}18`, color: insightDetailSheet.accent || T.mint500, fontWeight: 800 }}>
                    {tag}
                  </span>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {editOpen && (
        <div style={setupMode
          ? { position: 'absolute', inset: 0, zIndex: 80, background: T.cream50, display: 'flex', alignItems: 'stretch', overflow: 'hidden' }
          : { position: 'absolute', inset: 0, zIndex: 80, background: 'rgba(15,28,8,0.34)', display: 'flex', alignItems: 'flex-end' }
        }>
          <div style={setupMode
            ? { width: '100%', height: '100%', overflow: 'hidden', background: T.cream50, borderRadius: 0, padding: 0, boxShadow: 'none', display: 'flex', flexDirection: 'column', position: 'relative' }
            : { width: '100%', maxHeight: '88vh', overflowY: 'auto', background: 'white', borderRadius: '24px 24px 0 0', padding: '10px 18px 22px', boxShadow: '0 -18px 48px rgba(15,28,8,0.18)' }
          } className="mobile-scroll">
            {setupMode ? <StatusBar /> : <div style={{ width: 38, height: 4, borderRadius: 4, background: T.slate200, margin: '0 auto 18px' }} />}
            <div style={setupMode ? { padding: '14px 18px 0', flexShrink: 0 } : undefined}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <div style={{ fontSize: 18, fontWeight: 900, color: T.slate900 }}>{setupMode ? '完善健康档案' : '编辑健康档案'}</div>
                  {setupMode && (
                    <span style={{ height: 22, padding: '0 8px', borderRadius: 999, background: 'rgba(255,255,255,0.78)', border: `1px solid ${T.slate200}`, color: T.slate500, fontSize: 10.5, fontWeight: 900, display: 'inline-flex', alignItems: 'center' }}>
                      {activeProfileIndex + 1}/{visibleProfileSections.length}
                    </span>
                  )}
                </div>
                <div style={{ fontSize: 12, color: T.slate400, marginTop: 2 }}>{setupMode ? '可先跳过，后续在“我的”页补填' : '用于问诊、洞察和个性化建议'}</div>
              </div>
              <button onClick={() => setupMode ? onSetupComplete?.() : setEditOpen(false)} style={setupMode
                ? { height: 34, borderRadius: 999, border: `1px solid ${T.slate200}`, background: 'white', padding: '0 12px', color: T.slate500, fontSize: 12, fontWeight: 900, cursor: 'pointer' }
                : { width: 34, height: 34, borderRadius: 12, border: `1px solid ${T.slate200}`, background: T.slate50, display: 'flex', alignItems: 'center', justifyContent: 'center', color: T.slate500, cursor: 'pointer' }
              }>
                {setupMode ? '稍后填写' : <X size={16} />}
              </button>
            </div>

            <div
              {...dragProfileTabs}
              style={{ display: 'flex', gap: 8, overflowX: 'auto', padding: '0 1px 10px', margin: '0 -2px 12px', cursor: 'grab' }}
              className="mobile-scroll"
            >
              {visibleProfileSections.map(section => (
                <button
                  key={section.key}
                  type="button"
                  onClick={() => { if (!profileTabDragRef.current.moved) setActiveProfileSection(section.key); }}
                  style={{
                    flex: '0 0 auto',
                    height: 34,
                    padding: '0 14px',
                    borderRadius: 999,
                    border: `1px solid ${activeProfileSection === section.key ? T.mint500 : T.slate200}`,
                    background: activeProfileSection === section.key ? T.mint100 : 'white',
                    color: activeProfileSection === section.key ? T.mint700 : T.slate600,
                    fontSize: 12,
                    fontWeight: 900,
                    cursor: 'pointer',
                  }}
                >
                  {section.label}
                </button>
              ))}
            </div>
            </div>

            <div style={setupMode
              ? { flex: 1, minHeight: 0, overflowY: 'auto', padding: '0 18px 104px' }
              : { minHeight: 278, paddingBottom: 4 }
            } className={setupMode ? 'mobile-scroll' : undefined}>
              {activeProfileSection === 'basic' && (
                <>
                  {renderNumberField('age', '年龄', '岁', '例如：22')}
                  {renderNumberField('height', '身高', 'cm', '例如：165')}
                  {renderNumberField('weight', '体重', 'kg', '例如：50')}
                  <div style={{ marginBottom: 11 }}>
                    {renderFieldLabel('性别')}
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 8 }}>
                      {MOBILE_PROFILE_OPTIONS.gender.map(option => {
                        const active = draftProfile.gender === option;
                        return (
                          <button
                            key={option}
                            type="button"
                            onClick={() => setDraftField('gender', option)}
                            style={{
                              height: 42,
                              borderRadius: 13,
                              border: `1px solid ${active ? T.mint500 : T.slate200}`,
                              background: active ? T.mint100 : T.slate50,
                              color: active ? T.mint700 : T.slate600,
                              fontSize: 13,
                              fontWeight: 900,
                              cursor: 'pointer',
                            }}
                          >
                            {option}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                </>
              )}

              {activeProfileSection === 'life' && (
                <>
                  {renderFieldLabel('饮食偏好')}
                  {renderOptionField('diet', '饮食偏好', MOBILE_PROFILE_OPTIONS.diet)}
                  {renderFieldLabel('运动习惯')}
                  {renderOptionField('exercise', '运动习惯', MOBILE_PROFILE_OPTIONS.exercise)}
                  {renderFieldLabel('睡眠情况')}
                  {renderOptionField('sleep', '睡眠情况', MOBILE_PROFILE_OPTIONS.sleep)}
                  {renderFieldLabel('吸烟史')}
                  {renderOptionField('smoking', '吸烟史', MOBILE_PROFILE_OPTIONS.smoking)}
                  {renderFieldLabel('饮酒史')}
                  {renderOptionField('drinking', '饮酒史', MOBILE_PROFILE_OPTIONS.drinking)}
                </>
              )}

              {activeProfileSection === 'female' && draftProfile.gender === '女' && (
                <>
                  {renderFieldLabel('月经量')}
                  {renderOptionField('menstrual_volume', '月经量', MOBILE_PROFILE_OPTIONS.menstrual_volume)}
                  {renderFieldLabel('痛经情况')}
                  {renderOptionField('dysmenorrhea', '痛经情况', MOBILE_PROFILE_OPTIONS.dysmenorrhea)}
                  {renderNumberField('menstrual_cycle', '月经周期', '天', '例如：28')}
                  {renderFieldLabel('孕产状态')}
                  {renderOptionField('obstetric_status', '孕产状态', MOBILE_PROFILE_OPTIONS.obstetric_status)}
                  {draftProfile.obstetric_status === '怀孕中' && renderTextInput('due_date', '预产期', '选择或输入日期', 'date')}
                  {draftProfile.obstetric_status === '哺乳期' && renderTextInput('lactation_start_date', '哺乳开始日期', '选择或输入日期', 'date')}
                </>
              )}

              {activeProfileSection === 'disease' && (
                <>
                  {renderChipGroup('past_diseases_common', '常见慢病/既往病史', COMMON_PROFILE_DISEASES, '可多选')}
                  {renderCustomArrayInput('past_diseases_custom', '自定义病史', '用顿号或逗号分隔，例如：偏头痛、胃炎')}
                </>
              )}

              {activeProfileSection === 'allergy' && (
                <>
                  {renderChipGroup('allergies_common', '常见过敏史', COMMON_PROFILE_ALLERGIES, '可多选')}
                  {renderCustomArrayInput('allergies_custom', '自定义过敏史', '用顿号或逗号分隔，例如：芒果、酒精')}
                </>
              )}

              {activeProfileSection === 'vaccine' && (
                <>
                  {renderChipGroup('vaccines_common', '疫苗记录', COMMON_PROFILE_VACCINES, '可多选')}
                  {renderCustomArrayInput('vaccines_custom', '自定义疫苗', '用顿号或逗号分隔，例如：破伤风、狂犬')}
                </>
              )}

              {activeProfileSection === 'surgery' && (
                <>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 9 }}>
                    {renderFieldLabel('手术记录', '可添加多条，日期可选')}
                    <button
                      type="button"
                      onClick={() => setDraftField('surgeries', [...(Array.isArray(draftProfile.surgeries) ? draftProfile.surgeries : []), { name: '', date: '' }])}
                      style={{ border: `1px solid ${T.mint300}`, background: T.mint100, color: T.mint700, borderRadius: 999, padding: '7px 10px', fontSize: 11, fontWeight: 900, cursor: 'pointer' }}
                    >
                      + 添加
                    </button>
                  </div>
                  {(!Array.isArray(draftProfile.surgeries) || draftProfile.surgeries.length === 0) && (
                    <div style={{ padding: '22px 14px', borderRadius: 14, border: `1px dashed ${T.slate200}`, color: T.slate400, fontSize: 12, textAlign: 'center', marginBottom: 10 }}>
                      暂无手术记录
                    </div>
                  )}
                  {(Array.isArray(draftProfile.surgeries) ? draftProfile.surgeries : []).map((item: any, index: number) => (
                    <div key={index} style={{ border: `1px solid ${T.slate200}`, borderRadius: 14, padding: 10, marginBottom: 10, background: T.slate50 }}>
                      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 116px 30px', gap: 8, alignItems: 'center' }}>
                        <input
                          value={item?.name || ''}
                          placeholder="手术名称"
                          onChange={e => updateSurgery(index, { name: e.target.value })}
                          style={{ minWidth: 0, height: 38, border: 'none', outline: 'none', background: 'white', borderRadius: 11, padding: '0 10px', color: T.slate900, fontSize: 12.5, fontWeight: 800 }}
                        />
                        <input
                          type="date"
                          value={item?.date || ''}
                          onChange={e => updateSurgery(index, { date: e.target.value })}
                          style={{ minWidth: 0, height: 38, border: 'none', outline: 'none', background: 'white', borderRadius: 11, padding: '0 8px', color: T.slate700, fontSize: 11, fontWeight: 700 }}
                        />
                        <button type="button" onClick={() => removeSurgery(index)} style={{ width: 30, height: 30, borderRadius: 10, border: 'none', background: T.red50, color: T.red500, display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer' }}>
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </div>
                  ))}
                </>
              )}
            </div>

            {profileError && (
              <div style={{ marginBottom: 12, padding: '10px 12px', borderRadius: 12, background: T.red50, border: `1px solid ${T.red200}`, color: T.red700, fontSize: 12 }}>
                {profileError}
              </div>
            )}

            <div style={setupMode
              ? { position: 'absolute', left: 0, right: 0, bottom: 0, display: 'grid', gridTemplateColumns: '1fr 1.4fr', gap: 10, padding: '12px 18px 20px', background: 'rgba(255,255,255,0.98)', borderTop: `1px solid ${T.slate100}`, boxShadow: '0 -10px 30px rgba(15,28,8,0.08)' }
              : { position: 'sticky', bottom: -22, display: 'grid', gridTemplateColumns: '1fr 1.4fr', gap: 10, margin: '4px -18px -22px', padding: '12px 18px 18px', background: 'rgba(255,255,255,0.96)', borderTop: `1px solid ${T.slate100}` }
            }>
              <button onClick={() => setupMode ? (isLastProfileSection ? onSetupComplete?.() : goNextProfileSection()) : setEditOpen(false)} disabled={savingProfile} style={{ height: 46, borderRadius: 14, border: `1px solid ${T.slate200}`, background: 'white', color: T.slate600, fontSize: 14, fontWeight: 800, cursor: savingProfile ? 'not-allowed' : 'pointer' }}>
                {setupMode ? (isLastProfileSection ? '稍后填写' : '跳过') : '取消'}
              </button>
              <button onClick={setupMode && !isLastProfileSection ? goNextProfileSection : saveProfileDraft} disabled={savingProfile} style={{ height: 46, borderRadius: 14, border: 'none', background: savingProfile ? T.slate200 : 'linear-gradient(135deg, #5EC99D, #2F9B7F)', color: savingProfile ? T.slate400 : 'white', fontSize: 14, fontWeight: 900, cursor: savingProfile ? 'not-allowed' : 'pointer' }}>
                {savingProfile ? '保存中…' : (setupMode && !isLastProfileSection ? '下一步' : '保存档案')}
              </button>
            </div>
          </div>

          {profileOptionSheet && (
            <div
              onClick={() => setProfileOptionSheet(null)}
              style={{ position: 'absolute', inset: 0, zIndex: 2, background: 'rgba(15,28,8,0.22)', display: 'flex', alignItems: 'flex-end' }}
            >
              <div
                onClick={e => e.stopPropagation()}
                style={{ width: '100%', borderRadius: '22px 22px 0 0', background: 'white', padding: '10px 16px 18px', boxShadow: '0 -18px 44px rgba(15,28,8,0.18)' }}
              >
                <div style={{ width: 36, height: 4, borderRadius: 4, background: T.slate200, margin: '0 auto 14px' }} />
                <div style={{ fontSize: 16, fontWeight: 900, color: T.slate900, marginBottom: 12 }}>{profileOptionSheet.title}</div>
                <div style={{ display: 'grid', gap: 8, marginBottom: 12 }}>
                  {profileOptionSheet.options.map(option => {
                    const active = draftProfile[profileOptionSheet.field] === option;
                    return (
                      <button
                        key={option}
                        type="button"
                        onClick={() => {
                          setDraftField(profileOptionSheet.field, option);
                          setProfileOptionSheet(null);
                        }}
                        style={{
                          height: 46,
                          borderRadius: 14,
                          border: `1px solid ${active ? T.mint500 : T.slate200}`,
                          background: active ? T.mint100 : T.slate50,
                          color: active ? T.mint700 : T.slate700,
                          fontSize: 14,
                          fontWeight: 900,
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'space-between',
                          padding: '0 14px',
                          cursor: 'pointer',
                        }}
                      >
                        <span>{option}</span>
                        {active && <CheckCircle size={17} color={T.mint600} />}
                      </button>
                    );
                  })}
                </div>
                <button
                  type="button"
                  onClick={() => setProfileOptionSheet(null)}
                  style={{ width: '100%', height: 44, borderRadius: 14, border: `1px solid ${T.slate200}`, background: 'white', color: T.slate500, fontSize: 14, fontWeight: 900, cursor: 'pointer' }}
                >
                  取消
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

// ─── SplashScreen ──────────────────────────────────────────────────
const SplashScreen: React.FC<{ onDone: () => void }> = ({ onDone }) => {
  const [phase, setPhase] = useState(0);
  useEffect(() => {
    const t1 = setTimeout(() => setPhase(1), 650);
    const t2 = setTimeout(() => setPhase(2), 1300);
    const t3 = setTimeout(() => setPhase(3), 2600);
    const t4 = setTimeout(() => onDone(), 3000);
    return () => { clearTimeout(t1); clearTimeout(t2); clearTimeout(t3); clearTimeout(t4); };
  }, [onDone]);

  return (
    <div style={{
      position: 'absolute', inset: 0, zIndex: 100,
      background: 'linear-gradient(160deg, #F2F8B8 0%, #D8F3A6 42%, #A9E8B6 76%, #76D0A3 100%)',
      display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
      overflow: 'hidden',
      opacity: phase >= 3 ? 0 : 1,
      transition: phase >= 3 ? 'opacity 0.4s ease-out' : 'none',
      pointerEvents: phase >= 3 ? 'none' : 'all',
    }}>
      {/* Decorative orbs */}
      <div style={{ position: 'absolute', top: '-8%', left: '-10%', width: 260, height: 260, borderRadius: '50%', background: 'rgba(255,255,255,0.07)' }} />
      <div style={{ position: 'absolute', bottom: '12%', right: '-6%', width: 200, height: 200, borderRadius: '50%', background: 'rgba(255,255,255,0.14)' }} />
      <div style={{ position: 'absolute', top: '45%', left: '10%', width: 120, height: 120, borderRadius: '50%', background: 'rgba(242,248,184,0.22)' }} />

      {/* Logo with ring pulse */}
      <div style={{ position: 'relative', marginBottom: 32, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <div style={{ position: 'absolute', width: 88, height: 88, borderRadius: 26, border: '2px solid rgba(255,255,255,0.44)', animation: 'splashRing 2.2s ease-out infinite', animationDelay: '0s' }} />
        <div style={{ position: 'absolute', width: 88, height: 88, borderRadius: 26, border: '2px solid rgba(255,255,255,0.28)', animation: 'splashRing 2.2s ease-out infinite', animationDelay: '1.1s' }} />
        <div style={{
          width: 88, height: 88, borderRadius: 26,
          background: 'rgba(255,255,255,0.32)',
          border: '1.5px solid rgba(255,255,255,0.4)',
          backdropFilter: 'blur(10px)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          animation: 'splashLogoIn 0.7s cubic-bezier(0.34,1.56,0.64,1) forwards',
          boxShadow: '0 10px 40px rgba(0,0,0,0.15), inset 0 1px 0 rgba(255,255,255,0.35)',
        }}>
          <Activity size={40} color={T.g700} />
        </div>
      </div>

      {/* App name */}
      <div style={{
        textAlign: 'center',
        opacity: phase >= 1 ? 1 : 0,
        transform: phase >= 1 ? 'translateY(0)' : 'translateY(20px)',
        transition: 'opacity 0.55s ease, transform 0.55s ease',
      }}>
        <div style={{ fontSize: 32, fontWeight: 900, color: T.g900, letterSpacing: '-0.5px' }}>TrustMed AI</div>
        <div style={{ fontSize: 14, color: 'rgba(18,60,52,0.70)', marginTop: 7, fontWeight: 700 }}>可信医疗问答系统</div>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 7, marginTop: 10 }}>
          {['多智能体', '实时溯源', '六大专科'].map((label, i) => (
            <span key={i} style={{ fontSize: 11, padding: '3px 9px', borderRadius: 20, background: 'rgba(255,255,255,0.50)', color: T.g700, fontWeight: 700 }}>{label}</span>
          ))}
        </div>
      </div>

      {/* Progress & dots */}
      <div style={{
        position: 'absolute', bottom: 52, left: 0, right: 0,
        display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 14,
        opacity: phase >= 2 ? 1 : 0,
        transition: 'opacity 0.4s ease',
      }}>
        <div style={{ display: 'flex', gap: 7 }}>
          {[0, 0.22, 0.44].map((d, i) => (
            <div key={i} style={{ width: 7, height: 7, borderRadius: '50%', background: 'rgba(255,255,255,0.75)', animation: 'mobilePulse 1.4s ease-in-out infinite', animationDelay: `${d}s` }} />
          ))}
        </div>
        <div style={{ width: 140, height: 2.5, background: 'rgba(255,255,255,0.24)', borderRadius: 2, overflow: 'hidden' }}>
          <div style={{ height: '100%', background: 'rgba(255,255,255,0.92)', borderRadius: 2, width: phase >= 3 ? '100%' : '68%', transition: 'width 1.4s ease-out' }} />
        </div>
        <div style={{ fontSize: 11, color: 'rgba(18,60,52,0.54)', letterSpacing: '0.6px', fontWeight: 700 }}>正在加载数据…</div>
      </div>
    </div>
  );
};

// ─── MobileLoginScreen ─────────────────────────────────────────────
type AuthMode = 'login' | 'register';

const MobileLoginScreen: React.FC<{
  initialMode: AuthMode;
  onModeChange: (mode: AuthMode) => void;
  onSuccess: () => void;
  onRegisterSuccess: () => void;
}> = ({ initialMode, onModeChange, onSuccess, onRegisterSuccess }) => {
  const [isRegister, setIsRegister] = useState(initialMode === 'register');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showPw, setShowPw] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [cardVisible, setCardVisible] = useState(false);

  useEffect(() => { const t = setTimeout(() => setCardVisible(true), 60); return () => clearTimeout(t); }, []);
  useEffect(() => {
    setIsRegister(initialMode === 'register');
    setError('');
  }, [initialMode]);

  const switchMode = (mode: AuthMode) => {
    setIsRegister(mode === 'register');
    setError('');
    onModeChange(mode);
  };

  const handleSubmit = async () => {
    const u = username.trim();
    if (!u || !password.trim()) { setError('用户名和密码不能为空'); return; }
    if (password.length < 6) { setError('密码至少 6 位'); return; }
    setError(''); setLoading(true);
    try {
      if (isRegister) {
        await api.register(u, password);
        const data = await api.login(u, password);
        localStorage.setItem('access_token', data.access_token);
        localStorage.setItem('current_username', data.username || u);
        onRegisterSuccess();
      } else {
        const data = await api.login(u, password);
        localStorage.setItem('access_token', data.access_token);
        localStorage.setItem('current_username', data.username || u);
        onSuccess();
      }
    } catch (e: any) {
      const rawMessage = String(e?.message || '');
      const isRegisterConflict = isRegister && (
        rawMessage.includes('已被注册') ||
        rawMessage.includes('already') ||
        rawMessage.includes('400') ||
        /[璇鎴戶户]/.test(rawMessage)
      );
      setError(isRegisterConflict
        ? '该用户名已被注册，请直接登录或换一个用户名'
        : (rawMessage || (isRegister ? '注册失败' : '登录失败')));
    } finally { setLoading(false); }
  };

  const now = new Date();
  const timeStr = `${now.getHours().toString().padStart(2,'0')}:${now.getMinutes().toString().padStart(2,'0')}`;

  return (
    <div style={{ position: 'absolute', inset: 0, zIndex: 50, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      {/* Top gradient banner */}
      <div style={{ background: 'linear-gradient(160deg, #F2F8B8 0%, #D8F3A6 42%, #A9E8B6 76%, #76D0A3 100%)', flexShrink: 0, padding: '0 0 40px', position: 'relative', overflow: 'hidden' }}>
        <div style={{ position: 'absolute', top: -30, right: -20, width: 160, height: 160, borderRadius: '50%', background: 'rgba(255,255,255,0.08)' }} />
        <div style={{ position: 'absolute', bottom: 0, left: -30, width: 120, height: 120, borderRadius: '50%', background: 'rgba(242,248,184,0.22)' }} />

        {/* Status bar */}
        <div style={{ height: 44, display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 22px', position: 'relative', zIndex: 2 }}>
          <span style={{ fontSize: 15, fontWeight: 800, color: T.g900 }}>{timeStr}</span>
          <div style={{ position: 'absolute', left: '50%', top: 10, transform: 'translateX(-50%)', width: 110, height: 32, background: 'rgba(0,0,0,0.45)', borderRadius: 18 }} />
          <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
            <svg width={14} height={10} viewBox="0 0 16 12">{[0,1,2,3].map(i => <rect key={i} x={i*4} y={12-(i+1)*3} width={3} height={(i+1)*3} rx={1} fill={T.g700} />)}</svg>
            <div style={{ width: 22, height: 11, border: `1.5px solid ${T.g700}`, borderRadius: 3, padding: '1px', display: 'flex', alignItems: 'center', position: 'relative' }}>
              <div style={{ width: '80%', height: '100%', background: T.g700, borderRadius: 1 }} />
              <div style={{ position: 'absolute', right: -3, top: '50%', transform: 'translateY(-50%)', width: 2, height: 4, background: T.g700 }} />
            </div>
          </div>
        </div>

        {/* Branding */}
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', paddingTop: 10, position: 'relative', zIndex: 2 }}>
          <div style={{ width: 64, height: 64, borderRadius: 20, background: 'rgba(255,255,255,0.32)', border: '1.5px solid rgba(255,255,255,0.52)', display: 'flex', alignItems: 'center', justifyContent: 'center', marginBottom: 12, boxShadow: '0 8px 24px rgba(0,0,0,0.12)' }}>
            <Activity size={28} color={T.g700} />
          </div>
          <div style={{ fontSize: 22, fontWeight: 900, color: T.g900, letterSpacing: '-0.3px' }}>TrustMed AI</div>
          <div style={{ fontSize: 12, color: 'rgba(18,60,52,0.68)', marginTop: 5, fontWeight: 700 }}>多智能体可信医疗助手</div>
        </div>
      </div>

      {/* Bottom sheet */}
      <div style={{
        flex: 1, background: 'white', borderRadius: '28px 28px 0 0',
        padding: '22px 24px 28px', marginTop: -22,
        overflowY: 'auto',
        transform: cardVisible ? 'translateY(0)' : 'translateY(100%)',
        transition: 'transform 0.52s cubic-bezier(0.32,0.72,0,1)',
        boxShadow: '0 -6px 28px rgba(0,0,0,0.08)',
      }} className="mobile-scroll">
        {/* Drag handle */}
        <div style={{ width: 38, height: 4, borderRadius: 2, background: T.slate200, margin: '0 auto 20px' }} />

        {/* Toggle tabs */}
        <div style={{ display: 'flex', background: T.slate100, borderRadius: 13, padding: 3, marginBottom: 22 }}>
          {([['login','登录'],['register','注册']] as Array<[AuthMode, string]>).map(([k,l]) => (
            <button key={k} onClick={() => switchMode(k)} style={{
              flex: 1, padding: '9px 0', borderRadius: 11, border: 'none', cursor: 'pointer',
              fontSize: 13.5, fontWeight: 700,
              background: (isRegister ? k === 'register' : k === 'login') ? 'white' : 'transparent',
              color: (isRegister ? k === 'register' : k === 'login') ? T.mint700 : T.slate400,
              boxShadow: (isRegister ? k === 'register' : k === 'login') ? '0 2px 8px rgba(0,0,0,0.08)' : 'none',
              transition: 'all 0.22s',
            }}>{l}</button>
          ))}
        </div>

        {/* Heading */}
        <div style={{ fontSize: 23, fontWeight: 900, color: T.slate900, marginBottom: 4 }}>
          {isRegister ? '创建账号 ✨' : '欢迎回来 👋'}
        </div>
        <div style={{ fontSize: 13, color: T.slate400, marginBottom: 22 }}>
          {isRegister ? '注册后即可开始健康咨询' : '登录以继续使用 TrustMed AI'}
        </div>

        {/* Username */}
        <div style={{ marginBottom: 13 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, background: T.slate50, border: `1.5px solid ${T.slate200}`, borderRadius: 15, padding: '13px 16px' }}>
            <User size={17} color={T.slate400} style={{ flexShrink: 0 }} />
            <input value={username} onChange={e => setUsername(e.target.value)} onKeyDown={e => e.key === 'Enter' && handleSubmit()} placeholder="请输入用户名" style={{ flex: 1, border: 'none', background: 'none', outline: 'none', fontSize: 15, color: T.slate900 }} />
          </div>
        </div>

        {/* Password */}
        <div style={{ marginBottom: error ? 12 : 22 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, background: T.slate50, border: `1.5px solid ${T.slate200}`, borderRadius: 15, padding: '13px 16px' }}>
            <Lock size={17} color={T.slate400} style={{ flexShrink: 0 }} />
            <input value={password} onChange={e => setPassword(e.target.value)} onKeyDown={e => e.key === 'Enter' && handleSubmit()} type={showPw ? 'text' : 'password'} placeholder="请输入密码" style={{ flex: 1, border: 'none', background: 'none', outline: 'none', fontSize: 15, color: T.slate900 }} />
            <button onClick={() => setShowPw(!showPw)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: T.slate400, display: 'flex', padding: 0, flexShrink: 0 }}>
              {showPw ? <EyeOff size={17} /> : <Eye size={17} />}
            </button>
          </div>
        </div>

        {/* Error */}
        {error && (
          <div style={{ marginBottom: 14, padding: '10px 14px', borderRadius: 12, background: error.includes('注册成功') ? T.mint50 : T.red50, border: `1px solid ${error.includes('注册成功') ? T.mint200 : T.red200}`, fontSize: 13, color: error.includes('注册成功') ? T.mint700 : T.red700, display: 'flex', alignItems: 'center', gap: 8 }}>
            <div style={{ width: 6, height: 6, borderRadius: '50%', background: error.includes('注册成功') ? T.mint500 : T.red500, flexShrink: 0 }} />
            <span style={{ flex: 1 }}>{error}</span>
            {error.includes('已被注册') && (
              <button
                type="button"
                onClick={() => switchMode('login')}
                style={{ border: 'none', background: 'rgba(255,255,255,0.75)', color: T.mint700, borderRadius: 999, padding: '5px 9px', fontSize: 11, fontWeight: 900, cursor: 'pointer', flexShrink: 0 }}
              >
                去登录
              </button>
            )}
          </div>
        )}

        {/* Submit */}
        <button onClick={handleSubmit} disabled={loading} style={{
          width: '100%', height: 54, borderRadius: 17, border: 'none',
          cursor: loading ? 'not-allowed' : 'pointer',
          background: loading ? T.slate200 : 'linear-gradient(135deg, #5EC99D 0%, #2F9B7F 100%)',
          color: loading ? T.slate400 : 'white', fontSize: 16, fontWeight: 800,
          boxShadow: loading ? 'none' : '0 8px 24px rgba(90,112,72,0.3)',
          transition: 'all 0.2s', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8,
          marginBottom: 18,
        }}>
          {loading
            ? <div style={{ width: 20, height: 20, border: `2.5px solid ${T.slate400}`, borderTopColor: 'transparent', borderRadius: '50%', animation: 'spin360 0.8s linear infinite' }} />
            : (isRegister ? '创建账号' : '立即登录')
          }
        </button>

        {/* Demo hint */}
        <div style={{ padding: '11px 14px', borderRadius: 12, background: T.mint50, border: `1px solid ${T.mint200}`, marginBottom: 14 }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: T.mint700, marginBottom: 2 }}>🔑 体验提示</div>
          <div style={{ fontSize: 12, color: T.slate500 }}>可使用任意用户名注册后登录体验全部功能</div>
        </div>

        {/* Disclaimer */}
        <div style={{ textAlign: 'center', fontSize: 11, color: T.slate300 }}>
          🔒 登录即表示同意 <span style={{ color: T.mint600, fontWeight: 600 }}>服务条款</span> 与 <span style={{ color: T.mint600, fontWeight: 600 }}>隐私政策</span>
        </div>
      </div>
    </div>
  );
};

// ─── Bottom Navigation ─────────────────────────────────────────────
const BottomNav: React.FC<{ active: Tab; onChange: (t: Tab) => void }> = ({ active, onChange }) => {
  const tabs: { id: Tab; icon: React.ReactNode; label: string }[] = [
    { id: 'home', icon: <Home size={22} />, label: '首页' },
    { id: 'chat', icon: <MessageCircle size={22} />, label: '问诊' },
    { id: 'knowledge', icon: <BookOpen size={22} />, label: '知识' },
    { id: 'profile', icon: <User size={22} />, label: '我的' },
  ];
  return (
    <div style={{
      height: 64, background: 'white', borderTop: `1px solid ${T.slate200}`,
      display: 'grid', gridTemplateColumns: 'repeat(4,1fr)',
      flexShrink: 0, paddingBottom: 4,
    }}>
      {tabs.map(t => (
        <button key={t.id} onClick={() => onChange(t.id)} style={{
          display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
          gap: 3, border: 'none', background: 'none', cursor: 'pointer', padding: '6px 0',
          color: active === t.id ? T.mint600 : T.slate400, transition: 'color 0.18s',
          position: 'relative',
        }}>
          {active === t.id && (
            <div style={{ position: 'absolute', top: 0, left: '50%', transform: 'translateX(-50%)', width: 32, height: 2, borderRadius: 2, background: T.mint500 }} />
          )}
          <div style={{ transition: 'transform 0.18s', transform: active === t.id ? 'scale(1.1)' : 'scale(1)' }}>
            {t.icon}
          </div>
          <span style={{ fontSize: 10, fontWeight: active === t.id ? 700 : 500 }}>{t.label}</span>
          {t.id === 'chat' && (
            <div style={{ position: 'absolute', top: 8, right: '22%', width: 7, height: 7, borderRadius: '50%', background: T.g600, border: '1.5px solid white' }} />
          )}
        </button>
      ))}
    </div>
  );
};

// ─── Mobile App Inner ──────────────────────────────────────────────
type AppScreen = 'splash' | 'login' | 'profileSetup' | 'main';

const MobileApp: React.FC = () => {
  const [appScreen, setAppScreen] = useState<AppScreen>('splash');
  const [authMode, setAuthMode] = useState<AuthMode>(
    window.location.pathname.includes('/register') ? 'register' : 'login'
  );
  const [activeTab, setActiveTab] = useState<Tab>('home');
  const [chatTitle, setChatTitle] = useState<string | undefined>();
  const [graphOpen, setGraphOpen] = useState(false);

  const handleSplashDone = useCallback(() => {
    const token = localStorage.getItem('access_token');
    const routeMode: AuthMode = window.location.pathname.includes('/register') ? 'register' : 'login';
    setAuthMode(routeMode);
    if (token) {
      if (window.location.pathname === '/profile-setup') {
        setAppScreen('profileSetup');
        return;
      }
      if (window.location.pathname === '/login' || window.location.pathname === '/register') {
        window.history.replaceState(null, '', '/');
      }
      setAppScreen('main');
    } else {
      if (window.location.pathname !== '/login' && window.location.pathname !== '/register') {
        window.history.replaceState(null, '', '/login');
      }
      setAppScreen('login');
    }
  }, []);

  const handleLoginSuccess = useCallback(() => {
    if (window.location.pathname === '/login' || window.location.pathname === '/register') {
      window.history.replaceState(null, '', '/');
    }
    setAppScreen('main');
  }, []);

  const handleRegisterSuccess = useCallback(() => {
    if (window.location.pathname !== '/profile-setup') {
      window.history.replaceState(null, '', '/profile-setup');
    }
    setAppScreen('profileSetup');
  }, []);

  const handleProfileSetupDone = useCallback(() => {
    if (window.location.pathname === '/profile-setup') {
      window.history.replaceState(null, '', '/');
    }
    setAppScreen('main');
  }, []);

  const handleAuthModeChange = useCallback((mode: AuthMode) => {
    setAuthMode(mode);
    const path = mode === 'register' ? '/register' : '/login';
    if (window.location.pathname !== path) {
      window.history.replaceState(null, '', path);
    }
  }, []);

  const handleLogout = useCallback(() => {
    localStorage.removeItem('access_token');
    localStorage.removeItem('current_username');
    localStorage.removeItem('mobile_session_id');
    setActiveTab('home');
    setChatTitle(undefined);
    setGraphOpen(false);
    setAuthMode('login');
    if (window.location.pathname !== '/login') {
      window.history.replaceState(null, '', '/login');
    }
    setAppScreen('login');
  }, []);

  useEffect(() => {
    const handleAuthExpired = () => {
      setActiveTab('home');
      setChatTitle(undefined);
      setGraphOpen(false);
      setAuthMode('login');
      if (window.location.pathname !== '/login') {
        window.history.replaceState(null, '', '/login');
      }
      setAppScreen('login');
    };
    window.addEventListener('mobile-auth-expired', handleAuthExpired);
    return () => window.removeEventListener('mobile-auth-expired', handleAuthExpired);
  }, []);

  useEffect(() => {
    const handleRouteMode = () => {
      if (window.location.pathname === '/profile-setup') {
        if (localStorage.getItem('access_token')) {
          setAppScreen('profileSetup');
        } else {
          setAuthMode('login');
          window.history.replaceState(null, '', '/login');
          setAppScreen('login');
        }
        return;
      }
      if (window.location.pathname === '/login' || window.location.pathname === '/register') {
        const nextMode: AuthMode = window.location.pathname === '/register' ? 'register' : 'login';
        setAuthMode(nextMode);
        if (!localStorage.getItem('access_token')) setAppScreen('login');
      }
    };
    window.addEventListener('popstate', handleRouteMode);
    return () => window.removeEventListener('popstate', handleRouteMode);
  }, []);

  const handleTabChange = (t: Tab) => { setActiveTab(t); };
  const openChatWith = (title: string) => { setChatTitle(title); setActiveTab('chat'); };

  return (
    <div style={{ width: '100%', height: '100%', display: 'flex', flexDirection: 'column', background: 'white', overflow: 'hidden', position: 'relative' }}>
      {/* ── Main app (always mounted but below overlays) ── */}
      {appScreen === 'main' && (
        <>
          <StatusBar />
          {activeTab === 'home' && <HomeScreen onTabChange={handleTabChange} onChatOpen={openChatWith} onGraphOpen={() => setGraphOpen(true)} />}
          {activeTab === 'chat' && <ChatScreen initialTitle={chatTitle} onBack={() => setActiveTab('home')} />}
          {activeTab === 'knowledge' && <KnowledgeScreen />}
          {activeTab === 'profile' && <ProfileScreen onLogout={handleLogout} />}
          <BottomNav active={activeTab} onChange={handleTabChange} />
          {graphOpen && <MobileGraphScreen onClose={() => setGraphOpen(false)} />}
        </>
      )}

      {/* ── Login overlay ── */}
      {appScreen === 'login' && <MobileLoginScreen initialMode={authMode} onModeChange={handleAuthModeChange} onSuccess={handleLoginSuccess} onRegisterSuccess={handleRegisterSuccess} />}

      {/* ── First profile setup after registration ── */}
      {appScreen === 'profileSetup' && <ProfileScreen onLogout={handleLogout} setupMode onSetupComplete={handleProfileSetupDone} />}

      {/* ── Splash overlay (on top of everything) ── */}
      {appScreen === 'splash' && <SplashScreen onDone={handleSplashDone} />}
    </div>
  );
};

// ─── Main Export — with phone frame on desktop ─────────────────────
export const MobileAppPage: React.FC = () => {
  const [isMobile, setIsMobile] = useState(window.innerWidth < 480);
  useEffect(() => {
    const h = () => setIsMobile(window.innerWidth < 480);
    window.addEventListener('resize', h); return () => window.removeEventListener('resize', h);
  }, []);

  if (isMobile) {
    return (
      <div style={{ width: '100vw', height: '100vh', overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
        <style>{`
          .mobile-scroll::-webkit-scrollbar, .checkin-editor-scroll::-webkit-scrollbar { display: none; }
          .checkin-editor-scroll { scrollbar-width: none; -ms-overflow-style: none; }
          @keyframes mobilePulse { 0%,60%,100%{transform:scale(0.7);opacity:0.4} 30%{transform:scale(1.3);opacity:1} }
          @keyframes splashLogoIn { from{transform:scale(0.3);opacity:0} to{transform:scale(1);opacity:1} }
          @keyframes splashRing { 0%{transform:scale(1);opacity:0.7} 100%{transform:scale(2.6);opacity:0} }
          @keyframes spin360 { to{transform:rotate(360deg)} }
          .rfg-spin { animation: spin360 1s linear infinite; }
          @keyframes slideInLeft { from{transform:translateX(-100%)} to{transform:translateX(0)} }
          @keyframes slideUpFade { from{transform:translateY(8px);opacity:0} to{transform:translateY(0);opacity:1} }
          /* 🆕 移动端 AI markdown 排版（紧凑，避免标题过大占满气泡） */
          .mobile-ai-md h1, .mobile-ai-md h2, .mobile-ai-md h3 { font-size: 14px; font-weight: 700; margin: 8px 0 4px; color: #1e2420; }
          .mobile-ai-md h1:first-child, .mobile-ai-md h2:first-child, .mobile-ai-md h3:first-child { margin-top: 0; }
          .mobile-ai-md h4, .mobile-ai-md h5 { font-size: 13.5px; font-weight: 700; margin: 6px 0 3px; }
          .mobile-ai-md p { margin: 4px 0; line-height: 1.7; }
          .mobile-ai-md ul, .mobile-ai-md ol { margin: 4px 0; padding-left: 20px; }
          .mobile-ai-md li { margin: 2px 0; line-height: 1.6; }
          .mobile-ai-md strong, .mobile-ai-md b { color: #10201A; font-weight: 700; }
          .mobile-ai-md code { font-size: 12px; padding: 1px 5px; border-radius: 4px; background: #edf5ef; color: #166035; word-break: break-all; }
          .mobile-ai-md pre { background: #f4fbf6; border: 1px solid #d8ead9; border-radius: 8px; padding: 8px 10px; overflow-x: auto; font-size: 12px; }
          .mobile-ai-md pre code { background: transparent; padding: 0; }
          .mobile-ai-md a { color: #228048; text-decoration: underline; word-break: break-all; }
          .mobile-ai-md blockquote { border-left: 3px solid #CFF2D8; padding: 4px 12px; margin: 6px 0; background: #F3FAEF; color: #34483E; border-radius: 0 6px 6px 0; }
          .mobile-table-scroll { max-width: 100%; overflow-x: auto; overflow-y: hidden; margin: 8px 0; border: 1px solid #CFF2D8; border-radius: 8px; background: #F3FAEF; cursor: grab; scrollbar-width: none; -ms-overflow-style: none; touch-action: pan-x; }
          .mobile-table-scroll::-webkit-scrollbar { display: none; }
          .mobile-table-scroll.dragging { cursor: grabbing; user-select: none; }
          .mobile-ai-md table { width: max-content; min-width: 100%; border-collapse: separate; border-spacing: 0; font-size: 12px; background: #F3FAEF; }
          .mobile-ai-md th, .mobile-ai-md td { border-right: 1px solid #c2d5b4; border-bottom: 1px solid #c2d5b4; padding: 5px 8px; text-align: left; background: #f4f9df; white-space: nowrap; }
          .mobile-ai-md th { background: #E7F6D4; color: #10201A; font-weight: 800; }
          .mobile-ai-md th:last-child, .mobile-ai-md td:last-child { border-right: none; }
          .mobile-ai-md tr:last-child td { border-bottom: none; }
          .mobile-ai-md hr { border: none; border-top: 1px solid #c2d5b4; margin: 10px 0; }
        `}</style>
        <MobileApp />
      </div>
    );
  }

  return (
    <div style={{
      minHeight: '100vh', width: '100vw',
      background: 'linear-gradient(135deg, #F7FBEA 0%, #EAF7C7 48%, #CDEFCB 100%)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
      padding: '30px 20px',
      position: 'relative', overflow: 'hidden',
    }}>
      {/* Bg orbs */}
      <div style={{ position: 'fixed', top: '-10%', left: '-8%', width: 500, height: 500, borderRadius: '50%', background: 'radial-gradient(circle, rgba(171,200,155,0.45) 0%, transparent 70%)', filter: 'blur(50px)', pointerEvents: 'none' }} />
      <div style={{ position: 'fixed', bottom: '-8%', right: '-5%', width: 450, height: 450, borderRadius: '50%', background: 'radial-gradient(circle, rgba(217,234,159,0.45) 0%, transparent 70%)', filter: 'blur(55px)', pointerEvents: 'none' }} />
      <div style={{ position: 'fixed', top: '40%', right: '8%', width: 300, height: 300, borderRadius: '50%', background: 'radial-gradient(circle, rgba(194,213,180,0.4) 0%, transparent 70%)', filter: 'blur(60px)', pointerEvents: 'none' }} />

      {/* Phone Frame */}
      <div style={{ position: 'relative', zIndex: 1 }}>
        {/* Side buttons */}
        <div style={{ position: 'absolute', left: -4, top: 115, width: 4, height: 30, background: '#2d2d2f', borderRadius: '3px 0 0 3px', boxShadow: '-1px 0 3px rgba(0,0,0,0.2)' }} />
        <div style={{ position: 'absolute', left: -4, top: 160, width: 4, height: 52, background: '#2d2d2f', borderRadius: '3px 0 0 3px', boxShadow: '-1px 0 3px rgba(0,0,0,0.2)' }} />
        <div style={{ position: 'absolute', left: -4, top: 224, width: 4, height: 52, background: '#2d2d2f', borderRadius: '3px 0 0 3px', boxShadow: '-1px 0 3px rgba(0,0,0,0.2)' }} />
        <div style={{ position: 'absolute', right: -4, top: 150, width: 4, height: 78, background: '#2d2d2f', borderRadius: '0 3px 3px 0', boxShadow: '1px 0 3px rgba(0,0,0,0.2)' }} />

        {/* Body */}
        <div style={{
          width: 390, height: 780,
          background: '#1a1a1c',
          borderRadius: 52,
          padding: '0',
          boxShadow: '0 40px 100px rgba(0,0,0,0.3), 0 12px 30px rgba(0,0,0,0.18), inset 0 0 0 1.5px rgba(255,255,255,0.12)',
          position: 'relative', overflow: 'hidden',
        }}>
          {/* Screen glass effect */}
          <div style={{ position: 'absolute', inset: 6, borderRadius: 47, overflow: 'hidden', background: 'white', display: 'flex', flexDirection: 'column' }}>
            <style>{`
              .mobile-scroll::-webkit-scrollbar, .checkin-editor-scroll::-webkit-scrollbar { display: none; }
              .checkin-editor-scroll { scrollbar-width: none; -ms-overflow-style: none; }
              @keyframes mobilePulse { 0%,60%,100%{transform:scale(0.7);opacity:0.4} 30%{transform:scale(1.3);opacity:1} }
              @keyframes splashLogoIn { from{transform:scale(0.3);opacity:0} to{transform:scale(1);opacity:1} }
              @keyframes splashRing { 0%{transform:scale(1);opacity:0.7} 100%{transform:scale(2.6);opacity:0} }
              @keyframes spin360 { to{transform:rotate(360deg)} }
              .rfg-spin { animation: spin360 1s linear infinite; }
              @keyframes slideInLeft { from{transform:translateX(-100%)} to{transform:translateX(0)} }
          @keyframes slideUpFade { from{transform:translateY(8px);opacity:0} to{transform:translateY(0);opacity:1} }
          /* 🆕 移动端 AI markdown 排版（紧凑，避免标题过大占满气泡） */
          .mobile-ai-md h1, .mobile-ai-md h2, .mobile-ai-md h3 { font-size: 14px; font-weight: 700; margin: 8px 0 4px; color: #1e2420; }
          .mobile-ai-md h1:first-child, .mobile-ai-md h2:first-child, .mobile-ai-md h3:first-child { margin-top: 0; }
          .mobile-ai-md h4, .mobile-ai-md h5 { font-size: 13.5px; font-weight: 700; margin: 6px 0 3px; }
          .mobile-ai-md p { margin: 4px 0; line-height: 1.7; }
          .mobile-ai-md ul, .mobile-ai-md ol { margin: 4px 0; padding-left: 20px; }
          .mobile-ai-md li { margin: 2px 0; line-height: 1.6; }
          .mobile-ai-md strong, .mobile-ai-md b { color: #10201A; font-weight: 700; }
          .mobile-ai-md code { font-size: 12px; padding: 1px 5px; border-radius: 4px; background: #edf5ef; color: #166035; word-break: break-all; }
          .mobile-ai-md pre { background: #f4fbf6; border: 1px solid #d8ead9; border-radius: 8px; padding: 8px 10px; overflow-x: auto; font-size: 12px; }
          .mobile-ai-md pre code { background: transparent; padding: 0; }
          .mobile-ai-md a { color: #228048; text-decoration: underline; word-break: break-all; }
          .mobile-ai-md blockquote { border-left: 3px solid #CFF2D8; padding: 4px 12px; margin: 6px 0; background: #F3FAEF; color: #34483E; border-radius: 0 6px 6px 0; }
          .mobile-table-scroll { max-width: 100%; overflow-x: auto; overflow-y: hidden; margin: 8px 0; border: 1px solid #CFF2D8; border-radius: 8px; background: #F3FAEF; cursor: grab; scrollbar-width: none; -ms-overflow-style: none; touch-action: pan-x; }
          .mobile-table-scroll::-webkit-scrollbar { display: none; }
          .mobile-table-scroll.dragging { cursor: grabbing; user-select: none; }
          .mobile-ai-md table { width: max-content; min-width: 100%; border-collapse: separate; border-spacing: 0; font-size: 12px; background: #F3FAEF; }
          .mobile-ai-md th, .mobile-ai-md td { border-right: 1px solid #c2d5b4; border-bottom: 1px solid #c2d5b4; padding: 5px 8px; text-align: left; background: #f4f9df; white-space: nowrap; }
          .mobile-ai-md th { background: #E7F6D4; color: #10201A; font-weight: 800; }
          .mobile-ai-md th:last-child, .mobile-ai-md td:last-child { border-right: none; }
          .mobile-ai-md tr:last-child td { border-bottom: none; }
          .mobile-ai-md hr { border: none; border-top: 1px solid #c2d5b4; margin: 10px 0; }
            `}</style>
            <MobileApp />
            {/* Bottom home indicator */}
            <div style={{ position: 'absolute', bottom: 8, left: '50%', transform: 'translateX(-50%)', width: 100, height: 4, background: 'rgba(0,0,0,0.18)', borderRadius: 4 }} />
          </div>
        </div>
      </div>

      {/* Desktop label */}
      <div style={{ position: 'fixed', bottom: 24, left: '50%', transform: 'translateX(-50%)', display: 'flex', alignItems: 'center', gap: 10, padding: '8px 20px', borderRadius: 40, background: 'rgba(234,244,204,0.85)', backdropFilter: 'blur(12px)', border: '1px solid rgba(194,213,180,0.6)', boxShadow: '0 4px 16px rgba(0,0,0,0.06)' }}>
        <div style={{ width: 8, height: 8, borderRadius: '50%', background: T.mint500 }} />
        <span style={{ fontSize: 12, color: T.slate600, fontWeight: 600 }}>TrustMed AI · 移动端预览</span>
        <span style={{ fontSize: 12, color: T.slate400 }}>390 × 780</span>
      </div>
    </div>
  );
};
