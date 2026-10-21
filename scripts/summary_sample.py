"""Clearly labelled offline fixtures for report and UI verification."""
from datetime import timedelta
from backend.app.news_summary_schema import SummaryDocument
from backend.app.time_utils import utc_now


def summary_sample(at=None, history_ref="M42"):
    at = at or utc_now()
    sources = {}
    for key, days, text in [(history_ref, 2, "Codex SDK 增加工具调用示例"), ("N1", 1, "Codex SDK 更新类型定义"), ("N2", 0, "Codex SDK 发布代理集成教程")]:
        sources[key] = {"account": "openaidevs", "url": f"https://x.com/OpenAIDevs/status/{100000 + days}",
                        "published_at": (at - timedelta(days=days)).isoformat(), "delivered_at": at.isoformat(),
                        "original": "Offline demo: " + text, "translated_zh": "离线演示：" + text, "historical": key == history_ref}
    document = SummaryDocument(title="Codex SDK 集成进展 · 离线演示", markdown="""## 本期重点
**示例内容，非真实新闻。** Codex SDK 的类型定义更新与代理集成教程形成互补。[N1] [N2]

| 新闻 | 本期要点 |
| --- | --- |
| 类型定义 | 便于接入工具调用 [N1] |
| 集成教程 | 展示代理工作流 [N2] |

> 共同关键词只能提示关联，不能据此推断因果。

### 接入步骤
1. 阅读类型定义变更。[N1]
2. 对照集成教程验证调用流程。[N2]

```python
# 离线代码示例
print("AI agent workflow 🚀")
```
""", covered_ids=["N1", "N2"], timelines=[{"title": "Codex SDK 文档与接入", "nodes": [
        {"text": "离线示例：集成教程补充代理工作流。", "source_ids": ["N2"]},
        {"text": "离线示例：工具调用示例建立接入背景。", "source_ids": [history_ref]},
        {"text": "离线示例：类型定义更新。", "source_ids": ["N1"]},
    ]}], connections=[{"current_ids": ["N1"], "history_ids": [history_ref], "relation": "补充", "explanation": "类型定义与此前工具调用示例都涉及 SDK 接入；这里只展示材料关联，不推断因果。"}])
    return document, sources
