import React, { useEffect, useState, useMemo } from 'react';
import {
  Button, Typography, Row, Col, Tag,
  ConfigProvider, Space, Timeline, message, Spin, Progress, Alert
} from 'antd';
import {
  ArrowLeftOutlined, UserOutlined,
  HeartOutlined, MedicineBoxOutlined, SafetyCertificateOutlined,
  RobotOutlined, CheckCircleFilled,
  FireOutlined, RestOutlined, CoffeeOutlined, ClockCircleOutlined, EditOutlined,
  BulbOutlined, ReloadOutlined
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import dayjs from 'dayjs';
import { Radar } from '@ant-design/charts';

const { Title, Text } = Typography;

// ============================================================
// 🎨 Login 同源黄绿配色（与 Grainient 渐变背景对齐）
// ============================================================
const PALETTE = {
  // 主品牌色（Login Threads / Primary）
  teal:        '#14B8A6',
  tealDeep:    '#0F766E',
  tealSoft:    '#5EEAD4',
  tealGhost:   'rgba(20, 184, 166, 0.10)',
  // Login Grainient 三色
  yellowGreen: '#afeebf',
  cream:       '#f0eac1',
  mint:        '#e0f5ee',
  // 文字
  textInk:     '#0F172A',
  textSlate:   '#334155',
  textMute:    '#64748B',
  // 中性 / 边框
  hairline:    'rgba(15, 118, 110, 0.10)',
  glass:       'rgba(255, 255, 255, 0.72)',
  glassThick:  'rgba(255, 255, 255, 0.85)',
  // 警示 / 状态
  amber:       '#F59E0B',
  amberSoft:   'rgba(245, 158, 11, 0.12)',
  rose:        '#F87171',
};

// ============================================================
// 💾 AI 洞察缓存（stale-while-revalidate）
// ============================================================
const CACHE_KEY = 'ai_insights_cache_v2';

// 简易 hash：profile 关键字段的稳定指纹
const hashProfile = (p) => {
  if (!p) return '';
  const keys = ['height','weight','age','gender','diet','exercise','sleep','smoking','drinking',
                'allergies','allergies_common','allergies_custom',
                'diseases','past_diseases_common','past_diseases_custom',
                'surgeries','vaccines_common','vaccines_custom'];
  return keys.map(k => JSON.stringify(p[k] ?? '')).join('|');
};

const readCache = (userKey) => {
  try {
    const raw = localStorage.getItem(`${CACHE_KEY}:${userKey}`);
    if (!raw) return null;
    return JSON.parse(raw);
  } catch { return null; }
};

const writeCache = (userKey, payload) => {
  try {
    localStorage.setItem(`${CACHE_KEY}:${userKey}`, JSON.stringify(payload));
  } catch { /* ignore quota */ }
};

const Profile = () => {
  const navigate = useNavigate();
  const [profile, setProfile] = useState(null);
  const [aiGenerating, setAiGenerating] = useState(false);
  const [aiRefreshing, setAiRefreshing] = useState(false);
  const [dynamicAIInsights, setDynamicAIInsights] = useState([]);
  const [insightsTimestamp, setInsightsTimestamp] = useState(null);

  const userKey = useMemo(() => localStorage.getItem('current_username') || 'anon', []);

  useEffect(() => {
    const fetchProfile = async () => {
      const token = localStorage.getItem('access_token');
      if (!token) {
        message.warning('登录已过期，请重新登录');
        navigate('/login');
        return;
      }

      try {
        const response = await fetch('http://localhost:8000/api/profile', {
          method: 'GET',
          headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' }
        });

        if (response.status === 401) { navigate('/login'); return; }

        const data = await response.json();
        let parsedData = {};
        if (data && data.profile_data) {
          parsedData = typeof data.profile_data === 'string'
            ? (() => { try { return JSON.parse(data.profile_data); } catch { return {}; } })()
            : data.profile_data;
        }
        setProfile(parsedData);

        // 🌟 stale-while-revalidate：先吃缓存，再后台拉新
        const cache = readCache(userKey);
        const currentHash = hashProfile(parsedData);
        const hasFreshCache = cache && cache.hash === currentHash && Array.isArray(cache.insights) && cache.insights.length > 0;

        if (hasFreshCache) {
          setDynamicAIInsights(cache.insights);
          setInsightsTimestamp(cache.ts);
          setAiGenerating(false);
          // 后台静默刷新（仅当缓存超过 6 小时）
          const sixHours = 6 * 60 * 60 * 1000;
          if (Date.now() - cache.ts > sixHours) {
            fetchRealAIInsights(token, currentHash, /*silent*/ true);
          }
        } else {
          // 没缓存或档案已变 → 拉新
          setAiGenerating(true);
          fetchRealAIInsights(token, currentHash, false);
        }

      } catch (error) {
        console.error('获取档案失败', error);
        message.error('无法连接到服务器');
      }
    };
    fetchProfile();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [navigate]);

  const fetchRealAIInsights = async (token, profileHash, silent = false) => {
    if (silent) setAiRefreshing(true);
    try {
      const res = await fetch('http://localhost:8000/api/profile/ai-insights', {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      const data = await res.json();
      if (data.status === 'success' && data.insights) {
        setDynamicAIInsights(data.insights);
        const ts = Date.now();
        setInsightsTimestamp(ts);
        writeCache(userKey, { hash: profileHash, ts, insights: data.insights });
      } else if (!silent) {
        message.error('AI 洞察生成异常');
      }
    } catch (e) {
      console.error("AI 洞察请求失败", e);
    } finally {
      setAiGenerating(false);
      setAiRefreshing(false);
    }
  };

  const handleManualRefresh = () => {
    const token = localStorage.getItem('access_token');
    if (!token || !profile) return;
    setAiRefreshing(true);
    fetchRealAIInsights(token, hashProfile(profile), true);
  };

  if (!profile) {
    return (
      <div style={{
        height: '100vh', display: 'flex', justifyContent: 'center', alignItems: 'center',
        background: `linear-gradient(135deg, ${PALETTE.yellowGreen} 0%, ${PALETTE.cream} 50%, ${PALETTE.mint} 100%)`,
      }}>
        <Spin size="large" tip="正在唤醒您的健康孪生晶体..." />
      </div>
    );
  }

  const {
    name, gender, age, height, weight,
    diet, exercise, sleep, smoking, drinking,
    allergies_common, allergies_custom, allergies,
    past_diseases_common, past_diseases_custom, diseases,
    surgeries,
    vaccines_common, vaccines_custom
  } = profile;

  const safeAllergies = [...new Set([...(allergies_common || []), ...(allergies_custom || []), ...(allergies || [])])];
  const safeChronicDiseases = [...new Set([...(past_diseases_common || []), ...(past_diseases_custom || []), ...(diseases || [])])];
  const safeVaccines = [...new Set([...(vaccines_common || []), ...(vaccines_custom || [])])];
  const safeSurgeries = (surgeries || []).map(surg => typeof surg === 'string' ? { name: [surg], date: null } : surg);

  const safeHeight = height || null;
  const safeWeight = weight || null;
  const safeName = name || localStorage.getItem('current_username') || '探索者';
  const safeGender = gender || '未知';
  const safeAge = age || null;

  let bmiStatus = '未填', bmiColor = PALETTE.textMute, bmiText = '补充身高体重，解锁体脂评估', bmiValStr = '--', pointerPosition = 0;
  let healthScore = 75;

  if (safeHeight && safeWeight) {
    const heightM = safeHeight / 100;
    const bmiVal = (safeWeight / (heightM * heightM)).toFixed(1);
    bmiValStr = bmiVal;

    const minBmi = 15, maxBmi = 35;
    pointerPosition = Math.max(0, Math.min(100, ((bmiVal - minBmi) / (maxBmi - minBmi)) * 100));

    if (bmiVal < 18.5) {
      bmiStatus = '偏瘦'; bmiColor = '#60A5FA';
      bmiText = '体重偏轻，建议适当增加蛋白质与优质脂肪摄入。'; healthScore -= 5;
    } else if (bmiVal < 24) {
      bmiStatus = '标准'; bmiColor = PALETTE.teal;
      bmiText = '体型完美，请继续保持优秀的自律生活习惯！'; healthScore += 10;
    } else if (bmiVal < 28) {
      bmiStatus = '微胖'; bmiColor = PALETTE.amber;
      bmiText = '体脂微超，建议有氧运动与力量训练相结合。'; healthScore -= 5;
    } else {
      bmiStatus = '偏胖'; bmiColor = PALETTE.rose;
      bmiText = '内脏脂肪风险较高，需警惕代谢与心脑血管疾病风险。'; healthScore -= 15;
    }
  }

  const scoreMap = {
    diet: { '荤素搭配': 90, '偏爱素食': 80, '偏爱肉食': 60, '重口味(嗜咸/嗜甜)': 40 },
    exercise: { '每周3次以上': 95, '每周1-2次': 75, '偶尔运动': 50, '几乎不运动': 20 },
    sleep: { '规律且充足': 90, '偶尔熬夜/失眠': 60, '经常熬夜/失眠': 30 },
    smoking: { '不吸烟': 100, '偶尔吸烟': 40, '长期吸烟': 10 },
    drinking: { '不饮酒': 100, '偶尔饮酒': 70, '经常饮酒': 30 }
  };

  const radarData = [
    { item: '饮食习惯', score: diet ? scoreMap.diet[diet] : 50 },
    { item: '运动频次', score: exercise ? scoreMap.exercise[exercise] : 50 },
    { item: '昼夜节律', score: sleep ? scoreMap.sleep[sleep] : 50 },
    { item: '烟草成瘾', score: smoking ? scoreMap.smoking[smoking] : 50 },
    { item: '酒精摄入', score: drinking ? scoreMap.drinking[drinking] : 50 },
  ];

  const avgLifestyleScore = radarData.reduce((acc, c) => acc + c.score, 0) / 5;
  healthScore = Math.min(100, Math.max(0, Math.floor(healthScore + (avgLifestyleScore - 50) * 0.4)));
  if (safeChronicDiseases.length > 0) healthScore -= 10;
  if (safeAllergies.length > 0) healthScore -= 5;

  const radarConfig = {
    data: radarData, xField: 'item', yField: 'score', meta: { score: { min: 0, max: 100 } },
    area: { style: { fill: PALETTE.teal, fillOpacity: 0.18 } },
    line: { style: { stroke: PALETTE.teal, lineWidth: 2 } },
    point: { shape: 'circle', style: { fill: PALETTE.teal, stroke: '#fff', lineWidth: 2 } },
    xAxis: { tickLine: null, line: null, label: { style: { fill: PALETTE.textSlate, fontSize: 11, fontWeight: 600 } } },
    yAxis: { tickLine: null, line: null, label: null,
      grid: { line: { type: 'line', style: { lineDash: [4, 4], stroke: 'rgba(15,118,110,0.12)' } } } },
  };

  const alertCount = safeAllergies.length + safeChronicDiseases.length;
  const aiNoticeMessage = alertCount > 0
    ? `数字孪生扫描完毕：发现 ${alertCount} 项医疗史红线与冲突风险，已为您自动挂载诊疗室监控。`
    : `数字孪生扫描完毕：未发现临床红线告警，当前档案状态极为健康，请继续保持！`;

  // ===== 通用样式块 =====
  const glassCard = {
    background: PALETTE.glass,
    backdropFilter: 'blur(24px) saturate(160%)',
    WebkitBackdropFilter: 'blur(24px) saturate(160%)',
    border: `1px solid ${PALETTE.hairline}`,
    borderRadius: 24,
    boxShadow: '0 16px 40px rgba(15, 118, 110, 0.07), 0 2px 8px rgba(15, 118, 110, 0.03)',
    overflow: 'hidden',
  };

  const innerSlab = {
    background: PALETTE.glassThick,
    border: `1px solid ${PALETTE.hairline}`,
    borderRadius: 16,
  };

  return (
    <ConfigProvider theme={{ token: { colorPrimary: PALETTE.teal, borderRadius: 12 } }}>
      <style>{`
        .glass-card { ${Object.entries(glassCard).map(([k,v]) => `${k.replace(/[A-Z]/g, m => '-'+m.toLowerCase())}:${v}`).join(';')} }
        .metric-box { background: ${PALETTE.glassThick}; border-radius: 14px; padding: 12px 16px; text-align: center; border: 1px solid ${PALETTE.hairline}; transition: transform .2s ease, box-shadow .2s ease; }
        .metric-box:hover { transform: translateY(-2px); box-shadow: 0 8px 20px rgba(15, 118, 110, 0.08); }
        .timeline-custom .ant-timeline-item-tail { border-left: 2px solid rgba(15,118,110,0.18); }
        .tag-brand { background: rgba(240, 234, 193, 0.55); border: 1px solid rgba(245, 158, 11, 0.25); color: ${PALETTE.tealDeep}; border-radius: 8px; padding: 3px 10px; font-weight: 600; font-size: 12px; }
        .tag-clin { background: rgba(248, 113, 113, 0.10); border: 1px solid rgba(248, 113, 113, 0.30); color: #B91C1C; border-radius: 8px; padding: 3px 10px; font-weight: 600; font-size: 12px; }
        .ribbon-divider { border-right: 1px solid ${PALETTE.hairline}; padding-right: 24px; }
        @media (max-width: 992px) {
          .ribbon-divider { border-right: none; padding-right: 0; border-bottom: 1px dashed ${PALETTE.hairline}; padding-bottom: 20px; margin-bottom: 20px; }
        }
        @keyframes iconPulse { 0% { transform: scale(1); opacity: 0.85; } 50% { transform: scale(1.08); opacity: 1; filter: drop-shadow(0 0 12px ${PALETTE.tealSoft}); } 100% { transform: scale(1); opacity: 0.85; } }
        .pulse-icon { animation: iconPulse 1.5s infinite; }
        @keyframes spin { to { transform: rotate(360deg); } }
        .spinning { animation: spin 1s linear infinite; }
        .section-bar { width: 4px; height: 18px; border-radius: 4px; }
        .ai-card { background: ${PALETTE.glassThick}; border-radius: 18px; transition: transform .25s ease, box-shadow .25s ease; }
        .ai-card:hover { transform: translateY(-3px); box-shadow: 0 12px 32px rgba(15, 118, 110, 0.10); }
        .stale-pill { display: inline-flex; align-items: center; gap: 6px; font-size: 11px; color: ${PALETTE.tealDeep}; background: ${PALETTE.tealGhost}; border: 1px solid ${PALETTE.hairline}; padding: 3px 10px; border-radius: 999px; font-weight: 600; }
      `}</style>

      <div style={{
        minHeight: '100vh',
        padding: '32px 40px',
        position: 'relative',
        background: `
          radial-gradient(1200px 600px at 0% 0%, rgba(175, 238, 191, 0.55) 0%, transparent 60%),
          radial-gradient(1000px 500px at 100% 0%, rgba(240, 234, 193, 0.55) 0%, transparent 55%),
          radial-gradient(900px 600px at 50% 100%, rgba(224, 245, 238, 0.65) 0%, transparent 55%),
          linear-gradient(135deg, #f7fbf6 0%, #fbf7e8 50%, #effaf4 100%)
        `,
      }}>
        <div style={{ maxWidth: 1440, margin: '0 auto', position: 'relative', zIndex: 1 }}>

          {/* ============== 顶部导航 ============== */}
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
              <Button type="text" icon={<ArrowLeftOutlined style={{ fontSize: 18, color: PALETTE.tealDeep }} />}
                onClick={() => navigate('/chat')}
                style={{ width: 44, height: 44, borderRadius: '50%',
                  background: PALETTE.glassThick, backdropFilter: 'blur(12px)',
                  boxShadow: '0 4px 12px rgba(15,118,110,0.08)', border: `1px solid ${PALETTE.hairline}` }} />
              <div>
                <Title level={3} style={{ margin: 0, color: PALETTE.textInk, fontWeight: 800, letterSpacing: '-0.5px' }}>全维数字健康看板</Title>
                <Text style={{ fontSize: 13, color: PALETTE.tealDeep, fontWeight: 500, letterSpacing: '0.5px' }}>Digital Health Twin Dashboard</Text>
              </div>
            </div>

            <Button type="primary" size="large" icon={<EditOutlined />}
              onClick={() => navigate('/onboarding')}
              style={{
                background: `linear-gradient(135deg, ${PALETTE.teal} 0%, ${PALETTE.tealDeep} 100%)`,
                color: '#fff', border: 'none', borderRadius: 14,
                boxShadow: '0 8px 20px rgba(20, 184, 166, 0.30)',
                fontWeight: 600, height: 44, padding: '0 24px'
              }}>
              修改健康档案
            </Button>
          </div>

          {/* ============== 警示横幅 ============== */}
          <Alert
            message={<span style={{ fontWeight: 700, color: PALETTE.textInk }}>数字扫描引擎已激活</span>}
            description={<span style={{ color: PALETTE.textSlate }}>{aiNoticeMessage}</span>}
            type="info" showIcon
            icon={<RobotOutlined style={{ color: PALETTE.teal }} />}
            style={{ borderRadius: 18, ...glassCard, padding: '14px 20px', marginBottom: 24 }}
          />

          {/* ============== 顶部信息条 ============== */}
          <div className="glass-card" style={{ padding: '24px 32px', marginBottom: 24 }}>
            <Row align="middle" gutter={[32, 24]}>
              <Col xs={24} lg={7} className="ribbon-divider" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
                  <div style={{
                    width: 64, height: 64, borderRadius: 18,
                    background: `linear-gradient(135deg, ${PALETTE.tealSoft} 0%, ${PALETTE.teal} 100%)`,
                    display: 'flex', justifyContent: 'center', alignItems: 'center',
                    boxShadow: '0 8px 20px rgba(20, 184, 166, 0.25)'
                  }}>
                    <UserOutlined style={{ fontSize: 28, color: '#fff' }} />
                  </div>
                  <div>
                    <Title level={4} style={{ margin: '0 0 4px', fontWeight: 800, color: PALETTE.textInk }}>{safeName}</Title>
                    <Tag style={{
                      background: PALETTE.tealGhost, border: `1px solid ${PALETTE.hairline}`,
                      color: PALETTE.tealDeep, borderRadius: 8, margin: 0, padding: '2px 10px', fontWeight: 600
                    }}>{safeGender}</Tag>
                  </div>
                </div>
                <div style={{ textAlign: 'center' }}>
                  <Progress type="circle" percent={healthScore} size={60}
                    strokeColor={{ '0%': PALETTE.teal, '100%': PALETTE.tealDeep }}
                    trailColor="rgba(15,118,110,0.10)"
                    format={p => <span style={{ fontWeight: 800, color: PALETTE.tealDeep, fontSize: 17 }}>{p}</span>} />
                  <Text style={{ fontSize: 12, display: 'block', marginTop: 4, color: PALETTE.textMute, fontWeight: 600 }}>综合指数</Text>
                </div>
              </Col>

              <Col xs={24} lg={8} className="ribbon-divider">
                <div style={{ display: 'flex', gap: 12, justifyContent: 'space-between' }}>
                  {[
                    { label: '年龄', val: safeAge ? `${safeAge}岁` : '--' },
                    { label: '身高', val: safeHeight ? `${safeHeight}cm` : '--' },
                    { label: '体重', val: safeWeight ? `${safeWeight}kg` : '--' },
                  ].map(m => (
                    <div className="metric-box" style={{ flex: 1 }} key={m.label}>
                      <Text style={{ fontSize: 12, color: PALETTE.textMute, fontWeight: 500 }}>{m.label}</Text>
                      <Text strong style={{ fontSize: 18, color: PALETTE.textInk, display: 'block', marginTop: 2 }}>{m.val}</Text>
                    </div>
                  ))}
                </div>
              </Col>

              <Col xs={24} lg={9}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 20 }}>
                  <div style={{ ...innerSlab, padding: '12px 16px', textAlign: 'center', width: 100 }}>
                    <Text style={{ fontSize: 26, fontWeight: 800, color: bmiColor, display: 'block', lineHeight: 1 }}>{bmiValStr}</Text>
                    <Text style={{ fontSize: 12, fontWeight: 700, color: bmiColor }}>{bmiStatus}</Text>
                  </div>
                  <div style={{ flex: 1 }}>
                    <div style={{
                      position: 'relative', height: 10,
                      background: `linear-gradient(90deg, #60A5FA 0%, ${PALETTE.teal} 30%, ${PALETTE.amber} 75%, ${PALETTE.rose} 100%)`,
                      borderRadius: 5, marginBottom: 8
                    }}>
                      {safeHeight && safeWeight && (
                        <div style={{
                          position: 'absolute', top: -4, left: `calc(${pointerPosition}% - 9px)`,
                          width: 18, height: 18, background: '#fff', border: `4px solid ${bmiColor}`,
                          borderRadius: '50%', boxShadow: '0 2px 8px rgba(0,0,0,0.15)'
                        }} />
                      )}
                    </div>
                    <Text style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 6, color: PALETTE.textSlate }}>
                       <MedicineBoxOutlined style={{ color: bmiColor }}/> {bmiText}
                    </Text>
                  </div>
                </div>
              </Col>
            </Row>
          </div>

          {/* ============== 临床医学史 + 生活方式 ============== */}
          <Row gutter={[24, 24]} align="stretch" style={{ marginBottom: 24 }}>
            <Col xs={24} lg={14}>
              <div className="glass-card" style={{ padding: '28px 32px', height: '100%', display: 'flex', flexDirection: 'column' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20 }}>
                  <div className="section-bar" style={{ background: PALETTE.teal }} />
                  <Title level={5} style={{ margin: 0, fontWeight: 700, color: PALETTE.textInk }}>临床医学史看板</Title>
                </div>

                <Row gutter={[16, 16]} style={{ flex: 1 }}>
                  <Col xs={24} md={12}>
                    <div style={{ ...innerSlab, padding: 18, height: '100%' }}>
                      <Text strong style={{ color: PALETTE.textInk, display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
                        <SafetyCertificateOutlined style={{ color: PALETTE.amber }}/> 免疫过敏与禁用红线
                      </Text>
                      {safeAllergies.length === 0 ? (
                        <Text style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: PALETTE.textSlate }}>
                          <CheckCircleFilled style={{ color: PALETTE.teal }} /> 未发现临床禁忌
                        </Text>
                      ) : (
                        <Space wrap size={[6, 8]}>{safeAllergies.map((item, idx) => (<span key={idx} className="tag-clin">{item} 阳性</span>))}</Space>
                      )}
                    </div>
                  </Col>

                  <Col xs={24} md={12}>
                    <div style={{ ...innerSlab, padding: 18, height: '100%' }}>
                      <Text strong style={{ color: PALETTE.textInk, display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
                        <HeartOutlined style={{ color: PALETTE.rose }} /> 确诊疾病与病史监控
                      </Text>
                      {safeChronicDiseases.length === 0 ? (
                        <Text style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: PALETTE.textSlate }}>
                          <CheckCircleFilled style={{ color: PALETTE.teal }} /> 无重大疾病史
                        </Text>
                      ) : (
                        <Space wrap size={[6, 8]}>{safeChronicDiseases.map((item, idx) => (<span key={idx} className="tag-brand">{item}</span>))}</Space>
                      )}
                    </div>
                  </Col>

                  <Col span={24}>
                    <div style={{ ...innerSlab, padding: 20 }}>
                      <Row gutter={24}>
                        <Col xs={24} md={12} style={{ borderRight: window.innerWidth > 768 ? `1px dashed ${PALETTE.hairline}` : 'none' }}>
                          <Text style={{ display: 'block', marginBottom: 12, fontSize: 13, color: PALETTE.textMute, fontWeight: 600 }}>
                            <SafetyCertificateOutlined style={{ color: PALETTE.teal, marginRight: 6 }} />免疫接种档案
                          </Text>
                          {safeVaccines.length === 0
                            ? <Text style={{ fontSize: 13, color: PALETTE.textMute }}>无接种记录</Text>
                            : <Space wrap size={[6, 8]}>{safeVaccines.map((v, i) => <span key={i} className="tag-brand">{v}</span>)}</Space>}
                        </Col>
                        <Col xs={24} md={12} style={{ paddingLeft: window.innerWidth > 768 ? 24 : 12, marginTop: window.innerWidth <= 768 ? 16 : 0 }}>
                          <Text style={{ display: 'block', marginBottom: 12, fontSize: 13, color: PALETTE.textMute, fontWeight: 600 }}>
                            <RestOutlined style={{ color: PALETTE.teal, marginRight: 6 }} />手术记录档案
                          </Text>
                          {safeSurgeries.length === 0
                            ? <Text style={{ fontSize: 13, color: PALETTE.textMute }}>无切除/植入史</Text>
                            : (
                              <Timeline className="timeline-custom" style={{ marginTop: -4, marginBottom: 0 }}
                                items={safeSurgeries.map(surg => ({
                                  color: PALETTE.teal,
                                  children: (
                                    <div style={{ marginTop: -4 }}>
                                      <Text strong style={{ fontSize: 13, color: PALETTE.textInk }}>{surg.name ? surg.name[0] : '未知手术'}</Text><br/>
                                      <Text style={{ fontSize: 12, color: PALETTE.textMute }}>{surg.date ? dayjs(surg.date).format('YYYY-MM-DD') : '时间未知'}</Text>
                                    </div>
                                  )
                                }))} />
                            )}
                        </Col>
                      </Row>
                    </div>
                  </Col>
                </Row>
              </div>
            </Col>

            <Col xs={24} lg={10}>
              <div className="glass-card" style={{ padding: '28px 32px', height: '100%', display: 'flex', flexDirection: 'column' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20 }}>
                  <div className="section-bar" style={{ background: PALETTE.amber }} />
                  <Title level={5} style={{ margin: 0, fontWeight: 700, color: PALETTE.textInk }}>生活方式与微习惯</Title>
                </div>

                <Row gutter={16} align="middle" style={{ flex: 1 }}>
                  <Col xs={24} md={10}>
                    <div style={{ height: 180, margin: '0 -20px' }}>
                      <Radar {...radarConfig} />
                    </div>
                  </Col>
                  <Col xs={24} md={14}>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                      <div style={{ ...innerSlab, display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: 12 }}>
                        <Space><CoffeeOutlined style={{ color: PALETTE.teal }} /><Text strong style={{ fontSize: 13, color: PALETTE.textInk }}>饮食/运动</Text></Space>
                        <Text style={{ fontSize: 12, color: PALETTE.textSlate }}>{diet ? diet.substring(0,4) : '--'} | {exercise ? exercise.substring(0,4) : '--'}</Text>
                      </div>
                      <div style={{ ...innerSlab, display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: 12 }}>
                        <Space><FireOutlined style={{ color: PALETTE.amber }} /><Text strong style={{ fontSize: 13, color: PALETTE.textInk }}>成瘾性习惯</Text></Space>
                        <Text style={{ fontSize: 12, color: PALETTE.textSlate }}>烟: {smoking ? smoking.substring(0,3) : '-'} | 酒: {drinking ? drinking.substring(0,3) : '-'}</Text>
                      </div>
                      <div style={{ ...innerSlab, display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: 12 }}>
                        <Space><ClockCircleOutlined style={{ color: PALETTE.teal }} /><Text strong style={{ fontSize: 13, color: PALETTE.textInk }}>昼夜节律</Text></Space>
                        <Text style={{ fontSize: 12, color: PALETTE.textSlate }}>{sleep || '未知睡眠'}</Text>
                      </div>
                    </div>
                  </Col>
                </Row>
              </div>
            </Col>
          </Row>

          {/* ============== AI 综合洞察看板 ============== */}
          <Row>
            <Col span={24}>
              <div className="glass-card" style={{
                padding: '32px 40px',
                background: `
                  linear-gradient(135deg, rgba(240, 234, 193, 0.45) 0%, rgba(255, 255, 255, 0.78) 50%, rgba(224, 245, 238, 0.45) 100%)
                `,
                border: `1px solid ${PALETTE.hairline}`,
              }}>
                {aiGenerating ? (
                  <div style={{ textAlign: 'center', padding: '40px 0' }}>
                    <div style={{ marginBottom: 16 }}>
                      <RobotOutlined style={{ fontSize: 48, color: PALETTE.teal }} className="pulse-icon" />
                    </div>
                    <Title level={4} style={{ color: PALETTE.textInk, margin: '0 0 8px 0' }}>综合健康大脑正在生成临床洞察...</Title>
                    <Text style={{ color: PALETTE.textMute }}>正在深度比对您的全维档案与生活节律</Text>
                  </div>
                ) : (
                  <div>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20, flexWrap: 'wrap', gap: 12 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                        <BulbOutlined style={{ fontSize: 28, color: PALETTE.amber }} />
                        <Title level={4} style={{ margin: 0, color: PALETTE.textInk, fontWeight: 800 }}>综合健康洞察看板</Title>
                      </div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                        {insightsTimestamp && (
                          <span className="stale-pill">
                            <ClockCircleOutlined /> 更新于 {dayjs(insightsTimestamp).format('MM-DD HH:mm')}
                          </span>
                        )}
                        <Button
                          size="small" type="text"
                          icon={<ReloadOutlined className={aiRefreshing ? 'spinning' : ''} />}
                          onClick={handleManualRefresh}
                          disabled={aiRefreshing}
                          style={{ color: PALETTE.tealDeep, fontWeight: 600 }}
                        >
                          {aiRefreshing ? '刷新中' : '重新生成'}
                        </Button>
                      </div>
                    </div>

                    <div style={{ fontSize: 15, lineHeight: 1.8, color: PALETTE.textSlate }}>
                      <p style={{ marginTop: 0 }}>
                        👩‍⚕️ 尊敬的 <b style={{ color: PALETTE.tealDeep }}>{safeName}</b>，您好！基于您的数字档案，后端大模型引擎为您实时推演了以下深度健康洞察：
                      </p>

                      <Row gutter={[24, 24]} style={{ marginTop: 20 }} align="stretch">
                        {dynamicAIInsights && dynamicAIInsights.map((insight, index) => {
                          const isLast = index === dynamicAIInsights.length - 1;
                          const accent = index === 0 ? PALETTE.amber : (index === 1 ? PALETTE.teal : PALETTE.tealDeep);
                          const tagBg = index === 0 ? PALETTE.amberSoft : PALETTE.tealGhost;
                          return (
                            <Col xs={24} lg={isLast ? 24 : 12} key={index}>
                              <div className="ai-card" style={{
                                padding: 24,
                                border: `1px solid ${PALETTE.hairline}`,
                                height: '100%',
                                position: 'relative',
                                overflow: 'hidden',
                              }}>
                                <div style={{
                                  position: 'absolute', top: 0, left: 0, width: 4, height: '100%',
                                  background: `linear-gradient(180deg, ${accent} 0%, transparent 100%)`,
                                }} />
                                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16, gap: 8, flexWrap: 'wrap' }}>
                                  <Text strong style={{ color: PALETTE.textInk, fontSize: 16 }}>{insight.title}</Text>
                                  <Space wrap size={[6, 6]}>
                                    {insight.tags && insight.tags.map((tag, ti) => (
                                      <Tag key={ti} style={{
                                        margin: 0, borderRadius: 8, fontWeight: 600,
                                        background: tagBg, color: accent, border: `1px solid ${accent}33`,
                                        padding: '2px 10px'
                                      }}>{tag}</Tag>
                                    ))}
                                  </Space>
                                </div>
                                <div style={{ margin: 0, color: PALETTE.textSlate, fontSize: 14, lineHeight: 1.75 }}
                                  dangerouslySetInnerHTML={{ __html: insight.content }} />
                              </div>
                            </Col>
                          );
                        })}
                      </Row>
                    </div>
                  </div>
                )}
              </div>
            </Col>
          </Row>

        </div>
      </div>
    </ConfigProvider>
  );
};

export default Profile;
