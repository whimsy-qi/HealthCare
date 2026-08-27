import React, { useMemo, useState, useRef, useEffect } from 'react';
import ForceGraph2D from 'react-force-graph-2d';
import { Tag, Tooltip, Typography, Empty } from 'antd';
import { NodeIndexOutlined, RightOutlined, DownOutlined } from '@ant-design/icons';

const { Text } = Typography;

/**
 * BlackboardDAGView
 * 输入：trace_data.blackboard_dag = { nodes: [{id, label, agent, ts, preview, value}], edges: [{from, to}] }
 * 渲染：力导向图 + 节点详情 hover 卡。
 *
 * 颜色按 agent 前缀分组，让评委一眼看到"哪些 agent 参与了这次推理"。
 */

// agent 命名空间 → 配色
const AGENT_COLORS = {
  triage:           '#0EA5E9',   // 蓝
  pre_flight:       '#06B6D4',   // 青
  med_extractor:    '#7C3AED',
  med_pharmacist:   '#7C3AED',
  med_reviewer:     '#7C3AED',   // 紫色一族 = 用药
  'report':         '#0891B2',   // 报告
  'symptom':        '#0EA5E9',   // 症状
  'general':        '#16A34A',   // 全科
  'rumor':          '#EA580C',
  'perception':     '#EA580C',
  'claim_classifier': '#EA580C',
  'risk_router':    '#EA580C',
  'rumor_judge':    '#EA580C',
  'advocate':       '#22C55E',
  'skeptic':        '#DC2626',
  'rumor_fast_path': '#F59E0B',
};

function agentColor(agentId = '') {
  // 优先精确匹配
  if (AGENT_COLORS[agentId]) return AGENT_COLORS[agentId];
  // 按前缀匹配（如 "report.vision_ocr" → 匹配 "report"）
  const prefix = String(agentId).split('.')[0];
  if (AGENT_COLORS[prefix]) return AGENT_COLORS[prefix];
  return '#94A3B8'; // 默认灰
}

export default function BlackboardDAGView({ dag }) {
  const [expanded, setExpanded] = useState(false);
  const [hoveredNode, setHoveredNode] = useState(null);
  const fgRef = useRef(null);
  const containerRef = useRef(null);
  const [size, setSize] = useState({ w: 600, h: 360 });

  // 适配 ForceGraph 数据格式：edges → links
  const data = useMemo(() => {
    if (!dag || !Array.isArray(dag.nodes)) return { nodes: [], links: [] };
    const nodes = dag.nodes.map(n => ({
      id: n.id,
      label: n.label || `v${n.id}`,
      agent: n.agent || '',
      preview: n.preview || '',
      ts: n.ts,
      _color: agentColor(n.agent),
    }));
    const links = (dag.edges || []).map(e => ({
      source: e.from,
      target: e.to,
    }));
    return { nodes, links };
  }, [dag]);

  // 容器大小自适应
  useEffect(() => {
    if (!containerRef.current) return;
    const ro = new ResizeObserver(entries => {
      for (const entry of entries) {
        const { width } = entry.contentRect;
        setSize({ w: Math.max(400, width - 16), h: 360 });
      }
    });
    ro.observe(containerRef.current);
    return () => ro.disconnect();
  }, [expanded]);

  // agent 统计（必须在条件早返回之前调用，否则 hooks 顺序违规）
  const agentStats = useMemo(() => {
    if (!dag || !Array.isArray(dag.nodes)) return [];
    const m = new Map();
    for (const n of dag.nodes) {
      const key = String(n.agent || '').split('.')[0] || 'unknown';
      m.set(key, (m.get(key) || 0) + 1);
    }
    return Array.from(m.entries()).sort((a, b) => b[1] - a[1]);
  }, [dag]);

  if (!dag || !Array.isArray(dag.nodes) || dag.nodes.length === 0) {
    return null;
  }

  return (
    <div style={{
      background: 'linear-gradient(180deg, #F0F9FF 0%, #FFFFFF 100%)',
      border: '1px solid #BAE6FD', borderRadius: 10, padding: 12,
      boxShadow: '0 1px 3px rgba(0,0,0,0.04)',
    }}>
      <div
        onClick={() => setExpanded(e => !e)}
        style={{
          display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer',
          marginBottom: expanded ? 12 : 0, userSelect: 'none',
        }}
      >
        {expanded ? <DownOutlined style={{ fontSize: 11, color: '#0369A1' }} /> :
                    <RightOutlined style={{ fontSize: 11, color: '#0369A1' }} />}
        <span style={{ fontWeight: 700, color: '#0369A1', fontSize: 13 }}>
          🗒️ 共享黑板·因果 DAG
        </span>
        <span style={{ color: '#0C4A6E', fontSize: 12 }}>
          · {dag.nodes.length} 节点 / {(dag.edges || []).length} 边
        </span>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 4 }}>
          {agentStats.slice(0, 6).map(([k, v]) => (
            <Tag key={k} color={agentColor(k)} style={{ fontSize: 11, margin: 0 }}>
              {k}·{v}
            </Tag>
          ))}
        </div>
      </div>

      {expanded && (
        <div ref={containerRef}>
          {/* 图区 */}
          <div style={{
            background: '#FFFFFF', borderRadius: 6, border: '1px solid #E0F2FE',
            position: 'relative', overflow: 'hidden',
          }}>
            <ForceGraph2D
              ref={fgRef}
              graphData={data}
              width={size.w}
              height={size.h}
              nodeId="id"
              linkSource="source"
              linkTarget="target"
              linkDirectionalArrowLength={6}
              linkDirectionalArrowRelPos={1}
              linkColor={() => '#94A3B8'}
              linkWidth={1.2}
              nodeRelSize={6}
              cooldownTicks={80}
              nodeCanvasObject={(node, ctx, globalScale) => {
                const r = 8;
                const label = `${node.id}·${node.label}`;
                ctx.beginPath();
                ctx.arc(node.x, node.y, r, 0, 2 * Math.PI);
                ctx.fillStyle = node._color;
                ctx.fill();
                ctx.strokeStyle = '#0F172A';
                ctx.lineWidth = 0.5;
                ctx.stroke();
                // 文字
                const fontSize = 11 / Math.max(1, globalScale);
                ctx.font = `${fontSize}px sans-serif`;
                ctx.textAlign = 'center';
                ctx.textBaseline = 'top';
                ctx.fillStyle = '#0F172A';
                ctx.fillText(label, node.x, node.y + r + 1);
              }}
              nodePointerAreaPaint={(node, color, ctx) => {
                ctx.fillStyle = color;
                ctx.beginPath();
                ctx.arc(node.x, node.y, 12, 0, 2 * Math.PI);
                ctx.fill();
              }}
              onNodeHover={n => setHoveredNode(n)}
            />

            {/* hover 详情卡 */}
            {hoveredNode && (
              <div style={{
                position: 'absolute', right: 8, top: 8, maxWidth: 320,
                background: 'rgba(15,23,42,0.92)', color: '#F8FAFC',
                padding: '8px 10px', borderRadius: 6, fontSize: 12,
                boxShadow: '0 2px 8px rgba(0,0,0,0.2)', pointerEvents: 'none',
              }}>
                <div style={{ marginBottom: 4 }}>
                  <Tag color={agentColor(hoveredNode.agent)} style={{ fontSize: 11 }}>
                    v{hoveredNode.id} · {hoveredNode.agent}
                  </Tag>
                </div>
                <div style={{ fontWeight: 600, marginBottom: 4 }}>{hoveredNode.label}</div>
                <div style={{ opacity: 0.85, lineHeight: 1.5 }}>
                  {hoveredNode.preview || '（无预览）'}
                </div>
              </div>
            )}
          </div>

          {/* 节点列表（折叠备份，方便没图形化时也能看） */}
          <div style={{ marginTop: 10, fontSize: 12, color: '#475569' }}>
            <Text strong>事件序列：</Text>{' '}
            {dag.nodes.slice(0, 16).map((n) => (
              <Tooltip key={n.id} title={`${n.agent} · ${n.preview || n.label}`}>
                <Tag color={agentColor(n.agent)}
                     style={{ fontSize: 11, marginInline: 2, cursor: 'help' }}>
                  v{n.id}·{n.label}
                </Tag>
              </Tooltip>
            ))}
            {dag.nodes.length > 16 && <span>… 共 {dag.nodes.length} 条</span>}
          </div>
        </div>
      )}
    </div>
  );
}
