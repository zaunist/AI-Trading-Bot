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

load_dotenv()

# 初始化DeepSeek客户端
deepseek_client = OpenAI(
    api_key=os.getenv('DEEPSEEK_API_KEY'),
    base_url="https://api.deepseek.com"
)

exchange = ccxt.binance({
    'options': {'defaultType': 'future'},
    'apiKey': os.getenv('BINANCE_API_KEY'),
    'secret': os.getenv('BINANCE_SECRET'),
})

# 交易参数配置 - 结合两个版本的优点
TRADE_CONFIG = {
    'symbol': 'BTC/USDT',  # 币安的合约符号格式
    'amount': 0.001,  # 交易数量 (BTC)
    'leverage': 10,  # 杠杆倍数
    'timeframe': "5m",  # 使用5分钟K线
    'test_mode': os.getenv('TEST_MODE'),  # 测试模式
    'data_points': 288,  # 24小时数据（288根5分钟K线）
    'analysis_periods': {
        'short_term': 60,  # 短期均线
        'medium_term': 150,  # 中期均线
        'long_term': 288  # 长期趋势
    }
}

signal_history = []
position = None

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
            
            # 创建价格数据表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS price_data (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            
            # 创建交易信号表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS trading_signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            
            # 创建持仓记录表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS position_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    side TEXT,
                    size REAL,
                    entry_price REAL,
                    unrealized_pnl REAL,
                    position_amt REAL,
                    stop_loss REAL,
                    confidence REAL
                )
            ''')
            
            # 创建交易统计表
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
            
            # 创建技术指标历史表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS technical_indicators (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            print("数据库初始化成功")
            return True
            
    except Exception as e:
        print(f"数据库初始化失败: {e}")
        return False


def save_price_data(price_data):
    """保存价格数据到数据库"""
    global db_connection
    try:
        with db_lock:
            cursor = db_connection.cursor()
            tech = price_data.get('technical_data', {})
            
            cursor.execute('''
                INSERT INTO price_data
                (timestamp, price, high, low, volume, timeframe, price_change,
                 sma_5, sma_20, sma_50, rsi, macd, macd_signal, macd_histogram,
                 bb_upper, bb_lower, bb_position, volume_ratio)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
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
        print(f"保存价格数据失败: {e}")


def save_trading_signal(signal_data, price_data):
    """保存交易信号到数据库"""
    global db_connection
    try:
        with db_lock:
            cursor = db_connection.cursor()
            
            cursor.execute('''
                INSERT INTO trading_signals
                (timestamp, signal, reason, stop_loss, take_profit, confidence, price, is_fallback)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                signal_data.get('timestamp', datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
                signal_data['signal'],
                signal_data['reason'],
                signal_data.get('stop_loss'),
                signal_data.get('take_profit'),
                signal_data.get('confidence'),
                price_data['price'],
                1 if signal_data.get('is_fallback', False) else 0
            ))
            
            db_connection.commit()
            
    except Exception as e:
        print(f"保存交易信号失败: {e}")


def save_position_record(position_data):
    """保存持仓记录到数据库"""
    global db_connection
    try:
        with db_lock:
            cursor = db_connection.cursor()
            
            if position_data:
                cursor.execute('''
                    INSERT INTO position_records
                    (timestamp, side, size, entry_price, unrealized_pnl, position_amt)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    position_data['side'],
                    position_data['size'],
                    position_data['entry_price'],
                    position_data.get('unrealized_pnl', 0),
                    position_data.get('position_amt', 0)
                ))
            else:
                cursor.execute('''
                    INSERT INTO position_records
                    (timestamp, side, size, entry_price, unrealized_pnl, position_amt)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    None, 0, 0, 0, 0
                ))
            
            db_connection.commit()
            
    except Exception as e:
        print(f"保存持仓记录失败: {e}")


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
        print(f"更新交易统计失败: {e}")


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
                print(f"已加载历史统计数据: 启动时间 {result[1]}, 总调用次数 {result[2]}")
            else:
                print("未找到历史统计数据，使用默认值")
            
    except Exception as e:
        print(f"加载交易统计失败: {e}")


def get_historical_price_data(limit=100):
    """获取历史价格数据"""
    global db_connection
    try:
        with db_lock:
            cursor = db_connection.cursor()
            cursor.execute('''
                SELECT * FROM price_data
                ORDER BY timestamp DESC
                LIMIT ?
            ''', (limit,))
            
            results = cursor.fetchall()
            return results
            
    except Exception as e:
        print(f"获取历史价格数据失败: {e}")
        return []


def get_historical_signals(limit=30):
    """获取历史交易信号"""
    global db_connection
    try:
        with db_lock:
            cursor = db_connection.cursor()
            cursor.execute('''
                SELECT timestamp, signal, reason, stop_loss, take_profit, confidence, price, is_fallback
                FROM trading_signals
                ORDER BY timestamp DESC
                LIMIT ?
            ''', (limit,))
            
            results = cursor.fetchall()
            return results
            
    except Exception as e:
        print(f"获取历史交易信号失败: {e}")
        return []


def setup_exchange():
    """设置交易所参数"""
    try:
        # 设置杠杆
        exchange.set_leverage(TRADE_CONFIG['leverage'], TRADE_CONFIG['symbol'])
        print(f"设置杠杆倍数: {TRADE_CONFIG['leverage']}x")

        # 获取余额
        balance = exchange.fetch_balance()
        usdt_balance = balance['USDT']['free']
        print(f"当前USDT余额: {usdt_balance:.2f}")

        return True
    except Exception as e:
        print(f"交易所设置失败: {e}")
        return False


def calculate_technical_indicators(df):
    """计算技术指标 - 来自第一个策略"""
    try:
        # 移动平均线
        df['sma_5'] = df['close'].rolling(window=5, min_periods=1).mean()
        df['sma_20'] = df['close'].rolling(window=20, min_periods=1).mean()
        df['sma_50'] = df['close'].rolling(window=50, min_periods=1).mean()

        # 指数移动平均线
        df['ema_12'] = df['close'].ewm(span=12).mean()
        df['ema_26'] = df['close'].ewm(span=26).mean()
        df['macd'] = df['ema_12'] - df['ema_26']
        df['macd_signal'] = df['macd'].ewm(span=9).mean()
        df['macd_histogram'] = df['macd'] - df['macd_signal']

        # 相对强弱指数 (RSI)
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        df['rsi'] = 100 - (100 / (1 + rs))

        # 布林带
        df['bb_middle'] = df['close'].rolling(20).mean()
        bb_std = df['close'].rolling(20).std()
        df['bb_upper'] = df['bb_middle'] + (bb_std * 2)
        df['bb_lower'] = df['bb_middle'] - (bb_std * 2)
        df['bb_position'] = (df['close'] - df['bb_lower']) / (df['bb_upper'] - df['bb_lower'])

        # 成交量均线
        df['volume_ma'] = df['volume'].rolling(20).mean()
        df['volume_ratio'] = df['volume'] / df['volume_ma']

        # 支撑阻力位
        df['resistance'] = df['high'].rolling(20).max()
        df['support'] = df['low'].rolling(20).min()

        # 填充NaN值
        df = df.bfill().ffill()

        return df
    except Exception as e:
        print(f"技术指标计算失败: {e}")
        return df


def get_support_resistance_levels(df, lookback=20):
    """计算支撑阻力位"""
    try:
        recent_high = df['high'].tail(lookback).max()
        recent_low = df['low'].tail(lookback).min()
        current_price = df['close'].iloc[-1]

        resistance_level = recent_high
        support_level = recent_low

        # 动态支撑阻力（基于布林带）
        bb_upper = df['bb_upper'].iloc[-1]
        bb_lower = df['bb_lower'].iloc[-1]

        return {
            'static_resistance': resistance_level,
            'static_support': support_level,
            'dynamic_resistance': bb_upper,
            'dynamic_support': bb_lower,
            'price_vs_resistance': ((resistance_level - current_price) / current_price) * 100,
            'price_vs_support': ((current_price - support_level) / support_level) * 100
        }
    except Exception as e:
        print(f"支撑阻力计算失败: {e}")
        return {}

def get_btc_ohlcv_enhanced():
    """增强版：获取BTC K线数据并计算技术指标"""
    try:
        # 获取K线数据
        ohlcv = exchange.fetch_ohlcv(TRADE_CONFIG['symbol'], TRADE_CONFIG['timeframe'],
                                     limit=TRADE_CONFIG['data_points'])

        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')

        # 计算技术指标
        df = calculate_technical_indicators(df)

        current_data = df.iloc[-1]
        previous_data = df.iloc[-2]

        # 获取技术分析数据
        levels_analysis = get_support_resistance_levels(df)

        return {
            'price': current_data['close'],
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'high': current_data['high'],
            'low': current_data['low'],
            'volume': current_data['volume'],
            'timeframe': TRADE_CONFIG['timeframe'],
            'price_change': ((current_data['close'] - previous_data['close']) / previous_data['close']) * 100,
            'kline_data': df[['timestamp', 'open', 'high', 'low', 'close', 'volume']].tail(10).to_dict('records'),
            'technical_data': {
                'sma_5': current_data.get('sma_5', 0),
                'sma_20': current_data.get('sma_20', 0),
                'sma_50': current_data.get('sma_50', 0),
                'rsi': current_data.get('rsi', 0),
                'macd': current_data.get('macd', 0),
                'macd_signal': current_data.get('macd_signal', 0),
                'macd_histogram': current_data.get('macd_histogram', 0),
                'bb_upper': current_data.get('bb_upper', 0),
                'bb_lower': current_data.get('bb_lower', 0),
                'bb_position': current_data.get('bb_position', 0),
                'volume_ratio': current_data.get('volume_ratio', 0)
            },
            'levels_analysis': levels_analysis,
            'full_data': df
        }
    except Exception as e:
        print(f"获取增强K线数据失败: {e}")
        return None


def get_btc_ohlcv():
    """获取BTC/USDT的K线数据（1小时或15分钟）- 保持向后兼容"""
    return get_btc_ohlcv_enhanced()


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
            print(f"JSON解析失败，原始内容: {json_str}")
            print(f"错误详情: {e}")
            return None


def create_fallback_signal(price_data):
    """创建备用交易信号"""
    return {
        "signal": "HOLD",
        "reason": "因技术分析暂时不可用，采取保守策略",
        "stop_loss": price_data['price'] * 0.98,  # -2%
        "take_profit": price_data['price'] * 1.02,  # +2%
        "confidence": "LOW",
        "is_fallback": True
    }


def get_current_position():
    """获取当前持仓情况"""
    try:
        positions = exchange.fetch_positions([TRADE_CONFIG['symbol']])

        # 标准化配置的交易对符号用于比较
        config_symbol_normalized = 'BTC/USDT:USDT'

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

                print(f"调试 - 持仓量: {position_amt}")

                if position_amt != 0:  # 有持仓
                    side = 'long' if position_amt > 0 else 'short'
                    return {
                        'side': side,
                        'size': abs(position_amt),
                        'entry_price': float(pos.get('entryPrice', 0)),
                        'unrealized_pnl': float(pos.get('unrealizedPnl', 0)),
                        'position_amt': position_amt,
                        'symbol': pos['symbol']  # 返回实际的symbol用于调试
                    }

        print("调试 - 未找到有效持仓")
        return None

    except Exception as e:
        print(f"获取持仓失败: {e}")
        import traceback
        traceback.print_exc()
        return None


def get_btc_funding_and_open_interest():
    """获取BTC永续合约的资金费率和未平合约数据"""
    try:
        # 获取资金费率
        funding_rate = exchange.fetch_funding_rate('BTC/USDT:USDT')
        current_funding_rate = funding_rate.get('fundingRate', 0)
        funding_timestamp = funding_rate.get('timestamp', 0)

        print(f"调试 - 当前资金费率: {current_funding_rate}, 时间戳: {funding_timestamp}")
        
        # 获取未平合约数据
        open_interest = exchange.fetch_open_interest('BTC/USDT:USDT')
        current_open_interest = open_interest.get('openInterestAmount', 0)
        oi_timestamp = open_interest.get('timestamp', 0)

        print(f"调试 - 当前未平合约: {current_open_interest}, 时间戳: {oi_timestamp}")
        
        # 获取历史资金费率来计算平均值
        try:
            # 尝试获取多个时间点的资金费率
            funding_history = []
            for i in range(8, 0, -1):  # 获取最近8个时间点的数据
                try:
                    historical_funding = exchange.fetch_funding_rate_history('BTC/USDT:USDT', limit=1, params={'endTime': funding_timestamp - i * 8 * 60 * 60 * 1000})
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
        print(f"获取资金费率和未平合约数据失败: {e}")
        # 返回默认值
        return {
            'current_funding_rate': 1.25e-05,
            'avg_funding_rate': 1.25e-05,
            'current_open_interest': 23470.36,
            'avg_open_interest': 23506.57,
            'funding_timestamp': 0,
            'oi_timestamp': 0
        }


def generate_enhanced_prompt(price_data):
    """生成类似示例的详细提示词"""
    
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
    historical_prices = get_historical_price_data(20)
    historical_signals = get_historical_signals(10)
    
    # 获取当前持仓
    current_pos = get_current_position()
    
    # 获取资金费率和未平合约数据
    funding_data = get_btc_funding_and_open_interest()
    
    # 获取技术指标数据
    tech = price_data.get('technical_data', {})
    levels = price_data.get('levels_analysis', {})
    
    # 准备价格序列数据
    df = price_data.get('full_data')
    if df is not None and len(df) >= 10:
        # 获取最近10个数据点的价格和指标
        recent_prices = df['close'].tail(10).tolist()
        recent_emas = df['ema_20'].tail(10).tolist() if 'ema_20' in df.columns else [0] * 10
        recent_macd = df['macd'].tail(10).tolist() if 'macd' in df.columns else [0] * 10
        recent_rsi_7 = df['rsi'].tail(10).tolist() if 'rsi' in df.columns else [0] * 10
        
        # 计算14周期RSI（如果不存在）
        if 'rsi_14' not in df.columns:
            delta = df['close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
            rs = gain / loss
            df['rsi_14'] = 100 - (100 / (1 + rs))
        
        recent_rsi_14 = df['rsi_14'].tail(10).tolist()
        
        # 获取更长周期的数据（4小时）
        try:
            longer_ohlcv = exchange.fetch_ohlcv(TRADE_CONFIG['symbol'], '4h', limit=20)
            longer_df = pd.DataFrame(longer_ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            longer_df = calculate_technical_indicators(longer_df)
            
            longer_ema_20 = longer_df['ema_20'].iloc[-1] if 'ema_20' in longer_df.columns else 0
            longer_ema_50 = longer_df['ema_50'].iloc[-1] if 'ema_50' in longer_df.columns else 0
            
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
            print(f"获取长周期数据失败: {e}")
            longer_ema_20 = 0
            longer_ema_50 = 0
            atr_3 = 0
            atr_14 = 0
            current_volume = 0
            avg_volume = 0
            sharpe_ratio = 0
    else:
        recent_prices = [price_data['price']] * 10
        recent_emas = [tech.get('sma_20', 0)] * 10
        recent_macd = [tech.get('macd', 0)] * 10
        recent_rsi_7 = [tech.get('rsi', 0)] * 10
        recent_rsi_14 = [tech.get('rsi', 0)] * 10
        longer_ema_20 = tech.get('sma_20', 0)
        longer_ema_50 = tech.get('sma_50', 0)
        atr_3 = 0
        atr_14 = 0
        current_volume = price_data.get('volume', 0)
        avg_volume = current_volume
        sharpe_ratio = 0
    
    # 获取持仓信息
    position_info = "无持仓"
    if current_pos:
        position_info = f"{current_pos['side']}仓 {current_pos['size']} BTC, 入场价: ${current_pos['entry_price']:.2f}, 浮盈: ${current_pos['unrealized_pnl']:.2f}"
    
    # 获取账户信息
    try:
        balance = exchange.fetch_balance()
        usdt_balance = balance['USDT']['free']
        total_balance = balance['USDT']['total']
    except:
        usdt_balance = 0
        total_balance = 0
    
    # 构建详细提示词
    prompt = f"""It has been {minutes_running} minutes since you started trading. The current time is {current_time.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} and you've been invoked {trading_stats['total_calls']} times. Below, we are providing you with a variety of state data, price data, and predictive signals so you can discover alpha. Below that is your current account information, value, performance, positions, etc.
ALL OF THE PRICE OR SIGNAL DATA BELOW IS ORDERED: OLDEST → NEWEST
Timeframes note: Unless stated otherwise in a section title, intraday series are provided at {TRADE_CONFIG['timeframe']} intervals. If a coin uses a different interval, it is explicitly stated in that coin's section.

CURRENT MARKET STATE FOR ALL COINS
ALL BTC DATA
current_price = {price_data['price']}, current_ema20 = {tech.get('sma_20', 0)}, current_macd = {tech.get('macd', 0)}, current_rsi (7 period) = {tech.get('rsi', 0)}
In addition, here is the latest BTC open interest and funding rate for perps (the instrument you are trading):
Open Interest: Latest: {funding_data['current_open_interest']:.2f}  Average: {funding_data['avg_open_interest']:.2f}
Funding Rate: {funding_data['current_funding_rate']:.8f}
Intraday series (by minute, oldest → latest):
Mid prices: {[f"{p:.1f}" for p in recent_prices]}
EMA indicators (20-period): {[f"{e:.3f}" for e in recent_emas]}
MACD indicators: {[f"{m:.3f}" for m in recent_macd]}
RSI indicators (7-Period): {[f"{r:.3f}" for r in recent_rsi_7]}
RSI indicators (14-Period): {[f"{r:.3f}" for r in recent_rsi_14]}
Longer-term context (4-hour timeframe):
20-Period EMA: {longer_ema_20:.3f} vs. 50-Period EMA: {longer_ema_50:.3f}
3-Period ATR: {atr_3:.3f} vs. 14-Period ATR: {atr_14:.3f}
Current Volume: {current_volume:.3f} vs. Average Volume: {avg_volume:.3f}
Sharpe Ratio: {sharpe_ratio:.3f}

CURRENT ACCOUNT AND POSITION STATUS
Account Balance: {usdt_balance:.2f} USDT (Free) / {total_balance:.2f} USDT (Total)
Current Position: {position_info}
Leverage: {TRADE_CONFIG['leverage']}x
Trade Amount: {TRADE_CONFIG['amount']} BTC

RECENT TRADING HISTORY
Last 5 Signals:
"""
    
    # 添加最近信号历史
    for i, signal in enumerate(historical_signals[:5]):
        prompt += f"{i+1}. {signal[2]} at {signal[1]} - Confidence: {signal[6]}, Price: ${signal[7]:.2f}\n"
    
    prompt += f"""
TECHNICAL ANALYSIS SUMMARY
Support Levels: {levels.get('static_support', 0):.2f} (Static) / {levels.get('dynamic_support', 0):.2f} (Dynamic)
Resistance Levels: {levels.get('static_resistance', 0):.2f} (Static) / {levels.get('dynamic_resistance', 0):.2f} (Dynamic)
Bollinger Band Position: {tech.get('bb_position', 0):.2%}
Volume Ratio: {tech.get('volume_ratio', 0):.2f}

TRADING INSTRUCTIONS
Based on the comprehensive market data above, provide a trading decision for BTC/USDT {TRADE_CONFIG['timeframe']} timeframe.

IMPORTANT: Respond with a valid JSON object containing:
- signal: "BUY", "SELL", or "HOLD"
- reason: Your detailed analysis reasoning
- stop_loss: Specific price level for stop loss
- take_profit: Specific price level for take profit
- confidence: "HIGH", "MEDIUM", or "LOW"

Example format:
{{"signal": "BUY", "reason": "Strong bullish momentum with RSI oversold", "stop_loss": 108000, "take_profit": 112000, "confidence": "HIGH"}}
"""
    
    return prompt


def analyze_with_deepseek(price_data):
    """使用DeepSeek分析市场并生成交易信号（增强版）"""
    
    # 更新统计
    trading_stats['total_calls'] += 1
    trading_stats['last_signal_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # 生成增强提示词
    prompt = generate_enhanced_prompt(price_data)

    try:
        response = deepseek_client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system",
                 "content": "You are a professional cryptocurrency trading analyst specializing in BTC/USDT futures trading. Analyze the provided market data comprehensively and provide trading signals in the exact JSON format requested. Response with Chinese."},
                {"role": "user", "content": prompt}
            ],
            stream=False,
            temperature=0.1
        )

        # 安全解析JSON
        result = response.choices[0].message.content
        print(f"DeepSeek原始回复: {result}")

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
        required_fields = ['signal', 'reason', 'stop_loss', 'take_profit', 'confidence']
        if not all(field in signal_data for field in required_fields):
            signal_data = create_fallback_signal(price_data)

        # 保存信号到历史记录
        signal_data['timestamp'] = price_data['timestamp']
        signal_history.append(signal_data)
        if len(signal_history) > 30:
            signal_history.pop(0)

        # 保存到数据库
        save_trading_signal(signal_data, price_data)
        update_trading_stats()

        # 信号统计
        signal_count = len([s for s in signal_history if s.get('signal') == signal_data['signal']])
        total_signals = len(signal_history)
        print(f"信号统计: {signal_data['signal']} (最近{total_signals}次中出现{signal_count}次)")

        # 信号连续性检查
        if len(signal_history) >= 3:
            last_three = [s['signal'] for s in signal_history[-3:]]
            if len(set(last_three)) == 1:
                print(f"⚠️ 注意：连续3次{signal_data['signal']}信号")

        return signal_data

    except Exception as e:
        print(f"DeepSeek分析失败: {e}")
        return create_fallback_signal(price_data)


def analyze_with_deepseek_with_retry(price_data, max_retries=2):
    """带重试的DeepSeek分析"""
    for attempt in range(max_retries):
        try:
            signal_data = analyze_with_deepseek(price_data)
            if signal_data and not signal_data.get('is_fallback', False):
                return signal_data

            print(f"第{attempt + 1}次尝试失败，进行重试...")
            time.sleep(1)

        except Exception as e:
            print(f"第{attempt + 1}次尝试异常: {e}")
            if attempt == max_retries - 1:
                return create_fallback_signal(price_data)
            time.sleep(1)

    return create_fallback_signal(price_data)


def execute_trade(signal_data, price_data):
    """执行交易 - 币安版本（增强版）"""
    global position

    current_position = get_current_position()

    print(f"交易信号: {signal_data['signal']}")
    print(f"信心程度: {signal_data['confidence']}")
    print(f"理由: {signal_data['reason']}")
    print(f"止损: ${signal_data['stop_loss']:,.2f}")
    print(f"止盈: ${signal_data['take_profit']:,.2f}")
    print(f"当前持仓: {current_position}")

    # 风险管理：低信心信号不执行
    if signal_data['confidence'] == 'LOW' and not TRADE_CONFIG['test_mode']:
        print("⚠️ 低信心信号，跳过执行")
        return

    if TRADE_CONFIG['test_mode']:
        print("测试模式 - 仅模拟交易")
        return

    try:
        # 获取账户余额
        balance = exchange.fetch_balance()
        usdt_balance = balance['USDT']['free']

        # 智能保证金检查
        required_margin = 0

        if signal_data['signal'] == 'BUY':
            if current_position and current_position['side'] == 'short':
                # 平空仓 + 开多仓：需要额外保证金
                required_margin = price_data['price'] * TRADE_CONFIG['amount'] / TRADE_CONFIG['leverage']
                operation_type = "平空开多"
            elif not current_position:
                # 开多仓：需要保证金
                required_margin = price_data['price'] * TRADE_CONFIG['amount'] / TRADE_CONFIG['leverage']
                operation_type = "开多仓"
            else:
                # 已持有多仓：不需要额外保证金
                required_margin = 0
                operation_type = "保持多仓"

        elif signal_data['signal'] == 'SELL':
            if current_position and current_position['side'] == 'long':
                # 平多仓 + 开空仓：需要额外保证金
                required_margin = price_data['price'] * TRADE_CONFIG['amount'] / TRADE_CONFIG['leverage']
                operation_type = "平多开空"
            elif not current_position:
                # 开空仓：需要保证金
                required_margin = price_data['price'] * TRADE_CONFIG['amount'] / TRADE_CONFIG['leverage']
                operation_type = "开空仓"
            else:
                # 已持有空仓：不需要额外保证金
                required_margin = 0
                operation_type = "保持空仓"

        elif signal_data['signal'] == 'HOLD':
            print("建议观望，不执行交易")
            return

        print(f"操作类型: {operation_type}, 需要保证金: {required_margin:.2f} USDT")

        # 只有在需要额外保证金时才检查
        if required_margin > 0:
            if required_margin > usdt_balance * 0.8:
                print(f"⚠️ 保证金不足，跳过交易。需要: {required_margin:.2f} USDT, 可用: {usdt_balance:.2f} USDT")
                return
        else:
            print("✅ 无需额外保证金，继续执行")

        # 执行交易逻辑
        if signal_data['signal'] == 'BUY':
            if current_position and current_position['side'] == 'short':
                print("平空仓并开多仓...")
                # 平空仓
                exchange.create_market_order(
                    TRADE_CONFIG['symbol'],
                    'buy',
                    current_position['size'],
                    params={'reduceOnly': True}
                )
                time.sleep(1)
                # 开多仓
                exchange.create_market_order(
                    TRADE_CONFIG['symbol'],
                    'buy',
                    TRADE_CONFIG['amount']
                )
            elif current_position and current_position['side'] == 'long':
                print("已有多头持仓，保持现状")
            else:
                # 无持仓时开多仓
                print("开多仓...")
                exchange.create_market_order(
                    TRADE_CONFIG['symbol'],
                    'buy',
                    TRADE_CONFIG['amount']
                )

        elif signal_data['signal'] == 'SELL':
            if current_position and current_position['side'] == 'long':
                print("平多仓并开空仓...")
                # 平多仓
                exchange.create_market_order(
                    TRADE_CONFIG['symbol'],
                    'sell',
                    current_position['size'],
                    params={'reduceOnly': True}
                )
                time.sleep(1)
                # 开空仓
                exchange.create_market_order(
                    TRADE_CONFIG['symbol'],
                    'sell',
                    TRADE_CONFIG['amount']
                )
            elif current_position and current_position['side'] == 'short':
                print("已有空头持仓，保持现状")
            else:
                # 无持仓时开空仓
                print("开空仓...")
                exchange.create_market_order(
                    TRADE_CONFIG['symbol'],
                    'sell',
                    TRADE_CONFIG['amount']
                )

        print("订单执行成功")
        time.sleep(2)
        position = get_current_position()
        print(f"更新后持仓: {position}")

    except Exception as e:
        print(f"订单执行失败: {e}")
        import traceback
        traceback.print_exc()

def trading_bot():
    """主交易机器人函数"""
    print("\n" + "=" * 60)
    print(f"执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # 1. 获取增强版K线数据
    price_data = get_btc_ohlcv_enhanced()
    if not price_data:
        return

    print(f"BTC当前价格: ${price_data['price']:,.2f}")
    print(f"数据周期: {TRADE_CONFIG['timeframe']}")
    print(f"价格变化: {price_data['price_change']:+.2f}%")

    # 2. 保存价格数据到数据库
    save_price_data(price_data)

    # 3. 获取当前持仓并保存到数据库
    current_position = get_current_position()
    save_position_record(current_position)

    # 4. 使用DeepSeek分析（带重试）
    signal_data = analyze_with_deepseek_with_retry(price_data)

    if signal_data.get('is_fallback', False):
        print("⚠️ 使用备用交易信号")

    # 5. 执行交易
    execute_trade(signal_data, price_data)
    
    # 6. 更新交易统计
    if signal_data['signal'] in ['BUY', 'SELL']:
        trading_stats['total_trades'] += 1
        # 这里可以添加更复杂的盈亏计算逻辑
        update_trading_stats()


def main():
    """主函数"""
    print("BTC/USDT 币安自动交易机器人启动成功！")
    print("融合技术指标策略 + 币安实盘接口 + SQLite数据库存储")

    if TRADE_CONFIG['test_mode']:
        print("当前为模拟模式，不会真实下单")
    else:
        print("实盘交易模式，请谨慎操作！")

    print(f"交易周期: {TRADE_CONFIG['timeframe']}")
    print("已启用完整技术指标分析、持仓跟踪功能和历史数据存储")

    # 初始化数据库
    if not init_database():
        print("数据库初始化失败，程序退出")
        return
    
    # 加载历史统计数据
    load_trading_stats()
    
    # 设置开始时间（如果是新启动）
    if trading_stats['start_time'] is None:
        trading_stats['start_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        update_trading_stats()
        print(f"设置新的启动时间: {trading_stats['start_time']}")
    else:
        print(f"使用历史启动时间: {trading_stats['start_time']}")

    # 设置交易所
    if not setup_exchange():
        print("交易所初始化失败，程序退出")
        return

    # 根据时间周期设置执行频率
    if TRADE_CONFIG['timeframe'] == '1h':
        schedule.every().hour.at(":01").do(trading_bot)
        print("执行频率: 每小时一次")
    elif TRADE_CONFIG['timeframe'] == '15m':
        schedule.every(15).minutes.do(trading_bot)
        print("执行频率: 每15分钟一次")
    elif TRADE_CONFIG['timeframe'] == '5m':
        schedule.every(5).minutes.do(trading_bot)
        print("执行频率: 每5分钟一次")
    else:
        schedule.every().hour.at(":01").do(trading_bot)
        print("执行频率: 每小时一次")

    # 立即执行一次
    trading_bot()

    # 循环执行
    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n程序被用户中断")
        # 关闭数据库连接
        if db_connection:
            db_connection.close()
            print("数据库连接已关闭")
    except Exception as e:
        print(f"程序运行异常: {e}")
        # 关闭数据库连接
        if db_connection:
            db_connection.close()
            print("数据库连接已关闭")


if __name__ == "__main__":
    main()