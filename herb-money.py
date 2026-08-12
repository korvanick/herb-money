import urllib.request
import json
import math
import time
import os

LATEST_URL = "https://prices.runescape.wiki/api/v1/osrs/latest"
VOL_URL = "https://prices.runescape.wiki/api/v1/osrs/1h"
TREND_URL = "https://prices.runescape.wiki/api/v1/osrs/5m"
HEADERS = {"User-Agent": "HerbDashboard/8.0 (local)"}
POLL_RATE = 60
HERBS_PER_HOUR = 16000
NATURE_RUNE_ID = 561

GREEN = '\033[92m'
RED = '\033[91m'
RESET = '\033[0m'

# (Grimy ID, Clean ID, Name, Base Manual XP)
HERBS = [
    (199, 249, "Guam", 2.5), (201, 251, "Marrentill", 3.8), 
    (203, 253, "Tarromin", 5.0), (205, 255, "Harralander", 6.3), 
    (207, 257, "Ranarr", 7.5), (3049, 2998, "Toadflax", 8.0),
    (209, 259, "Irit", 8.8), (211, 261, "Avantoe", 10.0), 
    (213, 263, "Kwuarm", 11.3), (3051, 3000, "Snapdragon", 11.8), 
    (215, 265, "Cadantine", 12.5), (2485, 2481, "Lantadyme", 13.1),
    (217, 267, "Dwarf weed", 13.8), (219, 269, "Torstol", 15.0)
]

def fetch_data(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req) as response:
        return json.loads(response.read().decode())['data']

def get_trend(current, previous, is_buy):
    if not previous or current == previous:
        return "  -   "
    
    diff = current - previous
    symbol = "↑" if diff > 0 else "↓"
    
    if is_buy:
        color = RED if diff > 0 else GREEN
    else:
        color = GREEN if diff > 0 else RED
        
    diff_str = f"{abs(diff):<3.0f}"
    return f"{color}{symbol} {diff_str} {RESET}"

def color_profit(val, fmt_spec):
    formatted_val = format(val, fmt_spec)
    color = GREEN if val > 0 else RED
    return f"{color}{formatted_val}{RESET}"

def main():
    while True:
        try:
            latest = fetch_data(LATEST_URL)
            hourly = fetch_data(VOL_URL)
            trend = fetch_data(TREND_URL)
            
            nature_rune_price = latest.get(str(NATURE_RUNE_ID), {}).get('high', 100)
            rune_cost_per_herb = (2 * nature_rune_price) / 27
            
            results = []
            for grimy_id, clean_id, name, base_xp in HERBS:
                if str(grimy_id) not in latest or str(clean_id) not in latest:
                    continue
                    
                buy_grimy = latest[str(grimy_id)]['low']
                
                # Split sell into Insta-sell (low) and Slow-sell (high)
                sell_clean_low = latest[str(clean_id)]['low']
                sell_clean_high = latest[str(clean_id)]['high']
                
                # Trend analysis now bases Sell Direction on the dump price
                prev_buy = trend.get(str(grimy_id), {}).get('avgLowPrice', buy_grimy)
                prev_sell = trend.get(str(clean_id), {}).get('avgLowPrice', sell_clean_low)
                
                buy_dir = get_trend(buy_grimy, prev_buy, is_buy=True)
                sell_dir = get_trend(sell_clean_low, prev_sell, is_buy=False)
                
                vol_data = hourly.get(str(grimy_id), {})
                volume = vol_data.get('highPriceVolume', 0) + vol_data.get('lowPriceVolume', 0)
                
                tax_low = math.floor(sell_clean_low * 0.01)
                tax_high = math.floor(sell_clean_high * 0.01)
                
                prof_low = (sell_clean_low - tax_low) - buy_grimy - rune_cost_per_herb
                prof_high = (sell_clean_high - tax_high) - buy_grimy - rune_cost_per_herb
                
                hr_prof_low = prof_low * HERBS_PER_HOUR
                xp_per_hour = (base_xp / 2) * HERBS_PER_HOUR
                
                results.append((
                    name, buy_grimy, buy_dir, sell_clean_low, sell_clean_high, 
                    sell_dir, prof_low, prof_high, hr_prof_low, xp_per_hour, volume
                ))
                
            # Sort strictly by the conservative Insta-Sell Hourly Profit
            results.sort(key=lambda x: x[8], reverse=True)
            
            os.system('cls' if os.name == 'nt' else 'clear')
            
            print(f"Degrime Dashboard (Updating {POLL_RATE}s) | Nature Rune: {nature_rune_price}gp")
            print(f"Assumes Earth Staff + Rune Pouch (27 items/cast) @ {HERBS_PER_HOUR:,} herbs/hr")
            print("-" * 105)
            print(f"{'Herb':<12} | {'Buy':<5} | {'B-Dir':<6} | {'Sell-L':<6} | {'Sell-H':<6} | {'S-Dir':<6} | {'Prof-L':<7} | {'Prof-H':<7} | {'Prof/Hr(L)':<11} | {'1h Vol'}")
            print("-" * 105)
            
            for name, buy, b_dir, s_low, s_high, s_dir, p_low, p_high, hr_p_low, xp_hr, vol in results:
                vol_warn = "!" if vol < 2000 else " "
                
                c_p_low = color_profit(p_low, ">7.2f")
                c_p_high = color_profit(p_high, ">7.2f")
                c_hr_p_low = color_profit(hr_p_low, ">11,.0f")
                
                print(f"{name:<12} | {buy:<5} | {b_dir} | {s_low:<6} | {s_high:<6} | {s_dir} | {c_p_low} | {c_p_high} | {c_hr_p_low} | {vol:<5}{vol_warn}")
                
            print("-" * 105)
            magic_xp = 83 * (HERBS_PER_HOUR / 27)
            print(f"Passive Magic XP/Hr: {magic_xp:,.0f} | Base Herb XP sorting omitted for terminal width.")
            
            time.sleep(POLL_RATE)
            
        except Exception as e:
            print(f"Network error: {e}. Retrying in {POLL_RATE} seconds...")
            time.sleep(POLL_RATE)

if __name__ == "__main__":
    main()
