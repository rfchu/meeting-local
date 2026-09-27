"""Editable, source-grounded meeting minutes and document exports."""
from io import BytesIO
import shutil
import subprocess
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

from pydantic import BaseModel, Field

from .analysis import AnalysisItem, MeetingAnalysis, AIMinutesResult, SECTION_NAMES, validate_citations


MINUTES_VERSION = "meeting-minutes-v2"
MINUTES_SECTIONS = ("overview", "conclusions", "decisions", "action_items", "open_questions")
MINUTES_LABELS = {
    "overview": "会议概述",
    "conclusions": "核心结论",
    "decisions": "已定事项",
    "action_items": "行动项",
    "open_questions": "待确认问题",
}


class MinutesItem(BaseModel):
    content: str = Field(min_length=1, max_length=2000)
    source_segment_ids: list[str] = Field(default_factory=list, max_length=100)
    owner: str | None = Field(default=None, max_length=200)
    due_date: str | None = Field(default=None, max_length=200)
    status: str | None = Field(default=None, max_length=100)


class MeetingMinutes(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    meeting_date: str = Field(default="", max_length=100)
    meeting_form: str = Field(default="", max_length=200)
    location: str = Field(default="", max_length=300)
    attendees: str = Field(default="", max_length=3000)
    recorder: str = Field(default="", max_length=300)
    purpose: str = Field(default="", max_length=3000)
    style: str = Field(default="formal", pattern="^(formal|brief|internal|ai)$")
    basic_info_source_segment_ids: list[str] = Field(default_factory=list, max_length=100)
    overview: list[MinutesItem] = Field(default_factory=list, max_length=50)
    conclusions: list[MinutesItem] = Field(default_factory=list, max_length=50)
    decisions: list[MinutesItem] = Field(default_factory=list, max_length=50)
    action_items: list[MinutesItem] = Field(default_factory=list, max_length=100)
    open_questions: list[MinutesItem] = Field(default_factory=list, max_length=50)

    def all_items(self):
        for section in MINUTES_SECTIONS:
            yield from getattr(self, section)


def validate_minutes_citations(document: MeetingMinutes, valid_ids: set[str]):
    if not set(document.basic_info_source_segment_ids).issubset(valid_ids):
        raise ValueError("会议纪要基本信息包含不存在的原文引用")
    for item in document.all_items():
        if not set(item.source_segment_ids).issubset(valid_ids):
            raise ValueError("会议纪要包含不存在的原文引用")


def build_minutes(title: str, created: str, segments: list[dict], analysis: MeetingAnalysis, style: str):
    """Rearrange validated analysis without asking a model to invent new facts."""
    validate_citations(analysis, {segment["id"] for segment in segments})
    speakers = list(dict.fromkeys(
        segment.get("speaker", "").strip() for segment in segments
        if segment.get("speaker", "").strip() and segment.get("speaker") != "未分配"
    ))

    def copy(items, limit=None, action=False):
        values = items[:limit] if limit else items
        return [MinutesItem(**item.model_dump(), status="待开始" if action else None) for item in values]

    if style == "brief":
        limits = {"summary": 1, "key_points": 0, "decisions": 3, "action_items": 5, "open_questions": 3}
    elif style == "formal":
        limits = {"summary": 2, "key_points": 0, "decisions": 8, "action_items": 12, "open_questions": 6}
    else:
        limits = {name: None for name in SECTION_NAMES}
    return MeetingMinutes(
        title=f"{title}会议纪要",
        meeting_date=(created or "")[:10],
        attendees="、".join(speakers),
        style=style,
        overview=copy(analysis.summary, limits["summary"]),
        conclusions=copy(analysis.key_points, limits["key_points"]) if limits["key_points"] != 0 else [],
        decisions=copy(analysis.decisions, limits["decisions"]),
        action_items=copy(analysis.action_items, limits["action_items"], action=True),
        open_questions=copy(analysis.open_questions, limits["open_questions"]),
    )


def build_ai_minutes(created: str, result: AIMinutesResult):
    def copy(items, action=False):
        return [MinutesItem(**item.model_dump(),status="待开始" if action else None) for item in items]
    topic=result.basic_info.topic.strip()
    title=topic if topic.endswith("会议纪要") else topic+"会议纪要"
    return MeetingMinutes(title=title,meeting_date=(created or "")[:10],meeting_form=result.basic_info.meeting_form,
        attendees="、".join(result.basic_info.attendees),purpose=result.basic_info.purpose,style="ai",
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
    labels, sections = _style_layout(document.style)
    for section in sections:
        lines.extend([f"## {labels[section]}", ""])
        items = getattr(document, section)
        if not items:
            lines.extend(["- 无", ""]); continue
        if section == "decisions" and document.style != "internal":
            lines.extend(["| 决策事项 | 负责人 | 生效/完成时间 |", "| --- | --- | --- |"]) 
            for item in items:
                content = _content_with_ref(item, by_id, include_citations)
                lines.append(f"| {_md_cell(content)} | {_md_cell(item.owner or '待确认')} | {_md_cell(item.due_date or '待确认')} |")
            lines.append(""); continue
        if section == "action_items" and document.style != "internal":
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


def _style_layout(style):
    if style == "ai":
        return ({"overview":"核心内容纪要","action_items":"待办事项（Action Items）","conclusions":"会议总结"},
                ("overview","action_items","conclusions"))
    if style == "brief":
        return ({"overview":"会议结果","decisions":"关键决策","action_items":"行动清单","open_questions":"待确认"},
                ("overview","decisions","action_items","open_questions"))
    if style == "internal":
        return ({"overview":"会议摘要","conclusions":"讨论要点","decisions":"决策与依据","action_items":"行动跟踪","open_questions":"风险、依赖与待确认"},
                MINUTES_SECTIONS)
    return ({"overview":"会议成果","decisions":"决策事项","action_items":"行动计划","open_questions":"风险与待确认"},
            ("overview","decisions","action_items","open_questions"))


def _md_cell(value):
    return str(value).replace("|", "\\|").replace("\n", "<br>")


def _content_with_ref(item, by_id, include_citations):
    content = item.content
    if include_citations:
        refs = _citation_text(item, by_id)
        if refs: content += f"（原文：{refs}）"
    return content


def _paragraph(text, style=None, bold=False):
    safe = escape(str(text))
    pstyle = f'<w:pStyle w:val="{style}"/>' if style else ""
    weight = "<w:b/>" if bold else ""
    return f'<w:p><w:pPr>{pstyle}</w:pPr><w:r><w:rPr>{weight}<w:rFonts w:eastAsia="PingFang SC"/></w:rPr><w:t xml:space="preserve">{safe}</w:t></w:r></w:p>'


def _table(rows):
    cells = []
    for row_index, row in enumerate(rows):
        cells.append("<w:tr>" + "".join(
            '<w:tc><w:tcPr><w:tcW w:w="0" w:type="auto"/></w:tcPr>' +
            _paragraph(value or "—", bold=row_index == 0) + "</w:tc>" for value in row
        ) + "</w:tr>")
    return '<w:tbl><w:tblPr><w:tblBorders><w:top w:val="single" w:sz="4" w:color="D9DEE8"/><w:left w:val="single" w:sz="4" w:color="D9DEE8"/><w:bottom w:val="single" w:sz="4" w:color="D9DEE8"/><w:right w:val="single" w:sz="4" w:color="D9DEE8"/><w:insideH w:val="single" w:sz="4" w:color="D9DEE8"/><w:insideV w:val="single" w:sz="4" w:color="D9DEE8"/></w:tblBorders></w:tblPr>' + "".join(cells) + "</w:tbl>"


def minutes_docx(document: MeetingMinutes, segments: list[dict], include_citations=False):
    """Create a compact Word document using only the Python standard library."""
    by_id = {segment["id"]: segment for segment in segments}
    body = [_paragraph(document.title, "Title")]
    metadata = [
        ("日期", document.meeting_date), ("地点", document.location),
        ("会议形式", document.meeting_form), ("参会人", document.attendees), ("记录人", document.recorder),
    ]
    if any(value for _, value in metadata):
        body.append(_table([["会议要素", "内容"]] + [[label, value] for label, value in metadata if value]))
    if document.purpose: body.extend([_paragraph("会议目标", "Heading1"), _paragraph(document.purpose)])
    labels, sections = _style_layout(document.style)
    for section in sections:
        body.append(_paragraph(labels[section], "Heading1"))
        items = getattr(document, section)
        if section == "decisions" and items and document.style != "internal":
            rows = [["决策事项", "负责人", "生效/完成时间"]]
            for item in items:
                rows.append([_content_with_ref(item, by_id, include_citations), item.owner or "待确认", item.due_date or "待确认"])
            body.append(_table(rows)); continue
        if section == "action_items" and items and document.style != "internal":
            rows = [["行动项", "负责人", "截止时间", "状态"]]
            for item in items:
                content = _content_with_ref(item, by_id, include_citations)
                rows.append([content, item.owner or "待确认", item.due_date or "待确认", item.status or "待开始"])
            body.append(_table(rows)); continue
        if not items:
            body.append(_paragraph("无")); continue
        for item in items:
            details = []
            if item.owner: details.append("负责人：" + item.owner)
            if item.due_date: details.append("截止：" + item.due_date)
            if item.status: details.append("状态：" + item.status)
            if include_citations:
                refs = _citation_text(item, by_id)
                if refs: details.append("原文：" + refs)
            body.append(_paragraph("• " + item.content + (f"（{'；'.join(details)}）" if details else "")))
    document_xml = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + \
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + \
        "".join(body) + '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1200" w:right="1200" w:bottom="1200" w:left="1200"/></w:sectPr></w:body></w:document>'
    styles = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:rPr><w:sz w:val="22"/><w:szCs w:val="22"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:rPr><w:b/><w:sz w:val="36"/><w:szCs w:val="36"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:rPr><w:b/><w:color w:val="365CE6"/><w:sz w:val="28"/></w:rPr></w:style>
</w:styles>'''
    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>'''
    root_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>'''
    doc_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>'''
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("word/document.xml", document_xml)
        archive.writestr("word/styles.xml", styles)
        archive.writestr("word/_rels/document.xml.rels", doc_rels)
    return output.getvalue()


def minutes_pdf(document: MeetingMinutes, segments: list[dict], include_citations=False):
    """Render a Unicode PDF with the native macOS print filter."""
    cupsfilter=shutil.which('cupsfilter') or ('/usr/sbin/cupsfilter' if Path('/usr/sbin/cupsfilter').exists() else None)
    if not cupsfilter:raise RuntimeError('当前系统缺少 PDF 转换组件')
    lines=[]
    for line in minutes_markdown(document,segments,include_citations).splitlines():
        if line.startswith('| ---'):continue
        if line.startswith('#'):line=line.lstrip('#').strip()
        line=line.replace('**','')
        if line.startswith('|'):line='  '.join(cell.strip() for cell in line.strip('|').split('|'))
        lines.append(line)
    with tempfile.NamedTemporaryFile('w',suffix='.txt',encoding='utf-8',delete=False) as source:
        source.write('\n'.join(lines));path=Path(source.name)
    try:
        result=subprocess.run([cupsfilter,'-m','application/pdf',str(path)],capture_output=True,timeout=30)
        if result.returncode or not result.stdout.startswith(b'%PDF'):
            raise RuntimeError('PDF 生成失败：'+result.stderr.decode(errors='replace')[-500:])
        return result.stdout
    except subprocess.TimeoutExpired as error:raise RuntimeError('PDF 生成超时') from error
    finally:path.unlink(missing_ok=True)
