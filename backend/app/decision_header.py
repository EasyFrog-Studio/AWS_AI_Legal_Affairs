"""決定書表頭在 F4 落地那一刻的預設值。

預設值實際寫進 case.decision_header,不是渲染時回落——承辦人之後改的就是這份,畫面上改了
下載的一定跟著改。發文日期與發文字號發文時才由案管系統配號,留空。
"""
from __future__ import annotations

import re

from app.models import Case, DecisionHeader

_CITED_LAW = re.compile(r"^(?P<law>[^#]+)#(?P<article>.+)$")


def related_laws_from_cited(cited_laws: list[str]) -> str:
    """「廢棄物清理法#46」-> 「廢棄物清理法 第 46 條」,一行一條;沒有條號的原樣保留。"""
    lines = []
    for item in cited_laws:
        m = _CITED_LAW.match(item.strip())
        lines.append(f"{m.group('law').strip()} 第 {m.group('article').strip()} 條" if m else item.strip())
    return "\n".join(line for line in lines if line)


_ERA_PREFIX_RE = re.compile(r"^\s*(?:中華)?民國\s*")


def opening_paragraph(f1) -> str:
    """敘明句。文書種類(裁處書/函)不在 f1,故只寫到文號;承辦人在畫面上補。"""
    date = _ERA_PREFIX_RE.sub("", (f1 and f1.disposition_date) or "")
    return (
        f"上列訴願人因{(f1 and f1.case_type) or ''}事件，不服原處分機關民國{date}"
        f"{(f1 and f1.disposition_no) or ''}所為之處分，提起訴願一案，本府依法決定如下："
    )


def decision_header_defaults(case: Case) -> DecisionHeader:
    """以 case_id / f1 / f4 填出表頭;主任委員、委員、發文與決定日期留白。"""
    f1 = case.f1
    f4 = case.f4
    return DecisionHeader(
        case_no=case.case_id,
        gist=f4.gist if f4 else "",
        related_laws=related_laws_from_cited(f4.cited_laws) if f4 else "",
        appellant=f1.appellant if f1 else "",
        agent_role=f1.agent_role if f1 else "",
        agent_name=f1.agent_name if f1 else "",
        agency=f1.agency if f1 else "",
        preamble=opening_paragraph(f1),
    )
