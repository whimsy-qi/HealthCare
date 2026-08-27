import React from 'react';
import { ShieldCheck, AlertTriangle, Gavel, Search } from 'lucide-react';

interface RumorEvent {
  type?: string;
  event_type?: string;
  agent?: string;
  content?: string;
  weight?: number;
  verdict?: string;
  [key: string]: unknown;
}

interface RumorLiveDebateProps {
  events: RumorEvent[];
  isLive?: boolean;
}

const rumorConfig: Record<string, { icon: React.ReactNode; color: string; bg: string; label: string }> = {
  evidence_search:   { icon: <Search size={12} />,      color: '#4D6E4D', bg: '#F4F7F4', label: '证据检索' },
  pro_argument:      { icon: <ShieldCheck size={12} />, color: '#3A7A5A', bg: '#EEF4EE', label: '支持方论点' },
  con_argument:      { icon: <AlertTriangle size={12} />, color: '#9A5454', bg: '#F7EEEE', label: '反对方论点' },
  weight_vote:       { icon: <Gavel size={12} />,       color: '#A8845A', bg: '#F7F2EA', label: '权重投票' },
  final_verdict:     { icon: <Gavel size={12} />,       color: '#4D6E4D', bg: '#E4EEE4', label: '最终裁定' },
};

const getConfig = (event: RumorEvent) => {
  const key = event.event_type || event.type || 'evidence_search';
  return rumorConfig[key] || { icon: <Search size={12} />, color: '#64748B', bg: '#F8FAFC', label: key };
};

export const RumorLiveDebate: React.FC<RumorLiveDebateProps> = ({ events, isLive }) => {
  if (!events || events.length === 0) return null;

  return (
    <div style={{
      background: '#F8F7F4',
      border: '1px solid #E2DED6',
      borderRadius: 12,
      padding: '14px 16px',
      marginBottom: 8,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
        <div style={{
          width: 6, height: 6, borderRadius: '50%',
          background: isLive ? '#C07878' : '#A8A49C',
          boxShadow: isLive ? '0 0 0 3px rgba(192,120,120,0.2)' : 'none',
        }} />
        <span style={{ fontSize: 11, fontWeight: 700, color: '#64748B', letterSpacing: '0.6px', textTransform: 'uppercase' }}>
          {isLive ? '谣言加权辩论进行中' : '辟谣溯源记录'}
        </span>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
        {events.map((evt, idx) => {
          const cfg = getConfig(evt);
          const isLast = idx === events.length - 1;
          return (
            <div key={idx} style={{ display: 'flex', gap: 10, position: 'relative' }}>
              {!isLast && (
                <div style={{
                  position: 'absolute', left: 10, top: 20, bottom: 0,
                  width: 1, background: '#E2E8F0', zIndex: 0,
                }} />
              )}
              <div style={{
                width: 20, height: 20, borderRadius: '50%', flexShrink: 0,
                background: cfg.bg, border: `1.5px solid ${cfg.color}25`,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                color: cfg.color, zIndex: 1, marginTop: 2,
              }}>
                {cfg.icon}
              </div>
              <div style={{ flex: 1, paddingBottom: isLast ? 0 : 10 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 3 }}>
                  <span style={{ fontSize: 10, fontWeight: 700, color: cfg.color, textTransform: 'uppercase' }}>
                    {evt.agent || cfg.label}
                  </span>
                  {evt.weight !== undefined && (
                    <span style={{ fontSize: 10, color: '#94A3B8', background: '#F1F5F9', borderRadius: 4, padding: '1px 5px' }}>
                      权重 {evt.weight}
                    </span>
                  )}
                </div>
                {evt.content && (
                  <div style={{
                    fontSize: 12, color: '#475569', lineHeight: 1.6,
                    background: cfg.bg, borderRadius: 8, padding: '5px 10px',
                    border: `1px solid ${cfg.color}12`,
                    display: '-webkit-box', WebkitLineClamp: 3, WebkitBoxOrient: 'vertical', overflow: 'hidden',
                  }}>
                    {evt.content}
                  </div>
                )}
                {evt.verdict && (
                  <div style={{
                    fontSize: 12, fontWeight: 700, color: cfg.color,
                    marginTop: 4, padding: '4px 10px',
                    background: cfg.bg, borderRadius: 6, display: 'inline-block'
                  }}>
                    裁定：{evt.verdict}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default RumorLiveDebate;