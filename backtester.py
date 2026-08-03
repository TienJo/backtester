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
# 1. 多重備援行情數據引擎 (修正快取參數)
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

# 正確快取：將所有查詢條件作為 Key，避免時間切換無效
@st.cache_data(ttl=300)
def cached_fetch_ohlc(symbol: str, start_date: str = None, end_date: str = None):
    return data_engine.fetch_ohlc(symbol, start_date, end_date)

# ==========================================
# 2. 動態溫控主升段策略引擎
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

    @staticmethod
    def evaluate_pre_trade_advice(df: pd.DataFrame, stock_info: dict, pos_summary: dict, target_capital: float) -> tuple[dict, list[dict]]:
        df_ind = TradingStrategyEngine.calculate_indicators(df)
        today = df_ind.iloc[-1]
        yesterday = df_ind.iloc[-2]
        prev_10 = df_ind.iloc[-10:-1]

        price = float(today['Close'])
        high = float(today['High'])
        low = float(today['Low'])
        yesterday_low = float(yesterday['Low'])
        volume = float(today['Volume'])
        ma5, ma10, ma20, ma60 = float(today['MA5']), float(today['MA10']), float(today['MA20']), float(today['MA60'])
        ma10_vol_prev = float(yesterday['MA10_Vol'])
        ma60_upward = float(today['MA60_Slope']) > 0
        rsi14 = float(today['RSI14'])
        temp = float(today['Temperature'])
        atr14 = float(today['ATR14'])
        high_20 = float(today['High_20']) if not np.isnan(today['High_20']) else float(today['High'])

        vol_ratio = volume / ma10_vol_prev if ma10_vol_prev > 0 else 0.0
        breakout_20_high = price > high_20 and vol_ratio >= 1.5
        price_low_10 = low < prev_10['Low'].min()
        rsi_not_low_10 = rsi14 > prev_10['RSI14'].min()
        rsi_bullish_div = price_low_10 and rsi_not_low_10 and rsi14 < 45

        metrics = {
            "Close": price, "High": high, "MA5": ma5, "MA10": ma10, "MA20": ma20, "MA60": ma60,
            "MA60_Trend": "向上走牛 ↗️" if ma60_upward else "走平/向下 ↘️",
            "RSI14": rsi14, "High_20": high_20,
            "Temperature": temp, "ATR14": atr14,
            "KLine_Date": str(df_ind.index[-1].strftime("%Y-%m-%d"))
        }

        tranches_held = pos_summary['tranches_held']
        avg_cost = pos_summary['avg_cost']
        
        target_shares_40 = int((target_capital * 0.4) / price) if price > 0 else 0
        target_shares_60 = int((target_capital * 0.6) / price) if price > 0 else 0

        trades = stock_info.get("trades", [])
        if trades:
            last_trade_date = trades[-1]["date"]
            if last_trade_date >= metrics["KLine_Date"]:
                return metrics, [{"type": "info", "action_code": "COMPLETED", "title": "🟢 當前 K 線進場訊號已執行完畢", "desc": f"您已於 {last_trade_date} 完成此輪交易操作。請耐心等待新交易日。（目前持倉：{tranches_held*10:.0f}%）"}]

        if tranches_held > 0:
            if temp > 95.0 and (price < yesterday_low or price < ma5):
                return metrics, [{"type": "warning", "action_code": "SELL_ALL", "title": "🔥 策略建議：沸點反轉，一鍵逃頂", "desc": f"當前溫度極度超買 ({temp:.1f}°C)，且價格轉弱跌破 MA5/前低！判定高檔反轉，請立即全數清倉鎖定最大獲利。"}]

            hard_stop_price = avg_cost * 0.92
            if price <= hard_stop_price:
                return metrics, [{"type": "error", "action_code": "STOP_LOSS", "title": "🚨 策略建議：跌穿成本 8%，絕對停損", "desc": f"當前淨值/價格 ({price:.2f}) 已跌破平均成本 8% 的絕對防守線 ({hard_stop_price:.2f})！請無條件全數斷頭出場，嚴格控制虧損。"}]
            
            if price < ma20 * 0.97:
                return metrics, [{"type": "error", "action_code": "STOP_LOSS", "title": "⚠️ 策略建議：跌破月線防守，提早停損", "desc": f"實體跌破 MA20 生命線達 3%，中期趨勢已遭破壞，建議先行清倉觀望，保留資金實力。"}]

        if tranches_held == 0:
            if rsi_bullish_div and temp < 35.0:
                return metrics, [{"type": "success", "action_code": "BUY_40", "title": "🎯 策略建議：【極寒抄底】投入 40% 資金", "desc": f"RSI 底背離確立，且系統處於冰點 (溫度 {temp:.1f}°C)！建議投入 40% 資金 (~${target_capital * 0.4:,.0f} 元，約 {target_shares_40:,} 股/份)。"}]
            elif breakout_20_high and 35.0 <= temp <= 80.0:
                return metrics, [{"type": "success", "action_code": "BUY_60", "title": "🚀 策略建議：【黃金突破】投入 60% 資金", "desc": f"強勢突破 20 日高點，動能充沛 (溫度 {temp:.1f}°C)！建議直接投入 60% 資金 (~${target_capital * 0.6:,.0f} 元，約 {target_shares_60:,} 股/份)。"}]
            else:
                return metrics, [{"type": "info", "action_code": "HOLD", "title": "💤 策略建議：保留現金，耐心等待", "desc": f"當前溫度 {temp:.1f}°C，尚未出現【極寒抄底】或【黃金突破】訊號，請將 100% 資金保留為現金觀望。"}]
        
        elif tranches_held > 0 and tranches_held < 9:
            if breakout_20_high and 35.0 <= temp <= 80.0:
                return metrics, [{"type": "success", "action_code": "BUY_REMAIN", "title": "🚀 策略建議：【黃金突破】打滿剩餘資金", "desc": f"趨勢強勢表態 (溫度 {temp:.1f}°C)！建議將剩餘的 60% 資金全數打滿，完成主升段重倉佈局。"}]
            else:
                return metrics, [{"type": "info", "action_code": "HOLD", "title": "🟢 策略建議：持股續抱，等待突破", "desc": f"目前持有部分底倉 (平均成本 {avg_cost:.2f})。未達 8% 停損與黃金突破條件，建議抱單觀望。"}]
                
        else:
            return metrics, [{"type": "info", "action_code": "HOLD", "title": "🟢 策略建議：滿倉獲利奔跑中", "desc": f"目前已達 100% 滿倉狀態 (平均成本 {avg_cost:.2f})！系統已啟動【絕對 8% 停損】與【沸點反轉逃頂】監控，請安心讓利潤奔跑。"}]

    @staticmethod
    def audit_post_trade(action_type: str, trade_price: float, trade_shares: int, pre_signals: list[dict], pre_pos: dict, metrics: dict) -> tuple[list[dict], list[dict]]:
        audit_items = []
        watchlist_items = []

        pre_tranches = pre_pos['tranches_held']
        avg_cost = pre_pos['avg_cost']
        advised_codes = [s.get('action_code') for s in pre_signals]

        if action_type in ["BUY", "ADD"]:
            if pre_tranches >= 9:
                audit_items.append({"level": "error", "title": "❌ 嚴重違規：突破 100% 滿倉上限", "detail": "您已處於滿倉狀態，禁止繼續借貸或動用額外資金追高買進。"})
            elif any(code in ['BUY_40', 'BUY_60', 'BUY_REMAIN'] for code in advised_codes):
                audit_items.append({"level": "success", "title": "✅ 依策略建議合規【精準重倉】", "detail": "您的買進完美契合【極寒抄底】或【黃金突破】的高勝率買點。"})
            else:
                audit_items.append({"level": "warning", "title": "⚠️ 頻繁進出：非策略性盲目買進", "detail": "當日並未出現明確的兩步建倉訊號，此操作將產生無謂的手續費與滑價風險。"})

        elif action_type == "SELL":
            if any(code in ['STOP_LOSS', 'SELL_ALL'] for code in advised_codes):
                audit_items.append({"level": "success", "title": "💯 嚴格風控：果斷執行逃頂或斷頭", "detail": "成功切斷虧損或於高檔鎖定利潤，完美落實交易紀律。"})
            else:
                audit_items.append({"level": "warning", "title": "⚠️ 過早離場：未達出場標準", "detail": "趨勢尚未破壞，亦未達 8% 停損線，提前賣出可能錯失主升段大行情。"})

        elif action_type == "NONE":
            if any(code in ['STOP_LOSS', 'SELL_ALL'] for code in advised_codes):
                audit_items.append({"level": "error", "title": "🚨 致命錯誤：抗單未停損 / 高檔未逃頂", "detail": "已跌破 8% 絕對死亡線或高檔沸點反轉，未賣出將面臨毀滅性虧損！"})
            else:
                audit_items.append({"level": "success", "title": "💯 紀律抱單：無效震盪不動作", "detail": "當日無操作，完美規避了頻繁交易產生的摩擦成本，展現極高抱單定力。"})

        ma20 = metrics['MA20']
        high_20 = metrics['High_20']
        
        if avg_cost > 0:
            hard_stop = avg_cost * 0.92
            watchlist_items.append({"title": "💀 絕對 8% 斷頭防守線", "value": f"{hard_stop:.4f}", "desc": f"只要收盤價跌破平均成本的 8% ({hard_stop:.4f})，請立刻無條件全數賣出！"})
        
        watchlist_items.append({"title": "🛡️ 技術均線止損價 (MA20 - 3%)", "value": f"{(ma20 * 0.97):.4f}", "desc": "價格跌穿此處表示中期趨勢破壞，提早認錯出場。"})
        watchlist_items.append({"title": "🚀 強勢突破觸發價 (20日高價)", "value": f"{high_20:.4f}", "desc": "若帶量突破此價位，即刻觸發 60% 資金的黃金突破重倉點。"})
        
        return audit_items, watchlist_items


# ==========================================
# 3. 歷史回測引擎 (修復重複當沖買賣與時間截取Bug)
# ==========================================
class StrategyBacktester:
    def __init__(self, df: pd.DataFrame, initial_capital: float = 200000.0, start_date: str = None, end_date: str = None):
        # 先計算好全量數據的技術指標
        df_calc = TradingStrategyEngine.calculate_indicators(df)
        
        # 截取用戶指定的歷史區間進行回測（確保包含前面60天計算指標）
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
        
        trades = []
        equity_curve = []
        
        for i in range(len(df)):
            if i < 10:  # 確保有足夠的前10天資料計算背離
                continue
                
            date = df.index[i]
            date_str = date.strftime("%Y-%m-%d")
            today = df.iloc[i]
            yesterday = df.iloc[i-1]
            prev_10 = df.iloc[i-10:i]
            
            price = float(today['Close'])
            high = float(today['High'])
            low = float(today['Low'])
            volume = float(today['Volume'])
            
            ma5 = float(today['MA5']) if not np.isnan(today['MA5']) else price
            ma20 = float(today['MA20']) if not np.isnan(today['MA20']) else price
            rsi14 = float(today['RSI14']) if not np.isnan(today['RSI14']) else 50.0
            temp = float(today['Temperature']) if not np.isnan(today['Temperature']) else 50.0
            high_20 = float(today['High_20']) if not np.isnan(today['High_20']) else high
            ma10_vol_prev = float(yesterday['MA10_Vol']) if not np.isnan(yesterday['MA10_Vol']) else volume
            yesterday_low = float(yesterday['Low'])
            
            vol_ratio = volume / ma10_vol_prev if ma10_vol_prev > 0 else 0.0
            breakout_20_high = (price > high_20) and (vol_ratio >= 1.5)
            price_low_10 = low < prev_10['Low'].min()
            rsi_not_low_10 = rsi14 > prev_10['RSI14'].min()
            rsi_bullish_div = price_low_10 and rsi_not_low_10 and (rsi14 < 45)
            
            # 1. 出場清倉機制 (賣出後必須 continue，防止當天被立刻買回)
            sold_today = False
            if shares > 0:
                hard_stop_price = avg_cost * 0.92
                
                # 沸點逃頂
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
                    sold_today = True
                    
                # 8% 絕對停損
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
                    sold_today = True
                    
                # 月線破位停損
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
                    sold_today = True

            # 若今日已賣出，直接進入下一天，防止當天剛賣完立刻又符合進場條件買回
            if sold_today:
                current_portfolio_value = cash
                benchmark_value = (self.initial_capital / df.iloc[0]['Close']) * price
                equity_curve.append({
                    "Date": date,
                    "策略資產淨值": current_portfolio_value,
                    "買入持有基準": benchmark_value
                })
                continue

            # 2. 進場建倉與加倉檢查
            if shares == 0:
                if rsi_bullish_div and temp < 35.0:
                    buy_budget = self.initial_capital * 0.4
                    buy_shares = int(buy_budget / price)
                    if buy_shares > 0 and cash >= buy_shares * price:
                        cost = buy_shares * price
                        cash -= cost
                        shares = buy_shares
                        avg_cost = price
                        trades.append({
                            "日期": date_str, "動作": "建倉(40%)", "原因": "🎯 極寒抄底", 
                            "成交價": price, "股數": buy_shares, "損益": 0.0, "報酬率": "0.00%", "剩餘現金": cash
                        })
                elif breakout_20_high and 35.0 <= temp <= 80.0:
                    buy_budget = self.initial_capital * 0.6
                    buy_shares = int(buy_budget / price)
                    if buy_shares > 0 and cash >= buy_shares * price:
                        cost = buy_shares * price
                        cash -= cost
                        shares = buy_shares
                        avg_cost = price
                        trades.append({
                            "日期": date_str, "動作": "建倉(60%)", "原因": "🚀 黃金突破", 
                            "成交價": price, "股數": buy_shares, "損益": 0.0, "報酬率": "0.00%", "剩餘現金": cash
                        })

            elif shares > 0 and cash > (self.initial_capital * 0.1):
                if breakout_20_high and 35.0 <= temp <= 80.0:
                    buy_shares = int(cash / price)
                    if buy_shares > 0:
                        total_cost = (shares * avg_cost) + (buy_shares * price)
                        shares += buy_shares
                        avg_cost = total_cost / shares
                        cash -= (buy_shares * price)
                        trades.append({
                            "日期": date_str, "動作": "加倉打滿", "原因": "🚀 突破加碼", 
                            "成交價": price, "股數": buy_shares, "損益": 0.0, "報酬率": "0.00%", "剩餘現金": cash
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
        "stock_order": ["2330.TW", "561160"],
        "stocks": {
            "2330.TW": {
                "symbol": "2330.TW", "name": "台積電",
                "target_capital": 200000.0, "trades": [],
                "peak_price_since_entry": 0.0, "peak_unrealized_pnl": 0.0, "last_operated_date": ""
            },
            "561160": {
                "symbol": "561160", "name": "富國電池ETF",
                "target_capital": 200000.0, "trades": [],
                "peak_price_since_entry": 0.0, "peak_unrealized_pnl": 0.0, "last_operated_date": ""
            }
        }
    }

    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

            if "stocks" in data:
                for s_key, s_val in data["stocks"].items():
                    s_val.setdefault("target_capital", data.get("total_capital", 200000.0))
                    s_val.setdefault("last_operated_date", "")
                
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
                            "target_capital": 200000.0, "trades": [],
                            "peak_price_since_entry": 0.0, "peak_unrealized_pnl": 0.0, "last_operated_date": ""
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

def compute_position_summary(trades: list, current_price: float, target_capital: float) -> dict:
    tranche_budget = target_capital * 0.10
    total_shares = 0
    total_cost = 0.0
    realized_pnl = 0.0

    for t in trades:
        t_type = t['type']
        t_price = float(t['price'])
        t_shares = int(t['shares'])
        t_amount = t_price * t_shares

        if t_type == "BUY":
            total_shares += t_shares
            total_cost += t_amount
        elif t_type == "SELL":
            if total_shares > 0:
                avg_cost_before = total_cost / total_shares
                sold_cost = avg_cost_before * t_shares
                total_shares = max(0, total_shares - t_shares)
                total_cost = max(0.0, total_cost - sold_cost)
                realized_pnl += (t_amount - sold_cost)

    avg_cost = total_cost / total_shares if total_shares > 0 else 0.0
    unrealized_pnl = (current_price * total_shares - total_cost) if total_shares > 0 else 0.0
    tranches_held = round(total_cost / tranche_budget, 1) if tranche_budget > 0 else 0.0

    return {
        "total_shares": total_shares,
        "total_cost": total_cost,
        "avg_cost": avg_cost,
        "unrealized_pnl": unrealized_pnl,
        "realized_pnl": realized_pnl,
        "tranches_held": tranches_held,
        "tranche_budget": tranche_budget
    }


# ==========================================
# 5. Streamlit 視覺化 GUI 主介面
# ==========================================
st.set_page_config(page_title="主升段最大化交易 App", layout="wide", page_icon="📈")
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

# ----------------- 側邊欄設定 -----------------
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

selected_option = st.sidebar.selectbox(
    "🔍 選擇當前操作標的",
    options=list(stock_options.keys()),
    format_func=lambda x: stock_options[x] if x in stock_options else x
)

active_stock = selected_option

if active_stock and active_stock in db["stocks"]:
    curr_stock = db["stocks"][active_stock]
    st.sidebar.markdown("---")
    st.sidebar.subheader(f"💰 {curr_stock['name']} 滿倉預算設定")
    
    target_cap = st.sidebar.number_input(
        f"全額 100% 滿倉資金 (元)",
        min_value=10000.0, max_value=100000000.0,
        value=float(curr_stock.get("target_capital", 200000.0)), step=50000.0
    )
    if target_cap != curr_stock.get("target_capital"):
        curr_stock["target_capital"] = target_cap
        save_db(db)
        st.rerun()

    st.sidebar.caption(f"💡 絕對 8% 極限虧損容忍 = ${target_cap * 0.08:,.0f} 元")

st.sidebar.markdown("---")

with st.sidebar.expander("➕ 新增標的", expanded=False):
    new_sym = st.text_input("代碼 (台股 2330.TW / ETF 561160 / 基金 013396)", "").strip().upper()
    new_name = st.text_input("標的名稱 (選填，若空白將自動使用代碼)", "").strip()
    new_cap = st.number_input("為此標的設定獨立資本上限 (元)", min_value=10000.0, max_value=100000000.0, value=200000.0, step=50000.0)
    
    if st.button("確認新增"):
        if new_sym:
            final_name = new_name if new_name else new_sym
            if new_sym not in db["stocks"]:
                db["stocks"][new_sym] = {
                    "symbol": new_sym, "name": final_name, "target_capital": new_cap, "trades": [],
                    "peak_price_since_entry": 0.0, "peak_unrealized_pnl": 0.0, "last_operated_date": ""
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

# ----------------- 右側主頁面 -----------------
st.title("📈 動態溫控主升段最大化 App (滿倉八趴防守版)")

if not active_stock or active_stock not in db["stocks"]:
    st.info("請先在左側邊欄新增自選標的。")
    st.stop()

stock_info = db["stocks"][active_stock]
stock_target_capital = float(stock_info.get("target_capital", 200000.0))

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📊 每日決策與交易對帳", 
    "📐 技術指標與標的溫度", 
    "🔍 標的溫度一鍵掃描",
    "📈 總盈利綜合分析",
    "📜 歷史區間回測"
])

# Tab 1: 主介面
with tab1:
    col_t1, col_t2 = st.columns([3, 1])
    with col_t1:
        st.subheader(f"{stock_info['name']} ({stock_info['symbol']})")

    try:
        df_kline, source_used = cached_fetch_ohlc(stock_info['symbol'])
        col_t2.caption(f"🟢 行情來源: {source_used}")
        pos_summary = compute_position_summary(stock_info.get('trades', []), df_kline.iloc[-1]['Close'], stock_target_capital)
        metrics, pre_signals = TradingStrategyEngine.evaluate_pre_trade_advice(df_kline, stock_info, pos_summary, stock_target_capital)

        m1, m2, m3, m4, m5, m6 = st.columns(6)
        with m1:
            st.metric("最新價/淨值", f"{metrics['Close']:.4f}" if metrics['Close'] < 10 else f"{metrics['Close']:.2f}")
            st.caption(f"🕒 更新時間：{metrics.get('KLine_Date', '最新交易日')} (盤中延遲)")
        
        temp_val = metrics['Temperature']
        temp_color = "🔥" if temp_val > 95 else ("🌡️" if temp_val >= 35 else "❄️")
        m2.metric("標的動態溫度", f"{temp_color} {temp_val:.1f}°C")
        
        m3.metric("持倉配置進度", f"{pos_summary['tranches_held']*10:.0f}% / 100%")
        m4.metric("平均持倉成本", f"{pos_summary['avg_cost']:.4f}" if pos_summary['avg_cost'] < 10 else f"{pos_summary['avg_cost']:.2f}" if pos_summary['total_shares'] > 0 else "未開倉")
        u_pnl_pct = (pos_summary['unrealized_pnl'] / pos_summary['total_cost'] * 100.0) if pos_summary['total_cost'] > 0 else 0.0
        m5.metric("未實現損益", f"${pos_summary['unrealized_pnl']:,.0f}", delta=f"{u_pnl_pct:.2f}%" if pos_summary['total_shares'] > 0 else "0%")
        m6.metric("持有總份額", f"{pos_summary['total_shares']:,}")

        st.markdown("---")

        today_date_str = datetime.now().strftime("%Y-%m-%d")
        is_weekend = datetime.now().weekday() >= 5
        last_op_date = stock_info.get("last_operated_date", "")

        if is_weekend:
            st.info("☕ **今天為週末休市時間 (股市休市)**\n行情暫停更新，系統已自動鎖定進場建議與表單填寫。請於下一個交易日開盤後再進行對帳。")
        elif last_op_date == today_date_str:
            st.success(f"### ✅ 今日 ({today_date_str}) 操作已完成並寫入數據庫\n系統已記錄今日交易對帳。在下一交易日開盤新 K 線產生前，將不再重複顯示買賣建議。")
        else:
            st.markdown("### 🎯 第一次分析：主升段溫控進場指引")
            for sig in pre_signals:
                if sig['type'] == 'success':
                    st.success(f"**{sig['title']}**\n\n{sig['desc']}")
                elif sig['type'] == 'warning':
                    st.warning(f"**{sig['title']}**\n\n{sig['desc']}")
                elif sig['type'] == 'error':
                    st.error(f"**{sig['title']}**\n\n{sig['desc']}")
                else:
                    st.info(f"**{sig['title']}**\n\n{sig['desc']}")

        st.markdown("---")

        st.markdown("### ✏️ 紀錄今日實際執行之交易")
        with st.form("daily_trade_form"):
            col_f1, col_f2, col_f3 = st.columns(3)
            with col_f1:
                action_choice = st.selectbox("當日操作類型", ["今日無操作 / 續抱", "買進 / 加倉", "賣出 / 減倉"], disabled=is_weekend)
            with col_f2:
                trade_price = st.number_input("成交單價/淨值", min_value=0.0, value=float(metrics['Close']), format="%.4f", disabled=is_weekend)
            with col_f3:
                trade_shares = st.number_input("成交數量 (股/份)", min_value=0, value=1000, step=100, disabled=is_weekend)

            trade_note = st.text_input("交易備註 (選填，例如：黃金突破重倉打滿)", "", disabled=is_weekend)
            submit_trade = st.form_submit_button("💾 記錄交易並進行二次合規分析", type="primary", disabled=is_weekend)

            if submit_trade and not is_weekend:
                if action_choice != "今日無操作 / 續抱" and trade_shares > 0:
                    t_type = "BUY" if "買進" in action_choice or "加倉" in action_choice else "SELL"
                    new_trade = {
                        "date": today_date_str, "type": t_type, "price": trade_price,
                        "shares": trade_shares, "note": trade_note
                    }
                    stock_info.setdefault("trades", []).append(new_trade)
                    stock_info["last_operated_date"] = today_date_str
                    save_db(db)
                    st.session_state[f"last_action_{active_stock}"] = t_type
                    st.session_state[f"last_price_{active_stock}"] = trade_price
                    st.session_state[f"last_shares_{active_stock}"] = trade_shares
                else:
                    stock_info["last_operated_date"] = today_date_str
                    save_db(db)
                    st.session_state[f"last_action_{active_stock}"] = "NONE"
                    st.session_state[f"last_price_{active_stock}"] = metrics['Close']
                    st.session_state[f"last_shares_{active_stock}"] = 0
                st.rerun()

        st.markdown("### 🔍 第二次分析：交易紀律回測與防守價位")

        stock_action_key = f"last_action_{active_stock}"
        stock_price_key = f"last_price_{active_stock}"
        stock_shares_key = f"last_shares_{active_stock}"

        last_act = st.session_state.get(stock_action_key, "NONE")
        last_p = st.session_state.get(stock_price_key, metrics['Close'])
        last_s = st.session_state.get(stock_shares_key, 0)

        trades_list = stock_info.get("trades", [])
        if last_act != "NONE" and trades_list:
            trades_before = trades_list[:-1]
            pos_summary_before = compute_position_summary(trades_before, metrics['Close'], stock_target_capital)
            _, pre_signals_before = TradingStrategyEngine.evaluate_pre_trade_advice(df_kline, stock_info, pos_summary_before, stock_target_capital)
        else:
            pos_summary_before = pos_summary
            pre_signals_before = pre_signals

        audit_results, watchlist_results = TradingStrategyEngine.audit_post_trade(
            last_act, last_p, last_s, pre_signals_before, pos_summary_before, metrics
        )

        c_audit, c_watch = st.columns([1, 1])

        with c_audit:
            st.markdown("#### 💯 今日紀律合規診斷")
            for item in audit_results:
                if item['level'] == 'success':
                    st.success(f"**{item['title']}**\n\n{item['detail']}")
                elif item['level'] == 'warning':
                    st.warning(f"**{item['title']}**\n\n{item['detail']}")
                else:
                    st.error(f"**{item['title']}**\n\n{item['detail']}")

        with c_watch:
            st.markdown("#### 📌 短線重要防守與進攻價位")
            for item in watchlist_results:
                if "💀" in item['title']:
                    st.error(f"**{item['title']}**: `{item['value']}`\n\n↳ {item['desc']}")
                else:
                    st.info(f"**{item['title']}**: `{item['value']}`\n\n↳ {item['desc']}")

    except Exception as e:
        st.error(f"即時數據讀取失敗: {e}")

    st.markdown("---")
    st.markdown("### 📜 歷史交易紀錄")
    if stock_info.get("trades"):
        trade_df = pd.DataFrame(stock_info["trades"])
        trade_df['金額'] = trade_df['price'] * trade_df['shares']
        trade_df['類型'] = trade_df['type'].map({"BUY": "買進/加倉", "SELL": "賣出/減碼"})
        st.dataframe(trade_df[['date', '類型', 'price', 'shares', '金額', 'note']], use_container_width=True)
        if st.button("🗑️ 清空該標的所有交易紀錄"):
            stock_info["trades"] = []
            stock_info["peak_price_since_entry"] = 0.0
            stock_info["peak_unrealized_pnl"] = 0.0
            stock_info["last_operated_date"] = ""
            if stock_action_key in st.session_state:
                del st.session_state[stock_action_key]
            save_db(db)
            st.success("交易紀錄已重置！")
            st.rerun()
    else:
        st.caption("暫無歷史交易紀錄。")

# Tab 2: 指標詳情與溫度拆解
with tab2:
    st.markdown("### 📐 當日技術指標與標的動態溫度")
    try:
        df_kline, _ = cached_fetch_ohlc(stock_info['symbol'])
        metrics, _ = TradingStrategyEngine.evaluate_pre_trade_advice(df_kline, stock_info, pos_summary, stock_target_capital)
        
        t_col1, t_col2 = st.columns([1, 2])
        with t_col1:
            st.metric("綜合溫度 (T)", f"{metrics['Temperature']:.1f} °C")
            if metrics['Temperature'] > 95:
                st.error("🔥 **沸點超買區 (T > 95)**\n隨時提防反轉跌破 MA5，請準備一鍵獲利了結。")
            elif metrics['Temperature'] >= 35:
                st.success("🟢 **黃金趨勢區 (35~95)**\n趨勢動能強勁，若突破 20 日高點可重倉買進。")
            else:
                st.warning("❄️ **冰點沉寂區 (T < 35)**\n走勢極弱，切勿追高，僅能觀察 RSI 抄底訊號。")

        with t_col2:
            ind_df = pd.DataFrame([
                {"指標項目": "20日漲幅分位 (權重 24%)", "當前數值": f"{metrics['Temperature']:.1f}°C 綜合對映"},
                {"指標項目": "20日區間相對位置 (權重 22%)", "當前數值": f"現價 {metrics['Close']:.2f} / 20日高點 {metrics['High_20']:.2f}"},
                {"指標項目": "均線多頭結構 (權重 22%)", "當前數值": f"MA20: {metrics['MA20']:.2f} | MA60: {metrics['MA60']:.2f} ({metrics['MA60_Trend']})"},
                {"指標項目": "RSI 14 擺盪指標 (權重 18%)", "當前數值": f"{metrics['RSI14']:.2f}"},
                {"指標項目": "14日真實波幅 (ATR14)", "當前數值": f"{metrics['ATR14']:.4f}"},
            ])
            st.dataframe(ind_df, use_container_width=True, hide_index=True)
    except Exception as e:
        st.error(f"技術指標載入失敗: {e}")

# Tab 3: 全自選掃描
with tab3:
    st.markdown("### 🔍 每日全自選標的進場點與溫度掃描")
    if st.button("🚀 開始自動掃描", type="primary"):
        results = []
        progress_bar = st.progress(0)
        ordered_keys = [k for k in db.get("stock_order", []) if k in db.get("stocks", {})]
        
        for idx, sym in enumerate(ordered_keys):
            s_data = db["stocks"][sym]
            try:
                s_cap = float(s_data.get("target_capital", 200000.0))
                df_s, src = cached_fetch_ohlc(sym)
                p_sum = compute_position_summary(s_data.get('trades', []), df_s.iloc[-1]['Close'], s_cap)
                m, sigs = TradingStrategyEngine.evaluate_pre_trade_advice(df_s, s_data, p_sum, s_cap)
                sig_summary = " | ".join([s['title'] for s in sigs])
                results.append({
                    "代號": sym, "名稱": s_data['name'], 
                    "最新價/淨值": f"{m['Close']:.4f}" if m['Close'] < 10 else f"{m['Close']:.2f}",
                    "標的溫度": f"{m['Temperature']:.1f}°C",
                    "持倉進度": f"{p_sum['tranches_held']*10:.0f}%",
                    "RSI 14": f"{m['RSI14']:.1f}",
                    "當日策略指引": sig_summary,
                    "數據源": src
                })
            except Exception as ex:
                results.append({"代號": sym, "名稱": s_data['name'], "當日策略指引": f"抓取失敗: {ex}"})
            progress_bar.progress((idx + 1) / len(ordered_keys))

        res_df = pd.DataFrame(results)
        st.dataframe(res_df, use_container_width=True, hide_index=True)

# Tab 4: 總盈利與健康度綜合分析
with tab4:
    st.markdown("### 📊 全帳戶獲利與防守狀態診斷")
    
    tw_stocks = {}
    cn_stocks = {}
    for k, v in db.get("stocks", {}).items():
        if k.endswith(".TW") or k.endswith(".TWO"):
            tw_stocks[k] = v
        else:
            cn_stocks[k] = v

    sub_tab_tw, sub_tab_cn = st.tabs(["🇹🇼 台股資產 (NTD $)", "🇨🇳 陸股/基金資產 (RMB ¥)"])

    today_dt = datetime.now()
    week_ago_dt = today_dt - timedelta(days=7)
    month_ago_dt = today_dt - timedelta(days=30)

    def render_market_analysis(market_stocks: dict, currency_symbol: str, currency_name: str):
        if not market_stocks:
            st.info(f"目前無 {currency_name} 相關自選標的。")
            return

        total_cost = 0.0
        total_unrealized = 0.0
        total_realized = 0.0
        weekly_pnl = 0.0
        monthly_pnl = 0.0
        total_trades = 0
        winning_trades = 0
        health_alerts = []
        score = 80

        holding_details = []

        with st.spinner(f"正在計算 {currency_name} 資產損益與統計個股狀態..."):
            for sym_k, s_info in market_stocks.items():
                cap = float(s_info.get("target_capital", 200000.0))
                trades = s_info.get("trades", [])
                total_trades += len(trades)

                try:
                    df_s, _ = cached_fetch_ohlc(sym_k)
                    df_s_ind = TradingStrategyEngine.calculate_indicators(df_s)
                    curr_price = float(df_s_ind.iloc[-1]['Close'])
                    ma20 = float(df_s_ind.iloc[-1]['MA20']) if 'MA20' in df_s_ind else curr_price
                    temp_val = float(df_s_ind.iloc[-1]['Temperature'])
                except Exception:
                    curr_price = 0.0
                    ma20 = 0.0
                    temp_val = 50.0

                pos = compute_position_summary(trades, curr_price, cap)
                total_cost += pos['total_cost']
                total_unrealized += pos['unrealized_pnl']
                total_realized += pos['realized_pnl']

                for t in trades:
                    t_dt = datetime.strptime(t['date'], "%Y-%m-%d")
                    if t['type'] == 'SELL' and pos['avg_cost'] > 0 and float(t['price']) > pos['avg_cost']:
                        winning_trades += 1
                    if t_dt >= week_ago_dt:
                        weekly_pnl += (pos['unrealized_pnl'] if t['type'] == 'BUY' else 0)
                    if t_dt >= month_ago_dt:
                        monthly_pnl += (pos['unrealized_pnl'] if t['type'] == 'BUY' else 0)

                stock_status = "🟢 正常持倉/空倉"
                
                hard_stop_price = pos['avg_cost'] * 0.92 if pos['avg_cost'] > 0 else 0
                if pos['total_shares'] > 0 and curr_price <= hard_stop_price:
                    health_alerts.append(f"💀 **{s_info['name']} ({sym_k})**：已跌破成本 8%，請務必即刻斷頭清倉！")
                    score -= 20
                    stock_status = "💀 觸及 8% 斷頭線"
                elif pos['total_shares'] > 0 and curr_price < ma20 * 0.97:
                    health_alerts.append(f"🚨 **{s_info['name']} ({sym_k})**：已跌破 MA20 防守線 3%，中期轉弱建議出場！")
                    score -= 10
                    stock_status = "🚨 跌破月線防守"

                if pos['tranches_held'] > 10:
                    health_alerts.append(f"⚠️ **{s_info['name']} ({sym_k})**：當前持倉已破 100% 滿倉上限！")
                    score -= 10
                    stock_status = "⚠️ 倉位超限"

                u_pnl_rate = (pos['unrealized_pnl'] / pos['total_cost'] * 100) if pos['total_cost'] > 0 else 0.0

                holding_details.append({
                    "代號": sym_k, "標的名稱": s_info['name'],
                    "目前價/淨值": f"{curr_price:.4f}" if curr_price < 10 else f"{curr_price:.2f}",
                    "標的溫度": f"{temp_val:.1f}°C",
                    "持倉配置": f"{pos['tranches_held']*10:.0f}%",
                    "持倉成本": f"{currency_symbol}{pos['total_cost']:,.0f}",
                    "未實現損益": f"{currency_symbol}{pos['unrealized_pnl']:,.0f}",
                    "帳面報酬率": f"{u_pnl_rate:+.2f}%" if pos['total_shares'] > 0 else "0.00%",
                    "健康狀態": stock_status
                })

        total_pnl = total_unrealized + total_realized
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0

        if win_rate >= 60:
            score += 10
        elif win_rate < 40 and total_trades > 2:
            score -= 10

        score = max(0, min(100, score))

        k1, k2, k3, k4 = st.columns(4)
        k1.metric(f"💼 {currency_name} 總投入成本", f"{currency_symbol}{total_cost:,.0f}")
        k2.metric(f"📈 累計總收益", f"{currency_symbol}{total_pnl:,.0f}", delta=f"{(total_pnl / total_cost * 100):.2f}%" if total_cost > 0 else "0%")
        k3.metric("📅 週收益 (近7日)", f"{currency_symbol}{weekly_pnl:,.0f}")
        k4.metric("🗓️ 月收益 (近30日)", f"{currency_symbol}{monthly_pnl:,.0f}")

        st.markdown("---")

        col_h1, col_h2 = st.columns([1, 1])
        with col_h1:
            st.markdown(f"#### 🎯 {currency_name} 交易勝率與風控評分")
            sc1, sc2 = st.columns(2)
            sc1.metric("🎯 交易勝率", f"{win_rate:.1f}%")
            sc2.metric("🏆 操作紀律評分", f"{score} / 100 分")

            if score >= 85:
                st.success("🌟 **評價：優秀**！完全落實兩步建倉與斷頭紀律，資金效益極佳。")
            elif score >= 70:
                st.info("👍 **評價：良好**。整體持倉健康，請持續緊盯 8% 防守線。")
            else:
                st.error("🚨 **評價：需要修正**！出現死扛不賣或過度重倉，請盡快執行停損。")

        with col_h2:
            st.markdown(f"#### 🩺 {currency_name} 持倉風險診斷")
            if health_alerts:
                for alert in health_alerts:
                    st.warning(alert)
            else:
                st.success(f"✅ **{currency_name} 持倉狀態極為健康**！無觸發 8% 斷頭或超限加碼情形。")

        st.markdown("---")
        st.markdown(f"#### 📋 {currency_name} 個股與基金投資明細")
        if holding_details:
            details_df = pd.DataFrame(holding_details)
            st.dataframe(details_df, use_container_width=True, hide_index=True)

    with sub_tab_tw:
        render_market_analysis(tw_stocks, "NTD $", "台股")

    with sub_tab_cn:
        render_market_analysis(cn_stocks, "RMB ¥", "陸股/基金")

# ==========================================
# Tab 5: 歷史區間回測 (精準時間切割與邏輯修復)
# ==========================================
with tab5:
    st.markdown("### 📜 個股歷史策略模擬與回測系統")
    st.caption("請選擇回測區間並按下按鈕，系統將自動進行精準模擬。")

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
        bt_capital = st.number_input("初始回測資金", min_value=10000.0, max_value=10000000.0, value=200000.0, step=50000.0)

    if st.button("🚀 開始歷史回測模擬", type="primary"):
        start_str = bt_start.strftime("%Y-%m-%d")
        end_str = bt_end.strftime("%Y-%m-%d")

        with st.spinner(f"正在擷取 {bt_symbol} 行情數據與執行回測..."):
            try:
                # 為了確保技術指標（如MA60）計算準確，向前多抓60天數據
                fetch_start = (bt_start - timedelta(days=120)).strftime("%Y-%m-%d")
                df_bt_raw, src_bt = cached_fetch_ohlc(bt_symbol, start_date=fetch_start, end_date=end_str)
                
                if df_bt_raw.empty or len(df_bt_raw) < 30:
                    st.error("歷史數據不足，無法執行回測。請確認代碼或重新選擇區間。")
                else:
                    # 執行精準時間區間回測
                    backtester = StrategyBacktester(df_bt_raw, initial_capital=bt_capital, start_date=start_str, end_date=end_str)
                    df_equity, df_trades = backtester.run()

                    if df_equity.empty:
                        st.warning("選定日期區間內沒有可用的交易日行情數據。")
                    else:
                        final_strat_val = df_equity["策略資產淨值"].iloc[-1]
                        final_bench_val = df_equity["買入持有基準"].iloc[-1]
                        
                        strat_return = ((final_strat_val - bt_capital) / bt_capital) * 100.0
                        bench_return = ((final_bench_val - bt_capital) / bt_capital) * 100.0

                        # MDD 計算
                        equity_series = df_equity["策略資產淨值"]
                        cummax = equity_series.cummax()
                        drawdown = (equity_series - cummax) / cummax
                        max_drawdown = drawdown.min() * 100.0 if not drawdown.empty else 0.0

                        # 出場交易勝率
                        if not df_trades.empty and ("全數賣出" in df_trades["動作"].values or "停損出場" in df_trades["動作"].values):
                            closed_trades = df_trades[df_trades["動作"].isin(["全數賣出", "停損出場"])]
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
                        b5.metric("出場勝率", f"{win_rate:.1f}%", f"共 {total_closed} 次出場")

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
