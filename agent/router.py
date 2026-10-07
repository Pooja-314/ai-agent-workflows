"""Workflow selection.

Two strategies, both driven by the Excel definitions (nothing hard-coded per workflow):

1. LLM router  - the model sees a catalogue built from the Excel rows
                 (id, name, trigger, inputs, expected output) and returns
                 {workflow_id, confidence, reasoning, params} as JSON.
2. Lexical router (offline fallback / sanity check) - TF-IDF cosine similarity
                 between the request and a text profile of each workflow
                 (name, trigger, steps, outputs + optional keywords).

If neither is confident, the router returns no match and the agent asks the user
to clarify (listing the closest candidates) instead of guessing.
"""
from __future__ import annotations

import math
import re
from collections import Counter

from . import config, llm
from .models import RouteDecision
from .registry import Registry

_STOP = set("""a an the of to for and or in on with is are be this that these those my our me i we you it its
from by as at into than then which what who how do does did can could please show give tell find get make
need want all any new today""".split())


def _tokens(text: str) -> list[str]:
    out = []
    for t in re.findall(r"[a-z0-9]+", text.lower()):
        if t in _STOP or len(t) < 2:
            continue
        for suf in ("ing", "ed", "es", "s"):          # tiny stemmer
            if t.endswith(suf) and len(t) - len(suf) >= 3:
                t = t[: -len(suf)]
                break
        out.append(t)
    return out


class Router:
    def __init__(self, registry: Registry):
        self.reg = registry
        self._build_index()

    # -- lexical index ---------------------------------------------------------
    def _build_index(self) -> None:
        self.docs: dict[str, Counter] = {}
        for wid, spec in self.reg.specs.items():
            kw = " ".join(self.reg.meta[wid].keywords) if wid in self.reg.meta else ""
            text = " ".join([spec.name] * 3 + [spec.trigger] * 2 + [kw] * 2 +
                            [spec.inputs, " ".join(spec.steps), spec.expected_output])
            self.docs[wid] = Counter(_tokens(text))
        n = len(self.docs)
        df = Counter(t for c in self.docs.values() for t in c)
        self.idf = {t: math.log((1 + n) / (1 + d)) + 1 for t, d in df.items()}
        self.norm = {wid: math.sqrt(sum((tf * self.idf[t]) ** 2 for t, tf in c.items()))
                     for wid, c in self.docs.items()}

    def lexical_scores(self, request: str) -> list[tuple[str, float]]:
        q = Counter(_tokens(request))
        qn = math.sqrt(sum((tf * self.idf.get(t, 1)) ** 2 for t, tf in q.items())) or 1
        scores = []
        for wid, doc in self.docs.items():
            dot = sum(tf * self.idf.get(t, 1) * doc[t] * self.idf[t] for t, tf in q.items() if t in doc)
            scores.append((wid, round(dot / (qn * self.norm[wid]), 3)))
        return sorted(scores, key=lambda x: -x[1])

    # -- LLM routing -----------------------------------------------------------
    def _catalogue(self) -> str:
        lines = []
        for wid, s in self.reg.specs.items():
            lines.append(f"- {wid} | {s.name} | trigger: {s.trigger} | inputs: {s.inputs} | output: {s.expected_output}")
        return "\n".join(lines)

    # -- public ----------------------------------------------------------------
    def route(self, request: str, forced: str | None = None) -> RouteDecision:
        if forced:
            if forced not in self.reg.specs:
                return RouteDecision(None, "forced", 0.0, f"Unknown workflow id {forced}")
            return RouteDecision(forced, "forced", 1.0, "Workflow chosen explicitly by the caller.",
                                 self._extract(forced, request))

        lex = self.lexical_scores(request)
        top_id, top = lex[0]
        second = lex[1][1] if len(lex) > 1 else 0.0

        if llm.provider() != "offline":
            system = (
                "You route user requests to exactly one business workflow. "
                "Choose only from the provided catalogue; if none fits, use workflow_id null. "

                "You MUST extract parameter values explicitly stated by the user. "
                "Never invent, omit, or replace an explicitly stated parameter value. "

                "For estimated_hours, extract the numeric number of hours exactly as stated. "
                "For example, if the user says '100 hours', return "
                '{"estimated_hours": 100}. '
                
                "For role, normalize common equivalents such as "
                "'Python developer', 'Python engineer', and 'software developer' "
                "to 'developer'. "

                "For task, preserve the user's actual task description. "

                "Return valid JSON only."
            )
            wanted = {wid: self.reg.meta[wid].param_keys for wid in self.reg.specs if wid in self.reg.meta}
            user = (
                f"Workflow catalogue:\n{self._catalogue()}\n\n"
                f"Parameters each workflow understands: {wanted}\n\n"
                f"User request: {request!r}\n\n"
                'JSON: {"workflow_id": "WFxxx|null", "confidence": 0-1, "reasoning": "..."}'
            )

            def _validate(d: dict) -> None:
                if d.get("workflow_id") not in (None, "null", *self.reg.specs):
                    raise ValueError("LLM picked an unknown workflow id")

            data, src = llm.complete_json(system, user, offline=lambda: {"workflow_id": "__offline__"},
                                          validate=_validate)
            if src.startswith("llm:"):
                wid = data.get("workflow_id")
                if wid in (None, "null"):
                    return RouteDecision(None, "llm", float(data.get("confidence", 0) or 0),
                                         data.get("reasoning", "LLM found no matching workflow"),
                                         candidates=lex[:3])
                params = self._extract(wid, request)
                return RouteDecision(wid, "llm", float(data.get("confidence", 0.8) or 0.8),
                                     f"[{src}] " + data.get("reasoning", ""), params, lex[:3])

        # offline / lexical path
        if top < config.ROUTER_MIN_SCORE or (top - second) < 0.01:
            return RouteDecision(None, "lexical", top,
                                 "No workflow matched confidently; please clarify.", candidates=lex[:3])
        spec = self.reg.specs[top_id]
        return RouteDecision(top_id, "lexical", top,
                             f"Highest TF-IDF similarity ({top}) to '{spec.name}' (next best {second}).",
                             self._extract(top_id, request), lex[:3])

    def _extract(self, wid: str, request: str) -> dict:
        meta = self.reg.meta.get(wid)
        return meta.extractor(request) if meta and meta.extractor else {}
