import os
import json
import time
import requests
import pandas as pd
import numpy as np
import yfinance as yf
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta

# 載入原生拖曳元件
try:
    from streamlit_sortables import sort_items
    HAS_SORTABLES = True
except ImportError:
    HAS_SORTABLES = False

# ==========================================
# 1. 多重備援行情數據引擎
# ==========================================
class MultiSourceMarketData:
    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://quote.eastmoney.com/"
        }
        self.fund_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://fundf10.eastmoney.com/"
        }

    def fetch_ohlc(self, symbol: str, start_date: str = None, end_date: str = None) -> tuple[pd.DataFrame, str]:
        clean_code = symbol.split('.')[0].upper()

        try:
            df = self._fetch_eastmoney(symbol, clean_code, start_date, end_date)
            if not df.empty and len(df) >= 30:
                return df, "東方財富 (EastMoney)"
        except Exception:
            pass

        if clean_code.isdigit() and len(clean_code) == 6:
            try:
                df = self._fetch_eastmoney_fund(clean_code)
                if not df.empty and len(df) >= 30:
                    return df, "天天基金 (Tiantian Fund)"
            except Exception:
                pass

        try:
            df = self._fetch_tencent(symbol, clean_code)
            if not df.empty and len(df) >= 30:
                return df, "騰訊財經 (Tencent)"
        except Exception:
            pass

        try:
            df = self._fetch_yfinance(symbol, start_date, end_date)
            if not df.empty and len(df) >= 30:
                return df, "yfinance (備用)"
        except Exception:
            pass

        raise ValueError(f"無法獲取 {symbol} 行情數據，請確認代碼是否正確。")

    def _fetch_eastmoney_fund(self, fund_code: str) -> pd.DataFrame:
        url = "https://api.fund.eastmoney.com/f10/lsjz"
        params = {
            "fundCode": fund_code, "pageIndex": 1, "pageSize": 1000, "startDate": "", "endDate": ""
        }
        resp = requests.get(url, params=params, headers=self.fund_headers, timeout=5)
        data = resp.json()

        if not data or "Data" not in data or not data["Data"] or "LSJZList" not in data["Data"]:
            return pd.DataFrame()

        raw_list = data["Data"]["LSJZList"]
        records = []
        for item in raw_list:
            if item.get("DWJZ"):
                jz = float(item["DWJZ"])
                records.append({
                    "Date": item["FSRQ"], "Open": jz, "High": jz, "Low": jz, "Close": jz, "Volume": 10000.0
                })

        if not records:
            return pd.DataFrame()

        df = pd.DataFrame(records)
        df['Date'] = pd.to_datetime(df['Date'])
        df.sort_values('Date', inplace=True)
        df.set_index('Date', inplace=True)
        return df[['Open', 'High', 'Low', 'Close', 'Volume']]

    def _fetch_eastmoney(self, symbol: str, clean_code: str, start_date: str = None, end_date: str = None) -> pd.DataFrame:
        if symbol.endswith(".TW") or symbol.endswith(".TWO"):
            secid = f"116.{clean_code}"
        elif clean_code.startswith(("60", "688", "900", "51", "56", "58")) or symbol.endswith(".SS"):
            secid = f"1.{clean_code}"
        elif clean_code.startswith(("00", "01", "300", "200", "15", "16", "18")) or symbol.endswith(".SZ"):
            secid = f"0.{clean_code}"
        else:
            secid = f"0.{clean_code}"

        s_date = start_date.replace("-", "") if start_date else "19900101"
        e_date = end_date.replace("-", "") if end_date else "20500101"

        url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        params = {
            "fields1": "f1,f2,f3,f4,f5,f6", "fields2": "f51,f52,f53,f54,f55,f56",
            "ut": "fa5fd1943c7b386f172d6893dbfba10b", "klt": "101", "fqt": "1", 
            "beg": s_date, "end": e_date, "lmt": "1500", "secid": secid
        }
        resp = requests.get(url, params=params, headers=self.headers, timeout=5)
        data = resp.json()
        
        if not data or "data" not in data or not data["data"] or "klines" not in data["data"]:
            return pd.DataFrame()

        raw_klines = data["data"]["klines"]
        records = []
        for line in raw_klines:
            p = line.split(",")
            records.append({
                "Date": p[0], "Open": float(p[1]), "Close": float(p[2]),
                "High": float(p[3]), "Low": float(p[4]), "Volume": float(p[5])
            })
        df = pd.DataFrame(records)
        df['Date'] = pd.to_datetime(df['Date'])
        df.set_index('Date', inplace=True)
        return df[['Open', 'High', 'Low', 'Close', 'Volume']]

    def _fetch_tencent(self, symbol: str, clean_code: str) -> pd.DataFrame:
        if clean_code.startswith(("60", "688", "900", "51", "56", "58")) or symbol.endswith(".SS"):
            tc_symbol = f"sh{clean_code}"
        elif clean_code.startswith(("00", "01", "300", "200", "15", "16", "18")) or symbol.endswith(".SZ"):
            tc_symbol = f"sz{clean_code}"
        else:
            tc_symbol = f"r_tw{clean_code}"

        url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={tc_symbol},day,,,600,qfq"
        resp = requests.get(url, headers=self.headers, timeout=5)
        data = resp.json()
        
        if not data or "data" not in data or tc_symbol not in data["data"]:
            return pd.DataFrame()
            
        stock_data = data["data"][tc_symbol]
        kline_key = "qfqday" if "qfqday" in stock_data else ("day" if "day" in stock_data else None)
        if not kline_key or not stock_data[kline_key]:
            return pd.DataFrame()

        records = []
        for item in stock_data[kline_key]:
            records.append({
                "Date": item[0], "Open": float(item[1]), "Close": float(item[2]),
                "High": float(item[3]), "Low": float(item[4]), "Volume": float(item[5])
            })
        df = pd.DataFrame(records)
        df['Date'] = pd.to_datetime(df['Date'])
        df.set_index('Date', inplace=True)
        return df[['Open', 'High', 'Low', 'Close', 'Volume']]

    def _fetch_yfinance(self, symbol: str, start_date: str = None, end_date: str = None) -> pd.DataFrame:
        clean_code = symbol.split('.')[0].upper()
        
        if clean_code.isdigit() and not (symbol.endswith(".TW") or symbol.endswith(".TWO")):
            if clean_code.startswith(("60", "688", "51", "56", "58")):
                yf_symbol = f"{clean_code}.SS"
            elif clean_code.startswith(("00", "01", "300", "15", "16", "18")):
                yf_symbol = f"{clean_code}.SZ"
            else:
                yf_symbol = symbol
        else:
            yf_symbol = symbol

        for attempt in range(3):
            try:
                ticker = yf.Ticker(yf_symbol)
                if start_date and end_date:
                    df = ticker.history(start=start_date, end=end_date)
                else:
                    df = ticker.history(period="2y")
                
                if not df.empty and len(df) >= 30:
                    df = df.reset_index()
                    df['Date'] = pd.to_datetime(df['Date']).dt.tz_localize(None)
                    df.set_index('Date', inplace=True)
                    return df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()
            except Exception:
                pass
            time.sleep(1.0 * (attempt + 1))

        return pd.DataFrame()

data_engine = MultiSourceMarketData()

@st.cache_data(ttl=300)
def cached_fetch_ohlc(symbol: str, start_date: str = None, end_date: str = None):
    return data_engine.fetch_ohlc(symbol, start_date, end_date)

# ==========================================
# 2. 技術指標與動態溫控邏輯
# ==========================================
class TradingStrategyEngine:
    @staticmethod
    def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df['MA5'] = df['Close'].rolling(5).mean()
        df['MA10'] = df['Close'].rolling(10).mean()
        df['MA20'] = df['Close'].rolling(20).mean()
        df['MA60'] = df['Close'].rolling(60).mean()

        df['TR0'] = df['High'] - df['Low']
        df['TR1'] = (df['High'] - df['Close'].shift(1)).abs()
        df['TR2'] = (df['Low'] - df['Close'].shift(1)).abs()
        df['TR'] = df[['TR0', 'TR1', 'TR2']].max(axis=1)
        df['ATR14'] = df['TR'].ewm(alpha=1/14, adjust=False).mean()

        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        df['RSI14'] = 100 - (100 / (1 + rs))

        df['High_20'] = df['High'].shift(1).rolling(20).max()
        df['Low_20'] = df['Low'].shift(1).rolling(20).min()

        ema12 = df['Close'].ewm(span=12, adjust=False).mean()
        ema26 = df['Close'].ewm(span=26, adjust=False).mean()
        df['DIF'] = ema12 - ema26
        df['DEA'] = df['DIF'].ewm(span=9, adjust=False).mean()
        df['MACD_Hist'] = (df['DIF'] - df['DEA']) * 2

        df['Ret20'] = df['Close'].pct_change(20)
        df['Score_Rank20'] = df['Ret20'].rolling(60).apply(
            lambda x: (pd.Series(x).rank(pct=True).iloc[-1] * 100) if len(x) > 0 else 50, raw=False
        )

        range_20 = df['High_20'] - df['Low_20']
        df['Score_Pos20'] = np.where(range_20 > 0, (df['Close'] - df['Low_20']) / range_20 * 100.0, 50.0)

        ma_score = np.zeros(len(df))
        ma_score += np.where(df['Close'] > df['MA5'], 25, 0)
        ma_score += np.where(df['MA5'] > df['MA10'], 25, 0)
        ma_score += np.where(df['MA10'] > df['MA20'], 25, 0)
        ma_score += np.where(df['MA20'] > df['MA60'], 25, 0)
        df['Score_MA'] = ma_score

        df['Score_RSI'] = df['RSI14'].clip(0, 100)

        df['Score_MACD'] = df['MACD_Hist'].rolling(60).apply(
            lambda x: (pd.Series(x).rank(pct=True).iloc[-1] * 100) if len(x) > 0 else 50, raw=False
        )

        df['Temperature'] = (
            0.24 * df['Score_Rank20'].fillna(50) +
            0.22 * df['Score_Pos20'].fillna(50) +
            0.22 * df['Score_MA'] +
            0.18 * df['Score_RSI'].fillna(50) +
            0.14 * df['Score_MACD'].fillna(50)
        ).clip(0, 100)

        return df

# ==========================================
# 3. 歷史回測引擎 (連2天站穩 + 1.5%緩衝防洗版)
# ==========================================
class StrategyBacktester:
    def __init__(self, df: pd.DataFrame, initial_capital: float = 200000.0, start_date: str = None, end_date: str = None):
        df_calc = TradingStrategyEngine.calculate_indicators(df)
        if start_date and end_date:
            self.df = df_calc.loc[start_date:end_date]
        else:
            self.df = df_calc
            
        self.initial_capital = initial_capital

    def run(self):
        df = self.df.copy()
        cash = self.initial_capital
        shares = 0
        avg_cost = 0.0
        
        took_profit_15 = False
        took_atr_profit = False
        has_crossed_ma20 = False
        cooldown_counter = 0        
        last_trade_was_loss = False 
        
        highest_price_since_entry = 0.0
        last_add_price = 0.0
        
        trades = []
        equity_curve = []
        
        for i in range(len(df)):
            if i < 20:
                continue
                
            date = df.index[i]
            date_str = date.strftime("%Y-%m-%d")
            today = df.iloc[i]
            yesterday = df.iloc[i-1]
            prev_20 = df.iloc[i-20:i]
            prev_10 = df.iloc[i-10:i]
            
            price = float(today['Close'])
            open_p = float(today['Open'])
            low = float(today['Low'])
            
            ma5 = float(today['MA5']) if not np.isnan(today['MA5']) else price
            ma10 = float(today['MA10']) if not np.isnan(today['MA10']) else price
            ma20 = float(today['MA20']) if not np.isnan(today['MA20']) else price
            ma60 = float(today['MA60']) if not np.isnan(today['MA60']) else price
            rsi14 = float(today['RSI14']) if not np.isnan(today['RSI14']) else 50.0
            temp = float(today['Temperature']) if not np.isnan(today['Temperature']) else 50.0
            atr14 = float(today['ATR14']) if not np.isnan(today['ATR14']) else 0.0
            yesterday_low = float(yesterday['Low'])
            yesterday_close = float(yesterday['Close'])
            yesterday_ma10 = float(yesterday['MA10']) if not np.isnan(yesterday['MA10']) else yesterday_close
            yesterday_ma20 = float(yesterday['MA20']) if not np.isnan(yesterday['MA20']) else ma20

            if cooldown_counter > 0:
                cooldown_counter -= 1

            # 斜率與結構判定
            if i >= 25:
                ma20_5d_ago = float(df.iloc[i-5]['MA20'])
                ma20_slope_5d = ((ma20 - ma20_5d_ago) / ma20_5d_ago) * 100.0 if ma20_5d_ago > 0 else 0.0
            else:
                ma20_slope_5d = 0.0

            # 🛑 雙重空頭防守條件：1. 斜率陡峭下滑  2. 價格低於 MA20 且 MA20 低於 MA60
            is_downtrend_structure = (price < ma20 and ma20 < ma60)
            is_steep_downtrend = (ma20_slope_5d <= -1.0) or is_downtrend_structure

            # RSI 背離計算
            price_low_20 = low < prev_20['Low'].min()
            min_rsi_20 = prev_20['RSI14'].min()
            rsi_diff = rsi14 - min_rsi_20
            rsi_bullish_div = price_low_20 and (rsi_diff > 0)
            is_bullish_candle = price > open_p

            if shares > 0:
                if price > highest_price_since_entry:
                    highest_price_since_entry = price
                if price >= ma20:
                    has_crossed_ma20 = True

            # ==========================================
            # 1. 出場與減倉機制
            # ==========================================
            sold_today = False
            if shares > 0:
                unrealized_pct = ((price - avg_cost) / avg_cost) * 100.0

                # 🛑 1. 8% 硬停損
                if unrealized_pct <= -8.0:
                    sell_amount = shares * price
                    pnl = sell_amount - (shares * avg_cost)
                    cash += sell_amount
                    
                    sell_shares = shares
                    shares = 0
                    avg_cost = 0.0
                    highest_price_since_entry = 0.0
                    last_add_price = 0.0
                    took_profit_15 = False
                    took_atr_profit = False
                    has_crossed_ma20 = False
                    cooldown_counter = 3
                    last_trade_was_loss = True
                    sold_today = True

                    trades.append({
                        "Date": date, "日期": date_str, "動作": "全數賣出", "類別": "Sell", "原因": "🛑 8% 硬停損止血", 
                        "成交價": price, "股數": sell_shares, "損益": round(pnl, 2), "報酬率": f"{unrealized_pct:+.2f}%", 
                        "當下倉位": "0 股 (0.0%)", "剩餘現金": round(cash, 2)
                    })

                # 🚨 2. MA20 生命線清倉 (加入 1.5% 跌破緩衝區，避免影線洗盤)
                # 條件：收盤價實質跌破 MA20 達 1.5% 以上 (price < ma20 * 0.985)
                elif (has_crossed_ma20 or took_profit_15 or took_atr_profit) and (price < ma20 * 0.985):
                    sell_amount = shares * price
                    pnl = sell_amount - (shares * avg_cost)
                    cash += sell_amount
                    
                    sell_shares = shares
                    shares = 0
                    avg_cost = 0.0
                    highest_price_since_entry = 0.0
                    last_add_price = 0.0
                    took_profit_15 = False
                    took_atr_profit = False
                    has_crossed_ma20 = False
                    cooldown_counter = 3
                    last_trade_was_loss = (pnl < 0)
                    sold_today = True

                    trades.append({
                        "Date": date, "日期": date_str, "動作": "清倉離場", "類別": "Sell", "原因": "🚨 實質跌破 MA20 (超過1.5%)", 
                        "成交價": price, "股數": sell_shares, "損益": round(pnl, 2), "報酬率": f"{unrealized_pct:+.2f}%", 
                        "當下倉位": "0 股 (0.0%)", "剩餘現金": round(cash, 2)
                    })

                # 🔥 3. 沸點反轉全清倉
                elif temp > 95.0 and (price < yesterday_low or price < ma5):
                    sell_amount = shares * price
                    pnl = sell_amount - (shares * avg_cost)
                    cash += sell_amount
                    
                    sell_shares = shares
                    shares = 0
                    avg_cost = 0.0
                    highest_price_since_entry = 0.0
                    last_add_price = 0.0
                    took_profit_15 = False
                    took_atr_profit = False
                    has_crossed_ma20 = False
                    cooldown_counter = 3
                    last_trade_was_loss = False
                    sold_today = True

                    trades.append({
                        "Date": date, "日期": date_str, "動作": "全數賣出", "類別": "Sell", "原因": "🔥 沸點反轉全清倉", 
                        "成交價": price, "股數": sell_shares, "損益": round(pnl, 2), "報酬率": f"{unrealized_pct:+.2f}%", 
                        "當下倉位": "0 股 (0.0%)", "剩餘現金": round(cash, 2)
                    })

                # 💰 4. 盈利超 15% 減碼 50%
                elif unrealized_pct >= 15.0 and not took_profit_15:
                    sell_shares = int(shares * 0.5)
                    if sell_shares > 0:
                        sell_amount = sell_shares * price
                        pnl = sell_amount - (sell_shares * avg_cost)
                        cash += sell_amount
                        shares -= sell_shares
                        took_profit_15 = True
                        
                        curr_val = cash + (shares * price)
                        pos_pct = (shares * price / curr_val * 100) if curr_val > 0 else 0
                        trades.append({
                            "Date": date, "日期": date_str, "動作": "減碼50%", "類別": "Sell", "原因": "💰 盈利超15%鎖利", 
                            "成交價": price, "股數": sell_shares, "損益": round(pnl, 2), "報酬率": f"{unrealized_pct:+.2f}%", 
                            "當下倉位": f"{shares:,} 股 ({pos_pct:.1f}%)", "剩餘現金": round(cash, 2)
                        })

                # 🛡️ 5. 2.0x ATR 保險絲
                elif not took_atr_profit and highest_price_since_entry > 0 and (highest_price_since_entry - price) >= (2.0 * atr14):
                    sell_shares = int(shares * 0.5)
                    if sell_shares > 0:
                        sell_amount = sell_shares * price
                        pnl = sell_amount - (sell_shares * avg_cost)
                        cash += sell_amount
                        shares -= sell_shares
                        took_atr_profit = True

                        curr_val = cash + (shares * price)
                        pos_pct = (shares * price / curr_val * 100) if curr_val > 0 else 0
                        trades.append({
                            "Date": date, "日期": date_str, "動作": "減碼50%", "類別": "Sell", "原因": "🛡️ 2.0x ATR 保險絲", 
                            "成交價": price, "股數": sell_shares, "損益": round(pnl, 2), "報酬率": f"{unrealized_pct:+.2f}%", 
                            "當下倉位": f"{shares:,} 股 ({pos_pct:.1f}%)", "剩餘現金": round(cash, 2)
                        })

            if sold_today:
                current_portfolio_value = cash
                benchmark_value = (self.initial_capital / df.iloc[0]['Close']) * price
                equity_curve.append({
                    "Date": date,
                    "策略資產淨值": current_portfolio_value,
                    "買入持有基準": benchmark_value
                })
                continue

            # ==========================================
            # 2. 進場與加倉機制
            # ==========================================
            if cooldown_counter == 0 and temp <= 85.0:
                
                # 開倉邏輯 (未持股)
                if shares == 0:
                    strict_rsi_cond = (rsi_diff > 15.0 and price > ma5) if last_trade_was_loss else True

                    # 🥶 方式 A：左側抄底 (需同時滿足：非空頭結構 + 無陡峭下彎)
                    if rsi_bullish_div and temp < 35.0 and is_bullish_candle and strict_rsi_cond and not is_steep_downtrend:
                        buy_budget = self.initial_capital * 0.20
                        buy_shares = int(buy_budget / price)
                        if buy_shares > 0 and cash >= buy_shares * price:
                            cost = buy_shares * price
                            cash -= cost
                            shares = buy_shares
                            avg_cost = price
                            last_add_price = price
                            highest_price_since_entry = price
                            took_profit_15 = False
                            took_atr_profit = False
                            has_crossed_ma20 = (price >= ma20)

                            curr_val = cash + (shares * price)
                            pos_pct = (shares * price / curr_val * 100) if curr_val > 0 else 0
                            trades.append({
                                "Date": date, "日期": date_str, "動作": "建倉(20%)", "類別": "Buy", "原因": "🥶 極寒抄底 (結構正常)", 
                                "成交價": price, "股數": buy_shares, "損益": 0.0, "報酬率": "0.00%", 
                                "當下倉位": f"{shares:,} 股 ({pos_pct:.1f}%)", "剩餘現金": round(cash, 2)
                            })

                    # 📈 方式 B：右側強勢突破 (需要連續 2 天收盤價站穩 MA20 + 創 5 日新高)
                    elif price > ma20 and (ma5 > ma10) and is_bullish_candle:
                        yesterday_price = float(yesterday['Close'])
                        yesterday_ma20_val = float(yesterday['MA20']) if not np.isnan(yesterday['MA20']) else yesterday_price
                        
                        # 關鍵雙重濾網：1. 今日與昨日皆站上 MA20  2. MA20 翻揚  3. 創近 5 日新高
                        hold_above_ma20_2days = (price > ma20) and (yesterday_price > yesterday_ma20_val)
                        ma20_turning_up = ma20 >= yesterday_ma20
                        is_5d_high = price >= prev_10['Close'].iloc[-5:].max()

                        if hold_above_ma20_2days and ma20_turning_up and is_5d_high:
                            buy_budget = self.initial_capital * 0.30
                            buy_shares = int(buy_budget / price)
                            if buy_shares > 0 and cash >= buy_shares * price:
                                cost = buy_shares * price
                                cash -= cost
                                shares = buy_shares
                                avg_cost = price
                                last_add_price = price
                                highest_price_since_entry = price
                                took_profit_15 = False
                                took_atr_profit = False
                                has_crossed_ma20 = True

                                curr_val = cash + (shares * price)
                                pos_pct = (shares * price / curr_val * 100) if curr_val > 0 else 0
                                trades.append({
                                    "Date": date, "日期": date_str, "動作": "建倉(30%)", "類別": "Buy", "原因": "🚀 右側突破 (連2天站穩MA20+創5日新高)", 
                                    "成交價": price, "股數": buy_shares, "損益": 0.0, "報酬率": "0.00%", 
                                    "當下倉位": f"{shares:,} 股 ({pos_pct:.1f}%)", "剩餘現金": round(cash, 2)
                                })

                # 加碼邏輯 (已持股時快加碼)
                elif shares > 0 and cash >= (price * 100):
                    price_change_from_last = (price - last_add_price) / last_add_price
                    
                    # 🚀 右側快速打滿：建倉後只要獲利突破 3% 且站穩 MA5，直接把剩餘資金打滿
                    if price_change_from_last >= 0.03 and price > ma5:
                        add_shares = int(cash / price)
                        if add_shares > 0:
                            total_cost = (shares * avg_cost) + (add_shares * price)
                            shares += add_shares
                            avg_cost = total_cost / shares
                            cash -= (add_shares * price)
                            last_add_price = price

                            curr_val = cash + (shares * price)
                            pos_pct = (shares * price / curr_val * 100) if curr_val > 0 else 0
                            trades.append({
                                "Date": date, "日期": date_str, "動作": "快速打滿100%", "類別": "Buy", "原因": f"🚀 趨勢確立加碼打滿 ({price_change_from_last:+.1f}%)", 
                                "成交價": price, "股數": add_shares, "損益": 0.0, "報酬率": "0.00%", 
                                "當下倉位": f"{shares:,} 股 ({pos_pct:.1f}%)", "剩餘現金": round(cash, 2)
                            })

            current_portfolio_value = cash + (shares * price)
            benchmark_value = (self.initial_capital / df.iloc[0]['Close']) * price
            equity_curve.append({
                "Date": date,
                "策略資產淨值": current_portfolio_value,
                "買入持有基準": benchmark_value
            })

        df_equity = pd.DataFrame(equity_curve).set_index("Date")
        df_trades = pd.DataFrame(trades)
        
        return df_equity, df_trades, df

# ==========================================
# 4. 本地 JSON 數據庫管理者
# ==========================================
DB_FILE = "portfolio_data.json"

def save_db(db):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=4)

def load_db():
    default_db = {
        "stock_order": ["2330.TW", "513380"],
        "stocks": {
            "2330.TW": {
                "symbol": "2330.TW", "name": "台積電",
                "target_capital": 200000.0
            },
            "513380": {
                "symbol": "513380", "name": "恒生科技ETF廣發",
                "target_capital": 200000.0
            }
        }
    }

    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

            if "stocks" in data:
                saved_order = data.get("stock_order", [])
                existing_keys = list(data["stocks"].keys())
                final_order = [k for k in saved_order if k in existing_keys]
                for k in existing_keys:
                    if k not in final_order:
                        final_order.append(k)
                data["stock_order"] = final_order
                return data
            else:
                new_data = {"stock_order": [], "stocks": {}}
                for key, val in data.items():
                    if isinstance(val, dict):
                        new_data["stocks"][key] = {
                            "symbol": val.get("symbol", key),
                            "name": val.get("name", key),
                            "target_capital": 200000.0
                        }
                        new_data["stock_order"].append(key)
                save_db(new_data)
                return new_data
        except Exception:
            save_db(default_db)
            return default_db
    else:
        save_db(default_db)
        return default_db

# ==========================================
# 5. Streamlit GUI 主介面
# ==========================================
st.set_page_config(page_title="動態溫控策略回測系統", layout="wide", page_icon="📜")

if "db" not in st.session_state:
    st.session_state.db = load_db()

db = st.session_state.db
db.setdefault("stocks", {})
db.setdefault("stock_order", list(db["stocks"].keys()))

st.sidebar.title("⚙️ 戰情控制面板")

stock_keys = [k for k in db.get("stock_order", []) if k in db["stocks"]]
stock_options = {k: f"{k} - {db['stocks'][k].get('name', k)}" for k in stock_keys}

if hasattr(st, "dialog"):
    @st.dialog("↕️ 拖曳調整自選標的順序")
    def reorder_modal():
        st.write("按住標籤可自由上下拖動，排序完成後點擊儲存：")
        display_items = [stock_options[k] for k in stock_keys]
        sorted_display = sort_items(display_items)
        
        reverse_map = {v: k for k, v in stock_options.items()}
        new_order = [reverse_map[item] for item in sorted_display if item in reverse_map]
        
        if st.button("💾 儲存並套用新順序", type="primary"):
            db["stock_order"] = new_order
            save_db(db)
            st.success("✅ 已更新！")
            st.rerun()

    if HAS_SORTABLES:
        if st.sidebar.button("↕️ 調整自選清單順序"):
            reorder_modal()

st.sidebar.markdown("---")

with st.sidebar.expander("➕ 新增標的", expanded=False):
    new_sym = st.text_input("代碼 (台股 2330.TW / ETF 513380 / 基金 013396)", "").strip().upper()
    new_name = st.text_input("標的名稱 (選填，若空白將自動使用代碼)", "").strip()
    new_cap = st.number_input("獨立資本上限 (元)", min_value=10000.0, max_value=100000000.0, value=200000.0, step=50000.0)
    
    if st.button("確認新增"):
        if new_sym:
            final_name = new_name if new_name else new_sym
            if new_sym not in db["stocks"]:
                db["stocks"][new_sym] = {
                    "symbol": new_sym, "name": final_name, "target_capital": new_cap
                }
                if new_sym not in db["stock_order"]:
                    db["stock_order"].append(new_sym)
                save_db(db)
                st.sidebar.success(f"✅ 已成功加入 {new_sym} ({final_name})")
                st.rerun()
            else:
                st.sidebar.warning(f"⚠️ {new_sym} 已存在於自選庫中！")

if db.get("stocks"):
    del_sym = st.sidebar.selectbox(
        "🗑️ 刪除標的",
        options=stock_keys,
        format_func=lambda x: stock_options[x] if x in stock_options else x
    )
    if st.sidebar.button("確認刪除標的"):
        if del_sym in db["stocks"]:
            del db["stocks"][del_sym]
        if del_sym in db["stock_order"]:
            db["stock_order"].remove(del_sym)
        save_db(db)
        st.sidebar.success(f"已刪除 {del_sym}")
        st.rerun()

st.title("📜 個股歷史策略模擬與回測系統")

col_bt1, col_bt2, col_bt3 = st.columns([2, 2, 2])
with col_bt1:
    bt_symbol = st.selectbox(
        "選擇回測標的",
        options=stock_keys,
        format_func=lambda x: stock_options[x] if x in stock_options else x,
        key="bt_symbol"
    )
with col_bt2:
    default_start = datetime.now() - timedelta(days=365 * 2)
    bt_start = st.date_input("回測開始日期", value=default_start)
with col_bt3:
    bt_end = st.date_input("回測結束日期", value=datetime.now())

col_cap1, col_cap2 = st.columns([2, 4])
with col_cap1:
    init_cap = float(db["stocks"].get(bt_symbol, {}).get("target_capital", 200000.0))
    bt_capital = st.number_input("初始回測資金", min_value=10000.0, max_value=10000000.0, value=init_cap, step=50000.0)

if st.button("🚀 開始歷史回測模擬", type="primary"):
    start_str = bt_start.strftime("%Y-%m-%d")
    end_str = bt_end.strftime("%Y-%m-%d")

    with st.spinner(f"正在擷取 {bt_symbol} 行情數據與執行回測..."):
        try:
            fetch_start = (bt_start - timedelta(days=120)).strftime("%Y-%m-%d")
            df_bt_raw, src_bt = cached_fetch_ohlc(bt_symbol, start_date=fetch_start, end_date=end_str)
            
            if df_bt_raw.empty or len(df_bt_raw) < 30:
                st.error("歷史數據不足，無法執行回測。請確認代碼或重新選擇區間。")
            else:
                backtester = StrategyBacktester(df_bt_raw, initial_capital=bt_capital, start_date=start_str, end_date=end_str)
                df_equity, df_trades, df_kline = backtester.run()

                if df_equity.empty:
                    st.warning("選定日期區間內沒有可用的交易日行情數據。")
                else:
                    final_strat_val = df_equity["策略資產淨值"].iloc[-1]
                    final_bench_val = df_equity["買入持有基準"].iloc[-1]
                    
                    strat_return = ((final_strat_val - bt_capital) / bt_capital) * 100.0
                    bench_return = ((final_bench_val - bt_capital) / bt_capital) * 100.0

                    equity_series = df_equity["策略資產淨值"]
                    cummax = equity_series.cummax()
                    drawdown = (equity_series - cummax) / cummax
                    max_drawdown = drawdown.min() * 100.0 if not drawdown.empty else 0.0

                    if not df_trades.empty and any(act in df_trades["動作"].values for act in ["全數賣出", "清倉離場", "減碼50%"]):
                        closed_trades = df_trades[df_trades["動作"].isin(["全數賣出", "清倉離場", "減碼50%"])]
                        win_count = len(closed_trades[closed_trades["損益"] > 0])
                        total_closed = len(closed_trades)
                        win_rate = (win_count / total_closed * 100.0) if total_closed > 0 else 0.0
                    else:
                        win_rate = 0.0
                        total_closed = 0

                    st.markdown("---")
                    st.markdown("#### 📊 回測績效總覽")
                    
                    b1, b2, b3, b4, b5 = st.columns(5)
                    b1.metric("策略期末總資產", f"${final_strat_val:,.0f}")
                    b2.metric("策略總累積報酬率", f"{strat_return:+.2f}%", delta=f"{strat_return - bench_return:+.2f}% vs 基準")
                    b3.metric("買入持有 (Benchmark)", f"{bench_return:+.2f}%")
                    b4.metric("最大資產回撤 (MDD)", f"{max_drawdown:.2f}%")
                    b5.metric("出場/減碼勝率", f"{win_rate:.1f}%", f"共 {total_closed} 次操作")

                    st.markdown("---")
                    st.markdown("#### 🎯 K 線與 Buy / Sell 交易點位圖")

                    df_kline_sub = df_kline.loc[start_str:end_str]
                    fig_kline = make_subplots(rows=1, cols=1, shared_xaxes=True)

                    fig_kline.add_trace(go.Candlestick(
                        x=df_kline_sub.index,
                        open=df_kline_sub['Open'], high=df_kline_sub['High'],
                        low=df_kline_sub['Low'], close=df_kline_sub['Close'],
                        name='K線',
                        increasing_line_color='#26a69a', decreasing_line_color='#ef5350'
                    ))

                    fig_kline.add_trace(go.Scatter(x=df_kline_sub.index, y=df_kline_sub['MA5'], mode='lines', name='MA5', line=dict(color='#FF9800', width=1)))
                    fig_kline.add_trace(go.Scatter(x=df_kline_sub.index, y=df_kline_sub['MA10'], mode='lines', name='MA10', line=dict(color='#2196F3', width=1)))
                    fig_kline.add_trace(go.Scatter(x=df_kline_sub.index, y=df_kline_sub['MA20'], mode='lines', name='MA20', line=dict(color='#9C27B0', width=1.5)))

                    if not df_trades.empty:
                        buys = df_trades[df_trades['類別'] == 'Buy']
                        sells = df_trades[df_trades['類別'] == 'Sell']

                        if not buys.empty:
                            fig_kline.add_trace(go.Scatter(
                                x=buys['Date'],
                                y=buys['成交價'] * 0.98,
                                mode='markers+text',
                                name='買進/加碼',
                                marker=dict(symbol='triangle-up', size=14, color='#00E676'),
                                text=buys['動作'],
                                textposition='bottom center',
                                hovertext=buys['原因']
                            ))

                        if not sells.empty:
                            fig_kline.add_trace(go.Scatter(
                                x=sells['Date'],
                                y=sells['成交價'] * 1.02,
                                mode='markers+text',
                                name='賣出/減碼',
                                marker=dict(symbol='triangle-down', size=14, color='#FF1744'),
                                text=sells['動作'],
                                textposition='top center',
                                hovertext=sells['原因']
                            ))

                    fig_kline.update_layout(
                        title=f"{bt_symbol} 交易訊號發布對照圖",
                        xaxis_title="日期",
                        yaxis_title="價格",
                        xaxis_rangeslider_visible=False,
                        hovermode="x unified",
                        template="plotly_white",
                        height=550
                    )
                    st.plotly_chart(fig_kline, use_container_width=True)

                    st.markdown("---")
                    st.markdown("#### 📈 資產淨值成長曲線 vs 買入持有對照")

                    fig_eq = go.Figure()
                    fig_eq.add_trace(go.Scatter(x=df_equity.index, y=df_equity["策略資產淨值"], mode='lines', name='動態溫控策略', line=dict(color='#2962FF', width=2)))
                    fig_eq.add_trace(go.Scatter(x=df_equity.index, y=df_equity["買入持有基準"], mode='lines', name='買入持有基準 (Buy & Hold)', line=dict(color='#B0BEC5', width=1.5, dash='dash')))

                    fig_eq.update_layout(
                        title=f"{bt_symbol} 策略與大盤持有權益對比圖 ({start_str} ~ {end_str})",
                        xaxis_title="日期",
                        yaxis_title="資產總淨值",
                        hovermode="x unified",
                        template="plotly_white",
                        height=400
                    )
                    st.plotly_chart(fig_eq, use_container_width=True)

                    st.markdown("---")
                    st.markdown("#### 📜 模擬交易進出場明細")
                    if not df_trades.empty:
                        cols_order = ["日期", "動作", "原因", "成交價", "股數", "損益", "報酬率", "當下倉位", "剩餘現金"]
                        display_df = df_trades[cols_order]
                        st.dataframe(display_df, use_container_width=True, hide_index=True)
                    else:
                        st.info("於此歷史區間內，未觸發任何買賣進場條件。")

        except Exception as ex:
            st.error(f"執行歷史回測失敗: {ex}")
