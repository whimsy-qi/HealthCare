/**
 * 健康档案编辑页 — 弥补之前 /onboarding 路由空跳的产品级 bug。
 *
 * 设计原则：
 *  - 一屏分 5 个 section 卡片（基础信息 / 生活方式 / 过敏史 / 慢病史 / 手术&疫苗）
 *  - select / chip 组合式输入，避免大段裸 textarea
 *  - 顶栏 "返回" 不丢已填字段；底部 "保存档案" 调用 api.saveProfile
 *  - 兼容首次填写（profile_data=null）与编辑回填两种场景
 *
 * 后端契约：
 *   POST /api/profile  body={profile_data: {...}}  返回 {status:"success"}
 *   GET  /api/profile  → {profile_data: {...} | null}
 */
import React, { useEffect, useState, useCallback } from 'react';
import { useNavigate } from 'react-router';
import { toast } from 'sonner';
import {
  ArrowLeft, Save, User, Activity, Heart, AlertTriangle, Pill,
  Plus, X, CheckCircle, Shield,
} from 'lucide-react';
import { api } from '../lib/api';

// ─── 设计 token（与 ProfilePage 风格统一） ─────────────────────────
const T = {
  teal50:  '#edfaf2', teal100: '#d4f5df', teal200: '#afeebf',
  teal400: '#4eba78', teal500: '#32a05f',
  teal600: '#228048', teal700: '#166035',
  slate50:  '#f4fbf6', slate100: '#edf5ef', slate200: '#d8ead9',
  slate300: '#b8ccba', slate400: '#90a892', slate500: '#637065',
  slate600: '#465049', slate700: '#313830', slate800: '#1e2420', slate900: '#0e120f',
  red50: '#fef0f2', red200: '#fecaca', red500: '#e06870', red600: '#b84850',
  amber50: '#fef8e6', amber600: '#a88028',
};

// ─── Profile schema 选项库 ─────────────────────────────────────────
const GENDER_OPTIONS = ['男', '女', '其他'];
const DIET_OPTIONS = ['均衡饮食', '素食为主', '高蛋白', '低碳水', '其他'];
const EXERCISE_OPTIONS = ['每周3次以上', '每周1-2次', '偶尔运动', '几乎不运动'];
const SLEEP_OPTIONS = ['规律且充足', '偶尔熬夜/失眠', '经常熬夜/失眠'];
const SMOKING_OPTIONS = ['不吸烟', '偶尔吸烟', '长期吸烟'];
const DRINKING_OPTIONS = ['不饮酒', '偶尔饮酒', '经常饮酒'];

const COMMON_ALLERGIES = ['青霉素', '头孢类', '磺胺类', '阿司匹林', '海鲜', '坚果', '花粉', '尘螨', '乳制品', '鸡蛋'];
const COMMON_DISEASES = ['高血压', '糖尿病', '冠心病', '脑卒中', '慢性肾病', '哮喘', '甲状腺疾病', '高脂血症', '脂肪肝', '骨质疏松'];
const COMMON_VACCINES = ['乙肝', '甲肝', '流感', 'HPV', '新冠', '肺炎', '带状疱疹'];

// ─── 子组件：选择/取消的 chip ─────────────────────────────────────
const Chip: React.FC<{ label: string; active: boolean; onToggle: () => void; variant?: 'teal' | 'red' }> = ({ label, active, onToggle, variant = 'teal' }) => {
  const colors = variant === 'red'
    ? { bg: active ? T.red50 : 'white', border: active ? '#fecaca' : T.slate200, color: active ? T.red600 : T.slate500 }
    : { bg: active ? T.teal50 : 'white', border: active ? T.teal200 : T.slate200, color: active ? T.teal700 : T.slate500 };
  return (
    <button type="button" onClick={onToggle} style={{
      padding: '6px 12px', borderRadius: 16, fontSize: 12, fontWeight: 600,
      background: colors.bg, border: `1.5px solid ${colors.border}`, color: colors.color,
      cursor: 'pointer', transition: 'all 0.15s',
    }}>
      {active && '✓ '}{label}
    </button>
  );
};

// ─── 子组件：自定义 tag 输入（按 Enter 添加） ────────────────────
const TagInput: React.FC<{ tags: string[]; onChange: (next: string[]) => void; placeholder: string }> = ({ tags, onChange, placeholder }) => {
  const [val, setVal] = useState('');
  const [composing, setComposing] = useState(false);
  const add = () => {
    const t = val.trim();
    if (!t || tags.includes(t)) return;
    onChange([...tags, t]);
    setVal('');
  };
  return (
    <div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 8 }}>
        {tags.map(t => (
          <span key={t} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, padding: '4px 10px', borderRadius: 14, background: T.amber50, border: `1px solid #fde68a`, color: T.amber600, fontSize: 12, fontWeight: 600 }}>
            {t}
            <button type="button" onClick={() => onChange(tags.filter(x => x !== t))} style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0, color: T.amber600, display: 'flex', alignItems: 'center' }}>
              <X size={11} />
            </button>
          </span>
        ))}
      </div>
      <div style={{ display: 'flex', gap: 6 }}>
        <input
          value={val}
          onChange={e => setVal(e.target.value)}
          onCompositionStart={() => setComposing(true)}
          onCompositionEnd={() => setComposing(false)}
          onKeyDown={e => { if (e.key === 'Enter' && !composing) { e.preventDefault(); add(); } }}
          placeholder={placeholder}
          style={{
            flex: 1, padding: '8px 12px', borderRadius: 10,
            border: `1.5px solid ${T.slate200}`, fontSize: 13, color: T.slate800,
            outline: 'none',
          }}
        />
        <button type="button" onClick={add} disabled={!val.trim()} style={{
          padding: '0 14px', borderRadius: 10, border: 'none',
          background: val.trim() ? T.teal500 : T.slate200,
          color: val.trim() ? 'white' : T.slate400,
          cursor: val.trim() ? 'pointer' : 'not-allowed', fontSize: 12, fontWeight: 700,
          display: 'flex', alignItems: 'center', gap: 4,
        }}>
          <Plus size={13} /> 添加
        </button>
      </div>
    </div>
  );
};

// ─── 子组件：Section Card ─────────────────────────────────────────
const Section: React.FC<{ title: string; icon: React.ReactNode; accentColor: string; children: React.ReactNode }> = ({ title, icon, accentColor, children }) => (
  <div style={{ background: 'white', border: `1px solid ${T.slate200}`, borderRadius: 16, padding: '20px 24px', marginBottom: 14 }}>
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16 }}>
      <div style={{ width: 4, height: 18, borderRadius: 4, background: accentColor }} />
      <span style={{ color: accentColor }}>{icon}</span>
      <span style={{ fontSize: 15, fontWeight: 700, color: T.slate900 }}>{title}</span>
    </div>
    {children}
  </div>
);

// ─── 表单 Field 通用包装 ─────────────────────────────────────────
const Field: React.FC<{ label: string; required?: boolean; children: React.ReactNode }> = ({ label, required, children }) => (
  <div style={{ marginBottom: 14 }}>
    <div style={{ fontSize: 12, fontWeight: 600, color: T.slate600, marginBottom: 6 }}>
      {label}{required && <span style={{ color: T.red500, marginLeft: 3 }}>*</span>}
    </div>
    {children}
  </div>
);

const Select: React.FC<{ value: string; onChange: (v: string) => void; options: string[]; placeholder?: string }> = ({ value, onChange, options, placeholder = '请选择' }) => (
  <select value={value} onChange={e => onChange(e.target.value)} style={{
    width: '100%', padding: '10px 12px', borderRadius: 10,
    border: `1.5px solid ${T.slate200}`, fontSize: 13, color: T.slate800,
    background: 'white', outline: 'none', cursor: 'pointer',
  }}>
    <option value="">{placeholder}</option>
    {options.map(o => <option key={o} value={o}>{o}</option>)}
  </select>
);

const NumberInput: React.FC<{ value: any; onChange: (v: number | '') => void; placeholder?: string; suffix?: string; min?: number; max?: number }> = ({ value, onChange, placeholder, suffix, min, max }) => (
  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
    <input
      type="number"
      value={value ?? ''}
      onChange={e => {
        const v = e.target.value;
        onChange(v === '' ? '' : Number(v));
      }}
      placeholder={placeholder}
      min={min} max={max}
      style={{
        flex: 1, padding: '10px 12px', borderRadius: 10,
        border: `1.5px solid ${T.slate200}`, fontSize: 13, color: T.slate800,
        outline: 'none',
      }}
    />
    {suffix && <span style={{ fontSize: 12, color: T.slate500, fontWeight: 600 }}>{suffix}</span>}
  </div>
);

// ─── 主页面 ─────────────────────────────────────────────────────
export const OnboardingPage: React.FC = () => {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  // 基础信息
  const [name, setName] = useState('');
  const [gender, setGender] = useState('');
  const [age, setAge] = useState<number | ''>('');
  const [height, setHeight] = useState<number | ''>('');
  const [weight, setWeight] = useState<number | ''>('');

  // 生活方式
  const [diet, setDiet] = useState('');
  const [exercise, setExercise] = useState('');
  const [sleep, setSleep] = useState('');
  const [smoking, setSmoking] = useState('');
  const [drinking, setDrinking] = useState('');

  // 过敏 / 慢病 / 疫苗
  const [allergiesCommon, setAllergiesCommon] = useState<string[]>([]);
  const [allergiesCustom, setAllergiesCustom] = useState<string[]>([]);
  const [diseasesCommon, setDiseasesCommon] = useState<string[]>([]);
  const [diseasesCustom, setDiseasesCustom] = useState<string[]>([]);
  const [vaccinesCommon, setVaccinesCommon] = useState<string[]>([]);
  const [vaccinesCustom, setVaccinesCustom] = useState<string[]>([]);

  // 手术（{name, date}[]）
  const [surgeries, setSurgeries] = useState<Array<{ name: string; date: string }>>([]);

  // 拉取已有档案回填
  useEffect(() => {
    let mounted = true;
    (async () => {
      try {
        const res: any = await api.getProfile();
        let p = res?.profile_data;
        if (typeof p === 'string') {
          try { p = JSON.parse(p); } catch { p = {}; }
        }
        if (!mounted) return;
        if (p && typeof p === 'object') {
          setName(p.name || '');
          setGender(p.gender || '');
          setAge(p.age ?? '');
          setHeight(p.height ?? '');
          setWeight(p.weight ?? '');
          setDiet(p.diet || '');
          setExercise(p.exercise || '');
          setSleep(p.sleep || '');
          setSmoking(p.smoking || '');
          setDrinking(p.drinking || '');
          setAllergiesCommon(p.allergies_common || []);
          setAllergiesCustom(p.allergies_custom || []);
          setDiseasesCommon(p.past_diseases_common || []);
          setDiseasesCustom(p.past_diseases_custom || []);
          setVaccinesCommon(p.vaccines_common || []);
          setVaccinesCustom(p.vaccines_custom || []);
          setSurgeries(Array.isArray(p.surgeries) ? p.surgeries.map((s: any) =>
            typeof s === 'string' ? { name: s, date: '' } : { name: s.name || '', date: s.date || '' }
          ) : []);
        }
      } catch (e: any) {
        // 401 已被全局拦截器跳转；其他错误温柔提示
        if (e?.message && !e.message.includes('登录已失效')) {
          toast.error(`加载档案失败：${e.message}`);
        }
      } finally {
        if (mounted) setLoading(false);
      }
    })();
    return () => { mounted = false; };
  }, []);

  const toggle = (arr: string[], setter: (a: string[]) => void, item: string) => {
    setter(arr.includes(item) ? arr.filter(x => x !== item) : [...arr, item]);
  };

  const handleSave = useCallback(async () => {
    // 最低限度的校验
    if (!gender) { toast.warning('请选择性别'); return; }
    if (!age || age < 1 || age > 120) { toast.warning('请填写有效年龄（1-120）'); return; }
    if (!height || height < 50 || height > 250) { toast.warning('请填写有效身高（50-250cm）'); return; }
    if (!weight || weight < 20 || weight > 300) { toast.warning('请填写有效体重（20-300kg）'); return; }

    const profileData: Record<string, any> = {
      name: name.trim(),
      gender, age, height, weight,
      diet, exercise, sleep, smoking, drinking,
      allergies_common: allergiesCommon,
      allergies_custom: allergiesCustom,
      past_diseases_common: diseasesCommon,
      past_diseases_custom: diseasesCustom,
      vaccines_common: vaccinesCommon,
      vaccines_custom: vaccinesCustom,
      surgeries: surgeries.filter(s => s.name.trim()),
    };

    setSaving(true);
    try {
      await api.saveProfile(profileData);
      toast.success('健康档案保存成功！');
      // 触发 ProfilePage 重新拉档案 — 通过 navigate 自然刷新
      navigate('/profile', { replace: true });
    } catch (e: any) {
      toast.error(`保存失败：${e?.message || '请稍后重试'}`);
    } finally {
      setSaving(false);
    }
  }, [name, gender, age, height, weight, diet, exercise, sleep, smoking, drinking,
      allergiesCommon, allergiesCustom, diseasesCommon, diseasesCustom,
      vaccinesCommon, vaccinesCustom, surgeries, navigate]);

  if (loading) {
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: T.slate50 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, color: T.teal600, fontSize: 14, fontWeight: 600 }}>
          <div style={{ width: 18, height: 18, border: `2.5px solid ${T.teal100}`, borderTopColor: T.teal500, borderRadius: '50%', animation: 'spin360 0.8s linear infinite' }} />
          正在加载您的健康档案…
        </div>
        <style>{`@keyframes spin360 { to { transform: rotate(360deg); } }`}</style>
      </div>
    );
  }

  return (
    <div style={{
      minHeight: '100vh', background: T.slate50,
      fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    }}>
      {/* 顶栏 */}
      <div style={{
        position: 'sticky', top: 0, zIndex: 10,
        background: 'white', borderBottom: `1px solid ${T.slate200}`,
        padding: '14px 20px', display: 'flex', alignItems: 'center', gap: 14,
      }}>
        <button onClick={() => navigate(-1)} style={{
          width: 36, height: 36, borderRadius: 10, background: T.slate100,
          border: 'none', cursor: 'pointer',
          display: 'flex', alignItems: 'center', justifyContent: 'center', color: T.slate600,
        }}>
          <ArrowLeft size={16} />
        </button>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 17, fontWeight: 800, color: T.slate900 }}>编辑健康档案</div>
          <div style={{ fontSize: 12, color: T.slate500, marginTop: 2 }}>完善信息后可获得更精准的 AI 建议</div>
        </div>
      </div>

      {/* 表单主体 */}
      <div style={{ maxWidth: 720, margin: '0 auto', padding: '20px 16px 100px' }}>
        {/* 基础信息 */}
        <Section title="基础信息" icon={<User size={15} />} accentColor={T.teal600}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
            <Field label="姓名（昵称）">
              <input value={name} onChange={e => setName(e.target.value)} placeholder="选填，便于个性化称呼"
                style={{ width: '100%', padding: '10px 12px', borderRadius: 10, border: `1.5px solid ${T.slate200}`, fontSize: 13, outline: 'none' }} />
            </Field>
            <Field label="性别" required>
              <Select value={gender} onChange={setGender} options={GENDER_OPTIONS} />
            </Field>
            <Field label="年龄" required>
              <NumberInput value={age} onChange={setAge} placeholder="例如 32" suffix="岁" min={1} max={120} />
            </Field>
            <Field label="身高" required>
              <NumberInput value={height} onChange={setHeight} placeholder="例如 175" suffix="cm" min={50} max={250} />
            </Field>
            <Field label="体重" required>
              <NumberInput value={weight} onChange={setWeight} placeholder="例如 68" suffix="kg" min={20} max={300} />
            </Field>
          </div>
        </Section>

        {/* 生活方式 */}
        <Section title="生活方式" icon={<Activity size={15} />} accentColor={T.teal500}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
            <Field label="饮食偏好"><Select value={diet} onChange={setDiet} options={DIET_OPTIONS} /></Field>
            <Field label="运动频次"><Select value={exercise} onChange={setExercise} options={EXERCISE_OPTIONS} /></Field>
            <Field label="睡眠状况"><Select value={sleep} onChange={setSleep} options={SLEEP_OPTIONS} /></Field>
            <Field label="吸烟"><Select value={smoking} onChange={setSmoking} options={SMOKING_OPTIONS} /></Field>
            <Field label="饮酒"><Select value={drinking} onChange={setDrinking} options={DRINKING_OPTIONS} /></Field>
          </div>
        </Section>

        {/* 过敏史 */}
        <Section title="过敏史" icon={<AlertTriangle size={15} />} accentColor={T.red500}>
          <Field label="常见过敏原（点击切换）">
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7 }}>
              {COMMON_ALLERGIES.map(item => (
                <Chip key={item} label={item} active={allergiesCommon.includes(item)} onToggle={() => toggle(allergiesCommon, setAllergiesCommon, item)} variant="red" />
              ))}
            </div>
          </Field>
          <Field label="其他过敏原（自定义）">
            <TagInput tags={allergiesCustom} onChange={setAllergiesCustom} placeholder="输入后按 Enter 添加，例如：芒果" />
          </Field>
        </Section>

        {/* 慢病史 */}
        <Section title="既往慢病" icon={<Heart size={15} />} accentColor="#a88028">
          <Field label="常见慢病（点击切换）">
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7 }}>
              {COMMON_DISEASES.map(item => (
                <Chip key={item} label={item} active={diseasesCommon.includes(item)} onToggle={() => toggle(diseasesCommon, setDiseasesCommon, item)} />
              ))}
            </div>
          </Field>
          <Field label="其他疾病（自定义）">
            <TagInput tags={diseasesCustom} onChange={setDiseasesCustom} placeholder="输入后按 Enter 添加" />
          </Field>
        </Section>

        {/* 手术史 + 疫苗 */}
        <Section title="手术与疫苗史" icon={<Shield size={15} />} accentColor={T.teal700}>
          <Field label="既往手术">
            {surgeries.length > 0 && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 8 }}>
                {surgeries.map((s, i) => (
                  <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px', borderRadius: 10, background: T.slate100, border: `1px solid ${T.slate200}` }}>
                    <input value={s.name} onChange={e => setSurgeries(arr => arr.map((x, j) => j === i ? { ...x, name: e.target.value } : x))}
                      placeholder="手术名称" style={{ flex: 1, padding: '6px 10px', borderRadius: 8, border: `1px solid ${T.slate200}`, fontSize: 13, outline: 'none', background: 'white' }} />
                    <input value={s.date} onChange={e => setSurgeries(arr => arr.map((x, j) => j === i ? { ...x, date: e.target.value } : x))}
                      placeholder="日期 (例 2023-05)" style={{ width: 130, padding: '6px 10px', borderRadius: 8, border: `1px solid ${T.slate200}`, fontSize: 13, outline: 'none', background: 'white' }} />
                    <button type="button" onClick={() => setSurgeries(arr => arr.filter((_, j) => j !== i))} style={{ width: 26, height: 26, borderRadius: '50%', background: T.red50, border: 'none', cursor: 'pointer', color: T.red500, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                      <X size={12} />
                    </button>
                  </div>
                ))}
              </div>
            )}
            <button type="button" onClick={() => setSurgeries(arr => [...arr, { name: '', date: '' }])} style={{
              padding: '8px 14px', borderRadius: 10, background: T.teal50, border: `1.5px dashed ${T.teal200}`,
              color: T.teal700, cursor: 'pointer', fontSize: 12, fontWeight: 700,
              display: 'inline-flex', alignItems: 'center', gap: 6,
            }}>
              <Plus size={13} /> 添加手术记录
            </button>
          </Field>
          <Field label="已接种疫苗（点击切换）">
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7 }}>
              {COMMON_VACCINES.map(item => (
                <Chip key={item} label={item} active={vaccinesCommon.includes(item)} onToggle={() => toggle(vaccinesCommon, setVaccinesCommon, item)} />
              ))}
            </div>
          </Field>
          <Field label="其他疫苗（自定义）">
            <TagInput tags={vaccinesCustom} onChange={setVaccinesCustom} placeholder="输入后按 Enter 添加" />
          </Field>
        </Section>
      </div>

      {/* 底部固定保存栏 */}
      <div style={{
        position: 'fixed', bottom: 0, left: 0, right: 0,
        background: 'white', borderTop: `1px solid ${T.slate200}`,
        padding: '12px 20px', display: 'flex', justifyContent: 'center', gap: 12,
        boxShadow: '0 -4px 16px rgba(0,0,0,0.04)',
      }}>
        <div style={{ maxWidth: 720, width: '100%', display: 'flex', gap: 10 }}>
          <button onClick={() => navigate(-1)} disabled={saving} style={{
            padding: '12px 24px', borderRadius: 12, border: `1.5px solid ${T.slate200}`,
            background: 'white', color: T.slate600, cursor: saving ? 'not-allowed' : 'pointer',
            fontSize: 14, fontWeight: 600, opacity: saving ? 0.6 : 1,
          }}>
            取消
          </button>
          <button onClick={handleSave} disabled={saving} style={{
            flex: 1, padding: '12px 24px', borderRadius: 12, border: 'none',
            background: saving ? T.slate200 : `linear-gradient(135deg, ${T.teal500}, ${T.teal700})`,
            color: saving ? T.slate400 : 'white',
            cursor: saving ? 'not-allowed' : 'pointer', fontSize: 14, fontWeight: 700,
            display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 7,
            boxShadow: saving ? 'none' : '0 4px 12px rgba(50,160,95,0.25)',
          }}>
            {saving ? (
              <>
                <div style={{ width: 16, height: 16, border: `2.5px solid rgba(255,255,255,0.4)`, borderTopColor: 'white', borderRadius: '50%', animation: 'spinSave 0.8s linear infinite' }} />
                保存中…
              </>
            ) : (
              <>
                <Save size={15} /> 保存档案
              </>
            )}
          </button>
        </div>
        <style>{`@keyframes spinSave { to { transform: rotate(360deg); } }`}</style>
      </div>
    </div>
  );
};
