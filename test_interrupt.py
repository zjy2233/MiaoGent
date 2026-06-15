"""Verify astream_events v2 does NOT raise on interrupt (production graph)."""
import asyncio, os
os.environ["REWOO_ENABLED"] = "false"

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import interrupt, Command
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.language_models import BaseChatModel
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.errors import GraphInterrupt

class MockLLM(BaseChatModel):
    call_idx: int = 0
    def bind_tools(self, tools, **kwargs):
        self._tools = tools; return self
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.call_idx += 1
        if self.call_idx == 1:
            return ChatResult(generations=[
                ChatGeneration(message=AIMessage(content="", tool_calls=[
                    {"name": "shell", "args": {"command": "del /f /q test.txt"}, "id": "call_1", "type": "tool_call"}
                ]))
            ])
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="Done."))])
    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        return self._generate(messages, stop=stop, **kwargs)
    @property
    def _llm_type(self): return "mock"

async def main():
    from src.agent.builder import build_agent
    from src.store.memory_store import MemoryStore
    llm = MockLLM()
    bundle = build_agent(llm, checkpointer=MemorySaver(), memory_store=MemoryStore())
    agent = bundle.agent
    config = {"configurable": {"thread_id": "ae-test-2"}}

    print("=== Phase 1: astream_events v2 ===")
    got_interrupt_event = False
    exc = None
    try:
        async for event in agent.astream_events(
            {"messages": [HumanMessage(content="delete test.txt")]}, config, version="v2"
        ):
            kind = event.get("event","")
            if kind in ("on_tool_start",):
                pass  # tool was invoked
    except Exception as e:
        exc = e
        print(f"  Exception raised: {type(e).__name__}")

    if exc:
        print(f"  BUG: astream_events raised {type(exc).__name__} instead of finishing normally!")
    else:
        print(f"  OK: astream_events finished normally (no exception)")

    # Check interrupt via aget_state
    state = await agent.aget_state(config)
    pending = bool(state.tasks and state.tasks[0].interrupts)
    print(f"  Interrupt via aget_state: {pending}")
    print(f"  Messages: {len(state.values.get('messages',[]))}")

    # Phase 2: Resume
    print()
    print("=== Phase 2: ainvoke(Command(resume=True)) ===")
    try:
        r = await agent.ainvoke(Command(resume=True), config)
        has_int = isinstance(r, dict) and "__interrupt__" in r
        msgs = r.get("messages",[])
        print(f"  Interrupt in result: {has_int}")
        print(f"  Messages: {len(msgs)}")
        if msgs:
            print(f"  Last: {type(msgs[-1]).__name__}: {str(msgs[-1].content)[:80]}")
    except Exception as e:
        print(f"  Exception: {type(e).__name__}: {e}")

    state2 = await agent.aget_state(config)
    still = bool(state2.tasks and state2.tasks[0].interrupts)
    print(f"  Final: msgs={len(state2.values.get('messages',[]))}, interrupt={still}")

if __name__ == "__main__":
    asyncio.run(main())
