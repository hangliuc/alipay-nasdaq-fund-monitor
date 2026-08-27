"""直销适配器共享的解析与记录工具。"""

from io import BytesIO
import re
import subprocess
import tempfile
from typing import Optional

from pypdf import PdfReader
import requests

from fund_monitor.fetch.sources.base import format_limit_yuan
from .registry import adapter_for_fund


def _record(limit: str, source: str, url: str, note: str, status: str = "ok") -> dict:
    return {
        "direct_sales_limit": limit,
        "direct_sales_status": status,
        "direct_sales_source": source,
        "direct_sales_url": url,
        "direct_sales_note": note,
    }


def _snapshot_record(snapshot) -> dict:
    """把独立 Adapter 的快照原样投影到统一输出结构。

    即使快照是 ``official_api_no_data`` 或 ``official_interface_requires_auth``，
    也保留该状态和官方来源；不会把失败转换成历史/统一兼容层的成功值。
    """
    return _record(
        snapshot.limit or "未获取",
        snapshot.channel,
        snapshot.source_url,
        snapshot.quota_remark or "独立 Adapter 未返回当前可验证直销字段",
        status=snapshot.status,
    )


def _fetch_adapter_limits(funds: list[dict], adapter) -> dict[str, dict]:
    """Run one independent Adapter over only its configured manager's funds.

    The old product-page and announcement entry points each carried a copy of
    this loop.  Keeping it here makes the manager filter, exception boundary,
    and snapshot projection identical without introducing a compatibility
    fallback or changing an Adapter's source semantics.
    """
    records = {}
    for fund in funds:
        manager = adapter_for_fund(fund)
        if not manager or manager.manager != adapter.manager_id:
            continue
        try:
            snapshot = adapter.fetch_direct_limit(adapter.identity_from_config(fund))
        except (
            requests.RequestException,
            PermissionError,
            RuntimeError,
            ValueError,
            TypeError,
            KeyError,
            OSError,
        ):
            continue
        records[fund["code"]] = _snapshot_record(snapshot)
    return records


def _extract_office_text(content: bytes, source_url: str) -> str:
    """将官网附件转换为文本；支持 PDF、DOCX 与老式 DOC。"""
    lower = source_url.lower()
    if lower.endswith(".pdf") or content.startswith(b"%PDF"):
        return "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(content)).pages)
    if lower.endswith(".docx") or content.startswith(b"PK"):
        # docx 是 zip+xml；尽量使用可选 python-docx，缺失时退回 XML 文本。
        try:
            from docx import Document  # type: ignore
            with tempfile.NamedTemporaryFile(suffix=".docx") as tmp:
                tmp.write(content)
                tmp.flush()
                return "\n".join(p.text for p in Document(tmp.name).paragraphs)
        except (ImportError, OSError, ValueError):
            return content.decode("utf-8", errors="ignore")
    # 建信目前提供 application/msword 的 OLE .doc。textutil 是 macOS 的
    # 系统转换器；失败时返回二进制中的可读片段，至少保留公告状态而不伪造金额。
    try:
        with tempfile.NamedTemporaryFile(suffix=".doc") as tmp:
            tmp.write(content)
            tmp.flush()
            completed = subprocess.run(
                ["textutil", "-convert", "txt", "-stdout", tmp.name],
                check=True,
                capture_output=True,
                timeout=20,
            )
            return completed.stdout.decode("utf-8", errors="ignore")
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return content.decode("utf-16le", errors="ignore")


def _amount(value) -> Optional[str]:
    match = re.search(r"([\d,.]+)\s*(亿元|亿|万元|万|元)", str(value or ""))
    if not match:
        return None
    amount = float(match.group(1).replace(",", ""))
    if match.group(2) in ("万", "万元"):
        amount *= 10000
    elif match.group(2) in ("亿", "亿元"):
        amount *= 100000000
    return format_limit_yuan(amount)
