"""Read-only document index shared by the database API and UI."""
from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from core.document_topics import classify_document, document_sample


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (ValueError, TypeError):
        return 0.0


def build_document_library(
    history: list[dict[str, Any]], discovered: list[dict[str, Any]],
    system_files: list[dict[str, Any]], metadata: Callable[[dict[str, Any]], dict[str, Any]],
    resolve: Callable[[str], Path | None],
) -> dict[str, Any]:
    """Deduplicate by authorized absolute path; never infer a thread from a topic."""
    files: dict[str, dict[str, Any]] = {}
    threads: dict[str, dict[str, Any]] = {}

    def add(raw: dict[str, Any], project: str = '', thread: str = '', title: str = '', at: str = '', ts: float = 0, source: str = 'workspace') -> None:
        rel = str(raw.get('rel_path') or raw.get('path') or '').strip()
        if not rel:
            return
        full = str(raw.get('full_path') or raw.get('path') or rel)
        if not Path(full).is_absolute() and project:
            # Project-relative history must not resolve against whichever project is active now.
            from core.workspace_manager import get_workspace_root  # noqa: PLC0415
            full = str(get_workspace_root() / project / full)
        authorized = resolve(full)
        key = str(authorized) if authorized else f'{project}:{full}'
        if key in files:
            return
        meta = metadata({**raw, 'path': rel, 'full_path': str(authorized) if authorized else full})
        sample = document_sample(authorized)
        topic = classify_document(rel, meta['name'], title, sample)
        meta.update(project=project or str(raw.get('project') or ''), thread=thread,
                    thread_title=title, dispatched_at=at, ts=ts, source=source,
                    available=authorized is not None, topic_id=topic['id'], topic_title=topic['title'],
                    classification=topic['method'], classification_reason=topic['evidence'],
                    search_text=' '.join(sample.split())[:1600])
        files[key] = meta
        if thread:
            threads[thread]['files'].append(meta)
            threads[thread]['count'] += 1

    for item in sorted((i for i in history if isinstance(i, dict)), key=lambda i: _number(i.get('ts')), reverse=True):
        raw_files = item.get('output_files')
        if not isinstance(raw_files, list) or not raw_files:
            continue
        ts = _number(item.get('ts'))
        thread = str(item.get('thread') or f'history-{len(threads)}')
        title = ' '.join(str(item.get('prompt') or '未命名任務').split())
        threads.setdefault(thread, {'thread': thread, 'title': title, 'project': str(item.get('project') or ''), 'ts': ts, 'dispatched_at': str(item.get('dispatched_at') or ''), 'count': 0, 'files': []})
        for raw in raw_files:
            add(raw if isinstance(raw, dict) else {'path': str(raw)}, str(item.get('project') or ''), thread, title, str(item.get('dispatched_at') or ''), ts, 'history')
    for raw in discovered:
        add(raw, source='workspace')
    for raw in system_files:
        add(raw, source='system')

    topics: dict[str, dict[str, Any]] = {}
    ungrouped = []
    for meta in files.values():
        topic = topics.setdefault(meta['topic_id'], {'id': meta['topic_id'], 'title': meta['topic_title'], 'icon': '', 'files': [], 'threads': {}, 'count': 0})
        topic['files'].append(meta)
        topic['count'] += 1
        group = meta['thread'] or 'archive:' + (meta['project'] or 'system')
        title = meta['thread_title'] or ((meta['project'] + ' · ') if meta['project'] else '') + ('專案文件' if meta['source'] == 'system' else '工作區文件（未連結來源任務）')
        tg = topic['threads'].setdefault(group, {'thread': group, 'title': title, 'project': meta['project'], 'dispatched_at': meta['dispatched_at'], 'files': [], 'count': 0})
        tg['files'].append(meta)
        tg['count'] += 1
        if not meta['thread']:
            ungrouped.append(meta)
    topic_list = []
    for topic in topics.values():
        topic['threads'] = list(topic['threads'].values())
        topic_list.append(topic)
    topic_list.sort(key=lambda t: (t['id'] == 'other', -t['count'], t['title']))
    return {'topics': topic_list, 'threads': list(threads.values()), 'ungrouped': ungrouped,
            'total': len(files), 'available': sum(f['available'] for f in files.values()),
            'scanned_at': time.strftime('%Y-%m-%d %H:%M:%S %Z'),
            'classification': {'method': 'rules-v1', 'uses_paid_model': False, 'moves_files': False, 'content_formats': ['MD', 'TXT', 'HTML', 'CSV', 'RST', 'DOCX', 'PPTX']}}
