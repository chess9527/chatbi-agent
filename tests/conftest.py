"""pytest 公共夹具：把测试切到临时数据库，避免污染真实数据。"""
import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture(scope="session", autouse=True)
def test_db(tmp_path_factory):
    """整个测试会话使用一个临时生成的电商库。"""
    db_path = tmp_path_factory.mktemp("data") / "test.db"
    os.environ["DB_PATH"] = str(db_path)
    os.environ["TRACE_DIR"] = str(tmp_path_factory.mktemp("traces"))
    # 假 Key：测试全程离线，但 OpenAI 客户端构造要求非空 api_key
    os.environ.setdefault("LLM_API_KEY", "test-key-offline")

    from chatbi.config import get_settings
    get_settings.cache_clear()

    from scripts.init_db import init_db
    init_db()
    yield db_path
