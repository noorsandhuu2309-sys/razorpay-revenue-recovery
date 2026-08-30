from .chat import ChatAgent
from .coding import CodingAgent
from .reasoning import ReasoningAgent
from .research import ResearchAgent
from .vision import VisionAgent

AGENT_CLASSES = {
    "chat": ChatAgent,
    "coding": CodingAgent,
    "reasoning": ReasoningAgent,
    "research": ResearchAgent,
    "vision": VisionAgent,
}
