
from flask import Flask, render_template, request, jsonify, session
from flask_cors import CORS
from datetime import datetime, timedelta
from dotenv import load_dotenv
import os
from groq_api import extract_with_groq
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import re

load_dotenv()

scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name("creds.json", scope)
client = gspread.authorize(creds)

SHEET_ID = "1wirb9ZLhLYZW45-1HtRxl3yiL0382zYUh9pv7lE8X1g"

sheet = client.open_by_key(SHEET_ID).sheet1

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "your_secret_key_change_this_in_production")
CORS(app)

def parse_duration(duration_str):
    """Parse duration string and return normalized format"""
    duration_str = duration_str.strip().lower()
    
    # Extract number and unit
    duration_match = re.search(r'(\d+)\s*(day|days|week|weeks|month|months|d|w|m)', duration_str)
    if duration_match:
        number = int(duration_match.group(1))
        unit = duration_match.group(2)
        
        # Normalize unit
        if unit in ['day', 'days', 'd']:
            return number, 'days'
        elif unit in ['week', 'weeks', 'w']:
            return number, 'weeks'
        elif unit in ['month', 'months', 'm']:
            return number, 'months'
    
    return None, None

def parse_food_timing(prescription_text):
    """FIXED: Parse food timing from prescription text - handles 'before' correctly"""
    text = prescription_text.lower()
    
    # Check for before food indicators - FIXED ORDER
    before_food_patterns = [
        r'before\s+food',
        r'before\s+meal',
        r'before\s+eating',
        r'empty\s+stomach',
        r'on\s+empty\s+stomach'
    ]
    
    # Check for after food indicators
    after_food_patterns = [
        r'after\s+food',
        r'after\s+meal',
        r'after\s+eating',
        r'with\s+food',
        r'with\s+meal'
    ]
    
    # FIXED: Check before food patterns FIRST
    for pattern in before_food_patterns:
        if re.search(pattern, text):
            return "before food"
    
    for pattern in after_food_patterns:
        if re.search(pattern, text):
            return "after food"
    
    return None

def parse_frequency(freq_str):
    """Parse frequency and timing from prescription text"""
    freq_str = freq_str.strip().lower()

    timings = {
        'morning': False,
        'afternoon': False,
        'night': False
    }

    # Look for keywords that suggest timing
    if any(word in freq_str for word in ['morning', 'morn', 'am', 'breakfast']):
        timings['morning'] = True
    if any(word in freq_str for word in ['afternoon', 'lunch', 'noon', 'pm']):
        timings['afternoon'] = True
    if any(word in freq_str for word in ['night', 'evening', 'dinner', 'bedtime', 'sleep']):
        timings['night'] = True

    # Improved regex to catch more patterns
    frequency_patterns = [
        r'for\s+(\d+)\s*times?\s*(?:a\s*)?day',
        r'(\d+)\s*times?\s*(?:a\s*)?day',
        r'(\d+)\s*times?\s*daily',
        r'(\d+)[x×]\s*day',
        r'(\d+)\s*/\s*day'
    ]

    times_per_day = 1  # Default fallback
    for pattern in frequency_patterns:
        match = re.search(pattern, freq_str)
        if match:
            times_per_day = int(match.group(1))
            break

    # If timing not explicitly mentioned, infer from times_per_day
    if not any(timings.values()) and times_per_day > 0:
        if times_per_day == 1:
            timings['morning'] = True
        elif times_per_day == 2:
            timings['morning'] = True
            timings['night'] = True
        elif times_per_day == 3:
            timings['morning'] = True
            timings['afternoon'] = True
            timings['night'] = True

    return timings, times_per_day, "complete" if any(timings.values()) else "timing_needed"

def calculate_total_tablets(duration_num, duration_unit, timings, times_per_day):
    """Calculate total tablet count based on accurate times per day"""
    try:
        # Convert duration to days
        if duration_unit == 'days':
            total_days = duration_num
        elif duration_unit == 'weeks':
            total_days = duration_num * 7
        elif duration_unit == 'months':
            total_days = duration_num * 30  # Approximate
        else:
            total_days = duration_num  # fallback

        # Use times_per_day directly as the actual frequency
        total_tablets = total_days * times_per_day
        return total_tablets

    except Exception as e:
        print(f"Error calculating total tablets: {str(e)}")
        return 0

def extract_medicine_name(prescription_text):
    """Extract medicine name from prescription text - IMPROVED VERSION"""
    text = prescription_text.lower()
    
    # Remove common prefixes and clean text
    text = re.sub(r'^(take\s+(the\s+)?|have\s+)', '', text)
    
    # Look for medicine name patterns
    medicine_patterns = [
        r'^([a-zA-Z][a-zA-Z\s]*?)\s+(?:\d+|tablet|twice|once|morning|afternoon|night|before|after|for)',
        r'^([a-zA-Z][a-zA-Z\s]*?)\s+(?:\d+\s*times)',
        r'^([a-zA-Z][a-zA-Z\s]*?)\s+(?:for\s+\d+)',
        r'^([a-zA-Z][a-zA-Z\s]*?)(?:\s+\d+|\s+tablet|\s+twice|\s+once)'
    ]
    
    for pattern in medicine_patterns:
        match = re.search(pattern, text)
        if match:
            medicine_name = match.group(1).strip()
            # Filter out common words that aren't medicine names
            if medicine_name not in ['the', 'for', 'take', 'tablet', 'tablets', 'times', 'day', 'days', 'in', 'and', 'or', 'have']:
                return medicine_name.title()
    
    return "Not specified"

@app.route('/')
def index():
    return render_template('prescription_index.html')

@app.route('/start_chat', methods=['POST'])
def start_chat():
    session.clear()
    patient_name = request.json.get('name')
    session['patient_name'] = patient_name
    
    welcome_msg = f"Hello Doctor! Please enter the patient prescription details (e.g., 'take paracetamol 2 times a day for 3 days before food')."
    
    return jsonify({"message": welcome_msg})

@app.route('/message', methods=['POST'])
def message():
    user_msg = request.json.get('message')
    patient_name = request.json.get('name')
    today = datetime.now().strftime("%Y-%m-%d")
    
    # Check if user is confirming or editing
    if user_msg.lower().strip() in ['yes', 'y', 'correct', 'ok', 'confirm']:
        if 'pending_prescription' in session:
            return save_prescription(session['pending_prescription'], patient_name, today)
        else:
            return jsonify({"message": "No pending prescription to save. Please start over."})
    
    if user_msg.lower().strip() in ['no', 'n', 'incorrect', 'edit']:
        return jsonify({
            "message": "What would you like to change? Please specify the field and new value (e.g., 'Duration: 5 days' or 'Timing: morning and night')",
            "show_quick_buttons": True,
            "quick_buttons": [
                {"text": "Change Medicine", "value": "medicine"},
                {"text": "Change Duration", "value": "duration"},
                {"text": "Change Timing", "value": "timing"},
                {"text": "Change Food Timing", "value": "food_timing"}
            ]
        })
    
    # Handle quick button responses
    if user_msg in ['medicine', 'duration', 'timing', 'food_timing']:
        return handle_quick_button_response(user_msg, patient_name, today)
    
    # Handle field edits
    if 'pending_prescription' in session:
        if ':' in user_msg:
            return handle_field_edit(user_msg, patient_name, today)
        elif 'morning' in user_msg.lower() or 'afternoon' in user_msg.lower() or 'night' in user_msg.lower():
            return handle_timing_update(user_msg, patient_name, today)
        elif 'before food' in user_msg.lower() or 'after food' in user_msg.lower():
            return handle_food_timing_update(user_msg, patient_name, today)
    
    # Process initial prescription message
    try:
        groq_response = extract_with_groq(user_msg)
        if groq_response is None:
            groq_response = ""
    except Exception as e:
        print(f"GROQ API error: {str(e)}")
        groq_response = ""
    
    # Initialize prescription fields
    prescription = {
        "Medicine Name": extract_medicine_name(user_msg),
        "Duration": "-",
        "Duration Unit": "-",
        "Morning": "no",
        "Afternoon": "no", 
        "Night": "no",
        "Times Per Day": 1,
        "Food Timing": "-",
        "Total Tablets": 0,
        "Raw Prescription": user_msg
    }
    
    # Parse duration
    duration_num, duration_unit = parse_duration(user_msg)
    if duration_num and duration_unit:
        prescription["Duration"] = str(duration_num)
        prescription["Duration Unit"] = duration_unit
    
    # Parse frequency and timing - FIXED
    timings, times_per_day, timing_status = parse_frequency(user_msg)
    prescription["Morning"] = "yes" if timings['morning'] else "no"
    prescription["Afternoon"] = "yes" if timings['afternoon'] else "no"
    prescription["Night"] = "yes" if timings['night'] else "no"
    prescription["Times Per Day"] = times_per_day
    
    # Parse food timing - FIXED
    food_timing = parse_food_timing(user_msg)
    if food_timing:
        prescription["Food Timing"] = food_timing
    
    # Process GROQ response if available
    if groq_response and groq_response.strip():
        for line in groq_response.strip().split("\n"):
            if ":" not in line:
                continue
            try:
                key, value = line.split(":", 1)
                key = key.strip().lower()
                value = value.strip()
                
                if key == "medicine name":
                    prescription["Medicine Name"] = value
                elif key == "duration":
                    prescription["Duration"] = value
                elif key == "duration unit":
                    prescription["Duration Unit"] = value
                elif key == "morning":
                    prescription["Morning"] = value.lower()
                elif key == "afternoon":
                    prescription["Afternoon"] = value.lower()
                elif key == "night":
                    prescription["Night"] = value.lower()
                elif key == "times per day":
                    try:
                        prescription["Times Per Day"] = int(value)
                    except:
                        pass
                elif key == "food timing":
                    prescription["Food Timing"] = value
            except ValueError:
                continue
    
    # Check for missing required information
    missing = []
    if prescription["Medicine Name"] == "Not specified":
        missing.append("Medicine Name")
    if prescription["Duration"] == "-":
        missing.append("Duration (e.g., '3 days', '2 weeks')")
    
    # Check if timing is needed
    if timing_status == "timing_needed":
        missing.append("Timing (morning/afternoon/night)")
    
    # Check if food timing is needed
    if prescription["Food Timing"] == "-":
        missing.append("Food Timing (before food/after food)")
    
    if missing:
        buttons = []
        if "Medicine Name" in missing:
            buttons.extend([
                {"text": "Paracetamol", "value": "Medicine: Paracetamol"},
                {"text": "Dolo", "value": "Medicine: Dolo"},
                {"text": "Aspirin", "value": "Medicine: Aspirin"}
            ])
        if "Timing (morning/afternoon/night)" in missing:
            buttons.extend([
                {"text": "Morning", "value": "morning"},
                {"text": "Afternoon", "value": "afternoon"},
                {"text": "Night", "value": "night"},
                {"text": "Morning & Night", "value": "morning and night"},
                {"text": "All three times", "value": "morning, afternoon and night"}
            ])
        if "Food Timing (before food/after food)" in missing:
            buttons.extend([
                {"text": "Before Food", "value": "before food"},
                {"text": "After Food", "value": "after food"}
            ])
        
        prompt = f"Please provide the following missing information: {', '.join(missing)}"
        return jsonify({
            "message": prompt,
            "show_quick_buttons": True,
            "quick_buttons": buttons
        })
    
    # Calculate total tablets - FIXED
    if prescription["Duration"] != "-" and prescription["Duration Unit"] != "-":
        try:
            duration_num = int(prescription["Duration"])
            timings_dict = {
                'morning': prescription["Morning"] == "yes",
                'afternoon': prescription["Afternoon"] == "yes",
                'night': prescription["Night"] == "yes"
            }
            prescription["Total Tablets"] = calculate_total_tablets(
                duration_num, prescription["Duration Unit"], timings_dict, prescription["Times Per Day"]
            )
        except:
            prescription["Total Tablets"] = 0
    
    # Store prescription for confirmation
    session['pending_prescription'] = prescription
    
    # Show confirmation
    return show_prescription_confirmation(prescription, patient_name, today)

def handle_quick_button_response(button_type, patient_name, today):
    """Handle responses from quick buttons"""
    if button_type == "medicine":
        return jsonify({
            "message": "Please enter the medicine name:",
            "show_quick_buttons": True,
            "quick_buttons": [
                {"text": "Paracetamol", "value": "Medicine: Paracetamol"},
                {"text": "Dolo", "value": "Medicine: Dolo"},
                {"text": "Aspirin", "value": "Medicine: Aspirin"},
                {"text": "Crocin", "value": "Medicine: Crocin"}
            ]
        })
    elif button_type == "duration":
        return jsonify({
            "message": "Please select duration:",
            "show_quick_buttons": True,
            "quick_buttons": [
                {"text": "3 days", "value": "Duration: 3 days"},
                {"text": "5 days", "value": "Duration: 5 days"},
                {"text": "7 days", "value": "Duration: 7 days"},
                {"text": "2 weeks", "value": "Duration: 2 weeks"}
            ]
        })
    elif button_type == "timing":
        return jsonify({
            "message": "Please select timing:",
            "show_quick_buttons": True,
            "quick_buttons": [
                {"text": "Morning", "value": "morning"},
                {"text": "Afternoon", "value": "afternoon"},
                {"text": "Night", "value": "night"},
                {"text": "Morning & Night", "value": "morning and night"},
                {"text": "All three times", "value": "morning, afternoon and night"}
            ]
        })
    elif button_type == "food_timing":
        return jsonify({
            "message": "Please select food timing:",
            "show_quick_buttons": True,
            "quick_buttons": [
                {"text": "Before Food", "value": "before food"},
                {"text": "After Food", "value": "after food"}
            ]
        })

def handle_food_timing_update(user_msg, patient_name, today):
    """Handle food timing updates"""
    if 'pending_prescription' not in session:
        return jsonify({"message": "No pending prescription to edit. Please start over."})
    
    prescription = session['pending_prescription']
    
    if 'before food' in user_msg.lower():
        prescription["Food Timing"] = "before food"
    elif 'after food' in user_msg.lower():
        prescription["Food Timing"] = "after food"
    
    session['pending_prescription'] = prescription
    return show_prescription_confirmation(prescription, patient_name, today, "Updated food timing")

def handle_timing_update(user_msg, patient_name, today):
    """Handle timing updates specifically"""
    if 'pending_prescription' not in session:
        return jsonify({"message": "No pending prescription to edit. Please start over."})
    
    prescription = session['pending_prescription']
    
    # Reset timings
    prescription["Morning"] = "no"
    prescription["Afternoon"] = "no"
    prescription["Night"] = "no"
    
    # Parse new timing
    user_msg_lower = user_msg.lower()
    if any(word in user_msg_lower for word in ['morning', 'morn', 'am', 'breakfast']):
        prescription["Morning"] = "yes"
    if any(word in user_msg_lower for word in ['afternoon', 'lunch', 'noon']):
        prescription["Afternoon"] = "yes"
    if any(word in user_msg_lower for word in ['night', 'evening', 'dinner', 'bedtime']):
        prescription["Night"] = "yes"
    
    # Update times per day
    timing_count = sum(1 for timing in [prescription["Morning"], prescription["Afternoon"], prescription["Night"]] if timing == "yes")
    prescription["Times Per Day"] = timing_count or 1
    
    # Recalculate total tablets
    if prescription["Duration"] != "-":
        try:
            duration_num = int(prescription["Duration"])
            timings_dict = {
                'morning': prescription["Morning"] == "yes",
                'afternoon': prescription["Afternoon"] == "yes",
                'night': prescription["Night"] == "yes"
            }
            prescription["Total Tablets"] = calculate_total_tablets(
                duration_num, prescription["Duration Unit"], timings_dict, prescription["Times Per Day"]
            )
        except:
            prescription["Total Tablets"] = 0
    
    session['pending_prescription'] = prescription
    
    return show_prescription_confirmation(prescription, patient_name, today, "Updated timing")

def handle_field_edit(user_msg, patient_name, today):
    """Handle editing of specific prescription fields"""
    if 'pending_prescription' not in session:
        return jsonify({"message": "No pending prescription to edit. Please start over."})
    
    prescription = session['pending_prescription']
    updated_fields = []
    
    field_updates = [update.strip() for update in user_msg.split(',')]
    
    for update in field_updates:
        if ':' in update:
            try:
                field_name, new_value = update.split(':', 1)
                field_name = field_name.strip().lower()
                new_value = new_value.strip()
                
                field_mapping = {
                    'medicine name': 'Medicine Name',
                    'medicine': 'Medicine Name',
                    'duration': 'Duration',
                    'timing': 'timing_special',  # Special handling
                    'times per day': 'Times Per Day',
                    'frequency': 'Times Per Day',
                    'food timing': 'Food Timing'
                }
                
                if field_name in field_mapping:
                    actual_field = field_mapping[field_name]
                    
                    if actual_field == 'timing_special':
                        # Handle timing specially
                        prescription["Morning"] = "yes" if "morning" in new_value.lower() else "no"
                        prescription["Afternoon"] = "yes" if "afternoon" in new_value.lower() else "no"
                        prescription["Night"] = "yes" if "night" in new_value.lower() else "no"
                        timing_count = sum(1 for timing in [prescription["Morning"], prescription["Afternoon"], prescription["Night"]] if timing == "yes")
                        prescription["Times Per Day"] = timing_count or 1
                        updated_fields.append("Timing")
                    elif actual_field == 'Duration':
                        # Parse duration
                        duration_num, duration_unit = parse_duration(new_value)
                        if duration_num and duration_unit:
                            prescription["Duration"] = str(duration_num)
                            prescription["Duration Unit"] = duration_unit
                            updated_fields.append("Duration")
                    else:
                        prescription[actual_field] = new_value
                        updated_fields.append(actual_field)
            except ValueError:
                continue
    
    # Recalculate total tablets
    if prescription["Duration"] != "-":
        try:
            duration_num = int(prescription["Duration"])
            timings_dict = {
                'morning': prescription["Morning"] == "yes",
                'afternoon': prescription["Afternoon"] == "yes",
                'night': prescription["Night"] == "yes"
            }
            prescription["Total Tablets"] = calculate_total_tablets(
                duration_num, prescription["Duration Unit"], timings_dict, prescription["Times Per Day"]
            )
        except:
            prescription["Total Tablets"] = 0
    
    session['pending_prescription'] = prescription
    
    if updated_fields:
        return show_prescription_confirmation(prescription, patient_name, today, f"Updated: {', '.join(updated_fields)}")
    else:
        return jsonify({"message": "I couldn't understand what you want to change. Please use format 'Field: New Value'"})

def format_duration(num, unit):
    """Formats duration with correct singular/plural"""
    if str(num) == "1" and unit.endswith('s'):
        unit = unit[:-1]
    return f"{num} {unit}"

def show_prescription_confirmation(prescription, patient_name, today, extra_msg=""):
    """Show prescription confirmation message"""
    timing_str = []
    if prescription["Morning"] == "yes":
        timing_str.append("Morning")
    if prescription["Afternoon"] == "yes":
        timing_str.append("Afternoon")
    if prescription["Night"] == "yes":
        timing_str.append("Night")
    
    formatted = f"""
        <b>Please review your prescription:</b><br>
        Patient: {patient_name}<br>
        Date: {today}<br>
        Medicine: {prescription['Medicine Name']}<br>
        Duration: {format_duration(prescription['Duration'], prescription['Duration Unit'])}<br>
        Timing: {', '.join(timing_str) if timing_str else 'Not specified'}<br>
        Food Timing: {prescription['Food Timing']}<br>
        Times per day: {prescription['Times Per Day']}<br>
        Total tablets needed: {prescription['Total Tablets']}<br>
        {f"<br><i>{extra_msg}</i>" if extra_msg else ""}
        <br><b>Is everything correct?</b><br>
    """
    
    return jsonify({
        "message": formatted,
        "show_quick_buttons": True,
        "quick_buttons": [
            {"text": "Yes, Save", "value": "yes"},
            {"text": "No, Edit", "value": "no"}
        ]
    })

def save_prescription(prescription, patient_name, today):
    """FIXED: Save confirmed prescription to Google Sheets with better error handling"""
    try:
        # Test sheet connection first
        print("Testing Google Sheets connection...")
        test_data = sheet.get_all_values()
        print(f"Successfully connected to sheet. Current rows: {len(test_data)}")
        
        # Ensure sheet headers exist
        try:
            headers = sheet.row_values(1)
            if not headers or len(headers) < 5:
                # Create headers if sheet is empty or incomplete
                headers = [
                    "Patient Name", "Date", "Medicine Name", "Duration", 
                    "Duration Unit", "Timing", "Food Timing", "Times Per Day", 
                    "Total Tablets", "Raw Prescription"
                ]
                if not sheet.row_values(1):
                    sheet.append_row(headers)
                else:
                    sheet.update('A1:J1', [headers])
                print("Headers created/updated successfully")
        except Exception as e:
            print(f"Error handling headers: {str(e)}")
            # Force create headers
            headers = [
                "Patient Name", "Date", "Medicine Name", "Duration", 
                "Duration Unit", "Timing", "Food Timing", "Times Per Day", 
                "Total Tablets", "Raw Prescription"
            ]
            sheet.update('A1:J1', [headers])
        
        # Prepare timing string
        timing_str = []
        if prescription["Morning"] == "yes":
            timing_str.append("Morning")
        if prescription["Afternoon"] == "yes":
            timing_str.append("Afternoon")
        if prescription["Night"] == "yes":
            timing_str.append("Night")
        
        # Prepare row data
        row = [
            patient_name or "Unknown",
            today,
            prescription["Medicine Name"] or "Not specified",
            format_duration(prescription["Duration"], prescription["Duration Unit"]),
            prescription["Duration Unit"] or "-", 
            ', '.join(timing_str) if timing_str else '-',
            prescription["Food Timing"] or "-",
            str(prescription["Times Per Day"]) or "1",
            str(prescription["Total Tablets"]) or "0",
            prescription["Raw Prescription"] or ""
        ]
        
        # Add row to sheet
        sheet.append_row(row)
        sheet_status = "✅ Prescription saved to Google Sheets successfully!"
        print(f"Successfully saved prescription: {row}")
        
        # Clear pending data
        session.pop('pending_prescription', None)
        
    except Exception as e:
        sheet_status = f"❌ Failed to save prescription to Google Sheets: {str(e)}"
        print(f"Sheet error details: {str(e)}")
        print(f"Exception type: {type(e)}")
        
        # Additional debugging
        try:
            import traceback
            traceback.print_exc()
        except:
            pass
    
    # Prepare timing string for display
    timing_str = []
    if prescription["Morning"] == "yes":
        timing_str.append("Morning")
    if prescription["Afternoon"] == "yes":
        timing_str.append("Afternoon")
    if prescription["Night"] == "yes":
        timing_str.append("Night")
    
    formatted = f"""
        <b>Final Saved Prescription:</b><br>
        Patient: {patient_name}<br>
        Date: {today}<br>
        Medicine: {prescription['Medicine Name']}<br>
        Duration: {prescription['Duration']} {prescription['Duration Unit']}<br>
        Timing: {', '.join(timing_str) if timing_str else 'Not specified'}<br>
        Food Timing: {prescription['Food Timing']}<br>
        Times per day: {prescription['Times Per Day']}<br>
        Total tablets needed: {prescription['Total Tablets']}<br>
        <br><b>{sheet_status}</b><br>
        <br>Thank you! You can enter another prescription anytime.<br>
    """
    return jsonify({"message": formatted})

# Admin routes for viewing data
@app.route('/admin/prescriptions', methods=['GET'])
def get_prescription_data():
    """Get all prescription records for admin view"""
    try:
        records = sheet.get_all_records()
        records.sort(key=lambda x: x.get('Date', ''), reverse=True)
        
        return jsonify({
            "success": True,
            "records": records
        })
    except Exception as e:
        print(f"Error fetching prescription data: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@app.route('/admin/stats', methods=['GET'])
def get_admin_stats():
    """Get statistics for admin dashboard"""
    try:
        records = sheet.get_all_records()
        today = datetime.now().strftime("%Y-%m-%d")
        
        total_prescriptions = len(records)
        today_prescriptions = len([r for r in records if r.get('Date') == today])
        unique_patients = len(set(r.get('Patient Name', '') for r in records if r.get('Patient Name')))
        
        # Calculate most prescribed medicine
        medicines = {}
        for record in records:
            medicine = record.get('Medicine Name', '')
            if medicine and medicine != '-':
                medicines[medicine] = medicines.get(medicine, 0) + 1
        
        most_prescribed = max(medicines.items(), key=lambda x: x[1])[0] if medicines else "None"
        
        return jsonify({
            "success": True,
            "totalPrescriptions": total_prescriptions,
            "todayPrescriptions": today_prescriptions,
            "uniquePatients": unique_patients,
            "mostPrescribed": most_prescribed
        })
    except Exception as e:
        print(f"Error calculating stats: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@app.route('/admin/dashboard')
def admin_dashboard():
    return render_template('prescription_admin.html')

if __name__ == '__main__':
    from dotenv import load_dotenv
    load_dotenv()
    app.run(host='0.0.0.0', port=5000)
