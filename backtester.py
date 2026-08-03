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
            if not df.empty and len(df) >= 10:
                return df, "東方財富 (EastMoney)"
        except Exception:
            pass

        if clean_code.isdigit() and len(clean_code) == 6:
            try:
                df = self._fetch_eastmoney_fund(clean_code)
                if not df.empty and len(df) >= 10:
                    return df, "天天基金 (Tiantian Fund)"
            except Exception:
                pass

        try:
            df = self._fetch_tencent(symbol, clean_code)
            if not df.empty and len(df) >= 10:
                return df, "騰訊財經 (Tencent)"
        except Exception:
            pass

        try:
            df = self._fetch_yfinance(symbol, start_date, end_date)
            if not df.empty and len(df) >= 10:
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

@st.cache_data(ttl=300)
def cached_fetch_ohlc(symbol: str, start_date: str = None, end_date: str = None):
    return data_engine.fetch_ohlc(symbol, start_date, end_date)

# ==========================================
# 2. 技術指標與量價分析計算引擎
# ==========================================
class TechnicalAnalysisEngine:
    @staticmethod
    def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        
        # 均線計算
        df['MA5'] = df['Close'].rolling(5).mean()
        df['MA10'] = df['Close'].rolling(10).mean()
        df['MA20'] = df['Close'].rolling(20).mean()
        df['MA60'] = df['Close'].rolling(60).mean()

        # 量能指標與量比計算
        df['Vol_MA5'] = df['Volume'].rolling(5).mean()
        df['Vol_MA10'] = df['Volume'].rolling(10).mean()
        df['Vol_MA20'] = df['Volume'].rolling(20).mean()

        # 當日量比（相對於前5日均量）、五日量比（5日均量對比前5日均量）、十日量比（10日均量對比前10日均量）
        df['Daily_Vol_Ratio'] = df['Volume'] / df['Vol_MA5'].shift(1)
        df['Vol_Ratio_5D'] = df['Vol_MA5'] / df['Vol_MA5'].shift(5)
        df['Vol_Ratio_10D'] = df['Vol_MA10'] / df['Vol_MA10'].shift(10)

        # RSI 計算
        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        df['RSI14'] = 100 - (100 / (1 + rs))

        # 5日與20日看多/看空結構分析
        # 5日看多：收盤 > MA5 且 MA5 向上；5日看空：收盤 < MA5 且 MA5 向下
        ma5_diff = df['MA5'].diff()
        df['Bull_5D'] = (df['Close'] > df['MA5']) & (ma5_diff > 0)
        df['Bear_5D'] = (df['Close'] < df['MA5']) & (ma5_diff < 0)

        # 20日看多：收盤 > MA20 且 MA20 向上且 MA5 > MA20；20日看空：收盤 < MA20 且 MA20 向下且 MA5 < MA20
        ma20_diff = df['MA20'].diff()
        df['Bull_20D'] = (df['Close'] > df['MA20']) & (ma20_diff > 0) & (df['MA5'] > df['MA20'])
        df['Bear_20D'] = (df['Close'] < df['MA20']) & (ma20_diff < 0) & (df['MA5'] < df['MA20'])

        # 均線金叉與趨勢反轉結構分析
        # 短線金叉：當日或近期 MA5 向上穿過 MA10 / MA20
        df['GC_MA5_MA10'] = (df['MA5'] > df['MA10']) & (df['MA5'].shift(1) <= df['MA10'].shift(1))
        df['GC_MA5_MA20'] = (df['MA5'] > df['MA20']) & (df['MA5'].shift(1) <= df['MA20'].shift(1))
        df['GC_MA10_MA20'] = (df['MA10'] > df['MA20']) & (df['MA10'].shift(1) <= df['MA20'].shift(1))

        # 5日內量價背離判定（頂背離：價格創新高但成交量/RSI縮減；底背離：價格創新低但成交量/RSI不創新低）
        price_5d_max = df['Close'].rolling(5).max()
        price_5d_min = df['Close'].rolling(5).min()
        vol_5d_max = df['Volume'].rolling(5).max()
        rsi_5d_min = df['RSI14'].rolling(5).min()

        # 頂背離：當前價格創5日新高但成交量明顯低於近5日最大量
        df['Bear_Divergence_5D'] = (df['Close'] == price_5d_max) & (df['Volume'] < vol_5d_max * 0.8) & (df['Close'] > df['Close'].shift(1))
        # 底背離：當前價格創5日新低但RSI未創新低且量能放大
        df['Bull_Divergence_5D'] = (df['Close'] == price_5d_min) & (df['RSI14'] > rsi_5d_min) & (df['Volume'] > df['Vol_MA5'])

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
st.set_page_config(page_title="專業K線與量價結構分析工具", layout="wide", page_icon="📈")

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

st.title("📈 K 線與量價趨勢結構工具")

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

if st.button("🚀 載入K線與量價結構分析", type="primary"):
    start_str = bt_start.strftime("%Y-%m-%d")
    end_str = bt_end.strftime("%Y-%m-%d")
    
    # 記憶瀏覽時間區段
    db["bt_start"] = start_str
    db["bt_end"] = end_str
    save_db(db)

    with st.spinner(f"正在擷取 {bt_symbol} 行情數據與計算量價指標..."):
        try:
            fetch_start = (bt_start - timedelta(days=90)).strftime("%Y-%m-%d")
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
                    
                    st.markdown("---")
                    st.markdown("#### 📊 最新量比與趨勢結構數據總覽")
                    
                    m1, m2, m3, m4, m5 = st.columns(5)
                    
                    # 1. 當日量比
                    d_vr = latest['Daily_Vol_Ratio']
                    m1.metric("當日量比 (對比5日均量)", f"{d_vr:.2f}" if not np.isnan(d_vr) else "N/A", 
                              delta="放量" if d_vr >= 1.2 else ("縮量" if d_vr <= 0.8 else "持平"))

                    # 2. 5日量比 & 10日量比
                    vr_5d = latest['Vol_Ratio_5D']
                    vr_10d = latest['Vol_Ratio_10D']
                    m2.metric("5日量比 / 10日量比", f"{vr_5d:.2f} / {vr_10d:.2f}" if not np.isnan(vr_5d) else "N/A")

                    # 3. 5日內量價背離監控
                    recent_5d = df_sub.iloc[-5:]
                    has_bear_div = recent_5d['Bear_Divergence_5D'].any()
                    has_bull_div = recent_5d['Bull_Divergence_5D'].any()
                    
                    if has_bear_div and has_bull_div:
                        div_status = "⚠️ 頂背離與底背離交替"
                    elif has_bear_div:
                        div_status = "🚨 5日出現頂背離 (價高量縮)"
                    elif has_bull_div:
                        div_status = "🟢 5日出現底背離 (價低量增)"
                    else:
                        div_status = "無明顯量價背離"
                    m3.metric("5日內量價背離狀態", div_status)

                    # 4. 均線金叉結構
                    has_gc_5_10 = recent_5d['GC_MA5_MA10'].any()
                    has_gc_5_20 = recent_5d['GC_MA5_MA20'].any()
                    has_gc_10_20 = recent_5d['GC_MA10_MA20'].any()
                    
                    if has_gc_5_20 or has_gc_10_20:
                        gc_status = "🚀 均線金叉 (強趨勢反轉預測)"
                    elif has_gc_5_10:
                        gc_status = "📈 MA5上穿MA10 (短線金叉)"
                    else:
                        gc_status = "無新金叉結構"
                    m4.metric("均線金叉反轉結構", gc_status)

                    # 5. 5日與20日看多/看空格局
                    b5 = "看多 🟢" if latest['Bull_5D'] else ("看空 🔴" if latest['Bear_5D'] else "震盪 🟡")
                    b20 = "看多 🟢" if latest['Bull_20D'] else ("看空 🔴" if latest['Bear_20D'] else "震盪 🟡")
                    m5.metric("5日 / 20日格局", f"5日:{b5} | 20日:{b20}")

                    st.markdown("---")
                    st.markdown("#### 🎯 K線與成交量技術分析主圖")

                    # 繪製主副圖 (Row 1: K線+MA, Row 2: 成交量)
                    fig = make_subplots(
                        rows=2, cols=1, 
                        shared_xaxes=True, 
                        vertical_spacing=0.05, 
                        row_heights=[0.7, 0.3],
                        subplot_titles=(f"{bt_symbol} K線與均線結構", "成交量與5日/10日均量線")
                    )

                    # K線圖
                    fig.add_trace(go.Candlestick(
                        x=df_sub.index,
                        open=df_sub['Open'], high=df_sub['High'],
                        low=df_sub['Low'], close=df_sub['Close'],
                        name='K線',
                        increasing_line_color='#26a69a', decreasing_line_color='#ef5350'
                    ), row=1, col=1)

                    # 均線
                    fig.add_trace(go.Scatter(x=df_sub.index, y=df_sub['MA5'], mode='lines', name='MA5', line=dict(color='#FF9800', width=1.2)), row=1, col=1)
                    fig.add_trace(go.Scatter(x=df_sub.index, y=df_sub['MA10'], mode='lines', name='MA10', line=dict(color='#2196F3', width=1.2)), row=1, col=1)
                    fig.add_trace(go.Scatter(x=df_sub.index, y=df_sub['MA20'], mode='lines', name='MA20', line=dict(color='#9C27B0', width=1.5)), row=1, col=1)
                    fig.add_trace(go.Scatter(x=df_sub.index, y=df_sub['MA60'], mode='lines', name='MA60', line=dict(color='#607D8B', width=1.5)), row=1, col=1)

                    # 金叉標註點
                    gc_points = df_sub[df_sub['GC_MA5_MA20'] | df_sub['GC_MA5_MA10']]
                    if not gc_points.empty:
                        fig.add_trace(go.Scatter(
                            x=gc_points.index,
                            y=gc_points['Low'] * 0.98,
                            mode='markers+text',
                            name='均線金叉點',
                            marker=dict(symbol='triangle-up', size=11, color='#00E676'),
                            text=['金叉' for _ in range(len(gc_points))],
                            textposition='bottom center'
                        ), row=1, col=1)

                    # 背離標註點
                    div_points = df_sub[df_sub['Bear_Divergence_5D'] | df_sub['Bull_Divergence_5D']]
                    if not div_points.empty:
                        fig.add_trace(go.Scatter(
                            x=div_points.index,
                            y=div_points['High'] * 1.02,
                            mode='markers+text',
                            name='量價背離訊號',
                            marker=dict(symbol='diamond', size=10, color='#FFD600'),
                            text=['背離' for _ in range(len(div_points))],
                            textposition='top center'
                        ), row=1, col=1)

                    # 成交量柱狀圖
                    colors = ['#26a69a' if c >= o else '#ef5350' for c, o in zip(df_sub['Close'], df_sub['Open'])]
                    fig.add_trace(go.Bar(
                        x=df_sub.index, y=df_sub['Volume'],
                        name='成交量', marker_color=colors
                    ), row=2, col=1)

                    fig.add_trace(go.Scatter(x=df_sub.index, y=df_sub['Vol_MA5'], mode='lines', name='5日均量', line=dict(color='#FF9800', width=1)), row=2, col=1)
                    fig.add_trace(go.Scatter(x=df_sub.index, y=df_sub['Vol_MA10'], mode='lines', name='10日均量', line=dict(color='#2196F3', width=1)), row=2, col=1)

                    fig.update_layout(
                        xaxis_rangeslider_visible=False,
                        hovermode="x unified",
                        template="plotly_white",
                        height=650
                    )
                    st.plotly_chart(fig, use_container_width=True)

                    st.markdown("---")
                    st.markdown("#### 📜 每日技術指標與量比數據明細表")
                    
                    show_df = df_sub.copy()
                    show_df['當日量比'] = show_df['Daily_Vol_Ratio'].round(2)
                    show_df['5日量比'] = show_df['Vol_Ratio_5D'].round(2)
                    show_df['10日量比'] = show_df['Vol_Ratio_10D'].round(2)
                    show_df['5日格局'] = np.where(show_df['Bull_5D'], '看多 🟢', np.where(show_df['Bear_5D'], '看空 🔴', '震盪 🟡'))
                    show_df['20日格局'] = np.where(show_df['Bull_20D'], '看多 🟢', np.where(show_df['Bear_20D'], '看空 🔴', '震盪 🟡'))
                    
                    show_df['背離標記'] = np.where(show_df['Bear_Divergence_5D'], '頂背離 🚨', np.where(show_df['Bull_Divergence_5D'], '底背離 🟢', '-'))
                    show_df['金叉結構'] = np.where(show_df['GC_MA5_MA20'], 'MA5跨MA20 🚀', np.where(show_df['GC_MA5_MA10'], 'MA5跨MA10 📈', '-'))

                    show_cols = ["Open", "High", "Low", "Close", "Volume", "當日量比", "5日量比", "10日量比", "5日格局", "20日格局", "背離標記", "金叉結構"]
                    
                    display_df = show_df[show_cols].sort_index(ascending=False)
                    st.dataframe(display_df, use_container_width=True)

        except Exception as ex:
            st.error(f"載入數據失敗: {ex}")
