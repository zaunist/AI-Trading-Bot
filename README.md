# AI-Trading-Bot

支持现货和合约交易的自动化交易系统，基于技术分析和AI决策。

## 功能特性

- **双模式交易**: 同时支持现货和合约交易
- **多币种支持**: 可同时交易BTC/USDT、ETH/USDT、SOL/USDT、BNB/USDT等多个交易对
- **智能分析**: 集成DeepSeek AI进行多币种综合分析
- **技术指标**: 包含RSI、MACD、布林带、EMA等多种技术指标
- **风险管理**: 多层次风险控制和资金管理
- **数据库存储**: SQLite数据库存储历史数据和交易记录
- **Docker支持**: 容器化部署，便于运行和维护

## 环境要求

- Python 3.10+
- Binance API密钥（现货和合约）
- DeepSeek API密钥

## 安装步骤

1. 克隆项目代码：
   ```bash
   git clone <repository-url>
   cd AI-Trading-Bot
   ```

2. 安装依赖：
   ```bash
   pip install -r requirements.txt
   ```

3. 配置环境变量：
   ```bash
   cp .env.example .env
   # 编辑 .env 文件，填入您的API密钥
   ```

## 环境变量配置

在 `.env` 文件中配置以下变量：

```env
# DeepSeek API 配置
DEEPSEEK_API_KEY=your_deepseek_api_key_here

# Binance 合约 API 配置
BINANCE_FUTURES_API_KEY=your_binance_futures_api_key_here
BINANCE_FUTURES_SECRET=your_binance_futures_secret_here

# Binance 现货 API 配置
BINANCE_SPOT_API_KEY=your_binance_spot_api_key_here
BINANCE_SPOT_SECRET=your_binance_spot_secret_here

# 交易模式配置
ENABLE_SPOT_TRADING=true
ENABLE_FUTURES_TRADING=true

# 测试模式（设置为true时不进行真实交易）
TEST_MODE=true
```

## 运行方式

### 本地运行

```bash
# 运行统一交易机器人（同时运行现货和合约）
python main.py

# 单独运行现货交易机器人
python spot_trading.py

# 单独运行合约交易机器人
python futures_trading.py
```

### Docker运行

```bash
# 构建镜像
docker build -t ai-trading-bot .

# 运行容器
docker run -d --name ai-trading-bot \
  --env-file .env \
  -v ./data:/app/data \
  -v ./logs:/app/logs \
  ai-trading-bot
```

或者使用docker-compose：

```bash
# 启动服务
docker-compose up -d

# 查看日志
docker-compose logs -f

# 停止服务
docker-compose down
```

## 项目结构

```
AI-Trading-Bot/
├── futures_trading.py  # 合约交易主程序
├── spot_trading.py     # 现货交易主程序
├── main.py            # 统一主程序
├── thread_logger.py   # 线程日志记录器
├── requirements.txt   # 依赖包列表
├── .env.example       # 环境变量示例
├── Dockerfile         # Docker配置
├── docker-compose.yml # Docker Compose配置
├── data/              # 数据库存储目录
└── logs/              # 日志文件目录
```

## 交易策略

### 技术指标分析

- **趋势指标**: EMA、SMA多周期分析
- **震荡指标**: RSI(7, 14, 21)多周期分析
- **动能指标**: MACD、动量指标
- **波动率指标**: 布林带、ATR
- **成交量分析**: 成交量比率分析

### 风险管理

1. **仓位控制**: 根据信心度和账户资金情况动态调整仓位
2. **止损止盈**: 自动设置止损止盈点位
3. **资金管理**: 总资金使用不超过账户余额的80%
4. **多币种分散**: 避免过度集中在单一币种

### AI决策

使用DeepSeek AI进行多币种综合分析，考虑：
- 当前持仓情况
- 账户资金状况
- 多币种相关性
- 市场趋势分析
- 风险评估

## 数据库设计

系统使用SQLite数据库存储：

1. **价格数据表** (`price_data`): 存储历史价格和指标，支持多币种
2. **交易信号表** (`trading_signals`): 存储AI生成的交易信号，包含信心度和风险评估
3. **持仓记录表** (`position_records`): 存储持仓变化记录，区分现货和合约
4. **交易统计表** (`trading_stats`): 存储交易统计信息，包含运行时间和调用次数
5. **技术指标表** (`technical_indicators`): 存储技术指标历史，支持多时间周期分析

数据库文件位置：
- 现货交易数据库：`/app/data/spot_trading_bot.db`
- 合约交易数据库：`/app/data/trading_bot.db`

## 注意事项

1. **API密钥安全**: 请妥善保管您的API密钥，不要泄露给他人
2. **测试模式**: 建议首次运行时开启测试模式(`TEST_MODE=true`)
3. **资金控制**: 请合理设置仓位，控制风险
4. **网络环境**: 确保服务器网络稳定，避免因网络问题导致交易失败

## 故障排除

### 常见问题

1. **API连接失败**: 检查API密钥是否正确，网络是否通畅
2. **数据库错误**: 检查data目录权限，确保有读写权限
3. **交易失败**: 检查账户余额是否充足，API权限是否正确

### 日志查看

```bash
# 查看现货交易日志
tail -f logs/spot_trading.log

# 查看合约交易日志
tail -f logs/futures_trading.log

# 查看主程序日志
tail -f logs/main_program.log
```

## 许可证

本项目仅供学习和研究使用，请在合规的前提下使用。

## 免责声明

数字货币交易存在高风险，可能导致部分或全部资金损失。本软件仅供参考，不构成投资建议。使用者需自行承担所有风险。