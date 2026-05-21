"""ChainMem 检索准确度基准测试"""
import tempfile, os
from chainmem import ChainMemory

test_texts = [
    ("记忆系统", "其实我的想法是把每一次的记忆包括一次对话全部变成一个链条这样只要想起开头几个字就能顺着把后面的内容推导出来。"),
    ("股决", "关于股决项目我觉得应该先做好最薄弱的一环然后让朋友内测反馈再扩从不用登录墙开始。"),
    ("博客", "AI风向标博客已经完成全栈升级用Express加SQLite做后端管理面板用React前端用Astro加TailwindCSS。"),
    ("股票", "每日股票分析系统支持A股港股美股自动生成分析报告并推送到企业微信飞书Telegram等渠道。"),
    ("部署", "部署铁律所有前端后端改动现在本地生产服务器上改好让用户确认用户说上了才能推云端。"),
]

queries = [
    ("其实我的想法", "记忆系统", "精确前缀"),
    ("我的想法", "记忆系统", "短语级模糊"),
    ("推导出来", "记忆系统", "尾部内容"),
    ("记忆", "记忆系统", "单关键词"),
    ("把每一次的记忆", "记忆系统", "中间短语"),
    ("关于股决", "股决", "精确前缀"),
    ("股决项目", "股决", "完整短语"),
    ("从不用登录墙开始", "股决", "尾部内容"),
    ("内测反馈", "股决", "内部短语"),
    ("AI风向标", "博客", "精确前缀"),
    ("博客", "博客", "单关键词"),
    ("Astro", "博客", "技术栈关键词"),
    ("A股", "股票", "关键词"),
    ("自动生成分析报告", "股票", "功能短语"),
    ("企业微信", "股票", "渠道名"),
    ("部署铁律", "部署", "精确前缀"),
    ("本地生产服务器", "部署", "内部短语"),
    ("推云端", "部署", "尾部短语"),
    ("1234567890", None, "完全不匹配"),
    ("xyz xyz", None, "噪音查询"),
]

db = tempfile.mktemp(suffix=".db")
cm = ChainMemory(db_path=db).open()

chain_map = {}
for topic, text in test_texts:
    chain = cm.ingest(text, source=topic, tags=[topic])
    chain_map[topic] = chain

cm.retriever.rebuild_index()

results = []
for query, expected_topic, scenario in queries:
    retrieved_texts = cm.retrieve(query)
    
    if not retrieved_texts:
        matched_topic = None
        match_ok = expected_topic is None
    else:
        reconstructed = "".join(retrieved_texts)
        matched_topic = None
        for topic, original_text in test_texts:
            if original_text in reconstructed or reconstructed in original_text:
                matched_topic = topic
                break
            overlap = len(set(reconstructed) & set(original_text)) / max(len(set(reconstructed)), 1)
            if overlap > 0.5 and len(reconstructed) > 5:
                matched_topic = topic
                break
        if matched_topic is None and retrieved_texts:
            for topic, original_text in test_texts:
                if any(t in original_text for t in retrieved_texts[:1]):
                    matched_topic = topic
                    break
        match_ok = matched_topic == expected_topic
    
    results.append((query, expected_topic, matched_topic, match_ok, scenario))

cm.close()
os.unlink(db)

total = len(results)
correct = sum(1 for r in results if r[3])
print(f"\n{'='*70}")
print(f"  ChainMem 检索准确度基准测试")
print(f"{'='*70}")
print(f"\n  总计: {total} 次查询, 正确: {correct}, 准确率: {correct/total*100:.1f}%\n")

for query, expected, actual, ok, scenario in results:
    status = "✅" if ok else "❌"
    exp = expected or "无"
    act = actual or "无"
    print(f"  {query:<24} 期望={exp:<6} 实际={act:<6} {status} ({scenario})")

# 分场景统计
print(f"\n{'='*70}")
print(f"  分场景准确率\n")
scenarios = {}
for q, exp, act, ok, sc in results:
    scenarios.setdefault(sc, {"total": 0, "correct": 0})
    scenarios[sc]["total"] += 1
    if ok:
        scenarios[sc]["correct"] += 1
for sc, data in sorted(scenarios.items()):
    rate = data["correct"] / data["total"] * 100
    bar = "█" * int(rate / 10) + "░" * (10 - int(rate / 10))
    print(f"  [{bar}] {sc:<16} {data['correct']}/{data['total']} ({rate:.0f}%)")
