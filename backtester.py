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

        df = pd.DataFrame()
        src = "未知數據源"

        try:
            df = self._fetch_eastmoney(symbol, clean_code, start_date, end_date)
            if not df.empty and len(df) >= 10:
                src = "東方財富 (EastMoney)"
        except Exception:
            pass

        if df.empty and clean_code.isdigit() and len(clean_code) == 6:
            try:
                df = self._fetch_eastmoney_fund(clean_code)
                if not df.empty and len(df) >= 10:
                    src = "天天基金 (Tiantian Fund)"
            except Exception:
                pass

        if df.empty:
            try:
                df = self._fetch_tencent(symbol, clean_code)
                if not df.empty and len(df) >= 10:
                    src = "騰訊財經 (Tencent)"
            except Exception:
                pass

        if df.empty:
            try:
                df = self._fetch_yfinance(symbol, start_date, end_date)
                if not df.empty and len(df) >= 10:
                    src = "yfinance (備用)"
            except Exception:
                pass

        if df.empty:
            raise ValueError(f"無法獲取 {symbol} 行情數據，請確認代碼是否正確。")

        # 針對台股標的試圖獲取證交所 (TWSE) 盤中即時股價
        if symbol.endswith(".TW") or symbol.endswith(".TWO"):
            try:
                rt_data = self._fetch_twse_realtime(clean_code, symbol.endswith(".TWO"))
                if rt_data:
                    rt_date = pd.to_datetime(rt_data['Date'])
                    if rt_date in df.index:
                        df.loc[rt_date, 'Close'] = rt_data['Close']
                        df.loc[rt_date, 'High'] = max(df.loc[rt_date, 'High'], rt_data['High'])
                        df.loc[rt_date, 'Low'] = min(df.loc[rt_date, 'Low'], rt_data['Low'])
                    else:
                        new_row = pd.DataFrame([{
                            "Open": rt_data['Open'], "High": rt_data['High'],
                            "Low": rt_data['Low'], "Close": rt_data['Close'],
                            "Volume": rt_data['Volume']
                        }], index=[rt_date])
                        df = pd.concat([df, new_row])
                    src += " + 證交所(TWSE)盤中即時"
            except Exception:
                pass

        return df, src

    def _fetch_twse_realtime(self, clean_code: str, is_otc: bool = False) -> dict:
        prefix = "otc" if is_otc else "tse"
        url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch={prefix}_{clean_code}.tw"
        resp = requests.get(url, timeout=3)
        data = resp.json()
        
        if "msgArray" in data and len(data["msgArray"]) > 0:
            info = data["msgArray"][0]
            close_p = info.get("z")
            if not close_p or close_p == "-":
                close_p = info.get("b", "").split("_")[0] or info.get("y")
            
            if close_p and close_p != "-":
                open_p = float(info.get("o", close_p) if info.get("o") != "-" else close_p)
                high_p = float(info.get("h", close_p) if info.get("h") != "-" else close_p)
                low_p = float(info.get("l", close_p) if info.get("l") != "-" else close_p)
                vol = float(info.get("v", 0) if info.get("v") != "-" else 0)
                d_str = info.get("d", datetime.now().strftime("%Y%m%d"))
                formatted_date = f"{d_str[:4]}-{d_str[4:6]}-{d_str[6:]}"

                return {
                    "Date": formatted_date,
                    "Open": open_p,
                    "High": high_p,
                    "Low": low_p,
                    "Close": float(close_p),
                    "Volume": vol
                }
        return None

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
                
                if not df.empty and len(df) >= 10:
                    df = df.reset_index()
                    df['Date'] = pd.to_datetime(df['Date']).dt.tz_localize(None)
                    df.set_index('Date', inplace=True)
                    return df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()
            except Exception:
                pass
            time.sleep(1.0 * (attempt + 1))

        return pd.DataFrame()

data_engine = MultiSourceMarketData()

@st.cache_data(ttl=15)
def cached_fetch_ohlc(symbol: str, start_date: str = None, end_date: str = None):
    return data_engine.fetch_ohlc(symbol, start_date, end_date)

# ==========================================
# 2. 技術指標與綜合策略分析引擎
# ==========================================
class TechnicalAnalysisEngine:
    @staticmethod
    def calculate_indicators(df: pd.DataFrame, display_start_date: str = None) -> pd.DataFrame:
        df = df.copy()
        
        # 1. 均線與布林通道計算
        df['MA5'] = df['Close'].rolling(5).mean()
        df['MA10'] = df['Close'].rolling(10).mean()
        df['MA20'] = df['Close'].rolling(20).mean()
        df['MA60'] = df['Close'].rolling(60).mean()

        std20 = df['Close'].rolling(20).std()
        df['BB_Upper'] = df['MA20'] + (2.0 * std20)
        df['BB_Lower'] = df['MA20'] - (2.0 * std20)
        df['BB_Bandwidth'] = (df['BB_Upper'] - df['BB_Lower']) / df['MA20']
        
        df['BB_Upper_5D_Diff'] = df['BB_Upper'].diff(5)
        df['MA20_Diff_1D'] = df['MA20'].diff(1)

        # 2. 量能與量比
        df['Vol_MA5'] = df['Volume'].rolling(5).mean()
        df['Daily_Vol_Ratio'] = df['Volume'] / df['Vol_MA5'].shift(1)
        df['Vol_Ratio_5D'] = df['Vol_MA5'] / df['Vol_MA5'].shift(5)

        # 3. RSI(14) 與變化量
        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        df['RSI14'] = 100 - (100 / (1 + rs))
        df['RSI_Diff_1D'] = df['RSI14'].diff(1)
        df['RSI_Diff_2D'] = df['RSI14'].diff(2)
        df['RSI_Diff_5D'] = df['RSI14'].diff(5)

        # 4. MACD 計算
        ema12 = df['Close'].ewm(span=12, adjust=False).mean()
        ema26 = df['Close'].ewm(span=26, adjust=False).mean()
        df['DIF'] = ema12 - ema26
        df['DEA'] = df['DIF'].ewm(span=9, adjust=False).mean()
        df['MACD_Hist'] = (df['DIF'] - df['DEA']) * 2

        # 5. 市場溫度 T 計算
        df['Ret20'] = df['Close'].pct_change(20)
        df['Score_Rank20'] = df['Ret20'].rolling(60).apply(
            lambda x: (pd.Series(x).rank(pct=True).iloc[-1] * 100) if len(x) > 0 else 50, raw=False
        )
        df['High_20'] = df['High'].shift(1).rolling(20).max()
        df['Low_20'] = df['Low'].shift(1).rolling(20).min()
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
        
        df['Temperature_MA10'] = df['Temperature'].rolling(10).mean()

        # 格局與詳細原因判定
        ma5_diff = df['MA5'].diff()
        df['Bull_5D'] = (df['Close'] > df['MA5']) & (ma5_diff > 0)
        df['Bear_5D'] = (df['Close'] < df['MA5']) & (ma5_diff < 0)

        ma20_diff = df['MA20'].diff()
        df['Bull_20D'] = (df['Close'] > df['MA20']) & (ma20_diff > 0) & (df['MA5'] > df['MA20'])
        df['Bear_20D'] = (df['Close'] < df['MA20']) & (ma20_diff < 0) & (df['MA5'] < df['MA20'])

        trend_reason_5d = []
        trend_reason_20d = []

        for i in range(len(df)):
            if i < 20:
                trend_reason_5d.append("數據累積中")
                trend_reason_20d.append("數據累積中")
                continue

            c = df['Close'].iloc[i]
            m5 = df['MA5'].iloc[i]
            m10 = df['MA10'].iloc[i]
            m20 = df['MA20'].iloc[i]
            m60 = df['MA60'].iloc[i]
            m5_d = ma5_diff.iloc[i]
            m20_d = ma20_diff.iloc[i]

            if c > m5 and m5_d > 0:
                trend_reason_5d.append(f"股價({c:.2f})站上MA5({m5:.2f})且5日線向上走揚，短線多頭掌控")
            elif c < m5 and m5_d < 0:
                trend_reason_5d.append(f"股價({c:.2f})跌破MA5({m5:.2f})且5日線向下彎頭，短線空頭佔優")
            else:
                trend_reason_5d.append(f"股價與MA5糾結，短線處於橫盤震盪走勢")

            if c > m20 and m20_d > 0 and m5 > m20:
                if m10 > m20 and m20 > m60:
                    trend_reason_20d.append("均線呈現標準多頭排列(MA5>MA10>MA20>MA60)，波段趨勢極為強勁")
                else:
                    trend_reason_20d.append(f"股價高於MA20({m20:.2f})，月線持續上揚且短均在高位，中線看多")
            elif c < m20 and m20_d < 0 and m5 < m20:
                if m10 < m20 and m20 < m60:
                    trend_reason_20d.append("均線呈現空頭排列(MA5<MA10<MA20<MA60)，中長線持續承壓")
                else:
                    trend_reason_20d.append(f"股價跌破MA20({m20:.2f})，月線走下坡且短均在下方，中線看空")
            else:
                trend_reason_20d.append("價格受限於MA20上下翻折，中線方向尚待進一步確立")

        df['Reason_5D'] = trend_reason_5d
        df['Reason_20D'] = trend_reason_20d

        # ----------------------------------------------------
        # 核心交易策略執行邏輯
        # ----------------------------------------------------
        action_list = []
        reason_list = []

        sub_start_dt = pd.to_datetime(display_start_date) if display_start_date else df.index[0]

        last_buy_index = -999
        in_position = False
        position_ratio = 0.0
        entry_mode = ""
        
        mode_bc_veto_active = False
        entry_day_low = 0.0
        entry_day_index = -999

        for i in range(len(df)):
            current_date = df.index[i]

            if current_date < sub_start_dt:
                in_position = False
                position_ratio = 0.0
                entry_mode = ""
                action_list.append("區間前(不交易)")
                reason_list.append("尚未進入設定的回測觀察時間區間")
                continue

            if i < 20:
                action_list.append("資料載入中")
                reason_list.append("計算指標所需日數不足20日，無法判定")
                continue

            close_p = df['Close'].iloc[i]
            open_p = df['Open'].iloc[i]
            high_p = df['High'].iloc[i]
            low_p = df['Low'].iloc[i]

            m5 = df['MA5'].iloc[i]
            m20 = df['MA20'].iloc[i]
            m60 = df['MA60'].iloc[i]
            m20_diff = df['MA20_Diff_1D'].iloc[i]

            bb_u = df['BB_Upper'].iloc[i]
            bb_u_diff5 = df['BB_Upper_5D_Diff'].iloc[i]
            bw = df['BB_Bandwidth'].iloc[i]

            rsi = df['RSI14'].iloc[i]
            prev_rsi = df['RSI14'].iloc[i-1]
            rsi_diff_1 = df['RSI_Diff_1D'].iloc[i]

            dif = df['DIF'].iloc[i]
            dea = df['DEA'].iloc[i]
            prev_dif = df['DIF'].iloc[i-1]
            prev_dea = df['DEA'].iloc[i-1]
            hist = df['MACD_Hist'].iloc[i]
            prev_hist = df['MACD_Hist'].iloc[i-1]
            
            temp_val = df['Temperature'].iloc[i]
            prev_temp_val = df['Temperature'].iloc[i-1]
            temp_ma10 = df['Temperature_MA10'].iloc[i]
            prev_temp_ma10 = df['Temperature_MA10'].iloc[i-1]

            # 1. 熊市與水下判定
            ma60_60d_ago = df['MA60'].iloc[i-60] if i >= 60 else df['MA60'].iloc[0]
            is_ma60_down = m60 < ma60_60d_ago
            is_bear_market = is_ma60_down and (close_p < m60)
            is_macd_underwater = (dif < 0) and (dea < 0)

            # 2. 高檔背離否決判定
            is_far_from_ma60 = close_p >= (m60 * 1.25)
            is_high_temp = temp_val > 75.0
            if is_far_from_ma60 and is_high_temp:
                mode_bc_veto_active = True

            macd_gold_cross_above_0 = (dif > dea) and (prev_dif <= prev_dea) and (dif > 0)
            if macd_gold_cross_above_0:
                mode_bc_veto_active = False

            # 3. 弱勢勾頭否決判定
            temp_ma10_declining = temp_ma10 < prev_temp_ma10
            weak_hook_filter = temp_ma10_declining and (temp_val < 50.0)

            # 4. 冷卻期機制
            cd_active = (i - last_buy_index) < 5

            # ------------------------------------------------
            # 進場條件判斷
            # ------------------------------------------------
            # 模式 A: 超賣強彈抄底
            rsi_recent_oversold = (df['RSI14'].iloc[max(0, i-4):i+1] < 30.0).any()
            rsi_surge_8 = rsi_diff_1 >= 8.0
            mode_a_buy = rsi_recent_oversold and rsi_surge_8

            # 模式 B: 強勢回檔再發動
            cond_b_env1 = (m20 > m60) or (dif > 0)
            cond_b_env2 = (close_p > m60) and (dif > 0)
            cond_b_rsi = (40.0 <= prev_rsi <= 50.0) and (rsi_diff_1 > 0)
            touched_ma20_recent = (df['Low'].iloc[max(0, i-1):i+1] <= df['MA20'].iloc[max(0, i-1):i+1] * 1.015).any()
            prev_close_p = df['Close'].iloc[i-1]
            cond_b_price = (
                touched_ma20_recent and 
                (close_p > open_p) and 
                (close_p > m5) and 
                (m20_diff > 0) and 
                (close_p >= m20 * 1.01) and 
                (low_p > prev_close_p)
            )
            mode_b_buy = cond_b_env1 and cond_b_env2 and cond_b_rsi and cond_b_price

            # 模式 C: 平台突破 (不受高檔背離否決限制)
            close_15d_max = df['Close'].iloc[max(0, i-14):i].max() if i >= 15 else df['Close'].iloc[:i].max()
            cond_c_ma_align = (close_p > m20) and (m20 > m60)
            cond_c_new_high = close_p > close_15d_max
            cond_c_long_red = (close_p > open_p) and (close_p >= bb_u * 0.995)
            mode_c_buy = cond_c_ma_align and cond_c_new_high and cond_c_long_red

            # ------------------------------------------------
            # 減倉與離場條件判斷
            # ------------------------------------------------
            macd_dc = (dif < dea) and (prev_dif >= prev_dea)
            
            # 1. 標準趨勢轉空清倉
            cond_exit_std = macd_dc and (close_p < m20)

            # 2. 高位獲利清倉
            close_20d_max = df['Close'].iloc[max(0, i-19):i+1].max()
            is_near_20d_high = close_p >= (close_20d_max * 0.98)
            cond_exit_high_profit = (entry_mode == "B") and is_near_20d_high and (prev_temp_val > 80.0) and macd_dc and (close_p < m20)

            # 3. 模式B2 (現為模式B持倉之MACD動能衰竭) 清倉
            cond_exit_macd_exhaust = (entry_mode in ["B", "C"]) and (dif > 0) and (hist < 10) and ((prev_hist - hist) >= 10)

            # 4. 模式 B 和 C 三日認錯停損
            days_since_entry = i - entry_day_index
            cond_exit_3d_fail = False
            if in_position and (entry_mode in ["B", "C"]) and (1 <= days_since_entry <= 3):
                if low_p < entry_day_low:
                    cond_exit_3d_fail = True

            # 5. 模式 C 專屬減倉邏輯：滿倉持有一日以上，近 3 日溫度累計銳減 > 20 度，隔天減至半倉
            temp_drop_3d = (df['Temperature'].iloc[i-2] - temp_val) if i >= 2 else 0.0
            cond_c_reduce_half = (position_ratio == 1.0) and (entry_mode == "C") and (temp_drop_3d > 20.0)

            # ------------------------------------------------
            # 狀態機決策樹
            # ------------------------------------------------
            act = "觀望待變"
            rsn = "指標未符合【模式A抄底】、【模式B強勢回檔】或【模式C平台突破】之開倉門檻"

            # 離場優先判定
            if cond_exit_std and in_position:
                act = "🛑 趨勢轉空清倉(100%清倉)"
                rsn = f"MACD死叉且股價({close_p:.2f})跌破MA20({m20:.2f})，觸發標準趨勢轉空，無條件100%清倉退場"
                in_position = False
                position_ratio = 0.0
                entry_mode = ""

            elif cond_exit_high_profit and in_position:
                act = "🛑 高位獲利清倉(100%清倉)"
                rsn = f"模式B持倉於20日高檔區，前日市場溫度T({prev_temp_val:.1f})>80，觸發MACD死叉跌破MA20，全數清倉落袋為安"
                in_position = False
                position_ratio = 0.0
                entry_mode = ""

            elif cond_exit_macd_exhaust and in_position:
                act = "🛑 動能衰竭清倉(100%清倉)"
                rsn = f"MACD在水上但當日柱狀體({hist:.2f})<10且較昨日驟降({prev_hist - hist:.2f}>=10)，觸發動能衰竭清倉"
                in_position = False
                position_ratio = 0.0
                entry_mode = ""

            elif cond_exit_3d_fail and in_position:
                act = "🛑 3日認錯停損(100%清倉)"
                rsn = f"{entry_mode}模式入場後3日內跌破建倉日最低點(${entry_day_low:.2f})，判定為假突破/假訊號，100%無條件清倉"
                in_position = False
                position_ratio = 0.0
                entry_mode = ""

            # 減倉判定
            elif cond_c_reduce_half:
                act = "⚠️ 模式C高檔過熱減碼(減至半倉)"
                rsn = f"滿倉持有模式C下，近3日市場溫度累計銳減{temp_drop_3d:.1f}°C(>20°C)，啟動風控隔日減碼至50%半倉"
                position_ratio = 0.5

            # 進場與加倉判定
            elif not in_position and not cd_active and mode_a_buy:
                act = "🟢 模式A:超賣強彈(建半倉)"
                rsn = f"【模式A抄底】近5日內曾RSI<30，今日RSI暴漲{rsi_diff_1:.1f}點(>=8)，觸發抄底試單半倉"
                last_buy_index = i
                in_position = True
                position_ratio = 0.5
                entry_mode = "A"

            elif not in_position and not cd_active and mode_b_buy:
                if is_bear_market:
                    act = "🚫 模式B被禁用(熊市狀態)"
                    rsn = "季線向下且價格處於季線下方，系統判定為熊市，嚴禁執行模式B建倉"
                elif is_macd_underwater:
                    act = "🚫 模式B被禁用(MACD水下)"
                    rsn = "MACD DIF與DEA均在0軸下方(水下)，空頭掌控全場，嚴禁執行模式B建倉"
                elif mode_bc_veto_active:
                    act = "⚠️ 模式B被否決(高檔極致背離)"
                    rsn = "股價遠離季線(>=25%)且溫度T>75，觸發高檔背離否決，暫停開倉"
                elif weak_hook_filter:
                    act = "⚠️ 模式B被否決(弱勢勾頭)"
                    rsn = f"市場溫度T之10日均線下滑且T值({temp_val:.1f})<50，屬動能失血之弱勢勾頭，拒絕開倉"
                else:
                    act = "🟢 模式B:強勢回檔再發動(建半倉)"
                    rsn = f"【模式B】大環境多頭，回踩20日均線後陽線強勢站上5日線，滿足開倉條件建立半倉"
                    last_buy_index = i
                    entry_day_index = i
                    entry_day_low = low_p
                    in_position = True
                    position_ratio = 0.5
                    entry_mode = "B"

            elif not cd_active and mode_c_buy:
                if is_bear_market:
                    act = "🚫 模式C被禁用(熊市狀態)"
                    rsn = "季線向下且價格處於季線下方，系統判定為熊市，嚴禁執行模式C追高突破"
                elif is_macd_underwater:
                    act = "🚫 模式C被禁用(MACD水下)"
                    rsn = "MACD DIF與DEA均在0軸下方(水下)，空頭掌控全場，嚴禁執行模式C追高突破"
                elif weak_hook_filter:
                    act = "⚠️ 模式C被否決(弱勢勾頭)"
                    rsn = f"市場溫度T之10日均線下滑且T值({temp_val:.1f})<50，屬動能失血，拒絕開倉"
                else:
                    # 模式C不受高檔背離否決限制
                    if not in_position:
                        act = "🟢 模式C:平台突破(建半倉)"
                        rsn = f"【模式C平台突破】多頭排列下收盤創15日新高，實體長紅站上布林上軌，開倉建立半倉"
                        last_buy_index = i
                        entry_day_index = i
                        entry_day_low = low_p
                        in_position = True
                        position_ratio = 0.5
                        entry_mode = "C"
                    elif position_ratio == 0.5:
                        act = "🚀 模式C:平台突破(補滿倉)"
                        rsn = f"【模式C平台突破】多頭主升浪拉出創15日新高的突破長紅棒，倉位補滿至100%"
                        last_buy_index = i
                        entry_day_index = i
                        entry_day_low = low_p
                        position_ratio = 1.0

            # 通用加倉機制
            elif (position_ratio == 0.5) and (close_p >= m20) and (bb_u_diff5 > 0) and not cd_active:
                act = "🚀 市場起立:準備起飛(補滿倉)"
                rsn = f"已有半倉，今日股價站上20日均線(${m20:.2f})且布林通道上軌在近5日呈現擴張走揚，補滿至100%滿倉"
                last_buy_index = i
                position_ratio = 1.0

            # 持倉維護
            elif in_position:
                if (high_p >= bb_u) and (rsi >= 70) and (hist > 0):
                    act = "🔥 強勢軌道游走"
                    rsn = f"持倉中({int(position_ratio*100)}%)，股價沿布林上軌強勢游走，主升段續抱"
                else:
                    act = "✊ 續抱觀察"
                    rsn = f"持倉中({int(position_ratio*100)}%)，未滿足減倉或清倉條件，行情沿趨勢運行，繼續持股"

            elif bw < 0.08:
                act = "🟡 盤整變盤在即"
                rsn = "布林帶寬極度收窄（Bandwidth<0.08），隨時可能變盤突破；密切觀察模式C平台突破訊號"

            action_list.append(act)
            reason_list.append(rsn)

        df['Advice_Action'] = action_list
        df['Advice_Reason'] = reason_list

        return df

    # ----------------------------------------------------
    # ⚡ 專屬雷達診斷引擎：分析當日技術狀態與警告訊息
    # ----------------------------------------------------
    @staticmethod
    def analyze_daily_radar(df: pd.DataFrame) -> tuple[str, str, str]:
        if len(df) < 20:
            return "⚠️ 資料不足", "無法分析", "歷史交易日資料過短"

        latest = df.iloc[-1]
        prev_1 = df.iloc[-2]
        
        close_p = latest['Close']
        high_p = latest['High']
        m5 = latest['MA5']
        m20 = latest['MA20']
        m60 = latest['MA60']
        bb_u = latest['BB_Upper']
        bw = latest['BB_Bandwidth']
        rsi = latest['RSI14']
        dif = latest['DIF']
        dea = latest['DEA']
        hist = latest['MACD_Hist']
        prev_hist = prev_1['MACD_Hist']
        temp = latest['Temperature']
        temp_ma10 = latest['Temperature_MA10']

        is_high_temp = temp >= 75.0
        is_rsi_oversold = rsi <= 35.0
        macd_gc = (dif > dea) and (prev_1['DIF'] <= prev_1['DEA'])
        macd_dc = (dif < dea) and (prev_1['DIF'] >= prev_1['DEA'])
        
        if (close_p > m20) and (m20 > m60) and (45.0 <= rsi <= 62.0) and (dif > dea) and not is_high_temp:
            setup_status = "🟢 極佳 (順勢發動)"
        elif is_rsi_oversold and (hist > prev_hist):
            setup_status = "🟢 良好 (超賣止跌)"
        elif is_high_temp or (rsi > 68.0):
            setup_status = "🔴 偏低 (過熱追高險)"
        elif (close_p < m20) and (dif < 0) and (dea < 0):
            setup_status = "🔴 不宜 (水下空頭趨勢)"
        else:
            setup_status = "🟡 一般 (震盪觀望)"

        alerts = []
        if macd_gc and is_high_temp:
            alerts.append("⚠️ MACD高位金叉(慎防高檔背離/誘多)")
        elif macd_gc:
            alerts.append("🟢 MACD低位/零軸上金叉(多頭發動)")
        
        if macd_dc and (close_p < m5):
            alerts.append("🛑 MACD死叉+跌破5日線(短線轉弱)")
            
        if (high_p >= bb_u) and (close_p < latest['Open']) and (hist < prev_hist):
            alerts.append("⚡ 衝高受阻(爆量留長上影/動能衰退)")
            
        if is_high_temp and (temp < temp_ma10):
            alerts.append("📉 動態溫度從高檔彎頭(短線漲勢過勞)")

        if (hist > 0) and (hist < prev_hist):
            alerts.append("⚠️ MACD紅柱縮短(向上衝勁不足)")

        if bw < 0.07:
            alerts.append("🟡 布林極度壓縮(即將強勢變盤)")

        primary_alert = " | ".join(alerts) if alerts else "✅ 技術面平穩運行"

        if "MACD高位金叉" in primary_alert or "動態溫度從高檔彎頭" in primary_alert:
            diag_detail = f"市場溫度已達{temp:.1f}°C過熱區，MACD雖然金叉但指標處於相對高位，慎防追高風險或頂背離過勞。"
        elif "衝高受阻" in primary_alert or "向上衝勁不足" in primary_alert:
            diag_detail = f"股價嘗試挑戰布林上軌(${bb_u:.2f})，但柱狀體開始收縮，顯示上方賣壓重、短線有衝不動跡象。"
        elif "短線轉弱" in primary_alert:
            diag_detail = f"今日MACD轉為死叉且收盤跌破5日均線(${m5:.2f})，短期多頭局勢有走壞跡象，宜注意減碼避險。"
        elif "順勢發動" in setup_status:
            diag_detail = f"均線呈現標準多頭排列，RSI({rsi:.1f})溫和上升且MACD維持多頭，極度適合順勢分批建倉。"
        elif "超賣止跌" in setup_status:
            diag_detail = f"RSI已落入超賣區後反彈，MACD負柱開始收縮，屬風險報酬比較佳的反彈試單時機。"
        elif "水下空頭趨勢" in setup_status:
            diag_detail = f"現價低於月線(${m20:.2f})且MACD在雙水下運作，市場屬於空頭掌控，暫不建議盲目建倉。"
        else:
            diag_detail = f"當前技術面指標呈橫盤震盪，指標暫無明確攻守訊號，建議靜待變盤與方向確立。"

        return setup_status, primary_alert, diag_detail

# ==========================================
# 3. 本地 JSON 數據庫管理者
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
                "symbol": "2330.TW", "name": "台積電"
            },
            "513380": {
                "symbol": "513380", "name": "恒生科技ETF廣發"
            }
        },
        "bt_start": (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d"),
        "bt_end": datetime.now().strftime("%Y-%m-%d")
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
                
                if "bt_start" not in data:
                    data["bt_start"] = default_db["bt_start"]
                if "bt_end" not in data:
                    data["bt_end"] = default_db["bt_end"]
                    
                return data
            else:
                new_data = {"stock_order": [], "stocks": {}, "bt_start": default_db["bt_start"], "bt_end": default_db["bt_end"]}
                for key, val in data.items():
                    if isinstance(val, dict):
                        new_data["stocks"][key] = {
                            "symbol": val.get("symbol", key),
                            "name": val.get("name", key)
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
# 4. Streamlit GUI 主介面
# ==========================================
st.set_page_config(page_title="專業布林+MACD+RSI多模式趨勢提醒工具", layout="wide", page_icon="📈")

if "db" not in st.session_state:
    st.session_state.db = load_db()

db = st.session_state.db
db.setdefault("stocks", {})
db.setdefault("stock_order", list(db["stocks"].keys()))

st.sidebar.title("⚙️ 標的與控制面板")

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
    
    if st.button("確認新增"):
        if new_sym:
            final_name = new_name if new_name else new_sym
            if new_sym not in db["stocks"]:
                db["stocks"][new_sym] = {
                    "symbol": new_sym, "name": final_name
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

st.title("📈 布林通道 + MACD + RSI 雙模式與平台突破趨勢分析工具")

tab1, tab2 = st.tabs(["🔍 單一標的 K 線與歷史回測", "⚡ 自選清單一鍵當日診斷雷達"])

# ----------------------------------------------------
# 🔍 頁籤一：單一標的 K 線分析
# ----------------------------------------------------
with tab1:
    col_bt1, col_bt2, col_bt3 = st.columns([2, 2, 2])
    with col_bt1:
        bt_symbol = st.selectbox(
            "選擇觀察標的",
            options=stock_keys,
            format_func=lambda x: stock_options[x] if x in stock_options else x,
            key="bt_symbol"
        )
    with col_bt2:
        default_start_str = db.get("bt_start", (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d"))
        default_start = datetime.strptime(default_start_str, "%Y-%m-%d")
        bt_start = st.date_input("開始日期", value=default_start, key="bt_start_input")
    with col_bt3:
        default_end_str = db.get("bt_end", datetime.now().strftime("%Y-%m-%d"))
        default_end = datetime.strptime(default_end_str, "%Y-%m-%d")
        bt_end = st.date_input("結束日期", value=default_end, key="bt_end_input")

    if st.button("🚀 載入 K 線與策略分析", type="primary"):
        start_str = bt_start.strftime("%Y-%m-%d")
        end_str = bt_end.strftime("%Y-%m-%d")
        
        db["bt_start"] = start_str
        db["bt_end"] = end_str
        save_db(db)

        with st.spinner(f"正在擷取 {bt_symbol} 行情數據並計算策略指標..."):
            try:
                fetch_start = (bt_start - timedelta(days=120)).strftime("%Y-%m-%d")
                df_raw, src_bt = cached_fetch_ohlc(bt_symbol, start_date=fetch_start, end_date=end_str)
                
                if df_raw.empty or len(df_raw) < 10:
                    st.error("歷史數據不足，無法繪製K線圖。請確認代碼或重新選擇區間。")
                else:
                    df_calc = TechnicalAnalysisEngine.calculate_indicators(df_raw, display_start_date=start_str)
                    df_sub = df_calc.loc[start_str:end_str]

                    if df_sub.empty:
                        st.warning("選定日期區間內沒有可用的交易日行情數據。")
                    else:
                        latest = df_sub.iloc[-1]
                        latest_date_str = df_sub.index[-1].strftime("%Y-%m-%d")
                        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                        curr_price = latest['Close']
                        prev_p = df_calc.iloc[df_calc.index.get_loc(df_sub.index[-1]) - 1]['Close'] if len(df_calc) > len(df_sub) else latest['Open']
                        price_diff = curr_price - prev_p
                        price_pct = (price_diff / prev_p) * 100.0 if prev_p > 0 else 0.0

                        st.markdown("---")
                        p_col1, p_col2 = st.columns([2, 4])
                        with p_col1:
                            st.metric(
                                label=f"即時最新收盤價 ({latest_date_str})",
                                value=f"${curr_price:,.2f}",
                                delta=f"{price_diff:+.2f} ({price_pct:+.2f}%)"
                            )
                        with p_col2:
                            st.caption(f"⏱️ 系統數據最後計算更新時間：`{now_str}`")
                            st.caption(f"📡 行情數據來源：`{src_bt}`")

                        st.markdown("---")
                        st.markdown("#### 💡 當前最新策略操作建議與動態提醒")
                        
                        act_text = latest['Advice_Action']
                        rsn_text = latest['Advice_Reason']
                        
                        if "清倉" in act_text or "砍倉" in act_text or "停損" in act_text:
                            st.error(f"**【操作建議】{act_text}** — {rsn_text}")
                        elif "被禁用" in act_text or "否決" in act_text or "減碼" in act_text or "減至半倉" in act_text:
                            st.warning(f"**【操作建議】{act_text}** — {rsn_text}")
                        elif "模式" in act_text or "建倉" in act_text or "補滿倉" in act_text or "起飛" in act_text:
                            st.success(f"**【操作建議】{act_text}** — {rsn_text}")
                        else:
                            st.info(f"**【操作建議】{act_text}** — {rsn_text}")

                        st.markdown("---")
                        st.markdown("#### 📊 最新市場指標總覽")
                        
                        m1, m2, m3, m4, m5, m6 = st.columns([1.2, 1.3, 1.2, 1.2, 1.1, 1.1])
                        
                        temp_val = latest['Temperature']
                        m1.metric("市場動態溫度 T", f"{temp_val:.1f}°C")

                        close_p = latest['Close']
                        bb_u = latest['BB_Upper']
                        bb_m = latest['MA20']
                        bb_l = latest['BB_Lower']
                        
                        if close_p >= bb_u:
                            bb_pos_str = "觸及/上破上軌 🔥"
                        elif close_p <= bb_l:
                            bb_pos_str = "觸及/下破下軌 🥶"
                        elif close_p > bb_m:
                            bb_pos_str = "中軌與上軌之間 📈"
                        else:
                            bb_pos_str = "中軌與下軌之間 📉"
                        m2.metric("布林通道位置", bb_pos_str)

                        rsi_now = latest['RSI14']
                        rsi_diff_2 = latest['RSI_Diff_2D']
                        rsi_diff_5 = latest['RSI_Diff_5D']
                        diff_2_str = f"{'▲' if rsi_diff_2 >= 0 else '▼'}{abs(rsi_diff_2):.2f}" if not np.isnan(rsi_diff_2) else "N/A"
                        diff_5_str = f"{'▲' if rsi_diff_5 >= 0 else '▼'}{abs(rsi_diff_5):.2f}" if not np.isnan(rsi_diff_5) else "N/A"
                        m3.metric("RSI(14) 當前值", f"{rsi_now:.2f}", f"2日:{diff_2_str} | 5日:{diff_5_str}")

                        dif_val = latest['DIF']
                        macd_tag = "0軸上方 (強勢)" if dif_val > 0 else "0軸下方 (弱勢)"
                        m4.metric("MACD 快線 (DIF)", f"{dif_val:.2f}", macd_tag)

                        b5_tag = "看多 🟢" if latest['Bull_5D'] else ("看空 🔴" if latest['Bear_5D'] else "震盪 🟡")
                        m5.metric("5日短期格局", b5_tag)

                        b20_tag = "看多 🟢" if latest['Bull_20D'] else ("看空 🔴" if latest['Bear_20D'] else "震盪 🟡")
                        m6.metric("20日中期格局", b20_tag)

                        st.markdown("---")
                        st.markdown("#### 🔍 多空格局技術面原因解析")
                        c_rs1, c_rs2 = st.columns(2)
                        with c_rs1:
                            st.info(f"**📌 5日短期格局原因：** {latest['Reason_5D']}")
                        with c_rs2:
                            st.info(f"**📌 20日中期格局原因：** {latest['Reason_20D']}")

                        st.markdown("---")
                        st.markdown("#### 🎯 K線(含均線與布林通道)、市場溫度、RSI 與 MACD 四圖對照")

                        fig = make_subplots(
                            rows=4, cols=1, 
                            shared_xaxes=True, 
                            vertical_spacing=0.03, 
                            row_heights=[0.4, 0.2, 0.2, 0.2],
                            subplot_titles=(
                                f"{bt_symbol} K線、MA5/10/20/60 均線與布林通道", 
                                "動態市場溫度 T (0-100)", 
                                "RSI(14) 指標", 
                                "MACD 指標 (DIF, DEA, 柱狀圖)"
                            )
                        )

                        fig.add_trace(go.Candlestick(
                            x=df_sub.index,
                            open=df_sub['Open'], high=df_sub['High'],
                            low=df_sub['Low'], close=df_sub['Close'],
                            name='K線',
                            increasing_line_color='#26a69a', decreasing_line_color='#ef5350'
                        ), row=1, col=1)

                        fig.add_trace(go.Scatter(x=df_sub.index, y=df_sub['MA5'], mode='lines', name='MA5', line=dict(color='#FF9800', width=1.2)), row=1, col=1)
                        fig.add_trace(go.Scatter(x=df_sub.index, y=df_sub['MA10'], mode='lines', name='MA10', line=dict(color='#00BCD4', width=1.2)), row=1, col=1)
                        fig.add_trace(go.Scatter(x=df_sub.index, y=df_sub['MA20'], mode='lines', name='MA20(布林中軌)', line=dict(color='#2196F3', width=1.5)), row=1, col=1)
                        fig.add_trace(go.Scatter(x=df_sub.index, y=df_sub['MA60'], mode='lines', name='MA60(季線)', line=dict(color='#78909C', width=1.5)), row=1, col=1)

                        fig.add_trace(go.Scatter(x=df_sub.index, y=df_sub['BB_Upper'], mode='lines', name='布林上軌', line=dict(color='#AB47BC', width=1, dash='dash')), row=1, col=1)
                        fig.add_trace(go.Scatter(x=df_sub.index, y=df_sub['BB_Lower'], mode='lines', name='布林下軌', line=dict(color='#AB47BC', width=1, dash='dash')), row=1, col=1)

                        fig.add_trace(go.Scatter(x=df_sub.index, y=df_sub['Temperature'], mode='lines', name='溫度 T', line=dict(color='#FF3D00', width=2)), row=2, col=1)
                        fig.add_trace(go.Scatter(x=df_sub.index, y=df_sub['Temperature_MA10'], mode='lines', name='溫度 T 10日均線', line=dict(color='#FFA726', width=1, dash='dot')), row=2, col=1)
                        fig.add_hline(y=75, line_dash="dash", line_color="#FF1744", row=2, col=1)
                        fig.add_hline(y=35, line_dash="dash", line_color="#00E676", row=2, col=1)

                        fig.add_trace(go.Scatter(x=df_sub.index, y=df_sub['RSI14'], mode='lines', name='RSI(14)', line=dict(color='#00E5FF', width=1.5)), row=3, col=1)
                        fig.add_hline(y=70, line_dash="dot", line_color="#FF8A80", row=3, col=1)
                        fig.add_hline(y=50, line_dash="dash", line_color="#CCCCCC", row=3, col=1)
                        fig.add_hline(y=30, line_dash="dot", line_color="#B9F6CA", row=3, col=1)

                        fig.add_trace(go.Scatter(x=df_sub.index, y=df_sub['DIF'], mode='lines', name='DIF (快線)', line=dict(color='#2962FF', width=1.2)), row=4, col=1)
                        fig.add_trace(go.Scatter(x=df_sub.index, y=df_sub['DEA'], mode='lines', name='DEA (慢線)', line=dict(color='#FF6D00', width=1.2)), row=4, col=1)
                        
                        macd_colors = ['#26a69a' if h >= 0 else '#ef5350' for h in df_sub['MACD_Hist']]
                        fig.add_trace(go.Bar(x=df_sub.index, y=df_sub['MACD_Hist'], name='MACD 柱狀圖', marker_color=macd_colors), row=4, col=1)
                        fig.add_hline(y=0, line_dash="solid", line_color="#9E9E9E", row=4, col=1)

                        fig.update_layout(
                            xaxis_rangeslider_visible=False,
                            hovermode="x unified",
                            template="plotly_white",
                            height=850
                        )
                        st.plotly_chart(fig, use_container_width=True)

                        st.markdown("---")
                        st.markdown("#### 📜 每日技術指標、多空原因與策略建議明細")
                        
                        show_df = df_sub.copy()
                        show_df['市場溫度 T'] = show_df['Temperature'].round(1)
                        show_df['RSI(14)'] = show_df['RSI14'].round(2)
                        show_df['MACD柱狀'] = show_df['MACD_Hist'].round(3)
                        
                        show_cols = ["Open", "High", "Low", "Close", "Volume", "市場溫度 T", "RSI(14)", "Reason_5D", "Reason_20D", "Advice_Action", "Advice_Reason"]
                        rename_dict = {
                            "Reason_5D": "5日格局原因",
                            "Reason_20D": "20日格局原因",
                            "Advice_Action": "操作建議",
                            "Advice_Reason": "策略分析原因"
                        }
                        
                        display_df = show_df[show_cols].rename(columns=rename_dict).sort_index(ascending=False)
                        st.dataframe(display_df, use_container_width=True)

            except Exception as ex:
                st.error(f"載入數據失敗: {ex}")

# ----------------------------------------------------
# ⚡ 頁籤二：自選清單一鍵當日診斷雷達
# ----------------------------------------------------
with tab2:
    st.markdown("### ⚡ 自選個股一鍵當日診斷雷達與漲跌幅檢視")
    st.write("此功能專為「當日盤後/即時診斷」設計，自動掃瞄自選清單中的所有個股，提示**建倉適性**、**高位背離/衝不動警報**與**多週期漲跌幅**。")

    col_scan1, col_scan2 = st.columns([3, 3])
    with col_scan1:
        scan_target_date = st.date_input("選擇診斷日期 (預設當天)", value=datetime.now(), key="scan_target_date")
    
    if st.button("🔍 執行一鍵當日雷達診斷", type="primary", key="btn_scan_all"):
        if not stock_keys:
            st.warning("目前自選清單中沒有標的，請先在左側邊欄新增股票。")
        else:
            scan_results = []
            target_date_str = scan_target_date.strftime("%Y-%m-%d")
            fetch_start_date = (scan_target_date - timedelta(days=450)).strftime("%Y-%m-%d")
            
            progress_bar = st.progress(0)
            status_text = st.empty()

            for idx, sym in enumerate(stock_keys):
                stock_name = db["stocks"][sym].get("name", sym)
                status_text.text(f"⏳ 正在診斷 [{idx+1}/{len(stock_keys)}]: {sym} ({stock_name})...")
                
                try:
                    df_raw, _ = cached_fetch_ohlc(sym, start_date=fetch_start_date, end_date=target_date_str)
                    
                    if not df_raw.empty and len(df_raw) >= 20:
                        df_calc = TechnicalAnalysisEngine.calculate_indicators(df_raw, display_start_date=fetch_start_date)
                        
                        latest_idx = df_calc.index.get_loc(df_calc.index[-1])
                        latest = df_calc.iloc[latest_idx]
                        curr_close = latest['Close']
                        
                        prev_1d_close = df_calc['Close'].iloc[latest_idx - 1] if latest_idx >= 1 else np.nan
                        ret_1d = ((curr_close - prev_1d_close) / prev_1d_close * 100.0) if not np.isnan(prev_1d_close) else 0.0

                        prev_1m_close = df_calc['Close'].iloc[latest_idx - 20] if latest_idx >= 20 else np.nan
                        ret_1m = ((curr_close - prev_1m_close) / prev_1m_close * 100.0) if not np.isnan(prev_1m_close) else np.nan

                        prev_3m_close = df_calc['Close'].iloc[latest_idx - 60] if latest_idx >= 60 else np.nan
                        ret_3m = ((curr_close - prev_3m_close) / prev_3m_close * 100.0) if not np.isnan(prev_3m_close) else np.nan

                        prev_1y_close = df_calc['Close'].iloc[latest_idx - 240] if latest_idx >= 240 else np.nan
                        ret_1y = ((curr_close - prev_1y_close) / prev_1y_close * 100.0) if not np.isnan(prev_1y_close) else np.nan

                        setup_status, alert_msg, diag_desc = TechnicalAnalysisEngine.analyze_daily_radar(df_calc)

                        scan_results.append({
                            "代碼": sym,
                            "名稱": stock_name,
                            "最新收盤": f"${curr_close:,.2f}",
                            "當日漲幅": f"{ret_1d:+.2f}%",
                            "建倉適性評估": setup_status,
                            "當日技術雷達警報": alert_msg,
                            "診斷說明": diag_desc,
                            "月漲幅(20D)": f"{ret_1m:+.2f}%" if not np.isnan(ret_1m) else "N/A",
                            "季漲幅(60D)": f"{ret_3m:+.2f}%" if not np.isnan(ret_3m) else "N/A",
                            "年漲幅(240D)": f"{ret_1y:+.2f}%" if not np.isnan(ret_1y) else "N/A",
                            "溫度T": round(latest['Temperature'], 1),
                            "RSI": round(latest['RSI14'], 2),
                            "DIF": round(latest['DIF'], 2)
                        })
                    else:
                        scan_results.append({
                            "代碼": sym, "名稱": stock_name, "最新收盤": "N/A", "當日漲幅": "N/A",
                            "建倉適性評估": "⚠️ 資料不足", "當日技術雷達警報": "無足夠 K 線資料",
                            "診斷說明": "歷史 K 線數據筆數不支援完整指標計算",
                            "月漲幅(20D)": "N/A", "季漲幅(60D)": "N/A", "年漲幅(240D)": "N/A",
                            "溫度T": "N/A", "RSI": "N/A", "DIF": "N/A"
                        })
                except Exception as ex:
                    scan_results.append({
                        "代碼": sym, "名稱": stock_name, "最新收盤": "N/A", "當日漲幅": "N/A",
                        "建倉適性評估": "❌ 擷取失敗", "當日技術雷達警報": str(ex),
                        "診斷說明": "行情 API 讀取異常",
                        "月漲幅(20D)": "N/A", "季漲幅(60D)": "N/A", "年漲幅(240D)": "N/A",
                        "溫度T": "N/A", "RSI": "N/A", "DIF": "N/A"
                    })
                
                progress_bar.progress((idx + 1) / len(stock_keys))

            status_text.text("✅ 全清單雷達診斷完成！")
            progress_bar.empty()

            res_df = pd.DataFrame(scan_results)
            
            st.markdown("---")
            st.markdown("#### 📋 全自選股當日診斷報告總表")
            st.dataframe(res_df, use_container_width=True)
