from flask import Flask, render_template, request, jsonify  # Add jsonify
import requests
import json
from bs4 import BeautifulSoup

app = Flask(__name__)

# --- NEW: Load train data once when the application starts ---
try:
    with open('train_list.json', 'r', encoding='utf-8') as f:
        train_list = json.load(f)
except FileNotFoundError:
    train_list = []
    print("WARNING: train_data.json not found. Search will not work.")


# --- NEW: Search endpoint for autocomplete ---
@app.route('/search')
def search():
    term = request.args.get('term', '').lower()
    if not term:
        return jsonify([])

    # Find trains where the name or number contains the search term
    matches = [
        train for train in train_list
        if term in train['train_name'].lower() or term in train['train_number']
    ]

    # Return the first 10 matches
    return jsonify(matches[:10])


def get_platform(station_data):
    """Safely retrieves the platform number."""
    if not station_data:
        return "N/A"
    platform = station_data.get('platform_number') or station_data.get('platform_no') or station_data.get(
        'platform') or station_data.get('pf')
    if platform in [0, None, ""]:
        return "N/A"
    return f"PF #{str(platform)}"


def get_train_status_from_railyatri(train_number, day_offset):
    """
    Fetches train status for a specific day offset (0=today, 1=yesterday, etc.).
    """
    if not train_number.isdigit() or len(train_number) != 5:
        return None

    url = f"https://www.railyatri.in/live-train-status/{train_number}?start_day={day_offset}"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }

    try:
        print(f"Fetching data for train {train_number} with start_day={day_offset} from {url}...")
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')
        script_tag = soup.find('script', {'id': '__NEXT_DATA__'})

        if not script_tag:
            return None

        data = json.loads(script_tag.string)
        lts_data = data.get('props', {}).get('pageProps', {}).get('ltsData', {})

        if not lts_data:
            return None

        current_delay_minutes = lts_data.get('delay')
        current_delay_display = "On Time" if current_delay_minutes == 0 else f"{current_delay_minutes} minutes" if isinstance(
            current_delay_minutes, int) else "N/A"

        current_status = {
            "fromStation": f"{lts_data.get('source_stn_name', 'N/A')} ({lts_data.get('source', 'N/A')})",
            "toStation": f"{lts_data.get('dest_stn_name', 'N/A')} ({lts_data.get('destination', 'N/A')})",
            "lastUpdate": f"{lts_data.get('status_as_of', 'N/A')} ({lts_data.get('update_time', 'N/A')})",
            "statusMessage": lts_data.get('status', 'N/A'),
            "currentDelay": current_delay_display,
            "lastCrossedStation": lts_data.get('current_station_name', 'N/A'),
            "lastCrossedTime": lts_data.get('current_station_eta', 'N/A'),
            "speed": f"{lts_data.get('avg_speed', 'N/A')} km/h"
        }

        # --- FIXED Next Stop section ---
        upcoming_stations = lts_data.get('upcoming_stations', [])
        # Find the first valid station, skipping empty placeholders
        next_stop_data = next((station for station in upcoming_stations if station and station.get('station_code')),
                              None)

        if next_stop_data:
            delay = next_stop_data.get('arrival_delay')
            delay_display = "On Time" if delay == 0 else (f"{delay} minutes" if delay is not None else "N/A")

            next_stoppage = {
                "stationName": next_stop_data.get('station_name', 'N/A'),
                "distanceToGo": next_stop_data.get('distance_from_current_station_txt', 'N/A'),
                "expectedArrival": next_stop_data.get('eta', 'N/A'),
                "expectedDelay": delay_display,
                "expectedPlatform": get_platform(next_stop_data)
            }
        else:
            next_stoppage = {
                "stationName": "N/A", "distanceToGo": "N/A", "expectedArrival": "N/A",
                "expectedDelay": "N/A", "expectedPlatform": "N/A"
            }

        # --- Process the full route ---
        raw_full_route = (lts_data.get('previous_stations', []) or []) + (upcoming_stations or [])
        processed_full_route = []
        for s in raw_full_route:
            if not s or not s.get('station_code'):
                continue

            delay = s.get('arrival_delay')
            delay_text = "On Time" if delay == 0 else (f"{delay} min" if delay is not None else "N/A")

            station_dict = {
                "station": s.get('station_name', 'N/A'),
                "scheduled_eta": s.get('sta', 'N/A'),
                "expected_eta": s.get('eta', 'N/A'),
                "etd": s.get('std', 'N/A'),
                "delayMin": delay,
                "delayText": delay_text,
                "platform": get_platform(s),
                "distance": f"{s.get('distance_from_source', 'N/A')} Kms"
            }
            processed_full_route.append(station_dict)

        # --- ENHANCEMENT: Calculate Journey Progress on Backend ---
        progress = 0
        total_stations = len(processed_full_route)
        if total_stations > 1:
            if lts_data.get('at_dstn') or "arrived" in lts_data.get('status', '').lower():
                progress = 100
            else:
                next_stop_name_for_progress = next_stoppage.get("stationName", "").strip()
                if next_stop_name_for_progress and next_stop_name_for_progress != 'N/A':
                    # Find the index of the next stop in our processed list
                    next_stop_index = next((i for i, stop in enumerate(processed_full_route) if
                                            stop['station'].strip() == next_stop_name_for_progress), -1)
                    if next_stop_index != -1:
                        progress = (next_stop_index / (total_stations - 1)) * 100

        return {
            "trainNumber": lts_data.get('train_number'), "trainName": lts_data.get('train_name'),
            "currentStatus": current_status, "nextStop": next_stoppage,
            "daysOfRun": lts_data.get('run_days', 'N/A').split(',') if lts_data.get('run_days') else ['N/A'],
            "trainType": lts_data.get('train_type', 'N/A'),
            "pantryCar": "Available" if lts_data.get('pantry_available') else "Not Available",
            "fullRoute": processed_full_route,
            "journeyProgress": progress  # Pass progress to the template
        }

    except Exception as e:
        print(f"An error occurred: {e}")
        return None


@app.route('/', methods=["GET", "POST"])
def index():
    train_number_input = ""
    train_data = None
    error_message = ""
    selected_day = "0"

    if request.method == "POST":
        train_number_input = request.form.get("train_number", "").strip()
        selected_day = request.form.get("start_day", "0")

        if not train_number_input or len(train_number_input) != 5 or not train_number_input.isdigit():
            error_message = "Please enter a valid 5-digit train number."
        else:
            scraped_data = get_train_status_from_railyatri(train_number_input, selected_day)

            if scraped_data:
                train_data = scraped_data
            else:
                day_map = {'0': 'Today', '1': 'Yesterday', '2': 'the Day Before'}
                error_message = f"Could not retrieve live status for train {train_number_input} that started on '{day_map.get(selected_day, 'the selected day')}'."

    return render_template("index.html",
                           train_number_input=train_number_input,
                           train_data=train_data,
                           error_message=error_message,
                           selected_day=selected_day)
# ✅ Route to serve files from public/ (like bg.png)
from flask import send_from_directory

@app.route('/public/<path:filename>')
def serve_public(filename):
    return send_from_directory('public', filename)
    

if __name__ == "__main__":
    app.run(debug=True)
