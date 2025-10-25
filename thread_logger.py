#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
线程日志记录器
用于区分不同线程的日志输出
"""

import logging
import threading
import sys
import os
from datetime import datetime

class ThreadFormatter(logging.Formatter):
    """自定义日志格式化器，添加线程标识"""
    
    # 颜色代码
    COLORS = {
        'SPOT': '\033[92m',  # 绿色
        'FUTURES': '\033[94m',  # 蓝色
        'MAIN': '\033[93m',  # 黄色
        'RESET': '\033[0m'  # 重置颜色
    }
    
    def __init__(self, use_color=True):
        super().__init__()
        self.use_color = use_color
    
    def format(self, record):
        # 获取线程名称
        thread_name = threading.current_thread().name
        
        # 根据线程名称确定标识和颜色
        if 'Spot' in thread_name:
            prefix = '[SPOT]'
            color = self.COLORS['SPOT'] if self.use_color else ''
        elif 'Futures' in thread_name:
            prefix = '[FUTURES]'
            color = self.COLORS['FUTURES'] if self.use_color else ''
        elif 'MainThread' in thread_name:
            prefix = '[MAIN]'
            color = self.COLORS['MAIN'] if self.use_color else ''
        else:
            prefix = f'[{thread_name}]'
            color = self.COLORS['RESET'] if self.use_color else ''
        
        # 添加时间戳
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # 格式化日志消息
        if self.use_color:
            formatted = f"{color}{prefix} {timestamp} {record.getMessage()}{self.COLORS['RESET']}"
        else:
            formatted = f"{prefix} {timestamp} {record.getMessage()}"
        
        return formatted

class FileFormatter(logging.Formatter):
    """文件日志格式化器，不包含颜色代码"""
    
    def format(self, record):
        # 获取线程名称
        thread_name = threading.current_thread().name
        
        # 根据线程名称确定标识
        if 'Spot' in thread_name:
            prefix = '[SPOT]'
        elif 'Futures' in thread_name:
            prefix = '[FUTURES]'
        elif 'MainThread' in thread_name:
            prefix = '[MAIN]'
        else:
            prefix = f'[{thread_name}]'
        
        # 添加时间戳
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # 格式化日志消息
        formatted = f"{prefix} {timestamp} {record.getMessage()}"
        
        return formatted

class ThreadLogger:
    """线程日志记录器类"""
    
    def __init__(self, name=None, log_file=None):
        self.logger = logging.getLogger(name or f"ThreadLogger_{id(self)}")
        self.logger.setLevel(logging.INFO)
        
        # 避免重复添加处理器
        if not self.logger.handlers:
            # 创建控制台处理器
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setFormatter(ThreadFormatter(use_color=True))
            self.logger.addHandler(console_handler)
            
            # 如果指定了日志文件，创建文件处理器
            if log_file:
                # 确保日志目录存在
                log_dir = os.path.dirname(log_file)
                if log_dir and not os.path.exists(log_dir):
                    os.makedirs(log_dir, exist_ok=True)
                
                file_handler = logging.FileHandler(log_file, encoding='utf-8')
                file_handler.setFormatter(FileFormatter())
                self.logger.addHandler(file_handler)
    
    def info(self, message):
        """记录信息级别日志"""
        self.logger.info(message)
    
    def warning(self, message):
        """记录警告级别日志"""
        self.logger.warning(message)
    
    def error(self, message):
        """记录错误级别日志"""
        self.logger.error(message)
    
    def debug(self, message):
        """记录调试级别日志"""
        self.logger.debug(message)

# 创建全局日志记录器实例
spot_logger = ThreadLogger("SpotTrading", "/app/logs/spot_trading.log")
futures_logger = ThreadLogger("FuturesTrading", "/app/logs/futures_trading.log")
main_logger = ThreadLogger("MainProgram", "/app/logs/main_program.log")

# 便捷函数
def log_spot(message):
    """记录现货交易日志"""
    spot_logger.info(message)

def log_futures(message):
    """记录合约交易日志"""
    futures_logger.info(message)

def log_main(message):
    """记录主程序日志"""
    main_logger.info(message)

def log_spot_warning(message):
    """记录现货交易警告日志"""
    spot_logger.warning(message)

def log_futures_warning(message):
    """记录合约交易警告日志"""
    futures_logger.warning(message)

def log_spot_error(message):
    """记录现货交易错误日志"""
    spot_logger.error(message)

def log_futures_error(message):
    """记录合约交易错误日志"""
    futures_logger.error(message)

# 兼容性函数，用于替换现有的print语句
def spot_print(*args, **kwargs):
    """替换现货交易中的print函数"""
    message = ' '.join(str(arg) for arg in args)
    log_spot(message)

def futures_print(*args, **kwargs):
    """替换合约交易中的print函数"""
    message = ' '.join(str(arg) for arg in args)
    log_futures(message)

def main_print(*args, **kwargs):
    """替换主程序中的print函数"""
    message = ' '.join(str(arg) for arg in args)
    log_main(message)