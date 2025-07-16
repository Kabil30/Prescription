from groq import Groq
import os
from dotenv import load_dotenv
import re

load_dotenv()

# Initialize Groq client
client = Groq(
    api_key=os.getenv('GROQ_API_KEY'),
)

def extract_with_groq(prescription_text):
    """
    Extract prescription information using GROQ API
    """
    try:
        prompt = f"""
        Extract the following information from this medical prescription text: "{prescription_text}"
        
        Return ONLY in this exact format:
        Medicine Name: [medicine name or not mentioned]
        Duration: [number only, e.g., 3]
        Duration Unit: [days/weeks/months or not mentioned]
        Morning: [yes/no]
        Afternoon: [yes/no]
        Night: [yes/no]
        Times Per Day: [number or not mentioned]
        
        Rules:
        - For Medicine Name: Extract the actual medicine/drug name (e.g., paracetamol, aspirin, etc.)
        - For Duration: Extract only the number (e.g., if "3 days" then return "3")
        - For Duration Unit: Extract only the unit (days, weeks, months)
        - For Morning/Afternoon/Night: Set to "yes" only if explicitly mentioned
        - For Times Per Day: Extract frequency number if mentioned (e.g., "2 times a day" = 2)
        - If timing is mentioned as "twice a day" or "2 times" without specific timing, set Times Per Day accordingly
        - Common timing keywords: morning, afternoon, evening, night, breakfast, lunch, dinner, bedtime
        - Be very specific about timing - only say "yes" if clearly mentioned
        
        Examples:
        - "take paracetamol 2 times a day for 3 days" → Morning: no, Afternoon: no, Night: no, Times Per Day: 2
        - "take aspirin in the morning and night for 1 week" → Morning: yes, Afternoon: no, Night: yes, Times Per Day: 2
        - "paracetamol for 5 days morning and evening" → Morning: yes, Afternoon: no, Night: yes, Times Per Day: 2
        """
        
        chat_completion = client.chat.completions.create(
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            model="llama3-8b-8192",
            temperature=0.1,
            max_tokens=200
        )
        
        response = chat_completion.choices[0].message.content
        
        # Clean up the response
        response = response.strip()
        
        # Validate response format
        if not response or len(response.split('\n')) < 4:
            return None
            
        return response
        
    except Exception as e:
        print(f"GROQ API error: {str(e)}")
        return None

def parse_prescription_smart(prescription_text):
    """
    Smart parsing function that combines GROQ with fallback logic
    """
    try:
        # First, try GROQ extraction
        groq_result = extract_with_groq(prescription_text)
        
        if groq_result:
            parsed_data = {}
            for line in groq_result.split('\n'):
                if ':' in line:
                    key, value = line.split(':', 1)
                    parsed_data[key.strip()] = value.strip()
            
            return parsed_data
        
        # Fallback to manual parsing
        return manual_prescription_parse(prescription_text)
        
    except Exception as e:
        print(f"Smart parsing error: {str(e)}")
        return manual_prescription_parse(prescription_text)

def manual_prescription_parse(prescription_text):
    """
    Manual parsing as fallback when GROQ fails
    """
    prescription_text = prescription_text.lower()
    
    result = {
        "Medicine Name": "not mentioned",
        "Duration": "not mentioned",
        "Duration Unit": "not mentioned",
        "Morning": "no",
        "Afternoon": "no",
        "Night": "no",
        "Times Per Day": "not mentioned"
    }
    
    # Extract medicine name
    medicine_patterns = [
        r'take\s+(?:the\s+)?([a-zA-Z]+)',
        r'([a-zA-Z]+)\s+for',
        r'([a-zA-Z]+)\s+\d+\s*times',
        r'([a-zA-Z]+)\s+tablet'
    ]
    
    for pattern in medicine_patterns:
        match = re.search(pattern, prescription_text)
        if match:
            medicine = match.group(1).strip()
            if medicine not in ['the', 'for', 'take', 'tablet', 'times', 'day']:
                result["Medicine Name"] = medicine.title()
                break
    
    # Extract duration
    duration_patterns = [
        r'for\s+(\d+)\s+(day|days|week|weeks|month|months)',
        r'(\d+)\s+(day|days|week|weeks|month|months)',
        r'for\s+(\d+)\s*d\b',
        r'(\d+)\s*d\b'
    ]
    
    for pattern in duration_patterns:
        match = re.search(pattern, prescription_text)
        if match:
            result["Duration"] = match.group(1)
            if len(match.groups()) > 1:
                unit = match.group(2)
                if unit in ['day', 'days', 'd']:
                    result["Duration Unit"] = 'days'
                elif unit in ['week', 'weeks', 'w']:
                    result["Duration Unit"] = 'weeks'
                elif unit in ['month', 'months', 'm']:
                    result["Duration Unit"] = 'months'
            break
    
    # Extract timing
    if any(word in prescription_text for word in ['morning', 'morn', 'breakfast', 'am']):
        result["Morning"] = "yes"
    if any(word in prescription_text for word in ['afternoon', 'lunch', 'noon']):
        result["Afternoon"] = "yes"
    if any(word in prescription_text for word in ['night', 'evening', 'dinner', 'bedtime', 'sleep']):
        result["Night"] = "yes"
    
    # Extract frequency
    frequency_patterns = [
        r'(\d+)\s*times?\s*a?\s*day',
        r'(\d+)\s*times?\s*daily',
        r'(\d+)x\s*day',
        r'(\d+)/day'
    ]
    
    for pattern in frequency_patterns:
        match = re.search(pattern, prescription_text)
        if match:
            result["Times Per Day"] = match.group(1)
            break
    
    # Smart frequency detection
    timing_count = sum(1 for key in ['Morning', 'Afternoon', 'Night'] if result[key] == 'yes')
    if timing_count > 0 and result["Times Per Day"] == "not mentioned":
        result["Times Per Day"] = str(timing_count)
    elif result["Times Per Day"] != "not mentioned" and timing_count == 0:
        # If frequency is specified but no timing, we need to ask for timing
        pass
    
    return result

def test_prescription_parsing():
    """
    Test function to verify prescription parsing
    """
    test_cases = [
        "take paracetamol 2 times a day for 3 days",
        "aspirin in the morning and night for 1 week",
        "paracetamol for 5 days morning and evening",
        "take amoxicillin 500mg three times daily for 7 days",
        "ibuprofen 400mg twice a day after meals for 5 days",
        "take vitamin D once daily in the morning for 1 month",
        "paracetamol 650mg every 6 hours for fever for 3 days",
        "take omeprazole 20mg once daily before breakfast for 2 weeks"
    ]
    
    print("Testing prescription parsing:")
    print("=" * 50)
    
    for i, test_case in enumerate(test_cases, 1):
        print(f"\nTest Case {i}: {test_case}")
        print("-" * 40)
        
        # Test GROQ extraction
        groq_result = extract_with_groq(test_case)
        if groq_result:
            print("GROQ Result:")
            for line in groq_result.split('\n'):
                if line.strip():
                    print(f"  {line}")
        else:
            print("GROQ Result: Failed")
        
        # Test smart parsing
        smart_result = parse_prescription_smart(test_case)
        print("\nSmart Parse Result:")
        for key, value in smart_result.items():
            print(f"  {key}: {value}")
        
        print("-" * 40)

# Additional utility functions for enhanced parsing
def normalize_medicine_name(medicine_name):
    """
    Normalize medicine names for consistency
    """
    # Common medicine name corrections
    corrections = {
        'paracetamol': 'Paracetamol',
        'aspirin': 'Aspirin',
        'ibuprofen': 'Ibuprofen',
        'amoxicillin': 'Amoxicillin',
        'omeprazole': 'Omeprazole',
        'metformin': 'Metformin',
        'atorvastatin': 'Atorvastatin',
        'lisinopril': 'Lisinopril',
        'amlodipine': 'Amlodipine',
        'simvastatin': 'Simvastatin'
    }
    
    medicine_lower = medicine_name.lower()
    return corrections.get(medicine_lower, medicine_name.title())

def validate_prescription_data(prescription_data):
    """
    Validate extracted prescription data for completeness
    """
    required_fields = ['Medicine Name', 'Duration', 'Duration Unit']
    missing_fields = []
    
    for field in required_fields:
        if field not in prescription_data or prescription_data[field] in ['not mentioned', '', None]:
            missing_fields.append(field)
    
    # Check if timing information is sufficient
    timing_fields = ['Morning', 'Afternoon', 'Night']
    has_timing = any(prescription_data.get(field) == 'yes' for field in timing_fields)
    
    if not has_timing and prescription_data.get('Times Per Day') in ['not mentioned', '', None]:
        missing_fields.append('Timing or Frequency')
    
    return len(missing_fields) == 0, missing_fields

def format_prescription_summary(prescription_data):
    """
    Format prescription data into a readable summary
    """
    medicine = prescription_data.get('Medicine Name', 'Unknown')
    duration = prescription_data.get('Duration', 'Unknown')
    duration_unit = prescription_data.get('Duration Unit', 'Unknown')
    
    # Format timing
    timings = []
    if prescription_data.get('Morning') == 'yes':
        timings.append('Morning')
    if prescription_data.get('Afternoon') == 'yes':
        timings.append('Afternoon')
    if prescription_data.get('Night') == 'yes':
        timings.append('Night')
    
    timing_str = ', '.join(timings) if timings else f"{prescription_data.get('Times Per Day', 'Unknown')} times per day"
    
    summary = f"""
    Medicine: {medicine}
    Duration: {duration} {duration_unit}
    Timing: {timing_str}
    """
    
    return summary.strip()

# Run tests if this file is executed directly
if __name__ == "__main__":
    test_prescription_parsing()