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
                    "Date": formatted_date, "Open": open_p, "High": high_p,
                    "Low": low_p, "Close": float(close_p), "Volume": vol
                }
        return None

    def _fetch_eastmoney_fund(self, fund_code: str) -> pd.DataFrame:
        url = "https://api.fund.eastmoney.com/f10/lsjz"
        params = {"fundCode": fund_code, "pageIndex": 1, "pageSize": 1000, "startDate": "", "endDate": ""}
        resp = requests.get(url, params=params, headers=self.fund_headers, timeout=5)
        data = resp.json()

        if not data or "Data" not in data or not data["Data"] or "LSJZList" not in data["Data"]:
            return pd.DataFrame()

        raw_list = data["Data"]["LSJZList"]
        records = []
        for item in raw_list:
            if item.get("DWJZ"):
                jz = float(item["DWJZ"])
                records.append({"Date": item["FSRQ"], "Open": jz, "High": jz, "Low": jz, "Close": jz, "Volume": 10000.0})

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
                df = ticker.history(start=start_date, end=end_date) if start_date and end_date else ticker.history(period="2y")
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
# 2. 技術指標與綜合策略分析引擎 (精準符合規範)
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
        
        df['BB_Upper_Diff5'] = df['BB_Upper'].diff(5)
        df['MA20_Diff_1D'] = df['MA20'].diff(1)
        df['MA60_Diff_60D'] = df['MA60'] - df['MA60'].shift(60)

        # 2. RSI(14)
        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        df['RSI14'] = 100 - (100 / (1 + rs))
        df['RSI_Diff_1D'] = df['RSI14'].diff(1)

        # 3. MACD
        ema12 = df['Close'].ewm(span=12, adjust=False).mean()
        ema26 = df['Close'].ewm(span=26, adjust=False).mean()
        df['DIF'] = ema12 - ema26
        df['DEA'] = df['DIF'].ewm(span=9, adjust=False).mean()
        df['MACD_Hist'] = (df['DIF'] - df['DEA']) * 2

        # 4. 市場溫度 T
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

        # 多空格局原因判定
        trend_reason_5d, trend_reason_20d = [], []
        for i in range(len(df)):
            if i < 20:
                trend_reason_5d.append("數據累積中")
                trend_reason_20d.append("數據累積中")
                continue

            c, m5, m10, m20, m60 = df['Close'].iloc[i], df['MA5'].iloc[i], df['MA10'].iloc[i], df['MA20'].iloc[i], df['MA60'].iloc[i]
            m5_d = df['MA5'].diff().iloc[i]
            m20_d = df['MA20_Diff_1D'].iloc[i]

            if c > m5 and m5_d > 0:
                trend_reason_5d.append(f"股價({c:.2f})站上MA5({m5:.2f})且5日線走揚，短線多頭掌控")
            elif c < m5 and m5_d < 0:
                trend_reason_5d.append(f"股價({c:.2f})跌破MA5({m5:.2f})且5日線彎頭，短線空頭佔優")
            else:
                trend_reason_5d.append("股價與MA5糾結，短線橫盤震盪")

            if c > m20 and m20_d > 0 and m5 > m20:
                if m10 > m20 and m20 > m60:
                    trend_reason_20d.append("均線呈現標準多頭排列，波段趨勢強勁")
                else:
                    trend_reason_20d.append(f"股價高於MA20({m20:.2f})且月線走揚，中線看多")
            elif c < m20 and m20_d < 0 and m5 < m20:
                trend_reason_20d.append(f"股價跌破MA20({m20:.2f})且月線下滑，中線看空")
            else:
                trend_reason_20d.append("價格受限於MA20上下翻折，方向尚待確立")

        df['Reason_5D'] = trend_reason_5d
        df['Reason_20D'] = trend_reason_20d

        # ----------------------------------------------------
        # 核心交易邏輯執行迴圈 (按規範重構)
        # ----------------------------------------------------
        action_list, reason_list = [], []
        sub_start_dt = pd.to_datetime(display_start_date) if display_start_date else df.index[0]

        last_buy_index = -999
        in_position = False
        position_ratio = 0.0
        entry_mode = ""  # "A", "B", "C"
        entry_low_price = 0.0
        entry_index = -999
        mode_c_reduced_half = False  # 記錄模式C是否已被減至半倉

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
                reason_list.append("計算指標所需日數(20日)不足")
                continue

            close_p = df['Close'].iloc[i]
            open_p = df['Open'].iloc[i]
            low_p = df['Low'].iloc[i]

            m5 = df['MA5'].iloc[i]
            m20 = df['MA20'].iloc[i]
            m60 = df['MA60'].iloc[i]
            m20_diff = df['MA20_Diff_1D'].iloc[i]
            m60_diff_60 = df['MA60_Diff_60D'].iloc[i]

            bb_u = df['BB_Upper'].iloc[i]
            bb_u_diff5 = df['BB_Upper_Diff5'].iloc[i]

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

            # ----------------------------------------------------
            # 一、濾網條件判定
            # ----------------------------------------------------
            # 1. 熊市與水下禁用 (60日線向下且股價<60日線 OR DIF<0且DEA<0)
            is_bear_market = (m60_diff_60 < 0) and (close_p < m60)
            is_macd_underwater = (dif < 0) and (dea < 0)
            mode_b_disabled = is_bear_market or is_macd_underwater

            # 2. 高檔背離否決 (股價遠離季線>=25% 且 溫度>75)
            is_high_divergence_veto = (close_p >= m60 * 1.25) and (temp_val > 75.0)

            # 3. 弱勢勾頭否決 (溫度10日線下滑 且 溫度<50)
            is_weak_hook_veto = (temp_ma10 < prev_temp_ma10) and (temp_val < 50.0)

            # 4. 冷卻期機制 (買入後5交易日)
            cd_active = (i - last_buy_index) < 5

            # ----------------------------------------------------
            # 二、進場條件訊號計算
            # ----------------------------------------------------
            # 模式 A: 超賣強彈抄底 (近5日內曾RSI<30 且 當日RSI暴漲>=8)
            rsi_recent_oversold = (df['RSI14'].iloc[max(0, i-4):i+1] < 30.0).any()
            mode_a_buy = rsi_recent_oversold and (rsi_diff_1 >= 8.0)

            # 模式 B: 強勢回檔再發動
            b_cond1 = (m20 > m60) or (dif > 0)
            b_cond2 = (close_p > m60) and (dif > 0)
            b_cond3 = (40.0 <= prev_rsi <= 50.0) and (rsi_diff_1 > 0)
            b_cond4 = (df['Low'].iloc[max(0, i-1):i+1] <= df['MA20'].iloc[max(0, i-1):i+1] * 1.015).any()
            b_cond5 = (close_p > open_p) and (close_p > m5) and (m20_diff > 0) and (close_p >= m20 * 1.01) and (low_p > df['Close'].iloc[i-1])
            mode_b_buy = b_cond1 and b_cond2 and b_cond3 and b_cond4 and b_cond5

            # 模式 C: 平台突破
            close_15d_max = df['Close'].iloc[max(0, i-14):i].max() if i >= 15 else df['Close'].iloc[:i].max()
            c_cond1 = (close_p > m20) and (m20 > m60)
            c_cond2 = close_p > close_15d_max
            c_cond3 = (close_p > open_p) and (close_p >= bb_u * 0.995)
            mode_c_buy = c_cond1 and c_cond2 and c_cond3

            # ----------------------------------------------------
            # 三、加倉條件訊號
            # ----------------------------------------------------
            # 市場起立:準備起飛 (持半倉 + 站上MA20 + 布林上軌近5日擴張)
            add_position_signal = (position_ratio == 0.5) and (close_p >= m20) and (bb_u_diff5 > 0)

            # ----------------------------------------------------
            # 四、減倉機制計算 (模式 C 專屬)
            # ----------------------------------------------------
            # 滿倉時三天內(含當天)溫度銳減總共 > 20度 -> 馬上減至半倉
            temp_drop_3d = (df['Temperature'].iloc[i-2] - temp_val) if i >= 2 else 0.0
            mode_c_reduce_half_signal = (entry_mode == "C") and (position_ratio == 1.0) and (temp_drop_3d > 20.0)

            # 減半倉後，若遇到單日溫度升溫 > 10度 -> 減至1層倉(10%)
            mode_c_reduce_10pct_signal = (entry_mode == "C") and mode_c_reduced_half and (position_ratio == 0.5) and (rsi_diff_1 > 10.0 or (temp_val - prev_temp_val) > 10.0)

            # ----------------------------------------------------
            # 五、離場清倉條件計算
            # ----------------------------------------------------
            macd_dc = (dif < dea) and (prev_dif >= prev_dea)
            
            # 1. 標準趨勢轉空清倉: MACD死叉 且 跌破MA20
            exit_cond_1 = macd_dc and (close_p < m20)

            # 2. 高位獲利清倉: 模式B持倉 + 近20日新高附近(>=98%) + 前日溫度>80 + MACD死叉跌破MA20
            close_20d_max = df['Close'].iloc[max(0, i-19):i+1].max()
            is_near_20d_high = close_p >= (close_20d_max * 0.98)
            exit_cond_2 = (entry_mode == "B") and is_near_20d_high and (prev_temp_val > 80.0) and macd_dc and (close_p < m20)

            # 3. 模式B2動能衰竭清倉: 持倉B + DIF>0 + 柱狀體<10 且 較昨日驟降>=10
            exit_cond_3 = (entry_mode == "B") and (dif > 0) and (hist < 10) and ((prev_hist - hist) >= 10)

            # 4. 模式B和C三日認錯停損: 1~3交易日內跌破建倉日最低點
            days_since_entry = i - entry_index
            exit_cond_4 = in_position and (entry_mode in ["B", "C"]) and (1 <= days_since_entry <= 3) and (low_p < entry_low_price)

            # ----------------------------------------------------
            # 邏輯決策優先順序執行
            # ----------------------------------------------------
            act = "觀望待變"
            rsn = "指標未符合【模式A抄底】、【模式B強勢回檔】或【模式C平台突破】之開倉門檻"

            # 【清倉優先執行】
            if in_position and (exit_cond_1 or exit_cond_2 or exit_cond_3 or exit_cond_4):
                if exit_cond_4:
                    act = "🛑 模式B/C 3日認錯停損(100%清倉)"
                    rsn = f"【三日認錯停損】建倉後3日內跌破建倉日低點(${entry_low_price:.2f})，判定為假訊號，無條件100%清倉"
                elif exit_cond_2:
                    act = "🛑 模式B 高位獲利清倉(100%清倉)"
                    rsn = f"【高位獲利清倉】創20日高點且前日溫度({prev_temp_val:.1f})>80，觸發MACD死叉跌破MA20，鎖定獲利離場"
                elif exit_cond_3:
                    act = "🛑 模式B MACD動能衰竭清倉(100%清倉)"
                    rsn = f"【動能衰竭清倉】MACD柱狀體({hist:.2f})<10且單日驟降({prev_hist - hist:.2f}>=10)，即刻全數清倉"
                else:
                    act = "🛑 標準趨勢轉空清倉(100%清倉)"
                    rsn = f"【標準趨勢轉空】MACD出現死叉且收盤價(${close_p:.2f})跌破MA20(${m20:.2f})，全數清倉避險"

                in_position = False
                position_ratio = 0.0
                entry_mode = ""
                mode_c_reduced_half = False

            # 【模式 C 減倉機制優先執行】
            elif in_position and mode_c_reduce_half_signal:
                act = "⚠️ 模式C 溫度銳減減半倉(持倉降至50%)"
                rsn = f"【模式C減倉】近3日內市場溫度銳減{temp_drop_3d:.1f}度(>20度)，觸發防守機制，倉位減半至50%"
                position_ratio = 0.5
                mode_c_reduced_half = True

            elif in_position and mode_c_reduce_10pct_signal:
                act = "⚠️ 模式C 單日升溫防禦(減至10%輕倉)"
                rsn = f"【模式C二次減倉】減半倉後單日市場溫度反彈升溫(>10度)，進行二次風險控管，倉位降至10%"
                position_ratio = 0.1

            # 【加倉機制】
            elif in_position and add_position_signal and not cd_active:
                act = "🚀 市場起立:準備起飛(補滿倉至100%)"
                rsn = f"持有半倉下，股價站上MA20(${m20:.2f})且布林上軌近5日呈現擴張走揚，補滿至100%滿倉"
                position_ratio = 1.0
                last_buy_index = i

            # 【開倉機制】
            elif not in_position and not cd_active:
                # 模式 A 開倉
                if mode_a_buy:
                    act = "🟢 模式A:超賣強彈抄底(建半倉50%)"
                    rsn = f"【模式A抄底】近5日內曾RSI<30，今日RSI暴漲{rsi_diff_1:.1f}點(>=8)，觸發抄底試單半倉"
                    in_position = True
                    position_ratio = 0.5
                    entry_mode = "A"
                    entry_low_price = low_p
                    entry_index = i
                    last_buy_index = i

                # 模式 B 開倉 (嚴格受開倉限制)
                elif mode_b_buy:
                    if mode_b_disabled:
                        act = "🚫 模式B被禁用(熊市/水下狀態)"
                        rsn = "季線向下且價格在季線下，或MACD DIF與DEA雙雙在0軸下方，全面禁止模式B建倉"
                    elif is_high_divergence_veto:
                        act = "⚠️ 模式B被否決(高檔極致背離)"
                        rsn = f"股價遠離季線>=25%且市場溫度({temp_val:.1f})>75，觸發高檔背離否決，暫停開倉"
                    elif is_weak_hook_veto:
                        act = "⚠️ 模式B被否決(弱勢勾頭動能失血)"
                        rsn = f"市場溫度10日線下滑且溫度({temp_val:.1f})<50，判定為動能失血，拒絕開倉"
                    else:
                        act = "🟢 模式B:強勢回檔再發動(建半倉50%)"
                        rsn = f"【模式B回檔再發動】多頭環境下回踩MA20止跌且陽線站上MA5，滿足建倉門檻"
                        in_position = True
                        position_ratio = 0.5
                        entry_mode = "B"
                        entry_low_price = low_p
                        entry_index = i
                        last_buy_index = i

                # 模式 C 開倉 (不受高檔背離限制，但仍受熊市/水下與弱勢勾頭限制)
                elif mode_c_buy:
                    if mode_b_disabled:
                        act = "🚫 模式C被禁用(熊市/水下狀態)"
                        rsn = "季線向下且價格在季線下，或MACD在0軸下方，禁止模式C突破追高"
                    elif is_weak_hook_veto:
                        act = "⚠️ 模式C被否決(弱勢勾頭動能失血)"
                        rsn = f"市場溫度10日線下滑且溫度({temp_val:.1f})<50，判定為動能失血，拒絕突破開倉"
                    else:
                        act = "🟢 模式C:平台突破(建半倉50%)"
                        rsn = f"【模式C平台突破】多頭排列下收盤價創15日新高，實體長紅站上布林上軌(不受高檔背離限制)"
                        in_position = True
                        position_ratio = 0.5
                        entry_mode = "C"
                        entry_low_price = low_p
                        entry_index = i
                        last_buy_index = i

            elif in_position:
                act = f"✊ 續抱觀察 (當前持倉{int(position_ratio*100)}%)"
                rsn = f"持倉中({int(position_ratio*100)}%)，未觸減倉或清倉條件，行情沿趨勢運行"

            action_list.append(act)
            reason_list.append(rsn)

        df['Advice_Action'] = action_list
        df['Advice_Reason'] = reason_list

        return df

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
            alerts.append("🟢 MACD金叉發動")
        
        if macd_dc and (close_p < m5):
            alerts.append("🛑 MACD死叉+跌破5日線(短線轉弱)")
            
        if (high_p >= bb_u) and (close_p < latest['Open']) and (hist < prev_hist):
            alerts.append("⚡ 衝高受阻(動能衰退)")
            
        if is_high_temp and (temp < temp_ma10):
            alerts.append("📉 動態溫度從高檔彎頭")

        primary_alert = " | ".join(alerts) if alerts else "✅ 技術面平穩運行"
        diag_detail = f"最新收盤價 ${close_p:.2f}，市場溫度 {temp:.1f}°C，RSI為 {rsi:.1f}，MACD柱狀體為 {hist:.2f}。"

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
            "2330.TW": {"symbol": "2330.TW", "name": "台積電"},
            "513380": {"symbol": "513380", "name": "恒生科技ETF廣發"}
        },
        "bt_start": (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d"),
        "bt_end": datetime.now().strftime("%Y-%m-%d")
    }

    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if "stocks" in data:
                return data
        except Exception:
            pass
    save_db(default_db)
    return default_db

# ==========================================
# 4. Streamlit GUI 主介面
# ==========================================
st.set_page_config(page_title="布林+MACD+RSI 多模式量化策略系統", layout="wide", page_icon="📈")

if "db" not in st.session_state:
    st.session_state.db = load_db()

db = st.session_state.db
db.setdefault("stocks", {})
db.setdefault("stock_order", list(db["stocks"].keys()))

st.sidebar.title("⚙️ 標的與控制面板")

stock_keys = [k for k in db.get("stock_order", []) if k in db["stocks"]]
stock_options = {k: f"{k} - {db['stocks'][k].get('name', k)}" for k in stock_keys}

if hasattr(st, "dialog") and HAS_SORTABLES:
    @st.dialog("↕️ 調整自選清單順序")
    def reorder_modal():
        st.write("拖動調整順序：")
        display_items = [stock_options[k] for k in stock_keys]
        sorted_display = sort_items(display_items)
        reverse_map = {v: k for k, v in stock_options.items()}
        new_order = [reverse_map[item] for item in sorted_display if item in reverse_map]
        
        if st.button("💾 儲存順序", type="primary"):
            db["stock_order"] = new_order
            save_db(db)
            st.rerun()

    if st.sidebar.button("↕️ 調整自選清單順序"):
        reorder_modal()

st.sidebar.markdown("---")

with st.sidebar.expander("➕ 新增標的", expanded=False):
    new_sym = st.text_input("代碼 (例如 2330.TW / 513380)", "").strip().upper()
    new_name = st.text_input("名稱 (選填)", "").strip()
    if st.button("確認新增"):
        if new_sym:
            final_name = new_name if new_name else new_sym
            if new_sym not in db["stocks"]:
                db["stocks"][new_sym] = {"symbol": new_sym, "name": final_name}
                db["stock_order"].append(new_sym)
                save_db(db)
                st.sidebar.success(f"✅ 已新增 {new_sym}")
                st.rerun()

if db.get("stocks"):
    del_sym = st.sidebar.selectbox("🗑️ 刪除標的", options=stock_keys, format_func=lambda x: stock_options[x])
    if st.sidebar.button("確認刪除"):
        if del_sym in db["stocks"]:
            del db["stocks"][del_sym]
            db["stock_order"].remove(del_sym)
            save_db(db)
            st.sidebar.success(f"已刪除 {del_sym}")
            st.rerun()

st.title("📈 布林通道 + MACD + RSI 雙/三模式策略量化系統")

tab1, tab2 = st.tabs(["🔍 單一標的 K 線與策略回測", "⚡ 自選診斷雷達"])

# ----------------------------------------------------
# 🔍 頁籤一：單一標的分析
# ----------------------------------------------------
with tab1:
    c1, c2, c3 = st.columns([2, 2, 2])
    with c1:
        bt_symbol = st.selectbox("選擇標的", options=stock_keys, format_func=lambda x: stock_options[x], key="bt_symbol")
    with c2:
        bt_start = st.date_input("開始日期", value=datetime.strptime(db.get("bt_start", "2025-01-01"), "%Y-%m-%d"))
    with c3:
        bt_end = st.date_input("結束日期", value=datetime.strptime(db.get("bt_end", datetime.now().strftime("%Y-%m-%d")), "%Y-%m-%d"))

    if st.button("🚀 載入 K 線與策略分析", type="primary"):
        start_str, end_str = bt_start.strftime("%Y-%m-%d"), bt_end.strftime("%Y-%m-%d")
        db["bt_start"], db["bt_end"] = start_str, end_str
        save_db(db)

        with st.spinner(f"分析數據中 ({bt_symbol})..."):
            try:
                fetch_start = (bt_start - timedelta(days=120)).strftime("%Y-%m-%d")
                df_raw, src_bt = cached_fetch_ohlc(bt_symbol, start_date=fetch_start, end_date=end_str)
                
                if df_raw.empty or len(df_raw) < 10:
                    st.error("歷史數據不足。")
                else:
                    df_calc = TechnicalAnalysisEngine.calculate_indicators(df_raw, display_start_date=start_str)
                    df_sub = df_calc.loc[start_str:end_str]

                    if df_sub.empty:
                        st.warning("區間內無交易數據。")
                    else:
                        latest = df_sub.iloc[-1]
                        st.markdown("---")
                        st.markdown(f"### 💡 當前最新策略建議 ({df_sub.index[-1].strftime('%Y-%m-%d')})")
                        
                        act_text, rsn_text = latest['Advice_Action'], latest['Advice_Reason']
                        if "100%清倉" in act_text or "停損" in act_text:
                            st.error(f"**【操作建議】{act_text}** — {rsn_text}")
                        elif "被禁用" in act_text or "否決" in act_text or "減倉" in act_text:
                            st.warning(f"**【操作建議】{act_text}** — {rsn_text}")
                        elif "模式" in act_text or "建倉" in act_text or "起飛" in act_text:
                            st.success(f"**【操作建議】{act_text}** — {rsn_text}")
                        else:
                            st.info(f"**【操作建議】{act_text}** — {rsn_text}")

                        # K線圖表繪製
                        fig = make_subplots(
                            rows=4, cols=1, shared_xaxes=True, vertical_spacing=0.03,
                            row_heights=[0.4, 0.2, 0.2, 0.2],
                            subplot_titles=(f"{bt_symbol} K線與布林通道", "動態市場溫度 T", "RSI(14) 指標", "MACD 指標")
                        )

                        fig.add_trace(go.Candlestick(
                            x=df_sub.index, open=df_sub['Open'], high=df_sub['High'],
                            low=df_sub['Low'], close=df_sub['Close'], name='K線'
                        ), row=1, col=1)

                        fig.add_trace(go.Scatter(x=df_sub.index, y=df_sub['MA5'], mode='lines', name='MA5'), row=1, col=1)
                        fig.add_trace(go.Scatter(x=df_sub.index, y=df_sub['MA20'], mode='lines', name='MA20'), row=1, col=1)
                        fig.add_trace(go.Scatter(x=df_sub.index, y=df_sub['MA60'], mode='lines', name='MA60'), row=1, col=1)
                        fig.add_trace(go.Scatter(x=df_sub.index, y=df_sub['BB_Upper'], mode='lines', name='布林上軌', line=dict(dash='dash')), row=1, col=1)

                        fig.add_trace(go.Scatter(x=df_sub.index, y=df_sub['Temperature'], mode='lines', name='溫度 T', line=dict(color='red')), row=2, col=1)
                        fig.add_trace(go.Scatter(x=df_sub.index, y=df_sub['RSI14'], mode='lines', name='RSI', line=dict(color='cyan')), row=3, col=1)
                        
                        macd_colors = ['#26a69a' if h >= 0 else '#ef5350' for h in df_sub['MACD_Hist']]
                        fig.add_trace(go.Bar(x=df_sub.index, y=df_sub['MACD_Hist'], name='MACD柱狀', marker_color=macd_colors), row=4, col=1)

                        fig.update_layout(xaxis_rangeslider_visible=False, height=800)
                        st.plotly_chart(fig, use_container_width=True)

                        st.markdown("#### 📜 明細紀錄")
                        show_cols = ["Open", "High", "Low", "Close", "Volume", "Temperature", "RSI14", "Advice_Action", "Advice_Reason"]
                        st.dataframe(df_sub[show_cols].sort_index(ascending=False), use_container_width=True)

            except Exception as ex:
                st.error(f"數據讀取失敗: {ex}")

# ----------------------------------------------------
# ⚡ 頁籤二：自選清單雷達
# ----------------------------------------------------
with tab2:
    st.markdown("### ⚡ 自選個股一鍵診斷雷達")
    scan_date = st.date_input("診斷日期", value=datetime.now())
    
    if st.button("🔍 執行雷達診斷", type="primary"):
        results = []
        target_date_str = scan_date.strftime("%Y-%m-%d")
        fetch_start = (scan_date - timedelta(days=400)).strftime("%Y-%m-%d")

        for sym in stock_keys:
            stock_name = db["stocks"][sym].get("name", sym)
            try:
                df_raw, _ = cached_fetch_ohlc(sym, start_date=fetch_start, end_date=target_date_str)
                if not df_raw.empty and len(df_raw) >= 20:
                    df_calc = TechnicalAnalysisEngine.calculate_indicators(df_raw, display_start_date=fetch_start)
                    setup_status, alert_msg, diag_desc = TechnicalAnalysisEngine.analyze_daily_radar(df_calc)
                    latest = df_calc.iloc[-1]
                    results.append({
                        "代碼": sym, "名稱": stock_name, "收盤價": f"${latest['Close']:.2f}",
                        "建倉適性": setup_status, "雷達警報": alert_msg, "診斷說明": diag_desc,
                        "最新建議": latest['Advice_Action']
                    })
            except Exception:
                pass

        if results:
            st.dataframe(pd.DataFrame(results), use_container_width=True)
