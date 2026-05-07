#!/usr/bin/env python3
"""
测试 LangGraph 重构版本
"""

import sys
import uuid

print("=" * 60)
print("测试 LangGraph 重构版本")
print("=" * 60)

try:
    print("\n1. 导入模块...")
    from multi_agent_customer_service import (
        process_customer_query,
        get_available_tools,
        get_workstation_status,
        get_ticket_status
    )
    print("✅ 模块导入成功")
except Exception as e:
    print(f"❌ 模块导入失败: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

session_id = str(uuid.uuid4())
print(f"\n会话ID: {session_id}")

print("\n2. 测试工具列表...")
try:
    tools = get_available_tools()
    print(f"✅ 获取到 {len(tools)} 个工具")
    for tool in tools[:3]:
        print(f"   - {tool['name']}: {tool['description']}")
except Exception as e:
    print(f"❌ 获取工具失败: {e}")

test_queries = [
    "北京到上海的机票",
    "今天天气怎么样",
    "这个航班会晚点吗",
    "我的账单有问题",
    "服务太差了，我要投诉",
]

print("\n3. 测试客户查询...")
for i, query in enumerate(test_queries, 1):
    print(f"\n{'='*60}")
    print(f"测试 {i}: {query}")
    print(f"{'='*60}")
    
    try:
        result = process_customer_query(query, session_id)
        print(f"\n✅ 查询成功")
        print(f"   意图: {result.get('intent', 'N/A')}")
        print(f"   Agent: {result.get('current_agent', 'N/A')}")
        print(f"   情绪标签: {result.get('mood_tag', 'N/A')}")
        
        if result.get('tool_results'):
            print(f"   工具结果: {list(result.get('tool_results').keys())}")
        
        if result.get('ticket_id'):
            print(f"   工单ID: {result.get('ticket_id')}")
        
        print(f"\n   响应:")
        print(result.get('response', ''))
        
    except Exception as e:
        print(f"❌ 查询失败: {e}")
        import traceback
        traceback.print_exc()

print("\n4. 测试工作台状态...")
try:
    status = get_workstation_status()
    print(f"✅ 工作台状态:")
    print(f"   总客服: {status['total_agents']}")
    print(f"   在线: {status['online_agents']}")
    print(f"   待处理L2任务: {status['pending_l2_tasks']}")
except Exception as e:
    print(f"❌ 获取工作台状态失败: {e}")

print("\n5. 测试工单状态...")
try:
    ticket_summary = get_ticket_status()
    print(f"✅ 工单状态:")
    print(f"   总工单: {ticket_summary['total']}")
    print(f"   待处理: {ticket_summary['pending']}")
except Exception as e:
    print(f"❌ 获取工单状态失败: {e}")

print("\n" + "=" * 60)
print("测试完成！")
print("=" * 60)
