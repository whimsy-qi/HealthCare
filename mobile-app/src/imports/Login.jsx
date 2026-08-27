import React, { useState } from 'react';
import { Form, Input, Button, Typography, message, ConfigProvider } from 'antd';
import { UserOutlined, LockOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import Threads from './Threads'; 
import Grainient from './Grainient'; 
import SplitText from './SplitText'; 

const { Title, Text } = Typography;

const Login = () => {
  const navigate = useNavigate();
  const [isRegister, setIsRegister] = useState(false);
  const [loading, setLoading] = useState(false);

  const onFinish = async (values) => {
    setLoading(true);
    const endpoint = isRegister ? 'http://localhost:8000/api/register' : 'http://localhost:8000/api/login';
    
    try {
      const response = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          username: values.username,
          password: values.password
        })
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.detail || '请求失败，请检查网络或后端运行状态');
      }

      if (isRegister) {
        message.success('注册成功！请直接登录');
        setIsRegister(false); 
      } 
      else {
        localStorage.setItem('access_token', data.access_token);
        localStorage.setItem('current_username', data.username);

        // 🔥 静默预热：登录成功立刻后台拉取实时热点，等用户逛到知识专区时已就绪
        // 无需 await、无需 try/catch 吞掉错误，失败也不影响登录流程
        fetch('http://localhost:8000/api/articles/hot-realtime').catch(() => {});

        try {
          const profileRes = await fetch('http://localhost:8000/api/profile', {
            headers: { 'Authorization': `Bearer ${data.access_token}` }
          });
          
          if (profileRes.ok) {
            const pData = await profileRes.json();
            let parsed = pData.profile_data;
            if (typeof parsed === 'string') {
              try { parsed = JSON.parse(parsed); } catch(e) { console.warn("档案解析失败:", e); parsed = {}; }
            }

            if (parsed && Object.keys(parsed).length > 0 && parsed.gender) {
              message.success('登录成功！欢迎回来');
              navigate('/chat');
            } else {
              message.success('登录成功！请先完善您的健康档案');
              navigate('/onboarding');
            }
          } else {
            navigate('/onboarding');
          }
        } catch (profileErr) {
          console.warn("档案获取异常，直接进入系统:", profileErr);
          navigate('/chat');
        }
      }
    } catch (error) {
      message.error(error.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <ConfigProvider theme={{ token: { colorPrimary: '#14B8A6' } }}>
      <style>
        {`
          .slogan-main {
            color: #0F766E; 
            font-weight: 800;
            font-size: 52px;
            margin: 0;
            letter-spacing: 2px;
          }
          .slogan-sub {
            color: #14B8A6; /* 🌟 改为比第一行浅一点的医疗绿 */
            font-weight: 800; /* 🌟 粗细与第一行一致 */
            font-size: 52px; /* 🌟 字号放大，与第一行完全一致 */
            margin-top: 12px;
            letter-spacing: 2px; /* 恢复中文的字间距 */
          }
        `}
      </style>
      <div style={{ 
        position: 'relative', 
        width: '100vw', 
        height: '100vh', 
        backgroundColor: '#F8FAFC', 
        display: 'flex', 
        flexDirection: 'column', 
        alignItems: 'center', 
        justifyContent: 'center', 
        overflow: 'hidden' 
      }}>
        
        {/* Layer 0: 弥散渐变背景 */}
        <div style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, zIndex: 0 }}>
          <Grainient
            color1="#afeebf" color2="#f0eac1" color3="#e0f5ee"
            timeSpeed={1.15} colorBalance={0} warpStrength={1}
            warpFrequency={5} warpSpeed={2} warpAmplitude={50}
            blendAngle={0} blendSoftness={0.05} rotationAmount={500}
            noiseScale={2} grainAmount={0.08} grainScale={2}
            grainAnimated={false} contrast={1.5} gamma={1}
            saturation={1} centerX={0} centerY={0} zoom={0.9}
          />
        </div>

        {/* Layer 1: WebGL 医疗流体丝线 */}
        <div style={{ position: 'absolute', top: '20vh', left: 0, right: 0, bottom: '-20vh', zIndex: 1, opacity: 0.65 }}>
           <Threads 
              color={[0.08, 0.72, 0.65]} 
              amplitude={1.5} 
              distance={0} 
              enableMouseInteraction={true} 
           />
        </div>

        {/* Layer 2: 核心视觉区 */}
        <div style={{ position: 'relative', zIndex: 10, display: 'flex', flexDirection: 'column', alignItems: 'center', width: '100%', padding: '0 20px' }}>
            
            {/* 顶部居中 Slogan */}
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', marginBottom: '56px', marginTop: '-40px' }}>
                <SplitText
                  text="懂医疗，更懂你"
                  className="slogan-main"
                  delay={60}
                  duration={1.2}
                  ease="power3.out"
                  splitType="chars"
                  from={{ opacity: 0, y: 30 }}
                  to={{ opacity: 1, y: 0 }}
                  tag="h1"
                />
                <SplitText
                  text="你的私人AI诊疗室" /* 🌟 换回中文 */
                  className="slogan-sub"
                  delay={60} /* 🌟 恢复中文的逐字动画节奏 */
                  duration={1.2}
                  ease="power3.out"
                  splitType="chars"
                  from={{ opacity: 0, y: 30 }} /* 🌟 保持与第一行相同的入场位移 */
                  to={{ opacity: 1, y: 0 }}
                  tag="h2"
                />
            </div>

            {/* 居中毛玻璃表单卡片 */}
            <div style={{ 
                width: '100%', 
                maxWidth: '440px', 
                background: 'rgba(255, 255, 255, 0.5)',
                backdropFilter: 'blur(32px)', 
                WebkitBackdropFilter: 'blur(32px)',
                border: '1px solid rgba(255, 255, 255, 0.8)',
                borderRadius: '28px', 
                padding: '48px 40px', 
                boxShadow: '0 24px 48px rgba(0,0,0,0.06), 0 0 0 1px rgba(255,255,255,0.4) inset' 
            }}>
                {isRegister && (
                  <div style={{ marginBottom: '36px', textAlign: 'center' }}>
                    <Title level={3} style={{ color: '#0F172A', fontWeight: 800, margin: 0 }}>
                      创建您的档案
                    </Title>
                    <Text type="secondary" style={{ fontSize: '15px', display: 'block', marginTop: '8px' }}>
                      开启您的专属数字医疗旅程
                    </Text>
                  </div>
                )}
                
                {!isRegister && <div style={{ height: '16px' }}></div>}

                <Form name="auth_form" onFinish={onFinish} layout="vertical" size="large">
                  <Form.Item name="username" rules={[{ required: true, message: '请输入用户名' }]}>
                    <Input 
                      prefix={<UserOutlined style={{ color: '#94A3B8', marginRight: 4 }} />} 
                      placeholder="用户名" 
                      style={{ borderRadius: '12px', background: 'rgba(255, 255, 255, 0.8)', border: '1px solid #E2E8F0', height: '52px', fontSize: '15px' }} 
                      onFocus={(e) => e.target.parentElement.style.borderColor = '#14B8A6'}
                      onBlur={(e) => e.target.parentElement.style.borderColor = '#E2E8F0'}
                    />
                  </Form.Item>

                  <Form.Item name="password" rules={[{ required: true, message: '请输入密码' }, { min: 6, message: '密码至少6位' }]}>
                    <Input.Password 
                      prefix={<LockOutlined style={{ color: '#94A3B8', marginRight: 4 }} />} 
                      placeholder="密码 (至少6位)" 
                      style={{ borderRadius: '12px', background: 'rgba(255, 255, 255, 0.8)', border: '1px solid #E2E8F0', height: '52px', fontSize: '15px' }} 
                    />
                  </Form.Item>

                  <Form.Item style={{ marginTop: '40px', marginBottom: '24px' }}>
                    <Button type="primary" htmlType="submit" block loading={loading} style={{ height: '52px', borderRadius: '12px', fontSize: '16px', fontWeight: 600, background: '#14B8A6', border: 'none', boxShadow: '0 8px 20px rgba(20,184,166,0.3)' }}>
                      {isRegister ? '注册并进入' : '安全登录'}
                    </Button>
                  </Form.Item>

                  <div style={{ textAlign: 'center' }}>
                    <Text type="secondary" style={{ fontSize: '14px' }}>
                      {isRegister ? '已有账户？' : '第一次来这里？'}
                    </Text>
                    <Button type="link" onClick={() => setIsRegister(!isRegister)} style={{ color: '#14B8A6', fontWeight: 600, padding: '0 4px', fontSize: '14px' }}>
                      {isRegister ? '直接登录' : '创建一个免费账户'}
                    </Button>
                  </div>
                </Form>
            </div>
        </div>

      </div>
    </ConfigProvider>
  );
};

export default Login;