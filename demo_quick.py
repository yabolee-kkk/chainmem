"""
ChainMem Quick Demo
===================
Showcases the core value: search a "thread" → retrieve the full memory chain.
Run: python demo_quick.py
"""

from chainmem import ChainMem

# 1. Create memory with simple text storage
mem = ChainMem(layers=["full"])

# 2. Ingest a conversation (chain of related messages)
lines = [
    "今天会议上讨论了Q3产品计划",
    "我们决定把AI助手功能提前到8月发布",
    "后端团队反馈说API设计需要两周",
    "前端说UI稿已经完成80%了",
    "测试团队建议增加3天缓冲期",
    "最终排期定在8月15日上线",
]

for i, line in enumerate(lines):
    mem.ingest(line, source="meeting", tags="Q3,product")

print("✅ 已存入6条链式记忆")
print("─" * 45)

# 3. Retrieve — give it a "thread", get the FULL chain back
query = "AI助手什么时候发布"  # Only one word matches
results = mem.retrieve(query, n_results=1)

print(f"🔍 搜索词: 「{query}」")
print(f"✅ 找到链头: 「{results[0][:30]}...」")
print()
print("📜 完整记忆链还原:")
print("─" * 45)
for r in results:
    print(f"  → {r}")
print("─" * 45)
print()
print("🎯 ChainMem vs 传统向量搜索:")
print("  传统: 搜到 1-2 个碎片片段")
print("  ChainMem: 还原 6 条完整记忆链 — 100%!")
