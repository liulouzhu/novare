"""论文 ID 规范化工具

统一各种 arXiv ID 格式为 canonical form: arxiv:<YYYY>.<NNNNN>

支持的输入格式:
  - 裸 ID: 2308.11681
  - 前缀 ID: arxiv:2308.11681
  - arXiv URL: https://arxiv.org/abs/2308.11681, https://arxiv.org/pdf/2308.11681
  - 带版本号: 2308.11681v2, arxiv:2308.11681v2
"""

from __future__ import annotations

import re

# arXiv ID pattern: YYYY.NNNNN (may have optional version suffix)
_ARXIV_ID_RE = re.compile(
    r"^(?:(?:https?://(?:arxiv\.org/(?:abs|pdf|html)/))?)"
    r"(?:(?:arxiv:)?)"
    r"(\d{4}\.\d{4,5}(?:v\d+)?)"
    r"$",
    re.IGNORECASE,
)


def canonicalize_paper_id(paper_id: str) -> str:
    """将各种格式的论文 ID 规范化为 canonical form。

    arXiv 论文 → arxiv:YYYY.NNNNN（去掉版本号）
    其他 ID → 原样返回（doi:..., s2:..., parsed 等）

    Examples:
        canonicalize_paper_id("2308.11681") → "arxiv:2308.11681"
        canonicalize_paper_id("arxiv:2308.11681") → "arxiv:2308.11681"
        canonicalize_paper_id("arxiv:2308.11681v2") → "arxiv:2308.11681"
        canonicalize_paper_id("https://arxiv.org/abs/2308.11681") → "arxiv:2308.11681"
        canonicalize_paper_id("https://arxiv.org/pdf/2308.11681v3") → "arxiv:2308.11681"
        canonicalize_paper_id("doi:10.1234/abc") → "doi:10.1234/abc"
        canonicalize_paper_id("s2:12345") → "s2:12345"
    """
    if not paper_id:
        return paper_id

    pid = paper_id.strip()
    m = _ARXIV_ID_RE.match(pid)
    if m:
        arxiv_id = m.group(1)
        # 去掉版本号
        base = re.sub(r"v\d+$", "", arxiv_id)
        return f"arxiv:{base}"

    # 非 arXiv ID，原样返回
    return pid


def is_arxiv_id(paper_id: str) -> bool:
    """判断是否为 arXiv 论文 ID（任何形式）"""
    if not paper_id:
        return False
    return _ARXIV_ID_RE.match(paper_id.strip()) is not None
