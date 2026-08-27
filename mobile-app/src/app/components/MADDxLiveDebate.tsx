import React from 'react';
import { Brain, Shield, Scale, Zap, ChevronRight, Bot } from 'lucide-react';

interface MADDxEvent {
  type?: string;
  event_type?: string;
  agent?: string;
  content?: string;
  role?: string;
  [key: string]: unknown;
}

interface MADDxLiveDebateProps {
  events: MADDxEvent[];
  isLive?: boolean;
}

const agentConfig: Record<string, { icon: React.ReactNode; color: string; bg: string; label: string }> = {
  advocate_response:  { icon: <Brain size={13} />,  color: '#4D6E4D', bg: '#F4F7F4', label: '支持方智能体' },
  devil_response:     { icon: <Shield size={13} />, color: '#7C6A8A', bg: '#F5F3F8', label: '质疑方智能体' },
  judge_response:     { icon: <Scale size={13} />,  color: '#A8845A', bg: '#F7F2EA', label: '裁判智能体' },
  routing:            { icon: <Zap size={13} />,    color: '#4D6E4D', bg: '#F4F7F4', label: '路由决策' },
  final_answer:       { icon: <Bot size={13} />,    color: '#3A7A5A', bg: '#EEF4EE', label: '最终结论' },
};

const getConfig = (event: MADDxEvent) => {
  const key = event.event_type || event.type || 'routing';
  return agentConfig[key] || { icon: <Bot size={13} />, color: '#64748B', bg: '#F8FAFC', label: key };
};

export const MADDxLiveDebate: React.FC<MADDxLiveDebateProps> = ({ events, isLive }) => {
  if (!events || events.length === 0) return null;

  return (
    <div style={{
      background: '#F8F7F4',
      border: '1px solid #E2DED6',
      borderRadius: 12,
      padding: '14px 16px',
      marginBottom: 8,
    }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
        <div style={{
          width: 6, height: 6, borderRadius: '50%',
          background: isLive ? '#638863' : '#A8A49C',
          boxShadow: isLive ? '0 0 0 3px rgba(99,136,99,0.2)' : 'none',
          animation: isLive ? 'pulse 2s infinite' : 'none',
        }} />
        <span style={{ fontSize: 11, fontWeight: 700, color: '#64748B', letterSpacing: '0.6px', textTransform: 'uppercase' }}>
          {isLive ? '多智能体辩论进行中' : '多智能体辩论记录'}
        </span>
      </div>

      {/* Timeline */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
        {events.map((evt, idx) => {
          const cfg = getConfig(evt);
          const isLast = idx === events.length - 1;
          return (
            <div key={idx} style={{ display: 'flex', gap: 10, position: 'relative' }}>
              {/* Line */}
              {!isLast && (
                <div style={{
                  position: 'absolute', left: 11, top: 22, bottom: 0,
                  width: 1, background: '#E2E8F0', zIndex: 0,
                }} />
              )}
              {/* Icon */}
              <div style={{
                width: 22, height: 22, borderRadius: '50%', flexShrink: 0,
                background: cfg.bg, border: `1.5px solid ${cfg.color}20`,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                color: cfg.color, zIndex: 1, marginTop: 2,
              }}>
                {cfg.icon}
              </div>
              {/* Content */}
              <div style={{ flex: 1, paddingBottom: isLast ? 0 : 12 }}>
                <div style={{ fontSize: 10, fontWeight: 700, color: cfg.color, textTransform: 'uppercase', letterSpacing: '0.4px', marginBottom: 3 }}>
                  {evt.agent || cfg.label}
                </div>
                {evt.content && (
                  <div style={{
                    fontSize: 12, color: '#475569', lineHeight: 1.6,
                    background: cfg.bg, borderRadius: 8, padding: '6px 10px',
                    border: `1px solid ${cfg.color}15`,
                    display: '-webkit-box', WebkitLineClamp: 4, WebkitBoxOrient: 'vertical', overflow: 'hidden',
                  }}>
                    {evt.content}
                  </div>
                )}
              </div>
            </div>
          );
        })}
        {isLive && (
          <div style={{ display: 'flex', gap: 10, marginTop: 4 }}>
            <div style={{ width: 22, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <div style={{ display: 'flex', gap: 3 }}>
                {[0, 0.2, 0.4].map((d, i) => (
                  <div key={i} style={{
                    width: 4, height: 4, borderRadius: '50%', background: '#0D9488',
                    animation: 'thinkPulse 1.4s ease-in-out infinite',
                    animationDelay: `${d}s`, opacity: 0.6,
                  }} />
                ))}
              </div>
            </div>
            <span style={{ fontSize: 11, color: '#94A3B8', fontStyle: 'italic' }}>智能体协作中…</span>
          </div>
        )}
      </div>
    </div>
  );
};

export default MADDxLiveDebate;