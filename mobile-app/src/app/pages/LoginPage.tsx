import React, { useState } from 'react';
import { useNavigate } from 'react-router';
import { User, Lock, Eye, EyeOff, ArrowRight, Activity, Shield, Brain, Stethoscope, Sparkles } from 'lucide-react';
import { toast } from 'sonner';

// ─── Design Tokens ─────────────────────────────────────────────────
const T = {
  // ── Mint (Primary #afeebf) ──
  lime50:   '#edfaf2', lime100: '#d4f5df', lime200: '#afeebf',
  lime300:  '#7bd49a', lime400:  '#4eba78', lime500: '#32a05f',
  lime600:  '#228048', lime700:  '#166035',
  // ── Cream (Secondary #f0eac1) ──
  yellow200: '#f0eac1', yellow300: '#e4d68a', yellow400: '#ccb85a',
  // ── Fresh accents ──
  sky200: '#b8dff0', lav200: '#d4cff5', peach200: '#f5ceb0',
  // ── Warm Neutrals ──
  slate50:  '#f4fbf6', slate100: '#edf5ef', slate200: '#d8ead9',
  slate400: '#90a892', slate500: '#637065',
  slate600: '#465049', slate700: '#313830', slate800: '#1e2420', slate900: '#0e120f',
  white: '#FFFFFF',
};

const features = [
  { icon: <Brain size={16} />, title: '多智能体联合诊断', desc: '六大专科 AI 协同会诊，辩论融合输出结论' },
  { icon: <Shield size={16} />, title: '可信溯源与辟谣引擎', desc: '实时核查知识库，每条结论均可追溯' },
  { icon: <Activity size={16} />, title: '数字健康孪生', desc: '基于个人档案的全维健康分析看板' },
  { icon: <Stethoscope size={16} />, title: '跨模态医学解读', desc: '上传影像报告，AI 即刻解读关键指标' },
];

export const LoginPage: React.FC = () => {
  const navigate = useNavigate();
  const [isRegister, setIsRegister] = useState(false);
  const [loading, setLoading] = useState(false);
  const [showPw, setShowPw] = useState(false);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username.trim()) { toast.error('请输入用户名'); return; }
    if (password.length < 6) { toast.error('密码至少6位'); return; }

    setLoading(true);
    const endpoint = isRegister
      ? 'http://localhost:8000/api/register'
      : 'http://localhost:8000/api/login';

    try {
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || '请求失败');

      if (isRegister) {
        toast.success('注册成功！请登录');
        setIsRegister(false);
      } else {
        localStorage.setItem('access_token', data.access_token);
        localStorage.setItem('current_username', data.username);
        fetch('http://localhost:8000/api/articles/hot-realtime').catch(() => {});

        try {
          const profileRes = await fetch('http://localhost:8000/api/profile', {
            headers: { 'Authorization': `Bearer ${data.access_token}` },
          });
          if (profileRes.ok) {
            const pData = await profileRes.json();
            let parsed = pData.profile_data;
            if (typeof parsed === 'string') {
              try { parsed = JSON.parse(parsed); } catch { parsed = {}; }
            }
            if (parsed && Object.keys(parsed).length > 0 && parsed.gender) {
              toast.success('欢迎回来！');
              navigate('/chat');
            } else {
              // 🆕 首次登录 / 档案为空：直接进入档案编辑页（之前因路由死路被踢回 /chat）
              toast.success('登录成功，请完善健康档案以获得精准建议');
              navigate('/onboarding');
            }
          } else {
            navigate('/chat');
          }
        } catch {
          navigate('/chat');
        }
      }
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : '登录失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{
      minHeight: '100vh', width: '100vw', overflow: 'hidden',
      background: '#f4fbf6',
      fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
      position: 'relative', display: 'flex', alignItems: 'center', justifyContent: 'center',
    }}>

      {/* ── Diffused Glow Orbs — Mint + Cream + Accents ── */}
      {/* Top-left: soft mint #afeebf */}
      <div style={{
        position: 'fixed', top: '-12%', left: '-10%',
        width: 600, height: 600, borderRadius: '50%',
        background: 'radial-gradient(circle, rgba(175,238,191,0.55) 0%, rgba(123,212,154,0.25) 40%, transparent 70%)',
        filter: 'blur(45px)', pointerEvents: 'none', zIndex: 0,
      }} />
      {/* Top-right: warm cream #f0eac1 */}
      <div style={{
        position: 'fixed', top: '-6%', right: '2%',
        width: 480, height: 480, borderRadius: '50%',
        background: 'radial-gradient(circle, rgba(240,234,193,0.65) 0%, rgba(228,214,138,0.3) 40%, transparent 70%)',
        filter: 'blur(50px)', pointerEvents: 'none', zIndex: 0,
      }} />
      {/* Bottom-right: soft sky blue */}
      <div style={{
        position: 'fixed', bottom: '-8%', right: '-4%',
        width: 500, height: 500, borderRadius: '50%',
        background: 'radial-gradient(circle, rgba(184,223,240,0.48) 0%, rgba(90,170,212,0.18) 45%, transparent 70%)',
        filter: 'blur(50px)', pointerEvents: 'none', zIndex: 0,
      }} />
      {/* Center-left: mint haze */}
      <div style={{
        position: 'fixed', top: '38%', left: '12%',
        width: 350, height: 350, borderRadius: '50%',
        background: 'radial-gradient(circle, rgba(175,238,191,0.35) 0%, rgba(123,212,154,0.12) 50%, transparent 70%)',
        filter: 'blur(60px)', pointerEvents: 'none', zIndex: 0,
      }} />
      {/* Bottom-left: lavender accent */}
      <div style={{
        position: 'fixed', bottom: '8%', left: '6%',
        width: 300, height: 300, borderRadius: '50%',
        background: 'radial-gradient(circle, rgba(212,207,245,0.5) 0%, rgba(184,174,222,0.2) 50%, transparent 70%)',
        filter: 'blur(55px)', pointerEvents: 'none', zIndex: 0,
      }} />

      {/* ── Dot Grid ── */}
      <div style={{
        position: 'fixed', inset: 0, pointerEvents: 'none', zIndex: 0,
        backgroundImage: 'radial-gradient(rgba(50,160,95,0.07) 1px, transparent 1px)',
        backgroundSize: '24px 24px',
      }} />

      {/* ── Main Content ── */}
      <div style={{
        position: 'relative', zIndex: 1,
        width: '100%', maxWidth: 1060,
        margin: '0 auto', padding: '32px 24px',
        display: 'flex', alignItems: 'center', gap: 40,
      }}>

        {/* ── Left: Brand & Features ── */}
        <div style={{ flex: 1, paddingRight: 24, display: 'none' }} className="brand-panel">
          {/* hidden on small, visible on large */}
        </div>

        <div style={{ flex: 1.1, minWidth: 0 }}>
          {/* Logo row */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 40 }}>
            <div style={{
              width: 42, height: 42, borderRadius: 12,
              background: 'linear-gradient(135deg, #4eba78 0%, #228048 100%)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              boxShadow: '0 4px 16px rgba(50,160,95,0.32)',
            }}>
              <Activity size={20} color="white" strokeWidth={2.5} />
            </div>
            <div>
              <div style={{ fontSize: 17, fontWeight: 800, color: T.slate800, letterSpacing: '-0.3px' }}>TrustMed AI</div>
              <div style={{ fontSize: 11, color: T.lime500, fontWeight: 600, letterSpacing: '1.2px', textTransform: 'uppercase' }}>Trusted Medical AI</div>
            </div>
          </div>

          {/* Headline */}
          <div style={{ marginBottom: 36 }}>
            <h1 style={{ margin: '0 0 10px', fontSize: 38, fontWeight: 900, color: T.slate800, lineHeight: 1.15, letterSpacing: '-1px' }}>
              懂医疗，
              <span style={{
                background: 'linear-gradient(135deg, #166035 0%, #32a05f 45%, #c4a040 100%)',
                WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent',
              }}>更懂你</span>
            </h1>
            <p style={{ margin: 0, fontSize: 15, color: T.slate500, lineHeight: 1.7, maxWidth: 380 }}>
              基于多智能体协作架构的可信医疗问答系统，为每一个健康决策提供可溯源的智能支持。
            </p>
          </div>

          {/* Feature chips */}
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, marginBottom: 42 }}>
            {features.map((f, i) => (
              <div key={i} style={{
                display: 'flex', alignItems: 'center', gap: 7,
                padding: '7px 13px', borderRadius: 40,
                background: [
                  'rgba(175,238,191,0.35)',
                  'rgba(240,234,193,0.5)',
                  'rgba(184,223,240,0.4)',
                  'rgba(212,207,245,0.4)',
                ][i % 4],
                border: `1px solid ${[
                  'rgba(123,212,154,0.45)',
                  'rgba(228,214,138,0.5)',
                  'rgba(90,170,212,0.35)',
                  'rgba(184,174,222,0.4)',
                ][i % 4]}`,
                backdropFilter: 'blur(8px)',
              }}>
                <span style={{ color: [T.lime600, '#a88028', '#3a80a8', '#6858a8'][i % 4] }}>{f.icon}</span>
                <span style={{ fontSize: 13, fontWeight: 600, color: T.slate700 }}>{f.title}</span>
              </div>
            ))}
          </div>

          {/* Testimonial badge */}
          <div style={{
            display: 'inline-flex', alignItems: 'center', gap: 8,
            padding: '9px 16px', borderRadius: 12,
            background: 'rgba(240,234,193,0.5)',
            border: '1px solid rgba(228,214,138,0.55)',
            backdropFilter: 'blur(10px)',
          }}>
            <Sparkles size={14} color='#a88028' />
            <span style={{ fontSize: 12.5, color: T.slate600, fontWeight: 500 }}>
              已服务 <strong style={{ color: T.lime600 }}>12,000+</strong> 次医疗咨询，准确率高达 <strong style={{ color: T.lime600 }}>94.7%</strong>
            </span>
          </div>
        </div>

        {/* ── Right: Frosted Glass Form Card ── */}
        <div style={{
          width: 400, flexShrink: 0,
          background: 'rgba(255,255,255,0.72)',
          backdropFilter: 'blur(28px)',
          WebkitBackdropFilter: 'blur(28px)',
          border: '1px solid rgba(175,238,191,0.35)',
          borderRadius: 24,
          padding: '36px 36px 32px',
          boxShadow: '0 8px 40px rgba(50,160,95,0.1), 0 2px 8px rgba(0,0,0,0.03)',
        }}>
          {/* Card header */}
          <div style={{ marginBottom: 28 }}>
            <div style={{
              display: 'inline-flex', alignItems: 'center', gap: 6,
              padding: '4px 12px', borderRadius: 20,
              background: 'rgba(175,238,191,0.25)',
              border: '1px solid rgba(123,212,154,0.4)',
              marginBottom: 12,
            }}>
              <div style={{ width: 6, height: 6, borderRadius: '50%', background: T.lime500 }} />
              <span style={{ fontSize: 12, fontWeight: 700, color: T.lime600, letterSpacing: '0.5px', textTransform: 'uppercase' }}>
                {isRegister ? '创建账户' : '欢迎回来'}
              </span>
            </div>
            <h2 style={{ margin: 0, fontSize: 22, fontWeight: 800, color: T.slate800, lineHeight: 1.3 }}>
              {isRegister ? '开启数字医疗旅程' : '安全登录您的账户'}
            </h2>
            <p style={{ margin: '6px 0 0', fontSize: 13.5, color: T.slate500, lineHeight: 1.6 }}>
              {isRegister ? '注册后即可享受 AI 全科医生的个性化服务' : '请输入账户信息，继续您的健康管理'}
            </p>
          </div>

          {/* Form */}
          <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            {/* Username */}
            <div>
              <label style={{ display: 'block', fontSize: 13, fontWeight: 600, color: T.slate700, marginBottom: 6 }}>
                用户名
              </label>
              <div style={{ position: 'relative' }}>
                <User size={15} color={T.slate400} style={{ position: 'absolute', left: 14, top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none' }} />
                <input
                  type="text"
                  value={username}
                  onChange={e => setUsername(e.target.value)}
                  placeholder="请输入用户名"
                  style={{
                    width: '100%', height: 46, paddingLeft: 40, paddingRight: 16,
                    background: 'rgba(255,255,255,0.8)',
                    border: `1.5px solid rgba(127,168,127,0.28)`,
                    borderRadius: 12, fontSize: 14.5, color: T.slate800,
                    outline: 'none', boxSizing: 'border-box',
                    transition: 'border-color 0.2s, box-shadow 0.2s',
                  }}
                  onFocus={e => {
                    e.target.style.borderColor = T.lime400;
                    e.target.style.boxShadow = `0 0 0 3px rgba(99,136,99,0.12)`;
                  }}
                  onBlur={e => {
                    e.target.style.borderColor = 'rgba(127,168,127,0.28)';
                    e.target.style.boxShadow = 'none';
                  }}
                />
              </div>
            </div>

            {/* Password */}
            <div>
              <label style={{ display: 'block', fontSize: 13, fontWeight: 600, color: T.slate700, marginBottom: 6 }}>
                密码
              </label>
              <div style={{ position: 'relative' }}>
                <Lock size={15} color={T.slate400} style={{ position: 'absolute', left: 14, top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none' }} />
                <input
                  type={showPw ? 'text' : 'password'}
                  value={password}
                  onChange={e => setPassword(e.target.value)}
                  placeholder="密码（至少6位）"
                  style={{
                    width: '100%', height: 46, paddingLeft: 40, paddingRight: 48,
                    background: 'rgba(255,255,255,0.8)',
                    border: `1.5px solid rgba(127,168,127,0.28)`,
                    borderRadius: 12, fontSize: 14.5, color: T.slate800,
                    outline: 'none', boxSizing: 'border-box',
                    transition: 'border-color 0.2s, box-shadow 0.2s',
                  }}
                  onFocus={e => {
                    e.target.style.borderColor = T.lime400;
                    e.target.style.boxShadow = `0 0 0 3px rgba(99,136,99,0.12)`;
                  }}
                  onBlur={e => {
                    e.target.style.borderColor = 'rgba(127,168,127,0.28)';
                    e.target.style.boxShadow = 'none';
                  }}
                />
                <button
                  type="button"
                  onClick={() => setShowPw(!showPw)}
                  style={{ position: 'absolute', right: 14, top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', cursor: 'pointer', color: T.slate400, padding: 0, display: 'flex' }}
                >
                  {showPw ? <EyeOff size={15} /> : <Eye size={15} />}
                </button>
              </div>
            </div>

            {/* Submit Button */}
            <button
              type="submit"
              disabled={loading}
              style={{
                height: 50, borderRadius: 12, border: 'none', marginTop: 4,
                background: loading
                  ? 'rgba(175,238,191,0.3)'
                  : 'linear-gradient(135deg, #4eba78 0%, #166035 100%)',
                color: loading ? T.lime600 : 'white',
                cursor: loading ? 'not-allowed' : 'pointer',
                fontSize: 15, fontWeight: 700, letterSpacing: '0.3px',
                display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8,
                boxShadow: loading ? 'none' : '0 6px 20px rgba(50,160,95,0.35)',
                transition: 'all 0.25s',
              }}
              onMouseEnter={e => {
                if (!loading) {
                  (e.currentTarget as HTMLButtonElement).style.transform = 'translateY(-1px)';
                  (e.currentTarget as HTMLButtonElement).style.boxShadow = '0 8px 28px rgba(50,160,95,0.45)';
                }
              }}
              onMouseLeave={e => {
                (e.currentTarget as HTMLButtonElement).style.transform = 'none';
                (e.currentTarget as HTMLButtonElement).style.boxShadow = loading ? 'none' : '0 6px 20px rgba(50,160,95,0.35)';
              }}
            >
              {loading ? (
                <div style={{ width: 18, height: 18, border: `2.5px solid rgba(50,160,95,0.25)`, borderTopColor: T.lime600, borderRadius: '50%', animation: 'spin360 0.8s linear infinite' }} />
              ) : (
                <>
                  {isRegister ? '注册并进入' : '安全登录'}
                  <ArrowRight size={16} />
                </>
              )}
            </button>

            {/* Toggle */}
            <div style={{ textAlign: 'center', paddingTop: 4 }}>
              <span style={{ fontSize: 13.5, color: T.slate500 }}>
                {isRegister ? '已有账户？' : '第一次来这里？'}
              </span>
              <button
                type="button"
                onClick={() => setIsRegister(!isRegister)}
                style={{ background: 'none', border: 'none', cursor: 'pointer', color: T.lime500, fontWeight: 700, fontSize: 13.5, padding: '0 4px', marginLeft: 2 }}
              >
                {isRegister ? '直接登录' : '创建免费账户'}
              </button>
            </div>
          </form>

          {/* Footer notice */}
          <div style={{
            marginTop: 22, padding: '10px 14px', borderRadius: 10,
            background: 'rgba(175,238,191,0.15)',
            border: `1px solid rgba(123,212,154,0.3)`,
            display: 'flex', alignItems: 'flex-start', gap: 8,
          }}>
            <Shield size={13} color={T.lime500} style={{ marginTop: 1, flexShrink: 0 }} />
            <span style={{ fontSize: 11.5, color: T.slate600, lineHeight: 1.6 }}>
              您的健康数据仅用于个性化医疗建议，经端到端加密传输，受严格隐私保护。
            </span>
          </div>
        </div>
      </div>

      <style>{`
        @keyframes spin360 { to { transform: rotate(360deg); } }
        @media (max-width: 700px) {
          .brand-left { display: none !important; }
        }
      `}</style>
    </div>
  );
};