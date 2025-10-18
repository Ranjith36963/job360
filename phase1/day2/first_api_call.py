"""
Day 2: First OpenAI API Call
Making your first request to GPT!
"""

import os
from dotenv import load_dotenv
from openai import OpenAI

def test_api_connection():
    """Test that we can connect to OpenAI API"""
    print("=" * 60)
    print("🤖 DAY 2: FIRST OPENAI API CALL")
    print("=" * 60)
    print()
    
    # Load environment variables from .env file
    load_dotenv()
    
    # Get API key
    api_key = os.getenv("OPENAI_API_KEY")
    
    if not api_key:
        print("❌ ERROR: No API key found!")
        print()
        print("Steps to fix:")
        print("1. Create .env file in project root folder")
        print("2. Add this line: OPENAI_API_KEY=sk-proj-your-actual-key")
        print("3. Save the file")
        print("4. Make sure .env is in .gitignore")
        print()
        return
    
    # Show partial API key (for security, don't show full key)
    print(f"✅ API Key loaded: {api_key[:20]}...{api_key[-4:]}")
    print()
    
    # Create OpenAI client
    try:
        client = OpenAI(api_key=api_key)
        print("✅ OpenAI client created successfully")
        print()
        
        # Make your first API call!
        print("🚀 Sending request to GPT-4o-mini...")
        print("💭 Question: 'Say Hello! I am AI and I am working! in an excited way'")
        print()
        
        response = client.chat.completions.create(
            model="gpt-4o-mini",  # Fast and cheap model for testing
            messages=[
                {
                    "role": "system",
                    "content": "You are a helpful, enthusiastic AI assistant."
                },
                {
                    "role": "user",
                    "content": "Say 'Hello! I am AI and I am working!' in an excited way"
                }
            ],
            max_tokens=100,
            temperature=0.7
        )
        
        # Get the response from AI
        ai_message = response.choices[0].message.content
        
        print("🎉 SUCCESS! AI Responded:")
        print("-" * 60)
        print(f"🤖 AI: {ai_message}")
        print("-" * 60)
        print()
        
        # Show detailed stats
        print("📊 Request Stats:")
        print(f"   Model used: {response.model}")
        print(f"   Prompt tokens: {response.usage.prompt_tokens}")
        print(f"   Completion tokens: {response.usage.completion_tokens}")
        print(f"   Total tokens: {response.usage.total_tokens}")
        
        # Calculate approximate cost
        # GPT-4o-mini: $0.150 per 1M input tokens, $0.600 per 1M output tokens
        input_cost = (response.usage.prompt_tokens / 1_000_000) * 0.150
        output_cost = (response.usage.completion_tokens / 1_000_000) * 0.600
        total_cost = input_cost + output_cost
        
        print(f"   Estimated cost: ${total_cost:.6f}")
        print()
        
        # Show usage URL
        print("💰 Track your usage:")
        print("   https://platform.openai.com/usage")
        print()
        
        print("=" * 60)
        print("🎉 CONGRATULATIONS! Your first AI call works!")
        print("=" * 60)
        
    except Exception as e:
        error_message = str(e)
        print(f"❌ Error occurred:")
        print(f"   {error_message}")
        print()
        
        # Provide helpful error messages
        if "insufficient_quota" in error_message or "429" in error_message:
            print("💡 Insufficient Quota Error - You need credits!")
            print()
            print("Solution:")
            print("   1. Go to: https://platform.openai.com/settings/organization/billing")
            print("   2. Click 'Add to credit balance'")
            print("   3. Add $5-10 (enough for thousands of requests)")
            print("   4. Wait 2-5 minutes for activation")
            print("   5. Try running this script again")
            print()
            
        elif "invalid_api_key" in error_message or "401" in error_message:
            print("💡 Invalid API Key Error!")
            print()
            print("Solution:")
            print("   1. Go to: https://platform.openai.com/api-keys")
            print("   2. Create new secret key")
            print("   3. Copy the FULL key (starts with sk-proj-...)")
            print("   4. Update .env file: OPENAI_API_KEY=your-full-key")
            print("   5. Save .env file")
            print("   6. Try running this script again")
            print()
            
        elif "rate_limit" in error_message:
            print("💡 Rate Limit Error!")
            print()
            print("Solution:")
            print("   1. Wait 60 seconds")
            print("   2. Try again")
            print("   3. Consider upgrading your plan if this persists")
            print()
            
        else:
            print("💡 Common Solutions:")
            print("   1. Check your internet connection")
            print("   2. Verify API key in .env file is correct")
            print("   3. Make sure you have credits in your OpenAI account")
            print("   4. Try running: pip install --upgrade openai")
            print()

def main():
    """Main function"""
    print("\n")
    print("👋 Welcome to Day 2, Ranjith!")
    print("🎯 Goal: Make your first AI API call with OpenAI")
    print("📍 Using: GPT-4o-mini (Fast & Affordable)")
    print()
    
    test_api_connection()
    
    print("\n✨ Next Step: Build an interactive chatbot!")
    print("   You'll be able to have a full conversation with AI!")
    print()

if __name__ == "__main__":
    main()