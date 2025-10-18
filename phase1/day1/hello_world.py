"""
Day 1: Environment Setup Test
Tests that all packages are installed correctly
"""

def test_imports():
    """Test that all required packages can be imported"""
    print("=" * 60)
    print("🚀 TESTING PHASE 1 - DAY 1 SETUP")
    print("=" * 60)
    print()
    
    packages_to_test = [
        ("langchain", "LangChain"),
        ("openai", "OpenAI"),
        ("dotenv", "Python-dotenv"),
        ("requests", "Requests"),
    ]
    
    all_passed = True
    
    for package_name, display_name in packages_to_test:
        try:
            __import__(package_name)
            print(f"✅ {display_name:20} - Imported successfully!")
        except ImportError as e:
            print(f"❌ {display_name:20} - Failed to import")
            print(f"   Error: {e}")
            all_passed = False
    
    print()
    print("=" * 60)
    
    if all_passed:
        print("🎉 ALL TESTS PASSED! Environment ready!")
        print("💪 You're ready to build AI agents!")
    else:
        print("⚠️  Some packages failed to import.")
        print("   Run: pip install -r requirements.txt")
    
    print("=" * 60)
    print()

def main():
    """Main function"""
    print("\n")
    print("👋 Hello, Ranjith! Welcome to AI Agent Development!")
    print("📍 Location: enterprise-mcp-hub/phase1/day1")
    print("🎯 Goal: Verify development environment")
    print("\n")
    
    test_imports()
    
    print("✨ Next Step: Day 2 - OpenAI API Integration")
    print()

if __name__ == "__main__":
    main()