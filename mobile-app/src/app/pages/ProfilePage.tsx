import React, { useState, useEffect, useMemo } from 'react';
import { useNavigate } from 'react-router';
import { toast } from 'sonner';
import dayjs from 'dayjs';
import { RadarChart, Radar, PolarGrid, PolarAngleAxis, ResponsiveContainer } from 'recharts';
import {
  ArrowLeft, User, Activity, Shield, Heart, Pill, Edit,
  CheckCircle, AlertCircle, Clock, Coffee, Flame,
  Bot, RefreshCw, Zap, Leaf,
} from 'lucide-react';

// ─── Design Tokens ──────────────────────────────────────────────────
const T = {
  teal50:  '#edfaf2', teal100: '#d4f5df', teal200: '#afeebf',
  teal400: '#4eba78', teal500: '#32a05f',
  teal600: '#228048', teal700: '#166035', teal900: '#061e10',
  slate50:  '#f4fbf6', slate100: '#edf5ef', slate200: '#d8ead9',
  slate300: '#b8ccba', slate400: '#90a892', slate500: '#637065',
  slate600: '#465049', slate700: '#313830', slate800: '#1e2420', slate900: '#0e120f',
  red50: '#fef0f2', red500: '#e06870', red700: '#b84850',
  amber50: '#fef8e6', amber500: '#d4a840', amber600: '#a88028',
  green50: '#edfaf2', green600: '#228048',
};

// ─── Cache ──────────────────────────────────────────────────────────
const CACHE_KEY = 'ai_insights_cache_v2';
const hashProfile = (p: Record<string, unknown>) => {
  const keys = ['height','weight','age','gender','diet','exercise','sleep','smoking','drinking','allergies','allergies_common','allergies_custom','diseases','past_diseases_common','past_diseases_custom','surgeries','vaccines_common','vaccines_custom'];
  return keys.map(k => JSON.stringify(p[k] ?? '')).join('|');
};
const readCache = (userKey: string) => { try { const raw = localStorage.getItem(`${CACHE_KEY}:${userKey}`); return raw ? JSON.parse(raw) : null; } catch { return null; } };
const writeCache = (userKey: string, payload: unknown) => { try { localStorage.setItem(`${CACHE_KEY}:${userKey}`, JSON.stringify(payload)); } catch { /* ignore */ } };

// ─── Health Score Ring (SVG) ─────────────────────────────────────────
const HealthRing: React.FC<{ score: number }> = ({ score }) => {
  const r = 38, circ = 2 * Math.PI * r;
  const offset = circ - (score / 100) * circ;
  const color = score >= 80 ? T.teal500 : score >= 60 ? T.amber500 : T.red500;
  return (
    <svg width={96} height={96} style={{ transform: 'rotate(-90deg)' }}>
      <circle cx={48} cy={48} r={r} fill="none" stroke={T.slate200} strokeWidth={9} />
      <circle cx={48} cy={48} r={r} fill="none" stroke={color} strokeWidth={9}
        strokeDasharray={circ} strokeDashoffset={offset} strokeLinecap="round"
        style={{ transition: 'stroke-dashoffset 1s ease' }}
      />
      <text x={48} y={52} textAnchor="middle" fill={T.slate900} style={{ transform: 'rotate(90deg)', transformOrigin: '48px 48px', fontSize: 20, fontWeight: 800 }}>{score}</text>
      <text x={48} y={64} textAnchor="middle" fill={T.slate400} style={{ transform: 'rotate(90deg)', transformOrigin: '48px 48px', fontSize: 9, fontWeight: 600 }}>综合评分</text>
    </svg>
  );
};

// ─── Metric Box ──────────────────────────────────────────────────────
const MetricBox: React.FC<{ label: string; value: string }> = ({ label, value }) => (
  <div style={{ textAlign: 'center', padding: '14px 16px', background: T.slate50, borderRadius: 12, border: `1px solid ${T.slate200}`, minWidth: 80 }}>
    <div style={{ fontSize: 11, color: T.slate400, fontWeight: 600, marginBottom: 4 }}>{label}</div>
    <div style={{ fontSize: 20, fontWeight: 800, color: T.slate900 }}>{value}</div>
  </div>
);

// ─── Tag ─────────────────────────────────────────────────────────────
const MedTag: React.FC<{ label: string; variant?: 'teal' | 'red' | 'amber' }> = ({ label, variant = 'teal' }) => {
  const colors = {
    teal:  { bg: T.teal50,  border: T.teal200,  color: T.teal700 },
    red:   { bg: T.red50,   border: '#FECACA',   color: T.red700 },
    amber: { bg: T.amber50, border: '#FDE68A',   color: T.amber600 },
  }[variant];
  return (
    <span style={{ display: 'inline-block', padding: '3px 10px', borderRadius: 6, fontSize: 12, fontWeight: 700, background: colors.bg, border: `1px solid ${colors.border}`, color: colors.color, margin: '3px 4px 3px 0' }}>
      {label}
    </span>
  );
};

// ─── Section Card ────────────────────────────────────────────────────
const SectionCard: React.FC<{ title: string; icon: React.ReactNode; accentColor?: string; children: React.ReactNode; style?: React.CSSProperties }> =
  ({ title, icon, accentColor = T.teal600, children, style }) => (
  <div style={{ background: 'white', border: `1px solid ${T.slate200}`, borderRadius: 16, padding: '24px 28px', ...style }}>
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 20 }}>
      <div style={{ width: 4, height: 18, borderRadius: 4, background: accentColor }} />
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ color: accentColor }}>{icon}</span>
        <span style={{ fontSize: 15, fontWeight: 700, color: T.slate900 }}>{title}</span>
      </div>
    </div>
    {children}
  </div>
);

// ─── Lifestyle Row ───────────────────────────────────────────────────
const LifestyleRow: React.FC<{ icon: React.ReactNode; label: string; value: string; color: string }> = ({ icon, label, value, color }) => (
  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 14px', background: T.slate50, borderRadius: 10, border: `1px solid ${T.slate100}`, marginBottom: 8 }}>
    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
      <span style={{ color }}>{icon}</span>
      <span style={{ fontSize: 13, fontWeight: 600, color: T.slate800 }}>{label}</span>
    </div>
    <span style={{ fontSize: 12, color: T.slate600, maxWidth: 160, textAlign: 'right' }}>{value || '未填写'}</span>
  </div>
);

// ─── AI Insight Card ─────────────────────────────────────────────────
const InsightCard: React.FC<{ item: { emoji?: string; title?: string; content?: string; tags?: string[]; type?: string } }> = ({ item }) => {
  const typeColor: Record<string, string> = { warning: T.amber600, risk: T.red700, tip: T.teal600, positive: T.green600 };
  const accent = typeColor[item.type ?? 'tip'] ?? T.teal600;
  return (
    <div style={{ background: 'white', border: `1px solid ${T.slate200}`, borderRadius: 14, padding: '18px 20px', borderLeft: `3px solid ${accent}`, transition: 'transform 0.2s, box-shadow 0.2s' }}
      onMouseEnter={e => { (e.currentTarget as HTMLDivElement).style.transform = 'translateY(-2px)'; (e.currentTarget as HTMLDivElement).style.boxShadow = '0 8px 20px rgba(0,0,0,0.07)'; }}
      onMouseLeave={e => { (e.currentTarget as HTMLDivElement).style.transform = 'none'; (e.currentTarget as HTMLDivElement).style.boxShadow = 'none'; }}
    >
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
        {item.emoji && <span style={{ fontSize: 20, flexShrink: 0, marginTop: 1 }}>{item.emoji}</span>}
        <div style={{ flex: 1 }}>
          {item.title && <div style={{ fontSize: 14, fontWeight: 700, color: T.slate900, marginBottom: 6 }}>{item.title}</div>}
          {item.content && <div style={{ fontSize: 13, color: T.slate600, lineHeight: 1.65 }}>{item.content}</div>}
          {item.tags && item.tags.length > 0 && (
            <div style={{ marginTop: 10, display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {item.tags.map((tag: string, i: number) => (
                <span key={i} style={{ fontSize: 11, padding: '2px 8px', borderRadius: 20, background: `${accent}14`, color: accent, fontWeight: 700, border: `1px solid ${accent}22` }}>{tag}</span>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

// ─── Main Component ──────────────────────────────────────────────────
export const ProfilePage: React.FC = () => {
  const navigate = useNavigate();
  const [profile, setProfile] = useState<Record<string, unknown> | null>(null);
  const [aiGenerating, setAiGenerating] = useState(false);
  const [aiRefreshing, setAiRefreshing] = useState(false);
  const [dynamicAIInsights, setDynamicAIInsights] = useState<unknown[]>([]);
  const [insightsTimestamp, setInsightsTimestamp] = useState<number | null>(null);

  const userKey = useMemo(() => localStorage.getItem('current_username') || 'anon', []);

  useEffect(() => {
    const fetchProfile = async () => {
      const token = localStorage.getItem('access_token');
      if (!token) { toast.warning('请重新登录'); navigate('/login'); return; }
      try {
        const res = await fetch('http://localhost:8000/api/profile', { method: 'GET', headers: { 'Authorization': `Bearer ${token}` } });
        if (res.status === 401) { navigate('/login'); return; }
        const data = await res.json();
        let parsed: Record<string, unknown> = {};
        if (data?.profile_data) {
          parsed = typeof data.profile_data === 'string' ? (() => { try { return JSON.parse(data.profile_data); } catch { return {}; } })() : data.profile_data;
        }
        setProfile(parsed);
        const cache = readCache(userKey);
        const currentHash = hashProfile(parsed);
        const hasFresh = cache && cache.hash === currentHash && Array.isArray(cache.insights) && cache.insights.length > 0;
        if (hasFresh) {
          setDynamicAIInsights(cache.insights); setInsightsTimestamp(cache.ts); setAiGenerating(false);
          if (Date.now() - cache.ts > 6 * 3600 * 1000) fetchRealAIInsights(token, currentHash, true);
        } else {
          setAiGenerating(true); fetchRealAIInsights(token, currentHash, false);
        }
      } catch { toast.error('无法连接到服务器'); }
    };
    fetchProfile();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const fetchRealAIInsights = async (token: string, profileHash: string, silent: boolean) => {
    if (silent) setAiRefreshing(true);
    try {
      const res = await fetch('http://localhost:8000/api/profile/ai-insights', { headers: { 'Authorization': `Bearer ${token}` } });
      const data = await res.json();
      if (data.status === 'success' && data.insights) {
        setDynamicAIInsights(data.insights);
        const ts = Date.now(); setInsightsTimestamp(ts);
        writeCache(userKey, { hash: profileHash, ts, insights: data.insights });
      } else if (!silent) { toast.error('AI 洞察生成异常'); }
    } catch { /* ignore */ }
    finally { setAiGenerating(false); setAiRefreshing(false); }
  };

  const handleManualRefresh = () => {
    const token = localStorage.getItem('access_token');
    if (!token || !profile) return;
    setAiRefreshing(true);
    fetchRealAIInsights(token, hashProfile(profile), true);
  };

  if (!profile) {
    return (
      <div style={{ height: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column', gap: 16, background: '#FAFFF4' }}>
        <div style={{ width: 40, height: 40, border: `3px solid ${T.slate200}`, borderTopColor: T.teal500, borderRadius: '50%', animation: 'spin360 0.8s linear infinite' }} />
        <span style={{ color: T.slate500, fontSize: 14 }}>正在唤醒健康数字孪生…</span>
        <style>{`@keyframes spin360 { to { transform:rotate(360deg); } }`}</style>
      </div>
    );
  }

  const { name, gender, age, height, weight, diet, exercise, sleep, smoking, drinking, allergies_common, allergies_custom, allergies, past_diseases_common, past_diseases_custom, diseases, surgeries, vaccines_common, vaccines_custom } = profile as Record<string, unknown>;

  const safeAllergies = [...new Set([...((allergies_common as string[]) || []), ...((allergies_custom as string[]) || []), ...((allergies as string[]) || [])])];
  const safeChronicDiseases = [...new Set([...((past_diseases_common as string[]) || []), ...((past_diseases_custom as string[]) || []), ...((diseases as string[]) || [])])];
  const safeVaccines = [...new Set([...((vaccines_common as string[]) || []), ...((vaccines_custom as string[]) || [])])];
  const safeSurgeries = ((surgeries as unknown[]) || []).map((s: unknown) => typeof s === 'string' ? { name: [s], date: null } : s as { name: string[]; date: string | null });
  const safeName = (name as string) || localStorage.getItem('current_username') || '探索者';

  // BMI
  const h = height as number, w = weight as number;
  let bmiVal = 0, bmiStatus = '未填', bmiColor = T.slate400, bmiText = '补充身高体重，解锁体脂评估', pointerPos = 50;
  let healthScore = 75;
  if (h && w) {
    bmiVal = parseFloat((w / ((h / 100) ** 2)).toFixed(1));
    pointerPos = Math.max(0, Math.min(100, ((bmiVal - 15) / 20) * 100));
    if (bmiVal < 18.5)      { bmiStatus = '偏瘦'; bmiColor = '#60A5FA'; bmiText = '体重偏轻，建议适当增加蛋白质摄入。'; healthScore -= 5; }
    else if (bmiVal < 24)   { bmiStatus = '正常'; bmiColor = T.teal500; bmiText = '体型完美，请继续保持优秀的自律！'; healthScore += 10; }
    else if (bmiVal < 28)   { bmiStatus = '微超'; bmiColor = T.amber500; bmiText = '体脂微超，建议有氧运动与力量训练结合。'; healthScore -= 5; }
    else                    { bmiStatus = '偏胖'; bmiColor = T.red500; bmiText = '内脏脂肪风险较高，注意代谢健康。'; healthScore -= 15; }
  }
  const scoreMap: Record<string, Record<string, number>> = {
    diet: { '荤素搭配': 90, '偏爱素食': 80, '偏爱肉食': 60, '重口味(嗜咸/嗜甜)': 40 },
    exercise: { '每周3次以上': 95, '每周1-2次': 75, '偶尔运动': 50, '几乎不运动': 20 },
    sleep: { '规律且充足': 90, '偶尔熬夜/失眠': 60, '经常熬夜/失眠': 30 },
    smoking: { '不吸烟': 100, '偶尔吸烟': 40, '长期吸烟': 10 },
    drinking: { '不饮酒': 100, '偶尔饮酒': 70, '经常饮酒': 30 },
  };
  const radarData = [
    { subject: '饮食', score: scoreMap.diet[diet as string] ?? 50 },
    { subject: '运动', score: scoreMap.exercise[exercise as string] ?? 50 },
    { subject: '睡眠', score: scoreMap.sleep[sleep as string] ?? 50 },
    { subject: '戒烟', score: scoreMap.smoking[smoking as string] ?? 50 },
    { subject: '戒酒', score: scoreMap.drinking[drinking as string] ?? 50 },
  ];
  const avgLife = radarData.reduce((a, c) => a + c.score, 0) / 5;
  healthScore = Math.min(100, Math.max(0, Math.floor(healthScore + (avgLife - 50) * 0.4)));
  if (safeChronicDiseases.length > 0) healthScore -= 10;
  if (safeAllergies.length > 0) healthScore -= 5;

  return (
    <>
      <style>{`@keyframes spin360 { to { transform:rotate(360deg); } }`}</style>
      <div style={{ minHeight: '100vh', background: '#f4fbf6', fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif' }}>

        {/* ── Header ── */}
        <div style={{ background: 'rgba(255,255,255,0.85)', backdropFilter: 'blur(10px)', borderBottom: `1px solid ${T.slate200}`, position: 'sticky', top: 0, zIndex: 10 }}>
          <div style={{ maxWidth: 1200, margin: '0 auto', padding: '16px 32px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
              <button onClick={() => navigate('/chat')} style={{ width: 38, height: 38, borderRadius: 10, background: T.slate100, border: 'none', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', color: T.slate600 }}>
                <ArrowLeft size={18} />
              </button>
              <div>
                <div style={{ fontSize: 17, fontWeight: 800, color: T.slate900 }}>全维健康看板</div>
                <div style={{ fontSize: 12, color: T.teal600, fontWeight: 600 }}>Digital Health Twin Dashboard</div>
              </div>
            </div>
            <button onClick={() => navigate('/onboarding')} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '9px 18px', borderRadius: 10, background: `linear-gradient(135deg, ${T.teal500}, ${T.teal700})`, color: 'white', border: 'none', cursor: 'pointer', fontSize: 13, fontWeight: 700, boxShadow: '0 4px 12px rgba(77,110,77,0.25)' }}>
              <Edit size={14} /> 修改档案
            </button>
          </div>
        </div>

        <div style={{ maxWidth: 1200, margin: '0 auto', padding: '28px 32px' }}>

          {/* ── AI Alert Banner ── */}
          {(safeAllergies.length + safeChronicDiseases.length) > 0 && (
            <div style={{ marginBottom: 24, padding: '14px 20px', background: T.teal50, border: `1px solid ${T.teal200}`, borderRadius: 14, display: 'flex', alignItems: 'flex-start', gap: 12 }}>
              <Bot size={18} color={T.teal600} style={{ flexShrink: 0, marginTop: 1 }} />
              <div>
                <div style={{ fontSize: 14, fontWeight: 700, color: T.slate900, marginBottom: 3 }}>数字扫描引擎已激活</div>
                <div style={{ fontSize: 13, color: T.slate600 }}>
                  数字孪生扫描完毕：发现 {safeAllergies.length + safeChronicDiseases.length} 项医疗史红线与冲突风险，已为您自动挂载诊疗室监控。
                </div>
              </div>
            </div>
          )}

          {/* ── Hero Card ── */}
          <div style={{ background: 'white', border: `1px solid ${T.slate200}`, borderRadius: 20, padding: '28px 32px', marginBottom: 24, display: 'flex', alignItems: 'center', gap: 32, flexWrap: 'wrap' }}>
            {/* Avatar + name */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 18 }}>
              <div style={{ width: 64, height: 64, borderRadius: 18, background: `linear-gradient(135deg, ${T.teal400}, ${T.teal700})`, display: 'flex', alignItems: 'center', justifyContent: 'center', boxShadow: '0 8px 20px rgba(77,110,77,0.2)' }}>
                <User size={28} color="white" />
              </div>
              <div>
                <div style={{ fontSize: 20, fontWeight: 800, color: T.slate900 }}>{safeName}</div>
                <span style={{ fontSize: 12, fontWeight: 700, color: T.teal700, background: T.teal50, border: `1px solid ${T.teal200}`, borderRadius: 6, padding: '2px 8px' }}>
                  {(gender as string) || '未知'}
                </span>
              </div>
            </div>

            {/* Divider */}
            <div style={{ width: 1, height: 60, background: T.slate200 }} />

            {/* Metrics */}
            <div style={{ display: 'flex', gap: 12 }}>
              <MetricBox label="年龄" value={(age as number) ? `${age}岁` : '--'} />
              <MetricBox label="身高" value={(height as number) ? `${height}cm` : '--'} />
              <MetricBox label="体重" value={(weight as number) ? `${weight}kg` : '--'} />
            </div>

            {/* Divider */}
            <div style={{ width: 1, height: 60, background: T.slate200 }} />

            {/* BMI */}
            <div style={{ flex: 1, minWidth: 200 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 8 }}>
                <div style={{ textAlign: 'center', minWidth: 56 }}>
                  <div style={{ fontSize: 26, fontWeight: 800, color: bmiColor, lineHeight: 1 }}>{h && w ? bmiVal : '--'}</div>
                  <div style={{ fontSize: 11, fontWeight: 700, color: bmiColor, marginTop: 2 }}>{bmiStatus}</div>
                </div>
                <div style={{ flex: 1 }}>
                  <div style={{ position: 'relative', height: 8, background: `linear-gradient(90deg, #60A5FA 0%, ${T.teal500} 30%, ${T.amber500} 65%, ${T.red500} 100%)`, borderRadius: 4, marginBottom: 6 }}>
                    {h && w && (
                      <div style={{ position: 'absolute', top: -4, left: `calc(${pointerPos}% - 8px)`, width: 16, height: 16, background: 'white', border: `3px solid ${bmiColor}`, borderRadius: '50%', boxShadow: '0 2px 6px rgba(0,0,0,0.15)' }} />
                    )}
                  </div>
                  <div style={{ fontSize: 12, color: T.slate600, display: 'flex', alignItems: 'center', gap: 5 }}>
                    <Pill size={12} color={bmiColor} /> {bmiText}
                  </div>
                </div>
              </div>
            </div>

            {/* Health Score */}
            <div style={{ flexShrink: 0 }}>
              <HealthRing score={healthScore} />
            </div>
          </div>

          {/* ── Two Column Grid ── */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 380px', gap: 24, marginBottom: 24 }}>

            {/* Clinical History */}
            <SectionCard title="临床医学史看板" icon={<Shield size={15} />}>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 16 }}>
                {/* Allergies */}
                <div style={{ padding: '16px', background: T.slate50, borderRadius: 12, border: `1px solid ${T.slate200}` }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
                    <AlertCircle size={14} color={T.amber600} />
                    <span style={{ fontSize: 13, fontWeight: 700, color: T.slate800 }}>过敏与用药禁忌</span>
                  </div>
                  {safeAllergies.length === 0 ? (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: T.slate500 }}>
                      <CheckCircle size={13} color={T.teal500} /> 未发现临床禁忌
                    </div>
                  ) : (
                    <div>{safeAllergies.map((a, i) => <MedTag key={i} label={`${a} 阳性`} variant="red" />)}</div>
                  )}
                </div>

                {/* Chronic Diseases */}
                <div style={{ padding: '16px', background: T.slate50, borderRadius: 12, border: `1px solid ${T.slate200}` }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
                    <Heart size={14} color={T.red500} />
                    <span style={{ fontSize: 13, fontWeight: 700, color: T.slate800 }}>确诊疾病与病史</span>
                  </div>
                  {safeChronicDiseases.length === 0 ? (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: T.slate500 }}>
                      <CheckCircle size={13} color={T.teal500} /> 无重大疾病史
                    </div>
                  ) : (
                    <div>{safeChronicDiseases.map((d, i) => <MedTag key={i} label={d} variant="amber" />)}</div>
                  )}
                </div>
              </div>

              {/* Vaccines + Surgeries */}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                <div style={{ padding: '16px', background: T.slate50, borderRadius: 12, border: `1px solid ${T.slate200}` }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
                    <Shield size={13} color={T.teal600} />
                    <span style={{ fontSize: 12, fontWeight: 700, color: T.slate500, textTransform: 'uppercase', letterSpacing: '0.4px' }}>免疫接种</span>
                  </div>
                  {safeVaccines.length === 0
                    ? <span style={{ fontSize: 13, color: T.slate400 }}>无接种记录</span>
                    : <div>{safeVaccines.map((v, i) => <MedTag key={i} label={v} />)}</div>
                  }
                </div>
                <div style={{ padding: '16px', background: T.slate50, borderRadius: 12, border: `1px solid ${T.slate200}` }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
                    <Activity size={13} color={T.teal600} />
                    <span style={{ fontSize: 12, fontWeight: 700, color: T.slate500, textTransform: 'uppercase', letterSpacing: '0.4px' }}>手术记录</span>
                  </div>
                  {safeSurgeries.length === 0
                    ? <span style={{ fontSize: 13, color: T.slate400 }}>无手术史</span>
                    : (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                        {safeSurgeries.map((s, i) => (
                          <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                            <div style={{ width: 6, height: 6, borderRadius: '50%', background: T.teal500, flexShrink: 0 }} />
                            <div>
                              <div style={{ fontSize: 13, fontWeight: 600, color: T.slate800 }}>{s.name?.[0] ?? '未知手术'}</div>
                              {s.date && <div style={{ fontSize: 11, color: T.slate400 }}>{dayjs(s.date).format('YYYY-MM-DD')}</div>}
                            </div>
                          </div>
                        ))}
                      </div>
                    )
                  }
                </div>
              </div>
            </SectionCard>

            {/* Lifestyle */}
            <SectionCard title="生活方式" icon={<Leaf size={15} />} accentColor={T.teal600}>
              {/* Radar */}
              <div style={{ height: 200, marginBottom: 16 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <RadarChart data={radarData} cx="50%" cy="50%" outerRadius="75%">
                    <PolarGrid stroke={T.slate200} />
                    <PolarAngleAxis dataKey="subject" tick={{ fill: T.slate500, fontSize: 11, fontWeight: 600 }} />
                    <Radar name="health" dataKey="score" stroke={T.teal600} fill={T.teal600} fillOpacity={0.15} strokeWidth={2} dot={{ fill: T.teal600, r: 3 }} />
                  </RadarChart>
                </ResponsiveContainer>
              </div>
              <div>
                <LifestyleRow icon={<Coffee size={14} />} label="饮食习惯" value={(diet as string) || ''} color={T.teal600} />
                <LifestyleRow icon={<Activity size={14} />} label="运动频次" value={(exercise as string) || ''} color={T.teal600} />
                <LifestyleRow icon={<Clock size={14} />} label="睡眠节律" value={(sleep as string) || ''} color={T.teal600} />
                <LifestyleRow icon={<Flame size={14} />} label="烟酒习惯" value={`烟: ${(smoking as string) ?? '-'} | 酒: ${(drinking as string) ?? '-'}`} color={T.amber600} />
              </div>
            </SectionCard>
          </div>

          {/* ── AI Insights ── */}
          <SectionCard
            title="AI 综合健康洞察"
            icon={<Bot size={15} />}
            accentColor={T.teal600}
            style={{ borderRadius: 20 }}
          >
            {/* Header row */}
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20, marginTop: -8 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                {aiRefreshing && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: T.teal600 }}>
                    <div style={{ width: 12, height: 12, border: `2px solid ${T.teal200}`, borderTopColor: T.teal500, borderRadius: '50%', animation: 'spin360 0.8s linear infinite' }} />
                    后台更新中…
                  </div>
                )}
                {insightsTimestamp && !aiRefreshing && (
                  <span style={{ fontSize: 11, color: T.slate400, background: T.slate100, padding: '2px 8px', borderRadius: 20 }}>
                    {dayjs(insightsTimestamp).format('MM-DD HH:mm')} 生成
                  </span>
                )}
              </div>
              <button onClick={handleManualRefresh} disabled={aiRefreshing} style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 12px', borderRadius: 8, border: `1px solid ${T.slate200}`, background: 'white', cursor: aiRefreshing ? 'not-allowed' : 'pointer', color: T.slate600, fontSize: 12, fontWeight: 600 }}>
                <RefreshCw size={12} /> 刷新洞察
              </button>
            </div>

            {aiGenerating ? (
              <div style={{ textAlign: 'center', padding: '40px 0', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 14 }}>
                <div style={{ width: 52, height: 52, borderRadius: 14, background: T.teal50, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <Bot size={26} color={T.teal600} />
                </div>
                <div style={{ fontSize: 14, fontWeight: 600, color: T.slate700 }}>AI 正在分析您的健康档案…</div>
                <div style={{ fontSize: 12, color: T.slate400 }}>这通常需要 10-20 秒，请稍候</div>
                <div style={{ display: 'flex', gap: 5 }}>
                  {[0, 0.2, 0.4].map((d, i) => (
                    <div key={i} style={{ width: 7, height: 7, borderRadius: '50%', background: T.teal400, animation: 'thinkPulse 1.4s ease-in-out infinite', animationDelay: `${d}s` }} />
                  ))}
                </div>
              </div>
            ) : dynamicAIInsights.length > 0 ? (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 14 }}>
                {dynamicAIInsights.map((item, i) => (
                  <InsightCard key={i} item={item as { emoji?: string; title?: string; content?: string; tags?: string[]; type?: string }} />
                ))}
              </div>
            ) : (
              <div style={{ textAlign: 'center', padding: '32px 0' }}>
                <Zap size={32} color={T.slate300} style={{ marginBottom: 10 }} />
                <div style={{ fontSize: 14, color: T.slate400 }}>暂无 AI 洞察，点击「刷新洞察」获取</div>
              </div>
            )}

            <style>{`@keyframes thinkPulse { 0%,60%,100% { transform:scale(0.7); opacity:0.4; } 30% { transform:scale(1.3); opacity:1; } }`}</style>
          </SectionCard>
        </div>
      </div>
    </>
  );
};