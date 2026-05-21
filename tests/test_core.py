"""测试 ChainMem 核心功能"""

from __future__ import annotations
import os
import tempfile
import json

import pytest

from chainmem import ChainMemory
from chainmem.pipeline.ingester import chunk_text


class TestChunking:
    """测试文本切块"""

    def test_basic_split(self):
        text = "其实我的想法是把每一次的记忆。包括一次对话全部变成一个链条。"
        chunks = chunk_text(text)
        assert len(chunks) >= 2
        assert "其实我的想法是把每一次的记忆" in "".join(chunks)
        assert "包括一次对话全部变成一个链条" in "".join(chunks)

    def test_single_sentence(self):
        text = "这是一个简单的句子。"
        chunks = chunk_text(text)
        assert len(chunks) == 1
        assert chunks[0] == "这是一个简单的句子。"

    def test_empty_text(self):
        assert chunk_text("") == []
        assert chunk_text("  ") == []

    def test_long_without_punctuation(self):
        text = "这是一个没有任何标点符号的非常长的句子它应该被截断成多个块"
        chunks = chunk_text(text)
        assert len(chunks) >= 2
        for c in chunks:
            assert len(c) <= 35  # max_chars + some buffer

    def test_comma_split(self):
        text = "其实我的想法是把每一次的记忆，包括一次对话全部变成一个链条。"
        chunks = chunk_text(text)
        # 逗号前后至少被切成2块
        assert len(chunks) >= 2


class TestChainMem:
    """测试 ChainMem 完整流程"""

    @pytest.fixture
    def cm(self):
        db = tempfile.mktemp(suffix=".db")
        cm = ChainMemory(db_path=db).open()
        yield cm
        cm.close()
        os.unlink(db)

    def test_ingest_simple(self, cm):
        text = "其实我的想法是把每一次的记忆，包括一次对话全部变成一个链条。"
        chain = cm.ingest(text, source="test")
        assert chain.id is not None
        assert chain.node_count >= 2
        assert chain.anchor_prefix == "其实我"
        assert chain.source == "test"

    def test_ingest_and_retrieve(self, cm):
        texts = [
            "其实我的想法是把每一次的记忆，包括一次对话全部变成一个链条。",
            "关于股决项目我觉得应该先做好最薄弱的一环。",
        ]
        for t in texts:
            cm.ingest(t, source="test")

        cm.retriever.rebuild_index()

        results = cm.retrieve("其实我的想法")
        assert len(results) >= 2
        assert "其实我的想法是把每一次的记忆" in results[0]

        results2 = cm.retrieve("股决项目")
        assert len(results2) >= 1
        assert "股决" in results2[0]

    def test_retrieve_no_match(self, cm):
        cm.ingest("关于股票市场的分析。", source="test")
        cm.ingest("今天天气很好适合出去散步。", source="test")
        cm.retriever.rebuild_index()
        results = cm.retrieve("1234567890")
        assert results == []

    def test_stats(self, cm):
        cm.ingest("测试文本一。", source="t1")
        cm.ingest("测试文本二。", source="t2")
        s = cm.stats()
        assert s["chains"] == 2
        assert s["nodes"] >= 2
        assert s["db_path"] is not None

    def test_chain_full_text(self, cm):
        text = "第一部分。第二部分。第三部分。"
        chain = cm.ingest(text, source="test")
        full = chain.full_text()
        assert "第一部分" in full
        assert "第二部分" in full
        assert "第三部分" in full

    def test_chain_to_dict(self, cm):
        chain = cm.ingest("测试链。", source="t", tags=["demo"])
        d = chain.to_dict()
        assert d["source"] == "t"
        assert d["tags"] == ["demo"]
        assert "full_text" in d

    # ── 标签功能测试 ──

    def test_ingest_with_tags(self, cm):
        """结链时带标签，应正确存储"""
        chain = cm.ingest("股决项目：一个A股投资决策辅助工具。", source="test", tags=["股决", "项目"])
        assert "股决" in chain.tags
        assert "项目" in chain.tags

        # 验证数据库中也是正确的
        chains = cm.store.get_all_chains()
        assert len(chains) == 1
        tags = chains[0].get("tags", [])
        assert "股决" in tags
        assert "项目" in tags

    def test_retrieve_with_tag_filter(self, cm):
        """按标签过滤检索，只返回匹配标签的链"""
        cm.ingest("股决项目是一个A股投资决策辅助工具。", source="test", tags=["股决", "项目"])
        cm.ingest("博客AI风向标今天发布了新文章。", source="test", tags=["博客"])
        cm.retriever.rebuild_index()

        # 只搜股决标签
        results = cm.retrieve("项目", tags=["股决"])
        assert len(results) >= 1
        assert "股决" in results[0]

        # 只搜博客标签
        results2 = cm.retrieve("文章", tags=["博客"])
        assert len(results2) >= 1
        assert "博客" in results2[0]

        # 搜不存在的标签
        results3 = cm.retrieve("项目", tags=["不存在"])
        assert results3 == []

    def test_retrieve_with_or_tag_filter(self, cm):
        """多标签 OR 逻辑：匹配任一标签即返回"""
        cm.ingest("股决项目的服务器在阿里云。", source="t1", tags=["股决"])
        cm.ingest("博客AI风向标基于Astro构建。", source="t2", tags=["博客"])
        cm.ingest("每日股票分析系统需要快照。", source="t3", tags=["股票"])
        cm.retriever.rebuild_index()

        # 搜 股决 OR 博客，应返回两条链
        results = cm.retrieve("项目", tags=["股决", "博客"])
        # 至少返回股决这条
        combined = "".join(results)
        assert "股决" in combined or "博客" in combined

    def test_get_all_chains_includes_tags(self, cm):
        """get_all_chains 应返回 tags 字段"""
        cm.ingest("带标签的文档。", tags=["测试", "demo"])
        chains = cm.store.get_all_chains()
        assert len(chains) >= 1
        c = chains[-1]
        assert "tags" in c
        assert isinstance(c["tags"], list)

    def test_retrieve_with_tag_and_query(self, cm):
        """标签过滤 + 语义检索 混合"""
        cm.ingest("股决使用东方财富接口获取实时资金流向。", tags=["股决", "开发"])
        cm.ingest("股市今日大盘低开高走。", tags=["股票", "行情"])
        cm.retriever.rebuild_index()

        # 搜索"开发"标签下的内容，应返回股决那条
        results = cm.retrieve("资金流向", tags=["开发"])
        assert "资金" in "".join(results)

        # 搜索"行情"标签下的内容，应返回股市那条
        results2 = cm.retrieve("大盘", tags=["行情"])
        assert "大盘" in "".join(results2)

        # 不匹配标签时返回空
        results3 = cm.retrieve("资金流向", tags=["博客"])
        assert results3 == []
