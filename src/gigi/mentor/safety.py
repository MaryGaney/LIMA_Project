"""
Conversation triage for LIMA check-ins.

Decides, for each thing a student says, whether LIMA should:
  * "urgent":   stop the scripted check-in, give crisis resources, and flag LLC staff now
  * "followup": acknowledge, point to a resource, and flag for the Peer Mentor
  * "none":     carry on

This is a transparent keyword screen, not a clinical assessment. It will miss
things (indirect language, sarcasm, typos) and raise false alarms. It exists so the
robot never answers a disclosure with a cheerful scripted question, and so a human
is always pulled in. Phrases must be reviewed with LLC staff, and every session
needs human supervision. Never replace this with an LLM judgement alone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

URGENT_PATTERNS = [
    r"\bsuicid\w*", r"\bkill(ing)? myself\b", r"\bend(ing)? (my|it all|my life)\b", r"\bwant(ed)? to die\b",
    r"\bdon'?t want to (be alive|live|exist)\b", r"\bhurt(ing)? myself\b", r"\bself[- ]?harm\w*",
    r"\bcut(ting)? myself\b", r"\bno reason to live\b", r"\bbetter off dead\b", r"\boverdos\w*",
    r"\b(hurt|kill) (someone|somebody|him|her|them)\b", r"\bnot safe at home\b", r"\bsomeone (is )?hurting me\b",
    r"\b(abus(e|ed|ing)|assault(ed)?)\b",
]
FOLLOWUP_PATTERNS = [
    r"\boverwhelm\w*", r"\b(stopped|stop|haven'?t been|not) (going|gone) to class\w*", r"\bskipp?(ing|ed) class\w*",
    r"\bcan'?t sleep\b", r"\bcannot sleep\b", r"\bnot (sleeping|eating)\b", r"\bpanic\w*", r"\banxi\w+",
    r"\bdepress\w*", r"\bhopeless\w*", r"\blonely\b", r"\bhomesick\w*", r"\bfail(ing|ed)?\b",
    r"\bdrop(ping)? out\b", r"\bno friends\b", r"\beat(ing)? alone\b", r"\bburn(ed|t)? out\b", r"\bcrying\b",
    r"\bfalling apart\b", r"\bcan'?t (cope|handle)\b",
]

_URGENT = [re.compile(p, re.IGNORECASE) for p in URGENT_PATTERNS]
_FOLLOWUP = [re.compile(p, re.IGNORECASE) for p in FOLLOWUP_PATTERNS]


@dataclass
class Triage:
    level: str = "none"
    matches: list[str] = field(default_factory=list)

    @property
    def reason(self) -> str:
        return f"Student said something matching: {', '.join(self.matches)}" if self.matches else ""


def assess(text: str) -> Triage:
    if not text:
        return Triage()
    urgent = [m.group(0) for p in _URGENT if (m := p.search(text))]
    if urgent:
        return Triage("urgent", urgent)
    followup = [m.group(0) for p in _FOLLOWUP if (m := p.search(text))]
    if followup:
        return Triage("followup", followup)
    return Triage()
