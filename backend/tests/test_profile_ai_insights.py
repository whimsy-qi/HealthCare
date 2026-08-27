import json
from types import SimpleNamespace

import pytest

import api_server


class _FakeCompletions:
    def __init__(self, content):
        self.content = content

    async def create(self, **_kwargs):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=self.content)
                )
            ]
        )


class _FakeChat:
    def __init__(self, content):
        self.completions = _FakeCompletions(content)


class _FakeLLMClient:
    def __init__(self, content):
        self.chat = _FakeChat(content)


@pytest.mark.asyncio
async def test_profile_ai_insights_accepts_guarded_llm_json(monkeypatch):
    payload = {
        "insights": [
            {"title": "睡眠节律提醒", "content": "档案显示存在熬夜或失眠情况，建议先固定入睡时间，减少睡前屏幕刺激，并观察白天疲劳变化。", "tags": ["睡眠", "恢复"]},
            {"title": "过敏安全提醒", "content": "档案记录了阿司匹林相关过敏信息，购药、接种或治疗前应主动告知医生和药师。", "tags": ["过敏", "安全"]},
            {"title": "运动习惯建议", "content": "当前运动频率偏低，建议从快走、骑行等低强度活动开始，逐步增加频次。", "tags": ["运动", "习惯"]},
        ]
    }
    monkeypatch.setattr(api_server, "shared_client", _FakeLLMClient(json.dumps(payload, ensure_ascii=False)))

    data = {"height": 165, "weight": 50, "sleep": "经常熬夜/失眠", "allergies": ["阿司匹林"], "exercise": "几乎不运动"}
    insights = await api_server._generate_llm_profile_insights(data, api_server._build_profile_health_context(data))

    assert len(insights) == 3
    assert insights[0]["title"] == "睡眠节律提醒"
    assert all("诊断为" not in item["content"] for item in insights)


@pytest.mark.asyncio
async def test_profile_ai_insights_rejects_forbidden_diagnosis_wording(monkeypatch):
    payload = {
        "insights": [
            {"title": "诊断结果", "content": "根据档案可诊断为某疾病。", "tags": ["诊断"]},
            {"title": "建议", "content": "建议继续观察。", "tags": ["健康"]},
            {"title": "运动", "content": "建议适度运动。", "tags": ["运动"]},
        ]
    }
    monkeypatch.setattr(api_server, "shared_client", _FakeLLMClient(json.dumps(payload, ensure_ascii=False)))

    data = {"height": 165, "weight": 50}
    with pytest.raises(ValueError):
        await api_server._generate_llm_profile_insights(data, api_server._build_profile_health_context(data))


@pytest.mark.asyncio
async def test_profile_ai_insights_rejects_invalid_json_and_rules_still_work(monkeypatch):
    monkeypatch.setattr(api_server, "shared_client", _FakeLLMClient("not-json"))

    data = {"height": 165, "weight": 50, "sleep": "经常熬夜/失眠", "exercise": "几乎不运动"}
    with pytest.raises(json.JSONDecodeError):
        await api_server._generate_llm_profile_insights(data, api_server._build_profile_health_context(data))

    fallback = api_server._profile_rule_insights(data)
    assert fallback
    assert fallback[0]["title"] == "体重偏轻提醒"
