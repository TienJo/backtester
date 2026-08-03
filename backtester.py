import os
import json
import time
import requests
import pandas as pd
import numpy as np
import yfinance as yf
import streamlit as st
import plotly.graph_objects as go
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
        df['MA10_Vol'] = df['Volume'].rolling(10).mean()

        candle_range = df['High'] - df['Low']
        df['Candle_Body_Ratio'] = np.where(candle_range > 0, (df['Close'] - df['Open']) / candle_range, 0.0)
        df['Bias_MA20'] = (df['Close'] - df['MA20']) / df['MA20'] * 100.0
        df['MA60_Slope'] = df['MA60'] - df['MA60'].shift(3)

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
# 3. 歷史回測引擎 (升級高勝率建倉模組)
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
        took_profit = False
        has_stopped_out = False
        
        trades = []
        equity_curve = []
        
        for i in range(len(df)):
            if i < 10:
                continue
                
            date = df.index[i]
            date_str = date.strftime("%Y-%m-%d")
            today = df.iloc[i]
            yesterday = df.iloc[i-1]
            prev_10 = df.iloc[i-10:i]
            
            price = float(today['Close'])
            open_price = float(today['Open'])
            low = float(today['Low'])
            volume = float(today['Volume'])
            
            ma5 = float(today['MA5']) if not np.isnan(today['MA5']) else price
            ma10 = float(today['MA10']) if not np.isnan(today['MA10']) else price
            ma20 = float(today['MA20']) if not np.isnan(today['MA20']) else price
            y_ma5 = float(yesterday['MA5']) if not np.isnan(yesterday['MA5']) else ma5
            y_ma20 = float(yesterday['MA20']) if not np.isnan(yesterday['MA20']) else ma20
            
            rsi14 = float(today['RSI14']) if not np.isnan(today['RSI14']) else 50.0
            temp = float(today['Temperature']) if not np.isnan(today['Temperature']) else 50.0
            high_20 = float(today['High_20']) if not np.isnan(today['High_20']) else price
            ma10_vol_prev = float(yesterday['MA10_Vol']) if not np.isnan(yesterday['MA10_Vol']) else volume
            yesterday_low = float(yesterday['Low'])
            
            vol_ratio = volume / ma10_vol_prev if ma10_vol_prev > 0 else 0.0
            bullish_trend = price > ma20 and ma5 > ma20

            # --- K 線實體與 ATR 過濾 ---
            candle_body = price - open_price
            atr14_val = float(today['ATR14']) if not np.isnan(today['ATR14']) else 0.0
            is_strong_red_candle = (candle_body > 0) and (candle_body >= atr14_val * 0.7)

            # --- RSI 背離計算 ---
            price_low_10 = low < prev_10['Low'].min()
            min_rsi_10 = prev_10['RSI14'].min()
            rsi_diff = rsi14 - min_rsi_10
            rsi_bullish_div = price_low_10 and (rsi14 > min_rsi_10) and (rsi14 < 45)

            # --- 新版高勝率建倉條件 ---
            # A. 左側帶量止跌建倉 (40%): 背離 + 站穩 MA5/MA10 + 實體強勢紅棒
            first_bottom_signal = (
                rsi_bullish_div and 
                (temp < 35.0) and 
                (price > ma5 and price > ma10) and 
                is_strong_red_candle
            )
            
            # B. 右側均線扭轉建倉 (60%): 突破 MA20 或 MA5金叉MA20 + 帶量 + 避開過熱區
            ma20_breakout = (price > ma20) and (yesterday['Close'] <= y_ma20)
            ma5_cross_ma20 = (ma5 > ma20) and (y_ma5 <= y_ma20)
            right_side_signal = (
                (ma20_breakout or ma5_cross_ma20) and 
                (vol_ratio >= 1.25) and 
                (35.0 <= temp <= 75.0)
            )

            # C. 停損後的二次探底條件：RSI背離 > 20點 + 最低價 > MA5
            second_bottom_signal = price_low_10 and (rsi_diff > 20.0) and (low > ma5)

            # 1. 出場與減倉機制
            sold_today = False
            if shares > 0:
                hard_stop_price = avg_cost * 0.92
                unrealized_pct = ((price - avg_cost) / avg_cost) * 100.0

                # A. 獲利 15% 減倉 50%
                if unrealized_pct >= 15.0 and not took_profit:
                    sell_shares = int(shares * 0.5)
                    if sell_shares > 0:
                        sell_amount = sell_shares * price
                        pnl = sell_amount - (sell_shares * avg_cost)
                        cash += sell_amount
                        shares -= sell_shares
                        took_profit = True
                        trades.append({
                            "日期": date_str, "動作": "減倉50%", "原因": "💰 獲利達15%鎖利", 
                            "成交價": price, "股數": sell_shares, "損益": pnl, "報酬率": f"{unrealized_pct:+.2f}%", "剩餘現金": cash
                        })

                # B. 沸點逃頂
                if temp > 95.0 and (price < yesterday_low or price < ma5):
                    sell_amount = shares * price
                    pnl = sell_amount - (shares * avg_cost)
                    pnl_pct = (pnl / (shares * avg_cost)) * 100
                    cash += sell_amount
                    trades.append({
                        "日期": date_str, "動作": "全數賣出", "原因": "🔥 沸點反轉逃頂", 
                        "成交價": price, "股數": shares, "損益": pnl, "報酬率": f"{pnl_pct:+.2f}%", "剩餘現金": cash
                    })
                    shares = 0
                    avg_cost = 0.0
                    took_profit = False
                    sold_today = True
                    has_stopped_out = False
                    
                # C. 8% 絕對停損
                elif price <= hard_stop_price:
                    sell_amount = shares * price
                    pnl = sell_amount - (shares * avg_cost)
                    pnl_pct = (pnl / (shares * avg_cost)) * 100
                    cash += sell_amount
                    trades.append({
                        "日期": date_str, "動作": "停損出場", "原因": "🚨 跌破成本 8%", 
                        "成交價": price, "股數": shares, "損益": pnl, "報酬率": f"{pnl_pct:+.2f}%", "剩餘現金": cash
                    })
                    shares = 0
                    avg_cost = 0.0
                    took_profit = False
                    sold_today = True
                    has_stopped_out = True
                    
                # D. 建倉失敗停損 (跌破 MA20 * 0.97)
                elif price < ma20 * 0.97:
                    sell_amount = shares * price
                    pnl = sell_amount - (shares * avg_cost)
                    pnl_pct = (pnl / (shares * avg_cost)) * 100
                    cash += sell_amount
                    trades.append({
                        "日期": date_str, "動作": "停損出場", "原因": "⚠️ 跌破 MA20 3%", 
                        "成交價": price, "股數": shares, "損益": pnl, "報酬率": f"{pnl_pct:+.2f}%", "剩餘現金": cash
                    })
                    shares = 0
                    avg_cost = 0.0
                    took_profit = False
                    sold_today = True
                    has_stopped_out = True

            if sold_today:
                current_portfolio_value = cash
                benchmark_value = (self.initial_capital / df.iloc[0]['Close']) * price
                equity_curve.append({
                    "Date": date,
                    "策略資產淨值": current_portfolio_value,
                    "買入持有基準": benchmark_value
                })
                continue

            # 2. 建倉與加倉機制
            if shares == 0:
                # 情境 A：曾停損離場，需滿足「二次探底：RSI背離>20且低點>MA5」
                if has_stopped_out:
                    if second_bottom_signal:
                        buy_budget = self.initial_capital * 0.4
                        buy_shares = int(buy_budget / price)
                        if buy_shares > 0 and cash >= buy_shares * price:
                            cost = buy_shares * price
                            cash -= cost
                            shares = buy_shares
                            avg_cost = price
                            took_profit = False
                            has_stopped_out = False
                            trades.append({
                                "日期": date_str, "動作": "建倉(40%)", "原因": f"🎯 二次探底建倉 (RSI背離達+{rsi_diff:.1f}點且低點>MA5)", 
                                "成交價": price, "股數": buy_shares, "損益": 0.0, "報酬率": "0.00%", "剩餘現金": cash
                            })

                # 情境 B：初始或正常建倉
                else:
                    if first_bottom_signal:
                        buy_budget = self.initial_capital * 0.4
                        buy_shares = int(buy_budget / price)
                        if buy_shares > 0 and cash >= buy_shares * price:
                            cost = buy_shares * price
                            cash -= cost
                            shares = buy_shares
                            avg_cost = price
                            took_profit = False
                            trades.append({
                                "日期": date_str, "動作": "建倉(40%)", "原因": "🛡️ 帶量止跌建倉 (站穩MA5/10+實體紅棒)", 
                                "成交價": price, "股數": buy_shares, "損益": 0.0, "報酬率": "0.00%", "剩餘現金": cash
                            })
                    elif right_side_signal:
                        buy_budget = self.initial_capital * 0.6
                        buy_shares = int(buy_budget / price)
                        if buy_shares > 0 and cash >= buy_shares * price:
                            cost = buy_shares * price
                            cash -= cost
                            shares = buy_shares
                            avg_cost = price
                            took_profit = False
                            trades.append({
                                "日期": date_str, "動作": "建倉(60%)", "原因": "🚀 均線結構扭轉 (突破MA20/金叉+帶量)", 
                                "成交價": price, "股數": buy_shares, "損益": 0.0, "報酬率": "0.00%", "剩餘現金": cash
                            })

            # 已有部位且尚有現金，觸發二次加碼打滿
            elif shares > 0 and cash >= (self.initial_capital * 0.1):
                if (right_side_signal or bullish_trend) and 35.0 <= temp <= 85.0:
                    add_shares = int(cash / price)
                    if add_shares > 0:
                        total_cost = (shares * avg_cost) + (add_shares * price)
                        shares += add_shares
                        avg_cost = total_cost / shares
                        cash -= (add_shares * price)
                        trades.append({
                            "日期": date_str, "動作": "加碼打滿", "原因": "🚀 主升段加碼", 
                            "成交價": price, "股數": add_shares, "損益": 0.0, "報酬率": "0.00%", "剩餘現金": cash
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
        
        return df_equity, df_trades


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
st.markdown("""
    <style>
        [data-testid="stSidebar"] > div:first-child {
            padding-bottom: 150px;
        }
    </style>
""", unsafe_allow_html=True)

if "db" not in st.session_state:
    st.session_state.db = load_db()

db = st.session_state.db
db.setdefault("stocks", {})
db.setdefault("stock_order", list(db["stocks"].keys()))

# ----------------- 側邊欄控制面板 -----------------
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
    else:
        st.sidebar.error("請先安裝拖曳庫：pip install streamlit-sortables")

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
        else:
            st.sidebar.error("❌ 請輸入標的代碼！")

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

# ----------------- 主介面：歷史區間回測 -----------------
st.title("📜 個股歷史策略模擬與回測系統")
st.caption("建倉邏輯已優化：導入左側帶量止跌（站穩MA5/10）與右側均線扭轉（突破MA20/金叉）雙重精準觸發。")

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
                df_equity, df_trades = backtester.run()

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

                    if not df_trades.empty and any(act in df_trades["動作"].values for act in ["全數賣出", "停損出場", "減倉50%"]):
                        closed_trades = df_trades[df_trades["動作"].isin(["全數賣出", "停損出場", "減倉50%"])]
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
                    b5.metric("出場/減倉勝率", f"{win_rate:.1f}%", f"共 {total_closed} 次操作")

                    st.markdown("---")
                    st.markdown("#### 📈 資產淨值成長曲線 vs 買入持有對照")

                    fig = go.Figure()
                    fig.add_trace(go.Scatter(x=df_equity.index, y=df_equity["策略資產淨值"], mode='lines', name='動態溫控策略', line=dict(color='#2962FF', width=2)))
                    fig.add_trace(go.Scatter(x=df_equity.index, y=df_equity["買入持有基準"], mode='lines', name='買入持有基準 (Buy & Hold)', line=dict(color='#B0BEC5', width=1.5, dash='dash')))

                    fig.update_layout(
                        title=f"{bt_symbol} 策略與大盤持有權益對比圖 ({start_str} ~ {end_str})",
                        xaxis_title="日期",
                        yaxis_title="資產總淨值",
                        hovermode="x unified",
                        template="plotly_white",
                        height=450
                    )
                    st.plotly_chart(fig, use_container_width=True)

                    st.markdown("---")
                    st.markdown("#### 📜 模擬交易進出場明細")
                    if not df_trades.empty:
                        st.dataframe(df_trades, use_container_width=True, hide_index=True)
                    else:
                        st.info("於此歷史區間內，未觸發任何買賣進場條件。")

        except Exception as ex:
            st.error(f"執行歷史回測失敗: {ex}")
