from __future__ import annotations

import html
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .email_templates import (
    EMAIL_TYPE_SPECS,
    RECIPIENT_ALIASES,
    ensure_template_variables_available,
    load_data_file,
    read_template,
    render_template,
    value_for_aliases,
)


@dataclass(frozen=True)
class EmailPreviewResult:
    path: Path
    count: int


def build_email_preview_page(
    *,
    email_type: str,
    data_file: Path,
    template_file: Path,
    subject_template: str,
    output_dir: Path,
) -> EmailPreviewResult:
    data = load_data_file(data_file)
    if not data.rows:
        raise ValueError("数据文件没有可预览的数据行。")

    template_text = read_template(template_file)
    spec = EMAIL_TYPE_SPECS[email_type]
    is_html_template = template_file.suffix.lower() in {".html", ".htm"}
    items = []
    for index, row in enumerate(data.rows, start=1):
        ensure_template_variables_available(subject_template, row, spec, source="邮件标题")
        ensure_template_variables_available(template_text, row, spec, source="邮件模板")
        subject = render_template(subject_template, row, spec).strip() or email_type
        body = render_template(template_text, row, spec)
        if not is_html_template:
            body = _text_body_to_html(body)
        items.append(
            {
                "index": index,
                "recipient": value_for_aliases(row, RECIPIENT_ALIASES) or "",
                "subject": subject,
                "html": body,
            }
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"email-preview-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.html"
    path.write_text(_preview_shell(items), encoding="utf-8")
    return EmailPreviewResult(path=path, count=len(items))


def _text_body_to_html(text: str) -> str:
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\"></head>"
        "<body style=\"margin:0;padding:24px;font-family:Arial,sans-serif;\">"
        f"<pre style=\"white-space:pre-wrap;line-height:1.55;\">{html.escape(text)}</pre>"
        "</body></html>"
    )


def _preview_shell(items: list[dict[str, str | int]]) -> str:
    payload = json.dumps(items, ensure_ascii=False).replace("<", "\\u003c")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>邮件预览</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: #eef2f5; color: #111827; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif; }}
    .toolbar {{ position: sticky; top: 0; z-index: 10; display: flex; gap: 12px; align-items: center; padding: 12px 16px; background: #ffffff; border-bottom: 1px solid #d8dee6; box-shadow: 0 1px 8px rgba(15, 23, 42, 0.08); }}
    button {{ border: 1px solid #cbd5e1; background: #f8fafc; color: #0f172a; border-radius: 8px; padding: 8px 14px; font-size: 14px; cursor: pointer; }}
    button:hover {{ background: #e2e8f0; }}
    button:disabled {{ opacity: 0.45; cursor: not-allowed; }}
    .meta {{ min-width: 0; flex: 1; font-size: 14px; color: #475569; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .subject {{ color: #111827; font-weight: 700; }}
    .counter {{ font-weight: 700; color: #0f766e; }}
    iframe {{ display: block; width: 100%; height: calc(100vh - 58px); border: 0; background: #ffffff; }}
  </style>
</head>
<body>
  <div class="toolbar">
    <button id="prevBtn" type="button">← 上一条</button>
    <button id="nextBtn" type="button">下一条 →</button>
    <span class="counter" id="counter"></span>
    <div class="meta">
      <span id="recipient"></span>
      <span id="separator"></span>
      <span class="subject" id="subject"></span>
    </div>
  </div>
  <iframe id="previewFrame" title="邮件内容预览"></iframe>
  <script id="preview-data" type="application/json">{payload}</script>
  <script>
    const items = JSON.parse(document.getElementById("preview-data").textContent);
    let current = 0;
    const frame = document.getElementById("previewFrame");
    const counter = document.getElementById("counter");
    const recipient = document.getElementById("recipient");
    const separator = document.getElementById("separator");
    const subject = document.getElementById("subject");
    const prevBtn = document.getElementById("prevBtn");
    const nextBtn = document.getElementById("nextBtn");

    function show(index) {{
      current = Math.max(0, Math.min(index, items.length - 1));
      const item = items[current] || {{ html: "", recipient: "", subject: "" }};
      frame.srcdoc = item.html;
      counter.textContent = `${{current + 1}} / ${{items.length}}`;
      recipient.textContent = item.recipient ? `收件人：${{item.recipient}}` : "";
      separator.textContent = item.recipient && item.subject ? "  |  " : "";
      subject.textContent = item.subject ? `主题：${{item.subject}}` : "";
      prevBtn.disabled = current <= 0;
      nextBtn.disabled = current >= items.length - 1;
    }}

    prevBtn.addEventListener("click", () => show(current - 1));
    nextBtn.addEventListener("click", () => show(current + 1));
    document.addEventListener("keydown", (event) => {{
      if (event.key === "ArrowLeft") show(current - 1);
      if (event.key === "ArrowRight") show(current + 1);
    }});
    show(0);
  </script>
</body>
</html>"""
