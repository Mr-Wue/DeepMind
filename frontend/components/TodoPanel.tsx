"use client";

import { useAgent } from "@copilotkit/react-core/v2";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Badge } from "@/components/ui/badge";

interface TodoItem {
  content: string;
  status: "pending" | "in_progress" | "completed";
  description?: string;
}

const STATUS_MAP: Record<string, { emoji: string; label: string; variant: "outline" | "default" | "secondary" }> = {
  completed: { emoji: "✅", label: "完成", variant: "default" },
  in_progress: { emoji: "🔄", label: "进行中", variant: "secondary" },
  pending: { emoji: "⏳", label: "待处理", variant: "outline" },
};

export function TodoPanel() {
  const { agent } = useAgent({ agentId: "deepmind" });
  const todos: TodoItem[] = agent?.state?.todos ?? [];

  return (
    <aside className="flex w-64 shrink-0 flex-col border-r border-border">
      <div className="flex h-12 items-center border-b border-border px-4 shrink-0">
        <h2 className="text-sm font-semibold text-muted-foreground">📋 任务规划</h2>
      </div>
      <ScrollArea className="flex-1 min-h-0">
        <div className="p-3 space-y-2">
          {todos.length === 0 ? (
            <p className="text-xs text-muted-foreground px-1">
              暂无任务。发送消息后，Agent 将自动规划任务。
            </p>
          ) : (
            todos.map((todo, i) => {
              const info = STATUS_MAP[todo.status] ?? STATUS_MAP.pending;
              return (
                <Card key={i} className="border-muted">
                  <CardHeader className="p-3 pb-1">
                    <CardTitle className="flex items-center gap-2 text-sm">
                      <span>{info.emoji}</span>
                      <span className="flex-1 truncate">{todo.content}</span>
                      <Badge variant={info.variant} className="text-[10px] h-4 px-1.5">
                        {info.label}
                      </Badge>
                    </CardTitle>
                  </CardHeader>
                  {todo.description && (
                    <CardContent className="p-3 pt-0">
                      <p className="text-xs text-muted-foreground">{todo.description}</p>
                    </CardContent>
                  )}
                </Card>
              );
            })
          )}
        </div>
      </ScrollArea>
    </aside>
  );
}
