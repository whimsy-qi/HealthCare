/**
 * 医疗知识图谱可视化页（PC + 移动端共用）
 *
 * 数据流：
 *   1. 进入页面 → api.graphPopular()  → 渲染热门"疾病/药物"快捷按钮
 *   2. 用户输入关键词 / 点击热门 → api.graphSearch({keyword, main_type, depth})
 *      返回 {nodes, links, normalized_from, norm_hint, truncated, ...}
 *   3. 渲染 react-force-graph-2d 中心辐射图 + 右侧详情面板
 *   4. 点击节点 → api.graphExplain(name, label) → LLM 生成 200 字科普卡片
 *
 * 后端契约：
 *   GET /api/graph/popular        → {diseases:[{name,degree,label}], drugs:[]}
 *   GET /api/graph/search?keyword → {nodes:[{id,name,label}], links:[{source,target,relationship}], ...}
 *   GET /api/graph/explain?name   → {status, explanation:Markdown}
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  ArrowLeft, Search, Sparkles, X, Activity,
  GitBranch, AlertCircle, Loader2, Pill, Stethoscope,
  RefreshCw, Info,
} from 'lucide-react';
import ForceGraph2D from 'react-force-graph-2d';
import { api } from '../lib/api';

// ─── 设计 token ─────────────────────────────────────
const T = {
  bg: '#f4fbf6', panel: '#ffffff',
  border: '#d8ead9', borderLight: '#edf5ef',
  text: '#1e2420', textMuted: '#637065', textDim: '#90a892',
  mint50: '#edfaf2', mint100: '#d4f5df', mint200: '#afeebf',
  mint400: '#4eba78', mint500: '#32a05f', mint600: '#228048', mint700: '#166035',
  // 节点颜色（按 label）
  diseaseColor:    '#7C3AED',
  symptomColor:    '#0891B2',
  drugColor:       '#DC2626',
  departmentColor: '#D97706',
};

const NODE_COLOR: Record<string, string> = {
  Disease: T.diseaseColor,
  Symptom: T.symptomColor,
  Drug: T.drugColor,
  Department: T.departmentColor,
};
const NODE_LABEL_CN: Record<string, string> = {
  Disease: '疾病', Symptom: '症状', Drug: '药物', Department: '科室',
};
const NODE_ICON: Record<string, React.ReactNode> = {
  Disease: <Activity size={13} />, Symptom: <AlertCircle size={13} />,
  Drug: <Pill size={13} />, Department: <Stethoscope size={13} />,
};

// 关系英文 → 中文
const REL_CN: Record<string, string> = {
  HAS_SYMPTOM: '症状表现',
  TREATS: '治疗',
  CONTRAINDICATED_FOR: '禁用于',
  BELONGS_TO: '所属科室',
};

interface GraphNode {
  id: string;
  name: string;
  label: string;
  // ForceGraph 内部还会塞 x/y/vx/vy
}
interface GraphLink {
  source: string | GraphNode;
  target: string | GraphNode;
  relationship?: string;
}

const GraphPage: React.FC = () => {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  // —— 数据状态 ——
  const [popular, setPopular] = useState<{ diseases: any[]; drugs: any[] }>({ diseases: [], drugs: [] });
  const [keyword, setKeyword] = useState('');
  const [mainType, setMainType] = useState<'全部' | 'Disease' | 'Symptom' | 'Drug' | 'Department'>('全部');
  const [depth, setDepth] = useState<number>(1);
  const [graphData, setGraphData] = useState<{ nodes: GraphNode[]; links: GraphLink[] }>({ nodes: [], links: [] });
  const [loading, setLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState('');
  const [searchMeta, setSearchMeta] = useState<{ truncated?: boolean; original?: number; shown?: number; norm?: { from: string; to: string; hint?: string } | null }>({});

  // 选中节点 + 解读
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
  const [explainMd, setExplainMd] = useState('');
  const [explainLoading, setExplainLoading] = useState(false);

  // ForceGraph ref（用来调 d3-force / 缩放）
  const fgRef = useRef<any>(null);

  // 容器尺寸自适应
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [size, setSize] = useState({ w: 800, h: 600 });
  useEffect(() => {
    const update = () => {
      if (!containerRef.current) return;
      const r = containerRef.current.getBoundingClientRect();
      setSize({ w: Math.max(320, r.width), h: Math.max(380, r.height) });
    };
    update();
    window.addEventListener('resize', update);
    return () => window.removeEventListener('resize', update);
  }, []);

  // 进页加载热门
  useEffect(() => {
    api.graphPopular(8).then(setPopular).catch(() => { /* 静默 */ });
    // 支持 URL 参数 ?q=高血压 直接跳进图谱
    const qp = searchParams.get('q');
    if (qp) {
      setKeyword(qp);
      // 延后一帧执行搜索（state 还没赋值前）
      setTimeout(() => doSearch(qp), 50);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 调整 d3-force 让节点更分散（中心引力适当 + 充足斥力）
  useEffect(() => {
    if (!fgRef.current || graphData.nodes.length === 0) return;
    try {
      const fg = fgRef.current;
      fg.d3Force('charge')?.strength(-260);
      fg.d3Force('link')?.distance(75).strength(1);
      fg.d3Force('center')?.strength(0.06);
      fg.d3ReheatSimulation?.();
      // 自动 fit 到画布
      setTimeout(() => fg.zoomToFit?.(400, 60), 700);
    } catch { /* 静默 */ }
  }, [graphData]);

  const doSearch = useCallback(async (kwOverride?: string) => {
    const kw = (kwOverride ?? keyword).trim();
    if (!kw) return;
    setLoading(true);
    setErrorMsg('');
    setSelectedNode(null);
    setExplainMd('');
    try {
      const r: any = await api.graphSearch({
        keyword: kw,
        main_type: mainType,
        depth,
      });
      if (r?.status === 'success') {
        const rawNodes: GraphNode[] = (r.data?.nodes || []).map((n: any) => ({
          id: String(n.id), name: String(n.name || ''), label: String(n.label || ''),
        }));
        const rawLinks: GraphLink[] = (r.data?.links || []).map((l: any) => ({
          source: String(l.source), target: String(l.target),
          relationship: l.relationship,
        }));
        setGraphData({ nodes: rawNodes, links: rawLinks });
        setSearchMeta({
          truncated: !!r.truncated,
          original: r.original_count,
          shown: r.shown_count,
          norm: (r.normalized_from && r.actual_keyword && r.normalized_from !== r.actual_keyword)
            ? { from: r.normalized_from, to: r.actual_keyword, hint: r.norm_hint }
            : null,
        });
        if (rawNodes.length === 0) setErrorMsg(`图谱中暂无与「${kw}」相关的节点`);
      } else {
        setErrorMsg('查询失败');
      }
    } catch (e: any) {
      setErrorMsg(e?.message || '网络异常');
    } finally {
      setLoading(false);
    }
  }, [keyword, mainType, depth]);

  const handleNodeClick = useCallback(async (node: any) => {
    if (!node) return;
    setSelectedNode({ id: node.id, name: node.name, label: node.label });
    // 中心化 + 放大该节点
    if (fgRef.current) {
      fgRef.current.centerAt(node.x, node.y, 600);
      fgRef.current.zoom(2.2, 600);
    }
    // 拉解读
    setExplainLoading(true);
    setExplainMd('');
    try {
      const r: any = await api.graphExplain(node.name, node.label);
      setExplainMd(r?.explanation || r?.markdown || '');
    } catch (e: any) {
      setExplainMd(`> ⚠️ 暂无解读：${e?.message || '请稍后再试'}`);
    } finally {
      setExplainLoading(false);
    }
  }, []);

  // 节点 canvas 自定义绘制（带颜色 + 文字）
  const nodeCanvasObject = useCallback((node: any, ctx: CanvasRenderingContext2D, globalScale: number) => {
    const r = (node.label === 'Disease' ? 8 : 6) + Math.min(2, (node.__degree || 0) / 4);
    ctx.beginPath();
    ctx.arc(node.x, node.y, r, 0, 2 * Math.PI, false);
    ctx.fillStyle = NODE_COLOR[node.label] || '#94a3b8';
    ctx.fill();
    ctx.lineWidth = selectedNode?.id === node.id ? 2.5 : 1.2;
    ctx.strokeStyle = selectedNode?.id === node.id ? '#0F172A' : 'rgba(255,255,255,0.85)';
    ctx.stroke();
    // 文字标签
    const fontSize = Math.max(10, 12 / globalScale);
    ctx.font = `${fontSize}px -apple-system, "PingFang SC", sans-serif`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillStyle = T.text;
    ctx.fillText(node.name, node.x, node.y + r + fontSize * 0.9);
  }, [selectedNode]);

  // 关系标签绘制（中文）
  const linkCanvasObject = useCallback((link: any, ctx: CanvasRenderingContext2D, globalScale: number) => {
    const start = link.source;
    const end = link.target;
    if (!start || !end || typeof start.x !== 'number') return;
    // 默认线条
    ctx.beginPath();
    ctx.moveTo(start.x, start.y);
    ctx.lineTo(end.x, end.y);
    ctx.strokeStyle = 'rgba(99,112,101,0.32)';
    ctx.lineWidth = 1.0;
    ctx.stroke();
    // 中点标签（仅缩放足够大时画出来，避免糊成一团）
    if (globalScale > 1.5 && link.relationship) {
      const mid = { x: (start.x + end.x) / 2, y: (start.y + end.y) / 2 };
      const text = REL_CN[link.relationship] || link.relationship;
      const fs = Math.max(7, 9 / globalScale);
      ctx.font = `${fs}px sans-serif`;
      ctx.fillStyle = 'rgba(70,80,73,0.85)';
      ctx.textAlign = 'center';
      ctx.fillText(text, mid.x, mid.y);
    }
  }, []);

  // 计算节点度数（用于尺寸）
  useMemo(() => {
    if (!graphData.nodes.length) return;
    const deg: Record<string, number> = {};
    graphData.links.forEach((l: any) => {
      const s = typeof l.source === 'string' ? l.source : l.source.id;
      const t = typeof l.target === 'string' ? l.target : l.target.id;
      deg[s] = (deg[s] || 0) + 1;
      deg[t] = (deg[t] || 0) + 1;
    });
    graphData.nodes.forEach((n: any) => { n.__degree = deg[n.id] || 0; });
  }, [graphData]);

  // 类型筛选 chips
  const TYPE_CHIPS = [
    { value: '全部',       label: '全部',   color: T.mint600 },
    { value: 'Disease',    label: '疾病',   color: T.diseaseColor },
    { value: 'Symptom',    label: '症状',   color: T.symptomColor },
    { value: 'Drug',       label: '药物',   color: T.drugColor },
    { value: 'Department', label: '科室',   color: T.departmentColor },
  ] as const;

  return (
    <div style={{
      minHeight: '100vh', width: '100vw', display: 'flex', flexDirection: 'column',
      background: T.bg, fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    }}>
      {/* 顶栏 */}
      <div style={{
        flexShrink: 0, background: 'white', borderBottom: `1px solid ${T.border}`,
        padding: '12px 20px', display: 'flex', alignItems: 'center', gap: 14,
      }}>
        <button onClick={() => navigate(-1)} style={{
          width: 36, height: 36, borderRadius: 10, background: T.borderLight,
          border: 'none', cursor: 'pointer',
          display: 'flex', alignItems: 'center', justifyContent: 'center', color: T.textMuted,
        }}>
          <ArrowLeft size={16} />
        </button>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flex: 1 }}>
          <div style={{ width: 36, height: 36, borderRadius: 10,
            background: `linear-gradient(135deg, ${T.mint400}, ${T.mint600})`,
            display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'white' }}>
            <GitBranch size={16} />
          </div>
          <div>
            <div style={{ fontSize: 16, fontWeight: 800, color: T.text }}>医疗知识图谱</div>
            <div style={{ fontSize: 11, color: T.textDim, marginTop: 1 }}>实时检索 · 关系拓扑 · AI 解读</div>
          </div>
        </div>
        {graphData.nodes.length > 0 && (
          <div style={{
            fontSize: 11, color: T.mint700, padding: '5px 12px', borderRadius: 8,
            background: T.mint50, border: `1px solid ${T.mint200}`, fontWeight: 700,
          }}>
            {graphData.nodes.length} 节点 · {graphData.links.length} 关系
          </div>
        )}
      </div>

      {/* 搜索栏 + 筛选 */}
      <div style={{
        flexShrink: 0, background: 'white', borderBottom: `1px solid ${T.border}`,
        padding: '12px 20px', display: 'flex', flexDirection: 'column', gap: 10,
      }}>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <div style={{
            flex: 1, display: 'flex', alignItems: 'center', gap: 10,
            background: T.bg, border: `1.5px solid ${T.border}`, borderRadius: 12,
            padding: '9px 14px',
          }}>
            <Search size={15} color={T.textDim} />
            <input
              value={keyword}
              onChange={e => setKeyword(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') doSearch(); }}
              placeholder="输入疾病/症状/药物名（如：高血压、咳嗽、阿司匹林）..."
              style={{
                flex: 1, border: 'none', background: 'none', outline: 'none',
                fontSize: 14, color: T.text, minWidth: 0,
              }}
            />
            {keyword && (
              <button onClick={() => setKeyword('')} style={{
                width: 22, height: 22, borderRadius: '50%', background: T.borderLight,
                border: 'none', cursor: 'pointer', color: T.textMuted,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}><X size={11} /></button>
            )}
          </div>
          <button onClick={() => doSearch()} disabled={!keyword.trim() || loading} style={{
            padding: '10px 20px', borderRadius: 12, border: 'none',
            background: (!keyword.trim() || loading) ? T.borderLight
                       : `linear-gradient(135deg, ${T.mint500}, ${T.mint700})`,
            color: (!keyword.trim() || loading) ? T.textDim : 'white',
            cursor: (!keyword.trim() || loading) ? 'not-allowed' : 'pointer',
            fontSize: 14, fontWeight: 700,
            display: 'flex', alignItems: 'center', gap: 6,
            boxShadow: (!keyword.trim() || loading) ? 'none' : '0 4px 12px rgba(50,160,95,0.25)',
          }}>
            {loading ? <Loader2 size={14} className="rfg-spin" /> : <Search size={14} />}
            探索图谱
          </button>
        </div>

        {/* 类型筛选 chips */}
        <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
          <span style={{ fontSize: 11, color: T.textDim, fontWeight: 600 }}>中心节点类型：</span>
          {TYPE_CHIPS.map(c => {
            const active = mainType === c.value;
            return (
              <button key={c.value} onClick={() => setMainType(c.value as any)} style={{
                padding: '4px 12px', borderRadius: 14, fontSize: 11.5, fontWeight: 600,
                background: active ? `${c.color}15` : 'white',
                border: `1.5px solid ${active ? c.color : T.border}`,
                color: active ? c.color : T.textMuted, cursor: 'pointer',
                transition: 'all 0.15s',
              }}>{c.label}</button>
            );
          })}
          <span style={{ fontSize: 11, color: T.textDim, fontWeight: 600, marginLeft: 14 }}>探索深度：</span>
          {[1, 2].map(d => (
            <button key={d} onClick={() => setDepth(d)} style={{
              padding: '4px 12px', borderRadius: 14, fontSize: 11.5, fontWeight: 600,
              background: depth === d ? T.mint50 : 'white',
              border: `1.5px solid ${depth === d ? T.mint400 : T.border}`,
              color: depth === d ? T.mint700 : T.textMuted, cursor: 'pointer',
            }}>{d} 跳</button>
          ))}
        </div>

        {/* 归一化提示 */}
        {searchMeta.norm && (
          <div style={{
            display: 'flex', alignItems: 'center', gap: 8,
            padding: '6px 12px', borderRadius: 10,
            background: '#fef8e6', border: '1px solid #fde68a', color: '#a88028',
            fontSize: 12,
          }}>
            <Sparkles size={12} />
            <span>系统已将「{searchMeta.norm.from}」语义归一化为「{searchMeta.norm.to}」{searchMeta.norm.hint ? `（${searchMeta.norm.hint}）` : ''}</span>
          </div>
        )}
        {searchMeta.truncated && (
          <div style={{
            display: 'flex', alignItems: 'center', gap: 8,
            padding: '6px 12px', borderRadius: 10,
            background: T.mint50, border: `1px solid ${T.mint200}`, color: T.mint700,
            fontSize: 12,
          }}>
            <Info size={12} />
            <span>结果包含 {searchMeta.original} 个节点，已智能截取最相关的 {searchMeta.shown} 个展示</span>
          </div>
        )}
      </div>

      {/* 主体：左侧画布 + 右侧详情 */}
      <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
        {/* 画布区 */}
        <div ref={containerRef} style={{ flex: 1, position: 'relative', minWidth: 0, background: 'white' }}>
          {graphData.nodes.length > 0 ? (
            <ForceGraph2D
              ref={fgRef}
              graphData={graphData as any}
              width={size.w}
              height={size.h}
              nodeRelSize={6}
              backgroundColor="#ffffff"
              nodeCanvasObject={nodeCanvasObject as any}
              linkCanvasObjectMode={() => 'replace'}
              linkCanvasObject={linkCanvasObject as any}
              onNodeClick={handleNodeClick}
              cooldownTicks={120}
              warmupTicks={50}
              enableNodeDrag
              minZoom={0.3}
              maxZoom={5}
            />
          ) : (
            <div style={{
              position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column',
              alignItems: 'center', justifyContent: 'center', padding: 40,
            }}>
              {loading ? (
                <>
                  <Loader2 size={32} className="rfg-spin" color={T.mint500} />
                  <div style={{ fontSize: 14, color: T.textMuted, marginTop: 12, fontWeight: 600 }}>
                    正在检索 1024 维向量空间...
                  </div>
                </>
              ) : errorMsg ? (
                <div style={{ textAlign: 'center', maxWidth: 480 }}>
                  <AlertCircle size={28} color="#a88028" style={{ marginBottom: 10 }} />
                  <div style={{ fontSize: 14, color: T.textMuted, fontWeight: 600 }}>{errorMsg}</div>
                </div>
              ) : (
                <div style={{ textAlign: 'center', maxWidth: 540 }}>
                  <div style={{
                    width: 84, height: 84, margin: '0 auto 16px', borderRadius: 26,
                    background: `linear-gradient(135deg, ${T.mint300 || T.mint200}, ${T.mint500})`,
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    boxShadow: '0 6px 20px rgba(50,160,95,0.18)',
                  }}>
                    <GitBranch size={36} color="white" />
                  </div>
                  <div style={{ fontSize: 18, fontWeight: 800, color: T.text, marginBottom: 6 }}>
                    探索医疗知识图谱
                  </div>
                  <div style={{ fontSize: 13, color: T.textMuted, lineHeight: 1.7, marginBottom: 20 }}>
                    我们的图谱涵盖<strong style={{ color: T.mint700 }}>疾病、症状、药物、科室</strong> 4 类实体，
                    通过<strong style={{ color: T.mint700 }}>症状表现 / 治疗 / 禁用 / 所属科室</strong> 等关系连接。
                    输入关键词或选择下方热门话题开始探索。
                  </div>

                  {/* 热门 chip */}
                  {(popular.diseases.length > 0 || popular.drugs.length > 0) && (
                    <div style={{ marginTop: 14 }}>
                      {popular.diseases.length > 0 && (
                        <div style={{ marginBottom: 12 }}>
                          <div style={{ fontSize: 11, color: T.textDim, fontWeight: 700, marginBottom: 8, textAlign: 'left' }}>
                            🔥 热门疾病
                          </div>
                          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, justifyContent: 'center' }}>
                            {popular.diseases.slice(0, 8).map(d => (
                              <button key={d.name}
                                onClick={() => { setKeyword(d.name); setMainType('Disease'); doSearch(d.name); }}
                                style={{
                                  padding: '5px 13px', borderRadius: 16, fontSize: 12, fontWeight: 600,
                                  background: 'white', border: `1.5px solid ${T.diseaseColor}40`,
                                  color: T.diseaseColor, cursor: 'pointer',
                                  display: 'inline-flex', alignItems: 'center', gap: 4,
                                }}>
                                <Activity size={11} />
                                {d.name}
                                <span style={{ fontSize: 9, color: T.textDim, fontWeight: 500 }}>·{d.degree}</span>
                              </button>
                            ))}
                          </div>
                        </div>
                      )}
                      {popular.drugs.length > 0 && (
                        <div>
                          <div style={{ fontSize: 11, color: T.textDim, fontWeight: 700, marginBottom: 8, textAlign: 'left' }}>
                            💊 常用药物
                          </div>
                          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, justifyContent: 'center' }}>
                            {popular.drugs.slice(0, 6).map(m => (
                              <button key={m.name}
                                onClick={() => { setKeyword(m.name); setMainType('Drug'); doSearch(m.name); }}
                                style={{
                                  padding: '5px 13px', borderRadius: 16, fontSize: 12, fontWeight: 600,
                                  background: 'white', border: `1.5px solid ${T.drugColor}40`,
                                  color: T.drugColor, cursor: 'pointer',
                                  display: 'inline-flex', alignItems: 'center', gap: 4,
                                }}>
                                <Pill size={11} />
                                {m.name}
                              </button>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* 图例（右下角悬浮） */}
          {graphData.nodes.length > 0 && (
            <div style={{
              position: 'absolute', right: 16, bottom: 16, padding: '10px 14px',
              background: 'rgba(255,255,255,0.94)', borderRadius: 12,
              border: `1px solid ${T.border}`, backdropFilter: 'blur(8px)',
              boxShadow: '0 4px 16px rgba(0,0,0,0.06)',
              display: 'flex', flexDirection: 'column', gap: 5,
            }}>
              <div style={{ fontSize: 10, color: T.textDim, fontWeight: 700, marginBottom: 2 }}>节点类型</div>
              {Object.entries(NODE_LABEL_CN).map(([k, v]) => (
                <div key={k} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <div style={{
                    width: 10, height: 10, borderRadius: '50%',
                    background: NODE_COLOR[k], border: '1.5px solid white',
                    boxShadow: '0 0 0 1px rgba(0,0,0,0.06)',
                  }} />
                  <span style={{ fontSize: 11, color: T.textMuted, fontWeight: 600 }}>{v}</span>
                </div>
              ))}
            </div>
          )}

          {/* 重置视图按钮 */}
          {graphData.nodes.length > 0 && (
            <button
              onClick={() => fgRef.current?.zoomToFit?.(400, 80)}
              title="重置视图"
              style={{
                position: 'absolute', right: 16, top: 16, width: 36, height: 36,
                borderRadius: 10, background: 'white', border: `1px solid ${T.border}`,
                cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center',
                color: T.textMuted, boxShadow: '0 2px 8px rgba(0,0,0,0.05)',
              }}>
              <RefreshCw size={15} />
            </button>
          )}
        </div>

        {/* 右侧详情面板（仅在选中节点时显示） */}
        {selectedNode && (
          <div style={{
            width: 360, flexShrink: 0,
            background: 'white', borderLeft: `1px solid ${T.border}`,
            display: 'flex', flexDirection: 'column', overflowY: 'auto',
            animation: 'rfg-slide-left 0.2s ease',
          }}>
            <div style={{
              padding: '16px 20px', borderBottom: `1px solid ${T.borderLight}`,
              display: 'flex', alignItems: 'center', gap: 10,
            }}>
              <div style={{
                width: 36, height: 36, borderRadius: 10,
                background: `${NODE_COLOR[selectedNode.label] || '#94a3b8'}15`,
                color: NODE_COLOR[selectedNode.label] || '#94a3b8',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}>
                {NODE_ICON[selectedNode.label] || <GitBranch size={13} />}
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 16, fontWeight: 800, color: T.text,
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {selectedNode.name}
                </div>
                <div style={{ fontSize: 11, color: T.textDim, marginTop: 2, fontWeight: 600 }}>
                  {NODE_LABEL_CN[selectedNode.label] || selectedNode.label}
                </div>
              </div>
              <button onClick={() => setSelectedNode(null)} style={{
                width: 28, height: 28, borderRadius: '50%', background: T.borderLight,
                border: 'none', cursor: 'pointer', color: T.textMuted,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}><X size={13} /></button>
            </div>

            <div style={{ flex: 1, padding: '16px 20px', minHeight: 0 }}>
              <div style={{ fontSize: 11, color: T.textDim, fontWeight: 700, marginBottom: 8,
                display: 'flex', alignItems: 'center', gap: 5 }}>
                <Sparkles size={11} /> AI 科普解读
              </div>
              {explainLoading ? (
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '20px 0', color: T.textMuted, fontSize: 13 }}>
                  <Loader2 size={14} className="rfg-spin" />
                  正在生成解读...
                </div>
              ) : explainMd ? (
                <div className="graph-explain-md" style={{ fontSize: 13, color: T.text, lineHeight: 1.75 }}>
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{explainMd}</ReactMarkdown>
                </div>
              ) : (
                <div style={{ fontSize: 12, color: T.textDim, fontStyle: 'italic' }}>
                  点击节点会自动加载 AI 解读
                </div>
              )}
            </div>

            <div style={{ padding: '12px 20px', borderTop: `1px solid ${T.borderLight}` }}>
              <button onClick={() => navigate(`/chat?q=${encodeURIComponent(selectedNode.name)}`)} style={{
                width: '100%', padding: '10px 14px', borderRadius: 12,
                background: `linear-gradient(135deg, ${T.mint500}, ${T.mint700})`,
                color: 'white', border: 'none', cursor: 'pointer',
                fontSize: 13, fontWeight: 700,
                display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
              }}>
                <Stethoscope size={13} />
                就此节点继续问 AI
              </button>
            </div>
          </div>
        )}
      </div>

      <style>{`
        @keyframes rfg-spin { to { transform: rotate(360deg); } }
        .rfg-spin { animation: rfg-spin 0.9s linear infinite; }
        @keyframes rfg-slide-left { from { transform: translateX(40px); opacity: 0; } to { transform: translateX(0); opacity: 1; } }
        .graph-explain-md h1, .graph-explain-md h2, .graph-explain-md h3 {
          font-size: 14px; font-weight: 700; margin: 8px 0 4px; color: ${T.text};
        }
        .graph-explain-md p { margin: 4px 0; }
        .graph-explain-md ul, .graph-explain-md ol { padding-left: 20px; margin: 4px 0; }
        .graph-explain-md strong { color: ${T.mint700}; }
        .graph-explain-md blockquote {
          border-left: 3px solid ${T.mint200};
          padding: 4px 12px; margin: 6px 0;
          background: ${T.mint50}; border-radius: 0 6px 6px 0;
          color: ${T.mint700};
        }
      `}</style>
    </div>
  );
};

export { GraphPage };
export default GraphPage;
