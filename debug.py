from fastapi import FastAPI, Request
import json
from datetime import datetime

app = FastAPI()

@app.post("/jotform/webhook")
async def debug_webhook(request: Request):
    """
    JotForm se ane wale data ko beautifully display karna
    Yeh dekh sakte hain ke kaunse fields aa rahe hain
    """
    
    # Form data receive karna
    form = await request.form()
    data = dict(form)
    
    # Terminal me timestamp print karna
    print("\n" + "="*100)
    print(f"🕐 WEBHOOK RECEIVED AT: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*100)
    
    # rawRequest parse karna
    try:
        raw = json.loads(data.get("rawRequest", "{}"))
    except:
        print("❌ Could not parse rawRequest")
        return {"status": "error"}
    
    print("\n📋 SUBMISSION DETAILS:")
    print("-"*100)
    print(f"  Submission ID: {data.get('submissionID', 'N/A')}")
    print(f"  Form ID: {data.get('formID', 'N/A')}")
    
    # Saare fields ko categorize karke dikhana
    print("\n📝 ALL FORM FIELDS:")
    print("-"*100)
    
    simple_fields = []
    object_fields = []
    array_fields = []
    
    for key, value in raw.items():
        if isinstance(value, dict):
            object_fields.append((key, value))
        elif isinstance(value, list):
            array_fields.append((key, value))
        else:
            simple_fields.append((key, value))
    
    # Simple text fields
    if simple_fields:
        print("\n  ✏️  SIMPLE TEXT FIELDS:")
        for key, value in simple_fields:
            print(f"      {key}: {value}")
    
    # Object fields (like address, phone, date)
    if object_fields:
        print("\n  📦 OBJECT FIELDS (Address, Phone, Date etc):")
        for key, value in object_fields:
            print(f"\n      {key}:")
            if isinstance(value, dict):
                for sub_key, sub_value in value.items():
                    print(f"        - {sub_key}: {sub_value}")
    
    # Array fields (like products)
    if array_fields:
        print("\n  📊 ARRAY FIELDS (Products, Lists etc):")
        for key, value in array_fields:
            print(f"\n      {key}: ({len(value)} items)")
            for idx, item in enumerate(value):
                print(f"        Item {idx + 1}:")
                if isinstance(item, dict):
                    for sub_key, sub_value in item.items():
                        print(f"          - {sub_key}: {sub_value}")
    
    # Products ki detailed info
    print("\n🛍️  PRODUCTS BREAKDOWN:")
    print("-"*100)
    
    retails = raw.get("q4_retails", {})
    products = retails.get("products", [])
    
    if products:
        for idx, product in enumerate(products):
            print(f"\n  Product #{idx + 1}:")
            print(f"    Name: {product.get('productName', 'N/A')}")
            print(f"    Quantity: {product.get('quantity', 'N/A')}")
            print(f"    Unit Price: ${product.get('unitPrice', 'N/A')}")
            print(f"    Subtotal: ${product.get('subTotal', 'N/A')}")
            
            # Product options (batch code etc)
            if 'productOptions' in product:
                print(f"    Options: {product['productOptions']}")
            
            # Check for batch code
            batch_keys = ['batchCode', 'batch_code', 'batch']
            for key in batch_keys:
                if key in product:
                    print(f"    Batch Code: {product[key]}")
                    break
    else:
        print("  ⚠️  No products found!")
    
    # Total summary
    print("\n💰 ORDER SUMMARY:")
    print("-"*100)
    total_items = len(products)
    total_qty = sum([p.get('quantity', 0) for p in products])
    total_amount = sum([p.get('subTotal', 0) for p in products])
    
    print(f"  Total Products: {total_items}")
    print(f"  Total Quantity: {total_qty}")
    print(f"  Total Amount: ${total_amount:.2f}")
    
    # Helpful tips
    print("\n💡 CONFIG TIPS:")
    print("-"*100)
    print("  To use these fields in config.json:")
    print("  1. Copy the field name (e.g., 'q14_businessName')")
    print("  2. Add it to 'fields' section in config.json")
    print("  3. Set 'show_in_pdf': true/false")
    print("  4. Set display 'order' number")
    
    print("\n" + "="*100 + "\n")
    
    # Full JSON export (for reference)
    print("📄 FULL JSON DATA (for advanced debugging):")
    print("-"*100)
    print(json.dumps(raw, indent=2))
    print("="*100 + "\n")
    
    return {
        "status": "success",
        "message": "Debug data logged to console",
        "fields_found": len(raw.keys()),
        "products_count": len(products)
    }


@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "status": "running",
        "message": "Debug server is active. Send webhook to /jotform/webhook"
    }


#  uvicorn debug:app --host 0.0.0.0 --port 8000 --reload
