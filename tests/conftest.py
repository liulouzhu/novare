"""共享测试 fixtures"""

from pathlib import Path

import pytest


@pytest.fixture
def tmp_workspace(tmp_path):
    """创建临时工作空间"""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / ".novare").mkdir()
    return ws


@pytest.fixture
def tmp_data_dir(tmp_path):
    """创建临时数据目录"""
    d = tmp_path / "data"
    d.mkdir()
    return d
