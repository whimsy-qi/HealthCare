import React, { useState, useEffect, useRef, useMemo } from 'react';
import { useNavigate } from 'react-router-dom'; // 🌟 新增路由 Hook
import { Input, Button, Spin, Select, Slider, message, Tooltip, Drawer, Tag, Alert } from 'antd';
import { SearchOutlined, ShareAltOutlined, MedicineBoxOutlined, NodeIndexOutlined, ArrowLeftOutlined, RobotOutlined, BulbOutlined } from '@ant-design/icons';
import ForceGraph2D from 'react-force-graph-2d';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { apiUrl } from '../config/api';

const PALETTE = {
  teal: '#14B8A6',
  tealDeep: '#0F766E',
  tealSoft: '#5EEAD4',
  tealGhost: 'rgba(20, 184, 166, 0.10)',
  yellowGreen: '#afeebf',
  cream: '#f0eac1',
  mint: '#e0f5ee',
  textInk: '#0F172A',
  textSlate: '#334155',
  textMute: '#64748B',
  hairline: 'rgba(15, 118, 110, 0.10)',
  glass: 'rgba(255, 255, 255, 0.72)',
  glassThick: 'rgba(255, 255, 255, 0.85)',
  amber: '#F59E0B',
  amberSoft: 'rgba(245, 158, 11, 0.12)',
};

const PAGE_BACKGROUND = `
  radial-gradient(1200px 600px at 0% 0%, rgba(175, 238, 191, 0.55) 0%, transparent 60%),
  radial-gradient(1000px 500px at 100% 0%, rgba(240, 234, 193, 0.55) 0%, transparent 55%),
  radial-gradient(900px 600px at 50% 100%, rgba(224, 245, 238, 0.65) 0%, transparent 55%),
  linear-gradient(135deg, #f7fbf6 0%, #fbf7e8 50%, #effaf4 100%)
`;

const glassSurface = {
  background: PALETTE.glass,
  backdropFilter: 'blur(24px) saturate(160%)',
  WebkitBackdropFilter: 'blur(24px) saturate(160%)',
  border: `1px solid ${PALETTE.hairline}`,
  boxShadow: '0 16px 40px rgba(15, 118, 110, 0.07), 0 2px 8px rgba(15, 118, 110, 0.03)',
};

const GraphExplore = () => {
  const navigate = useNavigate(); // 🌟 初始化返回功能
  const [keyword, setKeyword] = useState('扁桃体炎');
  const [mainType, setMainType] = useState('Disease');
  const [targetTypes, setTargetTypes] = useState(['Symptom', 'Drug', 'Food', 'Check']);
  const [depth, setDepth] = useState(1); 

  const [graphData, setGraphData] = useState({ nodes: [], links: [] });
  const [loading, setLoading] = useState(false);
  const [dimensions, setDimensions] = useState({ width: window.innerWidth, height: window.innerHeight - 80 });
  const graphRef = useRef();

  // 🧠 实体归一化提示
  const [normalizedHint, setNormalizedHint] = useState(null); // { from, to, hint }

  // 🧹 截断提示（节点过多时）
  const [truncationInfo, setTruncationInfo] = useState(null); // { shown, original }

  // 🤖 AI 节点解读抽屉
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerNode, setDrawerNode] = useState(null);    // { name, label }
  const [drawerLoading, setDrawerLoading] = useState(false);
  const [drawerData, setDrawerData] = useState(null);    // 后端返回的 explanation 等

  // 🔥 Hover 高亮状态
  const [hoveredNodeId, setHoveredNodeId] = useState(null);

  // 📊 度数计算：节点大小 + 标签显隐 + 中心节点识别
  const { degreeMap, topLabelIds, centerNodeId, neighborMap } = useMemo(() => {
    const deg = {};
    const neigh = {};
    graphData.links.forEach((l) => {
      const s = typeof l.source === 'object' ? l.source.id : l.source;
      const t = typeof l.target === 'object' ? l.target.id : l.target;
      deg[s] = (deg[s] || 0) + 1;
      deg[t] = (deg[t] || 0) + 1;
      (neigh[s] = neigh[s] || new Set()).add(t);
      (neigh[t] = neigh[t] || new Set()).add(s);
    });
    // 中心节点 = 名字命中 keyword 且度数最高
    let centerId = null;
    let maxDeg = -1;
    graphData.nodes.forEach((n) => {
      if (n.name === keyword && (deg[n.id] || 0) > maxDeg) {
        maxDeg = deg[n.id] || 0;
        centerId = n.id;
      }
    });
    // 如果精确命中没有，退到包含关系
    if (!centerId) {
      graphData.nodes.forEach((n) => {
        if (n.name && n.name.includes(keyword) && (deg[n.id] || 0) > maxDeg) {
          maxDeg = deg[n.id] || 0;
          centerId = n.id;
        }
      });
    }
    // Top 25% 高度数节点
    const sorted = [...graphData.nodes].sort((a, b) => (deg[b.id] || 0) - (deg[a.id] || 0));
    const topN = Math.max(5, Math.ceil(sorted.length * 0.25));
    const topSet = new Set(sorted.slice(0, topN).map((n) => n.id));
    return { degreeMap: deg, topLabelIds: topSet, centerNodeId: centerId, neighborMap: neigh };
  }, [graphData, keyword]);

  useEffect(() => {
    const handleResize = () => setDimensions({ width: window.innerWidth, height: window.innerHeight - 80 });
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  // 🌟 物理引擎：结合基础力与“语义聚类风”
  useEffect(() => {
    if (graphRef.current && graphData.nodes.length > 0) {
      const fg = graphRef.current;
      const isSparse = graphData.nodes.length <= 15; 

      fg.d3Force('charge').strength(isSparse ? -400 : -250); 
      fg.d3Force('link').distance(isSparse ? 100 : 60).strength(1); 
      fg.d3Force('center').strength(0.1); 

      fg.d3Force('cluster', (alpha) => {
        graphData.nodes.forEach(node => {
          let targetAngle = null;
          if (node.label === 'Drug') targetAngle = -Math.PI / 4;      
          if (node.label === 'Symptom') targetAngle = Math.PI * 0.8;  
          if (node.label === 'Department') targetAngle = Math.PI / 3; 

          if (targetAngle !== null) {
            const r = isSparse ? 120 : 250; 
            const targetX = Math.cos(targetAngle) * r;
            const targetY = Math.sin(targetAngle) * r;
            
            const strength = 0.6 * alpha; 
            node.vx += (targetX - node.x) * strength;
            node.vy += (targetY - node.y) * strength;
          }
        });
      });

      fg.d3ReheatSimulation();
    }
  }, [graphData]); 

  // 🌟 1. 更新节点的全局映射颜色
  const getColorByLabel = (label) => {
    const colorMap = {
      'Disease': '#F98C53',     // 暖橘
      'Drug': '#ABD7FB',        // 柔蓝
      'Symptom': '#D2E0AA',     // 豆绿
      'Department': '#FCC419',  // 明黄
      'Food': '#F5A623',        // 琥珀
      'Check': '#7B68EE',       // 中紫
      'Producer': '#50C878',    // 翠绿
      'Cure': '#FF6B6B'         // 珊瑚红
    };
    return colorMap[label] || '#ADB5BD';
  };

  const getRelName = (type) => {
    const relMap = {
      'TREATS': '治疗', 'HAS_SYMPTOM': '伴随症状',
      'BELONGS_TO': '就诊科室', 'CONTRAINDICATED_FOR': '禁忌',
      'DO_EAT': '宜吃', 'NOT_EAT': '忌吃', 'RECOMMAND_EAT': '推荐食谱',
      'COMMON_DRUG': '常用药', 'RECOMMAND_DRUG': '推荐用药',
      'NEED_CHECK': '所需检查', 'ACOMPANY_WITH': '并发症',
      'CURE_WAY': '治疗方法', 'PRODUCED_BY': '生产厂商',
      'DRUGS_OF': '在售药品', 'DEPT_PARENT': '上级科室',
      'RELATED_TO': '相关', 'RELATIONSHIP': '关联',
      'HAS_SECTION': '文档章节', 'HAS_FIELD': '字段',
    };
    return relMap[type] || type;
  };

  const getLinkRelationship = (link) => link?.relationship || link?.type || link?.label || '';

  const getLinkDisplayName = (link) => link?.display_label || getRelName(getLinkRelationship(link));

  const formatNodeLabel = (name, maxLen = 14) => {
    const text = String(name || '').replace(/\s+/g, '');
    if (!text) return '';
    return text.length > maxLen ? `${text.slice(0, maxLen)}...` : text;
  };

  // 🌈 关系类型 → 颜色（与节点色系呼应：症状↔豆绿、药物↔柔蓝、科室↔明黄、疾病↔暖橘）
  const getRelColor = (type) => {
    const m = {
      'HAS_SYMPTOM': 'rgba(163, 191, 111, ALPHA)',
      'TREATS': 'rgba(96, 165, 250, ALPHA)',
      'BELONGS_TO': 'rgba(234, 179, 8, ALPHA)',
      'CONTRAINDICATED_FOR': 'rgba(239, 68, 68, ALPHA)',
      'DO_EAT': 'rgba(34, 197, 94, ALPHA)',
      'NOT_EAT': 'rgba(239, 68, 68, ALPHA)',
      'RECOMMAND_EAT': 'rgba(34, 197, 94, ALPHA)',
      'COMMON_DRUG': 'rgba(96, 165, 250, ALPHA)',
      'RECOMMAND_DRUG': 'rgba(96, 165, 250, ALPHA)',
      'NEED_CHECK': 'rgba(123, 104, 238, ALPHA)',
      'ACOMPANY_WITH': 'rgba(245, 158, 11, ALPHA)',
      'CURE_WAY': 'rgba(80, 200, 120, ALPHA)',
      'PRODUCED_BY': 'rgba(148, 163, 184, ALPHA)',
      'DRUGS_OF': 'rgba(148, 163, 184, ALPHA)',
      'DEPT_PARENT': 'rgba(234, 179, 8, ALPHA)',
    };
    return m[type] || 'rgba(148, 163, 184, ALPHA)';     // 默认灰
  };

  // 🌫️ 每种节点类型对应的光晕 RGB（无 alpha，调用时补上）
  const getHaloRgb = (label) => ({
    'Disease':    '249, 140, 83',   // 暖橘
    'Drug':       '171, 215, 251',  // 柔蓝
    'Symptom':    '210, 224, 170',  // 豆绿
    'Department': '252, 196, 25',   // 明黄
    'Food':       '245, 166, 35',   // 琥珀
    'Check':      '123, 104, 238',  // 中紫
    'Producer':   '80, 200, 120',   // 翠绿
    'Cure':       '255, 107, 107'   // 珊瑚红
  }[label] || '148, 163, 184');

  const fetchGraphData = async (searchWord = keyword, currentDepth = depth) => {
    if (!searchWord) return;
    setLoading(true);
    try {
      const token = localStorage.getItem('access_token');
      const targetTypesStr = targetTypes.length > 0 ? targetTypes.join(',') : '全部';
      const url = apiUrl(`/api/graph/search?keyword=${encodeURIComponent(searchWord)}&main_type=${mainType}&target_types=${targetTypesStr}&depth=${currentDepth}`);
      
      const res = await fetch(url, { headers: { 'Authorization': `Bearer ${token}` } });
      const json = await res.json();
      
      if (json.status === 'success') {
        const parsedNodes = json.data.nodes.map(n => ({ ...n, id: String(n.id) }));
        const validIds = new Set(parsedNodes.map(n => n.id));

        const parsedLinks = [];
        json.data.links.forEach(l => {
           const s = String(l.source);
           const t = String(l.target);
           if (validIds.has(s) && validIds.has(t)) {
              const relationship = l.relationship || l.type || l.label || '';
              parsedLinks.push({
                ...l,
                relationship,
                display_label: l.display_label || getRelName(relationship),
                source: s,
                target: t,
              });
           }
        });

        setGraphData({
            nodes: JSON.parse(JSON.stringify(parsedNodes)),
            links: JSON.parse(JSON.stringify(parsedLinks))
        });

        // 🧠 处理实体归一化提示
        if (json.normalized_from && json.actual_keyword && json.normalized_from !== json.actual_keyword) {
          setNormalizedHint({
            from: json.normalized_from,
            to: json.actual_keyword,
            hint: json.norm_hint || '',
          });
          setKeyword(json.actual_keyword);
        } else {
          setNormalizedHint(null);
        }

        // 🧹 处理节点截断提示
        if (json.truncated) {
          setTruncationInfo({ shown: json.shown_count, original: json.original_count });
        } else {
          setTruncationInfo(null);
        }

        setTimeout(() => {
            if (graphRef.current) {
               const padding = parsedNodes.length <= 15 ? 150 : 60;
               graphRef.current.zoomToFit(600, padding);
            }
        }, 500);
      }
    } catch (error) {
      console.error("图谱网络请求异常:", error);
      message.error("图谱连接失败，请检查网络");
    } finally {
      setLoading(false);
    }
  };

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { fetchGraphData(); }, []);

  // 🤖 打开 AI 节点解读抽屉
  const openNodeExplain = async (node) => {
    setDrawerNode({ name: node.name, label: node.label });
    setDrawerOpen(true);
    setDrawerLoading(true);
    setDrawerData(null);
    try {
      const token = localStorage.getItem('access_token');
      const params = new URLSearchParams({
        name: node.name || '',
        label: node.label || '',
      });
      if (node.id) params.set('node_id', node.id);
      const url = apiUrl(`/api/graph/explain?${params.toString()}`);
      const res = await fetch(url, { headers: { 'Authorization': `Bearer ${token}` } });
      const json = await res.json();
      if (json.status === 'success') {
        setDrawerData(json);
      } else {
        message.warning(json.explanation || '该节点暂无解读');
      }
    } catch (err) {
      console.error('节点解读失败:', err);
      message.error('AI 解读服务暂不可用');
    } finally {
      setDrawerLoading(false);
    }
  };


  const isSparseGraph = graphData.nodes.length <= 15;

  // 提取一个复用的组件用于下拉框和图例的圆点渲染
  const Dot = ({ color }) => (
    <span style={{ display: 'inline-block', width: 10, height: 10, borderRadius: '50%', background: color, marginRight: 8 }}></span>
  );

  return (
    <div style={{ width: '100vw', height: '100vh', background: PAGE_BACKGROUND, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      
      {/* 顶部工具栏 */}
      <div style={{ height: '80px', padding: '0 32px', background: PALETTE.glassThick, backdropFilter: 'blur(24px) saturate(160%)', WebkitBackdropFilter: 'blur(24px) saturate(160%)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderBottom: `1px solid ${PALETTE.hairline}`, zIndex: 10, boxShadow: '0 10px 28px rgba(15,118,110,0.07)' }}>
        <div style={{ display: 'flex', alignItems: 'center' }}>
            {/* 🌟 新增：返回按钮 */}
          <Button 
            type="text" 
            icon={<ArrowLeftOutlined />} 
            onClick={() => navigate(-1)} // 返回上一页 (即 Chat 页)
            style={{ width: 44, height: 44, marginRight: 16, borderRadius: '50%', background: PALETTE.glassThick, border: `1px solid ${PALETTE.hairline}`, boxShadow: '0 4px 12px rgba(15,118,110,0.08)', fontSize: '18px', color: PALETTE.tealDeep }}
          />
          <ShareAltOutlined style={{ fontSize: 26, color: PALETTE.tealDeep, marginRight: 16 }} />
          <h3 style={{ margin: 0, color: PALETTE.textInk, fontWeight: 800, fontSize: '20px', letterSpacing: '-0.2px' }}>全科医疗图谱分析</h3>
        </div>
        
        <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
          {/* 🌟 2. 更新主靶点下拉框的颜色圆点 */}
          <Select value={mainType} onChange={setMainType} style={{ width: 120 }}>
            <Select.Option value="全部">全部主体</Select.Option>
            <Select.Option value="Disease"><Dot color="#F98C53"/> 疾病</Select.Option>
            <Select.Option value="Symptom"><Dot color="#D2E0AA"/> 症状</Select.Option>
            <Select.Option value="Drug"><Dot color="#ABD7FB"/> 药物</Select.Option>
            <Select.Option value="Department"><Dot color="#FCC419"/> 科室</Select.Option>
            <Select.Option value="Food"><Dot color="#F5A623"/> 食物</Select.Option>
            <Select.Option value="Check"><Dot color="#7B68EE"/> 检查</Select.Option>
            <Select.Option value="Producer"><Dot color="#50C878"/> 厂商</Select.Option>
            <Select.Option value="Cure"><Dot color="#FF6B6B"/> 疗法</Select.Option>
          </Select>

          {/* 🌟 3. 更新关联维度下拉框的颜色圆点 */}
          <Select mode="multiple" allowClear placeholder="关联维度" value={targetTypes} onChange={setTargetTypes} style={{ minWidth: 190, maxWidth: 320 }} maxTagCount="responsive">
            <Select.Option value="Disease"><Dot color="#F98C53"/> 疾病</Select.Option>
            <Select.Option value="Symptom"><Dot color="#D2E0AA"/> 症状</Select.Option>
            <Select.Option value="Drug"><Dot color="#ABD7FB"/> 药物</Select.Option>
            <Select.Option value="Department"><Dot color="#FCC419"/> 科室</Select.Option>
            <Select.Option value="Food"><Dot color="#F5A623"/> 食物</Select.Option>
            <Select.Option value="Check"><Dot color="#7B68EE"/> 检查</Select.Option>
            <Select.Option value="Producer"><Dot color="#50C878"/> 厂商</Select.Option>
            <Select.Option value="Cure"><Dot color="#FF6B6B"/> 疗法</Select.Option>
          </Select>

          <div style={{ display: 'flex', alignItems: 'center', gap: '12px', padding: '0 10px' }}>
            <Tooltip title="探索深度：1度为直接关联，2-3度可发现间接联系">
                <span style={{ color: PALETTE.textMute, fontSize: '13px' }}><NodeIndexOutlined /> 深度</span>
            </Tooltip>
            <Slider min={1} max={3} value={depth} onChange={setDepth} style={{ width: 80, margin: '0 8px' }} trackStyle={{ backgroundColor: PALETTE.tealDeep }} handleStyle={{ borderColor: PALETTE.tealDeep }} />
            <span style={{ color: PALETTE.tealDeep, fontWeight: 'bold' }}>{depth}</span>
          </div>

          <Input placeholder="检索医疗实体..." value={keyword} onChange={(e) => setKeyword(e.target.value)} onPressEnter={() => fetchGraphData()} style={{ width: 200, borderRadius: '8px' }} prefix={<SearchOutlined style={{ color: PALETTE.textMute }} />} />
          <Button type="primary" onClick={() => fetchGraphData()} loading={loading} style={{ background: PALETTE.tealDeep, border: 'none', borderRadius: '8px', fontWeight: 600, boxShadow: '0 6px 16px rgba(15,118,110,0.18)' }}>
            智能扫描
          </Button>
        </div>
      </div>

      <div style={{ flex: 1, position: 'relative' }}>
        {/* 🧹 节点截断提示条 */}
        {truncationInfo && (
          <Alert
            showIcon
            type="warning"
            style={{ position: 'absolute', top: normalizedHint ? 72 : 16, left: '50%', transform: 'translateX(-50%)', zIndex: 20,
                     borderRadius: 10, boxShadow: '0 4px 12px rgba(0,0,0,0.08)', minWidth: 380 }}
            message={
              <span>
                已为您展示最相关的 <b style={{ color: PALETTE.amber }}>{truncationInfo.shown}</b> / {truncationInfo.original} 个节点
                <span style={{ color: PALETTE.textMute, marginLeft: 8 }}>· 节点过多会影响清晰度，建议减小探索深度</span>
              </span>
            }
            closable
            onClose={() => setTruncationInfo(null)}
          />
        )}

        {/* 🧠 实体归一化提示条 */}
        {normalizedHint && (
          <Alert
            showIcon
            type="info"
            icon={<BulbOutlined />}
            style={{ position: 'absolute', top: 16, left: '50%', transform: 'translateX(-50%)', zIndex: 20,
                     borderRadius: 10, boxShadow: '0 4px 12px rgba(0,0,0,0.08)', minWidth: 380 }}
            message={
              <span>
                未找到「<b>{normalizedHint.from}</b>」，已智能匹配为「<b style={{ color: PALETTE.tealDeep }}>{normalizedHint.to}</b>」
                {normalizedHint.hint && <span style={{ color: PALETTE.textMute, marginLeft: 8 }}>· {normalizedHint.hint}</span>}
              </span>
            }
            closable
            onClose={() => setNormalizedHint(null)}
          />
        )}

        {loading && (
          <div style={{ position: 'absolute', top: '50%', left: '50%', transform: 'translate(-50%, -50%)', zIndex: 10, textAlign: 'center' }}>
            <Spin size="large" />
            <div style={{ color: PALETTE.tealDeep, marginTop: 15, fontWeight: 500, textShadow: '0 1px 2px rgba(255,255,255,0.8)' }}>正在提取知识星系...</div>
          </div>
        )}

        {graphData.nodes.length === 0 && !loading ? (
          <div style={{ position: 'absolute', top: '40%', left: '50%', transform: 'translate(-50%, -50%)', textAlign: 'center', color: PALETTE.textMute }}>
            <MedicineBoxOutlined style={{ fontSize: 54, marginBottom: 16, opacity: 0.3 }} />
            <h3 style={{ color: PALETTE.textMute }}>未观测到关联数据</h3>
            <span style={{ color: PALETTE.textMute }}>尝试增加深度，或切换搜索词</span>
          </div>
        ) : (
          <ForceGraph2D
            ref={graphRef}
            width={dimensions.width}
            height={dimensions.height}
            graphData={graphData}
            nodeLabel="name"
            
            linkDirectionalArrowLength={isSparseGraph ? 5 : 3}
            linkDirectionalArrowRelPos={1}
            nodeRelSize={isSparseGraph ? 12 : 6}

            // 🌈 边语义分层：按关系类型上色，hover 高亮
            linkColor={(link) => {
              const s = typeof link.source === 'object' ? link.source.id : link.source;
              const t = typeof link.target === 'object' ? link.target.id : link.target;
              const isRelated = hoveredNodeId && (s === hoveredNodeId || t === hoveredNodeId);
              const alpha = isRelated ? 0.95 : (hoveredNodeId ? 0.08 : 0.28);
              return getRelColor(getLinkRelationship(link)).replace('ALPHA', alpha);
            }}
            linkWidth={(link) => {
              const s = typeof link.source === 'object' ? link.source.id : link.source;
              const t = typeof link.target === 'object' ? link.target.id : link.target;
              return hoveredNodeId && (s === hoveredNodeId || t === hoveredNodeId) ? 2.5 : 1.2;
            }}

            linkCanvasObjectMode={() => 'after'}
            linkCanvasObject={(link, ctx, globalScale) => {
              const start = link.source;
              const end = link.target;
              if (!start || !end || typeof start.x !== 'number' || typeof end.x !== 'number') return;

              // ✏️ 关系标签：只有 Hover 到相关边 或 放大到一定倍率才显示，否则画面太乱
              const s = typeof link.source === 'object' ? link.source.id : link.source;
              const t = typeof link.target === 'object' ? link.target.id : link.target;
              const isRelated = hoveredNodeId && (s === hoveredNodeId || t === hoveredNodeId);
              if (!isRelated && globalScale < 2.2) return;

              const label = getLinkDisplayName(link);
              const fontSize = Math.min(10 / globalScale, 4);

              const textPos = { x: start.x + (end.x - start.x) / 2, y: start.y + (end.y - start.y) / 2 };
              const relLink = { x: end.x - start.x, y: end.y - start.y };
              let textAngle = Math.atan2(relLink.y, relLink.x);
              if (textAngle > Math.PI / 2) textAngle = -(Math.PI - textAngle);
              if (textAngle < -Math.PI / 2) textAngle = -(-Math.PI - textAngle);

              ctx.save();
              ctx.translate(textPos.x, textPos.y);
              ctx.rotate(textAngle);
              ctx.font = `600 ${fontSize}px Inter, Sans-Serif`;
              ctx.textAlign = 'center';
              ctx.textBaseline = 'middle';

              const textWidth = ctx.measureText(label).width;
              ctx.fillStyle = isRelated ? 'rgba(255, 255, 255, 0.98)' : 'rgba(255, 255, 255, 0.85)';
              ctx.fillRect(-textWidth / 2 - 2, -fontSize / 2 - 1.5, textWidth + 4, fontSize + 3);

              ctx.fillStyle = isRelated ? '#0F766E' : '#94A3B8';
              ctx.fillText(label, 0, 0);
              ctx.restore();
            }}

            onNodeClick={(node) => {
                if (graphRef.current && typeof node.x === 'number') {
                    graphRef.current.centerAt(node.x, node.y, 800);
                    graphRef.current.zoom(isSparseGraph ? 2 : 4, 800);
                }
                // 🤖 打开 AI 解读抽屉
                openNodeExplain(node);
            }}

            onNodeRightClick={(node) => {
                // 右键：以该节点为中心重新搜索
                setKeyword(node.name);
                setMainType(node.label);
                fetchGraphData(node.name);
            }}
            
            onNodeHover={(node) => {
                document.body.style.cursor = node ? 'pointer' : 'default';
                setHoveredNodeId(node ? node.id : null);
            }}

            nodeCanvasObject={(node, ctx, globalScale) => {
              if (typeof node.x !== 'number' || typeof node.y !== 'number') return;

              const isCenter = node.id === centerNodeId;
              const isHovered = node.id === hoveredNodeId;
              const isNeighborOfHover = hoveredNodeId && neighborMap[hoveredNodeId]?.has(node.id);
              const isTopNode = topLabelIds.has(node.id);

              // 📐 节点半径：度数对数 + 疾病/中心加成
              const deg = degreeMap[node.id] || 0;
              const baseRadius = 3 + Math.log2(deg + 2) * 1.6;         // 度数对数缩放
              const labelBonus = node.label === 'Disease' ? 1.15 : 1;  // 疾病略大
              const centerBonus = isCenter ? 1.5 : 1;                  // 中心主角加成
              const hoverBonus = isHovered ? 1.25 : 1;
              const radiusMultiplier = isSparseGraph ? 1.5 : 1;
              const nodeRadius = baseRadius * labelBonus * centerBonus * hoverBonus * radiusMultiplier;

              // ✨ 中心节点 + Hover 节点：同色系外发光光环（跟随节点颜色）
              if (isCenter || isHovered) {
                const haloRgb = getHaloRgb(node.label);
                ctx.beginPath();
                ctx.arc(node.x, node.y, nodeRadius + 5, 0, 2 * Math.PI, false);
                ctx.fillStyle = `rgba(${haloRgb}, 0.35)`;
                ctx.fill();
                // 第二层更外更淡
                ctx.beginPath();
                ctx.arc(node.x, node.y, nodeRadius + 11, 0, 2 * Math.PI, false);
                ctx.fillStyle = `rgba(${haloRgb}, 0.14)`;
                ctx.fill();
              }

              // 🎯 主节点圆盘
              const isDimmed = hoveredNodeId && !isHovered && !isNeighborOfHover && !isCenter;
              ctx.beginPath();
              ctx.arc(node.x, node.y, nodeRadius, 0, 2 * Math.PI, false);
              ctx.globalAlpha = isDimmed ? 0.25 : 1;
              ctx.fillStyle = getColorByLabel(node.label);
              ctx.fill();

              ctx.lineWidth = (isCenter ? 2.2 : 1.5) / globalScale;
              // 中心节点描边也跟随节点色系（取节点色加深）
              ctx.strokeStyle = isCenter
                ? `rgba(${getHaloRgb(node.label)}, 0.95)`
                : '#FFFFFF';
              ctx.stroke();
              ctx.globalAlpha = 1;

              // 🔤 标签智能显隐
              // 显示规则：中心节点 | Hover 节点 | Hover 邻居 | Top 节点 | 放大到 ≥1.4 时全部显示
              const shouldShowLabel =
                isCenter || isHovered || isNeighborOfHover || isTopNode || globalScale >= 1.4;
              if (!shouldShowLabel) return;

              const label = formatNodeLabel(node.name, isCenter ? 18 : (isHovered ? 16 : 12));
              const baseFontSize = isCenter ? 16 : (isHovered ? 14 : (isTopNode ? 12 : 11));
              const maxFontSize = isSparseGraph ? 10 : 7;
              const fontSize = Math.min(baseFontSize / globalScale, maxFontSize);
              ctx.font = `${isCenter ? '700' : (isTopNode ? '600' : '500')} ${fontSize}px Inter, Sans-Serif`;

              // 标签文字：浅灰为主，中心节点用节点色（更柔和，不抢眼）
              ctx.textAlign = 'center';
              ctx.textBaseline = 'middle';
              const textWidth = ctx.measureText(label).width;
              const textY = node.y + nodeRadius + fontSize * 0.9;

              // 只在低倍缩放 或 中心/Hover/Top 时绘制半透明白底，高缩放纯文字更干净
              const needsBg = globalScale < 2.2 || isCenter || isHovered || isTopNode;
              if (needsBg) {
                const padX = isCenter ? 3 : 1.5;
                const padY = isCenter ? 2 : 1;
                ctx.fillStyle = isCenter ? 'rgba(255, 255, 255, 0.88)' : 'rgba(255, 255, 255, 0.78)';
                ctx.fillRect(node.x - textWidth / 2 - padX, textY - fontSize / 2 - padY, textWidth + padX * 2, fontSize + padY * 2);
              }

              // 文字颜色：中心用节点同色系深一度，Hover 用 slate-500，其他全部浅灰
              if (isCenter) {
                const [r, g, b] = getHaloRgb(node.label).split(',').map(s => parseInt(s.trim()));
                // 把节点色压深 40% 作为文字色
                ctx.fillStyle = `rgb(${Math.floor(r*0.55)}, ${Math.floor(g*0.55)}, ${Math.floor(b*0.55)})`;
              } else if (isHovered) {
                ctx.fillStyle = '#64748B';
              } else if (isDimmed) {
                ctx.fillStyle = '#CBD5E1';
              } else {
                ctx.fillStyle = '#94A3B8';   // slate-400 浅灰
              }
              ctx.fillText(label, node.x, textY);
            }}

            nodePointerAreaPaint={(node, color, ctx) => {
              // 扩大点击热区到 12px，避免小节点难点
              if (typeof node.x !== 'number') return;
              ctx.fillStyle = color;
              ctx.beginPath();
              ctx.arc(node.x, node.y, 12, 0, 2 * Math.PI, false);
              ctx.fill();
            }}
          />
        )}

        {/* 🌟 4. 更新右下角悬浮图例的颜色圆点 */}
        <div style={{ position: 'absolute', bottom: 32, right: 32, ...glassSurface, borderRadius: 18, padding: '16px 20px', display: 'flex', flexDirection: 'column', gap: '10px' }}>
            <span style={{ color: PALETTE.textSlate, fontSize: '13px', fontWeight: 'bold' }}>图谱节点说明</span>
            <div style={{ display: 'flex', alignItems: 'center' }}><Dot color="#F98C53"/><span style={{ color: '#334155', fontSize: '13px' }}>疾病</span></div>
            <div style={{ display: 'flex', alignItems: 'center' }}><Dot color="#ABD7FB"/><span style={{ color: '#334155', fontSize: '13px' }}>药物</span></div>
            <div style={{ display: 'flex', alignItems: 'center' }}><Dot color="#D2E0AA"/><span style={{ color: '#334155', fontSize: '13px' }}>症状</span></div>
            <div style={{ display: 'flex', alignItems: 'center' }}><Dot color="#FCC419"/><span style={{ color: '#334155', fontSize: '13px' }}>科室</span></div>
            <div style={{ display: 'flex', alignItems: 'center' }}><Dot color="#F5A623"/><span style={{ color: '#334155', fontSize: '13px' }}>食物</span></div>
            <div style={{ display: 'flex', alignItems: 'center' }}><Dot color="#7B68EE"/><span style={{ color: '#334155', fontSize: '13px' }}>检查</span></div>
            <div style={{ display: 'flex', alignItems: 'center' }}><Dot color="#50C878"/><span style={{ color: '#334155', fontSize: '13px' }}>厂商</span></div>
            <div style={{ display: 'flex', alignItems: 'center' }}><Dot color="#FF6B6B"/><span style={{ color: '#334155', fontSize: '13px' }}>疗法</span></div>
            <div style={{ marginTop: 4, paddingTop: 10, borderTop: `1px solid ${PALETTE.hairline}` }}>
                <span style={{ color: PALETTE.textMute, fontSize: '11px' }}>💡 左键：AI 解读 · 右键：以此节点重新搜索</span>
            </div>
        </div>
      </div>

      {/* 🤖 AI 节点解读抽屉 */}
      <Drawer
        title={
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <div style={{ width: 36, height: 36, borderRadius: '50%',
                          background: `linear-gradient(135deg, ${PALETTE.teal} 0%, ${PALETTE.tealDeep} 100%)`,
                          display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <RobotOutlined style={{ color: '#fff', fontSize: 18 }} />
            </div>
            <div>
              <div style={{ fontWeight: 700, fontSize: 16 }}>AI 节点解读</div>
              <div style={{ fontSize: 12, color: PALETTE.textMute }}>基于图谱关联 + LLM 推理</div>
            </div>
          </div>
        }
        placement="right"
        width={460}
        onClose={() => setDrawerOpen(false)}
        open={drawerOpen}
      >
        {drawerNode && (
          <div style={{ marginBottom: 16 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
              <span style={{ display: 'inline-block', width: 10, height: 10, borderRadius: '50%',
                             background: getColorByLabel(drawerNode.label) }} />
              <span style={{ fontSize: 22, fontWeight: 700, color: PALETTE.textInk }}>{drawerNode.name}</span>
            </div>
            <Tag color="geekblue">{
              { Disease: '疾病', Symptom: '症状', Drug: '药物', Department: '科室', Food: '食物', Check: '检查', Producer: '厂商', Cure: '疗法' }[drawerNode.label] || drawerNode.label || '实体'
            }</Tag>
            {drawerData?.neighbor_count !== undefined && (
              <Tag color="green">{drawerData.neighbor_count} 个一跳邻居</Tag>
            )}
          </div>
        )}

        {drawerLoading ? (
          <div style={{ textAlign: 'center', padding: '40px 0' }}>
            <Spin />
            <div style={{ color: PALETTE.tealDeep, marginTop: 12 }}>AI 正在分析图谱关联...</div>
          </div>
        ) : drawerData ? (
          <>
            <div style={{ background: PALETTE.tealGhost, border: `1px solid ${PALETTE.hairline}`, borderRadius: 16,
                          padding: '16px 18px', fontSize: 14, lineHeight: 1.75, color: PALETTE.textInk }}>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {drawerData.explanation}
              </ReactMarkdown>
            </div>

            {drawerData.neighbor_buckets && Object.keys(drawerData.neighbor_buckets).length > 0 && (
              <div style={{ marginTop: 20 }}>
                <div style={{ fontSize: 13, color: PALETTE.textMute, marginBottom: 8, fontWeight: 600 }}>
                  📎 图谱关联邻居
                </div>
                {Object.entries(drawerData.neighbor_buckets).map(([k, arr]) => (
                  <div key={k} style={{ marginBottom: 10 }}>
                    <div style={{ fontSize: 12, color: PALETTE.textMute, marginBottom: 4 }}>{getRelName(k)}（{arr.length}）</div>
                    <div>
                      {arr.slice(0, 12).map((nm) => (
                        <Tag key={nm} style={{ marginBottom: 4, cursor: 'pointer' }}
                             onClick={() => {
                               setKeyword(nm);
                               fetchGraphData(nm);
                               setDrawerOpen(false);
                             }}>
                          {nm}
                        </Tag>
                      ))}
                      {arr.length > 12 && <span style={{ fontSize: 12, color: PALETTE.textMute }}>+{arr.length - 12}...</span>}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </>
        ) : (
          <div style={{ color: PALETTE.textMute, textAlign: 'center', padding: '40px 0' }}>暂无数据</div>
        )}
      </Drawer>
    </div>
  );
};

export default GraphExplore;
