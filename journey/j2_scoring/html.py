"""Render the self-contained offline HTML companion for production comparisons."""

import json
from pathlib import Path


def render_html(report):
    lab = Path(__file__).resolve().parent
    template = (lab / "templates" / "compare.html").read_text(encoding="utf-8")
    chart_js = (lab / "assets" / "chart.umd.min.js").read_text(encoding="utf-8")
    data = json.dumps(report, ensure_ascii=False).replace("<", "\\u003c")
    return template.replace("/*__CHART_JS__*/", chart_js).replace("/*__REPORT_DATA__*/", data)
