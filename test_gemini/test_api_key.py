"""
Test Gemini API Key
Run: python test_gemini/test_api_key.py
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

API_KEY = os.getenv('GEMINI_API_KEY', '')

if not API_KEY:
    print("❌ GEMINI_API_KEY not found in .env")
    exit(1)

print(f"API Key: {API_KEY[:10]}...{API_KEY[-5:]}")
print()

# Test 1: List available models
print("=" * 50)
print("TEST 1: List Available Models")
print("=" * 50)
try:
    import google.generativeai as genai
    genai.configure(api_key=API_KEY)

    models = []
    for m in genai.list_models():
        if 'generateContent' in str(m.supported_generation_methods):
            models.append(m.name)

    print(f"✅ {len(models)} models available:")
    for m in models[:10]:
        print(f"   {m}")
except Exception as e:
    print(f"❌ Error: {e}")

print()

# Test 2: Simple text generation
print("=" * 50)
print("TEST 2: Simple Text Generation")
print("=" * 50)
try:
    model = genai.GenerativeModel('gemini-2.5-flash')
    response = model.generate_content("Say 'Hello World' and nothing else.")
    print(f"✅ Response: {response.text.strip()}")
except Exception as e:
    print(f"❌ Error: {e}")

print()

# Test 3: Image understanding (cheque-like test)
print("=" * 50)
print("TEST 3: Image Understanding")
print("=" * 50)
try:
    import base64
    from PIL import Image
    import io

    # Create a simple test image with text
    img = Image.new('RGB', (400, 100), 'white')
    from PIL import ImageDraw
    draw = ImageDraw.Draw(img)
    draw.text((10, 30), "A/C No. 41332899174", fill='black')
    draw.text((10, 60), "IFSC: SBIN0011029", fill='black')

    # Convert to bytes
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    img_bytes = buf.getvalue()
    b64 = base64.b64encode(img_bytes).decode('utf-8')

    model = genai.GenerativeModel('gemini-2.5-flash')
    response = model.generate_content([
        "Extract ONLY the bank account number from this image. Return ONLY digits.",
        {"mime_type": "image/png", "data": b64}
    ])
    result = response.text.strip()
    print(f"✅ Extracted: {result}")

    if '41332899174' in result:
        print("✅ CORRECT account number extracted!")
    else:
        print(f"⚠️ Expected 41332899174, got {result}")
except Exception as e:
    print(f"❌ Error: {e}")

print()

# Test 4: Quota check
print("=" * 50)
print("TEST 4: Quota Status")
print("=" * 50)
try:
    model = genai.GenerativeModel('gemini-2.5-flash')
    response = model.generate_content("Reply with just 'OK'")
    print(f"✅ Quota OK — API responding: {response.text.strip()}")
except Exception as e:
    if '429' in str(e):
        print(f"❌ QUOTA EXCEEDED — Rate limit hit. Try again later.")
    else:
        print(f"❌ Error: {e}")

print()
print("=" * 50)
print("DONE")
print("=" * 50)
