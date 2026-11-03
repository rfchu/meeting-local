from typing import Literal
from .interview import InterviewResult,InterviewPreparation
"""Editable, source-grounded meeting minutes and document exports."""
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from pydantic import BaseModel, Field
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import BaseDocTemplate, Frame, LongTable, PageTemplate, Paragraph, TableStyle

from .analysis import AnalysisItem, MeetingAnalysis, AIMinutesResult, SECTION_NAMES, validate_citations
from .minutes_templates import MinutesTemplate, builtin_template, template_snapshot


MINUTES_VERSION = "meeting-minutes-v2"
MINUTES_SECTIONS = ("overview", "conclusions", "decisions", "action_items", "open_questions", "summary")
MINUTES_LABELS = {
    "overview": "会议概述",
    "conclusions": "核心结论",
    "decisions": "已定事项",
    "action_items": "行动项",
    "open_questions": "待确认问题",
    "summary": "会议总结",
}


class MinutesItem(BaseModel):
    content: str = Field(min_length=1, max_length=6000)
    source_segment_ids: list[str] = Field(default_factory=list, max_length=100)
    owner: str | None = Field(default=None, max_length=200)
    due_date: str | None = Field(default=None, max_length=200)
    status: str | None = Field(default=None, max_length=100)


class MeetingMinutes(BaseModel):
    interview_context:InterviewPreparation|None=None
    interview:InterviewResult|None=None
    interview_reviews:dict[str,Literal['kept','ignored']]=Field(default_factory=dict,max_length=30)
    interviewer_evaluation:str=Field(default='',max_length=1800)
    interview_evaluation_needs_review:bool=False
    title: str = Field(min_length=1, max_length=300)
    meeting_date: str = Field(default="", max_length=100)
    meeting_form: str = Field(default="", max_length=200)
    location: str = Field(default="", max_length=300)
    attendees: str = Field(default="", max_length=3000)
    recorder: str = Field(default="", max_length=300)
    purpose: str = Field(default="", max_length=3000)
    style: str = Field(default="formal", pattern="^(formal|brief|internal|ai)$")
    template_id: str = Field(default="general-meeting", max_length=100)
    template_version: int = Field(default=1, ge=1)
    template_snapshot: dict | None = None
    basic_info_source_segment_ids: list[str] = Field(default_factory=list, max_length=100)
    overview: list[MinutesItem] = Field(default_factory=list, max_length=50)
    conclusions: list[MinutesItem] = Field(default_factory=list, max_length=50)
    decisions: list[MinutesItem] = Field(default_factory=list, max_length=50)
    action_items: list[MinutesItem] = Field(default_factory=list, max_length=100)
    open_questions: list[MinutesItem] = Field(default_factory=list, max_length=50)

    summary: list[MinutesItem] = Field(default_factory=list, max_length=50)

    def all_items(self):
        for section in MINUTES_SECTIONS:
            yield from getattr(self, section)


def validate_minutes_citations(document: MeetingMinutes, valid_ids: set[str]):
    if not set(document.basic_info_source_segment_ids).issubset(valid_ids):
        raise ValueError("会议纪要基本信息包含不存在的原文引用")
    if document.interview:
        for evidence in document.interview.evidence():
            if evidence.segment_id not in valid_ids:raise ValueError('面试纪要包含不存在的原文引用')
    for item in document.all_items():
        if not set(item.source_segment_ids).issubset(valid_ids):
            raise ValueError("会议纪要包含不存在的原文引用")


def build_minutes(title: str, created: str, segments: list[dict], analysis: MeetingAnalysis, style: str, template: MinutesTemplate | None = None):
    """Rearrange validated analysis without asking a model to invent new facts."""
    validate_citations(analysis, {segment["id"] for segment in segments})
    speakers = list(dict.fromkeys(
        segment.get("speaker", "").strip() for segment in segments
        if segment.get("speaker", "").strip() and segment.get("speaker") != "未分配"
    ))

    def copy(items, limit=None, action=False):
        values = items[:limit] if limit else items
        return [MinutesItem(**item.model_dump(), status="待开始" if action else None) for item in values]

    template = template or builtin_template()
    if style == "brief":
        limits = {"summary": 1, "key_points": 0, "decisions": 3, "action_items": 5, "open_questions": 3}
    elif style == "formal":
        limits = {"summary": 2, "key_points": 0, "decisions": 8, "action_items": 12, "open_questions": 6}
    else:
        limits = {name: None for name in SECTION_NAMES}
    source_to_analysis = {"overview":"summary","conclusions":"key_points","decisions":"decisions",
                          "action_items":"action_items","open_questions":"open_questions"}
    if template.id != "general-meeting" and style != "brief":
        for section in template.sections:
            limits[source_to_analysis[section.source]] = section.max_items
    return MeetingMinutes(
        title=f"{title}会议纪要",
        meeting_date=(created or "")[:10],
        attendees="、".join(speakers),
        style=style,
        template_id=template.id,
        template_version=template.version,
        template_snapshot=template_snapshot(template),
        overview=copy(analysis.summary, limits["summary"]),
        conclusions=copy(analysis.key_points, limits["key_points"]) if limits["key_points"] != 0 else [],
        decisions=copy(analysis.decisions, limits["decisions"]),
        action_items=copy(analysis.action_items, limits["action_items"], action=True),
        open_questions=copy(analysis.open_questions, limits["open_questions"]),
    )


def build_ai_minutes(created: str, result: AIMinutesResult, template: MinutesTemplate | None = None):
    if template and template.id=='online-interview':
        from .minutes_composition import build_from_ai,CompositionSettings
        return build_from_ai(created,result,CompositionSettings(template_id=template.id),template=template)
    def copy(items, action=False):
        return [MinutesItem(**item.model_dump(),status="待开始" if action else None) for item in items]
    topic=result.basic_info.topic.strip()
    title=topic if topic.endswith("会议纪要") else topic+"会议纪要"
    template = template or builtin_template()
    return MeetingMinutes(title=title,meeting_date=(created or "")[:10],meeting_form=result.basic_info.meeting_form,
        attendees="、".join(result.basic_info.attendees),purpose=result.basic_info.purpose,style="ai",
        template_id=template.id,template_version=template.version,template_snapshot=template_snapshot(template),
        basic_info_source_segment_ids=result.basic_info.source_segment_ids,
        overview=copy(result.core_content),action_items=copy(result.action_items,True),conclusions=copy(result.summary))


def apply_polished_minutes(document: MeetingMinutes, result: MeetingAnalysis):
    """Apply cited LLM wording while rejecting newly invented structured fields."""
    mapping = {
        "overview": (result.summary, document.overview),
        "conclusions": (result.key_points, document.conclusions),
        "decisions": (result.decisions, document.decisions),
        "action_items": (result.action_items, document.action_items),
        "open_questions": (result.open_questions, document.open_questions),
    }
    limits = {"overview": 1, "conclusions": 0, "decisions": 3, "action_items": 5, "open_questions": 3} if document.style == "brief" else {}
    updates = {}
    for section, (polished, original) in mapping.items():
        cited_original = [item for item in original if item.source_segment_ids]
        manual_items = [item.model_copy(deep=True) for item in original if not item.source_segment_ids]
        cleaned = []
        for item in polished:
            refs = set(item.source_segment_ids)
            sources = [source for source in cited_original if refs.intersection(source.source_segment_ids)]
            owners = {source.owner for source in sources if source.owner}
            dates = {source.due_date for source in sources if source.due_date}
            statuses = [source.status for source in sources if source.status]
            cleaned.append(MinutesItem(content=item.content,source_segment_ids=list(dict.fromkeys(item.source_segment_ids)),
                owner=item.owner if item.owner in owners else None,due_date=item.due_date if item.due_date in dates else None,
                status=(statuses[0] if statuses else "待开始") if section == "action_items" else None))
        values = cleaned + manual_items
        if section in limits:values = values[:limits[section]] if limits[section] else []
        updates[section] = values
    return document.model_copy(update=updates,deep=True)


def stamp(seconds):
    value = max(0, int(seconds or 0)); hours, value = divmod(value, 3600); minutes, seconds = divmod(value, 60)
    return (f"{hours:02}:" if hours else "") + f"{minutes:02}:{seconds:02}"


def _citation_text(item: MinutesItem, segments: dict[str, dict]):
    return "、".join(stamp(segments[sid]["start"]) for sid in item.source_segment_ids if sid in segments)


def minutes_markdown(document: MeetingMinutes, segments: list[dict], include_citations=False):
    by_id = {segment["id"]: segment for segment in segments}
    lines = [f"# {document.title}", ""]
    metadata = (("日期", document.meeting_date), ("地点", document.location),
                ("会议形式", document.meeting_form), ("参会人", document.attendees), ("记录人", document.recorder))
    for label, value in metadata:
        if value: lines.append(f"**{label}：** {value}")
    if any(value for _, value in metadata): lines.append("")
    if document.purpose: lines.extend(["## 会议目标", "", document.purpose, ""])
    labels, sections = _style_layout(document)
    for section in sections:
        lines.extend([f"## {labels[section]}", ""])
        items = getattr(document, section)
        if not items:
            lines.extend(["- 无", ""]); continue
        if _section_kind(document,section) == "decision_table":
            lines.extend(["| 决策事项 | 负责人 | 生效/完成时间 |", "| --- | --- | --- |"]) 
            for item in items:
                content = _content_with_ref(item, by_id, include_citations)
                lines.append(f"| {_md_cell(content)} | {_md_cell(item.owner or '待确认')} | {_md_cell(item.due_date or '待确认')} |")
            lines.append(""); continue
        if _section_kind(document,section) == "action_table":
            lines.extend(["| 行动项 | 负责人 | 截止时间 | 状态 |", "| --- | --- | --- | --- |"]) 
            for item in items:
                content = _content_with_ref(item, by_id, include_citations)
                lines.append(f"| {_md_cell(content)} | {_md_cell(item.owner or '待确认')} | {_md_cell(item.due_date or '待确认')} | {_md_cell(item.status or '待开始')} |")
            lines.append(""); continue
        for item in items:
            details = []
            if item.owner: details.append("负责人：" + item.owner)
            if item.due_date: details.append("截止：" + item.due_date)
            if item.status: details.append("状态：" + item.status)
            if include_citations:
                refs = _citation_text(item, by_id)
                if refs: details.append("原文：" + refs)
            lines.append(f"- {item.content}" + (f"（{'；'.join(details)}）" if details else ""))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _style_layout(value):
    if isinstance(value, MeetingMinutes):
        snapshot=value.template_snapshot
        if snapshot:
            try:
                template=MinutesTemplate.model_validate(snapshot)
                return ({section.source:section.title for section in template.sections},
                        tuple(section.source for section in template.sections))
            except ValueError:
                pass
        style=value.style
    else:style=value
    if style == "ai":
        return ({"overview":"核心内容纪要","action_items":"待办事项（Action Items）","conclusions":"会议总结"},
                ("overview","action_items","conclusions"))
    if style == "brief":
        return ({"overview":"会议结果","decisions":"关键决策","action_items":"行动清单","open_questions":"待确认"},
                ("overview","decisions","action_items","open_questions"))
    if style == "internal":
        return ({"overview":"会议摘要","conclusions":"讨论要点","decisions":"决策与依据","action_items":"行动跟踪","open_questions":"风险、依赖与待确认","summary":"会议总结"},
                MINUTES_SECTIONS)
    return ({"overview":"会议成果","decisions":"决策事项","action_items":"行动计划","open_questions":"风险与待确认"},
            ("overview","decisions","action_items","open_questions"))


def _section_kind(document: MeetingMinutes, source: str):
    if document.template_snapshot:
        try:
            template=MinutesTemplate.model_validate(document.template_snapshot)
            section=next((item for item in template.sections if item.source==source),None)
            if section:return section.kind
        except ValueError:
            pass
    if document.style != "internal" and source=="decisions":return "decision_table"
    if document.style != "internal" and source=="action_items":return "action_table"
    return "list"


def _md_cell(value):
    return str(value).replace("|", "\\|").replace("\n", "<br>")


def _content_with_ref(item, by_id, include_citations):
    content = item.content
    if include_citations:
        refs = _citation_text(item, by_id)
        if refs: content += f"（原文：{refs}）"
    return content


STYLE_NAMES = {"formal":"正式会议纪要","brief":"精简会议纪要","internal":"内部工作纪要","ai":"AI 会议纪要"}


def _document_label(document: MeetingMinutes):
    if document.template_snapshot:
        name=document.template_snapshot.get("name")
        if isinstance(name,str) and name.strip():return name.strip()
    return STYLE_NAMES[document.style]
INK = "1E293B"; MUTED = "667085"; NAVY = "253A5B"; PALE = "F5F7FA"; LINE = "D9E0E8"
DOCX_FONT = "Heiti SC"


def _citation_index(document: MeetingMinutes, segments: list[dict]):
    by_id={segment["id"]:segment for segment in segments}
    ordered=[]
    for sid in document.basic_info_source_segment_ids:
        if sid in by_id and sid not in ordered:ordered.append(sid)
    labels,sections=_style_layout(document)
    for section in sections:
        for item in getattr(document,section):
            for sid in item.source_segment_ids:
                if sid in by_id and sid not in ordered:ordered.append(sid)
    return by_id,{sid:index+1 for index,sid in enumerate(ordered)},ordered


def _reference_marks(ids,reference_numbers):
    return " ".join(f"[{reference_numbers[sid]}]" for sid in ids if sid in reference_numbers)


def _excerpt(value,limit=240):
    text=" ".join(str(value or "").split())
    return text if len(text)<=limit else text[:limit-1]+"…"


def _set_docx_font(run,size=None,bold=None,color=INK):
    run.font.name=DOCX_FONT;run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"),DOCX_FONT)
    if size:run.font.size=Pt(size)
    if bold is not None:run.bold=bold
    run.font.color.rgb=RGBColor.from_string(color)


def _shade_cell(cell,color):
    tc_pr=cell._tc.get_or_add_tcPr();shading=tc_pr.find(qn("w:shd"))
    if shading is None:shading=OxmlElement("w:shd");tc_pr.append(shading)
    shading.set(qn("w:fill"),color)


def _cell_margins(cell,top=100,start=120,bottom=100,end=120):
    tc_pr=cell._tc.get_or_add_tcPr();margins=tc_pr.first_child_found_in("w:tcMar")
    if margins is None:margins=OxmlElement("w:tcMar");tc_pr.append(margins)
    for edge,value in (("top",top),("start",start),("bottom",bottom),("end",end)):
        node=margins.find(qn("w:"+edge))
        if node is None:node=OxmlElement("w:"+edge);margins.append(node)
        node.set(qn("w:w"),str(value));node.set(qn("w:type"),"dxa")


def _cant_split(row):
    props=row._tr.get_or_add_trPr();node=props.find(qn("w:cantSplit"))
    if node is None:props.append(OxmlElement("w:cantSplit"))


def _repeat_header(row):
    props=row._tr.get_or_add_trPr();node=props.find(qn("w:tblHeader"))
    if node is None:props.append(OxmlElement("w:tblHeader"))


def _set_table_borders(table,color=LINE,size="4"):
    tbl_pr=table._tbl.tblPr;borders=tbl_pr.first_child_found_in("w:tblBorders")
    if borders is None:borders=OxmlElement("w:tblBorders");tbl_pr.append(borders)
    for edge in ("top","left","bottom","right","insideH","insideV"):
        node=borders.find(qn("w:"+edge))
        if node is None:node=OxmlElement("w:"+edge);borders.append(node)
        node.set(qn("w:val"),"single");node.set(qn("w:sz"),size);node.set(qn("w:color"),color)


def _write_docx_text(paragraph,text,bold=False,color=INK,size=10.5):
    run=paragraph.add_run(str(text));_set_docx_font(run,size,bold,color);return run


def _write_docx_item(paragraph,item,reference_numbers,prefix=""):
    if prefix:_write_docx_text(paragraph,prefix,color=MUTED)
    _write_docx_text(paragraph,item.content)
    marks=_reference_marks(item.source_segment_ids,reference_numbers)
    if marks:_write_docx_text(paragraph," "+marks,color=MUTED,size=8)


def _configure_docx(document):
    section=document.sections[0];section.page_width=Cm(21);section.page_height=Cm(29.7)
    section.top_margin=Cm(2);section.bottom_margin=Cm(1.8);section.left_margin=Cm(2.1);section.right_margin=Cm(2.1)
    normal=document.styles["Normal"];normal.font.name=DOCX_FONT;normal._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"),DOCX_FONT)
    normal.font.size=Pt(10.5);normal.font.color.rgb=RGBColor.from_string(INK)
    normal.paragraph_format.line_spacing=1.4;normal.paragraph_format.space_after=Pt(6)
    title=document.styles["Title"];title.font.name=DOCX_FONT;title._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"),DOCX_FONT)
    title.font.size=Pt(21);title.font.bold=True;title.font.color.rgb=RGBColor(0,0,0)
    title.paragraph_format.space_after=Pt(5);title.paragraph_format.alignment=WD_ALIGN_PARAGRAPH.CENTER
    title_props=title._element.get_or_add_pPr();title_borders=title_props.find(qn("w:pBdr"))
    if title_borders is not None:title_props.remove(title_borders)
    heading=document.styles["Heading 1"];heading.font.name=DOCX_FONT;heading._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"),DOCX_FONT)
    heading.font.size=Pt(13);heading.font.bold=True;heading.font.color.rgb=RGBColor(0,0,0)
    heading.paragraph_format.space_before=Pt(14);heading.paragraph_format.space_after=Pt(7);heading.paragraph_format.keep_with_next=True
    return section


def _add_docx_field(paragraph,instruction):
    run=paragraph.add_run();begin=OxmlElement("w:fldChar");begin.set(qn("w:fldCharType"),"begin")
    code=OxmlElement("w:instrText");code.set(qn("xml:space"),"preserve");code.text=instruction
    separate=OxmlElement("w:fldChar");separate.set(qn("w:fldCharType"),"separate")
    text=OxmlElement("w:t");text.text="1";end=OxmlElement("w:fldChar");end.set(qn("w:fldCharType"),"end")
    for node in (begin,code,separate,text,end):run._r.append(node)
    _set_docx_font(run,8,color=MUTED)


def _add_docx_header_footer(section,document):
    header=section.header;table=header.add_table(rows=1,cols=2,width=Cm(16.8));table.autofit=False
    table.columns[0].width=Cm(11.8);table.columns[1].width=Cm(5)
    left,right=table.rows[0].cells;left.width=Cm(11.8);right.width=Cm(5)
    left_p=left.paragraphs[0];right_p=right.paragraphs[0];right_p.alignment=WD_ALIGN_PARAGRAPH.RIGHT
    _write_docx_text(left_p,_excerpt(document.title,45),size=8,color=MUTED)
    _write_docx_text(right_p,document.meeting_date or _document_label(document),size=8,color=MUTED)
    footer=section.footer.paragraphs[0];footer.alignment=WD_ALIGN_PARAGRAPH.CENTER
    _write_docx_text(footer,"第 ",size=8,color=MUTED);_add_docx_field(footer,"PAGE")
    _write_docx_text(footer," 页 / 共 ",size=8,color=MUTED);_add_docx_field(footer,"NUMPAGES")
    _write_docx_text(footer," 页",size=8,color=MUTED)


def _docx_table(document,headers,rows,widths,item_column=None,reference_numbers=None):
    table=document.add_table(rows=1,cols=len(headers));table.alignment=WD_TABLE_ALIGNMENT.CENTER;table.autofit=False
    _set_table_borders(table)
    for index,(cell,label,width) in enumerate(zip(table.rows[0].cells,headers,widths)):
        cell.width=Cm(width);cell.vertical_alignment=WD_CELL_VERTICAL_ALIGNMENT.CENTER;_shade_cell(cell,NAVY);_cell_margins(cell,120,140,120,140)
        paragraph=cell.paragraphs[0];paragraph.alignment=WD_ALIGN_PARAGRAPH.LEFT;paragraph.paragraph_format.space_after=Pt(0)
        _write_docx_text(paragraph,label,bold=True,color="FFFFFF",size=9.5)
    _repeat_header(table.rows[0])
    for row_index,row_values in enumerate(rows):
        row=table.add_row();_cant_split(row)
        for column,(cell,value,width) in enumerate(zip(row.cells,row_values,widths)):
            cell.width=Cm(width);cell.vertical_alignment=WD_CELL_VERTICAL_ALIGNMENT.CENTER;_cell_margins(cell,120,140,120,140)
            if row_index%2:_shade_cell(cell,PALE)
            paragraph=cell.paragraphs[0];paragraph.paragraph_format.space_after=Pt(0);paragraph.paragraph_format.line_spacing=1.25
            if item_column==column and isinstance(value,MinutesItem):_write_docx_item(paragraph,value,reference_numbers or {})
            else:_write_docx_text(paragraph,value or "待确认",size=9.5)
    document.add_paragraph().paragraph_format.space_after=Pt(0)


def _docx_metadata(document,minutes,reference_numbers):
    rows=[("日期",minutes.meeting_date), ("会议形式",minutes.meeting_form), ("地点",minutes.location),
          ("参会人员",minutes.attendees), ("记录人",minutes.recorder), ("会议目的",minutes.purpose)]
    rows=[row for row in rows if row[1]]
    table=document.add_table(rows=0,cols=2);table.alignment=WD_TABLE_ALIGNMENT.CENTER;table.autofit=False;_set_table_borders(table)
    for label,value in rows:
        cells=table.add_row().cells;_cant_split(table.rows[-1]);cells[0].width=Cm(2.8);cells[1].width=Cm(14)
        for cell in cells:cell.vertical_alignment=WD_CELL_VERTICAL_ALIGNMENT.CENTER;_cell_margins(cell,110,140,110,140)
        _shade_cell(cells[0],PALE);p=cells[0].paragraphs[0];p.paragraph_format.space_after=Pt(0);_write_docx_text(p,label,bold=True,size=9.5)
        p=cells[1].paragraphs[0];p.paragraph_format.space_after=Pt(0);_write_docx_text(p,value,size=9.5)
        if label=="会议目的":
            marks=_reference_marks(minutes.basic_info_source_segment_ids,reference_numbers)
            if marks:_write_docx_text(p," "+marks,color=MUTED,size=8)


def minutes_docx(document: MeetingMinutes, segments: list[dict], include_citations=False):
    """Create an editable, A4 Word document with stable Chinese typography and tables."""
    by_id,reference_numbers,ordered=_citation_index(document,segments)
    if not include_citations:reference_numbers={};ordered=[]
    word=Document();section=_configure_docx(word);_add_docx_header_footer(section,document)
    title=word.add_paragraph(style="Title");_write_docx_text(title,document.title,bold=True,color="000000",size=21)
    subtitle=word.add_paragraph();subtitle.alignment=WD_ALIGN_PARAGRAPH.CENTER;subtitle.paragraph_format.space_after=Pt(16)
    _write_docx_text(subtitle,_document_label(document)+(" · "+document.meeting_date if document.meeting_date else ""),size=9,color=MUTED)
    number=1;word.add_heading(f"{number} 会议基本信息",level=1);_docx_metadata(word,document,reference_numbers);number+=1
    labels,sections=_style_layout(document)
    for section_name in sections:
        items=getattr(document,section_name)
        if document.style=="brief" and not items:continue
        word.add_heading(f"{number} {labels[section_name]}",level=1);number+=1
        if not items:
            paragraph=word.add_paragraph();_write_docx_text(paragraph,"无明确内容",color=MUTED);continue
        if _section_kind(document,section_name)=="decision_table":
            _docx_table(word,["决策事项","负责人","生效或完成时间"],
                [[item,item.owner or "待确认",item.due_date or "待确认"] for item in items],[10.1,3,3.7],0,reference_numbers);continue
        if _section_kind(document,section_name)=="action_table":
            _docx_table(word,["行动项","负责人","截止时间","状态"],
                [[item,item.owner or "待确认",item.due_date or "待确认",item.status or "待开始"] for item in items],[8.5,2.6,3.4,2.3],0,reference_numbers);continue
        for item in items:
            paragraph=word.add_paragraph(style="List Bullet");paragraph.paragraph_format.keep_together=True
            _write_docx_item(paragraph,item,reference_numbers)
            details=[]
            if item.owner:details.append("负责人："+item.owner)
            if item.due_date:details.append("时间："+item.due_date)
            if item.status:details.append("状态："+item.status)
            if details:_write_docx_text(paragraph,"（"+"；".join(details)+"）",size=9,color=MUTED)
    if ordered:
        word.add_heading(f"{number} 原文依据",level=1)
        rows=[]
        for sid in ordered:
            segment=by_id[sid];rows.append([f"[{reference_numbers[sid]}]",stamp(segment.get("start")),segment.get("speaker") or "未分配",_excerpt(segment.get("text"))])
        _docx_table(word,["编号","时间","发言人","原文摘录"],rows,[1.5,2.2,2.8,10.3])
    output=BytesIO();word.save(output);return output.getvalue()


class _NumberedCanvas(Canvas):
    def __init__(self,*args,**kwargs):super().__init__(*args,**kwargs);self._saved_page_states=[]
    def showPage(self):self._saved_page_states.append(dict(self.__dict__));self._startPage()
    def save(self):
        page_count=len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state);self.setFillColor(colors.HexColor("#667085"));self.setFont("MinutesBody",8)
            self.drawCentredString(A4[0]/2,11*mm,f"第 {self._pageNumber} 页 / 共 {page_count} 页");Canvas.showPage(self)
        Canvas.save(self)


def _register_pdf_fonts():
    if "MinutesBody" in pdfmetrics.getRegisteredFontNames():return
    regular=next((path for path in (Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),Path("/Library/Fonts/Arial Unicode.ttf")) if path.exists()),None)
    bold=Path("/System/Library/Fonts/STHeiti Medium.ttc")
    if not regular or not bold.exists():raise RuntimeError("当前系统缺少 PDF 中文字体")
    pdfmetrics.registerFont(TTFont("MinutesBody",str(regular)))
    pdfmetrics.registerFont(TTFont("MinutesBold",str(bold)))
    pdfmetrics.registerFontFamily("MinutesBody",normal="MinutesBody",bold="MinutesBold")


def _pdf_styles():
    return {
        "title":ParagraphStyle("title",fontName="MinutesBold",fontSize=20,leading=27,textColor=colors.black,alignment=TA_CENTER,spaceAfter=5*mm,wordWrap="CJK"),
        "subtitle":ParagraphStyle("subtitle",fontName="MinutesBody",fontSize=9,leading=13,textColor=colors.HexColor("#667085"),alignment=TA_CENTER,spaceAfter=8*mm,wordWrap="CJK"),
        "heading":ParagraphStyle("heading",fontName="MinutesBold",fontSize=13,leading=18,textColor=colors.black,spaceBefore=5*mm,spaceAfter=2.5*mm,keepWithNext=True,wordWrap="CJK"),
        "body":ParagraphStyle("body",fontName="MinutesBody",fontSize=10,leading=15,textColor=colors.HexColor("#1E293B"),spaceAfter=2.2*mm,wordWrap="CJK"),
        "bullet":ParagraphStyle("bullet",fontName="MinutesBody",fontSize=10,leading=15,textColor=colors.HexColor("#1E293B"),leftIndent=5*mm,firstLineIndent=-3.5*mm,spaceAfter=2.2*mm,wordWrap="CJK"),
        "cell":ParagraphStyle("cell",fontName="MinutesBody",fontSize=9,leading=13,textColor=colors.HexColor("#1E293B"),wordWrap="CJK"),
        "head":ParagraphStyle("head",fontName="MinutesBold",fontSize=9,leading=13,textColor=colors.white,wordWrap="CJK"),
        "muted":ParagraphStyle("muted",fontName="MinutesBody",fontSize=8.5,leading=12,textColor=colors.HexColor("#667085"),wordWrap="CJK"),
    }


def _pdf_paragraph(value,style,marks=""):
    content=escape(str(value or "待确认"))
    if marks:content+=f' <font name="MinutesBody" size="8" color="#667085">{escape(marks)}</font>'
    return Paragraph(content,style)


def _pdf_table(headers,rows,widths,styles,item_column=None,reference_numbers=None):
    data=[[_pdf_paragraph(label,styles["head"]) for label in headers]]
    for row in rows:
        values=[]
        for index,value in enumerate(row):
            if index==item_column and isinstance(value,MinutesItem):
                values.append(_pdf_paragraph(value.content,styles["cell"],_reference_marks(value.source_segment_ids,reference_numbers or {})))
            else:values.append(_pdf_paragraph(value,styles["cell"]))
        data.append(values)
    table=LongTable(data,colWidths=[width*mm for width in widths],repeatRows=1,hAlign="LEFT",splitByRow=1)
    commands=[("BACKGROUND",(0,0),(-1,0),colors.HexColor("#253A5B")),("GRID",(0,0),(-1,-1),0.5,colors.HexColor("#D9E0E8")),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),("LEFTPADDING",(0,0),(-1,-1),6),("RIGHTPADDING",(0,0),(-1,-1),6),
        ("TOPPADDING",(0,0),(-1,-1),6),("BOTTOMPADDING",(0,0),(-1,-1),6)]
    for row_index in range(2,len(data),2):commands.append(("BACKGROUND",(0,row_index),(-1,row_index),colors.HexColor("#F5F7FA")))
    table.setStyle(TableStyle(commands));return table


def _pdf_header(canvas,doc):
    canvas.saveState();canvas.setFillColor(colors.HexColor("#667085"));canvas.setFont("MinutesBody",8)
    canvas.drawString(20*mm,A4[1]-12*mm,_excerpt(doc.meeting_title,48))
    if doc.meeting_date:canvas.drawRightString(A4[0]-20*mm,A4[1]-12*mm,doc.meeting_date)
    canvas.restoreState()


def minutes_pdf(document: MeetingMinutes, segments: list[dict], include_citations=False):
    """Render a polished A4 PDF with wrapping tables, page headers and source appendix."""
    _register_pdf_fonts();styles=_pdf_styles();by_id,reference_numbers,ordered=_citation_index(document,segments)
    if not include_citations:reference_numbers={};ordered=[]
    output=BytesIO();pdf=BaseDocTemplate(output,pagesize=A4,leftMargin=20*mm,rightMargin=20*mm,topMargin=20*mm,bottomMargin=19*mm,
        title=document.title,author="听记");pdf.meeting_title=document.title;pdf.meeting_date=document.meeting_date
    frame=Frame(pdf.leftMargin,pdf.bottomMargin,pdf.width,pdf.height,id="content",leftPadding=0,rightPadding=0,topPadding=0,bottomPadding=0)
    pdf.addPageTemplates(PageTemplate(id="minutes",frames=[frame],onPage=_pdf_header))
    story=[_pdf_paragraph(document.title,styles["title"]),_pdf_paragraph(_document_label(document)+(" · "+document.meeting_date if document.meeting_date else ""),styles["subtitle"])]
    number=1;story.append(_pdf_paragraph(f"{number} 会议基本信息",styles["heading"]));number+=1
    metadata=[("日期",document.meeting_date),("会议形式",document.meeting_form),("地点",document.location),("参会人员",document.attendees),("记录人",document.recorder),("会议目的",document.purpose)]
    metadata=[item for item in metadata if item[1]]
    meta_data=[]
    for label,value in metadata:
        marks=_reference_marks(document.basic_info_source_segment_ids,reference_numbers) if label=="会议目的" else ""
        meta_data.append([_pdf_paragraph(label,styles["head"]),_pdf_paragraph(value,styles["cell"],marks)])
    meta=LongTable(meta_data,colWidths=[28*mm,142*mm],splitByRow=1)
    meta.setStyle(TableStyle([("BACKGROUND",(0,0),(0,-1),colors.HexColor("#253A5B")),("GRID",(0,0),(-1,-1),0.5,colors.HexColor("#D9E0E8")),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),("LEFTPADDING",(0,0),(-1,-1),7),("RIGHTPADDING",(0,0),(-1,-1),7),
        ("TOPPADDING",(0,0),(-1,-1),6),("BOTTOMPADDING",(0,0),(-1,-1),6)]));story.append(meta)
    labels,sections=_style_layout(document)
    for section_name in sections:
        items=getattr(document,section_name)
        if document.style=="brief" and not items:continue
        story.append(_pdf_paragraph(f"{number} {labels[section_name]}",styles["heading"]));number+=1
        if not items:story.append(_pdf_paragraph("无明确内容",styles["muted"]));continue
        if _section_kind(document,section_name)=="decision_table":
            story.append(_pdf_table(["决策事项","负责人","生效或完成时间"],[[item,item.owner or "待确认",item.due_date or "待确认"] for item in items],[102,30,38],styles,0,reference_numbers));continue
        if _section_kind(document,section_name)=="action_table":
            story.append(_pdf_table(["行动项","负责人","截止时间","状态"],[[item,item.owner or "待确认",item.due_date or "待确认",item.status or "待开始"] for item in items],[86,26,35,23],styles,0,reference_numbers));continue
        for item in items:
            details=[]
            if item.owner:details.append("负责人："+item.owner)
            if item.due_date:details.append("时间："+item.due_date)
            if item.status:details.append("状态："+item.status)
            text="• "+escape(item.content)
            if details:text+=f' <font size="8.5" color="#667085">（{escape("；".join(details))}）</font>'
            marks=_reference_marks(item.source_segment_ids,reference_numbers)
            if marks:text+=f' <font size="8" color="#667085">{escape(marks)}</font>'
            story.append(Paragraph(text,styles["bullet"]))
    if ordered:
        story.append(_pdf_paragraph(f"{number} 原文依据",styles["heading"]))
        rows=[]
        for sid in ordered:
            segment=by_id[sid];rows.append([f"[{reference_numbers[sid]}]",stamp(segment.get("start")),segment.get("speaker") or "未分配",_excerpt(segment.get("text"))])
        story.append(_pdf_table(["编号","时间","发言人","原文摘录"],rows,[14,22,28,106],styles))
    pdf.build(story,canvasmaker=_NumberedCanvas);return output.getvalue()
