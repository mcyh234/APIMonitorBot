"""Curated X sources. Handles are identities, never arbitrary fetch URLs."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import re


@dataclass(frozen=True)
class NewsAccount:
    username: str
    name: str
    company: str
    category: str
    reason: str
    recommended: bool = True

    def view(self):
        return {**asdict(self), "url": f"https://x.com/{self.username}"}


# Reviewed against company websites, official repositories and account profiles.
# People are described by their connection to a team, avoiding stale job titles.
NEWS_ACCOUNTS = (
    NewsAccount("OpenAI", "OpenAI", "OpenAI", "公司", "模型、产品与公司公告"),
    NewsAccount("OpenAIDevs", "OpenAI Developers", "OpenAI", "产品", "API、Codex 与开发者更新"),
    NewsAccount("ChatGPT", "ChatGPT", "OpenAI", "产品", "ChatGPT 功能与使用更新"),
    NewsAccount("thsottiaux", "Tibo", "OpenAI", "负责人", "Codex 一线开发动态；保留原有监听"),
    NewsAccount("sama", "Sam Altman", "OpenAI", "创始人", "产品方向与公司动态，也包含个人观点", False),
    NewsAccount("gdb", "Greg Brockman", "OpenAI", "创始人", "技术进展与团队动态", False),
    NewsAccount("AnthropicAI", "Anthropic", "Anthropic", "公司", "Claude 模型、API 与研究公告"),
    NewsAccount("claudeai", "Claude", "Anthropic", "产品", "Claude 与 Claude Code 产品更新"),
    NewsAccount("bcherny", "Boris Cherny", "Anthropic", "负责人", "Claude Code 一线开发动态"),
    NewsAccount("alexalbert__", "Alex Albert", "Anthropic", "负责人", "Claude 开发者功能与实践"),
    NewsAccount("DarioAmodei", "Dario Amodei", "Anthropic", "创始人", "公司方向、研究与长文观点", False),
    NewsAccount("thinkymachines", "Thinking Machines Lab", "Thinking Machines", "公司", "模型、研究与开发者产品"),
    NewsAccount("miramurati", "Mira Murati", "Thinking Machines", "创始人", "研究、产品与团队方向", False),
    NewsAccount("ilyasut", "Ilya Sutskever", "SSI", "创始人", "研究与公司方向，更新较少", False),
    NewsAccount("GoogleDeepMind", "Google DeepMind", "Google", "公司", "Gemini、研究与模型发布"),
    NewsAccount("GoogleAI", "Google AI", "Google", "公司", "Google AI 研究与生态更新"),
    NewsAccount("GoogleAIStudio", "Google AI Studio", "Google", "产品", "Gemini API 与 AI Studio 更新"),
    NewsAccount("GeminiApp", "Gemini", "Google", "产品", "Gemini 应用功能更新"),
    NewsAccount("demishassabis", "Demis Hassabis", "Google", "创始人", "DeepMind 研究与模型进展", False),
    NewsAccount("sundarpichai", "Sundar Pichai", "Google", "负责人", "Google 产品与公司方向", False),
    NewsAccount("OfficialLoganK", "Logan Kilpatrick", "Google", "负责人", "Gemini API 与 AI Studio 开发者动态"),
    NewsAccount("xai", "xAI", "xAI", "公司", "Grok 模型与 API 公告"),
    NewsAccount("grok", "Grok", "xAI", "产品", "Grok 产品与公开互动，发帖频繁", False),
    NewsAccount("elonmusk", "Elon Musk", "xAI", "创始人", "xAI 与 Grok 动态，也包含大量其他话题", False),
    NewsAccount("AIatMeta", "AI at Meta", "Meta", "公司", "Llama、开源模型与研究"),
    NewsAccount("MicrosoftAI", "Microsoft AI", "Microsoft", "公司", "Microsoft AI 与 Copilot 动态"),
    NewsAccount("Copilot", "Microsoft Copilot", "Microsoft", "产品", "Copilot 产品更新"),
    NewsAccount("satyanadella", "Satya Nadella", "Microsoft", "负责人", "微软 AI 产品与公司方向", False),
    NewsAccount("mustafasuleyman", "Mustafa Suleyman", "Microsoft", "负责人", "微软 AI 与产品方向", False),
    NewsAccount("deepseek_ai", "DeepSeek", "DeepSeek", "公司", "模型发布、API 与开源更新"),
    NewsAccount("tianyi", "崔天翼 / Tianyi Cui", "DeepSeek", "负责人", "DeepSeek Harness 开发与产品动态"),
    NewsAccount("Alibaba_Qwen", "Qwen", "Qwen", "公司", "通义千问模型、开源与 API 更新"),
    NewsAccount("kimi_moonshot", "Kimi", "Moonshot", "产品", "Kimi 模型与产品更新"),
    NewsAccount("MiniMax_AI", "MiniMax", "MiniMax", "公司", "MiniMax 模型、API 与产品更新"),
    NewsAccount("zai_org", "Z.ai", "Z.ai", "公司", "GLM 模型与开发者更新"),
    NewsAccount("StepFun_ai", "StepFun", "StepFun", "公司", "阶跃星辰模型与开源更新"),
    NewsAccount("Baidu_Inc", "Baidu", "Baidu", "公司", "文心与百度 AI 生态，也包含公司其他业务", False),
    NewsAccount("MistralAI", "Mistral AI", "Mistral", "公司", "模型、API、Le Chat 与编码产品"),
    NewsAccount("arthurmensch", "Arthur Mensch", "Mistral", "创始人", "Mistral 产品与公司方向", False),
    NewsAccount("cohere", "Cohere", "Cohere", "公司", "企业模型、检索与 API"),
    NewsAccount("aidangomez", "Aidan Gomez", "Cohere", "创始人", "Cohere 技术与公司方向", False),
    NewsAccount("AI21Labs", "AI21 Labs", "AI21", "公司", "模型、企业产品与研究"),
    NewsAccount("allen_ai", "Allen Institute for AI", "Ai2", "公司", "开放模型、数据与研究"),
    NewsAccount("SakanaAILabs", "Sakana AI", "Sakana", "公司", "模型与研究进展"),
    NewsAccount("imbue_ai", "Imbue", "Imbue", "公司", "AI 代理与研究进展"),
    NewsAccount("AmazonScience", "Amazon Science", "Amazon", "公司", "Amazon AI 研究与技术进展"),
    NewsAccount("NousResearch", "Nous Research", "Nous", "公司", "开放模型与训练研究"),
    NewsAccount("huggingface", "Hugging Face", "Hugging Face", "公司", "开源模型、工具与生态更新"),
    NewsAccount("ClemDelangue", "Clément Delangue", "Hugging Face", "创始人", "开源 AI 生态与公司方向", False),
    NewsAccount("NVIDIAAI", "NVIDIA AI", "NVIDIA", "公司", "推理、训练与模型工具"),
    NewsAccount("togethercompute", "Together AI", "Together", "公司", "开放模型托管与推理 API"),
    NewsAccount("FireworksAI_HQ", "Fireworks AI", "Fireworks", "公司", "推理 API、模型上线与平台更新"),
    NewsAccount("GroqLLC", "Groq", "Groq", "公司", "推理服务与开发者平台"),
    NewsAccount("Cerebras", "Cerebras", "Cerebras", "公司", "推理平台与模型支持"),
    NewsAccount("cursor_ai", "Cursor", "Cursor", "产品", "AI 编程工具与代理更新"),
    NewsAccount("Replit", "Replit", "Replit", "产品", "编程代理与应用构建更新"),
    NewsAccount("amasad", "Amjad Masad", "Replit", "创始人", "Replit 与编程代理方向", False),
    NewsAccount("cognition", "Cognition", "Cognition", "公司", "Devin、Windsurf 与编程代理更新"),
    NewsAccount("midjourney", "Midjourney", "Midjourney", "产品", "图像与视频产品更新"),
    NewsAccount("runwayml", "Runway", "Runway", "公司", "视频模型与创作工具"),
    NewsAccount("pika_labs", "Pika", "Pika", "产品", "视频模型与产品功能"),
    NewsAccount("LumaLabsAI", "Luma AI", "Luma", "公司", "视频模型与 Dream Machine"),
    NewsAccount("elevenlabs", "ElevenLabs", "ElevenLabs", "公司", "语音、音频与代理产品"),
    NewsAccount("ElevenLabsDevs", "ElevenLabs Developers", "ElevenLabs", "产品", "音频 API 与开发者更新"),
    NewsAccount("StabilityAI", "Stability AI", "Stability", "公司", "图像、音频与开放模型"),
    NewsAccount("bfl_ai", "Black Forest Labs", "BFL", "公司", "FLUX 图像模型与 API"),
    NewsAccount("ideogram_ai", "Ideogram", "Ideogram", "产品", "图像生成模型与产品更新"),
    NewsAccount("suno", "Suno", "Suno", "产品", "音乐生成与创作功能"),
    NewsAccount("Kling_ai", "Kling AI", "Kling", "产品", "可灵视频模型与产品更新"),
)


def normalize_username(value: str) -> str:
    username = value.strip().lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", username):
        raise ValueError("X 账号格式错误：请填写 1–15 位字母、数字或下划线，不要填写网址。")
    return username.casefold()


def recommended_accounts() -> list[str]:
    return [item.username.casefold() for item in NEWS_ACCOUNTS if item.recommended]


def account_name(username: str) -> str:
    return next((item.name for item in NEWS_ACCOUNTS if item.username.casefold() == username.casefold()), username)
