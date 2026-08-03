# ==========================================
# 3. 歷史回測引擎 (修復頂部亂加碼與空頭頻繁抄底)
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
        cooldown_counter = 0  # 🛡️ 清倉冷卻計數器
        last_trade_was_loss = False # 紀錄上一筆是否停損
        
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

            # 冷卻期倒數
            if cooldown_counter > 0:
                cooldown_counter -= 1

            # RSI 底背離
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

                # 🛑 1. 8% 硬停損止血
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
                    cooldown_counter = 5  # 觸發 5 天冷卻期
                    last_trade_was_loss = True
                    sold_today = True

                    trades.append({
                        "Date": date, "日期": date_str, "動作": "全數賣出", "類別": "Sell", "原因": "🛑 8% 硬停損止血", 
                        "成交價": price, "股數": sell_shares, "損益": round(pnl, 2), "報酬率": f"{unrealized_pct:+.2f}%", 
                        "當下倉位": "0 股 (0.0%)", "剩餘現金": round(cash, 2)
                    })

                # 🚨 2. MA20 生命線清倉
                elif (has_crossed_ma20 or took_profit_15 or took_atr_profit) and price < ma20:
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
                    cooldown_counter = 5  # 觸發 5 天冷卻期
                    last_trade_was_loss = (pnl < 0)
                    sold_today = True

                    trades.append({
                        "Date": date, "日期": date_str, "動作": "清倉離場", "類別": "Sell", "原因": "🚨 跌破 MA20 生命線清倉", 
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
                    cooldown_counter = 5  # 觸發 5 天冷卻期
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
            # 2. 進場與加倉機制 (帶有冷卻期與空頭過濾)
            # ==========================================
            # 🛑 冷卻期內禁止任何買入操作
            if cooldown_counter == 0 and temp <= 80.0:
                
                # 大趨勢過濾：價格在 MA60 下方且 MA20 下彎時，判定為強空頭
                is_strong_downtrend = (price < ma60) and (ma20 < yesterday_ma20)

                # 第一步：🥶 極寒抄底
                if shares == 0:
                    # 如果上一筆是虧損，提高抄底標準（RSI 背離差額必須 > 20 且站上 MA5）
                    strict_rsi_cond = (rsi_diff > 20.0 and price > ma5) if last_trade_was_loss else True

                    # 強空頭趨勢下，禁止盲目抄底，除非站回 MA5 且背離極強
                    if rsi_bullish_div and temp < 35.0 and is_bullish_candle and strict_rsi_cond and not is_strong_downtrend:
                        buy_budget = self.initial_capital * 0.15
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
                                "Date": date, "日期": date_str, "動作": "建倉(15%)", "類別": "Buy", "原因": "🥶 極寒抄底 (20日背離+陽線)", 
                                "成交價": price, "股數": buy_shares, "損益": 0.0, "報酬率": "0.00%", 
                                "當下倉位": f"{shares:,} 股 ({pos_pct:.1f}%)", "剩餘現金": round(cash, 2)
                            })

                # 第二步：既有部位加碼
                elif shares > 0 and cash >= (price * 100):
                    price_change_from_last = (price - last_add_price) / last_add_price
                    
                    # 階梯式加碼 (必須高於 MA20 才能逢低加碼)
                    if (price_change_from_last <= -0.10 and price > ma20) or price_change_from_last >= 0.05:
                        add_budget = self.initial_capital * 0.10
                        add_shares = int(min(add_budget, cash) / price)
                        if add_shares > 0:
                            total_cost = (shares * avg_cost) + (add_shares * price)
                            shares += add_shares
                            avg_cost = total_cost / shares
                            cash -= (add_shares * price)
                            last_add_price = price

                            curr_val = cash + (shares * price)
                            pos_pct = (shares * price / curr_val * 100) if curr_val > 0 else 0
                            trades.append({
                                "Date": date, "日期": date_str, "動作": "加碼1層", "類別": "Buy", "原因": f"📈 動態加碼 ({price_change_from_last:+.1f}%)", 
                                "成交價": price, "股數": add_shares, "損益": 0.0, "報酬率": "0.00%", 
                                "當下倉位": f"{shares:,} 股 ({pos_pct:.1f}%)", "剩餘現金": round(cash, 2)
                            })

                    # 🚀 黃金突破打滿條件
                    breakout_ma10 = (price > ma10) and (yesterday_close <= yesterday_ma10)
                    ma10_turning_up = ma10 >= yesterday_ma10
                    above_life_line = price >= ma20
                    not_new_low = low >= prev_10['Low'].min()
                    
                    if breakout_ma10 and ma10_turning_up and above_life_line and not_new_low and (35.0 <= temp <= 80.0):
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
                                "Date": date, "日期": date_str, "動作": "黃金突破打滿", "類別": "Buy", "原因": "🚀 黃金突破打滿 (MA10翻揚+站上MA20)", 
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
