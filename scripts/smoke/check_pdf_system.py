#!/usr/bin/env python3
"""
Test script for PDF upload system
Tests the complete PDF processing pipeline
"""

import requests
import json
import time

def test_pdf_upload_system():
    """Test the PDF upload endpoints"""
    
    base_url = "http://localhost:8000"
    
    print("🧪 Testing PDF Upload System")
    print("=" * 50)
    
    # Test 1: Check system status
    print("\n1. Testing system status...")
    try:
        response = requests.get(f"{base_url}/documents/status")
        if response.status_code == 200:
            status = response.json()
            print(f"✅ System operational")
            print(f"   Supported formats: {status['supported_formats']}")
            print(f"   Max file size: {status['max_file_size']}")
            print(f"   Available models: {status['available_models']}")
        else:
            print(f"❌ Status check failed: {response.status_code}")
            return
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        return
    
    # Test 2: Check main chat endpoint
    print("\n2. Testing main chat endpoint...")
    try:
        chat_payload = {
            "model": "gemini-2.5-flash",
            "prompt": "What contract types are available?",
            "history": "[]"
        }
        response = requests.post(f"{base_url}/run/", json=chat_payload)
        if response.status_code == 200:
            print("✅ Chat endpoint working")
        else:
            print(f"❌ Chat endpoint failed: {response.status_code}")
    except Exception as e:
        print(f"❌ Chat test failed: {e}")
    
    # Test 3: Create a sample PDF for testing
    print("\n3. Creating sample PDF for testing...")
    try:
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import letter
        import io
        
        # Create a simple PDF in memory
        buffer = io.BytesIO()
        p = canvas.Canvas(buffer, pagesize=letter)
        
        # Add content that looks like a contract
        p.drawString(100, 750, "MASTER SERVICES AGREEMENT")
        p.drawString(100, 720, "")
        p.drawString(100, 700, "This Master Services Agreement ('Agreement') is entered into")
        p.drawString(100, 680, "between ABC Corporation ('Client') and XYZ Services LLC ('Provider')")
        p.drawString(100, 660, "effective January 1, 2024.")
        p.drawString(100, 640, "")
        p.drawString(100, 620, "1. SERVICES")
        p.drawString(100, 600, "Provider shall provide consulting services to Client.")
        p.drawString(100, 580, "")
        p.drawString(100, 560, "2. TERM")
        p.drawString(100, 540, "This Agreement shall commence on January 1, 2024")
        p.drawString(100, 520, "and shall continue until December 31, 2025.")
        p.drawString(100, 500, "")
        p.drawString(100, 480, "3. COMPENSATION")
        p.drawString(100, 460, "Client shall pay Provider $100,000 annually.")
        p.drawString(100, 440, "")
        p.drawString(100, 420, "4. GOVERNING LAW")
        p.drawString(100, 400, "This Agreement shall be governed by the laws of California.")
        
        p.save()
        buffer.seek(0)
        
        print("✅ Sample PDF created")
        
        # Test 4: Upload the PDF
        print("\n4. Testing PDF upload...")
        files = {
            'file': ('test_contract.pdf', buffer.getvalue(), 'application/pdf')
        }
        data = {
            'model': 'gemini-2.5-flash'
        }
        
        response = requests.post(f"{base_url}/documents/upload", files=files, data=data)
        
        if response.status_code == 200:
            result = response.json()
            print("✅ PDF upload successful!")
            print(f"   Filename: {result['filename']}")
            print(f"   Status: {result['status']}")
            print(f"   Contract ID: {result.get('contract_id', 'N/A')}")
            print(f"   Model used: {result['model_used']}")
            print(f"   Details: {result['details']}")
            
            # If successful, test querying the uploaded contract
            if result.get('contract_id'):
                print(f"\n5. Testing query for uploaded contract...")
                time.sleep(2)  # Give it a moment to process
                
                query_payload = {
                    "model": "gemini-2.5-flash", 
                    "prompt": f"Find contract with ID {result['contract_id']}",
                    "history": "[]"
                }
                
                query_response = requests.post(f"{base_url}/run/", json=query_payload)
                if query_response.status_code == 200:
                    print("✅ Contract query successful!")
                else:
                    print(f"❌ Contract query failed: {query_response.status_code}")
        else:
            error_detail = response.json().get('detail', 'Unknown error')
            print(f"❌ PDF upload failed: {response.status_code} - {error_detail}")
            
    except ImportError:
        print("⚠️  Skipping PDF creation test (reportlab not installed)")
        print("   You can manually test by uploading a PDF through the UI")
    except Exception as e:
        print(f"❌ PDF test failed: {e}")
    
    print("\n" + "=" * 50)
    print("🎉 PDF Upload System Test Complete!")
    print("\nNext steps:")
    print("1. Open http://localhost:3000 in your browser")
    print("2. Click 'Upload PDF' button")
    print("3. Upload a PDF contract")
    print("4. Chat about the uploaded contract")

if __name__ == "__main__":
    test_pdf_upload_system()