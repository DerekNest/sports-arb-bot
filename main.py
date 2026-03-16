import requests
import pandas as pd
import os
from datetime import datetime
import json
import time

HISTORY_FILE = "arb_history.json"
EXPIRATION_SECONDS = 86400  # 24 hours in seconds

def load_history():
    # If file exists, load it and prune entries older than 24 hours
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r") as f:
                history = json.load(f)
                
                # Failsafe: if previous history was saved as a list, reset to empty dictionary
                if isinstance(history, list):
                    return {}
                
                current_time = time.time()
                # Rebuild dictionary keeping only signatures less than 24 hours old
                return {
                    sig: ts for sig, ts in history.items() 
                    if current_time - ts < EXPIRATION_SECONDS
                }
        except json.JSONDecodeError:
            return {}
    return {}

def save_history(history):
    # Persist the dictionary of active signatures and their timestamps
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f)

# --- CONFIGURATION ---
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
API_KEY = os.environ.get("BETTINGPROS_API_KEY")

MIN_ROI = 0.5
MAX_ROI = 20.0

BOOK_MAP = {
    10: "FanDuel", 12: "DraftKings", 13: "Caesars", 14: "BetMGM",
    15: "BetRivers", 33: "ESPN Bet", 36: "Fliff", 37: "Pinnacle", 
    49: "HardRock", 60: "Novig", 68: "Sporttrade", 73: "Polymarket"
}

def send_discord_alert(arb):
    if not DISCORD_WEBHOOK_URL:
        print("❌ No Discord URL found. Skipping alert.")
        return

    embed = {
        "title": f"🚨 {arb['ROI']:.2f}% ARB FOUND: {arb['Player']}",
        "color": 5763719,
        "fields": [
            {"name": "Line", "value": str(arb['Line']), "inline": True},
            {"name": "Profit", "value": f"{arb['ROI']:.2f}% Risk-Free", "inline": True},
            {"name": "BET OVER", "value": arb['Bet_Over'], "inline": False},
            {"name": "BET UNDER", "value": arb['Bet_Under'], "inline": False}
        ],
        "footer": {"text": f"Found at {datetime.now().strftime('%H:%M:%S')} via GitHub Actions"}
    }
    
    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]})
        print(f"   🚀 Alert sent for {arb['Player']}")
    except Exception as e:
        print(f"   ⚠️ Failed to send alert: {e}")

def get_data():
    base_url = "https://api.bettingpros.com/v3/offers"
    event_ids = "27207:27208:27209:27210:27211:27212:27213:27214"
    
    headers = {
        'sec-ch-ua-platform': '"Linux"',
        'Referer': 'https://www.bettingpros.com/',
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'x-api-key': API_KEY, 
    }

    all_rows = []
    current_page = 1
    
    # Run a shorter scan (10 pages) to save time/resources
    while current_page <= 10:
        url = f"{base_url}?sport=NBA&market_id=156&event_id={event_ids}&book_id=null&limit=10&page={current_page}"
        try:
            response = requests.get(url, headers=headers)
            offers = response.json().get('offers', [])
            if not offers: break

            for offer in offers:
                participants = offer.get('participants', [])
                if not participants: continue
                player_name = participants[0].get('name')

                for selection in offer.get('selections', []):
                    label = selection.get('label')
                    for book in selection.get('books', []):
                        book_id = book.get('id')
                        if book_id in [0, 73]: continue # Filter Glitches
                        
                        book_name = BOOK_MAP.get(book_id, f"Book_{book_id}")
                        for line in book.get('lines', []):
                            all_rows.append({
                                "Player": player_name,
                                "Book": book_name,
                                "Type": label,
                                "Line": line.get('line'),
                                "Odds": line.get('cost')
                            })
            current_page += 1
        except:
            break
            
    return pd.DataFrame(all_rows)

def find_arbs(df):
    if df.empty: return []
    
    def get_decimal(american):
        if american > 0: return 1 + (american / 100)
        else: return 1 + (100 / abs(american))
    
    df['Decimal'] = df['Odds'].apply(get_decimal)
    arbs = []
    grouped = df.groupby(['Player', 'Line'])
    
    for (player, line), group in grouped:
        overs = group[group['Type'] == 'Over']
        unders = group[group['Type'] == 'Under']
        if overs.empty or unders.empty: continue

        best_over = overs.loc[overs['Decimal'].idxmax()]
        best_under = unders.loc[unders['Decimal'].idxmax()]
        
        imp_prob = (1 / best_over['Decimal']) + (1 / best_under['Decimal'])
        
        if imp_prob < 1.0:
            roi = ((1 / imp_prob) - 1) * 100
            if MIN_ROI < roi < MAX_ROI:
                arbs.append({
                    "Player": player,
                    "Line": line,
                    "Bet_Over": f"{best_over['Book']} ({best_over['Odds']})",
                    "Bet_Under": f"{best_under['Book']} ({best_under['Odds']})",
                    "ROI": roi
                })
    return arbs

if __name__ == "__main__":
    print("🤖 Starting Cloud Scan...")
    if not API_KEY:
        print("❌ CRITICAL: No API Key found in environment variables!")
    else:
        df = get_data()
        arbs = find_arbs(df)
        
        if arbs:
            print(f"✅ FOUND {len(arbs)} OPPORTUNITIES.")
            
            history = load_history()
            current_time = time.time()
            
            for arb in arbs:
                arb_signature = f"{arb['Player']}_{arb['Line']}_{arb['Bet_Over']}_{arb['Bet_Under']}"
                
                if arb_signature not in history:
                    print(f"   💰 NEW ARB: {arb['Player']} ({arb['ROI']:.2f}%)")
                    send_discord_alert(arb)
                    history[arb_signature] = current_time
                else:
                    print(f"   ⏳ SKIPPED (Already Alerted): {arb['Player']} ({arb['ROI']:.2f}%)")
            
            # Always save history to ensure expired entries are permanently removed from the JSON file
            save_history(history)
        else:
            print("😴 No arbs found.")
