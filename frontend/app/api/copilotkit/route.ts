import {
  CopilotRuntime,
  ExperimentalEmptyAdapter,
  copilotRuntimeNextJSAppRouterEndpoint,
} from "@copilotkit/runtime";
import { LangGraphHttpAgent } from "@copilotkit/runtime/langgraph";
import { NextRequest } from "next/server";

const AGENT_URL =
  process.env.AGENT_URL || "http://127.0.0.1:8000/copilotkit";

const serviceAdapter = new ExperimentalEmptyAdapter();

const runtime = new CopilotRuntime({
  agents: {
    deepmind: new LangGraphHttpAgent({ url: AGENT_URL }),
  },
});

const { handleRequest } = copilotRuntimeNextJSAppRouterEndpoint({
  runtime,
  serviceAdapter,
  endpoint: "/api/copilotkit",
});

export const GET = (req: NextRequest) => handleRequest(req);
export const POST = (req: NextRequest) => handleRequest(req);
