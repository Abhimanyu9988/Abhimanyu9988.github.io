"""Cover image for a post, drawn only from the lab's real output.

Claude picks what to show (a small chart, or a few terminal lines); this code checks every number and line against
the evidence logs and drops anything it can't find, then draws a 1200x630 card: the post title on top, the evidence
underneath. The same picture is the site's preview image, the dev.to cover, the top of the Medium copy and the
LinkedIn picture. Important content stays inside the middle 2.38:1 band, so dev.to's crop never cuts it.
"""
import re, textwrap

W, H, DPI = 12.0, 6.3, 100
BG, PANEL, TEXT, MUTED, ACCENT, ACCENT2 = "#0b1220", "#111a2e", "#f1f5f9", "#94a3b8", "#f59e0b", "#38bdf8"

FIGURE_SCHEMA = {"type": "object", "required": ["kind", "alt"], "properties": {
    "kind": {"enum": ["chart", "terminal"]},
    "alt": {"type": "string", "description": "one sentence describing the picture for screen readers"},
    "chart": {"type": "object", "properties": {
        "title": {"type": "string", "description": "max 60 characters, e.g. 'Scale-in 4 -> 1: tasks counted per minute'"},
        "x": {"type": "array", "items": {"type": "string"}, "description": "x labels exactly as in EVIDENCE"},
        "series": {"type": "array", "maxItems": 3, "items": {"type": "object", "required": ["name", "values"],
                   "properties": {"name": {"type": "string"}, "values": {"type": "array", "items": {"type": "number"}}}}},
        "y_label": {"type": "string"}}},
    "lines": {"type": "array", "maxItems": 6, "items": {"type": "string"},
              "description": "2 to 6 lines copied exactly from EVIDENCE"},
    "highlight": {"type": "array", "items": {"type": "integer"}, "description": "indexes of the 1-2 key lines"}}}

PICK = """Pick what the post's cover image shows: the one piece of EVIDENCE that makes the problem obvious at a glance.
- "chart": when EVIDENCE has the same numbers over time or before/after (a metric per minute, a count that should
  match but doesn't). x labels and every value must be copied exactly from EVIDENCE, each series name must be the
  name EVIDENCE prints right before its value (e.g. LiveTaskCount for "LiveTaskCount=4.0"), and each x label must
  appear on the same EVIDENCE line as its values. 3 to 12 points, 1 to 3 series with short names.
- "terminal": otherwise. 2 to 6 lines copied exactly from EVIDENCE (whole lines, no edits, no commands with secrets),
  showing the symptom and, if they fit, the fix working. "highlight" marks the 1 or 2 lines that matter most.
Never invent or round anything; the picture is checked line by line against EVIDENCE and anything not found is dropped."""

def _squash(s):
    return re.sub(r"\s+", " ", str(s)).strip()

def _num_forms(v):
    forms = {repr(v), f"{v:.1f}", f"{v:.2f}"}
    if float(v).is_integer():
        forms.add(str(int(v)))
    return forms

def _has_pair(line, name, v):
    """The series name followed by its value on this line, e.g. 'LiveTaskCount=4.0' or 'running: 3'."""
    return any(re.search(rf"{re.escape(name)}\s*[=:]?\s*{re.escape(f)}(?![\d])", line) for f in _num_forms(v))

def verify(fig, evidence):
    """Keep only what EVIDENCE shows. Returns a cleaned figure, or None if too little survives."""
    if not isinstance(fig, dict):
        return None
    lines = [_squash(l) for l in evidence.splitlines() if l.strip()]
    if fig.get("kind") == "chart":
        c = fig.get("chart") or {}
        xs = [str(x).strip() for x in c.get("x") or []]
        series = [s for s in c.get("series") or [] if isinstance(s, dict) and len(s.get("values") or []) == len(xs)]
        if not 3 <= len(xs) <= 12 or not 1 <= len(series) <= 3:
            return None
        for i, x in enumerate(xs):
            if not x or not any(x in l and all(_has_pair(l, _squash(s["name"]), float(s["values"][i])) for s in series)
                                for l in lines):
                return None
        return {"kind": "chart", "alt": str(fig.get("alt") or "")[:300], "chart": {
            "title": _squash(c.get("title") or "")[:70], "x": xs, "y_label": _squash(c.get("y_label") or "")[:30],
            "series": [{"name": _squash(s["name"])[:40], "values": [float(v) for v in s["values"]]} for s in series]}}
    blob = "\n".join(lines)
    keep, hl = [], []
    for i, l in enumerate(fig.get("lines") or []):
        sq = _squash(l)
        if sq and sq in blob and not re.search(r"(?i)(secret|token|password|AKIA|ASIA|HERO_RESULT)", sq):
            if i in (fig.get("highlight") or []):
                hl.append(len(keep))
            keep.append(sq)
    if len(keep) < 2:
        return None
    return {"kind": "terminal", "alt": str(fig.get("alt") or "")[:300], "lines": keep[:6], "highlight": hl[:2]}

def render(title, fig, tags, out_path, kind="problem"):
    """Draw the card to out_path (PNG). fig may be None: then it's the title card alone."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch
    f = plt.figure(figsize=(W, H), dpi=DPI, facecolor=BG)
    label = {"build": "BUILT AND TESTED IN A REAL AWS LAB", "monthly": "MONTHLY OPEN-SOURCE SUMMARY"}.get(
        kind, "REPRODUCED IN A REAL AWS LAB") + ("  \u00b7  " + "  ".join(t.upper() for t in tags[:3]) if tags else "")
    size, width = (30, 38) if len(title) <= 76 else (26, 46)   # fits DejaVu, the runner's font
    tl = textwrap.wrap(title, width)[:3]
    title_h = len(tl) * (size * 1.15 / (H * 72))
    panel_h = (0.11 + len(fig["lines"]) * 0.058) if fig and fig["kind"] == "terminal" else None
    off = 0.0
    if fig is None or panel_h:                      # a short block: centre it instead of leaving a gap below
        off = max(0.0, (0.815 - title_h - 0.045 - (panel_h or 0) - 0.155) / 2)
    f.text(0.06, 0.865 - off, label, color=ACCENT, fontsize=12.5, fontweight="bold", va="top")
    f.text(0.06, 0.815 - off, "\n".join(tl), color=TEXT, fontsize=size, fontweight="bold", va="top", linespacing=1.15)
    top = 0.815 - off - title_h - 0.045
    bottom = top - panel_h if panel_h else 0.155
    f.text(0.06, 0.112, "abhimanyu9988.github.io", color=MUTED, fontsize=12, va="center")
    f.text(0.94, 0.112, "Abhimanyu Bajaj", color=MUTED, fontsize=12, va="center", ha="right")
    if fig and top - bottom > 0.12:
        box = FancyBboxPatch((0.06, bottom), 0.88, top - bottom, boxstyle="round,pad=0,rounding_size=0.015",
                             transform=f.transFigure, facecolor=PANEL, edgecolor="#1e293b", linewidth=1.2)
        f.patches.append(box)
        if fig["kind"] == "terminal":
            for k, c in enumerate(("#ef4444", "#f59e0b", "#22c55e")):
                f.text(0.08 + k * 0.018, top - 0.035, "\u25cf", color=c, fontsize=10, va="center")
            step = 0.058
            for i, line in enumerate(fig["lines"]):
                txt = line if len(line) <= 92 else line[:91] + "\u2026"
                f.text(0.08, top - 0.085 - i * step, txt, family="monospace", fontsize=13,
                       color=ACCENT if i in fig["highlight"] else TEXT, va="center",
                       fontweight="bold" if i in fig["highlight"] else "normal")
        else:
            c = fig["chart"]
            ax = f.add_axes([0.11, bottom + 0.075, 0.8, top - bottom - 0.15], zorder=2)
            ax.set_facecolor(PANEL)
            colors = (ACCENT, ACCENT2, "#a78bfa")
            xs = list(range(len(c["x"])))
            for s, col in zip(c["series"], colors):
                ax.plot(xs, s["values"], marker="o", linewidth=2.6, markersize=6, color=col, label=s["name"])
            short = [re.sub(r"^\d{4}-\d\d-\d\dT(\d\d:\d\d).*$", r"\1", x) for x in c["x"]]
            ax.set_xticks(xs, short, color=MUTED, fontsize=10.5)
            ax.tick_params(colors=MUTED, labelsize=10.5)
            for side in ax.spines.values():
                side.set_color("#334155")
            ax.grid(axis="y", color="#1e293b")
            if c.get("y_label"):
                ax.set_ylabel(c["y_label"], color=MUTED, fontsize=11)
            if c.get("title"):
                ax.set_title(c["title"], color=TEXT, fontsize=13, loc="left", pad=8)
            leg = ax.legend(loc="upper right", frameon=False, fontsize=11)
            for t in leg.get_texts():
                t.set_color(TEXT)
    f.savefig(out_path, dpi=DPI, facecolor=BG)
    plt.close(f)
    return out_path

SHOT_SCHEMA = {"type": "object", "required": ["images"], "properties": {"images": {"type": "array", "maxItems": 3,
    "items": {"type": "object", "required": ["after_heading", "lines", "caption"], "properties": {
        "after_heading": {"type": "string", "description": "the exact text of the ## or ### heading whose section "
                          "this picture belongs to"},
        "lines": {"type": "array", "maxItems": 14, "items": {"type": "string"},
                  "description": "3 to 14 lines copied exactly from EVIDENCE"},
        "caption": {"type": "string", "description": "short title bar text, e.g. 'kubectl get pods after the annotation'"},
        "highlight": {"type": "array", "items": {"type": "integer"}}}}}}}

SHOTS = """Pick up to 3 moments in the lab's real output that a reader would want to SEE while following the post (the
before state, the key step working, the proof). For each: the heading of the post section it belongs to, 3 to 14
lines copied exactly from EVIDENCE (whole lines, no edits, nothing secret), a short caption, and the 1 or 2 lines to
highlight. These become pictures of the terminal output; every line is checked against EVIDENCE and anything not
found is dropped. Don't repeat what the cover already shows."""

def render_shot(lines, caption, out_path, highlight=()):
    """A picture of real terminal output: a window with a caption in the title bar and the lines in monospace."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch
    h_px = 110 + 34 * len(lines)
    f = plt.figure(figsize=(W, h_px / DPI), dpi=DPI, facecolor=BG)
    hf = lambda px: px / h_px                     # pixels from the top -> figure fraction from the bottom
    box = FancyBboxPatch((0.02, hf(18)), 0.96, 1 - hf(36), boxstyle="round,pad=0,rounding_size=0.01",
                         transform=f.transFigure, facecolor=PANEL, edgecolor="#1e293b", linewidth=1.2)
    f.patches.append(box)
    for k, c in enumerate(("#ef4444", "#f59e0b", "#22c55e")):
        f.text(0.04 + k * 0.018, 1 - hf(48), "●", color=c, fontsize=10, va="center")
    f.text(0.5, 1 - hf(48), caption[:80], color=MUTED, fontsize=12, va="center", ha="center")
    for i, line in enumerate(lines):
        txt = line if len(line) <= 108 else line[:107] + "…"
        f.text(0.04, 1 - hf(92 + i * 34), txt, family="monospace", fontsize=12.5,
               color=ACCENT if i in highlight else TEXT, fontweight="bold" if i in highlight else "normal", va="center")
    f.savefig(out_path, dpi=DPI, facecolor=BG)
    plt.close(f)
    return out_path
