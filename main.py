#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
统一的交易机器人主程序
支持现货和合约交易
"""

import os
import sys
import time
import threading
from dotenv import load_dotenv
from thread_logger import log_main, log_spot, log_futures, spot_print, futures_print, main_print

# 添加当前目录到Python路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# 加载环境变量
load_dotenv()

# 检查必要的环境变量
required_vars = ['DEEPSEEK_API_KEY']
binance_spot_vars = ['BINANCE_SPOT_API_KEY', 'BINANCE_SPOT_SECRET']
binance_futures_vars = ['BINANCE_FUTURES_API_KEY', 'BINANCE_FUTURES_SECRET']

missing_vars = []
for var in required_vars:
    if not os.getenv(var):
        missing_vars.append(var)

# 检查是否启用了现货交易但缺少现货API密钥
enable_spot = os.getenv('ENABLE_SPOT_TRADING', 'true').lower() == 'true'
if enable_spot:
    for var in binance_spot_vars:
        if not os.getenv(var):
            missing_vars.append(f"{var} (现货交易所需)")

# 检查是否启用了合约交易但缺少合约API密钥
enable_futures = os.getenv('ENABLE_FUTURES_TRADING', 'true').lower() == 'true'
if enable_futures:
    for var in binance_futures_vars:
        if not os.getenv(var):
            missing_vars.append(f"{var} (合约交易所需)")

if missing_vars:
    main_print("错误: 缺少以下必要的环境变量:")
    for var in missing_vars:
        main_print(f"  - {var}")
    main_print("\n请检查 .env 文件配置")
    sys.exit(1)

def run_spot_trading():
    """运行现货交易机器人"""
    try:
        from spot_trading import spot_main
        spot_print("启动现货交易机器人...")
        spot_main()
    except Exception as e:
        spot_print(f"现货交易机器人启动失败: {e}")
        import traceback
        traceback.print_exc()

def run_futures_trading():
    """运行合约交易机器人"""
    try:
        from futures_trading import futures_main
        futures_print("启动合约交易机器人...")
        futures_main()
    except Exception as e:
        futures_print(f"合约交易机器人启动失败: {e}")
        import traceback
        traceback.print_exc()


def main():
    """主函数 - 统一管理现货和合约交易"""
    main_print("=" * 60)
    main_print("统一交易机器人启动")
    main_print("=" * 60)
    
    # 获取环境变量配置
    enable_spot = os.getenv('ENABLE_SPOT_TRADING', 'true').lower() == 'true'
    enable_futures = os.getenv('ENABLE_FUTURES_TRADING', 'true').lower() == 'true'
    
    main_print(f"现货交易启用: {enable_spot}")
    main_print(f"合约交易启用: {enable_futures}")
    
    threads = []
    
    # 启动现货交易线程
    if enable_spot:
        spot_thread = threading.Thread(target=run_spot_trading, name="SpotTradingThread")
        spot_thread.daemon = True
        spot_thread.start()
        threads.append(spot_thread)
        spot_print("现货交易线程已启动")
    
    # 启动合约交易线程
    if enable_futures:
        futures_thread = threading.Thread(target=run_futures_trading, name="FuturesTradingThread")
        futures_thread.daemon = True
        futures_thread.start()
        threads.append(futures_thread)
        futures_print("合约交易线程已启动")
    
    if not threads:
        main_print("未启用任何交易模式，请检查环境变量配置")
        return
    
    main_print(f"总共启动了 {len(threads)} 个交易线程")
    main_print("按 Ctrl+C 停止所有交易机器人")
    
    try:
        # 保持主线程运行
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        main_print("\n接收到停止信号，正在关闭所有交易机器人...")
        # 等待所有线程结束
        for thread in threads:
            if thread.is_alive():
                thread.join(timeout=5)  # 等待最多5秒
        main_print("所有交易机器人已关闭")
    except Exception as e:
        main_print(f"主程序运行异常: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()