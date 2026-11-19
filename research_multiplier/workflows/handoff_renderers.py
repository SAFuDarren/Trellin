"""Render the same research snapshot as standalone HTML and editable Markdown."""

from html import escape
import json
import re


_LABELS = {
    "Research handoff": "科研交接",
    "Snapshot": "快照",
    "Captured at (UTC)": "快照时间（UTC）",
    "Snapshot SHA-256": "快照 SHA-256",
    "Workspace ID": "工作区编号",
    "Scope": "范围",
    "Whole project": "全部课题",
    "Selected claims": "所选命题",
    "Claim": "命题",
    "Title": "标题",
    "Statement": "陈述",
    "Status": "状态",
    "Reference": "引用编号",
    "Frozen assumptions": "冻结的假设",
    "Basis reviewed": "依据已审阅",
    "Yes": "是",
    "No": "否",
    "Exact formal statement": "精确形式化陈述",
    "Frozen conclusion": "冻结的结论",
    "Proof and basis nodes": "证明及依据节点",
    "Dependencies": "依赖",
    "Evidence and verification": "证据及核验",
    "Sources": "来源",
    "Notes from the researcher": "研究者笔记",
    "Open decisions and blockers": "待判断事项及卡点",
    "Next step": "下一步",
    "Project decisions and drafts": "课题待判断事项及草稿",
    "More items are available in Trellin.": "Trellin 中还有其他条目。",
    "No records in this section.": "本节没有记录。",
}


def _value(value):
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def _records(records, primary, *, hidden=()):
    for item in records:
        title = item.get(primary)
        data = {key: value for key, value in item.items() if key != primary and key not in hidden and value is not None}
        # Keep a searchable identifier beside each mathematical statement or finding.
        rest = "\n".join(f"{key}: {_value(value)}" for key, value in data.items())
        yield "\n".join(part for part in (_value(title) if title is not None else "", rest) if part)


def _hypotheses(items):
    for item in items:
        formal = item.get("formal") or {}
        lean = formal.get("lean") if isinstance(formal, dict) else None
        proposition = lean.get("proposition") if isinstance(lean, dict) else lean
        if not isinstance(proposition, str):
            proposition = None
        other = {key: value for key, value in item.items() if key not in {"label", "statement", "formal"}}
        if formal and formal not in ({"lean": proposition}, {"lean": {"proposition": proposition}}):
            other["formal"] = formal
        text = "\n".join([item["statement"], *([proposition] if proposition else []), *([_value(other)] if other else [])])
        yield item["label"], text


def _sections(document, captured_at, digest, label):
    for index, item in enumerate(document["claims"], start=1):
        graph = item["graph"]
        claim = graph["claim"]
        goal = next((node for node in graph["nodes"] if node["kind"] == "goal"), {})
        scope = item["scope"]
        formal = goal.get("formalization") or scope["lean_proposition"] or scope["formal_target"]
        conclusion = scope["conclusion"]
        yield 2, f"{label('Claim')} {index}", [
            (label("Title"), claim["title"]), (label("Statement"), claim["statement"]),
            (label("Status"), claim["status"]),
            (label("Exact formal statement"), _value(formal) if formal else None),
            (label("Frozen conclusion"), _value(conclusion) if conclusion and conclusion != claim["statement"] else None),
            (label("Basis reviewed"), label("Yes" if scope["basis_reviewed"] else "No")),
        ]
        yield 3, label("Frozen assumptions"), list(_hypotheses(scope["hypotheses"]))
        action = item["next"]
        yield 3, label("Next step"), [(None, action["message"]),
                                      (label("Reference"), _value(action["reference"]) if action.get("reference") else None)]
        by_id = {node["id"]: node.get("code", node["id"]) for node in graph["nodes"]}
        nodes = [(label("Reference"), _value(item["reference"]))]
        nodes.extend((None, record) for record in _records(
            (node for node in graph["nodes"] if node["kind"] != "goal"), "title",
        ))
        yield 3, label("Proof and basis nodes"), nodes
        yield 3, label("Dependencies"), [(edge["kind"], f"{by_id.get(edge['source'], edge['source'])} → "
              f"{by_id.get(edge['target'], edge['target'])}" + (f" ({edge['relation']})" if edge.get("relation") else ""))
              for edge in graph["edges"]]
        yield 3, label("Evidence and verification"), [(None, record) for record in _records(item["evidence"], "id")]
        sources = []
        for source in item["sources"]:
            sources.extend((None, record) for record in _records((source,), "title", hidden=("url",)))
            if source.get("url"):
                sources.append(("url", source["url"]))
        yield 3, label("Sources"), sources
        notes = [(None, record) for record in _records(item["notes"]["guidance"], "text")]
        notes.extend((None, record) for record in _records(item["notes"]["understanding"], "explanation"))
        yield 3, label("Notes from the researcher"), notes
        pending = [(row["kind"], _value(row)) for row in (*item["decisions"], *graph["blockers"])]
        if item["decision_limit_reached"]:
            pending.append((None, label("More items are available in Trellin.")))
        yield 3, label("Open decisions and blockers"), pending
    if "project_queue" in document:
        queue = document["project_queue"]
        waiting = [(None, _value(row)) for group in queue.values() for row in group["items"]]
        if any(group["has_more"] for group in queue.values()):
            waiting.append((None, label("More items are available in Trellin.")))
        yield 2, label("Project decisions and drafts"), waiting
    yield 2, label("Snapshot"), [
        (label("Captured at (UTC)"), captured_at), (label("Snapshot SHA-256"), digest),
        (label("Workspace ID"), document["workspace_id"]),
        (label("Scope"), label("Whole project" if document["scope"] == "project" else "Selected claims")),
    ]


_CSS = """
@page { margin: 15mm; }
body { font: 16px/1.5 system-ui,sans-serif; max-width: 820px; padding: 0 24px; margin: 32px auto; color: #1e293b; }
h1,h2,h3 { line-height: 1.2; break-after: avoid; }
h2 { border-bottom: 1px solid; padding-bottom: 6px; margin-top: 30px; }
h3 { margin-top: 24px; }
p { margin: 0.5em 0; }
dt { font-weight: 600; }
dd { margin: 0 0 10px; }
pre { margin: 0 0 12px; white-space: pre-wrap; overflow-wrap: anywhere; font: inherit; }
a { overflow-wrap: anywhere; }
@media print { body { color: #000; margin: 0; padding: 0; font-size: 11pt; } a { color: inherit; } }
"""


def _literal(text):
    fences = re.findall(r"`+", text)
    fence = "`" * max(3, 1 + max(map(len, fences), default=0))
    return f"{fence}text\n{text}\n{fence}"


def _markdown_label(text):
    inline = escape(" ".join(text.split()), quote=False)
    return "**" + re.sub(r"([\\`*_{}\[\]()#+.!|])", r"\\\1", inline) + "**"


def render(document, *, captured_at, digest, language):
    """Render fixed headings and literal research text without executable content."""
    label = _LABELS.get if language == "zh-CN" else None

    def translated(value):
        return label(value, value) if label else value

    title = translated("Research handoff")
    html = [f'<!doctype html><html lang="{escape(language)}"><meta charset="utf-8"><title>{escape(title)}</title>',
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; '
            'base-uri \'none\'; form-action \'none\'">',
            '<meta name="referrer" content="no-referrer">', f"<style>{_CSS}</style><main><h1>{escape(title)}</h1>"]
    markdown = [f"# {title}"]
    for level, heading, items in _sections(document, captured_at, digest, translated):
        html.extend((f"<h{level}>{escape(heading)}</h{level}>", "<dl>"))
        markdown.append(f"{'#' * level} {heading}")
        if not items:
            items = [(None, translated("No records in this section."))]
        for term, value in items:
            if value is None:
                continue
            if term == "url":
                html.append(f'<dd><a href="{escape(value, quote=True)}" rel="noreferrer">{escape(value)}</a></dd>')
                markdown.append(f"<{value}>")
            else:
                if term:
                    html.append(f"<dt>{escape(term)}</dt>")
                    markdown.append(_markdown_label(term))
                html.append(f"<dd><pre>{escape(value)}</pre></dd>")
                markdown.append(_literal(value))
        html.append("</dl>")
    html.append("</main></html>")
    return {"handoff.html": "\n".join(html) + "\n", "handoff.md": "\n\n".join(markdown) + "\n"}
