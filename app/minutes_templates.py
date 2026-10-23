"""Versioned meeting-minutes templates shared by generation, editing and export."""
from copy import deepcopy

from pydantic import BaseModel, Field


TEMPLATE_VERSION = 1


class MinutesTemplateSection(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=100)
    source: str = Field(pattern="^(overview|conclusions|decisions|action_items|open_questions|summary)$")
    kind: str = Field(default="list", pattern="^(list|decision_table|action_table)$")
    required: bool = False
    evidence_required: bool = True
    max_items: int = Field(default=10, ge=1, le=100)


class MinutesTemplate(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    category: str = Field(default="通用", max_length=100)
    version: int = Field(default=TEMPLATE_VERSION, ge=1)
    builtin: bool = True
    generation_instruction: str = Field(default="", max_length=2000)
    sections: list[MinutesTemplateSection] = Field(min_length=1, max_length=20)


BUILTIN_TEMPLATES = (
    MinutesTemplate(
        id="general-meeting",
        name="通用会议纪要",
        description="适合交流、协调和常规业务会议，突出结论、决策和行动。",
        category="通用",
        generation_instruction="按会议背景、核心讨论、明确决策、待办事项和总结组织内容。",
        sections=[
            MinutesTemplateSection(id="core", title="核心内容", source="overview", max_items=10),
            MinutesTemplateSection(id="decisions", title="决策事项", source="decisions", kind="decision_table", max_items=10),
            MinutesTemplateSection(id="actions", title="待办事项", source="action_items", kind="action_table", max_items=15),
            MinutesTemplateSection(id="summary", title="会议总结", source="conclusions", max_items=5),
            MinutesTemplateSection(id="questions", title="风险与待确认", source="open_questions", max_items=10),
        ],
    ),
    MinutesTemplate(
        id="project-weekly",
        name="项目例会",
        description="用于项目推进和周期例会，强调进展、决策、风险及责任人。",
        category="项目管理",
        generation_instruction="围绕项目状态、本期进展、决策、风险和下一步行动整理；行动项应尽量明确负责人和期限。",
        sections=[
            MinutesTemplateSection(id="status", title="项目状态摘要", source="overview", max_items=5),
            MinutesTemplateSection(id="progress", title="本期工作进展", source="conclusions", max_items=12),
            MinutesTemplateSection(id="decisions", title="项目决策", source="decisions", kind="decision_table", max_items=10),
            MinutesTemplateSection(id="risks", title="问题、风险与依赖", source="open_questions", max_items=12),
            MinutesTemplateSection(id="actions", title="下一步行动", source="action_items", kind="action_table", max_items=20),
        ],
    ),
    MinutesTemplate(
        id="customer-interview",
        name="客户访谈",
        description="用于客户交流和需求访谈，保留客户背景、痛点、机会及后续动作。",
        category="客户研究",
        generation_instruction="围绕客户现状、痛点与需求、产品机会、待验证问题和后续动作整理，避免把推测写成客户事实。",
        sections=[
            MinutesTemplateSection(id="background", title="客户背景与现状", source="overview", max_items=8),
            MinutesTemplateSection(id="needs", title="痛点与需求", source="conclusions", max_items=12),
            MinutesTemplateSection(id="opportunities", title="产品机会与共识", source="decisions", max_items=10),
            MinutesTemplateSection(id="questions", title="待验证问题", source="open_questions", max_items=12),
            MinutesTemplateSection(id="followups", title="后续动作", source="action_items", kind="action_table", max_items=15),
        ],
    ),
)


def builtin_template(template_id: str | None = None) -> MinutesTemplate:
    value = template_id or "general-meeting"
    for template in BUILTIN_TEMPLATES:
        if template.id == value:
            return template.model_copy(deep=True)
    raise KeyError(value)


def template_snapshot(template: MinutesTemplate) -> dict:
    return deepcopy(template.model_dump())
