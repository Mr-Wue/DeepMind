import asyncio
from agents.init import init_deepmind
from agents.deep_agent import create_deepmind_agent, DeepMindContext

async def test():
    config = await init_deepmind()
    agent = create_deepmind_agent(config)
    stream_count = 0
    model_end_count = 0
    content = ""

    async for event in agent.astream_events(
        {"messages": [{"role": "user", "content": "你好"}]},
        config={"configurable": {"thread_id": "test-stream"}},
        version="v2",
        context=DeepMindContext(user_id="default"),
    ):
        k = event["event"]
        node = event.get("metadata", {}).get("langgraph_node", "")

        if k == "on_chat_model_stream" and node in ("agent", "model"):
            chunk = event["data"]["chunk"]
            if hasattr(chunk, "content") and chunk.content:
                stream_count += 1
                content += str(chunk.content)

        if k == "on_chat_model_end" and node in ("agent", "model"):
            model_end_count += 1

    print(f"stream_events: {stream_count}")
    print(f"model_end_events: {model_end_count}")
    print(f"content_length: {len(content)}")
    print(f"content: {content[:200]}")
    print("PASS" if content else "FAIL - no content streamed")

asyncio.run(test())
