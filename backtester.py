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
    def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
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
        
        # 計算布林上軌 5 日趨勢
        df['BB_Upper_5D_Diff'] = df['BB_Upper'].diff(5)

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

        # 🛠️ 策略引擎：包含「MACD死叉 且 現價高於MA20不到3%無條件清倉」
        action_list = []
        reason_list = []

        last_buy_index = -999  # 上次建倉索引
        in_position = False    # 目前持倉狀態
        position_ratio = 0.0   # 0.0: 無持倉, 0.5: 半倉, 1.0: 滿倉

        for i in range(len(df)):
            if i < 20:
                action_list.append("資料載入中")
                reason_list.append("計算指標所需日數不足")
                continue

            close_p = df['Close'].iloc[i]
            high_p = df['High'].iloc[i]

            bb_u = df['BB_Upper'].iloc[i]
            bb_m = df['MA20'].iloc[i]
            bb_u_diff5 = df['BB_Upper_5D_Diff'].iloc[i]
            bw = df['BB_Bandwidth'].iloc[i]

            rsi = df['RSI14'].iloc[i]
            rsi_diff_1 = df['RSI_Diff_1D'].iloc[i]

            dif = df['DIF'].iloc[i]
            dea = df['DEA'].iloc[i]
            prev_dif = df['DIF'].iloc[i-1]
            prev_dea = df['DEA'].iloc[i-1]
            hist = df['MACD_Hist'].iloc[i]
            prev_hist = df['MACD_Hist'].iloc[i-1]

            # 🎯 MACD 當天死亡交叉 (DIF 由上往下穿越 DEA)
            macd_dc = (dif < dea) and (prev_dif >= prev_dea)
            
            # 🎯 現價高於 MA20 不到 3% (即 close_p < bb_m * 1.03)
            close_near_or_below_ma20 = close_p < (bb_m * 1.03)

            # 🎯【唯一建倉條件】：近 5 日內 RSI 曾低於 30，且當日 RSI 單日強彈 >= 8 點
            rsi_recent_oversold = (df['RSI14'].iloc[max(0, i-5):i+1] < 30.0).any()
            rsi_surge_8 = rsi_diff_1 >= 8.0

            # 🎯【補滿倉條件】：價格站上 MA20 且布林上軌 5 日內呈上升趨勢
            above_ma20 = close_p >= bb_m
            bb_upper_uptrend_5d = bb_u_diff5 > 0

            # 頂背離判定
            price_10d_high = close_p > df['Close'].iloc[i-10:i].max()
            rsi_is_high = rsi >= 65.0
            rsi_lower_than_peak = rsi < df['RSI14'].iloc[i-10:i].max()
            macd_hist_shrink = hist < prev_hist
            bear_divergence = price_10d_high and rsi_is_high and rsi_lower_than_peak and macd_hist_shrink

            # 冷卻期判斷
            cd_active = (i - last_buy_index) < 5

            act = "觀望待變"
            rsn = "三指標處於常態區域，未達 RSI<30 且強彈 >8 點之唯一建倉門檻"

            # 🛑 1. MACD 當天死叉 且 現價高於 MA20 不到 3% —— 果斷全數清倉離場 (最高優先級)
            if macd_dc and close_near_or_below_ma20:
                act = "🛑 MACD死叉+貼近中軌(全數離場)"
                rsn = f"當日 MACD 出現死叉，且現價(${close_p:.2f})高於MA20(${bb_m:.2f})不到3%，支撐力道不足，無條件清倉離場"
                in_position = False
                position_ratio = 0.0

            # ⚠️ 2. 高檔頂背離 —— 提前停利離場
            elif bear_divergence and (high_p >= bb_u * 0.995):
                act = "⚠️ 背離獲利了結"
                rsn = "價格高檔創新高但 RSI/MACD 出現頂背離，建議獲利離場"
                in_position = False
                position_ratio = 0.0

            # 🟢 3.【唯一建半倉條件】：RSI 超賣 + 單日強彈 >= 8 點
            elif not in_position and not cd_active and rsi_recent_oversold and rsi_surge_8:
                act = "🟢 RSI強彈(建半倉)"
                rsn = f"近5日曾進入超賣區(RSI<30)，今日RSI爆發大漲{rsi_diff_1:.1f}點(>=8)，買盤強勢介入，建議試單建半倉"
                last_buy_index = i
                in_position = True
                position_ratio = 0.5

            # 🚀 4. 抄底第二步：站上 MA20 + 布林上軌5日走揚 —— 補滿倉 (加碼)
            elif (position_ratio == 0.5) and above_ma20 and bb_upper_uptrend_5d:
                act = "🚀 趨勢擴張(補滿倉)"
                rsn = f"已有半倉，今日股價成功站上MA20({bb_m:.2f})且布林上軌5日持續走揚，波段多頭確立，建議補滿倉"
                last_buy_index = i
                position_ratio = 1.0

            # 📈 5. 持倉期間的動態維護
            elif in_position:
                if (dif > 0) and (rsi > 50) and (abs(df['Low'].iloc[i] - bb_m) / bb_m <= 0.015) and above_ma20 and (i - last_buy_index > 3):
                    act = "📈 順勢拉回(加碼)"
                    rsn = "持倉中，股價拉回測試布林中軌(MA20)獲支撐，可分批加碼"
                elif (high_p >= bb_u) and (rsi >= 70) and (hist > 0):
                    act = "🔥 強勢軌道游走"
                    rsn = f"持倉中({int(position_ratio*100)}%)，股價沿布林上軌強勢游走，主升段續抱"
                else:
                    act = "✊ 續抱觀察"
                    rsn = f"持倉中({int(position_ratio*100)}%)，行情沿趨勢運行，請繼續持股觀望"

            # ⚠️ 6. 常態警示
            elif bw < 0.08:
                act = "🟡 盤整變盤在即"
                rsn = "布林極度收窄（縮口），變盤在即；靜待 RSI 超賣強彈建倉訊號"

            action_list.append(act)
            reason_list.append(rsn)

        df['Advice_Action'] = action_list
        df['Advice_Reason'] = reason_list

        return df

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
st.set_page_config(page_title="專業布林+MACD+RSI趨勢提醒工具", layout="wide", page_icon="📈")

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

st.title("📈 布林通道 + MACD + RSI 趨勢技術分析工具")

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
    bt_start = st.date_input("開始日期", value=default_start)
with col_bt3:
    default_end_str = db.get("bt_end", datetime.now().strftime("%Y-%m-%d"))
    default_end = datetime.strptime(default_end_str, "%Y-%m-%d")
    bt_end = st.date_input("結束日期", value=default_end)

if st.button("🚀 載入 K 線與三指標組合分析", type="primary"):
    start_str = bt_start.strftime("%Y-%m-%d")
    end_str = bt_end.strftime("%Y-%m-%d")
    
    db["bt_start"] = start_str
    db["bt_end"] = end_str
    save_db(db)

    with st.spinner(f"正在擷取 {bt_symbol} 行情數據與計算布林+MACD+RSI..."):
        try:
            fetch_start = (bt_start - timedelta(days=120)).strftime("%Y-%m-%d")
            df_raw, src_bt = cached_fetch_ohlc(bt_symbol, start_date=fetch_start, end_date=end_str)
            
            if df_raw.empty or len(df_raw) < 10:
                st.error("歷史數據不足，無法繪製K線圖。請確認代碼或重新選擇區間。")
            else:
                df_calc = TechnicalAnalysisEngine.calculate_indicators(df_raw)
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
                    
                    if "停損" in act_text or "死叉" in act_text or "離場" in act_text:
                        st.error(f"**【操作建議】{act_text}** — {rsn_text}")
                    elif "減碼" in act_text or "假突破" in act_text or "警惕" in act_text:
                        st.warning(f"**【操作建議】{act_text}** — {rsn_text}")
                    elif "趨勢" in act_text or "建倉" in act_text or "建半倉" in act_text or "補滿倉" in act_text or "加碼" in act_text or "續抱" in act_text:
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
                    st.markdown("#### 🎯 K線(含均線MA5/10/20/60與布林通道)、市場溫度、RSI 與 MACD 四圖對照")

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
                    fig.add_hline(y=80, line_dash="dash", line_color="#FF1744", row=2, col=1)
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
