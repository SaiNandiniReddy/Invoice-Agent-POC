"""
scripts/hello_world_agent.py — SDK installation verification script.

Purpose:
    Run this FIRST after setting up your environment.
    It verifies the OpenAI Agents SDK is installed correctly and
    your API key is working by running the simplest possible agent.

Usage:
    python scripts/hello_world_agent.py

Expected Output:
    ✅ SDK import successful
    ✅ Agent created
    ✅ Agent responded: Hello! I'm ready to process invoices.
    🎉 Day 1 verification PASSED — SDK is working correctly.
"""

import sys
import os


def check_env() -> None:
    """Verify OPENAI_API_KEY is set before running the agent."""
    from dotenv import load_dotenv
    load_dotenv()

    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key or api_key == "your-openai-api-key-here":
        print("❌ ERROR: OPENAI_API_KEY is not set.")
        print("   1. Copy .env.example to .env")
        print("   2. Set your real OpenAI API key in .env")
        sys.exit(1)
    print("✅ OPENAI_API_KEY found")


def run_hello_world_agent() -> None:
    """Instantiate a minimal agent and run one turn to confirm the SDK works."""
    try:
        from agents import Agent, Runner
        print("✅ SDK import successful  (openai-agents installed correctly)")
    except ImportError as e:
        print(f"❌ SDK import failed: {e}")
        print("   Run: pip install openai-agents")
        sys.exit(1)

    try:
        agent = Agent(
            name="hello-invoice-agent",
            instructions=(
                "You are a helpful invoice processing assistant. "
                "When greeted, respond with exactly: "
                "'Hello! I'm ready to process invoices.'"
            ),
            model=os.getenv("OPENAI_MODEL", "gpt-4o"),
        )
        print("✅ Agent created successfully")
    except Exception as e:
        print(f"❌ Agent creation failed: {e}")
        sys.exit(1)

    try:
        result = Runner.run_sync(agent, "Say hello and confirm you're ready.")
        print(f"✅ Agent responded: {result.final_output}")
    except Exception as e:
        print(f"❌ Agent run failed: {e}")
        print("   Check your API key and network connection.")
        sys.exit(1)

    print("\n🎉 Day 1 verification PASSED — OpenAI Agents SDK is working correctly.")


if __name__ == "__main__":
    print("=" * 60)
    print("  Invoice Workflow Agent POC — SDK Hello World Test")
    print("=" * 60)
    check_env()
    run_hello_world_agent()
