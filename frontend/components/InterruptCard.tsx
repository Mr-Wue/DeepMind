"use client";

import { useInterrupt } from "@copilotkit/react-core/v2";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { useState } from "react";

interface InterruptPayload {
  action?: string;
  total?: number;
  summary?: string;
  by_type?: Record<string, number>;
  message?: string;
}

type InterruptAction = "store_entities" | "update_entities" | "generic";

function getInterruptMeta(action: InterruptAction | string) {
  switch (action) {
    case "store_entities":
      return { emoji: "📦", title: "确认入库", approveLabel: "✅ 确认入库" };
    case "update_entities":
      return { emoji: "✏️", title: "确认修改", approveLabel: "✅ 确认修改" };
    default:
      return { emoji: "⚠️", title: "确认操作", approveLabel: "✅ 确认" };
  }
}

export function InterruptHandler() {
  const [processing, setProcessing] = useState(false);

  useInterrupt({
    agentId: "deepmind",
    render({ event, resolve }) {
      const payload = (event.value ?? {}) as InterruptPayload;
      const action = (payload.action || "generic") as InterruptAction;
      const meta = getInterruptMeta(action);

      const byType = payload.by_type ?? {};
      const detailLines = Object.entries(byType)
        .map(([k, v]) => `${k}: ${v} 个`)
        .join("\n");

      const handleApprove = async () => {
        setProcessing(true);
        resolve(JSON.stringify({ decision: "approve" }));
      };

      const handleReject = () => {
        resolve(JSON.stringify({ decision: "reject" }));
      };

      return (
        <Card className="my-3 border-primary/40 bg-primary/5">
          <CardHeader className="p-4 pb-2">
            <CardTitle className="flex items-center gap-2 text-base">
              <span>{meta.emoji}</span>
              {meta.title}
              {payload.total != null && (
                <span className="text-sm font-normal text-muted-foreground">
                  · {payload.total} 条记录
                </span>
              )}
            </CardTitle>
            {payload.summary && (
              <CardDescription className="mt-1">{payload.summary}</CardDescription>
            )}
          </CardHeader>
          {detailLines && (
            <CardContent className="p-4 py-0">
              <pre className="whitespace-pre-wrap text-xs text-muted-foreground">
                {detailLines}
              </pre>
            </CardContent>
          )}
          <CardFooter className="flex gap-2 justify-end p-4 pt-3">
            <Button variant="outline" size="sm" onClick={handleReject} disabled={processing}>
              ❌ 取消
            </Button>
            <Button size="sm" onClick={handleApprove} disabled={processing}>
              {processing ? "处理中…" : meta.approveLabel}
            </Button>
          </CardFooter>
        </Card>
      );
    },
  });

  return null;
}
