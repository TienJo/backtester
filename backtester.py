def run(self):
        df = self.df.copy()
        cash = self.initial_capital
        shares = 0
        avg_cost = 0.0
        
        took_profit_15 = False
        took_atr_profit = False
        highest_price_since_entry = 0.0
        last_add_price = 0.0
        days_since_last_buy = 0  # 紀錄買入後的持股天數，避免隔天立刻清倉
        
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
            rsi14 = float(today['RSI14']) if not np.isnan(today['RSI14']) else 50.0
            temp = float(today['Temperature']) if not np.isnan(today['Temperature']) else 50.0
            atr14 = float(today['ATR14']) if not np.isnan(today['ATR14']) else 0.0
            
            yesterday_close = float(yesterday['Close'])
            yesterday_ma20 = float(yesterday['MA20']) if not np.isnan(yesterday['MA20']) else yesterday_close
            yesterday_ma10 = float(yesterday['MA10']) if not np.isnan(yesterday['MA10']) else yesterday_close

            # RSI 底背離
            price_low_20 = low < prev_20['Low'].min()
            min_rsi_20 = prev_20['RSI14'].min()
            rsi_bullish_div = price_low_20 and (rsi14 > min_rsi_20)
            is_bullish_candle = price > open_p

            if shares > 0:
                days_since_last_buy += 1
                if price > highest_price_since_entry:
                    highest_price_since_entry = price

            # ==========================================
            # 1. 出場與風控機制 (修復洗盤問題)
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
                    days_since_last_buy = 0
                    sold_today = True

                    trades.append({
                        "Date": date, "日期": date_str, "動作": "全數賣出", "類別": "Sell", "原因": "🛑 8% 硬停損止血", 
                        "成交價": price, "股數": sell_shares, "損益": round(pnl, 2), "報酬率": f"{unrealized_pct:+.2f}%", 
                        "當下倉位": "0 股 (0.0%)", "剩餘現金": round(cash, 2)
                    })

                # 🚨 2. 有效跌破 MA20 清倉
                # 條件：買入超過 3 天 (過濾假拉回) 且【連續 2 天收在 MA20 下方】或【跌破 MA20 超過 1.5%】
                elif days_since_last_buy >= 3 and (price < ma20 * 0.985 or (price < ma20 and yesterday_close < yesterday_ma20)):
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
                    days_since_last_buy = 0
                    sold_today = True

                    trades.append({
                        "Date": date, "日期": date_str, "動作": "清倉離場", "類別": "Sell", "原因": "🚨 有效跌破 MA20 清倉", 
                        "成交價": price, "股數": sell_shares, "損益": round(pnl, 2), "報酬率": f"{unrealized_pct:+.2f}%", 
                        "當下倉位": "0 股 (0.0%)", "剩餘現金": round(cash, 2)
                    })

                # 🔥 3. 沸點反轉全清倉
                elif temp > 95.0 and (price < float(today['Low']) or price < ma5):
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
                    days_since_last_buy = 0
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
            if temp <= 80.0:
                # 方式 A：🥶 極寒抄底 (左側建倉 15%)
                if shares == 0:
                    if rsi_bullish_div and temp < 35.0 and is_bullish_candle:
                        buy_budget = self.initial_capital * 0.15
                        buy_shares = int(buy_budget / price)
                        if buy_shares > 0 and cash >= buy_shares * price:
                            cash -= buy_shares * price
                            shares = buy_shares
                            avg_cost = price
                            last_add_price = price
                            highest_price_since_entry = price
                            took_profit_15 = False
                            took_atr_profit = False
                            days_since_last_buy = 0

                            curr_val = cash + (shares * price)
                            pos_pct = (shares * price / curr_val * 100) if curr_val > 0 else 0
                            trades.append({
                                "Date": date, "日期": date_str, "動作": "建倉(15%)", "類別": "Buy", "原因": "🥶 極寒抄底", 
                                "成交價": price, "股數": buy_shares, "損益": 0.0, "報酬率": "0.00%", 
                                "當下倉位": f"{shares:,} 股 ({pos_pct:.1f}%)", "剩餘現金": round(cash, 2)
                            })

                    # 方式 B：📈 多頭順勢建倉 (右側建倉 30%，避免錯過 5~6 月的大漲)
                    elif ma5 > ma10 and ma10 > ma20 and price > ma20 and temp >= 45.0:
                        buy_budget = self.initial_capital * 0.30
                        buy_shares = int(buy_budget / price)
                        if buy_shares > 0 and cash >= buy_shares * price:
                            cash -= buy_shares * price
                            shares = buy_shares
                            avg_cost = price
                            last_add_price = price
                            highest_price_since_entry = price
                            took_profit_15 = False
                            took_atr_profit = False
                            days_since_last_buy = 0

                            curr_val = cash + (shares * price)
                            pos_pct = (shares * price / curr_val * 100) if curr_val > 0 else 0
                            trades.append({
                                "Date": date, "日期": date_str, "動作": "建倉(30%)", "類別": "Buy", "原因": "📈 多頭排列順勢建倉", 
                                "成交價": price, "股數": buy_shares, "損益": 0.0, "報酬率": "0.00%", 
                                "當下倉位": f"{shares:,} 股 ({pos_pct:.1f}%)", "剩餘現金": round(cash, 2)
                            })

                # 既有部位加碼
                elif shares > 0 and cash >= (price * 100):
                    price_change_from_last = (price - last_add_price) / last_add_price
                    
                    # 階梯加碼 (只在站穩 MA20 時才加碼)
                    if (price_change_from_last <= -0.10 and price > ma20) or (price_change_from_last >= 0.08 and price > ma20):
                        add_budget = self.initial_capital * 0.10
                        add_shares = int(min(add_budget, cash) / price)
                        if add_shares > 0:
                            total_cost = (shares * avg_cost) + (add_shares * price)
                            shares += add_shares
                            avg_cost = total_cost / shares
                            cash -= (add_shares * price)
                            last_add_price = price
                            days_since_last_buy = 0

                            curr_val = cash + (shares * price)
                            pos_pct = (shares * price / curr_val * 100) if curr_val > 0 else 0
                            trades.append({
                                "Date": date, "日期": date_str, "動作": "加碼1層", "類別": "Buy", "原因": f"📈 動態加碼 ({price_change_from_last:+.1f}%)", 
                                "成交價": price, "股數": add_shares, "損益": 0.0, "報酬率": "0.00%", 
                                "當下倉位": f"{shares:,} 股 ({pos_pct:.1f}%)", "剩餘現金": round(cash, 2)
                            })

                    # 🚀 黃金突破打滿
                    breakout_ma10 = (price > ma10) and (yesterday_close <= yesterday_ma10)
                    ma10_turning_up = ma10 >= yesterday_ma10
                    above_life_line = price >= ma20
                    
                    if breakout_ma10 and ma10_turning_up and above_life_line and (35.0 <= temp <= 80.0):
                        add_shares = int(cash / price)
                        if add_shares > 0:
                            total_cost = (shares * avg_cost) + (add_shares * price)
                            shares += add_shares
                            avg_cost = total_cost / shares
                            cash -= (add_shares * price)
                            last_add_price = price
                            days_since_last_buy = 0

                            curr_val = cash + (shares * price)
                            pos_pct = (shares * price / curr_val * 100) if curr_val > 0 else 0
                            trades.append({
                                "Date": date, "日期": date_str, "動作": "黃金突破打滿", "類別": "Buy", "原因": "🚀 黃金突破打滿", 
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
