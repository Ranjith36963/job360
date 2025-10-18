"""
Day 2: Interactive AI Chatbot
Have a real conversation with GPT-4o-mini!
"""

import os
from dotenv import load_dotenv
from openai import OpenAI

def create_chatbot():
    """Create an interactive chatbot with memory"""
    print("=" * 60)
    print("🤖 INTERACTIVE AI CHATBOT")
    print("=" * 60)
    print()
    print("💬 Chat with GPT-4o-mini! Ask anything!")
    print()
    print("Features:")
    print("  ✅ Remembers conversation history")
    print("  ✅ Context-aware responses")
    print("  ✅ Natural conversation flow")
    print()
    print("Commands:")
    print("  • Type your message and press Enter")
    print("  • Type 'quit' or 'exit' to stop")
    print("  • Type 'clear' to clear conversation history")
    print()
    print("-" * 60)
    print()
    
    # Load API key
    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")
    
    if not api_key:
        print("❌ ERROR: No API key found in .env file!")
        return
    
    # Create OpenAI client
    client = OpenAI(api_key=api_key)
    
    # Conversation history (memory)
    conversation_history = [
        {
            "role": "system",
            "content": """You are a helpful, friendly, and enthusiastic AI assistant. 
You're knowledgeable about programming, AI agents, and technology. 
Keep responses concise but informative. Use emojis occasionally to be engaging."""
        }
    ]
    
    # Stats tracking
    total_tokens = 0
    total_cost = 0.0
    message_count = 0
    
    print("🤖 AI: Hello Ranjith! I'm ready to chat! What would you like to talk about? 🚀")
    print()
    
    # Main chat loop
    while True:
        # Get user input
        try:
            user_input = input("👤 You: ").strip()
            print()
            
            # Check for exit commands
            if user_input.lower() in ['quit', 'exit', 'bye', 'goodbye']:
                print("🤖 AI: Goodbye Ranjith! Great chatting with you! 👋")
                print()
                print("📊 Session Stats:")
                print(f"   Messages exchanged: {message_count}")
                print(f"   Total tokens used: {total_tokens}")
                print(f"   Total cost: ${total_cost:.6f}")
                print()
                break
            
            # Check for clear command
            if user_input.lower() == 'clear':
                conversation_history = [conversation_history[0]]  # Keep system message
                print("🤖 AI: Conversation history cleared! Let's start fresh! ✨")
                print()
                continue
            
            # Skip empty messages
            if not user_input:
                continue
            
            # Add user message to history
            conversation_history.append({
                "role": "user",
                "content": user_input
            })
            
            # Get AI response
            try:
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=conversation_history,
                    max_tokens=500,
                    temperature=0.7
                )
                
                # Extract AI's response
                ai_message = response.choices[0].message.content
                
                # Add AI response to history
                conversation_history.append({
                    "role": "assistant",
                    "content": ai_message
                })
                
                # Update stats
                tokens_used = response.usage.total_tokens
                input_cost = (response.usage.prompt_tokens / 1_000_000) * 0.150
                output_cost = (response.usage.completion_tokens / 1_000_000) * 0.600
                request_cost = input_cost + output_cost
                
                total_tokens += tokens_used
                total_cost += request_cost
                message_count += 1
                
                # Display AI response
                print(f"🤖 AI: {ai_message}")
                print()
                print(f"   💭 (tokens: {tokens_used} | cost: ${request_cost:.6f})")
                print()
                
            except Exception as e:
                print(f"❌ Error getting response: {e}")
                print()
                # Remove the user message that caused error
                conversation_history.pop()
                
        except KeyboardInterrupt:
            print("\n")
            print("🤖 AI: Chat interrupted. Goodbye! 👋")
            print()
            break
        except EOFError:
            print("\n")
            print("🤖 AI: Chat ended. Goodbye! 👋")
            print()
            break

def main():
    """Main function"""
    print("\n")
    print("👋 Welcome to Interactive Chatbot!")
    print("🎯 Goal: Have a real conversation with AI")
    print("📍 Using: GPT-4o-mini with conversation memory")
    print()
    
    create_chatbot()
    
    print("✨ Great chatting with you!")
    print()

if __name__ == "__main__":
    main()