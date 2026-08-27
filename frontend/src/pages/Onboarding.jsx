import React, { useState, useEffect } from 'react';
import { 
  Button, Form, Typography, Radio, Select, InputNumber, Space, 
  message, Steps, Divider, Checkbox, Row, Col, ConfigProvider, DatePicker, Tag 
} from 'antd';
import { 
  ArrowLeftOutlined,
  RightOutlined, 
  CheckCircleOutlined,
  FastForwardOutlined,
  PlusOutlined,
  DeleteOutlined
} from '@ant-design/icons';
import { useLocation, useNavigate } from 'react-router-dom';
import dayjs from 'dayjs';
import { apiUrl } from '../config/api';

const { Title, Text } = Typography;

const SUG_DICTIONARIES = {
  diseases: [
    { value: '心脏病' }, { value: '心律失常' }, { value: '心力衰竭' }, { value: '心肌梗死' },
    { value: '脑梗死' }, { value: '脑出血' }, { value: '甲亢' }, { value: '甲减' },
    { value: '慢性胃炎' }, { value: '消化性溃疡' }, { value: '抑郁症' }, { value: '焦虑症' }
  ],
  surgeries: [
    { value: '阑尾切除术' }, { value: '胆囊切除术' }, { value: '剖宫产术' }, 
    { value: '甲状腺切除术' }, { value: '白内障手术' }, { value: '扁桃体摘除术' }
  ],
  allergies: [
    { value: '阿莫西林' }, { value: '破伤风抗毒素' }, { value: '磺胺类' }, 
    { value: '尘螨' }, { value: '芒果' }, { value: '猕猴桃' }, { value: '酒精' }
  ],
  vaccines: [
    { value: '新冠疫苗' }, { value: '带状疱疹疫苗' }, { value: '轮状病毒疫苗' }, 
    { value: '甲肝疫苗' }, { value: '戊肝疫苗' }
  ]
};

const Onboarding = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const [form] = Form.useForm();
  
  const [currentStep, setCurrentStep] = useState(0);
  const [loading, setLoading] = useState(false);
  const fromProfile = new URLSearchParams(location.search).get('from') === 'profile';
  
  const gender = Form.useWatch('gender', form) || '男';
  const obstetricStatus = Form.useWatch('obstetric_status', form) || '无';

  const [historyToggles, setHistoryToggles] = useState({
    disease: '无', surgery: '无', allergy: '无', vaccine: '无'
  });

  useEffect(() => {
    const fetchProfile = async () => {
      const token = localStorage.getItem('access_token');
      if (!token) return;

      try {
        const response = await fetch(apiUrl('/api/profile'), {
          headers: { 'Authorization': `Bearer ${token}` }
        });
        const data = await response.json();
        
        if (data && data.profile_data) {
          let parsedData = data.profile_data;
          if (typeof parsedData === 'string') {
            try { 
              parsedData = JSON.parse(parsedData); 
            } catch (e) { 
              console.warn("档案解析错误:", e);
              parsedData = {}; 
            }
          }
          
          setHistoryToggles({
            disease: (parsedData?.past_diseases_common?.length || parsedData?.past_diseases_custom?.length) ? '有' : '无',
            surgery: (parsedData?.surgeries?.length) ? '有' : '无',
            allergy: (parsedData?.allergies_common?.length || parsedData?.allergies_custom?.length) ? '有' : '无',
            vaccine: (parsedData?.vaccines_common?.length || parsedData?.vaccines_custom?.length) ? '有' : '无'
          });

          if (parsedData?.due_date) parsedData.due_date = dayjs(parsedData.due_date);
          if (parsedData?.lactation_start_date) parsedData.lactation_start_date = dayjs(parsedData.lactation_start_date);
          
          if (Array.isArray(parsedData?.surgeries)) {
            parsedData.surgeries = parsedData.surgeries.map(s => ({
              ...s,
              date: s.date ? dayjs(s.date) : null
            }));
          }

          form.setFieldsValue(parsedData);
        }
      } catch (error) {
        console.error('回填档案失败:', error);
      }
    };
    fetchProfile();
  }, [form]);

  const handleToggle = (key, value) => {
    setHistoryToggles(prev => ({ ...prev, [key]: value }));
  };

  const handleSkipOrFinish = async (values = form.getFieldsValue()) => {
    setLoading(true);
    const token = localStorage.getItem('access_token');
    
    try {
      if (token) {
        await fetch(apiUrl('/api/profile'), {
          method: 'POST',
          headers: {
            'Authorization': `Bearer ${token}`, 
            'Content-Type': 'application/json'
          },
          body: JSON.stringify({ profile_data: values })
        });
      } else {
        localStorage.setItem('patient_profile', JSON.stringify(values));
      }
      
      message.success(fromProfile ? '健康档案已更新' : '档案已云端同步！正在为您开启诊疗室...');
      setTimeout(() => navigate(fromProfile ? '/profile' : '/chat'), 1000);
    } catch (error) {
      console.error("保存失败:", error); 
      message.error('保存失败，请检查网络');
    } finally {
      setLoading(false);
    }
  };

  const next = async () => {
    try {
      if (currentStep === 0) {
        await form.validateFields(['gender', 'age']);
        setCurrentStep(gender === '男' ? 2 : 1);
      } else if (currentStep === 1) {
        setCurrentStep(2);
      }
    } catch (error) {
      console.warn("表单校验未通过:", error); 
      message.warning('请先完善必填信息');
    }
  };

  const prev = () => {
    if (currentStep === 2) {
      setCurrentStep(gender === '男' ? 0 : 1);
    } else {
      setCurrentStep(0);
    }
  };

  const customTagRender = (props) => {
    const { label, closable, onClose } = props;
    return (
      <Tag color="#E6F4F1" closable={closable} onClose={onClose} style={{ fontSize: '12px', fontWeight: 500, color: '#0F172A', padding: '2px 8px', borderRadius: '4px', margin: '4px 4px 4px 0', border: '1px solid #CCFBF1' }}>
        {label}
      </Tag>
    );
  };

  const renderLabel = (text) => (
    <span style={{ fontWeight: 700, color: '#1E293B', fontSize: '16px' }}>{text}</span>
  );

  const renderToggleRow = (label, toggleKey) => (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: historyToggles[toggleKey] === '有' ? 12 : 24, paddingBottom: 12, borderBottom: '1px dashed #E2E8F0' }}>
      {renderLabel(label)}
      <Radio.Group optionType="button" buttonStyle="solid" value={historyToggles[toggleKey]} onChange={(e) => handleToggle(toggleKey, e.target.value)}>
        <Radio.Button value="有" style={{ width: 60, textAlign: 'center' }}>有</Radio.Button>
        <Radio.Button value="无" style={{ width: 60, textAlign: 'center' }}>无</Radio.Button>
      </Radio.Group>
    </div>
  );

  return (
    <ConfigProvider theme={{ token: { colorPrimary: '#14B8A6', colorInfo: '#14B8A6' } }}>
      <div style={{ display: 'flex', height: '100vh', width: '100vw', background: '#F8FAFC', justifyContent: 'center', alignItems: 'center', position: 'relative' }}>
        
        <div style={{ width: '100%', maxWidth: '640px', height: '90vh', background: '#fff', borderRadius: '24px', boxShadow: '0 10px 40px rgba(0, 0, 0, 0.05)', display: 'flex', flexDirection: 'column', overflow: 'hidden', position: 'relative' }}>
          {fromProfile && (
            <Button
              type="text"
              icon={<ArrowLeftOutlined style={{ fontSize: 16, color: '#0F766E' }} />}
              onClick={() => navigate('/profile')}
              aria-label="返回全维数字健康看板"
              style={{
                position: 'absolute',
                top: 16,
                left: 24,
                zIndex: 5,
                width: 40,
                height: 40,
                borderRadius: '50%',
                background: 'rgba(255,255,255,0.85)',
                border: '1px solid rgba(15,118,110,0.10)',
                boxShadow: '0 4px 12px rgba(15,118,110,0.08)'
              }}
            />
          )}
          
          <div style={{ padding: fromProfile ? '24px 32px 16px 76px' : '24px 32px 16px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '1px solid #F1F5F9' }}>
            <Title level={4} style={{ margin: 0, color: '#0F172A', fontWeight: 800 }}>完善健康档案</Title>
            <Button type="text" icon={<FastForwardOutlined />} onClick={() => handleSkipOrFinish()} style={{ display: fromProfile ? 'none' : 'inline-flex', color: '#94A3B8', fontWeight: 600 }}>
              跳过，直接问诊
            </Button>
          </div>

          <div style={{ padding: '20px 40px 0' }}>
            <Steps current={gender === '男' && currentStep === 2 ? 1 : currentStep} items={[{ title: '基础与生活' }, ...(gender === '女' ? [{ title: '女性生理' }] : []), { title: '既往史' }]} size="small" />
          </div>

          <div style={{ padding: '24px 40px', overflowY: 'auto', flex: 1 }}>
            <Form form={form} layout="vertical" initialValues={{ gender: '男', obstetric_status: '无' }} size="large">
              
              {/* ==================== 1. 基础与生活方式信息 (🌟升级补全版) ==================== */}
              <div style={{ display: currentStep === 0 ? 'block' : 'none' }}>
                <Row gutter={16}>
                  <Col span={12}>
                    <Form.Item label={renderLabel("性别")} name="gender" rules={[{ required: true }]}>
                      <Radio.Group optionType="button" buttonStyle="solid" style={{ display: 'flex' }}>
                        <Radio.Button value="男" style={{ flex: 1, textAlign: 'center' }}>男</Radio.Button>
                        <Radio.Button value="女" style={{ flex: 1, textAlign: 'center' }}>女</Radio.Button>
                      </Radio.Group>
                    </Form.Item>
                  </Col>
                  <Col span={12}>
                    <Form.Item label={renderLabel("年龄 (岁)")} name="age"><InputNumber style={{ width: '100%' }} placeholder="如: 28" min={1} max={120} /></Form.Item>
                  </Col>
                </Row>
                
                <Row gutter={16}>
                  <Col span={12}>
                    <Form.Item label={renderLabel("身高 (cm)")} name="height"><InputNumber style={{ width: '100%' }} placeholder="如: 175" min={50} max={250} /></Form.Item>
                  </Col>
                  <Col span={12}>
                    <Form.Item label={renderLabel("体重 (kg)")} name="weight"><InputNumber style={{ width: '100%' }} placeholder="如: 65" min={2} max={300} /></Form.Item>
                  </Col>
                </Row>

                <Divider orientation="left" style={{ margin: '16px 0', color: '#64748B', fontSize: '14px' }}>生活习惯与偏好</Divider>

                <Form.Item label={<span style={{ fontWeight: 600 }}>饮食偏好</span>} name="diet">
                  <Select placeholder="请选择您的日常饮食倾向" options={[{label:'荤素搭配', value:'荤素搭配'}, {label:'偏爱素食', value:'偏爱素食'}, {label:'偏爱肉食', value:'偏爱肉食'}, {label:'重口味(嗜咸/嗜甜)', value:'重口味(嗜咸/嗜甜)'}]} />
                </Form.Item>

                <Form.Item label={<span style={{ fontWeight: 600 }}>运动频率</span>} name="exercise">
                  <Select placeholder="请选择您的运动习惯" options={[{label:'每周3次以上', value:'每周3次以上'}, {label:'每周1-2次', value:'每周1-2次'}, {label:'偶尔运动', value:'偶尔运动'}, {label:'几乎不运动', value:'几乎不运动'}]} />
                </Form.Item>

                <Form.Item label={<span style={{ fontWeight: 600 }}>睡眠质量</span>} name="sleep">
                  <Select placeholder="请评估您的睡眠状态" options={[{label:'规律且充足', value:'规律且充足'}, {label:'偶尔熬夜/失眠', value:'偶尔熬夜/失眠'}, {label:'经常熬夜/失眠', value:'经常熬夜/失眠'}]} />
                </Form.Item>

                <Row gutter={16}>
                  <Col span={12}>
                    <Form.Item label={<span style={{ fontWeight: 600 }}>吸烟史</span>} name="smoking">
                      <Select placeholder="吸烟情况" options={[{label:'不吸烟', value:'不吸烟'}, {label:'偶尔吸烟', value:'偶尔吸烟'}, {label:'长期吸烟', value:'长期吸烟'}]} />
                    </Form.Item>
                  </Col>
                  <Col span={12}>
                    <Form.Item label={<span style={{ fontWeight: 600 }}>饮酒史</span>} name="drinking">
                      <Select placeholder="饮酒情况" options={[{label:'不饮酒', value:'不饮酒'}, {label:'偶尔饮酒', value:'偶尔饮酒'}, {label:'经常饮酒', value:'经常饮酒'}]} />
                    </Form.Item>
                  </Col>
                </Row>
              </div>

              {/* ==================== 2. 女性生理信息 ==================== */}
              <div style={{ display: currentStep === 1 ? 'block' : 'none' }}>
                <Divider orientation="left" style={{ margin: '0 0 16px 0', color: '#64748B' }}>月经史</Divider>
                <Row gutter={16}>
                  <Col span={12}>
                    <Form.Item label={renderLabel("月经量")} name="menstrual_volume">
                      <Radio.Group optionType="button" buttonStyle="solid" style={{ display: 'flex' }}>
                        <Radio.Button value="多" style={{ flex: 1, textAlign: 'center' }}>多</Radio.Button>
                        <Radio.Button value="少" style={{ flex: 1, textAlign: 'center' }}>少</Radio.Button>
                        <Radio.Button value="正常" style={{ flex: 1, textAlign: 'center' }}>正常</Radio.Button>
                      </Radio.Group>
                    </Form.Item>
                  </Col>
                  <Col span={12}>
                    <Form.Item label={renderLabel("痛经情况")} name="dysmenorrhea">
                      <Radio.Group optionType="button" buttonStyle="solid" style={{ display: 'flex' }}>
                        <Radio.Button value="有" style={{ flex: 1, textAlign: 'center' }}>有</Radio.Button>
                        <Radio.Button value="无" style={{ flex: 1, textAlign: 'center' }}>无</Radio.Button>
                        <Radio.Button value="偶尔" style={{ flex: 1, textAlign: 'center' }}>偶尔</Radio.Button>
                      </Radio.Group>
                    </Form.Item>
                  </Col>
                </Row>
                <Form.Item label={renderLabel("月经周期 (天)")} name="menstrual_cycle">
                  <InputNumber placeholder="选择或输入天数 (如: 28)" style={{ width: '100%' }} min={10} max={60} />
                </Form.Item>

                <Divider orientation="left" style={{ margin: '24px 0 16px 0', color: '#64748B' }}>孕产信息</Divider>
                <Form.Item label={renderLabel("当前状态")} name="obstetric_status">
                  <Radio.Group optionType="button" buttonStyle="solid" style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                    <Radio.Button value="无" style={{ width: 'calc(33% - 6px)', textAlign: 'center' }}>无</Radio.Button>
                    <Radio.Button value="备孕期" style={{ width: 'calc(33% - 6px)', textAlign: 'center' }}>备孕期</Radio.Button>
                    <Radio.Button value="妊娠期" style={{ width: 'calc(33% - 6px)', textAlign: 'center' }}>妊娠期</Radio.Button>
                    <Radio.Button value="哺乳期" style={{ width: 'calc(33% - 6px)', textAlign: 'center' }}>哺乳期</Radio.Button>
                  </Radio.Group>
                </Form.Item>
                {obstetricStatus === '妊娠期' && (
                  <Form.Item label={renderLabel("预产日期")} name="due_date">
                    <DatePicker style={{ width: '100%' }} format="YYYY年MM月DD日" placeholder="请选择预产日期" />
                  </Form.Item>
                )}
                {obstetricStatus === '哺乳期' && (
                  <Form.Item label={renderLabel("哺乳开始日期")} name="lactation_start_date">
                    <DatePicker style={{ width: '100%' }} format="YYYY年MM月DD日" placeholder="请选择哺乳开始日期" />
                  </Form.Item>
                )}
              </div>

              {/* ==================== 3. 既往史 ==================== */}
              <div style={{ display: currentStep === 2 ? 'block' : 'none' }}>
                {renderToggleRow("既往患病情况", "disease")}
                {historyToggles.disease === '有' && (
                  <div style={{ marginBottom: 24, background: '#F8FAFC', padding: '16px 16px 4px', borderRadius: 8 }}>
                    <Form.Item name="past_diseases_common" noStyle>
                      <Checkbox.Group style={{ width: '100%', marginBottom: 16 }}>
                        <Row gutter={[8, 12]}>
                          {['高血压', '糖尿病', '脂肪肝', '高血脂', '冠心病', '风湿', '哮喘'].map(d => (
                            <Col span={8} key={d}><Checkbox value={d} style={{ fontSize: '14px', color: '#475569' }}>{d}</Checkbox></Col>
                          ))}
                        </Row>
                      </Checkbox.Group>
                    </Form.Item>
                    <Form.Item name="past_diseases_custom" style={{ marginBottom: 12 }}>
                      <Select mode="tags" placeholder="🔍 输入关键字查找或新增疾病 (如: 心)" style={{ width: '100%' }} options={SUG_DICTIONARIES.diseases} tagRender={customTagRender} filterOption={(input, option) => option?.value.includes(input)} />
                    </Form.Item>
                  </div>
                )}

                {renderToggleRow("手术情况", "surgery")}
                {historyToggles.surgery === '有' && (
                  <div style={{ marginBottom: 24, background: '#F8FAFC', padding: 16, borderRadius: 8 }}>
                    <Form.List name="surgeries">
                      {(fields, { add, remove }) => (
                        <>
                          {fields.map(({ key, name, ...restField }) => (
                            <Row key={key} gutter={8} style={{ marginBottom: 12 }}>
                              <Col span={11}>
                                <Form.Item {...restField} name={[name, 'name']} style={{ marginBottom: 0 }}>
                                  <Select mode="tags" maxCount={1} placeholder="输入手术名称" options={SUG_DICTIONARIES.surgeries} filterOption={(input, option) => option?.value.includes(input)} />
                                </Form.Item>
                              </Col>
                              <Col span={11}>
                                <Form.Item {...restField} name={[name, 'date']} style={{ marginBottom: 0 }}>
                                  <DatePicker style={{ width: '100%' }} format="YYYY年MM月DD日" placeholder="选择手术日期" />
                                </Form.Item>
                              </Col>
                              <Col span={2} style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                                <DeleteOutlined onClick={() => remove(name)} style={{ color: '#EF4444', fontSize: '18px', cursor: 'pointer' }} />
                              </Col>
                            </Row>
                          ))}
                          <Button type="dashed" onClick={() => add()} block icon={<PlusOutlined />} style={{ borderColor: '#14B8A6', color: '#14B8A6' }}>新增手术记录</Button>
                        </>
                      )}
                    </Form.List>
                  </div>
                )}

                {renderToggleRow("食物、药物等过敏情况", "allergy")}
                {historyToggles.allergy === '有' && (
                  <div style={{ marginBottom: 24, background: '#F8FAFC', padding: '16px 16px 4px', borderRadius: 8 }}>
                    <Form.Item name="allergies_common" noStyle>
                      <Checkbox.Group style={{ width: '100%', marginBottom: 16 }}>
                        <Row gutter={[8, 12]}>
                          {['青霉素类', '头孢菌素类', '阿司匹林', '海鲜', '花生', '牛奶', '花粉'].map(a => (
                            <Col span={8} key={a}><Checkbox value={a} style={{ fontSize: '14px', color: '#475569' }}>{a}</Checkbox></Col>
                          ))}
                        </Row>
                      </Checkbox.Group>
                    </Form.Item>
                    <Form.Item name="allergies_custom" style={{ marginBottom: 12 }}>
                      <Select mode="tags" placeholder="🔍 查找或新增食物、药物等过敏" style={{ width: '100%' }} options={SUG_DICTIONARIES.allergies} tagRender={customTagRender} filterOption={(input, option) => option?.value.includes(input)} />
                    </Form.Item>
                  </div>
                )}

                {renderToggleRow("预防接种情况", "vaccine")}
                {historyToggles.vaccine === '有' && (
                  <div style={{ marginBottom: 24, background: '#F8FAFC', padding: '16px 16px 4px', borderRadius: 8 }}>
                    <Form.Item name="vaccines_common" noStyle>
                      <Checkbox.Group style={{ width: '100%', marginBottom: 16 }}>
                        <Row gutter={[8, 12]}>
                          {['乙肝疫苗', 'HPV疫苗', '肺炎疫苗', '流感疫苗', '狂犬疫苗', '水痘疫苗'].map(v => (
                            <Col span={8} key={v}><Checkbox value={v} style={{ fontSize: '14px', color: '#475569' }}>{v}</Checkbox></Col>
                          ))}
                        </Row>
                      </Checkbox.Group>
                    </Form.Item>
                    <Form.Item name="vaccines_custom" style={{ marginBottom: 12 }}>
                      <Select mode="tags" placeholder="🔍 查找或新增其他接种记录" style={{ width: '100%' }} options={SUG_DICTIONARIES.vaccines} tagRender={customTagRender} filterOption={(input, option) => option?.value.includes(input)} />
                    </Form.Item>
                  </div>
                )}
              </div>

            </Form>
          </div>

          {/* 底部按钮区 */}
          <div style={{ padding: '20px 40px', borderTop: '1px solid #F1F5F9', background: '#fff', display: 'flex', gap: 16 }}>
            {currentStep > 0 && (
              <Button size="large" onClick={prev} style={{ flex: 1, borderRadius: 12 }}>返回</Button>
            )}
            {currentStep < 2 ? (
              <Button size="large" type="primary" onClick={next} style={{ flex: 2, borderRadius: 12, fontWeight: 600 }}>
                下一步 <RightOutlined />
              </Button>
            ) : (
              <Button size="large" type="primary" onClick={() => handleSkipOrFinish()} loading={loading} style={{ flex: 2, borderRadius: 12, fontWeight: 600 }}>
                <CheckCircleOutlined /> 完成建档
              </Button>
            )}
          </div>

        </div>
      </div>
    </ConfigProvider>
  );
};

export default Onboarding;
