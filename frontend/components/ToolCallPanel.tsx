"use client";

import { useAgent } from "@copilotkit/react-core/v2";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Badge } from "@/components/ui/badge";
import { useMemo } from "react";

interface ToolCallEntry {
  id: string;
  name: string;
  status: "executing" | "complete" | "error";
  input?: string;
  output?: string;
}

function extractToolCalls(agent: any): ToolCallEntry[] {
  if (!agent?.messages) return [];

  const calls: ToolCallEntry[] = [];
  const toolResults: Record<string, { content: string; error?: string }> = {};

  for (const msg of agent.messages) {
    if (msg.role === "tool" && msg.toolCallId) {
      toolResults[msg.toolCallId] = {
        content: msg.content ?? "",
        error: msg.error,
      };
    }
  }

  for (const msg of agent.messages) {
    if (msg.role === "assistant" && msg.toolCalls) {
      for (const tc of msg.toolCalls) {
        const result = toolResults[tc.id];
        calls.push({
          id: tc.id || crypto.randomUUID(),
          name: tc.function?.name || tc.name || "unknown",
          status: result ? (result.error ? "error" : "complete") : "executing",
          input: tc.function?.arguments || JSON.stringify(tc.parameters ?? {}),
          output: result?.content,
        });
      }
    }
  }
  return calls;
}

const STATUS_BADGE: Record<string, { label: string; variant: "outline" | "default" | "destructive" }> = {
  executing: { label: "执行中", variant: "default" },
  complete: { label: "完成", variant: "outline" },
  error: { label: "失败", variant: "destructive" },
};

function ToolCallCard({ call }: { call: ToolCallEntry }) {
  const badge = STATUS_BADGE[call.status] ?? STATUS_BADGE.complete;
  const isRunning = call.status === "executing";

  return (
    <Card key={call.id} className={`border-muted ${isRunning ? "ring-1 ring-primary/30" : ""}`}>
      <CardHeader className="p-3 pb-1">
        <CardTitle className="flex items-center gap-2 text-sm">
          <span>🔧</span>
          <span className="flex-1 truncate font-mono text-xs">{call.name}</span>
          <Badge variant={badge.variant} className="text-[10px] h-4 px-1.5">
            {isRunning && <span className="mr-1 inline-block h-1.5 w-1.5 rounded-full bg-current animate-pulse" />}
            {badge.label}
          </Badge>
        </CardTitle>
      </CardHeader>
      <CardContent className="p-3 pt-0 space-y-2">
        {call.input && (
          <details className="text-xs">
            <summary className="cursor-pointer text-muted-foreground hover:text-foreground">输入</summary>
            <pre className="mt-1 whitespace-pre-wrap rounded bg-muted p-2 text-[11px] max-h-24 overflow-auto">
              {call.input.length > 500 ? call.input.slice(0, 500) + "\n…" : call.input}
            </pre>
          </details>
        )}
        {call.output && (
          <details className="text-xs">
            <summary className="cursor-pointer text-muted-foreground hover:text-foreground">输出</summary>
            <pre className="mt-1 whitespace-pre-wrap rounded bg-muted p-2 text-[11px] max-h-24 overflow-auto">
              {call.output.length > 500 ? call.output.slice(0, 500) + "\n…" : call.output}
            </pre>
          </details>
        )}
      </CardContent>
    </Card>
  );
}

export function ToolCallPanel() {
  const { agent } = useAgent({ agentId: "deepmind" });
  const toolCalls = useMemo(() => extractToolCalls(agent), [agent]);

  return (
    <aside className="flex w-72 shrink-0 flex-col border-l border-border">
      <div className="flex h-12 items-center border-b border-border px-4 shrink-0">
        <h2 className="text-sm font-semibold text-muted-foreground">🔧 工具调用</h2>
        {toolCalls.length > 0 && (
          <Badge variant="secondary" className="ml-2 text-[10px] h-4 px-1.5">
            {toolCalls.length}
          </Badge>
        )}
      </div>
      <ScrollArea className="flex-1 min-h-0">
        <div className="p-3 space-y-2">
          {toolCalls.length === 0 ? (
            <p className="text-xs text-muted-foreground px-1">
              暂无工具调用。Agent 执行时会在此显示。
            </p>
          ) : (
            [...toolCalls].reverse().map((call) => <ToolCallCard key={call.id} call={call} />)
          )}
        </div>
      </ScrollArea>
    </aside>
  );
}
