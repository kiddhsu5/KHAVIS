"""Bounded, deterministic topic classification; never executes document content."""
from __future__ import annotations

import html
import re
import zipfile
from functools import lru_cache
from pathlib import Path
from typing import Any

TOPICS = (
    ('learning', '學習與考試', ('備考', '考前', '試題', '題庫', '考試', '教材', '課程', 'exam', 'tutorial')),
    ('brand', '品牌與行銷', ('品牌', '莫蘭迪', '行銷', 'logo', 'brand', 'marketing', 'campaign')),
    ('finance', '財務與量化', ('財務', '量化', '回測', '投資', 'backtest', 'alpha', 'cashflow', 'valuation')),
    ('operations', '部署與維運', ('部署', '維運', '監控', '告警', 'docker', 'deploy', 'nginx', 'systemd', 'quota', 'failover', 'alerting')),
    ('engineering', '開發與技術', ('架構', '程式', '測試', '開發', 'architecture', 'code', 'test_', 'workspace_manager', 'custom_agents', 'escalation', 'dashboard')),
    ('research', '研究與分析', ('研究', '調研', '分析', 'research', 'analysis', 'assessment', 'report')),
    ('guides', '專案與使用指南', ('readme', '指南', '手冊', '規格', '計畫', '計劃', 'guide', 'specification', 'roadmap')),
)


@lru_cache(maxsize=1024)
def _sample_cached(path: str, mtime_ns: int, size: int) -> str:
    del mtime_ns, size  # cache invalidation keys
    p = Path(path)
    try:
        if p.suffix.lower() in {'.md', '.txt', '.html', '.csv', '.rst'}:
            with p.open('rb') as stream:
                text = stream.read(65536).decode('utf-8', errors='replace')
            return re.sub(r'<[^>]+>', ' ', html.unescape(text))[:12000]
        if p.suffix.lower() in {'.docx', '.pptx'}:
            with zipfile.ZipFile(p) as archive:
                names = [n for n in archive.namelist() if n == 'word/document.xml' or re.fullmatch(r'ppt/slides/slide\d+\.xml', n)]
                parts = []
                budget = 262144
                for name in names[:8]:
                    info = archive.getinfo(name)
                    if info.file_size > budget:
                        continue
                    with archive.open(name) as stream:
                        raw = stream.read(min(budget, info.file_size) + 1)
                    budget -= len(raw)
                    parts.append(re.sub(r'<[^>]+>', ' ', raw.decode('utf-8', errors='replace')))
                return html.unescape(' '.join(parts))[:12000]
    except (OSError, ValueError, KeyError, zipfile.BadZipFile):
        return ''
    return ''


def document_sample(path: Path | None) -> str:
    """Only callers with an already authorized path may request a content sample."""
    if path is None:
        return ''
    try:
        stat = path.stat()
        return _sample_cached(str(path), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return ''


def classify_document(path: str, name: str = '', context: str = '', sample: str = '') -> dict[str, Any]:
    """Prefer explicit name/path matches; use bounded content only as fallback."""
    fields = [(name or Path(path).name, '檔名'), (path, '路徑'), (context, '來源任務'), (sample[:12000], '內容摘要')]
    for text, source in fields:
        lower = text.casefold()
        scored = [(sum(1 for word in words if word.casefold() in lower), tid, title, words) for tid, title, words in TOPICS]
        best = max(scored, key=lambda item: item[0])
        if best[0]:
            _, tid, title, words = best
            matched = [word for word in words if word.casefold() in lower]
            return {'id': tid, 'title': title, 'method': 'rules-v1', 'evidence': source + '：' + '、'.join(matched[:3])}
    return {'id': 'other', 'title': '其他文件', 'method': 'rules-v1', 'evidence': '尚無明確主題；保留原始檔案'}
