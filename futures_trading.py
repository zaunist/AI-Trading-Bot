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
from thread_logger import log_futures, log_futures_warning, log_futures_error, futures_print

load_dotenv()

# 初始化DeepSeek客户端
deepseek_client = OpenAI(
    api_key=os.getenv('DEEPSEEK_API_KEY'),
    base_url="https://api.deepseek.com"
)

exchange = ccxt.binance({
    'options': {
        'defaultType': 'future'},
    'apiKey': os.getenv('BINANCE_FUTURES_API_KEY'),
    'secret': os.getenv('BINANCE_FUTURES_SECRET'),
})

# 交易参数配置 - 支持多个交易对
GLOBAL_CONFIG = {
    'timeframe': "5m",  # 使用5分钟K线
    'test_mode': os.getenv('TEST_MODE'),  # 测试模式
    'data_points': 288,  # 24小时数据（288根5分钟K线）
    'analysis_periods': {
        'short_term': 12,   # 1小时短期均线 (12 * 5分钟)
        'medium_term': 60,   # 5小时中期均线 (60 * 5分钟)
        'long_term': 288,    # 24小时长期趋势 (288 * 5分钟)
        'very_long_term': 1440  # 5天超长期趋势 (1440 * 5分钟)
    },
    'multi_timeframe_analysis': {
        'primary': '5m',      # 主要分析周期
        'secondary': '15m',    # 次要分析周期
        'tertiary': '1h',      # 三级分析周期
        'long_term': '4h'      # 长期分析周期
    }
}

# 各交易对的独立配置
TRADE_CONFIGS = {
    'BTC/USDT': {
        'symbol': 'BTC/USDT',  # 币安的合约符号格式
        'base_currency': 'BTC',  # 基础货币
        'enabled': True  # 是否启用此交易对
    },
    'ETH/USDT': {
        'symbol': 'ETH/USDT',  # 币安的合约符号格式
        'base_currency': 'ETH',  # 基础货币
        'enabled': True  # 是否启用此交易对
    },
    'SOL/USDT': {
        'symbol': 'SOL/USDT',  # 币安的合约符号格式
        'base_currency': 'SOL',  # 基础货币
        'enabled': True  # 是否启用此交易对
    },
    'BNB/USDT': {
        'symbol': 'BNB/USDT',  # 币安的合约符号格式
        'base_currency': 'BNB',  # 基础货币
        'enabled': True  # 是否启用此交易对
    }
}

signal_history = {}  # 改为字典，按交易对分别记录信号历史
position = None

# 获取启用的交易对列表
enabled_symbols = [symbol for symbol, config in TRADE_CONFIGS.items() if config['enabled']]

# 数据库相关变量
db_connection = None
db_lock = threading.Lock()

# 交易统计
trading_stats = {
    'start_time': None,
    'total_calls': 0,
    'total_trades': 0,
    'successful_trades': 0,
    'total_pnl': 0.0,
    'last_signal_time': None
}


def init_database():
    """初始化SQLite数据库"""
    global db_connection
    try:
        with db_lock:
            db_connection = sqlite3.connect('/app/data/trading_bot.db', check_same_thread=False)
            cursor = db_connection.cursor()
            
            # 检查并添加symbol字段到现有表（如果不存在）
            try:
                # 检查price_data表是否有symbol字段
                cursor.execute("PRAGMA table_info(price_data)")
                columns = [column[1] for column in cursor.fetchall()]
                if 'symbol' not in columns:
                    cursor.execute("ALTER TABLE price_data ADD COLUMN symbol TEXT")
                    futures_print("为price_data表添加symbol字段")
            except:
                pass
            
            try:
                # 检查trading_signals表是否有symbol字段
                cursor.execute("PRAGMA table_info(trading_signals)")
                columns = [column[1] for column in cursor.fetchall()]
                if 'symbol' not in columns:
                    cursor.execute("ALTER TABLE trading_signals ADD COLUMN symbol TEXT")
                    futures_print("为trading_signals表添加symbol字段")
            except:
                pass
            
            try:
                # 检查position_records表是否有symbol字段
                cursor.execute("PRAGMA table_info(position_records)")
                columns = [column[1] for column in cursor.fetchall()]
                if 'symbol' not in columns:
                    cursor.execute("ALTER TABLE position_records ADD COLUMN symbol TEXT")
                    futures_print("为position_records表添加symbol字段")
                    
                # 检查并添加take_profit字段（如果不存在）
                if 'take_profit' not in columns:
                    cursor.execute("ALTER TABLE position_records ADD COLUMN take_profit REAL")
                    futures_print("为position_records表添加take_profit字段")
            except:
                pass
            
            try:
                # 检查technical_indicators表是否有symbol字段
                cursor.execute("PRAGMA table_info(technical_indicators)")
                columns = [column[1] for column in cursor.fetchall()]
                if 'symbol' not in columns:
                    cursor.execute("ALTER TABLE technical_indicators ADD COLUMN symbol TEXT")
                    futures_print("为technical_indicators表添加symbol字段")
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
                    side TEXT,
                    size REAL,
                    entry_price REAL,
                    unrealized_pnl REAL,
                    position_amt REAL,
                    stop_loss REAL,
                    take_profit REAL,
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
            
            db_connection.commit()
            futures_print("数据库初始化成功")
            return True
            
    except Exception as e:
        log_futures_error(f"数据库初始化失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def save_price_data(price_data, symbol):
    """保存价格数据到数据库"""
    global db_connection
    try:
        with db_lock:
            cursor = db_connection.cursor()
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
            
            db_connection.commit()
            
    except Exception as e:
        log_futures_warning(f"保存价格数据失败: {e}")


def save_trading_signal(signal_data, price_data, symbol):
    """保存交易信号到数据库"""
    global db_connection
    try:
        with db_lock:
            cursor = db_connection.cursor()
            
            # 检查是否需要添加新字段
            try:
                cursor.execute("PRAGMA table_info(trading_signals)")
                columns = [column[1] for column in cursor.fetchall()]
                
                # 添加新字段（如果不存在）
                if 'partial_close_percentage' not in columns:
                    cursor.execute("ALTER TABLE trading_signals ADD COLUMN partial_close_percentage REAL")
                    futures_print("为trading_signals表添加partial_close_percentage字段")
                
                if 'signal_strength' not in columns:
                    cursor.execute("ALTER TABLE trading_signals ADD COLUMN signal_strength TEXT")
                    futures_print("为trading_signals表添加signal_strength字段")
                    
                if 'risk_assessment' not in columns:
                    cursor.execute("ALTER TABLE trading_signals ADD COLUMN risk_assessment TEXT")
                    futures_print("为trading_signals表添加risk_assessment字段")
                    
            except Exception as e:
                log_futures_warning(f"检查/添加trading_signals表字段失败: {e}")
            
            cursor.execute('''
                INSERT INTO trading_signals
                (symbol, timestamp, signal, reason, stop_loss, take_profit, confidence, price, is_fallback,
                 partial_close_percentage, signal_strength, risk_assessment)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                symbol,
                signal_data.get('timestamp', datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
                signal_data['signal'],
                signal_data['reason'],
                signal_data.get('stop_loss'),
                signal_data.get('take_profit'),
                signal_data.get('confidence'),
                price_data['price'],
                1 if signal_data.get('is_fallback', False) else 0,
                signal_data.get('partial_close_percentage', 50),
                signal_data.get('signal_strength', 'MEDIUM'),
                signal_data.get('risk_assessment', '')
            ))
            
            db_connection.commit()
            
    except Exception as e:
        log_futures_warning(f"保存交易信号失败: {e}")


def save_position_record(position_data, symbol, stop_loss=None, take_profit=None):
    """保存持仓记录到数据库"""
    global db_connection
    try:
        with db_lock:
            cursor = db_connection.cursor()
            
            if position_data:
                cursor.execute('''
                    INSERT INTO position_records
                    (symbol, timestamp, side, size, entry_price, unrealized_pnl, position_amt, stop_loss, take_profit)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    symbol,
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    position_data['side'],
                    position_data['size'],
                    position_data['entry_price'],
                    position_data.get('unrealized_pnl', 0),
                    position_data.get('position_amt', 0),
                    stop_loss if stop_loss and stop_loss > 0 else -1,
                    take_profit if take_profit and take_profit > 0 else -1
                ))
            else:
                cursor.execute('''
                    INSERT INTO position_records
                    (symbol, timestamp, side, size, entry_price, unrealized_pnl, position_amt, stop_loss, take_profit)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    symbol,
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    None, 0, 0, 0, 0,
                    stop_loss if stop_loss and stop_loss > 0 else -1,
                    take_profit if take_profit and take_profit > 0 else -1
                ))
            
            db_connection.commit()
            
    except Exception as e:
        log_futures_warning(f"保存持仓记录失败: {e}")


def update_trading_stats():
    """更新交易统计"""
    global db_connection, trading_stats
    try:
        with db_lock:
            cursor = db_connection.cursor()
            
            cursor.execute('''
                UPDATE trading_stats
                SET total_calls = ?, total_trades = ?, successful_trades = ?,
                    total_pnl = ?, last_signal_time = ?
                WHERE id = 1
            ''', (
                trading_stats['total_calls'],
                trading_stats['total_trades'],
                trading_stats['successful_trades'],
                trading_stats['total_pnl'],
                trading_stats['last_signal_time']
            ))
            
            db_connection.commit()
            
    except Exception as e:
        log_futures_warning(f"更新交易统计失败: {e}")


def load_trading_stats():
    """加载交易统计"""
    global db_connection, trading_stats
    try:
        with db_lock:
            cursor = db_connection.cursor()
            cursor.execute('SELECT * FROM trading_stats WHERE id = 1')
            result = cursor.fetchone()
            
            if result:
                trading_stats['start_time'] = result[1]
                trading_stats['total_calls'] = result[2]
                trading_stats['total_trades'] = result[3]
                trading_stats['successful_trades'] = result[4]
                trading_stats['total_pnl'] = result[5]
                trading_stats['last_signal_time'] = result[6]
                futures_print(f"已加载历史统计数据: 启动时间 {result[1]}, 总调用次数 {result[2]}")
            else:
                futures_print("未找到历史统计数据，使用默认值")
            
    except Exception as e:
        print(f"加载交易统计失败: {e}")


def get_historical_price_data(symbol, limit=100):
    """获取历史价格数据"""
    global db_connection
    try:
        with db_lock:
            cursor = db_connection.cursor()
            cursor.execute('''
                SELECT * FROM price_data
                WHERE symbol = ?
                ORDER BY timestamp DESC
                LIMIT ?
            ''', (symbol, limit))
            
            results = cursor.fetchall()
            return results
            
    except Exception as e:
        log_futures_warning(f"获取历史价格数据失败: {e}")
        return []


def get_historical_signals(symbol, limit=30):
    """获取历史交易信号"""
    global db_connection
    try:
        with db_lock:
            cursor = db_connection.cursor()
            
            # 检查表结构，确定哪些字段存在
            cursor.execute("PRAGMA table_info(trading_signals)")
            columns = [column[1] for column in cursor.fetchall()]
            
            # 构建查询语句，包含所有可能的字段
            if 'partial_close_percentage' in columns and 'signal_strength' in columns and 'risk_assessment' in columns:
                cursor.execute('''
                    SELECT timestamp, signal, reason, stop_loss, take_profit, confidence, price, is_fallback,
                           partial_close_percentage, signal_strength, risk_assessment
                    FROM trading_signals
                    WHERE symbol = ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                ''', (symbol, limit))
            else:
                # 使用原有字段
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
        log_futures_warning(f"获取历史交易信号失败: {e}")
        return []


def calculate_technical_indicators(df):
    """计算技术指标 - 优化版多时间周期分析"""
    try:
        periods = GLOBAL_CONFIG['analysis_periods']
        
        # 移动平均线 - 使用优化后的周期
        df['sma_5'] = df['close'].rolling(window=5, min_periods=1).mean()
        df['sma_12'] = df['close'].rolling(window=periods['short_term'], min_periods=1).mean()  # 1小时
        df['sma_60'] = df['close'].rolling(window=periods['medium_term'], min_periods=1).mean()  # 5小时
        df['sma_288'] = df['close'].rolling(window=periods['long_term'], min_periods=1).mean()  # 24小时

        # 指数移动平均线 - 多周期EMA
        df['ema_12'] = df['close'].ewm(span=periods['short_term']).mean()  # 1小时EMA
        df['ema_26'] = df['close'].ewm(span=26).mean()
        df['ema_60'] = df['close'].ewm(span=periods['medium_term']).mean()  # 5小时EMA
        df['ema_144'] = df['close'].ewm(span=144).mean()  # 12小时EMA
        df['ema_288'] = df['close'].ewm(span=periods['long_term']).mean()  # 24小时EMA
        
        # MACD - 使用适合5分钟K线的参数
        df['macd'] = df['ema_12'] - df['ema_26']
        df['macd_signal'] = df['macd'].ewm(span=9).mean()
        df['macd_histogram'] = df['macd'] - df['macd_signal']
        
        # 多周期MACD
        df['macd_long'] = df['ema_60'] - df['ema_144']
        df['macd_long_signal'] = df['macd_long'].ewm(span=18).mean()

        # 相对强弱指数 (RSI) - 多周期
        for period in [7, 14, 21]:
            delta = df['close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(period).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
            rs = gain / loss
            df[f'rsi_{period}'] = 100 - (100 / (1 + rs))

        # 布林带 - 多周期
        for period in [20, 50]:
            df[f'bb_middle_{period}'] = df['close'].rolling(period).mean()
            bb_std = df['close'].rolling(period).std()
            df[f'bb_upper_{period}'] = df[f'bb_middle_{period}'] + (bb_std * 2)
            df[f'bb_lower_{period}'] = df[f'bb_middle_{period}'] - (bb_std * 2)
            df[f'bb_position_{period}'] = (df['close'] - df[f'bb_lower_{period}']) / (df[f'bb_upper_{period}'] - df[f'bb_lower_{period}'])

        # 成交量指标 - 多周期
        for period in [20, 60]:
            df[f'volume_ma_{period}'] = df['volume'].rolling(period).mean()
        df['volume_ratio_short'] = df['volume'] / df['volume_ma_20']
        df['volume_ratio_long'] = df['volume'] / df['volume_ma_60']

        # 支撑阻力位 - 多周期
        for period in [20, 60, 288]:
            df[f'resistance_{period}'] = df['high'].rolling(period).max()
            df[f'support_{period}'] = df['low'].rolling(period).min()

        # 动量指标
        df['momentum_5'] = df['close'] / df['close'].shift(5) - 1
        df['momentum_10'] = df['close'] / df['close'].shift(10) - 1
        df['momentum_20'] = df['close'] / df['close'].shift(20) - 1

        # 价格变化率
        for period in [1, 5, 10, 20]:
            df[f'price_change_{period}'] = df['close'].pct_change(period)

        # 波动率指标
        df['volatility_20'] = df['close'].rolling(20).std()
        df['volatility_60'] = df['close'].rolling(60).std()

        # 填充NaN值
        df = df.bfill().ffill()

        return df
    except Exception as e:
        log_futures_warning(f"技术指标计算失败: {e}")
        return df

def get_multi_timeframe_data(symbol):
    """获取多时间周期数据"""
    multi_timeframe = GLOBAL_CONFIG['multi_timeframe_analysis']
    timeframe_data = {}
    
    try:
        config = TRADE_CONFIGS[symbol]
        
        for tf_name, timeframe in multi_timeframe.items():
            try:
                # 根据时间周期调整数据点数量
                if timeframe == '5m':
                    limit = 288  # 24小时
                elif timeframe == '15m':
                    limit = 96   # 24小时
                elif timeframe == '1h':
                    limit = 24   # 24小时
                elif timeframe == '4h':
                    limit = 42   # 7天
                else:
                    limit = 100  # 默认
                
                ohlcv = exchange.fetch_ohlcv(config['symbol'], timeframe, limit=limit)
                df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                df = calculate_technical_indicators(df)
                
                current_data = df.iloc[-1]
                
                timeframe_data[tf_name] = {
                    'timeframe': timeframe,
                    'current_price': current_data['close'],
                    'price_change': ((current_data['close'] - df.iloc[-2]['close']) / df.iloc[-2]['close']) * 100,
                    'volume': current_data['volume'],
                    'rsi_14': current_data.get('rsi_14', 0),
                    'rsi_7': current_data.get('rsi_7', 0),
                    'ema_12': current_data.get('ema_12', 0),
                    'ema_60': current_data.get('ema_60', 0),
                    'ema_288': current_data.get('ema_288', 0),
                    'macd': current_data.get('macd', 0),
                    'macd_signal': current_data.get('macd_signal', 0),
                    'bb_position_20': current_data.get('bb_position_20', 0),
                    'volume_ratio_short': current_data.get('volume_ratio_short', 0),
                    'volatility_20': current_data.get('volatility_20', 0),
                    'momentum_10': current_data.get('momentum_10', 0),
                    'full_data': df
                }
                
            except Exception as e:
                log_futures_warning(f"获取 {symbol} {timeframe} 数据失败: {e}")
                continue
                
        return timeframe_data
        
    except Exception as e:
        log_futures_error(f"获取 {symbol} 多时间周期数据失败: {e}")
        return {}


def get_ohlcv_enhanced(symbol):
    """增强版：获取指定交易对的K线数据并计算技术指标（支持多时间周期）"""
    try:
        config = TRADE_CONFIGS[symbol]
        
        # 获取主要时间周期的K线数据
        ohlcv = exchange.fetch_ohlcv(config['symbol'], GLOBAL_CONFIG['timeframe'],
                                     limit=GLOBAL_CONFIG['data_points'])

        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')

        # 计算技术指标
        df = calculate_technical_indicators(df)

        current_data = df.iloc[-1]
        previous_data = df.iloc[-2]
        
        # 获取多时间周期数据
        multi_tf_data = get_multi_timeframe_data(symbol)

        return {
            'symbol': symbol,
            'price': current_data['close'],
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'high': current_data['high'],
            'low': current_data['low'],
            'volume': current_data['volume'],
            'timeframe': GLOBAL_CONFIG['timeframe'],
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
            'multi_timeframe_data': multi_tf_data,
            'full_data': df
        }
    except Exception as e:
        log_futures_error(f"获取 {symbol} 增强K线数据失败: {e}")
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
            log_futures_error(f"JSON解析失败，原始内容: {json_str}")
            log_futures_error(f"错误详情: {e}")
            return None


def create_fallback_signal(price_data):
    """创建备用交易信号"""
    return {
        "signal": "HOLD",
        "reason": "因技术分析暂时不可用，采取保守策略",
        "stop_loss": price_data['price'] * 0.98,  # -2%
        "take_profit": price_data['price'] * 1.02,  # +2%
        "confidence": "LOW",
        "position_size_percent": 5,  # 默认5%仓位
        "leverage": 5,  # 默认5倍杠杆
        "action": "HOLD",
        "partial_close_percentage": 50,  # 默认50%减仓
        "signal_strength": "WEAK",  # 默认弱信号
        "risk_assessment": "备用信号，风险评估较低",
        "is_fallback": True
    }


def get_current_position(symbol):
    """获取指定交易对的当前持仓情况，包括止损和止盈位置"""
    try:
        config = TRADE_CONFIGS[symbol]
        positions = exchange.fetch_positions([config['symbol']])

        # 标准化配置的交易对符号用于比较
        config_symbol_normalized = f"{symbol}:USDT"

        for pos in positions:

            # 比较标准化的符号
            if pos['symbol'] == config_symbol_normalized:
                # 获取持仓数量
                position_amt = 0
                if 'positionAmt' in pos.get('info', {}):
                    position_amt = float(pos['info']['positionAmt'])
                elif 'contracts' in pos:
                    # 使用 contracts 字段，根据 side 确定方向
                    contracts = float(pos['contracts'])
                    if pos.get('side') == 'short':
                        position_amt = -contracts
                    else:
                        position_amt = contracts

                futures_print(f"{symbol} 调试 - 持仓量: {position_amt}")

                if position_amt != 0:  # 有持仓
                    side = 'long' if position_amt > 0 else 'short'
                    
                    # 获取止损和止盈位置
                    stop_loss = None
                    take_profit = None
                    
                    # 从数据库查询最近的止损和止盈位置
                    try:
                        with db_lock:
                            cursor = db_connection.cursor()
                            cursor.execute('''
                                SELECT stop_loss, take_profit
                                FROM position_records
                                WHERE symbol = ? AND side = ? AND size > 0
                                ORDER BY timestamp DESC
                                LIMIT 1
                            ''', (symbol, side))
                            
                            result = cursor.fetchone()
                            if result:
                                stop_loss, take_profit = result[0], result[1]
                            else:
                                # 如果没有找到记录，尝试从最近的交易信号中获取
                                cursor.execute('''
                                    SELECT stop_loss, take_profit
                                    FROM trading_signals
                                    WHERE symbol = ? AND signal IN ('BUY', 'SELL')
                                    ORDER BY timestamp DESC
                                    LIMIT 1
                                ''', (symbol,))
                                
                                signal_result = cursor.fetchone()
                                if signal_result:
                                    stop_loss, take_profit = signal_result[0], signal_result[1]
                    except Exception as e:
                        log_futures_warning(f"获取 {symbol} 止损止盈位失败: {e}")
                    
                    return {
                        'side': side,
                        'size': abs(position_amt),
                        'entry_price': float(pos.get('entryPrice', 0)),
                        'unrealized_pnl': float(pos.get('unrealizedPnl', 0)),
                        'position_amt': position_amt,
                        'symbol': pos['symbol'],  # 返回实际的symbol用于调试
                        'stop_loss': stop_loss,    # 添加止损位置
                        'take_profit': take_profit # 添加止盈位置
                    }

        futures_print(f"{symbol} 调试 - 未找到有效持仓")
        return None

    except Exception as e:
        log_futures_error(f"获取 {symbol} 持仓失败: {e}")
        import traceback
        traceback.print_exc()
        return None


def get_funding_and_open_interest(symbol):
    """获取指定交易对永续合约的资金费率和未平合约数据"""
    try:
        # 获取资金费率
        funding_rate = exchange.fetch_funding_rate(f'{symbol}:USDT')
        current_funding_rate = funding_rate.get('fundingRate', 0)
        funding_timestamp = funding_rate.get('timestamp', 0)

        futures_print(f"{symbol} 调试 - 当前资金费率: {current_funding_rate}, 时间戳: {funding_timestamp}")
        
        # 获取未平合约数据
        open_interest = exchange.fetch_open_interest(f'{symbol}:USDT')
        current_open_interest = open_interest.get('openInterestAmount', 0)
        oi_timestamp = open_interest.get('timestamp', 0)

        futures_print(f"{symbol} 调试 - 当前未平合约: {current_open_interest}, 时间戳: {oi_timestamp}")
        
        # 获取历史资金费率来计算平均值
        try:
            # 尝试获取多个时间点的资金费率
            funding_history = []
            for i in range(8, 0, -1):  # 获取最近8个时间点的数据
                try:
                    historical_funding = exchange.fetch_funding_rate_history(f'{symbol}:USDT', limit=1, params={'endTime': funding_timestamp - i * 8 * 60 * 60 * 1000})
                    if historical_funding:
                        funding_history.append(historical_funding[0]['fundingRate'])
                except:
                    continue
            
            avg_funding_rate = sum(funding_history) / len(funding_history) if funding_history else current_funding_rate
        except:
            avg_funding_rate = current_funding_rate
        
        # 获取历史未平合约数据来计算平均值
        try:
            # 币安API可能不直接提供历史未平合约，我们使用当前值作为参考
            avg_open_interest = current_open_interest
        except:
            avg_open_interest = current_open_interest
        
        return {
            'current_funding_rate': current_funding_rate,
            'avg_funding_rate': avg_funding_rate,
            'current_open_interest': current_open_interest,
            'avg_open_interest': avg_open_interest,
            'funding_timestamp': funding_timestamp,
            'oi_timestamp': oi_timestamp
        }
        
    except Exception as e:
        log_futures_warning(f"获取 {symbol} 资金费率和未平合约数据失败: {e}")
        # 返回默认值
        return {
            'current_funding_rate': 1.25e-05,
            'avg_funding_rate': 1.25e-05,
            'current_open_interest': 23470.36,
            'avg_open_interest': 23506.57,
            'funding_timestamp': 0,
            'oi_timestamp': 0
        }


def get_all_symbols_data():
    """获取所有启用交易对的数据"""
    all_data = {}
    all_positions = {}
    total_exposure = 0
    total_margin_used = 0
    
    try:
        # 获取账户总余额
        balance = exchange.fetch_balance()
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
    for symbol in enabled_symbols:
        try:
            # 获取价格数据
            price_data = get_ohlcv_enhanced(symbol)
            if price_data:
                all_data[symbol] = price_data
            
            # 获取持仓信息
            position = get_current_position(symbol)
            all_positions[symbol] = position
            
            # 计算总敞口和保证金使用
            if position:
                # 计算持仓价值（USDT）
                position_value = abs(position['position_amt']) * position['entry_price']
                total_exposure += position_value
                
                # 估算保证金使用（假设平均10倍杠杆）
                estimated_margin = position_value / 10
                total_margin_used += estimated_margin
                
        except Exception as e:
            log_futures_warning(f"获取 {symbol} 数据失败: {e}")
            continue
    
    return {
        'symbols_data': all_data,
        'positions': all_positions,
        'account_info': {
            'total_balance': total_balance,
            'free_balance': free_balance,
            'total_exposure': total_exposure,
            'total_margin_used': total_margin_used,
            'margin_usage_percent': (total_margin_used / total_balance * 100) if total_balance > 0 else 0,
            'exposure_to_balance_ratio': (total_exposure / total_balance) if total_balance > 0 else 0
        }
    }


def generate_enhanced_prompt(price_data, symbol, all_symbols_info=None):
    """生成增强的多币种综合分析提示词"""
    config = TRADE_CONFIGS[symbol]
    
    # 计算运行时间
    current_time = datetime.now()
    start_time_str = trading_stats.get('start_time', current_time.strftime('%Y-%m-%d %H:%M:%S'))
    
    # 将字符串转换为datetime对象
    try:
        start_time = datetime.strptime(start_time_str, '%Y-%m-%d %H:%M:%S')
    except (ValueError, TypeError):
        start_time = current_time
    
    minutes_running = int((current_time - start_time).total_seconds() / 60)
    
    # 获取历史数据
    historical_prices = get_historical_price_data(symbol, 20)
    historical_signals = get_historical_signals(symbol, 10)
    
    # 获取当前持仓
    current_pos = get_current_position(symbol)
    
    # 获取资金费率和未平合约数据
    funding_data = get_funding_and_open_interest(symbol)
    
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
            balance = exchange.fetch_balance()
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
            'total_margin_used': 0,
            'margin_usage_percent': 0,
            'exposure_to_balance_ratio': 0
        }
        all_positions = {symbol: current_pos}
        all_data = {symbol: price_data}
    
    # 多时间周期分析
    multi_tf_analysis = ""
    if price_data.get('multi_timeframe_data'):
        multi_tf = price_data['multi_timeframe_data']
        multi_tf_analysis = f"""
多时间周期技术分析:
"""
        for tf_name, tf_data in multi_tf.items():
            if tf_data:
                tf_timeframe = tf_data['timeframe']
                multi_tf_analysis += f"- {tf_timeframe}: 价格${tf_data['current_price']:.2f} ({tf_data['price_change']:+.2f}%), RSI14:{tf_data['rsi_14']:.1f}, EMA12:{tf_data['ema_12']:.2f}, MACD:{tf_data['macd']:.4f}\n"
    
    # 准备价格序列数据
    df = price_data.get('full_data')
    if df is not None and len(df) >= 10:
        # 获取最近10个数据点的价格和指标
        recent_prices = df['close'].tail(10).tolist()
        recent_emas = df['ema_12'].tail(10).tolist() if 'ema_12' in df.columns else [0] * 10
        recent_macd = df['macd'].tail(10).tolist() if 'macd' in df.columns else [0] * 10
        recent_rsi_7 = df['rsi_7'].tail(10).tolist() if 'rsi_7' in df.columns else [0] * 10
        recent_rsi_14 = df['rsi_14'].tail(10).tolist() if 'rsi_14' in df.columns else [0] * 10
        
        # 获取更长周期的数据（4小时）
        try:
            longer_ohlcv = exchange.fetch_ohlcv(config['symbol'], '4h', limit=20)
            longer_df = pd.DataFrame(longer_ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            longer_df = calculate_technical_indicators(longer_df)
            
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
            log_futures_warning(f"获取 {symbol} 长周期数据失败: {e}")
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
    stop_loss_status = "无止损状态"
    take_profit_status = "无止盈状态"
    
    if current_pos:
        position_info = f"{current_pos['side']}仓 {current_pos['size']} {symbol.split('/')[0]}, 入场价: ${current_pos['entry_price']:.2f}, 浮盈: ${current_pos['unrealized_pnl']:.2f}"
        
        # 获取固定的止损位和止盈位
        fixed_stop_loss = current_pos.get('stop_loss')
        fixed_take_profit = current_pos.get('take_profit')
        
        current_price = price_data['price']
        
        # 检查是否接近或达到止损/止盈位置
        if fixed_stop_loss  and fixed_stop_loss > 0 and isinstance(fixed_stop_loss, (int, float)):
            if current_pos['side'] == 'long':
                stop_loss_distance = (current_price - fixed_stop_loss) / fixed_stop_loss * 100
                if current_price <= fixed_stop_loss:
                    stop_loss_status = f"已触发止损 (当前价格 ${current_price:.2f} <= 止损价 ${fixed_stop_loss:.2f})"
                elif stop_loss_distance <= 2:
                    stop_loss_status = f"接近止损 (距离 {stop_loss_distance:.2f}%, 当前价 ${current_price:.2f}, 止损价 ${fixed_stop_loss:.2f})"
                else:
                    stop_loss_status = f"止损安全 (距离 {stop_loss_distance:.2f}%, 当前价 ${current_price:.2f}, 止损价 ${fixed_stop_loss:.2f})"
            else:  # short position
                stop_loss_distance = (fixed_stop_loss - current_price) / fixed_stop_loss * 100
                if current_price >= fixed_stop_loss:
                    stop_loss_status = f"已触发止损 (当前价格 ${current_price:.2f} >= 止损价 ${fixed_stop_loss:.2f})"
                elif stop_loss_distance <= 2:
                    stop_loss_status = f"接近止损 (距离 {stop_loss_distance:.2f}%, 当前价 ${current_price:.2f}, 止损价 ${fixed_stop_loss:.2f})"
                else:
                    stop_loss_status = f"止损安全 (距离 {stop_loss_distance:.2f}%, 当前价 ${current_price:.2f}, 止损价 ${fixed_stop_loss:.2f})"
        
        if fixed_take_profit and fixed_take_profit > 0 and isinstance(fixed_take_profit, (int, float)):
            if current_pos['side'] == 'long':
                take_profit_distance = (fixed_take_profit - current_price) / fixed_take_profit * 100
                if current_price >= fixed_take_profit:
                    take_profit_status = f"已触发止盈 (当前价格 ${current_price:.2f} >= 止盈价 ${fixed_take_profit:.2f})"
                elif take_profit_distance <= 2:
                    take_profit_status = f"接近止盈 (距离 {take_profit_distance:.2f}%, 当前价 ${current_price:.2f}, 止盈价 ${fixed_take_profit:.2f})"
                else:
                    take_profit_status = f"止盈未达 (距离 {take_profit_distance:.2f}%, 当前价 ${current_price:.2f}, 止盈价 ${fixed_take_profit:.2f})"
            else:  # short position
                take_profit_distance = (current_price - fixed_take_profit) / fixed_take_profit * 100
                if current_price <= fixed_take_profit:
                    take_profit_status = f"已触发止盈 (当前价格 ${current_price:.2f} <= 止盈价 ${fixed_take_profit:.2f})"
                elif take_profit_distance <= 2:
                    take_profit_status = f"接近止盈 (距离 {take_profit_distance:.2f}%, 当前价 ${current_price:.2f}, 止盈价 ${fixed_take_profit:.2f})"
                else:
                    take_profit_status = f"止盈未达 (距离 {take_profit_distance:.2f}%, 当前价 ${current_price:.2f}, 止盈价 ${fixed_take_profit:.2f})"
    
    # 构建多币种综合分析提示词
    prompt = f"""你已经开始交易 {minutes_running} 分钟。
当前时间是 {current_time.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} 
您已经被调用 {trading_stats['total_calls']} 次.

=== 多币种综合投资组合分析 ===

当前账户总览:
- 总余额: {account_info['total_balance']:.2f} USDT
- 可用余额: {account_info['free_balance']:.2f} USDT
- 总敞口: {account_info['total_exposure']:.2f} USDT
- 保证金使用: {account_info['total_margin_used']:.2f} USDT ({account_info['margin_usage_percent']:.1f}%)
- 敞口/余额比: {account_info['exposure_to_balance_ratio']:.2f}

当前所有持仓情况:
"""
    
    # 添加所有币种的持仓信息
    for sym, pos in all_positions.items():
        if pos:
            pnl_percent = (pos['unrealized_pnl'] / (pos['size'] * pos['entry_price']) * 100) if pos['entry_price'] > 0 else 0
            prompt += f"- {sym}: {pos['side']}仓 {pos['size']:.6f}, 入场价: ${pos['entry_price']:.2f}, 浮盈: ${pos['unrealized_pnl']:.2f} ({pnl_percent:+.2f}%)\n"
        else:
            prompt += f"- {sym}: 无持仓\n"
    
    prompt += f"""

=== 当前分析币种: {symbol} 详细数据 ===

当前价格: {price_data['price']} USDT
资金费率: {funding_data['current_funding_rate']:.8f}
未平合约: {funding_data['current_open_interest']:.2f}

持仓状态分析:
{position_info}
止损状态: {stop_loss_status}
止盈状态: {take_profit_status}

{multi_tf_analysis}

主要时间周期 ({GLOBAL_CONFIG['timeframe']}) 技术指标:
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
        if len(signal) >= 11:  # 新版本包含所有字段
            prompt += f"{i+1}. {signal[2]} at {signal[1]} - 信心: {signal[6]}, 价格: ${signal[7]:.2f}, 信号强度: {signal[9]}, 减仓比例: {signal[8]}%\n"
        else:  # 旧版本只有基本字段
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
                
                pos_status = f"({other_pos['side']}仓)" if other_pos else "(无持仓)"
                prompt += f"- {sym}: ${other_price:.2f} ({other_change:+.2f}%) RSI:{other_tech.get('rsi', 0):.1f} {pos_status}\n"
    
    prompt += f"""

=== 交易决策指令 ===

基于以上多币种综合分析，为 {symbol} 提供交易决策。

重要风险管理原则:
1. 总敞口不应超过账户余额的300%
2. 单个币种仓位不超过总余额的50%
3. 保证金使用率不超过70%
4. 根据当前整体持仓情况动态调整仓位大小
5. 考虑与其他币种的相关性，避免过度集中风险

防频繁交易重要原则:
1. 趋势持续性优先: 不要因单根K线或短期波动改变整体趋势判断
2. 持仓稳定性: 除非趋势明确强烈反转，否则避免频繁调整仓位
3. 反转确认: 需要至少2-3个技术指标同时确认趋势反转才改变信号，避免噪音影响
4. 成本意识: 减少不必要的仓位调整，每次交易都有成本

仓位和杠杆设置策略:
- 高信心信号 + 低整体敞口: 可使用较大仓位(15-25%)和中等杠杆(5-10x)
- 中等信心信号 + 中等敞口: 使用适中仓位(8-15%)和保守杠杆(3-7x)
- 低信心信号 + 高敞口: 使用小仓位(3-8%)和低杠杆(2-5x)
- 已有同向持仓: 考虑增加仓位或保持现状
- 已有反向持仓: 考虑平仓反转或减仓

智能止损/止盈策略:
当价格接近或已达到止损/止盈位置时，需要根据市场信号强度做出决策:
1. 已触发止损/止盈:
   - 强烈信号(技术指标明确反转): 完全平仓，不犹豫
   - 中等信号(指标有反转迹象但不够明确): 减仓50-70%，保留部分仓位观察
   - 弱信号(指标混乱，方向不明): 减仓30-50%，设置更紧的止损

2. 接近止损/止盈(距离2%以内):
   - 强烈信号(与持仓方向相反): 提前减仓30-50%，避免触发
   - 中等信号(有反转迹象): 减仓20-30%，调整止损位
   - 弱信号(无明显反转): 保持仓位，但提高警惕

3. 减仓幅度由DeepSeek根据以下因素决定:
   - 多时间周期技术指标一致性
   - 成交量和市场情绪
   - 支撑阻力位强度
   - 整体市场波动性
   - 当前持仓盈亏比例

请返回JSON格式的交易决策:
{{
  "signal": "BUY" | "SELL" | "HOLD" | "PARTIAL_CLOSE",
  "reason": "详细分析理由，包括多币种综合考量和止损/止盈策略",
  "stop_loss": 具体止损价格,
  "take_profit": 具体止盈价格,
  "confidence": "HIGH" | "MEDIUM" | "LOW",
  "position_size_percent": 仓位大小(1-100，基于可用余额百分比),
  "leverage": 杠杆倍数(2-20),
  "action": "OPEN" | "CLOSE" | "INCREASE" | "DECREASE" | "PARTIAL_CLOSE" | "HOLD",
  "partial_close_percentage": 减仓比例(10-90，仅在PARTIAL_CLOSE时使用),
  "signal_strength": "STRONG" | "MEDIUM" | "WEAK" (用于描述市场信号强度),
  "risk_assessment": "风险评估说明"
}}
"""
    return prompt


def analyze_with_deepseek(price_data, symbol, all_symbols_info=None):
    """使用DeepSeek进行多币种综合分析并生成交易信号"""
    
    # 更新统计
    trading_stats['total_calls'] += 1
    trading_stats['last_signal_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # 如果没有提供多币种信息，获取一次
    if all_symbols_info is None:
        all_symbols_info = get_all_symbols_data()
    
    # 生成多币种综合分析提示词
    prompt = generate_enhanced_prompt(price_data, symbol, all_symbols_info)

    futures_print(f"{symbol} DeepSeek多币种综合分析提示词生成完毕，准备请求DeepSeek API...")
    futures_print(f"{symbol} 提示词长度: {len(prompt)} 字符")
    futures_print(f"{symbol} 完整提示词:\n{prompt}\n")

    try:
        response = deepseek_client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system",
                 "content": f"你是一个专业的加密货币投资组合分析师，专门进行多币种综合分析。你需要基于整体投资组合的风险状况来制定交易决策，而不仅仅是单个币种的技术分析。请严格按照要求的JSON格式返回交易信号，使用中文回复。特别注意：当价格接近或达到止损/止盈位置时，你需要根据市场信号强度智能决策是完全平仓还是部分减仓，减仓幅度由你根据技术指标和市场情况决定。"},
                {"role": "user", "content": prompt}
            ],
            stream=False,
            temperature=0.1
        )

        # 安全解析JSON
        result = response.choices[0].message.content
        # print(f"{symbol} DeepSeek原始回复: {result}")

        # 提取JSON部分
        start_idx = result.find('{')
        end_idx = result.rfind('}') + 1

        if start_idx != -1 and end_idx != 0:
            json_str = result[start_idx:end_idx]
            signal_data = safe_json_parse(json_str)

            if signal_data is None:
                signal_data = create_fallback_signal(price_data)
        else:
            signal_data = create_fallback_signal(price_data)

        # 验证必需字段
        required_fields = ['signal', 'reason', 'stop_loss', 'take_profit', 'confidence', 'position_size_percent', 'leverage', 'action']
        if not all(field in signal_data for field in required_fields):
            signal_data = create_fallback_signal(price_data)
            # 为备用信号添加默认字段
            signal_data['position_size_percent'] = 10  # 默认10%
            signal_data['leverage'] = 10  # 默认10倍杠杆
            signal_data['action'] = 'HOLD'

        # 添加智能止损/止盈相关字段（如果没有）
        if 'partial_close_percentage' not in signal_data:
            signal_data['partial_close_percentage'] = 50  # 默认50%
        if 'signal_strength' not in signal_data:
            signal_data['signal_strength'] = 'MEDIUM'  # 默认中等信号强度
        if 'risk_assessment' not in signal_data:
            signal_data['risk_assessment'] = "基于当前投资组合状况的风险评估"

        # 保存信号到历史记录
        signal_data['timestamp'] = price_data['timestamp']
        
        # 按交易对分别记录信号历史
        if symbol not in signal_history:
            signal_history[symbol] = []
        
        signal_history[symbol].append(signal_data)
        if len(signal_history[symbol]) > 30:
            signal_history[symbol].pop(0)

        # 保存到数据库
        save_trading_signal(signal_data, price_data, symbol)
        update_trading_stats()

        # 信号统计（按交易对分别统计）
        symbol_signals = signal_history.get(symbol, [])
        signal_count = len([s for s in symbol_signals if s.get('signal') == signal_data['signal']])
        total_signals = len(symbol_signals)
        futures_print(f"{symbol} 信号统计: {signal_data['signal']} (最近{total_signals}次中出现{signal_count}次)")

        # 信号连续性检查（按交易对分别检查）
        if len(symbol_signals) >= 3:
            last_three = [s['signal'] for s in symbol_signals[-3:]]
            if len(set(last_three)) == 1:
                log_futures_warning(f"{symbol} ⚠️ 注意：连续3次{signal_data['signal']}信号")

        # 打印多币种分析结果
        futures_print(f"{symbol} 多币种综合分析结果:")
        futures_print(f"  信号: {signal_data['signal']}")
        futures_print(f"  仓位: {signal_data['position_size_percent']}%")
        futures_print(f"  杠杆: {signal_data['leverage']}x")
        futures_print(f"  风险评估: {signal_data.get('risk_assessment', 'N/A')}")

        return signal_data

    except Exception as e:
        log_futures_error(f"{symbol} DeepSeek多币种分析失败: {e}")
        return create_fallback_signal(price_data)


def analyze_with_deepseek_with_retry(price_data, symbol, all_symbols_info=None, max_retries=2):
    """带重试的DeepSeek多币种分析"""
    for attempt in range(max_retries):
        try:
            signal_data = analyze_with_deepseek(price_data, symbol, all_symbols_info)
            if signal_data and not signal_data.get('is_fallback', False):
                return signal_data

            futures_print(f"{symbol} 第{attempt + 1}次尝试失败，进行重试...")
            time.sleep(1)

        except Exception as e:
            log_futures_error(f"{symbol} 第{attempt + 1}次尝试异常: {e}")
            if attempt == max_retries - 1:
                return create_fallback_signal(price_data)
            time.sleep(1)

    return create_fallback_signal(price_data)


def execute_trade(signal_data, price_data, symbol):
    """执行交易 - 币安版本（增强版，支持智能止损/止盈和部分减仓）"""
    config = TRADE_CONFIGS[symbol]
    current_position = get_current_position(symbol)

    futures_print(f"{symbol} 交易信号: {signal_data['signal']}")
    futures_print(f"{symbol} 信心程度: {signal_data['confidence']}")
    futures_print(f"{symbol} 理由: {signal_data['reason']}")
    # 安全显示止损和止盈值，避免显示-1造成混淆
    stop_loss_display = signal_data['stop_loss'] if signal_data['stop_loss'] and signal_data['stop_loss'] > 0 else "未设置"
    take_profit_display = signal_data['take_profit'] if signal_data['take_profit'] and signal_data['take_profit'] > 0 else "未设置"
    futures_print(f"{symbol} 止损: {stop_loss_display}")
    futures_print(f"{symbol} 止盈: {take_profit_display}")
    futures_print(f"{symbol} 当前持仓: {current_position}")
    
    # 添加部分减仓相关信息的打印
    if signal_data.get('signal') == 'PARTIAL_CLOSE':
        futures_print(f"{symbol} 减仓比例: {signal_data.get('partial_close_percentage', 50)}%")
        futures_print(f"{symbol} 信号强度: {signal_data.get('signal_strength', 'MEDIUM')}")

    # 风险管理：低信心信号不执行
    if signal_data['confidence'] == 'LOW' and not GLOBAL_CONFIG['test_mode']:
        log_futures_warning(f"{symbol} ⚠️ 低信心信号，跳过执行")
        return

    if GLOBAL_CONFIG['test_mode']:
        futures_print(f"{symbol} 测试模式 - 仅模拟交易")
        return

    try:
        # 获取账户余额
        balance = exchange.fetch_balance()
        # 检查余额结构并获取USDT余额
        if 'USDT' in balance:
            usdt_balance = balance['USDT'].get('free', 0)
            if isinstance(usdt_balance, (int, float)):
                # 确保是数字类型
                usdt_balance = float(usdt_balance)
                futures_print(f"{symbol} 可用USDT余额: {usdt_balance:.2f} USDT")
            else:
                log_futures_warning(f"{symbol} 警告 - USDT余额不是数字类型: {usdt_balance}")
                usdt_balance = 0  # 如果不是数字，设为0
        else:
            futures_print(f"{symbol} 未找到USDT余额信息")
            futures_print(f"{symbol} 可用余额类型: {list(balance.keys())}")
            usdt_balance = 0

        # 从信号中获取交易参数
        position_size_percent = signal_data.get('position_size_percent', 10)  # 默认10%
        leverage = signal_data.get('leverage', 10)  # 默认10倍杠杆
        action = signal_data.get('action', 'HOLD')

        # 计算实际交易金额（USDT）
        position_size_usdt = usdt_balance * (position_size_percent / 100)
        
        # 计算需要的保证金
        required_margin = position_size_usdt / leverage

        # 根据动作类型执行不同的交易逻辑
        if action == 'HOLD' or signal_data['signal'] == 'HOLD':
            print(f"{symbol} 建议观望，不执行交易")
            return

        # 处理部分减仓逻辑
        if signal_data['signal'] == 'PARTIAL_CLOSE' or action == 'PARTIAL_CLOSE':
            if not current_position:
                log_futures_warning(f"{symbol} 无持仓，无法执行部分减仓")
                return
                
            # 获取减仓比例
            partial_close_percentage = signal_data.get('partial_close_percentage', 50)
            if partial_close_percentage <= 0 or partial_close_percentage > 100:
                partial_close_percentage = 50  # 默认50%
                
            # 计算减仓数量
            partial_close_amount = current_position['size'] * (partial_close_percentage / 100)
            
            futures_print(f"{symbol} 执行部分减仓: {partial_close_percentage}% ({partial_close_amount:.6f} {config['base_currency']})")
            
            # 执行部分减仓
            if current_position['side'] == 'long':
                # 减少多仓
                exchange.create_market_order(
                    config['symbol'],
                    'sell',
                    partial_close_amount,
                    params={'reduceOnly': True}
                )
                futures_print(f"{symbol} 多仓部分减仓成功")
            else:  # short position
                # 减少空仓
                exchange.create_market_order(
                    config['symbol'],
                    'buy',
                    partial_close_amount,
                    params={'reduceOnly': True}
                )
                futures_print(f"{symbol} 空仓部分减仓成功")
                
            time.sleep(2)
            position = get_current_position(symbol)
            futures_print(f"{symbol} 减仓后持仓: {position}")
            return

        # 计算交易数量（以基础货币为单位）
        if signal_data['signal'] in ['BUY', 'SELL']:
            trade_amount_base = position_size_usdt / price_data['price']
            
            futures_print(f"{symbol} 仓位大小: {position_size_percent}% ({position_size_usdt:.2f} USDT)")
            futures_print(f"{symbol} 杠杆倍数: {leverage}x")
            futures_print(f"{symbol} 计算得出的交易数量({config['base_currency']}): {trade_amount_base:.6f}")
            futures_print(f"{symbol} 需要保证金: {required_margin:.2f} USDT")

            # 检查保证金是否足够
            if required_margin > usdt_balance * 0.8:
                log_futures_warning(f"{symbol} ⚠️ 保证金不足，跳过交易。需要: {required_margin:.2f} USDT, 可用: {usdt_balance:.2f} USDT")
                return

            futures_print(f"{symbol} ✅ 保证金充足，继续执行")

            # 设置杠杆
            exchange.set_leverage(leverage, config['symbol'])
            futures_print(f"{symbol} 设置杠杆倍数: {leverage}x")

            # 执行交易逻辑
            if signal_data['signal'] == 'BUY':
                if current_position and current_position['side'] == 'short':
                    futures_print(f"{symbol} 平空仓并开多仓...")
                    # 平空仓
                    exchange.create_market_order(
                        config['symbol'],
                        'buy',
                        current_position['size'],
                        params={'reduceOnly': True}
                    )
                    time.sleep(1)
                    # 开多仓
                    exchange.create_market_order(
                        config['symbol'],
                        'buy',
                        trade_amount_base
                    )
                elif current_position and current_position['side'] == 'long':
                    if action == 'INCREASE':
                        futures_print(f"{symbol} 增加多仓...")
                        exchange.create_market_order(
                            config['symbol'],
                            'buy',
                            trade_amount_base
                        )
                    else:
                        futures_print(f"{symbol} 已有多头持仓，保持现状")
                else:
                    # 无持仓时开多仓
                    futures_print(f"{symbol} 开多仓...")
                    exchange.create_market_order(
                        config['symbol'],
                        'buy',
                        trade_amount_base
                    )

            elif signal_data['signal'] == 'SELL':
                if current_position and current_position['side'] == 'long':
                    futures_print(f"{symbol} 平多仓并开空仓...")
                    # 平多仓
                    exchange.create_market_order(
                        config['symbol'],
                        'sell',
                        current_position['size'],
                        params={'reduceOnly': True}
                    )
                    time.sleep(1)
                    # 开空仓
                    exchange.create_market_order(
                        config['symbol'],
                        'sell',
                        trade_amount_base
                    )
                elif current_position and current_position['side'] == 'short':
                    if action == 'INCREASE':
                        futures_print(f"{symbol} 增加空仓...")
                        exchange.create_market_order(
                            config['symbol'],
                            'sell',
                            trade_amount_base
                        )
                    else:
                        futures_print(f"{symbol} 已有空头持仓，保持现状")
                else:
                    # 无持仓时开空仓
                    futures_print(f"{symbol} 开空仓...")
                    exchange.create_market_order(
                        config['symbol'],
                        'sell',
                        trade_amount_base
                    )

        futures_print(f"{symbol} 订单执行成功")
        time.sleep(2)
        position = get_current_position(symbol)
        futures_print(f"{symbol} 更新后持仓: {position}")
        
        # 更新持仓记录表中的止损位和止盈位
        if position and signal_data['signal'] in ['BUY', 'SELL']:
            # 确保止损和止盈值有效，避免保存-1到数据库
            stop_loss_to_save = signal_data['stop_loss'] if signal_data['stop_loss'] and signal_data['stop_loss'] > 0 else None
            take_profit_to_save = signal_data['take_profit'] if signal_data['take_profit'] and signal_data['take_profit'] > 0 else None
            save_position_record(position, symbol, stop_loss_to_save, take_profit_to_save)
            
            # 安全显示保存的止损和止盈值
            stop_loss_display = stop_loss_to_save if stop_loss_to_save else "未设置"
            take_profit_display = take_profit_to_save if take_profit_to_save else "未设置"
            futures_print(f"{symbol} 已更新持仓记录表 - 止损: {stop_loss_display}, 止盈: {take_profit_display}")

    except Exception as e:
        log_futures_error(f"{symbol} 订单执行失败: {e}")
        import traceback
        traceback.print_exc()

def trading_bot_for_symbol(symbol, all_symbols_info=None, balance=None, usdt_balance=0):
    """为单个交易对执行交易逻辑（支持多币种综合分析）"""
    config = TRADE_CONFIGS[symbol]
    
    if not config['enabled']:
        futures_print(f"{symbol} 交易对已禁用，跳过")
        return
    
    futures_print(f"\n{'='*20} {symbol} {'='*20}")
    futures_print(f"执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    futures_print(f"{'='*50}")
    
    # 1. 获取增强版K线数据
    price_data = get_ohlcv_enhanced(symbol)
    if not price_data:
        log_futures_warning(f"{symbol} 获取价格数据失败，跳过")
        return
    
    futures_print(f"{symbol} 当前价格: ${price_data['price']:,.2f}")
    futures_print(f"{symbol} 数据周期: {GLOBAL_CONFIG['timeframe']}")
    futures_print(f"{symbol} 价格变化: {price_data['price_change']:+.2f}%")
    
    # 2. 保存价格数据到数据库
    save_price_data(price_data, symbol)
    
    # 3. 获取当前持仓并保存到数据库
    current_position = get_current_position(symbol)
    # 从当前持仓中获取止损和止盈位
    stop_loss = current_position.get('stop_loss') if current_position else None
    take_profit = current_position.get('take_profit') if current_position else None
    save_position_record(current_position, symbol, stop_loss, take_profit)
    
    # 4. 使用DeepSeek多币种综合分析（带重试）
    signal_data = analyze_with_deepseek_with_retry(price_data, symbol, all_symbols_info)
    
    if signal_data.get('is_fallback', False):
        log_futures_warning(f"{symbol} ⚠️ 使用备用交易信号")
    
    # 5. 执行交易
    execute_trade(signal_data, price_data, symbol)
    
    # 6. 更新交易统计
    if signal_data['signal'] in ['BUY', 'SELL', 'PARTIAL_CLOSE']:
        trading_stats['total_trades'] += 1
        # 这里可以添加更复杂的盈亏计算逻辑
        update_trading_stats()


def trading_bot():
    """主交易机器人函数 - 处理所有启用的交易对（多币种综合分析）"""
    futures_print("\n" + "=" * 60)
    futures_print(f"多交易对交易机器人执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    futures_print("=" * 60)
    
    # 获取所有币种的综合数据（一次获取，多次使用）
    futures_print("正在获取所有币种的综合数据...")
    all_symbols_info = get_all_symbols_data()
    
    # 显示账户总览
    account_info = all_symbols_info['account_info']
    futures_print(f"账户总览:")
    futures_print(f"  总余额: {account_info['total_balance']:.2f} USDT")
    futures_print(f"  可用余额: {account_info['free_balance']:.2f} USDT")
    futures_print(f"  总敞口: {account_info['total_exposure']:.2f} USDT")
    futures_print(f"  保证金使用: {account_info['total_margin_used']:.2f} USDT ({account_info['margin_usage_percent']:.1f}%)")
    futures_print(f"  敞口/余额比: {account_info['exposure_to_balance_ratio']:.2f}")
    
    # 遍历所有启用的交易对
    for symbol in enabled_symbols:
        trading_bot_for_symbol(symbol, all_symbols_info)
        # 在处理不同交易对之间添加短暂延迟，避免API限制
        time.sleep(1)


def futures_main():
    """主函数"""
    futures_print("多交易对币安自动交易机器人启动成功！")
    futures_print(f"启用的交易对: {', '.join(enabled_symbols)}")
    futures_print("融合技术指标策略 + 币安实盘接口 + SQLite数据库存储")

    if GLOBAL_CONFIG['test_mode']:
        futures_print("当前为模拟模式，不会真实下单")
    else:
        futures_print("实盘交易模式，请谨慎操作！")

    futures_print(f"交易周期: {GLOBAL_CONFIG['timeframe']}")
    futures_print("已启用完整技术指标分析、持仓跟踪功能和历史数据存储")

    # 初始化数据库
    if not init_database():
        log_futures_error("数据库初始化失败，程序退出")
        return
    
    # 加载历史统计数据
    load_trading_stats()
    
    # 设置开始时间（如果是新启动）
    if trading_stats['start_time'] is None:
        trading_stats['start_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        update_trading_stats()
        futures_print(f"设置新的启动时间: {trading_stats['start_time']}")
    else:
        futures_print(f"使用历史启动时间: {trading_stats['start_time']}")

    # 根据时间周期设置执行频率
    if GLOBAL_CONFIG['timeframe'] == '1h':
        schedule.every().hour.at(":01").do(trading_bot)
        futures_print("执行频率: 每小时一次")
    elif GLOBAL_CONFIG['timeframe'] == '15m':
        schedule.every(15).minutes.do(trading_bot)
        futures_print("执行频率: 每15分钟一次")
    elif GLOBAL_CONFIG['timeframe'] == '5m':
        schedule.every(5).minutes.do(trading_bot)
        futures_print("执行频率: 每5分钟一次")
    else:
        schedule.every().hour.at(":01").do(trading_bot)
        futures_print("执行频率: 每小时一次")

    # 立即执行一次
    trading_bot()

    # 循环执行
    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        futures_print("\n程序被用户中断")
        # 关闭数据库连接
        if db_connection:
            db_connection.close()
            futures_print("数据库连接已关闭")
    except Exception as e:
        log_futures_error(f"程序运行异常: {e}")
        # 关闭数据库连接
        if db_connection:
            db_connection.close()
            futures_print("数据库连接已关闭")


if __name__ == "__main__":
    main()