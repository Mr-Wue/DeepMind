"use client";

import { CopilotChat } from "@copilotkit/react-core/v2";
import { TodoPanel } from "@/components/TodoPanel";
import { ToolCallPanel } from "@/components/ToolCallPanel";
import { InterruptHandler } from "@/components/InterruptCard";

export default function Home() {
  return (
    <div className="flex h-full">
      {/* ── 左侧：Todo 任务面板 ── */}
      <TodoPanel />

      {/* ── 中间：对话区 ── */}
      <div className="flex flex-1 flex-col min-w-0">
        <header className="flex h-12 items-center justify-center border-b border-border px-4 shrink-0">
          <h1 className="text-sm font-semibold text-muted-foreground">
            DeepMind · 需求管理智能助手
          </h1>
        </header>
        <CopilotChat
          labels={{ chatInputPlaceholder: "输入你的问题或指令…" }}
          className="flex-1 min-h-0"
        />
      </div>

      {/* ── 右侧：工具调用面板 ── */}
      <ToolCallPanel />

      {/* ── 中断确认处理 ── */}
      <InterruptHandler />
    </div>
  );
}
