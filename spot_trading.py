#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
币安现货交易机器人
支持多币种现货交易
"""

import os
import time
import schedule
from openai import OpenAI
import ccxt
import pandas as pd
from datetime import datetime, timedelta
import json
import re
import sqlite3
import threading
from dotenv import load_dotenv
from thread_logger import log_spot, log_spot_warning, log_spot_error, spot_print

load_dotenv()

# 初始化DeepSeek客户端
deepseek_client = OpenAI(
    api_key=os.getenv('DEEPSEEK_API_KEY'),
    base_url="https://api.deepseek.com"
)

# 初始化现货交易所连接
spot_exchange = ccxt.binance({
    'options': {
        'defaultType': 'spot'},  # 现货交易
    'apiKey': os.getenv('BINANCE_SPOT_API_KEY'),
    'secret': os.getenv('BINANCE_SPOT_SECRET'),
})

# 交易参数配置 - 支持多个交易对
SPOT_GLOBAL_CONFIG = {
    'timeframe': "1h",  # 使用1小时K线
    'test_mode': os.getenv('TEST_MODE'),  # 测试模式
    'data_points': 168,  # 7天数据（168根1h K线）
    'analysis_periods': {
        'short_term': 12,   # 12根1小时K线，即12小时短期均线
        'medium_term': 72,   # 72根1小时K线，即72小时(3天)中期均线
        'long_term': 168,    # 168根1小时K线，即168小时(7天)长期趋势
    }
}

# 各交易对的独立配置
SPOT_TRADE_CONFIGS = {
    'BTC/USDT': {
        'symbol': 'BTC/USDT',  # 币安的现货符号格式
        'base_currency': 'BTC',  # 基础货币
        'enabled': True  # 是否启用此交易对
    },
    'ETH/USDT': {
        'symbol': 'ETH/USDT',  # 币安的现货符号格式
        'base_currency': 'ETH',  # 基础货币
        'enabled': True  # 是否启用此交易对
    },
    'SOL/USDT': {
        'symbol': 'SOL/USDT',  # 币安的现货符号格式
        'base_currency': 'SOL',  # 基础货币
        'enabled': True  # 是否启用此交易对
    },
    'BNB/USDT': {
        'symbol': 'BNB/USDT',  # 币安的现货符号格式
        'base_currency': 'BNB',  # 基础货币
        'enabled': True  # 是否启用此交易对
    }
}

spot_signal_history = {}  # 改为字典，按交易对分别记录信号历史

# 获取启用的交易对列表
spot_enabled_symbols = [symbol for symbol, config in SPOT_TRADE_CONFIGS.items() if config['enabled']]

# 数据库相关变量
spot_db_connection = None
spot_db_lock = threading.Lock()

# 交易统计
spot_trading_stats = {
    'start_time': None,
    'total_calls': 0,
    'total_trades': 0,
    'successful_trades': 0,
    'total_pnl': 0.0,
    'last_signal_time': None
}


def init_spot_database():
    """初始化SQLite数据库"""
    global spot_db_connection
    try:
        with spot_db_lock:
            spot_db_connection = sqlite3.connect('/app/data/spot_trading_bot.db', check_same_thread=False)
            cursor = spot_db_connection.cursor()
            
            # 检查并添加symbol字段到现有表（如果不存在）
            try:
                # 检查price_data表是否有symbol字段
                cursor.execute("PRAGMA table_info(price_data)")
                columns = [column[1] for column in cursor.fetchall()]
                if 'symbol' not in columns:
                    cursor.execute("ALTER TABLE price_data ADD COLUMN symbol TEXT")
                    spot_print("为price_data表添加symbol字段")
            except:
                pass
            
            try:
                # 检查trading_signals表是否有symbol字段
                cursor.execute("PRAGMA table_info(trading_signals)")
                columns = [column[1] for column in cursor.fetchall()]
                if 'symbol' not in columns:
                    cursor.execute("ALTER TABLE trading_signals ADD COLUMN symbol TEXT")
                    spot_print("为trading_signals表添加symbol字段")
            except:
                pass
            
            try:
                # 检查position_records表是否有symbol字段
                cursor.execute("PRAGMA table_info(position_records)")
                columns = [column[1] for column in cursor.fetchall()]
                if 'symbol' not in columns:
                    cursor.execute("ALTER TABLE position_records ADD COLUMN symbol TEXT")
                    spot_print("为position_records表添加symbol字段")
            except:
                pass
            
            try:
                # 检查technical_indicators表是否有symbol字段
                cursor.execute("PRAGMA table_info(technical_indicators)")
                columns = [column[1] for column in cursor.fetchall()]
                if 'symbol' not in columns:
                    cursor.execute("ALTER TABLE technical_indicators ADD COLUMN symbol TEXT")
                    spot_print("为technical_indicators表添加symbol字段")
            except:
                pass
            
            # 创建价格数据表（如果不存在）
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS price_data (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    price REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    volume REAL NOT NULL,
                    timeframe TEXT NOT NULL,
                    price_change REAL,
                    sma_5 REAL,
                    sma_20 REAL,
                    sma_50 REAL,
                    rsi REAL,
                    macd REAL,
                    macd_signal REAL,
                    macd_histogram REAL,
                    bb_upper REAL,
                    bb_lower REAL,
                    bb_position REAL,
                    volume_ratio REAL
                )
            ''')
            
            # 创建交易信号表（如果不存在）
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS trading_signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    signal TEXT NOT NULL,
                    reason TEXT,
                    stop_loss REAL,
                    take_profit REAL,
                    confidence TEXT,
                    price REAL,
                    is_fallback INTEGER DEFAULT 0
                )
            ''')
            
            # 创建持仓记录表（如果不存在）
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS position_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    size REAL,
                    entry_price REAL,
                    position_amt REAL,
                    stop_loss REAL,
                    confidence REAL
                )
            ''')
            
            # 创建交易统计表（如果不存在）
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS trading_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    start_time TEXT,
                    total_calls INTEGER DEFAULT 0,
                    total_trades INTEGER DEFAULT 0,
                    successful_trades INTEGER DEFAULT 0,
                    total_pnl REAL DEFAULT 0.0,
                    last_signal_time TEXT
                )
            ''')
            
            # 创建技术指标历史表（如果不存在）
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS technical_indicators (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    mid_prices TEXT,
                    ema_indicators TEXT,
                    macd_indicators TEXT,
                    rsi_7_indicators TEXT,
                    rsi_14_indicators TEXT,
                    longer_term_ema_20 REAL,
                    longer_term_ema_50 REAL,
                    atr_3 REAL,
                    atr_14 REAL,
                    current_volume REAL,
                    avg_volume REAL,
                    sharpe_ratio REAL
                )
            ''')
            
            # 检查是否已有统计数据
            cursor.execute('SELECT COUNT(*) FROM trading_stats')
            if cursor.fetchone()[0] == 0:
                # 初始化统计数据
                cursor.execute('''
                    INSERT INTO trading_stats (start_time, total_calls, total_trades, successful_trades, total_pnl)
                    VALUES (?, 0, 0, 0, 0.0)
                ''', (datetime.now().strftime('%Y-%m-%d %H:%M:%S'),))
            
            spot_db_connection.commit()
            spot_print("现货数据库初始化成功")
            return True
            
    except Exception as e:
        log_spot_error(f"现货数据库初始化失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def save_spot_price_data(price_data, symbol):
    """保存价格数据到数据库"""
    global spot_db_connection
    try:
        with spot_db_lock:
            cursor = spot_db_connection.cursor()
            tech = price_data.get('technical_data', {})
            
            cursor.execute('''
                INSERT INTO price_data
                (symbol, timestamp, price, high, low, volume, timeframe, price_change,
                 sma_5, sma_20, sma_50, rsi, macd, macd_signal, macd_histogram,
                 bb_upper, bb_lower, bb_position, volume_ratio)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                symbol,
                price_data['timestamp'],
                price_data['price'],
                price_data['high'],
                price_data['low'],
                price_data['volume'],
                price_data['timeframe'],
                price_data['price_change'],
                tech.get('sma_5', 0),
                tech.get('sma_20', 0),
                tech.get('sma_50', 0),
                tech.get('rsi', 0),
                tech.get('macd', 0),
                tech.get('macd_signal', 0),
                tech.get('macd_histogram', 0),
                tech.get('bb_upper', 0),
                tech.get('bb_lower', 0),
                tech.get('bb_position', 0),
                tech.get('volume_ratio', 0)
            ))
            
            spot_db_connection.commit()
            
    except Exception as e:
        log_spot_warning(f"保存价格数据失败: {e}")


def save_spot_trading_signal(signal_data, price_data, symbol):
    """保存交易信号到数据库"""
    global spot_db_connection
    try:
        with spot_db_lock:
            cursor = spot_db_connection.cursor()
            
            cursor.execute('''
                INSERT INTO trading_signals
                (symbol, timestamp, signal, reason, stop_loss, take_profit, confidence, price, is_fallback)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                symbol,
                signal_data.get('timestamp', datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
                signal_data['signal'],
                signal_data['reason'],
                signal_data.get('stop_loss'),
                signal_data.get('take_profit'),
                signal_data.get('confidence'),
                price_data['price'],
                1 if signal_data.get('is_fallback', False) else 0
            ))
            
            spot_db_connection.commit()
            
    except Exception as e:
        log_spot_warning(f"保存交易信号失败: {e}")


def save_spot_position_record(position_data, symbol):
    """保存持仓记录到数据库"""
    global spot_db_connection
    try:
        with spot_db_lock:
            cursor = spot_db_connection.cursor()
            
            if position_data:
                cursor.execute('''
                    INSERT INTO position_records
                    (symbol, timestamp, size, entry_price, position_amt)
                    VALUES (?, ?, ?, ?, ?)
                ''', (
                    symbol,
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    position_data.get('position_percentage', 0),  # 使用仓位百分比作为size
                    position_data.get('entry_price', 0),
                    position_data.get('position_amt', 0)
                ))
            else:
                cursor.execute('''
                    INSERT INTO position_records
                    (symbol, timestamp, size, entry_price, position_amt)
                    VALUES (?, ?, ?, ?, ?)
                ''', (
                    symbol,
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    0, 0, 0
                ))
            
            spot_db_connection.commit()
            
    except Exception as e:
        log_spot_warning(f"保存持仓记录失败: {e}")


def update_spot_trading_stats():
    """更新交易统计"""
    global spot_db_connection, spot_trading_stats
    try:
        with spot_db_lock:
            cursor = spot_db_connection.cursor()
            
            cursor.execute('''
                UPDATE trading_stats
                SET total_calls = ?, total_trades = ?, successful_trades = ?,
                    total_pnl = ?, last_signal_time = ?
                WHERE id = 1
            ''', (
                spot_trading_stats['total_calls'],
                spot_trading_stats['total_trades'],
                spot_trading_stats['successful_trades'],
                spot_trading_stats['total_pnl'],
                spot_trading_stats['last_signal_time']
            ))
            
            spot_db_connection.commit()
            
    except Exception as e:
        log_spot_warning(f"更新交易统计失败: {e}")


def load_spot_trading_stats():
    """加载交易统计"""
    global spot_db_connection, spot_trading_stats
    try:
        with spot_db_lock:
            cursor = spot_db_connection.cursor()
            cursor.execute('SELECT * FROM trading_stats WHERE id = 1')
            result = cursor.fetchone()
            
            if result:
                spot_trading_stats['start_time'] = result[1]
                spot_trading_stats['total_calls'] = result[2]
                spot_trading_stats['total_trades'] = result[3]
                spot_trading_stats['successful_trades'] = result[4]
                spot_trading_stats['total_pnl'] = result[5]
                spot_trading_stats['last_signal_time'] = result[6]
                spot_print(f"已加载历史统计数据: 启动时间 {result[1]}, 总调用次数 {result[2]}")
            else:
                spot_print("未找到历史统计数据，使用默认值")
            
    except Exception as e:
        print(f"加载交易统计失败: {e}")


def get_spot_historical_price_data(symbol, limit=100):
    """获取历史价格数据"""
    global spot_db_connection
    try:
        with spot_db_lock:
            cursor = spot_db_connection.cursor()
            cursor.execute('''
                SELECT * FROM price_data
                WHERE symbol = ?
                ORDER BY timestamp DESC
                LIMIT ?
            ''', (symbol, limit))
            
            results = cursor.fetchall()
            return results
            
    except Exception as e:
        log_spot_warning(f"获取历史价格数据失败: {e}")
        return []


def get_spot_historical_signals(symbol, limit=30):
    """获取历史交易信号"""
    global spot_db_connection
    try:
        with spot_db_lock:
            cursor = spot_db_connection.cursor()
            cursor.execute('''
                SELECT timestamp, signal, reason, stop_loss, take_profit, confidence, price, is_fallback
                FROM trading_signals
                WHERE symbol = ?
                ORDER BY timestamp DESC
                LIMIT ?
            ''', (symbol, limit))
            
            results = cursor.fetchall()
            return results
            
    except Exception as e:
        log_spot_warning(f"获取历史交易信号失败: {e}")
        return []


def calculate_spot_technical_indicators(df):
    """计算技术指标 - 适用于1小时K线分析"""
    try:
        periods = SPOT_GLOBAL_CONFIG['analysis_periods']
        
        # 移动平均线 - 使用优化后的周期
        df['sma_5'] = df['close'].rolling(window=5, min_periods=1).mean()
        df['sma_12'] = df['close'].rolling(window=periods['short_term'], min_periods=1).mean()  # 12根1小时K线，即12小时
        df['sma_60'] = df['close'].rolling(window=periods['medium_term'], min_periods=1).mean()  # 72根1小时K线，即72小时(3天)
        df['sma_288'] = df['close'].rolling(window=periods['long_term'], min_periods=1).mean()  # 168根1小时K线，即168小时(7天)

        # 指数移动平均线 - 适用于1小时K线的EMA
        df['ema_12'] = df['close'].ewm(span=periods['short_term']).mean()  # 12根1小时K线，即12小时EMA
        df['ema_26'] = df['close'].ewm(span=26).mean()  # 26根1小时K线，即26小时EMA
        df['ema_60'] = df['close'].ewm(span=periods['medium_term']).mean()  # 72根1小时K线，即72小时EMA
        df['ema_144'] = df['close'].ewm(span=144).mean()  # 144根1小时K线，即144小时(6天)EMA
        df['ema_288'] = df['close'].ewm(span=periods['long_term']).mean()  # 168根1小时K线，即168小时(7天)EMA
        
        # MACD - 使用适合1小时K线的参数
        df['macd'] = df['ema_12'] - df['ema_26']
        df['macd_signal'] = df['macd'].ewm(span=9).mean()  # 9根1小时K线，即9小时信号线
        df['macd_histogram'] = df['macd'] - df['macd_signal']
        
        # 长期MACD - 适用于更长期趋势分析
        df['macd_long'] = df['ema_60'] - df['ema_144']
        df['macd_long_signal'] = df['macd_long'].ewm(span=18).mean()  # 18根1小时K线，即18小时信号线

        # 相对强弱指数 (RSI) - 适用于1小时K线的周期
        for period in [7, 14, 21]:
            delta = df['close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(period).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
            rs = gain / loss
            df[f'rsi_{period}'] = 100 - (100 / (1 + rs))

        # 布林带 - 适用于1小时K线的周期
        for period in [20, 50]:
            df[f'bb_middle_{period}'] = df['close'].rolling(period).mean()
            bb_std = df['close'].rolling(period).std()
            df[f'bb_upper_{period}'] = df[f'bb_middle_{period}'] + (bb_std * 2)
            df[f'bb_lower_{period}'] = df[f'bb_middle_{period}'] - (bb_std * 2)
            df[f'bb_position_{period}'] = (df['close'] - df[f'bb_lower_{period}']) / (df[f'bb_upper_{period}'] - df[f'bb_lower_{period}'])

        # 成交量指标 - 适用于1小时K线的周期
        for period in [20, 60]:
            df[f'volume_ma_{period}'] = df['volume'].rolling(period).mean()
        df['volume_ratio_short'] = df['volume'] / df['volume_ma_20']
        df['volume_ratio_long'] = df['volume'] / df['volume_ma_60']

        # 支撑阻力位 - 适用于1小时K线的周期
        for period in [20, 60, 168]:
            df[f'resistance_{period}'] = df['high'].rolling(period).max()
            df[f'support_{period}'] = df['low'].rolling(period).min()

        # 动量指标 - 适用于1小时K线的周期
        df['momentum_5'] = df['close'] / df['close'].shift(5) - 1  # 5小时动量
        df['momentum_10'] = df['close'] / df['close'].shift(10) - 1  # 10小时动量
        df['momentum_20'] = df['close'] / df['close'].shift(20) - 1  # 20小时动量

        # 价格变化率 - 适用于1小时K线的周期
        for period in [1, 5, 10, 20]:
            df[f'price_change_{period}'] = df['close'].pct_change(period)

        # 波动率指标 - 适用于1小时K线的周期
        df['volatility_20'] = df['close'].rolling(20).std()  # 20小时波动率
        df['volatility_60'] = df['close'].rolling(60).std()  # 60小时波动率

        # 填充NaN值
        df = df.bfill().ffill()

        return df
    except Exception as e:
        log_spot_warning(f"技术指标计算失败: {e}")
        return df


def get_spot_ohlcv_enhanced(symbol):
    """获取指定交易对的K线数据并计算技术指标"""
    try:
        config = SPOT_TRADE_CONFIGS[symbol]
        
        # 获取主要时间周期的K线数据
        ohlcv = spot_exchange.fetch_ohlcv(config['symbol'], SPOT_GLOBAL_CONFIG['timeframe'],
                                     limit=SPOT_GLOBAL_CONFIG['data_points'])

        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')

        # 计算技术指标
        df = calculate_spot_technical_indicators(df)

        current_data = df.iloc[-1]
        previous_data = df.iloc[-2]

        return {
            'symbol': symbol,
            'price': current_data['close'],
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'high': current_data['high'],
            'low': current_data['low'],
            'volume': current_data['volume'],
            'timeframe': SPOT_GLOBAL_CONFIG['timeframe'],
            'price_change': ((current_data['close'] - previous_data['close']) / previous_data['close']) * 100,
            'kline_data': df[['timestamp', 'open', 'high', 'low', 'close', 'volume']].tail(10).to_dict('records'),
            'technical_data': {
                # 短期指标
                'sma_5': current_data.get('sma_5', 0),
                'sma_12': current_data.get('sma_12', 0),
                'ema_12': current_data.get('ema_12', 0),
                'rsi_7': current_data.get('rsi_7', 0),
                'rsi_14': current_data.get('rsi_14', 0),
                'momentum_5': current_data.get('momentum_5', 0),
                'momentum_10': current_data.get('momentum_10', 0),
                'volatility_20': current_data.get('volatility_20', 0),
                
                # 中期指标
                'sma_60': current_data.get('sma_60', 0),
                'ema_60': current_data.get('ema_60', 0),
                'volume_ratio_short': current_data.get('volume_ratio_short', 0),
                'volume_ratio_long': current_data.get('volume_ratio_long', 0),
                
                # 长期指标
                'sma_288': current_data.get('sma_288', 0),
                'ema_288': current_data.get('ema_288', 0),
                
                # MACD指标
                'macd': current_data.get('macd', 0),
                'macd_signal': current_data.get('macd_signal', 0),
                'macd_histogram': current_data.get('macd_histogram', 0),
                'macd_long': current_data.get('macd_long', 0),
                
                # 布林带
                'bb_upper_20': current_data.get('bb_upper_20', 0),
                'bb_lower_20': current_data.get('bb_lower_20', 0),
                'bb_position_20': current_data.get('bb_position_20', 0),
                'bb_position_50': current_data.get('bb_position_50', 0),
                
                # 支撑阻力
                'resistance_20': current_data.get('resistance_20', 0),
                'support_20': current_data.get('support_20', 0),
                'resistance_60': current_data.get('resistance_60', 0),
                'support_60': current_data.get('support_60', 0),
            },
            'full_data': df
        }
    except Exception as e:
        log_spot_error(f"获取 {symbol} 增强K线数据失败: {e}")
        return None

def safe_json_parse(json_str):
    """安全解析JSON，处理格式不规范的情况"""
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        try:
            # 修复常见的JSON格式问题
            json_str = json_str.replace("'", '"')
            json_str = re.sub(r'(\w+):', r'"\1":', json_str)
            json_str = re.sub(r',\s*}', '}', json_str)
            json_str = re.sub(r',\s*]', ']', json_str)
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            log_spot_error(f"JSON解析失败，原始内容: {json_str}")
            log_spot_error(f"错误详情: {e}")
            return None


def create_spot_fallback_signal(price_data):
    """创建备用交易信号"""
    return {
        "signal": "HOLD",
        "reason": "因技术分析暂时不可用，采取保守策略",
        "stop_loss": price_data['price'] * 0.98,  # -2%
        "take_profit": price_data['price'] * 1.02,  # +2%
        "confidence": "LOW",
        "position_size_percent": 5,  # 默认5%仓位
        "action": "HOLD",
        "is_fallback": True
    }


def get_spot_current_position(symbol):
    """获取指定交易对的当前持仓情况（现货）"""
    try:
        config = SPOT_TRADE_CONFIGS[symbol]
        
        # 获取账户余额
        balance = spot_exchange.fetch_balance()
        
        # 获取基础货币和计价货币的余额
        base_currency = config['base_currency']  # 例如 BTC
        quote_currency = 'USDT'  # 计价货币
        
        base_balance = 0
        quote_balance = 0
        
        if base_currency in balance and 'free' in balance[base_currency]:
            base_balance = float(balance[base_currency]['free'])
            
        if quote_currency in balance and 'free' in balance[quote_currency]:
            quote_balance = float(balance[quote_currency]['free'])
        
        spot_print(f"{symbol} 调试 - 基础货币余额: {base_balance}, 计价货币余额: {quote_balance}")
        
        # 计算仓位百分比（基于账户总资金）
        try:
            # 获取账户总余额
            total_balance = 0
            if 'USDT' in balance:
                total_balance = float(balance['USDT'].get('total', 0))
            
            # 计算仓位百分比
            if total_balance > 0:
                # 仓位百分比基于基础货币的USDT价值
                position_value = base_balance * get_spot_ohlcv_enhanced(symbol)['price'] if get_spot_ohlcv_enhanced(symbol) else 0
                position_percentage = (position_value / total_balance) * 100
            else:
                position_percentage = 0
        except:
            position_percentage = 0
        
        # 对于现货，我们将基础货币余额视为持仓量，并计算仓位百分比
        return {
            'size': base_balance,  # 持仓数量为基础货币余额
            'entry_price': 0,     # 现货没有固定的入场价
            'position_amt': base_balance,  # 持仓数量
            'position_percentage': position_percentage,  # 仓位百分比
            'symbol': symbol,
            'base_balance': base_balance,
            'quote_balance': quote_balance
        }
        
    except Exception as e:
        log_spot_error(f"获取 {symbol} 现货余额失败: {e}")
        import traceback
        traceback.print_exc()
        return None


def get_spot_all_symbols_data():
    """获取所有启用交易对的数据"""
    all_data = {}
    all_positions = {}
    total_exposure = 0
    total_balance = 0
    
    try:
        # 获取账户总余额
        balance = spot_exchange.fetch_balance()
        if 'USDT' in balance:
            total_balance = float(balance['USDT'].get('total', 0))
            free_balance = float(balance['USDT'].get('free', 0))
        else:
            total_balance = 0
            free_balance = 0
    except:
        total_balance = 0
        free_balance = 0
    
    # 遍历所有启用的交易对
    for symbol in spot_enabled_symbols:
        try:
            # 获取价格数据
            price_data = get_spot_ohlcv_enhanced(symbol)
            if price_data:
                all_data[symbol] = price_data
            
            # 获取持仓信息（在现货中是余额信息）
            position = get_spot_current_position(symbol)
            all_positions[symbol] = position
            
            # 计算总敞口（这里简单计算为USDT余额）
            total_exposure = free_balance  # 现货交易中，敞口就是可用USDT余额
                
        except Exception as e:
            log_spot_warning(f"获取 {symbol} 数据失败: {e}")
            continue
    
    return {
        'symbols_data': all_data,
        'positions': all_positions,
        'account_info': {
            'total_balance': total_balance,
            'free_balance': free_balance,
            'total_exposure': total_exposure,
            'exposure_to_balance_ratio': (total_exposure / total_balance) if total_balance > 0 else 0
        }
    }


def generate_spot_enhanced_prompt(price_data, symbol, all_symbols_info=None):
    """生成多币种综合分析提示词（现货版本）"""
    config = SPOT_TRADE_CONFIGS[symbol]
    
    # 计算运行时间
    current_time = datetime.now()
    start_time_str = spot_trading_stats.get('start_time', current_time.strftime('%Y-%m-%d %H:%M:%S'))
    
    # 将字符串转换为datetime对象
    try:
        start_time = datetime.strptime(start_time_str, '%Y-%m-%d %H:%M:%S')
    except (ValueError, TypeError):
        start_time = current_time
    
    minutes_running = int((current_time - start_time).total_seconds() / 60)
    
    # 获取历史数据
    historical_prices = get_spot_historical_price_data(symbol, 20)
    historical_signals = get_spot_historical_signals(symbol, 10)
    
    # 获取当前持仓（余额信息）
    current_pos = get_spot_current_position(symbol)
    
    # 获取技术指标数据
    tech = price_data.get('technical_data', {})
    
    # 获取账户信息
    if all_symbols_info:
        account_info = all_symbols_info['account_info']
        all_positions = all_symbols_info['positions']
        all_data = all_symbols_info['symbols_data']
    else:
        # 如果没有提供多币种信息，使用原有逻辑
        try:
            balance = spot_exchange.fetch_balance()
            if 'USDT' in balance:
                usdt_balance = float(balance['USDT'].get('free', 0))
                total_balance = float(balance['USDT'].get('total', 0))
            else:
                usdt_balance = 0
                total_balance = 0
        except:
            usdt_balance = 0
            total_balance = 0
        
        account_info = {
            'total_balance': total_balance,
            'free_balance': usdt_balance,
            'total_exposure': 0,
            'exposure_to_balance_ratio': 0
        }
        all_positions = {symbol: current_pos}
        all_data = {symbol: price_data}
    
    # 准备价格序列数据
    df = price_data.get('full_data')
    if df is not None and len(df) >= 10:
        # 获取最近10个数据点的价格和指标
        recent_prices = df['close'].tail(10).tolist()
        recent_emas = df['ema_12'].tail(10).tolist() if 'ema_12' in df.columns else [0] * 10
        recent_macd = df['macd'].tail(10).tolist() if 'macd' in df.columns else [0] * 10
        recent_rsi_7 = df['rsi_7'].tail(10).tolist() if 'rsi_7' in df.columns else [0] * 10
        recent_rsi_14 = df['rsi_14'].tail(10).tolist() if 'rsi_14' in df.columns else [0] * 10
        
        # 获取更长周期的数据（72小时）
        try:
            longer_ohlcv = spot_exchange.fetch_ohlcv(config['symbol'], '1d', limit=20)
            longer_df = pd.DataFrame(longer_ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            longer_df = calculate_spot_technical_indicators(longer_df)
            
            longer_ema_20 = longer_df['ema_12'].iloc[-1] if 'ema_12' in longer_df.columns else 0
            longer_ema_50 = longer_df['ema_60'].iloc[-1] if 'ema_60' in longer_df.columns else 0
            
            # 计算ATR
            high_low = longer_df['high'] - longer_df['low']
            high_close = abs(longer_df['high'] - longer_df['close'].shift())
            low_close = abs(longer_df['low'] - longer_df['close'].shift())
            ranges = pd.concat([high_low, high_close, low_close], axis=1)
            true_range = ranges.max(axis=1)
            longer_df['atr_3'] = true_range.rolling(3).mean()
            longer_df['atr_14'] = true_range.rolling(14).mean()
            
            atr_3 = longer_df['atr_3'].iloc[-1]
            atr_14 = longer_df['atr_14'].iloc[-1]
            
            current_volume = longer_df['volume'].iloc[-1]
            avg_volume = longer_df['volume'].mean()
            
            # 计算夏普比率
            returns = longer_df['close'].pct_change().dropna()
            sharpe_ratio = returns.mean() / returns.std() * (252 ** 0.5) if returns.std() != 0 else 0
            
        except Exception as e:
            log_spot_warning(f"获取 {symbol} 长周期数据失败: {e}")
            longer_ema_20 = 0
            longer_ema_50 = 0
            atr_3 = 0
            atr_14 = 0
            current_volume = 0
            avg_volume = 0
            sharpe_ratio = 0
    else:
        recent_prices = [price_data['price']] * 10
        recent_emas = [tech.get('ema_12', 0)] * 10
        recent_macd = [tech.get('macd', 0)] * 10
        recent_rsi_7 = [tech.get('rsi_7', 0)] * 10
        recent_rsi_14 = [tech.get('rsi_14', 0)] * 10
        longer_ema_20 = tech.get('ema_12', 0)
        longer_ema_50 = tech.get('ema_60', 0)
        atr_3 = 0
        atr_14 = 0
        current_volume = price_data.get('volume', 0)
        avg_volume = current_volume
        sharpe_ratio = 0
    
    # 获取持仓信息
    position_info = "无持仓"
    if current_pos:
        base_balance = current_pos.get('base_balance', 0)
        quote_balance = current_pos.get('quote_balance', 0)
        position_info = f"基础货币余额: {base_balance} {symbol.split('/')[0]}, 计价货币余额: {quote_balance} USDT"
    
    # 构建多币种综合分析提示词
    prompt = f"""你已经开始交易 {minutes_running} 分钟。
当前时间是 {current_time.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} 
您已经被调用 {spot_trading_stats['total_calls']} 次.

=== 多币种综合投资组合分析（现货） ===

当前账户总览:
- 总余额: {account_info['total_balance']:.2f} USDT
- 可用余额: {account_info['free_balance']:.2f} USDT
- 可用资金: {account_info['total_exposure']:.2f} USDT
- 可用资金/总余额比: {account_info['exposure_to_balance_ratio']:.2f}

当前所有币种余额情况:
"""
    
    # 添加所有币种的余额信息
    for sym, pos in all_positions.items():
        if pos:
            base_bal = pos.get('base_balance', 0)
            quote_bal = pos.get('quote_balance', 0)
            prompt += f"- {sym}: 基础货币 {base_bal:.6f}, USDT {quote_bal:.2f}\n"
        else:
            prompt += f"- {sym}: 无数据\n"
    
    prompt += f"""

=== 当前分析币种: {symbol} 详细数据 ===

当前价格: {price_data['price']} USDT

主要时间周期 ({SPOT_GLOBAL_CONFIG['timeframe']}) 技术指标:
短期指标 (1-5小时):
- SMA5: {tech.get('sma_5', 0):.2f}, SMA12: {tech.get('sma_12', 0):.2f}, EMA12: {tech.get('ema_12', 0):.2f}
- RSI7: {tech.get('rsi_7', 0):.1f}, RSI14: {tech.get('rsi_14', 0):.1f}
- 动量5: {tech.get('momentum_5', 0):+.3%}, 动量10: {tech.get('momentum_10', 0):+.3%}
- 波动率20: {tech.get('volatility_20', 0):.4f}

中期指标 (5小时):
- SMA60: {tech.get('sma_60', 0):.2f}, EMA60: {tech.get('ema_60', 0):.2f}
- 成交量比率(短期): {tech.get('volume_ratio_short', 0):.2f}
- 成交量比率(长期): {tech.get('volume_ratio_long', 0):.2f}

长期指标 (24小时):
- SMA288: {tech.get('sma_288', 0):.2f}, EMA288: {tech.get('ema_288', 0):.2f}

MACD指标:
- 短期MACD: {tech.get('macd', 0):.4f}, 信号线: {tech.get('macd_signal', 0):.4f}, 柱状图: {tech.get('macd_histogram', 0):.4f}
- 长期MACD: {tech.get('macd_long', 0):.4f}

布林带指标:
- 20周期布林带位置: {tech.get('bb_position_20', 0):.2%}
- 50周期布林带位置: {tech.get('bb_position_50', 0):.2%}

支撑阻力位:
- 短期支撑: {tech.get('support_20', 0):.2f} | 短期阻力: {tech.get('resistance_20', 0):.2f}
- 中期支撑: {tech.get('support_60', 0):.2f} | 中期阻力: {tech.get('resistance_60', 0):.2f}

价格序列 (最近10个): {[f"{p:.1f}" for p in recent_prices]}
EMA指标序列 (12周期): {[f"{e:.3f}" for e in recent_emas]}
MACD指标序列: {[f"{m:.3f}" for m in recent_macd]}
RSI指标序列 (7周期): {[f"{r:.3f}" for r in recent_rsi_7]}
RSI指标序列 (14周期): {[f"{r:.3f}" for r in recent_rsi_14]}

长周期技术面 (4小时):
- EMA12: {longer_ema_20:.3f} vs EMA60: {longer_ema_50:.3f}
- ATR3: {atr_3:.3f} vs ATR14: {atr_14:.3f}
- 当前成交量: {current_volume:.3f} vs 平均成交量: {avg_volume:.3f}
- 夏普比率: {sharpe_ratio:.3f}

最近交易信号 (最近5次):
"""
    
    # 添加最近信号历史
    for i, signal in enumerate(historical_signals[:5]):
        prompt += f"{i+1}. {signal[2]} at {signal[1]} - 信心: {signal[6]}, 价格: ${signal[7]:.2f}\n"
    
    # 添加其他币种的简要信息
    if all_symbols_info and len(all_data) > 1:
        prompt += "\n=== 其他币种概况 ===\n"
        for sym, data in all_data.items():
            if sym != symbol and data:
                other_pos = all_positions.get(sym)
                other_tech = data.get('technical_data', {})
                other_price = data.get('price', 0)
                other_change = data.get('price_change', 0)
                
                pos_status = f"(有余额)" if other_pos and (other_pos.get('base_balance', 0) > 0 or other_pos.get('quote_balance', 0) > 0) else "(无余额)"
                prompt += f"- {sym}: ${other_price:.2f} ({other_change:+.2f}%) RSI:{other_tech.get('rsi', 0):.1f} {pos_status}\n"
    
    prompt += f"""

=== 交易决策指令 ===

基于以上多币种综合分析，为 {symbol} 提供现货交易决策。

重要风险管理原则:
1. 总资金使用不应超过账户余额的80%
2. 单个币种交易金额不超过总余额的30%
3. 根据当前整体资金情况动态调整交易金额
4. 考虑与其他币种的相关性，避免过度集中风险

防频繁交易重要原则:
1. 趋势持续性优先: 不要因单根K线或短期波动改变整体趋势判断
2. 持仓稳定性: 除非趋势明确强烈反转，否则避免频繁调整仓位
3. 反转确认: 需要至少2-3个技术指标同时确认趋势反转才改变信号，避免噪音影响
4. 成本意识: 减少不必要的仓位调整，每次交易都有成本

交易金额设置策略:
- 高信心信号 + 低整体资金使用: 可使用较大交易金额(15-25%)
- 中等信心信号 + 中等资金使用: 使用适中交易金额(8-15%)
- 低信心信号 + 高资金使用: 使用小交易金额(3-8%)
- 已有持仓: 考虑增加仓位或保持现状
- 已经满仓: 市场信号给出的趋势非常明确时，考虑卖出部分或全部卖出以降低风险

请返回JSON格式的交易决策:
{{
  "signal": "BUY" | "SELL" | "HOLD",
  "reason": "详细分析理由，包括多币种综合考量",
  "stop_loss": 具体止损价格,
  "take_profit": 具体止盈价格,
  "confidence": "HIGH" | "MEDIUM" | "LOW",
  "position_size_percent": 交易金额占可用资金百分比(1-100),
  "action": "OPEN" | "CLOSE" | "INCREASE" | "DECREASE" | "HOLD",
  "risk_assessment": "风险评估说明"
}}
"""
    return prompt


def analyze_spot_with_deepseek(price_data, symbol, all_symbols_info=None):
    """使用DeepSeek进行多币种综合分析并生成交易信号（现货版本）"""
    
    # 更新统计
    spot_trading_stats['total_calls'] += 1
    spot_trading_stats['last_signal_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # 如果没有提供多币种信息，获取一次
    if all_symbols_info is None:
        all_symbols_info = get_spot_all_symbols_data()
    
    # 生成多币种综合分析提示词
    prompt = generate_spot_enhanced_prompt(price_data, symbol, all_symbols_info)

    spot_print(f"{symbol} DeepSeek多币种综合分析提示词生成完毕，准备请求DeepSeek API...")
    spot_print(f"{symbol} 提示词长度: {len(prompt)} 字符")
    spot_print(f"{symbol} 完整提示词: \n{prompt}\n")

     # 调用DeepSeek API进行多币种综合分析

    try:
        response = deepseek_client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system",
                 "content": f"你是一个专业的加密货币投资组合分析师，专门进行多币种现货交易分析。你需要基于整体投资组合的资金状况来制定交易决策，而不仅仅是单个币种的技术分析。请严格按照要求的JSON格式返回交易信号，使用中文回复。"},
                {"role": "user", "content": prompt}
            ],
            stream=False,
            temperature=0.1
        )

        # 安全解析JSON
        result = response.choices[0].message.content

        # 提取JSON部分
        start_idx = result.find('{')
        end_idx = result.rfind('}') + 1

        if start_idx != -1 and end_idx != 0:
            json_str = result[start_idx:end_idx]
            signal_data = safe_json_parse(json_str)

            if signal_data is None:
                signal_data = create_spot_fallback_signal(price_data)
        else:
            signal_data = create_spot_fallback_signal(price_data)

        # 验证必需字段
        required_fields = ['signal', 'reason', 'stop_loss', 'take_profit', 'confidence', 'position_size_percent', 'action']
        if not all(field in signal_data for field in required_fields):
            signal_data = create_spot_fallback_signal(price_data)
            # 为备用信号添加默认字段
            signal_data['position_size_percent'] = 10  # 默认10%
            signal_data['action'] = 'HOLD'

        # 添加风险评估字段（如果没有）
        if 'risk_assessment' not in signal_data:
            signal_data['risk_assessment'] = "基于当前投资组合状况的风险评估"

        # 保存信号到历史记录
        signal_data['timestamp'] = price_data['timestamp']
        
        # 按交易对分别记录信号历史
        if symbol not in spot_signal_history:
            spot_signal_history[symbol] = []
        
        spot_signal_history[symbol].append(signal_data)
        if len(spot_signal_history[symbol]) > 30:
            spot_signal_history[symbol].pop(0)

        # 保存到数据库
        save_spot_trading_signal(signal_data, price_data, symbol)
        update_spot_trading_stats()

        # 信号统计（按交易对分别统计）
        symbol_signals = spot_signal_history.get(symbol, [])
        signal_count = len([s for s in symbol_signals if s.get('signal') == signal_data['signal']])
        total_signals = len(symbol_signals)
        spot_print(f"{symbol} 信号统计: {signal_data['signal']} (最近{total_signals}次中出现{signal_count}次)")

        # 信号连续性检查（按交易对分别检查）
        if len(symbol_signals) >= 3:
            last_three = [s['signal'] for s in symbol_signals[-3:]]
            if len(set(last_three)) == 1:
                log_spot_warning(f"{symbol} ⚠️ 注意：连续3次{signal_data['signal']}信号")

        # 打印多币种分析结果
        spot_print(f"{symbol} 多币种综合分析结果:")
        spot_print(f"  信号: {signal_data['signal']}")
        spot_print(f"  仓位: {signal_data['position_size_percent']}%")
        spot_print(f"  风险评估: {signal_data.get('risk_assessment', 'N/A')}")

        return signal_data

    except Exception as e:
        log_spot_error(f"{symbol} DeepSeek多币种分析失败: {e}")
        return create_spot_fallback_signal(price_data)


def analyze_spot_with_deepseek_with_retry(price_data, symbol, all_symbols_info=None, max_retries=2):
    """带重试的DeepSeek多币种分析（现货版本）"""
    for attempt in range(max_retries):
        try:
            signal_data = analyze_spot_with_deepseek(price_data, symbol, all_symbols_info)
            if signal_data and not signal_data.get('is_fallback', False):
                return signal_data

            spot_print(f"{symbol} 第{attempt + 1}次尝试失败，进行重试...")
            time.sleep(1)

        except Exception as e:
            log_spot_error(f"{symbol} 第{attempt + 1}次尝试异常: {e}")
            if attempt == max_retries - 1:
                return create_spot_fallback_signal(price_data)
            time.sleep(1)

    return create_spot_fallback_signal(price_data)


def execute_spot_trade(signal_data, price_data, symbol):
    """执行现货交易"""
    config = SPOT_TRADE_CONFIGS[symbol]
    current_position = get_spot_current_position(symbol)

    spot_print(f"{symbol} 交易信号: {signal_data['signal']}")
    spot_print(f"{symbol} 信心程度: {signal_data['confidence']}")
    spot_print(f"{symbol} 理由: {signal_data['reason']}")
    spot_print(f"{symbol} 止损: ${signal_data['stop_loss']:,.2f}")
    spot_print(f"{symbol} 止盈: ${signal_data['take_profit']:,.2f}")
    spot_print(f"{symbol} 当前持仓: {current_position}")

    # 风险管理：低信心信号不执行
    if signal_data['confidence'] == 'LOW' and not SPOT_GLOBAL_CONFIG['test_mode']:
        log_spot_warning(f"{symbol} ⚠️ 低信心信号，跳过执行")
        return

    if SPOT_GLOBAL_CONFIG['test_mode']:
        spot_print(f"{symbol} 测试模式 - 仅模拟交易")
        return

    try:
        # 获取账户余额
        balance = spot_exchange.fetch_balance()
        # 检查余额结构并获取USDT余额
        if 'USDT' in balance:
            usdt_balance = balance['USDT'].get('free', 0)
            if isinstance(usdt_balance, (int, float)):
                # 确保是数字类型
                usdt_balance = float(usdt_balance)
                spot_print(f"{symbol} 可用USDT余额: {usdt_balance:.2f} USDT")
            else:
                log_spot_warning(f"{symbol} 警告 - USDT余额不是数字类型: {usdt_balance}")
                usdt_balance = 0  # 如果不是数字，设为0
        else:
            spot_print(f"{symbol} 未找到USDT余额信息")
            spot_print(f"{symbol} 可用余额类型: {list(balance.keys())}")
            usdt_balance = 0

        # 从信号中获取交易参数
        position_size_percent = signal_data.get('position_size_percent', 10)  # 默认10%
        action = signal_data.get('action', 'HOLD')

        # 计算实际交易金额（USDT）
        position_size_usdt = usdt_balance * (position_size_percent / 100)
        
        # 根据动作类型执行不同的交易逻辑
        if action == 'HOLD' or signal_data['signal'] == 'HOLD':
            print(f"{symbol} 建议观望，不执行交易")
            return

        # 计算交易数量（以基础货币为单位）
        if signal_data['signal'] in ['BUY', 'SELL']:
            trade_amount_base = position_size_usdt / price_data['price']
            
            spot_print(f"{symbol} 交易金额: {position_size_percent}% ({position_size_usdt:.2f} USDT)")
            spot_print(f"{symbol} 计算得出的交易数量({config['base_currency']}): {trade_amount_base:.6f}")

            # 检查余额是否足够
            if position_size_usdt > usdt_balance * 0.9:  # 留10%作为缓冲
                log_spot_warning(f"{symbol} ⚠️ 资金不足，跳过交易。需要: {position_size_usdt:.2f} USDT, 可用: {usdt_balance:.2f} USDT")
                return

            spot_print(f"{symbol} ✅ 资金充足，继续执行")

            # 执行交易逻辑
            if signal_data['signal'] == 'BUY':
                spot_print(f"{symbol} 买入...")
                spot_exchange.create_market_buy_order(
                    config['symbol'],
                    trade_amount_base
                )

            elif signal_data['signal'] == 'SELL':
                # 检查是否有足够的基础货币余额
                base_currency = config['base_currency']
                if base_currency in balance and 'free' in balance[base_currency]:
                    base_balance = float(balance[base_currency]['free'])
                    if base_balance >= trade_amount_base:
                        spot_print(f"{symbol} 卖出...")
                        spot_exchange.create_market_sell_order(
                            config['symbol'],
                            trade_amount_base
                        )
                    else:
                        log_spot_warning(f"{symbol} ⚠️ 基础货币余额不足，跳过交易。需要: {trade_amount_base:.6f}, 可用: {base_balance:.6f}")
                else:
                    log_spot_warning(f"{symbol} ⚠️ 未找到基础货币余额信息")

        spot_print(f"{symbol} 订单执行成功")
        time.sleep(2)
        position = get_spot_current_position(symbol)
        spot_print(f"{symbol} 更新后持仓: {position}")

    except Exception as e:
        log_spot_error(f"{symbol} 订单执行失败: {e}")
        import traceback
        traceback.print_exc()


def spot_trading_bot_for_symbol(symbol, all_symbols_info=None):
    """为单个交易对执行现货交易逻辑（支持多币种综合分析）"""
    config = SPOT_TRADE_CONFIGS[symbol]
    
    if not config['enabled']:
        spot_print(f"{symbol} 交易对已禁用，跳过")
        return
    
    spot_print(f"\n{'='*20} {symbol} 现货交易 {'='*20}")
    spot_print(f"执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    spot_print(f"{'='*50}")
    
    # 1. 获取增强版K线数据
    price_data = get_spot_ohlcv_enhanced(symbol)
    if not price_data:
        log_spot_warning(f"{symbol} 获取价格数据失败，跳过")
        return
    
    spot_print(f"{symbol} 当前价格: ${price_data['price']:,.2f}")
    spot_print(f"{symbol} 数据周期: {SPOT_GLOBAL_CONFIG['timeframe']}")
    spot_print(f"{symbol} 价格变化: {price_data['price_change']:+.2f}%")
    
    # 2. 保存价格数据到数据库
    save_spot_price_data(price_data, symbol)
    
    # 3. 获取当前持仓并保存到数据库
    current_position = get_spot_current_position(symbol)
    save_spot_position_record(current_position, symbol)
    
    # 4. 使用DeepSeek多币种综合分析（带重试）
    signal_data = analyze_spot_with_deepseek_with_retry(price_data, symbol, all_symbols_info)
    
    if signal_data.get('is_fallback', False):
        log_spot_warning(f"{symbol} ⚠️ 使用备用交易信号")
    
    # 5. 执行交易
    execute_spot_trade(signal_data, price_data, symbol)
    
    # 6. 更新交易统计
    if signal_data['signal'] in ['BUY', 'SELL']:
        spot_trading_stats['total_trades'] += 1
        # 这里可以添加更复杂的盈亏计算逻辑
        update_spot_trading_stats()


def spot_trading_bot():
    """主现货交易机器人函数 - 处理所有启用的交易对（多币种综合分析）"""
    spot_print("\n" + "=" * 60)
    spot_print(f"多交易对现货交易机器人执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    spot_print("=" * 60)
    
    # 获取所有币种的综合数据（一次获取，多次使用）
    spot_print("正在获取所有币种的综合数据...")
    all_symbols_info = get_spot_all_symbols_data()
    
    # 显示账户总览
    account_info = all_symbols_info['account_info']
    spot_print(f"账户总览:")
    spot_print(f"  总余额: {account_info['total_balance']:.2f} USDT")
    spot_print(f"  可用余额: {account_info['free_balance']:.2f} USDT")
    spot_print(f"  可用资金: {account_info['total_exposure']:.2f} USDT")
    spot_print(f"  可用资金/总余额比: {account_info['exposure_to_balance_ratio']:.2f}")
    
    # 遍历所有启用的交易对
    for symbol in spot_enabled_symbols:
        spot_trading_bot_for_symbol(symbol, all_symbols_info)
        # 在处理不同交易对之间添加短暂延迟，避免API限制
        time.sleep(1)


def spot_main():
    """现货交易主函数"""
    spot_print("多交易对币安现货自动交易机器人启动成功！")
    spot_print(f"启用的交易对: {', '.join(spot_enabled_symbols)}")
    spot_print("融合技术指标策略 + 币安实盘接口 + SQLite数据库存储")

    if SPOT_GLOBAL_CONFIG['test_mode']:
        spot_print("当前为模拟模式，不会真实下单")
    else:
        spot_print("实盘交易模式，请谨慎操作！")

    spot_print(f"交易周期: {SPOT_GLOBAL_CONFIG['timeframe']}")
    spot_print("已启用完整技术指标分析、持仓跟踪功能和历史数据存储")

    # 初始化数据库
    if not init_spot_database():
        log_spot_error("数据库初始化失败，程序退出")
        return
    
    # 加载历史统计数据
    load_spot_trading_stats()
    
    # 设置开始时间（如果是新启动）
    if spot_trading_stats['start_time'] is None:
        spot_trading_stats['start_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        update_spot_trading_stats()
        spot_print(f"设置新的启动时间: {spot_trading_stats['start_time']}")
    else:
        spot_print(f"使用历史启动时间: {spot_trading_stats['start_time']}")

    # 根据时间周期设置执行频率
    if SPOT_GLOBAL_CONFIG['timeframe'] == '1h':
        schedule.every().hour.at(":01").do(spot_trading_bot)
        spot_print("执行频率: 每小时一次")
    elif SPOT_GLOBAL_CONFIG['timeframe'] == '15m':
        schedule.every(15).minutes.do(spot_trading_bot)
        spot_print("执行频率: 每15分钟一次")
    elif SPOT_GLOBAL_CONFIG['timeframe'] == '5m':
        schedule.every(5).minutes.do(spot_trading_bot)
        spot_print("执行频率: 每5分钟一次")
    else:
        schedule.every().hour.at(":01").do(spot_trading_bot)
        spot_print("执行频率: 每小时一次")

    # 立即执行一次
    spot_trading_bot()

    # 循环执行
    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        spot_print("\n程序被用户中断")
        # 关闭数据库连接
        if spot_db_connection:
            spot_db_connection.close()
            spot_print("数据库连接已关闭")
    except Exception as e:
        log_spot_error(f"程序运行异常: {e}")
        # 关闭数据库连接
        if spot_db_connection:
            spot_db_connection.close()
            spot_print("数据库连接已关闭")


if __name__ == "__main__":
    spot_main()