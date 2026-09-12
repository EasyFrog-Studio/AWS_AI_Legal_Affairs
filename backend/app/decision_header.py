"""決定書表頭在 F4 落地那一刻的預設值。

預設值實際寫進 case.decision_header,不是渲染時回落——承辦人之後改的就是這份,畫面上改了
下載的一定跟著改。發文日期與發文字號發文時才由案管系統配號,留白印空格線。
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
    )
