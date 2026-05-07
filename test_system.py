"""
测试脚本，验证系统修改是否正确
"""

import os
import sys

# 添加当前目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 测试智能体导入
try:
    from agents import ProductAgent, BillingAgent, ComplaintAgent, GeneralAgent
    print("✅ 智能体导入成功")
    
    # 测试ProductAgent是否正确修改为机票专家
    product_agent = ProductAgent()
    print(f"✅ 产品智能体名称: {product_agent.name}")
    print(f"✅ 产品智能体角色: {product_agent.role}")
    print(f"✅ 产品智能体专业领域: {', '.join(product_agent.expertise)}")
    
    # 测试是否能获取机票数据库
    if hasattr(product_agent, 'flight_database'):
        print("✅ 机票数据库存在")
        print(f"✅ 航班类型: {', '.join(product_agent.flight_database.keys())}")
    else:
        print("❌ 机票数据库不存在")
        
except ImportError as e:
    print(f"❌ 智能体导入失败: {e}")

# 测试查询分类工具
try:
    from tools import classify_query
    print("✅ 查询分类工具导入成功")
except ImportError as e:
    print(f"❌ 查询分类工具导入失败: {e}")

# 测试会话管理器
try:
    from memory import LangChainSessionManager
    print("✅ 会话管理器导入成功")
except ImportError as e:
    print(f"❌ 会话管理器导入失败: {e}")

print("\n测试完成！")
