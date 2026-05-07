"""
会话监控服务
定期检查会话活动状态，自动总结长时间无活动的会话
"""

import time
import threading
from memory import check_session_summary, summarize_session

class SessionMonitor:
    """
    会话监控器
    定期检查会话活动状态，自动总结长时间无活动的会话
    """

    def __init__(self, check_interval: int = 60):
        """
        初始化会话监控器
        
        Args:
            check_interval: 检查间隔（秒），默认60秒
        """
        self.check_interval = check_interval
        self.running = False
        self.thread = None

    def start(self):
        """启动会话监控器"""
        if not self.running:
            self.running = True
            self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
            self.thread.start()
            print(f"📡 会话监控器已启动，检查间隔: {self.check_interval}秒")

    def stop(self):
        """
        停止会话监控器
        """
        if self.running:
            self.running = False
            if self.thread:
                self.thread.join(timeout=5)
            print("📡 会话监控器已停止")

    def _monitor_loop(self):
        """
        监控循环
        """
        while self.running:
            try:
                # 检查需要总结的会话
                sessions_to_summary = check_session_summary()
                
                if sessions_to_summary:
                    print(f"📝 发现 {len(sessions_to_summary)} 个需要总结的会话")
                    
                    # 总结每个会话
                    for session_id in sessions_to_summary:
                        success = summarize_session(session_id)
                        if success:
                            print(f"✅ 成功总结会话: {session_id}")
                        else:
                            print(f"❌ 总结会话失败: {session_id}")
                
                # 等待下一次检查
                time.sleep(self.check_interval)
                
            except Exception as e:
                print(f"监控会话时出错: {e}")
                # 出错后继续执行
                time.sleep(self.check_interval)


# 创建全局会话监控器实例
global_session_monitor = SessionMonitor()


def start_session_monitor():
    """
    启动会话监控器的便捷函数
    """
    global_session_monitor.start()


def stop_session_monitor():
    """
    停止会话监控器的便捷函数
    """
    global_session_monitor.stop()


if __name__ == "__main__":
    # 测试会话监控器
    print("测试会话监控器...")
    start_session_monitor()
    
    try:
        # 运行一段时间后停止
        time.sleep(3600)  # 运行1小时
    finally:
        stop_session_monitor()
